"""
Hermetic tests for the campaign planner (service guards + routes + models).
SQLite in-memory, FastAPI dependency overrides, Anthropic client mocked — no
network, no real DB, no LLM calls.

Run: venv/bin/python -m pytest tests/test_campaigns.py -q
"""

import os

# Must run BEFORE any project import: importing models.py creates the engine
# from DATABASE_URL and (by default) runs Base.metadata.create_all against it.
# ALEMBIC_RUNNING=1 is the existing escape hatch in models.py that skips
# create_all; the fake DATABASE_URL guarantees the module-level engine can
# never touch the real database (config's load_dotenv does not override
# already-set environment variables).
os.environ["DATABASE_URL"] = "postgresql://test:test@campaign-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

import json
from datetime import date

import anthropic as anthropic_sdk
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.campaign_planner as cp
from auth import verify_google_token
from models import (
    Campaign,
    CampaignItem,
    CampaignPhase,
    Product,
    ProductCategory,
    SocialPost,
    Task,
    TaskCategory,
    TaskUser,
    get_db,
)
from routes.campaigns import router as campaigns_router

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine)
for m in (
    TaskUser,
    TaskCategory,
    Task,
    ProductCategory,
    Product,
    SocialPost,
    Campaign,
    CampaignPhase,
    CampaignItem,
):
    m.__table__.create(bind=engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()
app.include_router(campaigns_router)
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_google_token] = lambda: {"email": "dev@local.test"}
client = TestClient(app)


# ── Anthropic mock (lowest level: the client class in the service module) ────


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, payload, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.content = [_FakeBlock(json.dumps(payload))]


class _FakeAnthropic:
    """Queue-driven stand-in for anthropic.Anthropic; guard code stays real."""

    queue = []
    calls = []

    def __init__(self, api_key=None):
        self.messages = self

    def create(self, **kwargs):
        _FakeAnthropic.calls.append(kwargs)
        assert _FakeAnthropic.queue, "no queued fake LLM response"
        entry = _FakeAnthropic.queue.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return entry


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    _FakeAnthropic.queue = []
    _FakeAnthropic.calls = []
    monkeypatch.setattr(cp.anthropic, "Anthropic", _FakeAnthropic)
    yield


@pytest.fixture(autouse=True)
def seed_and_clean_db():
    db = TestingSession()
    # id=2 matches quote_followup's FOLLOWUP_SYSTEM_USER_ID default; email
    # matches the authenticated dev user so get_current_task_user finds it.
    db.add(
        TaskUser(
            id=2,
            email="dev@local.test",
            display_name="Dev",
            role="admin",
            is_active=True,
        )
    )
    db.commit()
    db.close()
    yield
    db = TestingSession()
    for M in (
        CampaignItem,
        CampaignPhase,
        Campaign,
        Task,
        SocialPost,
        Product,
        ProductCategory,
        TaskUser,
    ):
        db.query(M).delete()
    db.commit()
    db.close()


# ── Payload helpers ──────────────────────────────────────────────────────────

START = date(2026, 8, 3)  # a Monday


def _brief_payload(**over):
    data = {
        "title": "Campaña kits de bombeo solar",
        "objective": "Generar demanda de kits de bombeo solar en temporada de riego",
        "audience": "Productores con parcelas sin acceso a CFE",
        "size": "mediana",
        "size_rationale": "Temporada de riego con disparador estacional",
        "duration_days": 28,
        "goals": [
            {"goal": "Cotizaciones", "metric": "cotizaciones generadas", "target": "10"}
        ],
        "key_messages": ["Riego sin depender de la CFE"],
        "channel_plan": {
            "channels": [
                {"channel": "fb-post", "frequency_per_week": 3, "rationale": "alcance"},
                {
                    "channel": "wa-status",
                    "frequency_per_week": 5,
                    "rationale": "clientes",
                },
                {
                    "channel": "wa-broadcast",
                    "frequency_per_week": 1,
                    "rationale": "VIP",
                },
            ],
            "whatsapp_notify": True,
            "whatsapp_rationale": "Campaña mediana con disparador estacional",
        },
        "research": {
            "seasonality_notes": "Temporada de riego mayo-agosto",
            "market_context": "Durango, 79% temporal",
            "important_dates": [],
            "product_focus": ["Bombeo solar"],
        },
    }
    data.update(over)
    return data


def _plan_item(**over):
    item = {
        "kind": "post",
        "channel": "fb-post",
        "scheduled_date": "2026-08-04",
        "title": "Post de lanzamiento",
        "description": "Infografía con CTA a WhatsApp",
        "content": None,
    }
    item.update(over)
    return item


def _plan_payload(items, name="Lanzamiento"):
    return {
        "phases": [
            {
                "name": name,
                "description": "Fase principal",
                "goal": "Generar interés",
                "start_date": "2026-08-03",
                "end_date": "2026-08-30",
                "items": items,
            }
        ]
    }


def _campaign_obj(**over):
    c = Campaign(
        topic="kits de bombeo solar",
        title="Campaña bombeo solar",
        size="mediana",
        status="draft",
        start_date=START,
        end_date=date(2026, 8, 30),
        goals=[],
        key_messages=[],
        research={},
        channel_plan={
            "channels": [
                {"channel": "fb-post", "frequency_per_week": 4, "rationale": ""},
                {"channel": "wa-broadcast", "frequency_per_week": 2, "rationale": ""},
            ],
            "whatsapp_notify": True,
            "whatsapp_rationale": "",
        },
    )
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _make_campaign_via_api(brief_over=None):
    _FakeAnthropic.queue.append(_FakeResponse(_brief_payload(**(brief_over or {}))))
    r = client.post(
        "/campaigns/generate",
        json={
            "topic": "kits de bombeo solar para riego",
            "start_date": START.isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. Brief guard: chica forces whatsapp_notify off ─────────────────────────


def test_brief_guard_chica_forces_whatsapp_off():
    _FakeAnthropic.queue.append(_FakeResponse(_brief_payload(size="chica")))
    db = TestingSession()
    try:
        brief = cp.generate_campaign_brief(db, "kits solares", START)
    finally:
        db.close()
    assert brief["size"] == "chica"
    assert brief["channel_plan"]["whatsapp_notify"] is False


# ── 2. Brief guard: frequency clamp + unknown channel dropped ────────────────


def test_brief_guard_clamps_frequency_and_drops_unknown_channel():
    payload = _brief_payload(
        channel_plan={
            "channels": [
                {"channel": "fb-post", "frequency_per_week": 9, "rationale": ""},
                {"channel": "youtube", "frequency_per_week": 2, "rationale": ""},
                {"channel": "wa-broadcast", "frequency_per_week": 5, "rationale": ""},
            ],
            "whatsapp_notify": True,
            "whatsapp_rationale": "",
        }
    )
    _FakeAnthropic.queue.append(_FakeResponse(payload))
    db = TestingSession()
    try:
        brief = cp.generate_campaign_brief(db, "tema", START)
    finally:
        db.close()
    channels = {
        c["channel"]: c["frequency_per_week"] for c in brief["channel_plan"]["channels"]
    }
    assert "youtube" not in channels
    assert channels["fb-post"] == 4  # clamped to the fb-post cap
    assert channels["wa-broadcast"] == 2  # clamped to the wa-broadcast cap


def test_brief_duration_weeks_overrides_model_duration():
    _FakeAnthropic.queue.append(_FakeResponse(_brief_payload(duration_days=90)))
    db = TestingSession()
    try:
        brief = cp.generate_campaign_brief(db, "tema", START, duration_weeks=2)
    finally:
        db.close()
    assert brief["start_date"] == START
    assert brief["end_date"] == date(2026, 8, 16)  # 14 days inclusive


# ── 3. Plan guards ───────────────────────────────────────────────────────────


def test_plan_guard_drops_wa_items_when_notify_off():
    campaign = _campaign_obj(
        channel_plan={
            "channels": [
                {"channel": "fb-post", "frequency_per_week": 4, "rationale": ""}
            ],
            "whatsapp_notify": False,
            "whatsapp_rationale": "",
        }
    )
    items = [
        _plan_item(),
        _plan_item(
            kind="whatsapp",
            channel="wa-broadcast",
            title="Difusión",
            content="Hola, ya llegaron los kits.",
        ),
        _plan_item(channel="wa-status", title="Estado"),
        _plan_item(
            kind="task", channel="fb-post", title="Diseñar artes", scheduled_date=None
        ),
    ]
    _FakeAnthropic.queue.append(_FakeResponse(_plan_payload(items)))
    db = TestingSession()
    try:
        plan = cp.generate_campaign_plan(db, campaign)
    finally:
        db.close()
    kept = plan["phases"][0]["items"]
    kinds_channels = [(i["kind"], i["channel"]) for i in kept]
    assert ("whatsapp", "wa-broadcast") not in kinds_channels
    assert ("post", "wa-status") not in kinds_channels
    task_item = next(i for i in kept if i["kind"] == "task")
    assert task_item["channel"] is None  # nulled for task/research kinds
    assert ("post", "fb-post") in kinds_channels


def test_plan_guard_drops_channels_not_in_channel_plan():
    # Plan only includes fb-post; tiktok/fb-reel are globally known channels
    # but were never selected by the strategy, so items on them must be dropped.
    campaign = _campaign_obj(
        channel_plan={
            "channels": [
                {"channel": "fb-post", "frequency_per_week": 4, "rationale": ""}
            ],
            "whatsapp_notify": True,
            "whatsapp_rationale": "",
        }
    )
    items = [
        _plan_item(),
        _plan_item(channel="tiktok", title="Carrusel TikTok"),
        _plan_item(channel="fb-reel", title="Reel"),
        _plan_item(kind="task", channel=None, scheduled_date=None, title="Prep"),
    ]
    _FakeAnthropic.queue.append(_FakeResponse(_plan_payload(items)))
    db = TestingSession()
    try:
        plan = cp.generate_campaign_plan(db, campaign)
    finally:
        db.close()
    kept = plan["phases"][0]["items"]
    channels = [i["channel"] for i in kept if i["kind"] == "post"]
    assert channels == ["fb-post"]  # off-plan channels dropped
    assert any(i["kind"] == "task" for i in kept)  # channel-less kinds unaffected


def test_plan_guard_clamps_out_of_range_dates_and_nulls_invalid():
    campaign = _campaign_obj()
    items = [
        _plan_item(scheduled_date="2026-09-15", title="Muy tarde"),
        _plan_item(scheduled_date="2026-07-01", title="Muy temprano"),
        _plan_item(
            kind="research",
            channel=None,
            scheduled_date="no-es-fecha",
            title="Investigar precios",
        ),
    ]
    _FakeAnthropic.queue.append(_FakeResponse(_plan_payload(items)))
    db = TestingSession()
    try:
        plan = cp.generate_campaign_plan(db, campaign)
    finally:
        db.close()
    by_title = {i["title"]: i for i in plan["phases"][0]["items"]}
    assert by_title["Muy tarde"]["scheduled_date"] == date(
        2026, 8, 30
    )  # clamped to end
    assert by_title["Muy temprano"]["scheduled_date"] == START  # clamped to start
    assert (
        by_title["Investigar precios"]["scheduled_date"] is None
    )  # parse failure -> null


def test_plan_guard_enforces_weekly_caps():
    campaign = _campaign_obj()
    # 6 fb-posts in the same ISO week (Aug 3-9 2026); cap is 4.
    items = [
        _plan_item(scheduled_date=f"2026-08-0{d}", title=f"Post {d}")
        for d in range(3, 9)
    ]
    _FakeAnthropic.queue.append(_FakeResponse(_plan_payload(items)))
    db = TestingSession()
    try:
        plan = cp.generate_campaign_plan(db, campaign)
    finally:
        db.close()
    fb_posts = [i for i in plan["phases"][0]["items"] if i["channel"] == "fb-post"]
    assert len(fb_posts) == 4
    assert [i["title"] for i in fb_posts] == ["Post 3", "Post 4", "Post 5", "Post 6"]


# ── 4. POST /campaigns/generate end-to-end ───────────────────────────────────


def test_generate_endpoint_persists_and_returns_campaign():
    _FakeAnthropic.queue.append(_FakeResponse(_brief_payload()))
    r = client.post(
        "/campaigns/generate",
        json={
            "topic": "kits de bombeo solar para riego",
            "start_date": "2026-08-03",
            "duration_weeks": 4,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Campaña kits de bombeo solar"
    assert body["status"] == "draft"
    assert body["size"] == "mediana"
    assert body["start_date"] == "2026-08-03"
    assert body["end_date"] == "2026-08-30"  # 4 weeks = 28 days inclusive
    assert body["created_by"] == "dev@local.test"
    assert body["generation_model"] == cp.CAMPAIGN_MODEL
    assert body["phases"] == []
    assert body["channel_plan"]["whatsapp_notify"] is True

    db = TestingSession()
    row = db.query(Campaign).filter(Campaign.id == body["id"]).one()
    assert row.topic == "kits de bombeo solar para riego"
    assert row.channel_plan["channels"]  # JSON column round-trips on sqlite
    db.close()


def test_generate_endpoint_rejects_blank_topic():
    r = client.post("/campaigns/generate", json={"topic": "   "})
    assert r.status_code == 422


def test_generate_endpoint_maps_billing_402():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(402, request=req)
    _FakeAnthropic.queue.append(
        anthropic_sdk.APIStatusError(
            "credit balance is too low", response=resp, body=None
        )
    )
    r = client.post("/campaigns/generate", json={"topic": "tema"})
    assert r.status_code == 402
    assert "ANTHROPIC_CREDITS_EXHAUSTED" in r.json()["detail"]


def test_generate_endpoint_maps_refusal_to_502():
    _FakeAnthropic.queue.append(_FakeResponse({}, stop_reason="refusal"))
    r = client.post("/campaigns/generate", json={"topic": "tema"})
    assert r.status_code == 502
    assert "rechazó" in r.json()["detail"]


# ── 5. generate-plan persists and regeneration replaces ──────────────────────


def test_generate_plan_persists_and_regeneration_replaces():
    campaign = _make_campaign_via_api()
    cid = campaign["id"]

    plan_a = _plan_payload(
        [
            _plan_item(title="Post A1"),
            _plan_item(kind="task", channel=None, scheduled_date=None, title="Prep A"),
        ]
    )
    _FakeAnthropic.queue.append(_FakeResponse(plan_a))
    r = client.post(f"/campaigns/{cid}/generate-plan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["phases"]) == 1
    assert body["items_total"] == 2

    # Regenerate: old rows must be replaced, not accumulated.
    plan_b = {
        "phases": [
            _plan_payload([_plan_item(title="Post B1")], name="Expectativa")["phases"][
                0
            ],
            _plan_payload(
                [_plan_item(title="Post B2", scheduled_date="2026-08-11")],
                name="Lanzamiento",
            )["phases"][0],
        ]
    }
    plan_b["phases"][1]["start_date"] = "2026-08-10"
    _FakeAnthropic.queue.append(_FakeResponse(plan_b))
    r2 = client.post(f"/campaigns/{cid}/generate-plan")
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert len(body2["phases"]) == 2
    assert body2["items_total"] == 2

    db = TestingSession()
    assert db.query(CampaignPhase).filter(CampaignPhase.campaign_id == cid).count() == 2
    items = db.query(CampaignItem).filter(CampaignItem.campaign_id == cid).all()
    assert len(items) == 2
    # Old plan rows were replaced, not accumulated (sqlite may reuse row ids,
    # so assert on content rather than id disjointness).
    assert sorted(i.title for i in items) == ["Post B1", "Post B2"]
    db.close()


def test_generate_plan_404_for_missing_campaign():
    r = client.post("/campaigns/99999/generate-plan")
    assert r.status_code == 404


# ── 6. activate creates Tasks with distinct numbers, idempotently ────────────


def test_activate_creates_tasks_with_distinct_numbers_and_is_idempotent():
    campaign = _make_campaign_via_api()
    cid = campaign["id"]
    plan = _plan_payload(
        [
            _plan_item(
                kind="task", channel=None, scheduled_date=None, title="Diseñar artes"
            ),
            _plan_item(
                kind="whatsapp",
                channel="wa-broadcast",
                scheduled_date="2026-08-05",
                title="Difusión de lanzamiento",
                content="Ya llegaron los kits de bombeo solar. ¿Le comparto la información?",
            ),
            _plan_item(
                kind="research",
                channel=None,
                scheduled_date=None,
                title="Investigar competencia",
            ),
            _plan_item(title="Post de lanzamiento"),  # post: must NOT create a Task
        ]
    )
    _FakeAnthropic.queue.append(_FakeResponse(plan))
    assert client.post(f"/campaigns/{cid}/generate-plan").status_code == 200

    r = client.post(f"/campaigns/{cid}/activate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tasks_created"] == 3
    assert body["campaign"]["status"] == "active"

    db = TestingSession()
    tasks = db.query(Task).all()
    assert len(tasks) == 3
    numbers = [t.task_number for t in tasks]
    assert len(set(numbers)) == 3  # DISTINCT task numbers (db.flush gotcha)
    assert all(t.title.startswith("[Campaña] ") for t in tasks)
    assert all(t.created_by == 2 for t in tasks)
    wa_task = next(t for t in tasks if "Difusión" in t.title)
    assert wa_task.priority == "high"
    assert "Mensaje propuesto:" in wa_task.description
    assert "Fecha objetivo: 2026-08-05" in wa_task.description

    items = db.query(CampaignItem).filter(CampaignItem.campaign_id == cid).all()
    taskable = [i for i in items if i.kind in ("task", "whatsapp", "research")]
    assert all(i.task_id is not None for i in taskable)
    assert all(i.status == "ready" for i in taskable)
    post_item = next(i for i in items if i.kind == "post")
    assert post_item.task_id is None
    db.close()

    # Re-activation creates nothing new.
    r2 = client.post(f"/campaigns/{cid}/activate")
    assert r2.status_code == 200
    assert r2.json()["tasks_created"] == 0
    db = TestingSession()
    assert db.query(Task).count() == 3
    db.close()


def test_regenerate_preserves_materialized_items_and_activate_skips_them():
    """Regenerating the plan must NOT wipe items that already have a Task or
    SocialPost — deleting them resets the idempotency keys and a later
    activate/generate-post would duplicate the materialized rows."""
    campaign = _make_campaign_via_api()
    cid = campaign["id"]

    plan_a = _plan_payload(
        [
            _plan_item(kind="task", channel=None, scheduled_date=None, title="Prep A"),
            _plan_item(title="Post A"),
        ]
    )
    _FakeAnthropic.queue.append(_FakeResponse(plan_a))
    body = client.post(f"/campaigns/{cid}/generate-plan").json()
    post_item_id = next(
        i["id"] for i in body["phases"][0]["items"] if i["kind"] == "post"
    )

    # Materialize both items: a Task (activate) and a SocialPost (generate-post).
    assert client.post(f"/campaigns/{cid}/activate").json()["tasks_created"] == 1
    _FakeAnthropic.queue.append(
        _FakeResponse(
            {
                "caption": "Caption A",
                "image_prompt": "Prompt A",
                "post_type": "Promoción puntual",
                "content_tone": "Promotional",
            }
        )
    )
    assert (
        client.post(f"/campaigns/items/{post_item_id}/generate-post").status_code == 200
    )

    # Regenerate: fresh plan, but the two materialized items must survive.
    plan_b = _plan_payload(
        [
            _plan_item(kind="task", channel=None, scheduled_date=None, title="Prep B"),
            _plan_item(title="Post B"),
        ],
        name="Plan nuevo",
    )
    _FakeAnthropic.queue.append(_FakeResponse(plan_b))
    r = client.post(f"/campaigns/{cid}/generate-plan")
    assert r.status_code == 200, r.text
    body2 = r.json()
    phase_titles = [i["title"] for p in body2["phases"] for i in p["items"]]
    assert sorted(phase_titles) == ["Post B", "Prep B"]
    orphans = body2.get("orphan_items") or []
    assert sorted(o["title"] for o in orphans) == ["Post A", "Prep A"]
    assert all(o["phase_id"] is None for o in orphans)
    orphan_task = next(o for o in orphans if o["title"] == "Prep A")
    orphan_post = next(o for o in orphans if o["title"] == "Post A")
    assert orphan_task["task_id"] is not None
    assert orphan_post["social_post_id"] is not None
    assert body2["items_total"] == 4  # includes the preserved orphans

    # Re-activation only materializes the NEW task item — no duplicates.
    r2 = client.post(f"/campaigns/{cid}/activate")
    assert r2.status_code == 200
    assert r2.json()["tasks_created"] == 1  # Prep B only
    db = TestingSession()
    assert db.query(Task).count() == 2  # Prep A + Prep B, no duplicate for Prep A
    assert db.query(SocialPost).count() == 1
    db.close()

    # generate-post on the preserved post item still 409s (link intact).
    assert (
        client.post(f"/campaigns/items/{post_item_id}/generate-post").status_code == 409
    )


def test_activate_matches_task_user_email_case_insensitively():
    """A mixed-case task_user email must still resolve to the real user, not
    silently fall back to the system user (models.get_current_task_user is
    case-sensitive; the route must match like quote_followup does)."""
    db = TestingSession()
    db.add(
        TaskUser(
            id=7,
            email="Jared.Zena@Local.Test",  # mixed case on purpose
            display_name="Jared",
            role="admin",
            is_active=True,
        )
    )
    db.commit()
    db.close()

    app.dependency_overrides[verify_google_token] = lambda: {
        "email": "jared.zena@LOCAL.test"
    }
    try:
        campaign = _make_campaign_via_api()
        cid = campaign["id"]
        _FakeAnthropic.queue.append(
            _FakeResponse(
                _plan_payload(
                    [
                        _plan_item(
                            kind="task", channel=None, scheduled_date=None, title="Prep"
                        )
                    ]
                )
            )
        )
        assert client.post(f"/campaigns/{cid}/generate-plan").status_code == 200
        assert client.post(f"/campaigns/{cid}/activate").json()["tasks_created"] == 1
    finally:
        app.dependency_overrides[verify_google_token] = lambda: {
            "email": "dev@local.test"
        }

    db = TestingSession()
    task = db.query(Task).one()
    assert task.created_by == 7  # the mixed-case user, NOT the system user (2)
    assert task.assigned_to == 7
    db.close()


# ── 7. PATCH item status validation ──────────────────────────────────────────


def test_patch_item_rejects_bad_status_and_accepts_valid():
    campaign = _make_campaign_via_api()
    cid = campaign["id"]
    _FakeAnthropic.queue.append(_FakeResponse(_plan_payload([_plan_item()])))
    body = client.post(f"/campaigns/{cid}/generate-plan").json()
    item_id = body["phases"][0]["items"][0]["id"]

    bad = client.patch(f"/campaigns/items/{item_id}", json={"status": "no-existe"})
    assert bad.status_code == 422

    ok = client.patch(f"/campaigns/items/{item_id}", json={"status": "done"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "done"


def test_patch_campaign_rejects_bad_status():
    campaign = _make_campaign_via_api()
    r = client.patch(f"/campaigns/{campaign['id']}", json={"status": "no-existe"})
    assert r.status_code == 422


# ── generate-post: SocialPost creation for a post item ───────────────────────


def test_generate_post_creates_social_post_and_409s_on_repeat():
    campaign = _make_campaign_via_api()
    cid = campaign["id"]
    _FakeAnthropic.queue.append(_FakeResponse(_plan_payload([_plan_item()])))
    body = client.post(f"/campaigns/{cid}/generate-plan").json()
    item_id = body["phases"][0]["items"][0]["id"]

    _FakeAnthropic.queue.append(
        _FakeResponse(
            {
                "caption": "Riego sin CFE con bombeo solar. Escríbenos por WhatsApp.",
                "image_prompt": "Panel solar junto a una parcela en Durango",
                "post_type": "Promoción puntual",
                "content_tone": "Promotional",
            }
        )
    )
    r = client.post(f"/campaigns/items/{item_id}/generate-post")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["item"]["social_post_id"] == payload["social_post_id"]
    assert payload["item"]["status"] == "ready"

    db = TestingSession()
    post = db.query(SocialPost).filter(SocialPost.id == payload["social_post_id"]).one()
    assert post.topic  # NOT NULL
    assert post.topic_hash and len(post.topic_hash) == 64  # NOT NULL sha256
    assert post.channel == "fb-post"
    assert post.status == "planned"
    assert post.date_for == date(2026, 8, 4)
    db.close()

    # Second call must 409 (already generated).
    r2 = client.post(f"/campaigns/items/{item_id}/generate-post")
    assert r2.status_code == 409


# ── list + delete ────────────────────────────────────────────────────────────


def test_list_filters_by_status_and_counts_items():
    campaign = _make_campaign_via_api()
    cid = campaign["id"]
    _FakeAnthropic.queue.append(
        _FakeResponse(
            _plan_payload(
                [
                    _plan_item(),
                    _plan_item(title="Otro post", scheduled_date="2026-08-05"),
                ]
            )
        )
    )
    body = client.post(f"/campaigns/{cid}/generate-plan").json()
    item_id = body["phases"][0]["items"][0]["id"]
    client.patch(f"/campaigns/items/{item_id}", json={"status": "done"})

    listed = client.get("/campaigns").json()
    assert len(listed) == 1
    assert listed[0]["items_total"] == 2
    assert listed[0]["items_done"] == 1

    assert client.get("/campaigns", params={"status": "active"}).json() == []
    assert client.get("/campaigns", params={"status": "bogus"}).status_code == 422


def test_delete_campaign_removes_children_but_keeps_tasks():
    campaign = _make_campaign_via_api()
    cid = campaign["id"]
    _FakeAnthropic.queue.append(
        _FakeResponse(
            _plan_payload(
                [
                    _plan_item(
                        kind="task", channel=None, scheduled_date=None, title="Prep"
                    )
                ]
            )
        )
    )
    assert client.post(f"/campaigns/{cid}/generate-plan").status_code == 200
    assert client.post(f"/campaigns/{cid}/activate").json()["tasks_created"] == 1

    r = client.delete(f"/campaigns/{cid}")
    assert r.status_code == 204

    db = TestingSession()
    assert db.query(Campaign).count() == 0
    assert db.query(CampaignPhase).count() == 0
    assert db.query(CampaignItem).count() == 0
    assert db.query(Task).count() == 1  # tasks created from the campaign survive
    db.close()
