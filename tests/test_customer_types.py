"""
Hermetic tests for «Clientes por tipo» (services/customer_types.py,
GET /sales/customer-types). SQLite in-memory.

Run: venv/bin/python -m pytest tests/test_customer_types.py -q
"""

import os

os.environ["DATABASE_URL"] = (
    "postgresql://test:test@customer-types-tests.invalid/testdb"
)
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

from datetime import date
from decimal import Decimal
from itertools import count

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routes.sales as sales_routes
from models import Base, Customer, Quote, QuoteItem, Sale, get_db
from services.customer_types import classify_text, customer_types

TABLES = [Customer.__table__, Quote.__table__, QuoteItem.__table__, Sale.__table__]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
ROWS = count(1)


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    s = Session()
    yield s
    s.close()


def _sale(db, name, description=None, amount=1000, day=date(2026, 6, 1), **kw):
    s = Sale(
        sheet_tab=kw.pop("sheet_tab", "VENTAS_2026"),
        source_row=next(ROWS),
        sale_date=day,
        customer_name=name,
        description=description,
        amount=Decimal(amount),
        quarantined=kw.pop("quarantined", False),
        **kw,
    )
    db.add(s)
    db.commit()
    return s


def _by_slug(result):
    return {t["slug"]: t for t in result["types"]}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Mallasombra 50% monofilamento 3.6x100m", "protegida"),
        ("Plastico Agrotermic 6.2 x 100", "protegida"),
        ("Bolsa vivero 17x17 cal 400", "vivero"),
        ("Charola 200 cavidades", "vivero"),
        ("100 MICRO ASPERSOR 75 LPH", "riego"),
        ("Cintilla 8 mil 20 cm", "riego"),
        ("Kit de Bombeo solar kol4 60-150-20", "bombeo"),
        # ganado wins over the pump it is for
        ("Bombeo Solar kolos95 para 70 vacas", "ganaderia"),
        ("POSTE PARA CERCO GANADERO 6 FT", "ganaderia"),
        # a solar lamp is not a pump nor a producer
        ("Lampara Solar 1000 w", "hogar_solar"),
        ("Geomembrana 10 mil instalada", "agua"),
        ("Semilla de cebolla", "vivero"),  # "olla" only as a word
        ("Ponchadora 3-8 mm", "otro"),
        ("", None),
        (None, None),
    ],
)
def test_classify_text(text, expected):
    assert classify_text(text) == expected


def test_buyer_type_is_where_they_spent_most(db):
    _sale(db, "Berenice Ruiz", "Mallasombra 50% monofilamento", 8000)
    _sale(db, "BERENICE RUIZ", "Cintilla 8 mil", 500)  # same person, other caps
    _sale(db, "Oscar Gunther", "Bolsa 17x17", 300)

    result = customer_types(db)
    types = _by_slug(result)
    assert result["total_customers"] == 2
    assert types["protegida"]["customers"] == 1
    berenice = types["protegida"]["items"][0]
    assert berenice["amount"] == 8500
    assert berenice["sales"] == 2
    assert berenice["types"] == ["riego"]  # also bought, not their main type
    assert types["vivero"]["customers"] == 1


def test_blank_row_borrows_quote_text(db):
    q = Quote(
        quote_number="190626DGO",
        status="accepted",
        customer_name="Fransisco Barraza Gomez",
        customer_phone="S/N",
        created_by="dev@local.test",
        notes="Material/Proyecto: Bombeo Solar kolos 180\n[Registro] x",
    )
    db.add(q)
    db.commit()
    # linked by quote_id
    _sale(db, "Alguien Distinto", None, 50000, quote_id=q.id)
    # matched by two name words
    _sale(db, "Fransisco Barraza", None, 20000)
    # a lone first name is never matched to a quote
    _sale(db, "Fransisco", None, 100)

    types = _by_slug(customer_types(db))
    assert types["bombeo"]["customers"] == 2
    assert [i["name"] for i in types["sin_dato"]["items"]] == ["Fransisco"]


def test_quote_items_count_as_product_text(db):
    q = Quote(
        quote_number="010126DGO",
        status="sent",
        customer_name="Juan Soria Perez",
        customer_phone="S/N",
        created_by="dev@local.test",
    )
    db.add(q)
    db.commit()
    db.add(
        QuoteItem(
            quote_id=q.id, description="Bebedero pileta 200 L", quantity=1, unit_price=0
        )
    )
    db.commit()
    _sale(db, "Juan Soria Perez", None, 3000)
    assert _by_slug(customer_types(db))["ganaderia"]["customers"] == 1


def test_verified_buyer_overrides_rules(db):
    _sale(db, "WORLD VISION", None, 290000)
    assert _by_slug(customer_types(db))["bombeo"]["items"][0]["name"] == "WORLD VISION"


def test_year_filter_and_quarantine(db):
    _sale(
        db,
        "Ana Lopez",
        "Bolsa vivero",
        100,
        day=date(2025, 3, 1),
        sheet_tab="VENTAS_2025",
    )
    _sale(db, "Luis Mora", "Cintilla", 200)
    _sale(db, "Luis Mora", "Cintilla", 999, quarantined=True)

    assert customer_types(db)["total_customers"] == 2
    only_2026 = customer_types(db, year=2026)
    assert only_2026["year"] == 2026
    assert only_2026["total_customers"] == 1
    assert only_2026["total_amount"] == 200


def test_sembrando_vida_by_tag_name_and_cac(db):
    tagged = Customer(display_name="Maria Garcia Lopez", tags=["sembrando-vida"])
    db.add(tagged)
    db.commit()
    _sale(db, "Maria Garcia Lopez", "Malla sombra")  # name of a tagged contact
    _sale(db, "Geovany Resendiz (CAC Basoco)", "Malla 70%")  # CAC in the name
    _sale(db, "Pedro Diaz", "Bolsa 17x17", customer_id=tagged.id)  # linked row
    _sale(db, "Rosa Nava", "Bolsa 17x17")

    result = customer_types(db)
    assert result["sembrando_vida"] == 3
    types = _by_slug(result)
    assert types["protegida"]["sembrando_vida"] == 2
    assert types["vivero"]["sembrando_vida"] == 1


def test_route_returns_types(db):
    _sale(db, "Toño Meraz", "Alambre Zig Zag cal 13")
    app = FastAPI()
    app.include_router(sales_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    body = client.get("/sales/customer-types").json()
    assert body["total_customers"] == 1
    assert body["types"][0]["slug"] == "protegida"
    assert body["types"][0]["label"] == "Invernadero / casa sombra"
    assert client.get("/sales/customer-types?year=1999").status_code == 422
