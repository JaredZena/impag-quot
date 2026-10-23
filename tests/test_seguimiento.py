"""
Hermetic tests for "Seguimiento del día" (services/seguimiento.py,
GET/POST /hoy/seguimiento) and the follow-up sweep switch. SQLite in-memory.

Run: venv/bin/python -m pytest tests/test_seguimiento.py -q
"""

import os

os.environ["DATABASE_URL"] = "postgresql://test:test@seguimiento-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from itertools import count

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routes.hoy as hoy_routes
from models import (
    Base,
    Customer,
    FollowupContact,
    Quote,
    QuoteItem,
    Sale,
    Task,
    TaskCategory,
    TaskComment,
    TaskUser,
    get_db,
)
from services import seguimiento
from services.quote_capture import BUSINESS_TZ
from services.quote_followup import sweep_stale_quotes

TABLES = [
    TaskUser.__table__,
    TaskCategory.__table__,
    Task.__table__,
    TaskComment.__table__,
    Customer.__table__,
    Quote.__table__,
    QuoteItem.__table__,
    Sale.__table__,
    FollowupContact.__table__,
]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
NOW = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)  # 12:00 in Durango
ROWS = count(1)


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    s = Session()
    s.add(TaskUser(email="hernan@x.com", display_name="Hernán Cordero"))
    s.commit()
    yield s
    s.close()


def _quote(
    db, number, name, phone="S/N", days=10, material="Malla sombra", now=NOW, **kw
):
    q = Quote(
        quote_number=number,
        status=kw.pop("status", "sent"),
        customer_name=name,
        customer_phone=phone,
        sent_at=now - timedelta(days=days),
        notes=f"Material/Proyecto: {material}",
        created_by="carga-whatsapp-2026-10",
        **kw,
    )
    db.add(q)
    db.commit()
    return q


def _sale(db, name, day, amount, description="Plastico 6.2 cal 720 25% sombra"):
    db.add(
        Sale(
            sheet_tab="VENTAS 2025",
            source_row=next(ROWS),
            sale_date=day,
            customer_name=name,
            amount=Decimal(amount),
            description=description,
            quarantined=False,
        )
    )
    db.commit()


def _todo(db, **kw):
    return seguimiento.daily_list(db, sender_email="hernan@x.com", now=NOW, **kw)[
        "todo"
    ]


def test_helpers():
    assert seguimiento.wa_number("+52 677 105 9056") == "526771059056"
    assert seguimiento.wa_number("5216771059056") == "526771059056"
    assert seguimiento.wa_number("6771059056") == "526771059056"
    assert seguimiento.wa_number("S/N") is None
    assert seguimiento.contact_key("Aron Contreras [Cecy]") == "aron contreras"
    assert seguimiento.item_text(
        "1 saco Multicote Agricola 8 [Fertilizante], 20 pzas"
    ) == ("multicote Agricola 8")
    assert seguimiento.item_text("CINTILLA 16 mm Longitud: 3,962 metros, Cal 5000") == (
        "cintilla 16 mm Longitud: 3,962 metros"
    )
    assert seguimiento._hello("COMUNIDAD SAN BERNARDINO") == "Hola, buen día."
    assert seguimiento._hello("VANESSA AGUILAR") == "Hola Vanessa, buen día."


def test_nudged_quote_waits_a_week(db):
    # Hernán 10/10: quotes he messaged on Monday came back on Saturday. A
    # nudged quote stays off the list for 7 days, not 4.
    _quote(
        db,
        "COT-IMPAG-110926DGO",
        "Adriana",
        days=20,
        followup_count=1,
        last_followup_at=NOW - timedelta(days=5),
    )
    _quote(
        db,
        "COT-IMPAG-120926DGO",
        "Amelia Cano",
        days=20,
        followup_count=1,
        last_followup_at=NOW - timedelta(days=8),
    )
    assert [c["customer_name"] for c in _todo(db)] == ["Amelia Cano"]


def test_open_quotes_one_card_per_person(db):
    # A bulk load stamped last_followup_at without nudging: still due.
    _quote(
        db,
        "COT-IMPAG-270926DGO",
        "Morales",
        "+526771059056",
        days=11,
        material="Kit de riego",
        last_followup_at=NOW - timedelta(hours=10),
    )
    _quote(
        db,
        "COT-IMPAG-260926DGO",
        "Morales",
        "+526771059056",
        days=11,
        material="Xcel Wobbler",
    )
    _quote(
        db,
        "COT-IMPAG-250926DGO",
        "Morales",
        "+526741078982",
        days=12,
        material="Molino",
    )
    _quote(db, "COT-IMPAG-021026DGO", "Edgar", days=1)  # too fresh
    _quote(db, "COT-IMPAG-010826DGO", "Viejo", days=60)  # dead lead
    _quote(db, "COT-IMPAG-200926DGO", "Lupe", days=13, status="accepted")

    todo = _todo(db)
    assert [c["customer_name"] for c in todo] == ["Morales", "Morales"]
    first = todo[0]
    assert len(first["quote_ids"]) == 2 and first["wa"] == "526771059056"
    assert first["message"].startswith(
        "Hola Morales, buen día. Le saluda Hernán de IMPAG."
    )
    assert "Kit de riego y Xcel Wobbler" in first["message"]
    assert "¿Pudo revisar" in first["message"]


def test_expired_quote_offers_new_prices(db):
    _quote(db, "COT-IMPAG-050926DGO", "Dariel Rubio", days=28, material="Bombeo Solar")
    (card,) = _todo(db)
    assert "ya pasó su vigencia" in card["message"]
    assert card["wa"] is None  # no number: the admin asks for it


def test_season_and_inactive_lists(db):
    _sale(db, "Vanessa Aguilar Bautista", date(2025, 11, 3), "10900")  # season
    _sale(db, "Sarahi Soto", date(2026, 9, 1), "8000")  # bought recently
    _sale(db, "Sarahi Soto", date(2025, 10, 20), "9000")
    _sale(db, "MINA INDE DURANGO", date(2025, 7, 24), "161749", "Geomembrana 1mm")
    _sale(db, "Chico", date(2025, 6, 1), "900")  # too small to chase
    _sale(db, "Publico en general", date(2025, 10, 10), "5000")
    _quote(db, "COT-IMPAG-150926DGO", "Vanessa Aguilar Bautista", days=19)

    todo = _todo(db)
    kinds = {c["customer_name"]: c["kind"] for c in todo}
    # Vanessa has an open quote: one card, as a quote.
    assert kinds == {
        "Vanessa Aguilar Bautista": "cotizacion",
        "MINA INDE DURANGO": "inactivo",
    }
    db.query(Quote).delete()
    db.commit()
    season = next(c for c in _todo(db) if c["kind"] == "temporada")
    assert season["message"].endswith(
        "En noviembre del año pasado nos compró plastico 6.2 cal 720 25% sombra. "
        "¿Lo va a necesitar esta temporada? Ya tenemos disponible y con gusto le cotizamos."
    )
    inactive = next(c for c in _todo(db) if c["kind"] == "inactivo")
    assert inactive["message"].startswith("Hola, buen día.")  # an organization


def test_quotas_and_backfill(db):
    for i in range(15):
        _quote(db, f"COT-IMPAG-{i:02d}0926DGO", f"Cliente{i} Uno", days=5 + i)
    for i in range(6):
        _sale(db, f"Temporada{i} Dos", date(2025, 10, 15), str(1000 + i))
    todo = _todo(db)
    kinds = [c["kind"] for c in todo]
    # Every due quote (15) + 5 season; the 3 empty "inactivo" slots take the
    # one season buyer left.
    assert kinds == ["cotizacion"] * 15 + ["temporada"] * 6


def test_every_due_quote_is_listed(db):
    # More quotes than a 20-person day: all of them show, plus the season quota.
    for i in range(30):
        _quote(db, f"COT-IMPAG-{i:02d}0926DGO", f"Cliente{i} Uno", days=5 + (i % 30))
    for i in range(6):
        _sale(db, f"Temporada{i} Dos", date(2025, 10, 15), str(1000 + i))
    data = seguimiento.daily_list(db, now=NOW)
    kinds = [c["kind"] for c in data["todo"]]
    assert kinds == ["cotizacion"] * 30 + ["temporada"] * 6
    assert data["target"] == 38  # 30 quotes + 5 season + 3 inactive slots


def test_send_then_outcomes(db):
    # Through the API, which runs on the real clock.
    q = _quote(
        db,
        "COT-IMPAG-090926DGO",
        "Vladimir González",
        days=10,
        now=datetime.now(timezone.utc),
    )
    client = _client()
    card = client.get("/hoy/seguimiento").json()["data"]["todo"][0]
    sent = client.post(
        "/hoy/seguimiento",
        json={
            "key": card["key"],
            "customer_name": card["customer_name"],
            "kind": card["kind"],
            "quote_ids": card["quote_ids"],
            "phone": "618 123 4567",
            "message": card["message"],
        },
    )
    assert sent.status_code == 200, sent.text
    contact = sent.json()["data"]
    db.expire_all()
    q = db.get(Quote, q.id)
    assert q.followup_count == 1 and q.customer_phone == "+526181234567"
    assert "[Seguimiento]" in q.notes and "WhatsApp enviado" in q.notes

    data = client.get("/hoy/seguimiento").json()["data"]
    assert data["todo"] == [] and data["done"][0]["outcome"] == "enviado"
    hoy = client.get("/hoy", params={"day": data["day"]}).json()["data"]
    assert hoy["followups"][0]["detail"] == "WhatsApp enviado"

    bad = client.post(
        f"/hoy/seguimiento/{contact['id']}/outcome", json={"outcome": "x"}
    )
    assert bad.status_code == 400
    client.post(
        f"/hoy/seguimiento/{contact['id']}/outcome", json={"outcome": "no_interesa"}
    )
    db.expire_all()
    assert db.get(Quote, q.id).status == "rejected"
    assert "Perdida — no le interesa" in db.get(Quote, q.id).notes
    hoy = client.get("/hoy", params={"day": data["day"]}).json()["data"]
    assert len(hoy["followups"]) == 1  # the [Estado] line, not twice


def test_not_interested_without_a_message(db):
    q = _quote(db, "COT-IMPAG-140926DGO", "Diego Saucedo", days=20)
    _quote(db, "COT-IMPAG-200826DGO", "Diego Saucedo", days=40)
    contact = seguimiento.log_contact(
        db,
        customer_name="Diego Saucedo",
        kind="cotizacion",
        quote_ids=[q.id],
        outcome="no_interesa",
        user_email="hernan@x.com",
        now=NOW,
    )
    assert contact.outcome == "no_interesa" and contact.message is None
    statuses = {x.quote_number: (x.status, x.followup_count) for x in db.query(Quote)}
    assert statuses == {
        "COT-IMPAG-140926DGO": ("rejected", 0),
        "COT-IMPAG-200826DGO": ("rejected", 0),
    }
    assert _todo(db) == []


def test_already_bought_closes_only_that_quote(db):
    q = _quote(db, "COT-IMPAG-020926DGO", "Linda Ramos Medina", days=30)
    other = _quote(db, "COT-IMPAG-150926DGO", "Linda Ramos Medina", days=20)
    contact = seguimiento.log_contact(
        db,
        customer_name="Linda Ramos Medina",
        kind="cotizacion",
        quote_ids=[q.id],
        outcome="venta",
        user_email="hernan@x.com",
        now=NOW,
    )
    assert contact.outcome == "venta" and contact.message is None
    db.expire_all()
    q, other = db.get(Quote, q.id), db.get(Quote, other.id)
    assert q.status == "accepted" and q.accepted_at is not None
    assert q.followup_count == 0
    assert "Aceptada — compró (seguimiento por WhatsApp)" in q.notes
    assert other.status == "sent"  # may still be alive


def test_misclicked_outcome_reopens_the_quote(db):
    q = _quote(db, "COT-IMPAG-090926DGO", "Ordoñez", days=10)
    contact = seguimiento.log_contact(
        db,
        customer_name="Ordoñez",
        kind="cotizacion",
        quote_ids=[q.id],
        user_email="hernan@x.com",
        now=NOW,
    )

    def mark(outcome, minutes):
        seguimiento.set_outcome(
            db,
            contact.id,
            outcome,
            user_email="hernan@x.com",
            now=NOW + timedelta(minutes=minutes),
        )
        db.expire_all()
        return db.get(Quote, q.id).status

    assert mark("venta", 1) == "accepted"
    assert mark("no_interesa", 2) == "rejected"  # Venta undone, then Perdida
    assert mark("respondio", 3) == "sent"
    assert mark("venta", 4) == "accepted"
    # Closed again by someone else after the misclick: leave it alone.
    db.get(Quote, q.id).updated_at = NOW + timedelta(minutes=5)
    db.commit()
    assert mark("respondio", 6) == "accepted"
    assert db.get(Quote, q.id).notes.count("se corrigió el seguimiento") == 2


def test_postponed_quote_waits_and_comes_back(db):
    q = _quote(
        db,
        "COT-IMPAG-150926DGO",
        "Omar Barraza",
        phone="6771234567",
        days=39,
        material="10 M Plastico invernadero 6.2",
        followup_count=1,
        last_followup_at=NOW - timedelta(days=8),
    )
    assert [c["customer_name"] for c in _todo(db)] == ["Omar Barraza"]

    seguimiento.snooze(
        db,
        quote_ids=[q.id],
        due=date(2026, 12, 1),
        reason=" espera la pastura ",
        user_email="hernan@x.com",
        now=NOW,
    )
    db.expire_all()
    q = db.get(Quote, q.id)
    assert q.next_action == f"{seguimiento.SNOOZE_ACTION}: espera la pastura"
    assert (q.next_action_owner, q.next_action_due) == ("Hernán", date(2026, 12, 1))
    assert "[Siguiente paso]" in q.notes
    assert _todo(db) == []

    # Past 45 days by then, but the customer asked for that day.
    nov30 = datetime(2026, 11, 30, 18, 0, tzinfo=timezone.utc)
    dec1 = datetime(2026, 12, 1, 18, 0, tzinfo=timezone.utc)
    assert seguimiento.daily_list(db, now=nov30)["todo"] == []
    [card] = seguimiento.daily_list(db, sender_email="hernan@x.com", now=dec1)["todo"]
    assert card["quote_ids"] == [q.id]
    assert "pospuesta al 01/12: espera la pastura" in card["reason"]
    assert (
        "Le escribo como quedamos sobre la cotización de 10 M Plastico"
        in card["message"]
    )

    # Sending it completes the postponed step, so it does not come back weekly.
    seguimiento.log_contact(
        db,
        customer_name=card["customer_name"],
        kind="cotizacion",
        key=card["key"],
        quote_ids=card["quote_ids"],
        message=card["message"],
        user_email="hernan@x.com",
        now=dec1,
    )
    db.expire_all()
    q = db.get(Quote, q.id)
    assert q.next_action is None and q.next_action_due is None
    assert q.notes.rstrip().endswith("hernan@x.com: hecho")
    later = dec1 + timedelta(days=8)
    assert seguimiento.daily_list(db, now=later)["todo"] == []


def test_scheduled_next_step_holds_the_card(db):
    q = _quote(db, "COT-IMPAG-200926DGO", "Rosa Ibarra", days=10)
    q.next_action, q.next_action_due = "Llamar para cerrar", date(2026, 10, 6)
    db.commit()
    assert _todo(db) == []  # NOW is 10-03
    q.next_action_due = date(2026, 10, 3)
    db.commit()
    assert [c["customer_name"] for c in _todo(db)] == ["Rosa Ibarra"]
    # Not a "Posponer": an old quote with a due step stays out.
    q.sent_at = NOW - timedelta(days=60)
    db.commit()
    assert _todo(db) == []


def test_snooze_rejects_bad_input(db):
    q = _quote(db, "COT-IMPAG-210926DGO", "Rosa Ibarra", days=10)
    lost = _quote(db, "COT-IMPAG-220926DGO", "Rosa Ibarra", days=10, status="rejected")

    def snooze(ids, due, reason="espera la pastura"):
        seguimiento.snooze(
            db,
            quote_ids=ids,
            due=due,
            reason=reason,
            user_email="hernan@x.com",
            now=NOW,
        )

    for ids, due, reason, error in [
        ([q.id], date(2026, 10, 3), "espera", "después de hoy"),  # today
        ([q.id], date(2027, 10, 5), "espera", "próximo año"),
        ([q.id], date(2026, 12, 1), None, "motivo"),
        ([q.id], date(2026, 12, 1), "   ", "motivo"),
        ([], date(2026, 12, 1), "espera", "abiertas"),
        ([lost.id], date(2026, 12, 1), "espera", "abiertas"),
    ]:
        with pytest.raises(seguimiento.SeguimientoError, match=error):
            snooze(ids, due, reason)
    db.expire_all()
    assert db.get(Quote, q.id).next_action is None


def test_posponer_through_the_api(db):
    q = _quote(
        db,
        "COT-IMPAG-230926DGO",
        "Omar Barraza",
        days=10,
        now=datetime.now(timezone.utc),
    )
    client = _client()
    today = datetime.now(BUSINESS_TZ).date()
    bad = client.post(
        "/hoy/seguimiento/posponer",
        json={"quote_ids": [q.id], "due": today.isoformat(), "reason": "espera"},
    )
    assert bad.status_code == 400 and "después de hoy" in bad.json()["detail"]
    due = today + timedelta(days=40)
    res = client.post(
        "/hoy/seguimiento/posponer",
        json={
            "quote_ids": [q.id],
            "due": due.isoformat(),
            "reason": "espera la pastura",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"] == {"quote_ids": [q.id], "due": due.isoformat()}
    assert client.get("/hoy/seguimiento").json()["data"]["todo"] == []


def test_messaged_people_wait(db):
    _sale(db, "Vanessa Aguilar Bautista", date(2025, 11, 3), "10900")
    db.add(
        FollowupContact(
            contact_key="vanessa aguilar bautista",
            customer_name="Vanessa Aguilar Bautista",
            kind="temporada",
            outcome="enviado",
            created_by="hernan@x.com",
            created_at=NOW - timedelta(days=12),
        )
    )
    db.commit()
    assert _todo(db) == []
    db.query(FollowupContact).update(
        {FollowupContact.created_at: NOW - timedelta(days=31)}
    )
    db.commit()
    assert [c["kind"] for c in _todo(db)] == ["temporada"]


def test_sweep_is_off_by_default(db, monkeypatch):
    _quote(db, "COT-IMPAG-090926DGO", "Vladimir", days=10)
    monkeypatch.delenv("FOLLOWUP_SWEEP_ENABLED", raising=False)
    summary = sweep_stale_quotes(db, dry_run=False, now=NOW)
    assert summary["candidates"] == 1 and "skipped" in summary
    assert db.query(Task).count() == 0


def _client():
    app = FastAPI()
    app.include_router(hoy_routes.router)

    def override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)
