"""
Hermetic tests for Agroproyectos: POST /storefront/agro-projects through the
plan/productos/riego/quote/questions/contact/events storefront endpoints, and
the admin list/detail/patch/stats/settings endpoints. SQLite behind a get_db
override, Claude replaced by monkeypatching services.ai_usage.create_message,
network blocked.

Run: venv/bin/python -m pytest tests/test_agroproyectos.py -q
"""

import json
import os
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import app
from models import (
    AgroProject,
    AgroProjectMessage,
    AIUsage,
    Apoyo,
    Base,
    Task,
    TaskCategory,
    TaskUser,
    get_db,
)
from services import agroproyectos, ai_usage, background, mailer

_tmpdir = tempfile.mkdtemp(prefix="agroproyectos_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'agro.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
client = TestClient(app)

KEY = "orders-test-key"
HEADERS = {"X-API-Key": KEY}
HERNAN = "hernan@example.com"
SEGUIMIENTO_ID = 8


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


def setup_module(module):
    app.dependency_overrides[get_db] = _override_get_db
    db = TestingSession()
    try:
        db.add_all(
            [
                TaskUser(
                    id=2,
                    email="jared@impag.mx",
                    display_name="Jared",
                    role="admin",
                    is_active=True,
                ),
                TaskUser(
                    id=5,
                    email=HERNAN,
                    display_name="Hernán",
                    role="member",
                    is_active=True,
                ),
            ]
        )
        db.flush()
        db.add(
            TaskCategory(
                id=SEGUIMIENTO_ID, name="Seguimiento a cotizaciones", created_by=2
            )
        )
        db.commit()
    finally:
        db.close()


def teardown_module(module):
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("STOREFRONT_ORDERS_API_KEY", KEY)
    monkeypatch.setenv("WEB_ORDER_NOTIFY_EMAILS", HERNAN)
    monkeypatch.setenv("WEB_ORDER_ASSIGNEE", HERNAN)
    monkeypatch.delenv("WEB_QUOTES_MAX_PER_HOUR", raising=False)
    monkeypatch.setattr(ai_usage, "claude_api_key", "test-key")


OUTBOX: list[dict] = []


@pytest.fixture(autouse=True)
def _outbox(monkeypatch):
    OUTBOX.clear()

    def capture(message, *, tag, idempotency_key=None):
        OUTBOX.append({**message, "tag": tag})
        return True

    monkeypatch.setattr(mailer, "send", capture)


@pytest.fixture(autouse=True)
def _sync_background(monkeypatch):
    """plan/productos/riego run on services.background's thread pool, which
    opens its OWN db session via models.SessionLocal — point that at THIS
    module's test engine (not models.engine's, which conftest.py reserves for
    the shared sqlite file) and make jobs run inline so assertions right
    after a POST see the finished result, per services/background.py."""
    monkeypatch.setattr(background, "SessionLocal", TestingSession)
    background.set_sync_for_tests(True)
    yield
    background.set_sync_for_tests(False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a hermetic test")

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture(autouse=True)
def _clean_db():
    """Each AgroProject test gets a clean slate (shared engine across the module)."""
    yield
    db = TestingSession()
    try:
        db.query(AgroProjectMessage).delete()
        db.query(AIUsage).delete()
        db.query(Task).filter(Task.title.startswith("Agroproyecto")).delete()
        db.query(AgroProject).delete()
        db.query(Apoyo).delete()
        db.commit()
    finally:
        db.close()


# ── fake Claude ──────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(
        self,
        data=None,
        text=None,
        model="claude-opus-5",
        usage=None,
        stop_reason="end_turn",
    ):
        if text is None:
            text = json.dumps(data if data is not None else {})
        self.content = [SimpleNamespace(type="text", text=text)]
        self.model = model
        self.stop_reason = stop_reason
        self.usage = usage or SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )


PLAN_WITH_IRRIGATION = {
    "project_kind": "riego-goteo-maiz",
    "sector": "agricultura",
    "title": "Riego por goteo para maíz",
    "summary": "Plan para tecnificar el riego de maíz.",
    "viability": {"verdict": "viable", "explanation": "Con pozo y CFE es viable."},
    "budget": {
        "provided_mxn": 150000,
        "investment_min_mxn": 80000,
        "investment_max_mxn": 120000,
        "working_capital_mxn": 20000,
        "recommended_start_mxn": 100000,
        "gap_mxn": None,
        "breakdown": [
            {
                "concept": "Cintilla de riego",
                "category": "equipo",
                "amount_min_mxn": 30000,
                "amount_max_mxn": 50000,
                "notes": None,
            }
        ],
    },
    "key_numbers": [{"label": "Área", "value": "3 ha", "note": None}],
    "scale": {"recommendation": "3 hectáreas", "rationale": "Cabe en el presupuesto"},
    "options": [
        {
            "name": "Maíz forrajero",
            "kind": "variedad",
            "recommended": True,
            "why": "Buena adaptación",
            "pros": ["Rápido"],
            "cons": ["Requiere agua constante"],
        }
    ],
    "infrastructure": [
        {
            "item": "Cabezal de riego",
            "spec": "con filtros",
            "why": "protege la cintilla",
        }
    ],
    "economics": {
        "monthly_operating_cost_mxn": 5000,
        "monthly_income_min_mxn": 8000,
        "monthly_income_max_mxn": 15000,
        "months_to_first_income": 4,
        "payback_months_min": 12,
        "payback_months_max": 24,
        "assumptions": ["precio de venta estable"],
    },
    "timeline": [
        {"phase": "Instalación", "duration": "2 semanas", "tasks": ["Tender cintilla"]}
    ],
    "risks": [{"risk": "Sequía", "mitigation": "Pozo de respaldo"}],
    "next_steps": ["Confirmar pozo"],
    "needs_irrigation": True,
    "irrigation_hint": "Goteo para maíz en 3 ha",
    "product_needs": ["cintilla de riego", "bomba solar"],
    "apoyo_sectors": ["agricultura"],
    "questions_to_confirm": ["¿Cuántos litros da el pozo?"],
}

PLAN_NO_IRRIGATION = {
    **PLAN_WITH_IRRIGATION,
    "needs_irrigation": False,
    "sector": "ganaderia",
}

PRODUCTOS_DATA = {
    "items": [
        {
            "handle": "cintilla-riego-1",
            "quantity": 10,
            "unit_label": "rollo",
            "why": "riego",
            "priority": "esencial",
        },
        {
            "handle": "no-existe",
            "quantity": 1,
            "unit_label": None,
            "why": "x",
            "priority": "opcional",
        },
    ],
    "not_in_catalog": [
        {"item": "Pozo profundo", "why": "obra civil", "estimated_cost_mxn": 50000}
    ],
    "notes": "Arranque básico.",
}

RIEGO_DATA = {
    "system_type": "goteo",
    "area_m2": 30000,
    "crop_or_use": "maíz",
    "assumptions": ["suelo franco"],
    "design": {
        "water_need_m3_day": 10,
        "emitter": "goteros 4 L/h",
        "emitter_flow_lph": 4,
        "emitter_spacing_m": 0.3,
        "row_spacing_m": 0.8,
        "emitters_count": 1000,
        "total_flow_lph": 1,  # deliberately wrong: the backend must recompute
        "total_flow_lpm": 1,
        "sectors": 2,
        "irrigation_hours_per_day": 4,
        "mainline": "PVC 4in",
        "submain": "PVC 2in",
        "laterals_m": 3000,
        "filtration": "filtro de malla",
        "fertigation": None,
        "pump": {
            "type": "sumergible",
            "hp": 3,
            "head_m": 40,
            "flow_lpm": 200,
            "power_source": "solar",
        },
        "storage": None,
    },
    "calculations": [
        {"label": "Caudal total", "formula": "emisores × caudal", "result": "4000 L/h"}
    ],
    "items": [
        {
            "handle": "cintilla-riego-1",
            "quantity": 20,
            "unit_label": "rollo",
            "why": "riego",
            "priority": "esencial",
        }
    ],
    "not_in_catalog": [],
    "notes": "Diseño base.",
}


def _fake_create_message(data=None, text=None, raises=None, **_kwargs):
    if raises is not None:
        raise raises
    return FakeResponse(data=data, text=text)


@pytest.fixture
def plan_claude(monkeypatch):
    calls = []

    def fake(**kwargs):
        feature = kwargs["feature"]
        calls.append(feature)
        if feature == "agroproyectos.plan":
            response = FakeResponse(data=PLAN_WITH_IRRIGATION)
        elif feature == "agroproyectos.productos":
            response = FakeResponse(data=PRODUCTOS_DATA)
        elif feature == "agroproyectos.riego":
            response = FakeResponse(data=RIEGO_DATA)
        elif feature == "agroproyectos.chat":
            response = FakeResponse(text="Respuesta de prueba.")
        else:
            raise AssertionError(feature)
        # Mirror create_message's own ledger write (on THIS module's test
        # engine, not models.SessionLocal's — see ai_usage.record), so admin
        # usage/cost reads (built from the AIUsage table) have rows to show.
        tokens = ai_usage.tokens_of(response.usage)
        db = TestingSession()
        try:
            db.add(
                AIUsage(
                    feature=feature,
                    model=response.model,
                    requested_model=kwargs.get("model"),
                    input_tokens=tokens.input,
                    output_tokens=tokens.output,
                    cost_usd=ai_usage.cost_usd(response.model, tokens),
                    duration_ms=10,
                    status="ok",
                    ref_type=kwargs.get("ref_type"),
                    ref_id=kwargs.get("ref_id"),
                )
            )
            db.commit()
        finally:
            db.close()
        return response

    monkeypatch.setattr(ai_usage, "create_message", fake)
    return calls


CANDIDATES = [
    {
        "handle": "cintilla-riego-1",
        "title": "Cintilla de riego 16mm",
        "category_slug": "sistemas-de-riego",
        "price": 450.0,
        "price_total": 522.0,
        "iva_rate": 0.16,
        "unit_label": "rollo",
        "product_id": 101,
    }
]


def _land(has=True, area=3, unit="ha"):
    return {"has_land": has, "area": area, "unit": unit}


def _create_body(**over):
    body = {
        "producer_type": "nuevo",
        "idea": "Quiero poner riego por goteo para maíz en 3 hectáreas",
        "current_operation": None,
        "state": "durango",
        "municipio": "Canatlán",
        "land": _land(),
        "water": "pozo",
        "electricity": "no",
        "experience": "algo",
        "goal": "negocio",
        "timeline": "3-meses",
        "budget_mxn": 150000,
        "budget_unknown": False,
        "wants_financing": False,
        "contact": {
            "name": "Cliente Prueba",
            "phone": "+526181234567",
            "email": "cliente@example.com",
        },
        "utm": None,
    }
    body.update(over)
    return body


def _create() -> dict:
    r = client.post("/storefront/agro-projects", json=_create_body(), headers=HEADERS)
    assert r.status_code == 201, r.text
    return r.json()


def _project(token) -> AgroProject:
    db = TestingSession()
    try:
        return db.query(AgroProject).filter(AgroProject.token == token).one()
    finally:
        db.close()


# ── creation ─────────────────────────────────────────────────────────────────


def test_create_requires_the_orders_key():
    r = client.post("/storefront/agro-projects", json=_create_body())
    assert r.status_code == 401


def test_create_succeeds_and_is_pending():
    data = _create()
    assert data["status"] == "pending"
    assert data["ref"].startswith("AGR-")
    project = _project(data["token"])
    assert project.contact_phone == "+526181234567"
    assert project.lead_status == "nuevo"


def test_create_rejects_markup_and_bad_phone():
    r = client.post(
        "/storefront/agro-projects",
        json=_create_body(idea="<script>x</script> quiero sembrar maíz aquí"),
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "markup_in:idea" in r.json()["detail"]["problems"]

    r = client.post(
        "/storefront/agro-projects",
        json=_create_body(contact={"name": "Cliente X", "phone": "123", "email": None}),
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "invalid_phone" in r.json()["detail"]["problems"]


def test_create_requires_contact_when_settings_say_so():
    r = client.post(
        "/storefront/agro-projects", json=_create_body(contact=None), headers=HEADERS
    )
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "contact_required"


def test_create_disabled_is_503():
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"enabled": False}, "test")
    finally:
        db.close()
    r = client.post("/storefront/agro-projects", json=_create_body(), headers=HEADERS)
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "tool_disabled"
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"enabled": True}, "test")
    finally:
        db.close()


def test_create_rate_limit():
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"max_per_hour": 1}, "test")
    finally:
        db.close()
    assert (
        client.post(
            "/storefront/agro-projects", json=_create_body(), headers=HEADERS
        ).status_code
        == 201
    )
    r = client.post("/storefront/agro-projects", json=_create_body(), headers=HEADERS)
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "rate_limited"
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"max_per_hour": 30}, "test")
    finally:
        db.close()


def test_create_budget_exceeded():
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"daily_budget_usd": 1}, "test")
        db.add(
            AIUsage(
                feature="agroproyectos.plan",
                model="claude-opus-5",
                cost_usd=5,
                status="ok",
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.post("/storefront/agro-projects", json=_create_body(), headers=HEADERS)
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "budget_exceeded"
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"daily_budget_usd": 25}, "test")
    finally:
        db.close()


# ── AgroPublic hides contact ─────────────────────────────────────────────────


def test_public_project_never_exposes_phone_or_email():
    data = _create()
    r = client.get(f"/storefront/agro-projects/{data['token']}", headers=HEADERS)
    project = r.json()["project"]
    blob = json.dumps(project)
    assert "cliente@example.com" not in blob and "6181234567" not in blob
    assert project["first_name"] == "Cliente"
    assert "contact" not in project["inputs"] and "utm" not in project["inputs"]


def test_unknown_token_is_404():
    r = client.get(
        "/storefront/agro-projects/00000000-0000-0000-0000-000000000000",
        headers=HEADERS,
    )
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"


# ── plan / productos / riego: state machine + idempotency ───────────────────


def _get(token) -> dict:
    r = client.get(f"/storefront/agro-projects/{token}", headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["project"]


def test_full_generation_flow_with_irrigation(plan_claude):
    data = _create()
    token = data["token"]

    # The job runs via services.background: even with sync-for-tests mode
    # (which finishes it before submit() returns), the job commits through
    # ITS OWN db session, so the SUBMITTING request's own response still
    # reflects the pre-job state — exactly like real async execution, where
    # the job genuinely hasn't run yet by the time the 202 is built. A
    # separate request (GET, or the next POST) opens a fresh session and
    # sees what was actually committed, which is how the storefront polls.
    r = client.post(f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS)
    assert r.status_code == 202, r.text
    assert r.json()["project"]["generating"] == "plan"

    project = _get(token)
    assert project["status"] == "plan_ready" and project["plan"]["title"]
    assert project["generating"] is None

    # idempotent: calling plan again does not re-call Claude, and answers 200
    plan_claude.clear()
    r2 = client.post(
        f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS
    )
    assert r2.status_code == 200
    assert r2.json()["project"]["status"] == "plan_ready"
    assert plan_claude == []

    r = client.post(
        f"/storefront/agro-projects/{token}/productos",
        json={"candidates": CANDIDATES},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    assert r.json()["project"]["generating"] == "productos"

    project = _get(token)
    assert project["status"] == "products_ready"
    # the unknown handle was dropped, the known one enriched
    handles = [i["handle"] for i in project["products"]["items"]]
    assert handles == ["cintilla-riego-1"]
    assert project["products"]["items"][0]["price_total"] == 522.0
    assert project["products"]["items"][0]["line_total"] == 5220.0
    assert project["products"]["catalog_total_mxn"] == 5220.0

    r = client.post(
        f"/storefront/agro-projects/{token}/riego",
        json={"candidates": CANDIDATES},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    assert r.json()["project"]["generating"] == "riego"

    project = _get(token)
    assert project["status"] == "done"
    # the backend recomputes total_flow_lph/lpm instead of trusting the model
    design = project["irrigation"]["design"]
    assert design["total_flow_lph"] == 4000.0
    assert design["total_flow_lpm"] == pytest.approx(66.67, abs=0.01)
    assert project["irrigation"]["catalog_total_mxn"] == pytest.approx(
        522.0 * 20, abs=0.01
    )

    # apoyos matching: publish one that matches sector + state
    db = TestingSession()
    try:
        db.add(
            Apoyo(
                slug="riego-tecnificado",
                status="published",
                title="Riego tecnificado 2026",
                organism="SADER",
                level="federal",
                states=["nacional"],
                sectors=["agricultura"],
                amount_text="Hasta 50%",
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.get(f"/storefront/agro-projects/{token}", headers=HEADERS)
    apoyos = r.json()["project"]["apoyos"]
    assert apoyos and apoyos[0]["slug"] == "riego-tecnificado"


def test_productos_needs_plan_first():
    data = _create()
    r = client.post(
        f"/storefront/agro-projects/{data['token']}/productos",
        json={"candidates": CANDIDATES},
        headers=HEADERS,
    )
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_ready"


def test_riego_skipped_when_plan_says_not_needed(monkeypatch):
    def fake(**kwargs):
        if kwargs["feature"] == "agroproyectos.plan":
            return FakeResponse(data=PLAN_NO_IRRIGATION)
        if kwargs["feature"] == "agroproyectos.productos":
            return FakeResponse(data=PRODUCTOS_DATA)
        raise AssertionError(kwargs["feature"])

    monkeypatch.setattr(ai_usage, "create_message", fake)
    data = _create()
    token = data["token"]
    client.post(f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS)
    client.post(
        f"/storefront/agro-projects/{token}/productos",
        json={"candidates": CANDIDATES},
        headers=HEADERS,
    )
    assert _get(token)["status"] == "done"
    r = client.post(
        f"/storefront/agro-projects/{token}/riego",
        json={"candidates": CANDIDATES},
        headers=HEADERS,
    )
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_ready"


def test_ai_failure_is_recorded_on_the_project_not_surfaced_as_an_http_error(
    monkeypatch,
):
    """The Claude call now runs via services.background, after the response
    has already been built from the (synchronously-submitted) 202 — a
    mid-step AIUnavailable can no longer become the POST's HTTP status; it
    only shows up in the polled project (status=error, error=<reason>)."""

    def fake(**kwargs):
        raise ai_usage.AIUnavailable("ai_timeout")

    monkeypatch.setattr(ai_usage, "create_message", fake)
    data = _create()
    token = data["token"]
    r = client.post(f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS)
    assert r.status_code == 202, r.text
    # the submitting request's own body still shows the pre-job state (see
    # test_full_generation_flow_with_irrigation); the next request is what
    # observes the job's result.
    assert r.json()["project"]["generating"] == "plan"
    project = _get(token)
    assert project["status"] == "error" and project["error"] == "ai_timeout"
    assert project["generating"] is None
    row = _project(token)
    assert row.status == "error" and row.error == "ai_timeout"

    # retryable: calling plan again submits a fresh job
    def fake_ok(**kwargs):
        return FakeResponse(data=PLAN_WITH_IRRIGATION)

    monkeypatch.setattr(ai_usage, "create_message", fake_ok)
    r2 = client.post(
        f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS
    )
    assert r2.status_code == 202
    assert _get(token)["status"] == "plan_ready"


def test_no_api_key_is_recorded_as_error_too(monkeypatch):
    def fake(**kwargs):
        raise ai_usage.AIUnavailable("no_api_key")

    monkeypatch.setattr(ai_usage, "create_message", fake)
    data = _create()
    token = data["token"]
    r = client.post(f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS)
    assert r.status_code == 202, r.text
    assert _get(token)["error"] == "no_api_key"


def test_second_plan_request_while_generating_is_accepted_without_resubmitting(
    monkeypatch,
):
    """A real deployment can have a plan job genuinely in flight; this test
    simulates that by marking the project "generating" directly (sync-for-
    tests mode always finishes a *submitted* job before returning, so there
    is no other way to observe the "busy" branch from a test)."""
    data = _create()
    token = data["token"]
    db = TestingSession()
    try:
        project = db.query(AgroProject).filter(AgroProject.token == token).one()
        agroproyectos._set_generating(db, project, "plan")
    finally:
        db.close()

    def fake(**kwargs):
        raise AssertionError("must not call Claude while already generating")

    monkeypatch.setattr(ai_usage, "create_message", fake)
    r = client.post(f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS)
    assert r.status_code == 202, r.text
    assert r.json()["project"]["generating"] == "plan"


def test_stale_generating_mark_reads_as_error_and_can_be_retried(plan_claude):
    data = _create()
    token = data["token"]
    stale_since = (
        datetime.now(timezone.utc)
        - timedelta(minutes=agroproyectos.GENERATING_STALE_MINUTES + 1)
    ).isoformat()
    db = TestingSession()
    try:
        project = db.query(AgroProject).filter(AgroProject.token == token).one()
        agroproyectos._set_generating(db, project, "plan")
        project.events = {**project.events, "_generating_since": stale_since}
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(project, "events")
        db.commit()
    finally:
        db.close()

    # GET reports it as resolved even before any retry POST
    r = client.get(f"/storefront/agro-projects/{token}", headers=HEADERS)
    project = r.json()["project"]
    assert project["generating"] is None
    assert project["status"] == "error" and project["error"] == "timeout"

    # POST retries: a dead "generating" mark does not block a fresh submit
    r2 = client.post(
        f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS
    )
    assert r2.status_code == 202, r2.text
    assert _get(token)["status"] == "plan_ready"
    assert "agroproyectos.plan" in plan_claude


# ── quote ────────────────────────────────────────────────────────────────────


def test_quote_review_mode_needs_review(plan_claude):
    data = _create()
    token = data["token"]
    client.post(f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS)
    body = {
        "items": [
            {
                "handle": "cintilla-riego-1",
                "product_id": 101,
                "description": "Cintilla de riego 16mm",
                "unit_label": "rollo",
                "quantity": 10,
                "unit_price": 450.0,
                "iva_rate": 0.16,
            }
        ]
    }
    r = client.post(
        f"/storefront/agro-projects/{token}/quote", json=body, headers=HEADERS
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["needs_review"] is True
    project = _project(token)
    assert project.quote_id is not None and project.lead_status == "cotizado"


def test_quote_instant_mode():
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"quote_mode": "instant"}, "test")
    finally:
        db.close()
    data = _create()
    token = data["token"]
    body = {
        "items": [
            {
                "handle": "cintilla-riego-1",
                "product_id": 101,
                "description": "Cintilla de riego 16mm",
                "unit_label": "rollo",
                "quantity": 10,
                "unit_price": 450.0,
                "iva_rate": 0.16,
            }
        ]
    }
    r = client.post(
        f"/storefront/agro-projects/{token}/quote", json=body, headers=HEADERS
    )
    assert r.status_code == 201, r.text
    assert r.json()["needs_review"] is False
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"quote_mode": "review"}, "test")
    finally:
        db.close()


def test_quote_needs_contact():
    # project without contact (contact_required disabled for this call)
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"contact_required": False}, "test")
    finally:
        db.close()
    r = client.post(
        "/storefront/agro-projects", json=_create_body(contact=None), headers=HEADERS
    )
    token = r.json()["token"]
    body = {
        "items": [
            {
                "handle": "cintilla-riego-1",
                "product_id": 101,
                "description": "Cintilla de riego 16mm",
                "unit_label": "rollo",
                "quantity": 1,
                "unit_price": 450.0,
                "iva_rate": 0.16,
            }
        ]
    }
    r = client.post(
        f"/storefront/agro-projects/{token}/quote", json=body, headers=HEADERS
    )
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "contact_required"
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"contact_required": True}, "test")
    finally:
        db.close()


# ── contact request ──────────────────────────────────────────────────────────


def test_contact_request_creates_one_task_and_is_idempotent():
    data = _create()
    token = data["token"]
    body = {
        "preferred": "whatsapp",
        "best_time": "tardes",
        "message": "Llámenme por favor",
    }
    r = client.post(
        f"/storefront/agro-projects/{token}/contact", json=body, headers=HEADERS
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    project = _project(token)
    assert project.contact_requested_at is not None
    assert project.lead_status == "contactado"

    db = TestingSession()
    try:
        tasks = db.query(Task).filter(Task.title.contains(project.ref)).all()
        assert len(tasks) == 1
        assert tasks[0].priority == "high" and tasks[0].category_id == SEGUIMIENTO_ID
    finally:
        db.close()
    staff_mail = [m for m in OUTBOX if m.get("tag", "").startswith("agro_contact")]
    assert staff_mail

    # repeated contact request does not create a second task
    r2 = client.post(
        f"/storefront/agro-projects/{token}/contact", json=body, headers=HEADERS
    )
    assert r2.status_code == 200
    db = TestingSession()
    try:
        tasks = db.query(Task).filter(Task.title.contains(project.ref)).all()
        assert len(tasks) == 1
    finally:
        db.close()


# ── events ───────────────────────────────────────────────────────────────────


def test_events_are_recorded():
    data = _create()
    token = data["token"]
    r = client.post(
        f"/storefront/agro-projects/{token}/events",
        json={"event": "whatsapp_click"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    project = _project(token)
    assert "whatsapp_click" in (project.events or {})


# ── chat ─────────────────────────────────────────────────────────────────────


def test_chat_question_and_cap(plan_claude):
    data = _create()
    token = data["token"]
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"max_questions": 1}, "test")
    finally:
        db.close()
    r = client.post(
        f"/storefront/agro-projects/{token}/questions",
        json={"question": "¿Cuánto cuesta?"},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["answer"] == "Respuesta de prueba." and out["remaining"] == 0
    r2 = client.post(
        f"/storefront/agro-projects/{token}/questions",
        json={"question": "¿Otra pregunta?"},
        headers=HEADERS,
    )
    assert (
        r2.status_code == 429 and r2.json()["detail"]["reason"] == "questions_exhausted"
    )
    db = TestingSession()
    try:
        agroproyectos.write_settings(db, {"max_questions": 10}, "test")
    finally:
        db.close()


# ── admin ────────────────────────────────────────────────────────────────────


def test_admin_list_detail_patch_stats(plan_claude):
    data = _create()
    token = data["token"]
    client.post(f"/storefront/agro-projects/{token}/plan", json={}, headers=HEADERS)

    r = client.get("/agroproyectos/projects")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["total"] >= 1
    assert "nuevo" in body["lead_counts"]

    project_id = _project(token).id
    r = client.get(f"/agroproyectos/projects/{project_id}")
    admin = r.json()["data"]
    assert (
        admin["plan"]["title"]
        and admin["usage"]
        and admin["usage"][0]["feature"] == "agroproyectos.plan"
    )
    assert admin["whatsapp_url"] == "https://wa.me/526181234567"

    r = client.patch(
        f"/agroproyectos/projects/{project_id}",
        json={"lead_status": "ganado", "staff_notes": "Cerrado"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["lead_status"] == "ganado"

    r = client.get("/agroproyectos/stats?days=30")
    assert r.status_code == 200, r.text
    stats = r.json()["data"]
    assert stats["totals"]["projects"] >= 1
    assert stats["lead_status_counts"]["ganado"] >= 1


def test_admin_settings_get_and_put():
    r = client.get("/agroproyectos/settings")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["settings"]["model"] == "claude-opus-5"

    r = client.put("/agroproyectos/settings", json={"max_questions": 3})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["settings"]["max_questions"] == 3
    r = client.put("/agroproyectos/settings", json={"max_questions": 10})
    assert r.json()["data"]["settings"]["max_questions"] == 10

    r = client.put("/agroproyectos/settings", json={"quote_mode": "not-a-mode"})
    assert r.status_code == 422
