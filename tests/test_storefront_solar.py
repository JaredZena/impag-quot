"""
Hermetic tests for the cotizador solar: POST /storefront/solar-quotes and the
sizing in services/solar_quotes.py. SQLite behind a get_db override, Claude
replaced by canned answers, network blocked.

Run: venv/bin/python -m pytest tests/test_storefront_solar.py -q
"""

import base64
import os
import socket
import tempfile
from decimal import Decimal

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import app
from models import Base, Quote, TaskCategory, TaskUser, get_db
from services import mailer, solar_quotes
from services.web_orders import read_notes_block

_tmpdir = tempfile.mkdtemp(prefix="storefront_solar_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'solar.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
client = TestClient(app)

KEY = "orders-test-key"
HEADERS = {"X-API-Key": KEY}
HERNAN = "hernan@example.com"
PDF = base64.b64encode(b"%PDF-1.4 fake bill").decode()
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0 fake photo").decode()


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
        db.add(TaskCategory(id=8, name="Seguimiento a cotizaciones", created_by=2))
        db.commit()
    finally:
        db.close()


def teardown_module(module):
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _bill(**over):
    bill = {
        "es_recibo_cfe": True,
        "calidad": "completa",
        "tarifa": "1F",
        "periodo": "bimestral",
        "periodo_inicio": "2026-07-01",
        "periodo_fin": "2026-08-31",
        "consumo_kwh": 820,
        "importe_periodo": 1890.0,
        "total_a_pagar": 1890.0,
        "cargo_fijo": None,
        "historial": [
            {"periodo": p, "kwh": k, "importe": i}
            for p, k, i in [
                ("jul-ago", 820, 1890),
                ("may-jun", 910, 2150),
                ("mar-abr", 540, 980),
                ("ene-feb", 430, 760),
                ("nov-dic", 460, 810),
                ("sep-oct", 700, 1420),
            ]
        ],
        "demanda_kw": None,
        "numero_servicio": "641040700647",
        "titular": "CLIENTE PRUEBA",
        "municipio": "Canatlán",
        "estado": "Durango",
        "codigo_postal": "34450",
        "observaciones": None,
    }
    bill.update(over)
    return bill


class FakeClaude:
    def __init__(self):
        self.bill = _bill()
        self.pick = None
        self.advice_fails = False
        self.calls = []

    def __call__(self, content, schema, effort, timeout, max_tokens):
        if "es_recibo_cfe" in schema["properties"]:
            self.calls.append("read")
            return dict(self.bill)
        self.calls.append("advise")
        if self.advice_fails:
            raise solar_quotes.SolarUnavailable("ai_error")
        advice = {
            "titulo": "Tu sistema",
            "resumen": "Leímos tu recibo.",
            "puntos": ["Consumo alto"],
            "ahorro": None,
            "siguiente_paso": "Paga en línea.",
        }
        if "handle" in schema["properties"]:
            handles = schema["properties"]["handle"]["enum"]
            advice["handle"] = self.pick if self.pick in handles else handles[0]
        return advice


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("STOREFRONT_ORDERS_API_KEY", KEY)
    monkeypatch.setenv("WEB_ORDER_NOTIFY_EMAILS", HERNAN)
    monkeypatch.setenv("WEB_ORDER_ASSIGNEE", HERNAN)
    for name in (
        "SOLAR_INTERCONECTADO_BASE",
        "SOLAR_INTERCONECTADO_PER_KW",
        "SOLAR_PANEL_W",
        "SOLAR_MAX_INSTANT_KWP",
        "SOLAR_AI_MAX_PER_HOUR",
        "WEB_QUOTES_MAX_PER_HOUR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(solar_quotes, "claude_api_key", "test-key")
    solar_quotes._ai_calls.clear()


@pytest.fixture
def claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(solar_quotes, "_structured_call", fake)
    return fake


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

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


_phones = iter(range(6181000001, 6189999999))

AISLADO = [
    {
        "handle": f"kit-aislado-{wh}",
        "title": f"Kit aislado {wh}",
        "price": price,
        "iva_rate": 0.16,
        "specs": {"kind": "aislado", "daily_wh": wh},
    }
    for wh, price in [(300, 17500), (750, 44000), (1000, 55000)]
]
POZO = [
    {
        "handle": "kit-pozo-35",
        "title": "Kit pozo 35 m 46 L/min",
        "price": 28205.64,
        "iva_rate": 0.16,
        "product_id": None,
        "unit_label": "Kit",
        "specs": {"kind": "pozo", "head_m": 35, "flow_lpm": 46},
    },
    {
        "handle": "kit-pozo-95",
        "title": "Kit pozo 95 m 58 L/min",
        "price": 57888.54,
        "iva_rate": 0.16,
        "unit_label": "Kit",
        "specs": {"kind": "pozo", "head_m": 95, "flow_lpm": 58},
    },
    {
        "handle": "kit-pozo-112",
        "title": "Kit pozo 112 m 100 L/min",
        "price": 74850.90,
        "iva_rate": 0.16,
        "unit_label": "Kit",
        "specs": {"kind": "pozo", "head_m": 112, "flow_lpm": 100},
    },
    {
        "handle": "kit-sup-550",
        "title": "Bomba superficie 550 W",
        "price": 20173.80,
        "iva_rate": 0.16,
        "unit_label": "Kit",
        "specs": {"kind": "superficie"},
    },
]


def _body(
    system="interconectado", files=None, answers=None, candidates=None, **customer
):
    return {
        "system": system,
        "files": (
            [{"media_type": "application/pdf", "data": PDF}] if files is None else files
        ),
        "answers": answers or {},
        "customer": {
            "name": "Cliente Prueba",
            "phone": str(next(_phones)),
            "email": "cliente@example.com",
            "location": "Canatlán, Durango",
            **customer,
        },
        "candidates": candidates if candidates is not None else [],
    }


def _post(body):
    return client.post("/storefront/solar-quotes", json=body, headers=HEADERS)


def _quote(ref) -> Quote:
    db = TestingSession()
    try:
        quote = db.query(Quote).filter(Quote.quote_number == ref).one()
        list(quote.items)
        return quote
    finally:
        db.close()


# ── sizing ───────────────────────────────────────────────────────────────────


def test_state_key_reads_bill_and_free_text():
    assert solar_quotes.state_key("DGO.") == "durango"
    assert solar_quotes.state_key("Canatlán, Durango") == "durango"
    assert solar_quotes.state_key("Nuevo León") == "nuevo leon"
    assert solar_quotes.state_key("CDMX") == "ciudad de mexico"
    assert solar_quotes.state_key("") is None


def test_period_comes_from_the_printed_dates():
    usage = solar_quotes.usage_from_bill(
        _bill(periodo="mensual", historial=[], consumo_kwh=600), None
    )
    assert usage.periodo == "bimestral"
    assert usage.daily_kwh == pytest.approx(600 / (365 / 6), abs=0.01)


def test_interconectado_price_follows_the_owner_formula():
    usage = solar_quotes.usage_from_bill(_bill(), None)
    sizing = solar_quotes.size_interconectado(usage)
    kwp = sizing.facts["kwp"]
    assert sizing.facts["panels"] == 4 and kwp == pytest.approx(2.48)
    assert sizing.lines[0]["unit_price"] == pytest.approx(round(33000 + 9000 * kwp, -2))
    assert sizing.facts["coverage_pct"] == 100
    assert sizing.review == []


def test_fixed_charge_is_not_counted_as_saving():
    usage = solar_quotes.usage_from_bill(
        _bill(
            tarifa="PDBT",
            cargo_fijo=222.74,
            historial=[],
            consumo_kwh=15,
            importe_periodo=321.74,
        ),
        None,
    )
    facts = solar_quotes.size_interconectado(usage).facts
    assert facts["annual_saving_mxn"] < 321.74 * 6 * 0.25


# ── endpoint ─────────────────────────────────────────────────────────────────


def test_interconectado_quote_is_payable_and_mails_the_bill(claude):
    r = _post(_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["needs_review"] is False and data["status"] == "sent"
    assert data["bill"]["tarifa"] == "1F" and data["sizing"]["kwp"] == pytest.approx(
        2.48
    )
    assert data["advice"]["titulo"] == "Tu sistema"
    quote = _quote(data["quote_number"])
    assert len(quote.items) == 1 and "interconectado" in quote.items[0].description
    assert quote.total == pytest.approx(Decimal(str(data["total"])))
    block = read_notes_block(quote.notes)
    assert (
        block["origin"] == "cotizacion" and block["solar"]["system"] == "interconectado"
    )
    assert block["solar"]["bill"]["numero_servicio"] == "641040700647"
    # The buyer-facing explanation sits outside the JSON block.
    assert "Leímos tu recibo." in quote.notes.split("[/Pedido web]")[1]
    staff = [m for m in OUTBOX if HERNAN in m["to"]]
    assert staff and staff[0]["attachments"][0]["content"] == b"%PDF-1.4 fake bill"
    assert "Cotizador solar" in staff[0]["html"]
    assert claude.calls == ["read", "advise"]


def test_installation_outside_durango_goes_to_review(claude):
    claude.bill = _bill(estado="Jalisco", municipio="Zapopan")
    data = _post(_body(location="Zapopan, Jalisco")).json()
    assert data["needs_review"] is True
    assert "solar_install_outside" in data["review_reasons"]
    assert _quote(data["quote_number"]).status == "draft"


def test_aislado_uses_claudes_pick_within_the_choices(claude):
    claude.pick = "kit-aislado-750"
    data = _post(
        _body("aislado", candidates=AISLADO, answers={"usage": "Solo luces"})
    ).json()
    assert data["items"][0]["handle"] == "kit-aislado-750"
    assert data["items"][0]["unit_price"] == 44000
    quote = _quote(data["quote_number"])
    assert quote.status == "sent"
    assert "Comentarios del cliente:\nSolo luces" in quote.notes


def test_advice_failure_falls_back_to_template(claude):
    claude.advice_fails = True
    data = _post(_body("aislado", candidates=AISLADO)).json()
    assert (
        data["items"][0]["handle"] == "kit-aislado-1000"
    )  # largest, consumption is high
    assert data["advice"]["titulo"] == "Kit aislado 1000"


def test_bombeo_without_bill_picks_by_head_and_water(claude):
    answers = {"source": "pozo", "depth_m": 70, "lift_m": 5, "daily_liters": 15000}
    data = _post(_body("bombeo", files=[], answers=answers, candidates=POZO)).json()
    assert data["items"][0]["handle"] == "kit-pozo-112"
    assert data["needs_review"] is False and data["bill"] is None
    assert claude.calls == ["advise"]


def test_bombeo_too_deep_and_surface_go_to_review(claude):
    deep = _post(
        _body(
            "bombeo",
            files=[],
            answers={"source": "pozo", "depth_m": 150},
            candidates=POZO,
        )
    ).json()
    assert (
        deep["review_reasons"] == ["solar_no_fit"]
        and deep["items"][0]["handle"] == "kit-pozo-112"
    )
    surface = _post(
        _body(
            "bombeo",
            files=[],
            answers={"source": "superficie", "depth_m": 6},
            candidates=POZO,
        )
    ).json()
    assert surface["review_reasons"] == ["solar_specs"] and surface["status"] == "draft"


def test_not_a_bill_is_422(claude):
    claude.bill = _bill(es_recibo_cfe=False)
    r = _post(_body())
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "bill_unreadable"
    assert "advise" not in claude.calls


def test_rejects_bad_files_and_missing_bill(claude):
    assert _post(_body("aislado", files=[], candidates=AISLADO)).status_code == 422
    fake = [{"media_type": "application/pdf", "data": JPEG}]
    r = _post(_body(files=fake))
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "file_type_mismatch"
    photo = [{"media_type": "image/jpeg", "data": JPEG}]
    assert _post(_body(files=photo)).status_code == 200
    assert client.post("/storefront/solar-quotes", json=_body()).status_code == 401


def test_ai_cap_answers_429(claude, monkeypatch):
    monkeypatch.setenv("SOLAR_AI_MAX_PER_HOUR", "1")
    assert _post(_body()).status_code == 200
    r = _post(_body())
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "too_many_requests"


def test_no_api_key_is_503(monkeypatch):
    monkeypatch.setattr(solar_quotes, "claude_api_key", None)
    r = _post(_body())
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "no_api_key"
