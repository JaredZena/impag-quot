"""
Hermetic tests for gastos fijos / punto de equilibrio (routes/finance.py).
SQLite in-memory with only the tables the routes touch — no network, no real DB.

Run: venv/bin/python -m pytest tests/test_finance.py -q
"""

import os

# Must run BEFORE any project import (same guard as tests/test_tools.py).
os.environ["DATABASE_URL"] = "postgresql://test:test@finance-tests.invalid/testdb"
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

import routes.finance as finance_routes
from models import (
    Base,
    ExpenseConcept,
    MonthlyExpense,
    Quote,
    Sale,
    SaleBalance,
    get_db,
)

TABLES = [
    ExpenseConcept.__table__,
    MonthlyExpense.__table__,
    Sale.__table__,
    SaleBalance.__table__,
    Quote.__table__,
]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
TODAY = date(2026, 9, 26)


@pytest.fixture()
def client(monkeypatch):
    # quote/sale carry FKs to customer etc.; sqlite doesn't enforce them.
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    monkeypatch.setattr(finance_routes, "_today", lambda: TODAY)

    app = FastAPI()
    app.include_router(finance_routes.router)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture()
def db():
    session = TestingSession()
    yield session
    session.close()


def _concept(client, name, amount, category="operativo", **kw):
    r = client.post(
        "/finance/concepts",
        json={"name": name, "default_amount": amount, "category": category, **kw},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _sale(db, d, amount, row, quarantined=False):
    db.add(
        Sale(
            sheet_tab="VENTAS 2026",
            source_row=row,
            sale_date=d,
            amount=Decimal(str(amount)),
            quarantined=quarantined,
        )
    )


def test_concept_crud_and_validation(client):
    c = _concept(client, "Renta", 3000)
    assert c["category"] == "operativo" and c["default_amount"] == 3000
    assert client.post("/finance/concepts", json={"name": "renta"}).status_code == 409
    assert (
        client.post(
            "/finance/concepts", json={"name": "X", "category": "nope"}
        ).status_code
        == 422
    )
    r = client.put(f"/finance/concepts/{c['id']}", json={"default_amount": 3500})
    assert r.json()["default_amount"] == 3500
    assert client.delete(f"/finance/concepts/{c['id']}").status_code == 200
    assert client.get("/finance/concepts").json() == []


def test_open_month_copies_previous_amount_and_is_idempotent(client):
    renta = _concept(client, "Renta", 3000)
    _concept(client, "Luz", 175)
    _concept(client, "Viejo", 99, active=False)

    r = client.post("/finance/months/2026-08/open")
    assert r.json()["created"] == 2
    aug = {i["name"]: i for i in r.json()["items"]}
    client.put(f"/finance/expenses/{aug['Renta']['id']}", json={"amount": 3200})

    r = client.post("/finance/months/2026-09/open")
    sep = {i["name"]: i["amount"] for i in r.json()["items"]}
    assert sep == {"Renta": 3200, "Luz": 175}  # last month's amount wins
    assert client.post("/finance/months/2026-09/open").json()["created"] == 0

    dup = client.post(
        "/finance/months/2026-09/expenses",
        json={"concept_id": renta["id"], "amount": 1},
    )
    assert dup.status_code == 409


def test_paid_toggle_sets_and_clears_date(client):
    r = client.post(
        "/finance/months/2026-09/expenses", json={"name": "Gasolina", "amount": 800}
    )
    line = r.json()
    assert line["category"] == "otro" and line["paid"] is False
    r = client.put(f"/finance/expenses/{line['id']}", json={"paid": True})
    assert r.json()["paid_on"] == TODAY.isoformat()
    r = client.put(f"/finance/expenses/{line['id']}", json={"paid": False})
    assert r.json()["paid_on"] is None


def test_dashboard_breakeven_scenarios_and_pipeline(client, db):
    _concept(client, "Hernán", 10000)
    _concept(client, "Adriana", 1200)
    _concept(client, "Camioneta", 14000, category="financiamiento")

    # Measured margin: 2 reconciled tabs → 25k profit / 100k sales = 25%;
    # a mismatch tab must be ignored.
    db.add_all(
        [
            SaleBalance(
                tab_title="A",
                match_status="reconciled",
                sheet_profit=Decimal(20000),
                sheet_sale_total=Decimal(80000),
            ),
            SaleBalance(
                tab_title="B",
                match_status="reconciled",
                sheet_profit=Decimal(5000),
                sheet_sale_total=Decimal(20000),
            ),
            SaleBalance(
                tab_title="C",
                match_status="mismatch",
                sheet_profit=Decimal(90000),
                sheet_sale_total=Decimal(100000),
            ),
        ]
    )
    _sale(db, date(2026, 7, 10), 200000, 1)
    _sale(db, date(2026, 8, 10), 100000, 2)
    _sale(db, date(2026, 9, 5), 52000, 3)
    _sale(db, date(2026, 9, 6), 999999, 4, quarantined=True)
    db.add_all(
        [
            Quote(
                quote_number="Q1",
                status="sent",
                customer_name="a",
                customer_phone="1",
                created_by="t",
                total=Decimal(60000),
            ),
            Quote(
                quote_number="Q2",
                status="viewed",
                customer_name="b",
                customer_phone="2",
                created_by="t",
                total=Decimal(40000),
            ),
            Quote(
                quote_number="Q3",
                status="expired",
                customer_name="c",
                customer_phone="3",
                created_by="t",
                total=Decimal(500000),
            ),
        ]
    )
    db.commit()

    # Adriana unpaid in August → arrears.
    client.post("/finance/months/2026-08/open")
    aug = client.get("/finance/months/2026-08").json()["items"]
    for line in aug:
        if line["name"] != "Adriana":
            client.put(f"/finance/expenses/{line['id']}", json={"paid": True})
    client.post("/finance/months/2026-09/open")

    d = client.get("/finance/dashboard?months=3").json()
    assert d["margin"] == {
        "pct": 0.25,
        "source": "medido",
        "measured_pct": 0.25,
        "sample": 2,
    }
    sel = d["selected"]
    assert sel["month"] == "2026-09" and sel["sales"] == 52000
    assert sel["operativo"] == 11200 and sel["fixed_total"] == 25200
    assert sel["breakeven_operativo"] == 44800 and sel["breakeven_fixed"] == 100800
    assert sel["projection"] == 60000  # 52k in 26 of 30 days

    sc = {s["key"]: s for s in d["scenarios"]}
    assert sc["operativo"]["gap"] == 0
    assert sc["fijo"]["gap"] == 48800
    assert d["arrears"]["total"] == 1200
    assert sc["al_corriente"]["breakeven"] == 105600

    assert d["pipeline"]["open_count"] == 2 and d["pipeline"]["open_total"] == 100000
    assert d["pipeline"]["gross_profit_if_all_close"] == 25000
    assert d["pipeline"]["share_needed_to_cover_gap"] == 0.488

    ref = d["reference"]
    assert ref["completed_months"] == 2 and ref["avg_sales"] == 150000
    assert ref["months_below_breakeven"] == 1  # August 100k < 100.8k

    jul = d["series"][0]
    assert jul["month"] == "2026-07" and jul["expenses_source"] == "plantilla"
    assert d["series"][1]["unpaid"] == 1200


def test_dashboard_margin_override_and_fallback(client):
    _concept(client, "Renta", 3000)
    d = client.get("/finance/dashboard").json()
    assert d["margin"]["source"] == "supuesto" and d["margin"]["pct"] == 0.225
    d = client.get("/finance/dashboard?margin_pct=30").json()
    assert d["margin"]["source"] == "manual"
    assert d["selected"]["breakeven_operativo"] == 10000
    assert client.get("/finance/dashboard?month=2026-13").status_code == 422
