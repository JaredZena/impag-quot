"""
Hermetic tests for registering quotes from the *Cotización Enviada* WhatsApp
message (services/quote_capture.py, POST /quotes/capture) and the manual status
board (POST /quotes/{id}/status). SQLite in-memory — no network, no real DB.

Run: venv/bin/python -m pytest tests/test_quote_capture.py -q
"""

import os

# Must run BEFORE any project import (same guard as tests/test_tools.py): the
# fake DATABASE_URL keeps models.py's module-level engine away from production
# and ALEMBIC_RUNNING=1 skips its create_all.
os.environ["DATABASE_URL"] = "postgresql://test:test@quote-capture-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routes.quotes as quotes_routes
from models import Base, Customer, PosSale, Quote, QuoteItem, get_db
from services import quote_capture
from services.quote_capture import CaptureError, parse_cotizaciones, parse_single

DEV_EMAIL = "dev@local.test"  # what verify_google_token returns with DISABLE_AUTH
TABLES = [Customer.__table__, Quote.__table__, QuoteItem.__table__, PosSale.__table__]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

TEMPLATE = """*Cotización Enviada 400926DGO*
Cliente: Miguel Cordero
Ubicación: Nuevo Ideal, Dgo.
Entrega: *Nuevo Ideal, Dgo*
Material/Proyecto:
Bolsa para vivero 17x17 cal 400"""


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    app = FastAPI()
    app.include_router(quotes_routes.router)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _db():
    return TestingSession()


def _add_quote(**fields) -> int:
    db = _db()
    q = Quote(
        quote_number=fields.pop("quote_number", "COT-IMPAG-030626DGO"),
        status=fields.pop("status", "sent"),
        customer_name=fields.pop("customer_name", "Mary"),
        customer_phone=fields.pop("customer_phone", "S/N"),
        created_by=fields.pop("created_by", "backfill"),
        subtotal=Decimal("35650"),
        iva_amount=Decimal("0"),
        total=Decimal("35650"),
        **fields,
    )
    db.add(q)
    db.commit()
    qid = q.id
    db.close()
    return qid


# ==================== Parser ====================


def test_parses_the_template():
    p = parse_single(TEMPLATE)
    assert p.folio == "400926DGO"
    assert p.quote_number == "COT-IMPAG-400926DGO"
    assert p.tag is None
    assert p.cliente == "Miguel Cordero"
    assert p.ubicacion == "Nuevo Ideal, Dgo"
    assert p.entrega == "Nuevo Ideal, Dgo"
    assert p.material == "Bolsa para vivero 17x17 cal 400"


def test_parses_old_format_tag_and_edited_marker():
    text = """*Cotizacion Enviada 150626 (Actualización)*
Cliente: David Guido
Ubicación: Morelia, Michoacán..
Entrega: *Morelia Michoacan*
Material/Proyecto:
Cañón y áspersores falcon. <This message was edited>"""
    p = parse_single(text)
    assert p.folio == "150626"  # "\\nCliente" must not be read as a state
    assert p.quote_number == "COT-IMPAG-150626"
    assert p.tag == "Actualización"
    assert p.ubicacion == "Morelia, Michoacán"
    assert p.material == "Cañón y áspersores falcon"


def test_parses_flattened_line_and_whatsapp_web_prefix():
    text = (
        "[13:23, 2/10/2026] Impag Tech: cotizacion Enviada 310826DGO Cliente: Ejido Esfuerzos "
        "Ubicación: Esfuerzos unidos Entrega: Esfuerzos unidos, Nuevo Ideal "
        "Material/Proyecto: Malla sombra 50% Total: $12,450.50"
    )
    p = parse_single(text)
    assert p.folio == "310826DGO"
    assert p.cliente == "Ejido Esfuerzos"
    assert p.entrega == "Esfuerzos unidos, Nuevo Ideal"
    assert p.material == "Malla sombra 50%"
    assert p.total == Decimal("12450.50")


def test_tolerates_the_cotiacion_typo():
    p = parse_single(
        "Cotiacion Enviada 300926QUI Cliente: Grupo JURENA Ubicación: Cancun Quintanaroo. "
        "Entrega: Cancun Quintanaroo Material/Proyecto: 2 sacos Multicote"
    )
    assert p.quote_number == "COT-IMPAG-300926QUI"
    assert p.cliente == "Grupo JURENA"


def test_rejects_unusable_pastes():
    with pytest.raises(CaptureError, match="No encontré"):
        parse_single("Balance de Venta 021026 Cliente: VICOR")
    with pytest.raises(CaptureError, match="una cotización a la vez"):
        parse_single(TEMPLATE + "\n" + TEMPLATE.replace("400926", "410926"))
    with pytest.raises(CaptureError, match="Cliente"):
        parse_single("*Cotización Enviada 400926DGO*\nMaterial/Proyecto: algo")
    assert (
        len(parse_cotizaciones(TEMPLATE + "\n" + TEMPLATE.replace("400926", "410926")))
        == 2
    )


# ==================== POST /quotes/capture ====================


def test_dry_run_previews_without_writing(client):
    r = client.post("/quotes/capture", json={"text": TEMPLATE, "dry_run": True})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["preview"]["action"] == "created"
    assert data["preview"]["quote_number"] == "COT-IMPAG-400926DGO"
    assert data["quote"] is None
    assert any("Sin total" in w for w in data["warnings"])
    db = _db()
    assert db.query(Quote).count() == 0
    db.close()


def test_capture_creates_a_sent_quote_linked_to_the_customer(client):
    db = _db()
    db.add(Customer(display_name="Miguel Cordero", phone_e164="+526181234567"))
    db.commit()
    customer_id = db.query(Customer).first().id
    db.close()

    r = client.post(
        "/quotes/capture",
        json={"text": TEMPLATE, "total": "$18,500", "customer_phone": "618 123 4567"},
    )
    assert r.status_code == 200, r.text
    q = r.json()["data"]["quote"]
    assert r.json()["data"]["preview"]["action"] == "created"
    assert q["quote_number"] == "COT-IMPAG-400926DGO"
    assert q["status"] == "sent"
    assert q["sent_at"] is not None
    assert q["total"] == 18500 and q["subtotal"] == 18500 and q["iva_amount"] == 0
    assert q["customer_phone"] == "+526181234567"
    assert q["customer_id"] == customer_id
    assert q["customer_location"] == "Nuevo Ideal, Dgo"
    assert q["created_by"] == DEV_EMAIL and q["assigned_to"] == DEV_EMAIL
    assert "Entrega: Nuevo Ideal, Dgo" in q["notes"]
    assert "Material/Proyecto: Bolsa para vivero 17x17 cal 400" in q["notes"]
    assert "[Registro]" in q["notes"]


def test_capture_backdates_sent_at_to_noon_business_time(client):
    r = client.post(
        "/quotes/capture", json={"text": TEMPLATE, "sent_date": "2026-09-30"}
    )
    assert r.status_code == 200, r.text
    # SQLite drops tz on read; noon America/Mexico_City (UTC-6) = 18:00 UTC
    assert r.json()["data"]["quote"]["sent_at"].startswith("2026-09-30T18:00")


def test_same_folio_again_is_a_resend_not_a_duplicate(client):
    first = client.post(
        "/quotes/capture", json={"text": TEMPLATE, "total": "18500"}
    ).json()
    qid = first["data"]["quote"]["id"]
    db = _db()
    q = db.get(Quote, qid)
    q.status = "rejected"
    q.followup_count = 3
    db.commit()
    db.close()

    update = TEMPLATE.replace("400926DGO*", "400926DGO (Contraoferta)*")
    r = client.post("/quotes/capture", json={"text": update, "total": "16,900"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["preview"]["action"] == "updated"
    q = data["quote"]
    assert q["id"] == qid
    assert q["status"] == "sent"  # a counter-offer revives a lost quote
    assert q["total"] == 16900
    assert "[Contraoferta]" in q["notes"]
    db = _db()
    assert db.query(Quote).count() == 1
    assert db.get(Quote, qid).followup_count == 0  # new follow-up cycle
    db.close()


def test_bare_folio_matches_the_backfilled_quote(client):
    qid = _add_quote(quote_number="COT-IMPAG-030626DGO", status="expired")
    text = "*Cotizacion Enviada 030626 (Actualización)*\nCliente: Mary\nMaterial/Proyecto: Vivero"
    r = client.post("/quotes/capture", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["quote"]["id"] == qid
    assert r.json()["data"]["quote"]["status"] == "sent"
    assert r.json()["data"]["quote"]["total"] == 35650  # no total given: kept


def test_resend_keeps_an_accepted_quote_accepted(client):
    qid = _add_quote(quote_number="COT-IMPAG-400926DGO", status="accepted")
    r = client.post("/quotes/capture", json={"text": TEMPLATE})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["quote"]["id"] == qid
    assert r.json()["data"]["quote"]["status"] == "accepted"
    assert any("Aceptada" in w for w in r.json()["data"]["warnings"])


def test_capture_rejects_bad_text_and_total(client):
    assert client.post("/quotes/capture", json={"text": "hola"}).status_code == 400
    r = client.post("/quotes/capture", json={"text": TEMPLATE, "total": "mucho"})
    assert r.status_code == 400


# ==================== POST /quotes/{id}/status ====================


def test_needs_work_and_lost_require_a_reason(client):
    qid = _add_quote()
    for status in ("needs_work", "rejected"):
        r = client.post(f"/quotes/{qid}/status", json={"status": status})
        assert r.status_code == 400
    r = client.post(
        f"/quotes/{qid}/status",
        json={"status": "needs_work", "reason": "Requiere algo más económico"},
    )
    assert r.status_code == 200, r.text
    q = r.json()["data"]
    assert q["status"] == "needs_work"
    assert "Por ajustar — Requiere algo más económico (dev@local.test)" in q["notes"]

    r = client.post(
        f"/quotes/{qid}/status", json={"status": "rejected", "reason": "Muy caro"}
    )
    assert r.json()["data"]["status"] == "rejected"
    assert "Perdida — Muy caro" in r.json()["data"]["notes"]


def test_accepted_and_expired_stamp_their_timestamps(client):
    qid = _add_quote()
    q = client.post(f"/quotes/{qid}/status", json={"status": "accepted"}).json()["data"]
    assert q["accepted_at"] is not None
    q = client.post(f"/quotes/{qid}/status", json={"status": "expired"}).json()["data"]
    assert q["accepted_at"] is None and q["expired_at"] is not None
    q = client.post(f"/quotes/{qid}/status", json={"status": "sent"}).json()["data"]
    assert q["expired_at"] is None


def test_status_guards(client):
    draft = _add_quote(quote_number="TEC-2026-0009", status="draft")
    web = _add_quote(quote_number="WEB-261002-ABC123", created_by="tienda-web")
    sold = _add_quote(quote_number="COT-IMPAG-500926DGO", status="accepted")
    db = _db()
    db.add(
        PosSale(
            folio="100926DGO",
            branch="DGO",
            sale_date=date(2026, 9, 30),
            quote_id=sold,
            status="completada",
            payment_method="efectivo",
            subtotal=Decimal("0"),
            iva_amount=Decimal("0"),
            total=Decimal("0"),
        )
    )
    db.commit()
    db.close()

    assert (
        client.post(f"/quotes/{draft}/status", json={"status": "sent"}).status_code
        == 400
    )
    assert (
        client.post(f"/quotes/{web}/status", json={"status": "accepted"}).status_code
        == 400
    )
    r = client.post(
        f"/quotes/{sold}/status", json={"status": "rejected", "reason": "x"}
    )
    assert r.status_code == 409 and "100926DGO" in r.json()["detail"]
    assert (
        client.post(f"/quotes/{draft}/status", json={"status": "bogus"}).status_code
        == 400
    )
    assert (
        client.post("/quotes/9999/status", json={"status": "sent"}).status_code == 404
    )


# ==================== PUT total + stats ====================


def test_total_editable_only_without_items(client):
    qid = _add_quote()
    r = client.put(f"/quotes/{qid}", json={"total": 42000})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 42000 and r.json()["data"]["iva_amount"] == 0

    db = _db()
    db.add(QuoteItem(quote_id=qid, description="Cañón", quantity=1, unit_price=100))
    db.commit()
    db.close()
    assert client.put(f"/quotes/{qid}", json={"total": 1}).status_code == 400


def test_stats_count_needs_work(client):
    qid = _add_quote()
    client.post(
        f"/quotes/{qid}/status",
        json={"status": "needs_work", "reason": "Cambiar marca"},
    )
    stats = client.get("/quotes/stats").json()["data"]
    assert stats["needs_work"] == 1


def test_sent_at_for_today_is_now():
    assert quote_capture.sent_at_for(None).date() >= date(2026, 1, 1)


def test_july_template_labels():
    """July 2026 messages: "Entrega en:", "Material:", "Monto Total:"."""
    p = parse_single(
        "Cotización Enviada 050726DGO Cliente: Miguel Serrano Ubicación: Nazas, "
        "Durango Entrega en: Corsarios Nazas Durango Material: Malla al 70% "
        "Monto Total: $11,820.00"
    )
    assert p.ubicacion == "Nazas, Durango"
    assert p.entrega == "Corsarios Nazas Durango"
    assert p.material == "Malla al 70%"
    assert p.total == Decimal("11820.00")


# ---------- *Solicitud de Cotización* → Por cotizar ----------

REQUEST = """Solicitud de Cotización
Proyecto/Material: Sistema de Riego e Invernadero 1 ha
Cliente: Camila Ortiz Aviña +52 393 131 2326v
Ubicación: La barca entre Jalisco y Michoacán.
Datos:  Área: 1 Ha Cultivo: Jitomate."""


def test_parses_the_request_template():
    p = quote_capture.parse_message(REQUEST)
    assert p.kind == "request"
    assert p.cliente == "Camila Ortiz Aviña"
    assert p.telefono == "+52 393 131 2326"
    assert p.ubicacion == "La barca entre Jalisco y Michoacán"
    assert p.material == "Sistema de Riego e Invernadero 1 ha"
    assert p.datos == "Área: 1 Ha Cultivo: Jitomate"


def test_request_becomes_por_cotizar_dated_the_day_asked(client):
    r = client.post(
        "/quotes/capture", json={"text": REQUEST, "sent_date": "2026-10-01"}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["preview"]["kind"] == "request"
    q = data["quote"]
    assert q["quote_number"] == "SOL-011026-1"
    assert q["status"] == "requested"
    assert q["sent_at"] is None
    assert q["created_at"].startswith("2026-10-01")
    assert q["customer_phone"] == "+523931312326"
    assert "Datos: Área: 1 Ha Cultivo: Jitomate" in q["notes"]

    # Asking again adds to the same request.
    again = client.post(
        "/quotes/capture", json={"text": REQUEST, "sent_date": "2026-10-02"}
    )
    assert again.json()["data"]["preview"]["action"] == "updated"
    db = _db()
    assert db.query(Quote).count() == 1
    assert db.query(Quote).first().notes.count("[Solicitud]") == 2
    stats = client.get("/quotes/stats").json()["data"]
    assert stats["requested"] == 1


def test_cotizacion_enviada_turns_the_request_into_the_quote(client):
    rid = client.post("/quotes/capture", json={"text": REQUEST}).json()["data"][
        "quote"
    ]["id"]
    sent = (
        "*Cotización Enviada 051026JAL*\nCliente: Camila Ortiz\n"
        "Material/Proyecto: Riego por goteo invernadero 1 ha"
    )
    preview = client.post(
        "/quotes/capture", json={"text": sent, "dry_run": True}
    ).json()
    assert preview["data"]["preview"]["action"] == "converted"
    assert preview["data"]["preview"]["request_number"].startswith("SOL-")
    assert preview["data"]["preview"]["quote_number"] == "COT-IMPAG-051026JAL"

    r = client.post("/quotes/capture", json={"text": sent, "total": "250,000"})
    q = r.json()["data"]["quote"]
    assert q["id"] == rid  # same row, history kept
    assert q["quote_number"] == "COT-IMPAG-051026JAL"
    assert q["status"] == "sent" and q["sent_at"] is not None
    assert q["total"] == 250000
    assert "de la solicitud SOL-" in q["notes"]
    assert _db().query(Quote).count() == 1


def test_request_matched_by_phone_when_the_quote_names_the_company(client):
    client.post(
        "/quotes/capture",
        json={
            "text": "Solicitud de cotización Cliente: Pablo Valencia +52 55 4386 6137 "
            "Material/Proyecto: 3 Geomembranas de 20 mil litros"
        },
    )
    sent = "Cotización Enviada 320926CDMX Cliente: Cimentaciones NECS Material/Proyecto: Bolsa"
    r = client.post(
        "/quotes/capture", json={"text": sent, "customer_phone": "55 4386 6137"}
    )
    data = r.json()["data"]
    assert data["preview"]["action"] == "converted"
    assert data["quote"]["customer_name"] == "Cimentaciones NECS"
    assert "(Pablo Valencia)" in data["quote"]["notes"]
