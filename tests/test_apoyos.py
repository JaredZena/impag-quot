"""
Hermetic tests for Apoyos gubernamentales: settings, CRUD/validation,
slug/fingerprint/merge, tick/step gating, the feed, admin status transitions
and GitHub dispatch. SQLite (conftest.py forces DATABASE_URL); Claude and
`requests` are replaced by fakes; real network is blocked.

Run: venv/bin/python -m pytest tests/test_apoyos.py -q
"""

import socket
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import app
from models import Apoyo, ApoyoRun, SessionLocal
from services import (
    ai_usage,
    apoyos,
    apoyos_discovery,
    app_settings,
    background,
    mailer,
)

# Deliberately NOT a separate sqlite engine: ai_usage.record() always opens
# its own SessionLocal() (see services/ai_usage.py — "in its own session so a
# caller's rollback never loses the record"), bound to models.engine, which
# conftest.py already points at a dedicated sqlite tmp file for this test
# run. Using that same SessionLocal here (instead of a second, disconnected
# sqlite file) is what lets _finalize_run's ai_usage-ledger sum see rows
# written by a fake Claude call during the same test.
TestingSession = SessionLocal
client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_db():
    db = TestingSession()
    try:
        db.query(ApoyoRun).delete()
        db.query(Apoyo).delete()
        from models import AIUsage, AppSetting

        db.query(AIUsage).delete()
        db.query(AppSetting).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JOB_TRIGGER_SECRET", "job-secret")
    monkeypatch.setenv("STOREFRONT_API_KEY", "storefront-key")
    monkeypatch.setenv("GITHUB_STOREFRONT_TOKEN", "gh-token")
    monkeypatch.setattr(ai_usage, "claude_api_key", "test-key")


@pytest.fixture(autouse=True)
def _sync_background():
    """step() now submits each topic's Claude work to services.background's
    thread pool instead of running it inline. Tests need the result
    deterministically before their assertions, so every topic job here runs
    synchronously (still through the exact same background.submit() call
    step() makes — see services/background.py's _InlineExecutor)."""
    background.set_sync_for_tests(True)
    yield
    background.set_sync_for_tests(False)


OUTBOX: list[dict] = []


@pytest.fixture(autouse=True)
def _outbox(monkeypatch):
    OUTBOX.clear()

    def capture(message, *, tag, idempotency_key=None):
        OUTBOX.append(message)
        return True

    monkeypatch.setattr(mailer, "send", capture)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a hermetic test")

    monkeypatch.setattr(socket, "create_connection", _blocked)


def db_session():
    return TestingSession()


JOB_HEADERS = {"X-Job-Token": "job-secret"}
STOREFRONT_HEADERS = {"X-API-Key": "storefront-key"}


# ── settings ─────────────────────────────────────────────────────────────────


def test_settings_default_and_round_trip():
    r = client.get("/apoyos/settings")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["settings"]["frequency"] == "weekly"
    assert "options" in data and "models" in data["options"]

    r = client.put(
        "/apoyos/settings", json={"frequency": "daily", "monthly_budget_usd": 10}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["settings"]["frequency"] == "daily"
    assert data["settings"]["monthly_budget_usd"] == 10
    assert (
        data["settings"]["states"] == apoyos.ApoyosSettings().states
    )  # untouched field kept


def test_settings_rejects_invalid_patch():
    r = client.put("/apoyos/settings", json={"frequency": "hourly"})
    assert r.status_code == 422


def test_settings_model_validates_emails_and_queries():
    with pytest.raises(ValidationError):
        apoyos.ApoyosSettings(notify_emails=["not-an-email"])
    with pytest.raises(ValidationError):
        apoyos.ApoyosSettings(extra_queries=["x" * 201])


# ── slug / fingerprint ───────────────────────────────────────────────────────


def test_slugify_strips_accents_and_punctuation():
    assert (
        apoyos.slugify("Producción para el Bienestar!")
        == "produccion-para-el-bienestar"
    )
    assert apoyos.slugify("") == "programa"


def test_generate_slug_is_unique_with_suffixes():
    db = db_session()
    try:
        s1 = apoyos.generate_slug(db, "Crédito a la palabra", "FIRA", 2026)
        db.add(Apoyo(slug=s1, title="x"))
        db.commit()
        s2 = apoyos.generate_slug(db, "Crédito a la palabra", "FIRA", 2026)
        assert s2 != s1 and s2.startswith(s1)
    finally:
        db.close()


def test_fingerprint_matches_regardless_of_accents():
    a = apoyos.fingerprint_of("Sembrando Vida", "SADER", 2026)
    b = apoyos.fingerprint_of("sembrando vida", "sader", 2026)
    assert a == b


# ── CRUD + validation ────────────────────────────────────────────────────────


def test_create_requires_title():
    r = client.post("/apoyos", json={"program": "x"})
    assert r.status_code == 422


def test_create_update_delete_cycle():
    r = client.post(
        "/apoyos",
        json={
            "title": "Programa de prueba",
            "organism": "SADER",
            "states": ["durango"],
            "sectors": ["ganaderia"],
            "source_url": "https://www.gob.mx/programa",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "draft" and data["slug"]
    apoyo_id = data["id"]

    r = client.put(f"/apoyos/{apoyo_id}", json={"summary": "Resumen actualizado"})
    assert r.status_code == 200
    assert r.json()["data"]["summary"] == "Resumen actualizado"

    r = client.get(f"/apoyos/{apoyo_id}")
    assert r.status_code == 200 and r.json()["data"]["title"] == "Programa de prueba"

    r = client.post(f"/apoyos/{apoyo_id}/status", json={"status": "published"})
    assert r.status_code == 200
    published = r.json()["data"]
    assert published["status"] == "published" and published["published_at"]
    assert published["verified_by"] == "dev@local.test"

    # Publish once more: published_at must not move.
    first_published_at = published["published_at"]
    r = client.post(f"/apoyos/{apoyo_id}/status", json={"status": "draft"})
    r = client.post(f"/apoyos/{apoyo_id}/status", json={"status": "published"})
    assert r.json()["data"]["published_at"] == first_published_at

    # Only draft/rejected can be deleted.
    r = client.delete(f"/apoyos/{apoyo_id}")
    assert r.status_code == 409
    client.post(f"/apoyos/{apoyo_id}/status", json={"status": "rejected"})
    r = client.delete(f"/apoyos/{apoyo_id}")
    assert r.status_code == 200 and r.json()["data"]["deleted"] is True
    assert client.get(f"/apoyos/{apoyo_id}").status_code == 404


def test_update_unknown_apoyo_is_404():
    assert client.put("/apoyos/999999", json={"summary": "x"}).status_code == 404
    assert (
        client.post("/apoyos/999999/status", json={"status": "published"}).status_code
        == 404
    )
    assert client.delete("/apoyos/999999").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"title": "<b>hola</b>"},
        {"title": "ok", "source_url": "ftp://example.com"},
        {"title": "ok", "states": ["atlantis"]},
        {"title": "ok", "level": "galactico"},
        {"title": "ok", "requirements": ["<script>x</script>"]},
    ],
)
def test_create_rejects_invalid_input(body):
    assert client.post("/apoyos", json=body).status_code == 422


def test_list_filters_and_counts():
    db = db_session()
    try:
        db.add_all(
            [
                Apoyo(
                    slug="a",
                    title="A",
                    status="draft",
                    states=["durango"],
                    sectors=["ganaderia"],
                ),
                Apoyo(
                    slug="b",
                    title="B",
                    status="published",
                    states=["nacional"],
                    sectors=["agricultura"],
                    closes_at=date.today() + timedelta(days=10),
                ),
                Apoyo(slug="c", title="C", status="rejected"),
            ]
        )
        db.commit()
    finally:
        db.close()

    r = client.get("/apoyos", params={"status": "all"})
    data = r.json()["data"]
    assert data["counts"]["draft"] == 1 and data["counts"]["published"] == 1
    assert data["counts"]["rejected"] == 1
    assert data["total"] == 3
    # draft first regardless of alpha order
    assert data["items"][0]["slug"] == "a"

    r = client.get("/apoyos", params={"state": "durango"})
    assert [i["slug"] for i in r.json()["data"]["items"]] == ["a"]

    r = client.get("/apoyos", params={"window": "abierta"})
    assert [i["slug"] for i in r.json()["data"]["items"]] == ["b"]

    r = client.get("/apoyos", params={"q": "B"})
    assert [i["slug"] for i in r.json()["data"]["items"]] == ["b"]


def test_stats_endpoint():
    db = db_session()
    try:
        db.add(
            Apoyo(
                slug="open1",
                title="Abierto",
                status="published",
                states=["durango"],
                sectors=["agricultura"],
                closes_at=date.today() + timedelta(days=5),
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.get("/apoyos/stats")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["published_by_window"]["abierta"] == 1
    assert len(data["closing_soon"]) == 1
    assert data["frequency"] == "weekly"


# ── merge rules ──────────────────────────────────────────────────────────────


def _program(**over) -> dict:
    base = {
        "title": "Programa X",
        "program": None,
        "organism": "SADER",
        "level": "federal",
        "states": ["nacional"],
        "sectors": ["ganaderia"],
        "support_types": ["subsidio"],
        "summary": "Resumen",
        "description": None,
        "beneficiaries": None,
        "amount_text": "$10,000",
        "amount_max_mxn": 10000,
        "requirements": ["Acta de nacimiento"],
        "how_to_apply": ["Entra a la página"],
        "opens_at": None,
        "closes_at": "2026-12-31",
        "deadline_text": None,
        "permanent": False,
        "year": 2026,
        "source_url": "https://www.gob.mx/programa-x",
        "sources": [],
        "category_slugs": [],
        "confidence": "alta",
        "notes": None,
        "existing_slug": None,
    }
    base.update(over)
    return base


def test_merge_creates_new_draft_by_default():
    db = db_session()
    try:
        run = ApoyoRun(trigger="manual", status="running", topics=[])
        db.add(run)
        db.commit()
        apoyos_discovery._merge_programs(db, run, [_program()], apoyos.ApoyosSettings())
        rows = db.query(Apoyo).all()
        assert len(rows) == 1 and rows[0].status == "draft"
        assert rows[0].fingerprint == apoyos.fingerprint_of("Programa X", "SADER", 2026)
    finally:
        db.close()


def test_merge_auto_publishes_official_high_confidence():
    db = db_session()
    try:
        run = ApoyoRun(trigger="manual", status="running", topics=[])
        db.add(run)
        db.commit()
        settings = apoyos.ApoyosSettings(auto_publish=True)
        apoyos_discovery._merge_programs(db, run, [_program()], settings)
        row = db.query(Apoyo).one()
        assert row.status == "published" and row.published_at is not None
    finally:
        db.close()


def test_merge_matches_existing_slug_and_updates():
    db = db_session()
    try:
        run = ApoyoRun(trigger="manual", status="running", topics=[])
        db.add(run)
        existing = Apoyo(
            slug="programa-x-sader-2026",
            title="Programa X",
            organism="SADER",
            year=2026,
            status="draft",
            fingerprint=apoyos.fingerprint_of("Programa X", "SADER", 2026),
        )
        db.add(existing)
        db.commit()
        counts = apoyos_discovery._merge_programs(
            db,
            run,
            [_program(existing_slug="programa-x-sader-2026", amount_text="$20,000")],
            apoyos.ApoyosSettings(),
        )
        assert counts == {"new": 0, "updated": 1}
        db.refresh(existing)
        assert existing.amount_text == "$20,000"
    finally:
        db.close()


def test_merge_matches_by_fingerprint_without_existing_slug():
    db = db_session()
    try:
        run = ApoyoRun(trigger="manual", status="running", topics=[])
        db.add(run)
        existing = Apoyo(
            slug="programa-x-sader-2026",
            title="Programa X",
            organism="SADER",
            year=2026,
            status="draft",
            fingerprint=apoyos.fingerprint_of("Programa X", "SADER", 2026),
        )
        db.add(existing)
        db.commit()
        counts = apoyos_discovery._merge_programs(
            db, run, [_program()], apoyos.ApoyosSettings()
        )
        assert counts == {"new": 0, "updated": 1}
    finally:
        db.close()


def test_merge_preserves_staff_edited_row_and_notes_suggestion():
    db = db_session()
    try:
        run = ApoyoRun(trigger="manual", status="running", topics=[])
        db.add(run)
        existing = Apoyo(
            slug="programa-x-sader-2026",
            title="Programa X",
            organism="SADER",
            year=2026,
            status="published",
            amount_text="$5,000",
            updated_by="staff@impag.mx",
            fingerprint=apoyos.fingerprint_of("Programa X", "SADER", 2026),
        )
        db.add(existing)
        db.commit()
        apoyos_discovery._merge_programs(
            db, run, [_program(amount_text="$20,000")], apoyos.ApoyosSettings()
        )
        db.refresh(existing)
        assert existing.amount_text == "$5,000"  # untouched
        assert existing.last_seen_at is not None
        assert "Cambios sugeridos" in (existing.ai_notes or "")
    finally:
        db.close()


# ── tick gating ──────────────────────────────────────────────────────────────


def _run(db, **over):
    run = ApoyoRun(trigger="schedule", status="done", topics=[], **over)
    db.add(run)
    db.commit()
    return run


def test_tick_due_when_no_prior_run():
    db = db_session()
    try:
        result = apoyos_discovery.tick(db)
        assert result["due"] is True and result["reason"] == "scheduled"
        run = db.get(ApoyoRun, result["run_id"])
        assert run.status == "running" and run.topics
    finally:
        db.close()


def test_tick_not_due_before_frequency_elapsed():
    db = db_session()
    try:
        _run(db, finished_at=datetime.now(timezone.utc) - timedelta(hours=1))
        result = apoyos_discovery.tick(db)
        assert result == {"due": False, "run_id": None, "reason": "not_due"}
    finally:
        db.close()


def test_tick_paused_setting():
    db = db_session()
    try:
        app_settings.write(
            db, "apoyos", apoyos.ApoyosSettings, {"frequency": "paused"}, "dev"
        )
        result = apoyos_discovery.tick(db)
        assert result == {"due": False, "run_id": None, "reason": "paused"}
    finally:
        db.close()


def test_tick_budget_exhausted():
    db = db_session()
    try:
        app_settings.write(
            db, "apoyos", apoyos.ApoyosSettings, {"monthly_budget_usd": 1}, "dev"
        )
        from models import AIUsage

        db.add(AIUsage(feature="apoyos.discovery", cost_usd=Decimal(5)))
        db.commit()
        result = apoyos_discovery.tick(db)
        assert result == {"due": False, "run_id": None, "reason": "budget_exhausted"}
    finally:
        db.close()


def test_tick_prefers_queued_manual_run():
    db = db_session()
    try:
        manual = ApoyoRun(trigger="manual", status="queued", topics=[{"key": "a"}])
        db.add(manual)
        db.commit()
        result = apoyos_discovery.tick(db)
        assert result == {"due": True, "run_id": manual.id, "reason": "queued"}
        db.refresh(manual)
        assert manual.status == "running"
    finally:
        db.close()


def test_tick_recovers_stalled_run():
    db = db_session()
    try:
        stalled = ApoyoRun(
            trigger="schedule",
            status="running",
            topics=[],
            started_at=datetime.now(timezone.utc) - timedelta(hours=4),
        )
        db.add(stalled)
        db.commit()
        apoyos_discovery.tick(db)
        db.refresh(stalled)
        assert stalled.status == "failed" and stalled.error == "stalled"
    finally:
        db.close()


# ── step flow ────────────────────────────────────────────────────────────────


class FakeClaude:
    """Replaces ai_usage.create_message; also writes the AIUsage ledger row
    like the real function does, so run-level cost/web-search totals (summed
    from the ledger in _finalize_run) reflect these fake calls."""

    def __init__(self, queue: list[dict] | None = None):
        self.queue = list(queue or [])
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.queue.pop(0) if self.queue else {"programas": []}
        tool_name = (
            "guardar_verificaciones"
            if "verificaciones" in payload
            else "guardar_programas"
        )
        response = SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", name=tool_name, input=payload)],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=200,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                server_tool_use=SimpleNamespace(
                    web_search_requests=1, web_fetch_requests=0
                ),
            ),
            model=kwargs["model"],
        )
        ai_usage.record(
            feature=kwargs["feature"],
            model=kwargs["model"],
            requested_model=kwargs["model"],
            tokens=ai_usage.tokens_of(response.usage),
            duration_ms=10,
            status="ok",
            ref_type=kwargs.get("ref_type"),
            ref_id=kwargs.get("ref_id"),
        )
        return response


@pytest.fixture
def claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(ai_usage, "create_message", fake)
    return fake


def test_step_runs_each_topic_to_done_and_notifies(claude):
    db = db_session()
    try:
        app_settings.write(
            db,
            "apoyos",
            apoyos.ApoyosSettings,
            {
                "states": ["durango"],
                "sectors": ["ganaderia"],
                "include_national": True,
                "extra_queries": [],
                "notify_emails": ["staff@impag.mx"],
            },
            "dev",
        )
        settings = app_settings.read(db, "apoyos", apoyos.ApoyosSettings)
        topics = apoyos_discovery.build_topics(db, settings)
        run = ApoyoRun(
            trigger="manual",
            status="running",
            topics=topics,
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        claude.queue = [{"programas": [_program()]} for _ in topics]

        # With sync-for-tests, each step() call that submits a topic also
        # runs it to completion inline — but step() itself still answers
        # busy:true from the pre-submit snapshot, so a second call per topic
        # is needed to notice "done" and move on (or finalize).
        done = False
        steps = 0
        while not done and steps < len(topics) * 2 + 2:
            # Mimic separate HTTP requests (each gets a fresh session in
            # prod): without this, this one reused session's identity map
            # keeps serving the stale `run` it first loaded, never seeing
            # what the background job's own session committed.
            db.expire_all()
            result = apoyos_discovery.step(db, run.id)
            done = result["done"]
            steps += 1
        assert done is True
        db.refresh(run)
        # Both topics report the same program (same title/organism/year): the
        # first topic creates it, the second merges into it by fingerprint.
        assert run.status == "done"
        assert run.new_count == 1 and run.updated_count == len(topics) - 1
        assert run.cost_usd > 0  # summed from the ai_usage ledger
        assert OUTBOX and "apoyos nuevos" in OUTBOX[0]["subject"]
    finally:
        db.close()


def test_step_isolates_topic_errors(claude, monkeypatch):
    db = db_session()
    try:
        topics = [
            {
                "key": "estado:durango",
                "label": "x",
                "query": "q",
                "kind": "estado",
                "status": "pending",
                "found": 0,
                "new": 0,
                "updated": 0,
                "closed": 0,
                "error": None,
                "cost_usd": 0.0,
                "web_searches": 0,
                "started_at": None,
                "finished_at": None,
            },
        ]
        run = ApoyoRun(
            trigger="manual",
            status="running",
            topics=topics,
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()

        def boom(**kwargs):
            raise RuntimeError("ai exploded")

        monkeypatch.setattr(ai_usage, "create_message", boom)
        first = apoyos_discovery.step(db, run.id)
        assert first["busy"] is True  # submitted (and, sync-for-tests, already failed)
        db.expire_all()  # see the background job's own session's commit
        second = apoyos_discovery.step(db, run.id)
        assert second["done"] is True
        db.refresh(run)
        assert run.topics[0]["status"] == "error"
        assert run.status == "done"
    finally:
        db.close()


def _topic(key: str, **over) -> dict:
    base = {
        "key": key,
        "label": "x",
        "query": "q",
        "kind": "estado",
        "status": "pending",
        "found": 0,
        "new": 0,
        "updated": 0,
        "closed": 0,
        "error": None,
        "cost_usd": 0.0,
        "web_searches": 0,
        "started_at": None,
        "finished_at": None,
    }
    base.update(over)
    return base


def test_step_is_busy_while_a_topic_is_running_and_submits_nothing_new(monkeypatch):
    db = db_session()
    try:
        topics = [
            _topic(
                "estado:durango",
                status="running",
                started_at=(
                    datetime.now(timezone.utc) - timedelta(minutes=1)
                ).isoformat(),
            ),
            _topic("estado:zacatecas"),
        ]
        run = ApoyoRun(
            trigger="manual",
            status="running",
            topics=topics,
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()

        calls = []
        monkeypatch.setattr(background, "submit", lambda name, job: calls.append(name))

        result = apoyos_discovery.step(db, run.id)
        assert result["busy"] is True and result["done"] is False
        assert result["topic"]["key"] == "estado:durango"
        assert calls == []  # no new work submitted while one topic is in flight
        db.refresh(run)
        assert run.topics[0]["status"] == "running"
        assert run.topics[1]["status"] == "pending"
    finally:
        db.close()


def test_step_recovers_a_stale_running_topic_and_starts_the_next_one(claude):
    db = db_session()
    try:
        topics = [
            _topic(
                "estado:durango",
                status="running",
                started_at=(
                    datetime.now(timezone.utc) - timedelta(minutes=10)
                ).isoformat(),
            ),
            _topic("estado:zacatecas"),
        ]
        run = ApoyoRun(
            trigger="manual",
            status="running",
            topics=topics,
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        claude.queue = [{"programas": []}]

        result = apoyos_discovery.step(db, run.id)
        assert result["started"] == "estado:zacatecas"
        db.refresh(run)
        assert (
            run.topics[0]["status"] == "error" and run.topics[0]["error"] == "stalled"
        )
        # sync-for-tests: the newly submitted topic already ran to completion.
        assert run.topics[1]["status"] == "done"
    finally:
        db.close()


def test_step_skips_remaining_when_budget_exhausted_mid_run(claude):
    db = db_session()
    try:
        app_settings.write(
            db,
            "apoyos",
            apoyos.ApoyosSettings,
            {"monthly_budget_usd": 0.0000001},
            "dev",
        )
        topics = [
            {
                "key": f"estado:t{i}",
                "label": "x",
                "query": "q",
                "kind": "estado",
                "status": "pending",
                "found": 0,
                "new": 0,
                "updated": 0,
                "closed": 0,
                "error": None,
                "cost_usd": 0.0,
                "web_searches": 0,
                "started_at": None,
                "finished_at": None,
            }
            for i in range(3)
        ]
        run = ApoyoRun(
            trigger="manual",
            status="running",
            topics=topics,
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        from models import AIUsage

        db.add(AIUsage(feature="apoyos.discovery", cost_usd=Decimal(999)))
        db.commit()

        result = apoyos_discovery.step(db, run.id)
        assert result["done"] is True
        db.refresh(run)
        assert all(t["status"] in ("skipped",) for t in run.topics)
    finally:
        db.close()


def test_step_on_unknown_run_raises():
    db = db_session()
    try:
        with pytest.raises(apoyos_discovery.ApoyoRunNotFound):
            apoyos_discovery.step(db, 999999)
    finally:
        db.close()


def test_recheck_topic_marks_closed_program():
    db = db_session()
    try:
        apoyo = Apoyo(
            slug="viejo",
            title="Viejo programa",
            organism="SADER",
            status="published",
            closes_at=date(2026, 1, 1),
            source_url="https://www.gob.mx/viejo",
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        db.add(apoyo)
        run = ApoyoRun(trigger="manual", status="running", topics=[])
        db.add(run)
        db.commit()

        def fake_create(**kwargs):
            response = SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="guardar_verificaciones",
                        input={
                            "verificaciones": [
                                {
                                    "slug": "viejo",
                                    "still_open": False,
                                    "closes_at": "2025-06-01",
                                    "deadline_text": None,
                                    "notes": "Convocatoria cerrada",
                                }
                            ]
                        },
                    )
                ],
                stop_reason="end_turn",
                usage=SimpleNamespace(
                    input_tokens=50,
                    output_tokens=50,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                    server_tool_use=SimpleNamespace(
                        web_search_requests=1, web_fetch_requests=0
                    ),
                ),
                model=kwargs["model"],
            )
            ai_usage.record(
                feature=kwargs["feature"],
                model=kwargs["model"],
                requested_model=kwargs["model"],
                tokens=ai_usage.tokens_of(response.usage),
                duration_ms=10,
                status="ok",
                ref_type=kwargs.get("ref_type"),
                ref_id=kwargs.get("ref_id"),
            )
            return response

        import services.apoyos_discovery as mod

        orig = ai_usage.create_message
        ai_usage.create_message = fake_create
        try:
            topic = apoyos_discovery._blank_topic(
                "recheck:1", "Verificación 1", None, "recheck", apoyo_ids=[apoyo.id]
            )
            result = mod._run_recheck_topic(db, run, topic, apoyos.ApoyosSettings())
        finally:
            ai_usage.create_message = orig
        assert result["closed"] == 1
        db.refresh(apoyo)
        assert apoyo.closes_at == date(2025, 6, 1)
    finally:
        db.close()


# ── feed ─────────────────────────────────────────────────────────────────────


def test_storefront_feed_requires_api_key():
    assert client.get("/storefront/apoyos").status_code == 401
    r = client.get("/storefront/apoyos", headers=STOREFRONT_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


def test_storefront_feed_filters_and_orders():
    db = db_session()
    try:
        today = date.today()
        db.add_all(
            [
                Apoyo(
                    slug="cerrado",
                    title="Cerrado",
                    status="published",
                    closes_at=today - timedelta(days=200),
                ),  # outside keep_closed_days
                Apoyo(
                    slug="abierto",
                    title="Abierto",
                    status="published",
                    closes_at=today + timedelta(days=5),
                ),
                Apoyo(
                    slug="destacado",
                    title="Destacado",
                    status="published",
                    featured=True,
                    permanent=True,
                ),
                Apoyo(slug="borrador", title="Borrador", status="draft"),
            ]
        )
        db.commit()
    finally:
        db.close()
    r = client.get("/storefront/apoyos", headers=STOREFRONT_HEADERS)
    data = r.json()["data"]
    slugs = [d["slug"] for d in data]
    assert "borrador" not in slugs
    assert "cerrado" not in slugs
    assert slugs[0] == "destacado"  # featured first
    assert "confidence" not in data[0] and "ai_notes" not in data[0]


# ── jobs endpoints ───────────────────────────────────────────────────────────


def test_jobs_require_token():
    assert client.post("/jobs/apoyos/tick").status_code == 403
    r = client.post("/jobs/apoyos/tick", headers=JOB_HEADERS)
    assert r.status_code == 200


def test_jobs_step_unknown_run_is_404():
    r = client.post("/jobs/apoyos/step", json={"run_id": 999999}, headers=JOB_HEADERS)
    assert r.status_code == 404


# ── dispatch ─────────────────────────────────────────────────────────────────


def test_post_runs_conflicts_when_one_already_queued():
    db = db_session()
    try:
        db.add(ApoyoRun(trigger="manual", status="queued", topics=[]))
        db.commit()
    finally:
        db.close()
    assert client.post("/apoyos/runs").status_code == 409


def test_post_runs_dispatches_to_github(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(requests, "post", fake_post)
    r = client.post("/apoyos/runs")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "queued" and "dispatch_error" not in data
    assert calls and calls[0][1]["event_type"] == "apoyos-discover"


def test_post_runs_keeps_queued_on_dispatch_failure(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "post", fake_post)
    r = client.post("/apoyos/runs")
    data = r.json()["data"]
    assert data["status"] == "queued" and "dispatch_error" in data


def test_site_publish_dispatches(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        assert json["event_type"] == "apoyos-publish"
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(requests, "post", fake_post)
    r = client.post("/apoyos/site/publish")
    assert r.status_code == 200 and r.json()["data"]["status"] == "dispatched"


def test_site_status_reads_latest_workflow_run(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "workflow_runs": [
                    {
                        "status": "completed",
                        "conclusion": "success",
                        "created_at": "2026-10-20T00:00:00Z",
                        "html_url": "https://x",
                    }
                ]
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)
    r = client.get("/apoyos/site/status")
    assert r.status_code == 200
    assert r.json()["data"]["conclusion"] == "success"
