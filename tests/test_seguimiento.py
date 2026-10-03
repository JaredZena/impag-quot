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
    # 12 quotes + 5 season; the 3 empty "inactivo" slots go to quotes first.
    assert kinds == ["cotizacion"] * 15 + ["temporada"] * 5


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
