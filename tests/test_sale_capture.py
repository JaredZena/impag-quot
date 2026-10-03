"""
Hermetic tests for registering sales from the WhatsApp *Venta NN_MM_YYYY*
message (services/sale_capture.py, POST /sales/capture) and the sheet-sync
cutover / duplicate guard (services/sales_sync.upsert_sales). SQLite in-memory.

Run: venv/bin/python -m pytest tests/test_sale_capture.py -q
"""

import os

os.environ["DATABASE_URL"] = "postgresql://test:test@sale-capture-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routes.sales as sales_routes
from models import Base, Customer, PosSale, Quote, QuoteItem, Sale, SaleBalance, get_db
from services.sale_capture import VentaError, parse_venta
from services.sales_sync import upsert_sales

TABLES = [
    Customer.__table__,
    Quote.__table__,
    QuoteItem.__table__,
    PosSale.__table__,
    Sale.__table__,
    SaleBalance.__table__,
]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

TRINIDAD = """Venta 16_09_2026

4 metros plástico 6.2 m cal 720 Blanco 25% Sombra $145/m

Total: $580.00 (30/09/2026) [Efectivo]

Trinidad Rivas

Nuevo Ideal, Durango"""

ECHEVERRIA = """[13:44, 1/10/2026] Impag Tech: Venta 01_10_2026
2 Reducciones Galv Rosc 3" a 2" $350.00/ Pieza.
Total: $700.00
Anticipo 1: $40 (01/10/2026) [Efectivo]
Pendientes: $660.00
Alejandro Echeverría
Nuevo Ideal, Durango"""

ECHEVERRIA_UPDATE = """Venta 01_10_2026 (Actualización)

2 Reducciones Galv Rosc 3" a 2" $350.00/ Pieza.

1 Kit de Accesorios para conectar motobomba con 11 metros de maguera anillada 2"

Total: $4,595.00

Anticipo 1: $40.00 (01/10/2026) [Efectivo]

Anticipo 2: $4,555.00 (02/10/2026) [Efectivo]

Pendientes: $00.00

Alejandro Echeverría

Nuevo Ideal, Durango"""

MONTANEZ = """Venta 02_10_2026

1 Paq Bolsa 17x17 Cal 400 Fuelle y Perf

Total: $3,550.00 [Transferencia] (01/10/2026)

Miguel Montañez

Ejido Revolución, Durango"""


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    app = FastAPI()
    app.include_router(sales_routes.router)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _sheet_row(row: int, folio: str, day: date, amount: str, name: str) -> dict:
    return {
        "sheet_tab": "VENTAS_2026",
        "source_row": row,
        "sale_date": day,
        "customer_name": name,
        "amount": Decimal(amount),
        "folio": folio,
        "quarantined": False,
    }


# ---------- parser ----------


def test_paid_in_full_on_the_total_line():
    v = parse_venta(TRINIDAD)
    assert v.label == "16_09_2026" and v.folio == "160926DGO"
    assert v.source_row == 202609016
    assert v.total == Decimal("580.00")
    assert [(p.amount, p.date, p.method) for p in v.payments] == [
        (Decimal("580.00"), date(2026, 9, 30), "efectivo")
    ]
    assert v.customer == "Trinidad Rivas" and v.location == "Nuevo Ideal, Durango"
    item = v.items[0]
    assert (item.quantity, item.unit, item.unit_price) == (
        Decimal("4"),
        "metros",
        Decimal("145"),
    )


def test_anticipo_and_pending():
    v = parse_venta(ECHEVERRIA)
    assert v.paid == Decimal("40") and v.pending == Decimal("660.00")
    assert v.sale_date == date(2026, 10, 1)
    assert v.customer == "Alejandro Echeverría"
    assert v.warnings == []


def test_update_with_two_items_and_inconsistent_amounts_warns():
    v = parse_venta(ECHEVERRIA_UPDATE)
    assert v.tag == "Actualización"
    assert len(v.items) == 2
    assert v.paid == Decimal("4595.00") and v.warnings == []
    bad = parse_venta(ECHEVERRIA_UPDATE.replace("4,555.00", "4,635.00"))
    assert any("≠ total" in w for w in bad.warnings)


def test_method_before_date_and_one_line_paste():
    v = parse_venta(MONTANEZ)
    assert v.payments[0].method == "transferencia"
    assert v.payments[0].date == date(2026, 10, 1)
    flat = parse_venta(" ".join(ECHEVERRIA.split("\n")[0:]).replace("\n", " "))
    assert flat.total == Decimal("700.00") and flat.pending == Decimal("660.00")


def test_rejects_unusable_text():
    with pytest.raises(VentaError):
        parse_venta("Nota de Venta 120826DGO Cliente: Enrique")
    with pytest.raises(VentaError):
        parse_venta("Venta 01_10_2026\n2 Reducciones")  # no Total


# ---------- capture ----------


def test_capture_creates_the_ledger_row_and_quarantines_the_sheet_copy(client):
    db = TestingSession()
    db.add(
        Sale(
            **_sheet_row(
                5, "011026DGO", date(2026, 10, 1), "700", "ALEJANDRO ECHEVERRIA"
            )
        )
    )
    db.commit()

    preview = client.post(
        "/sales/capture", json={"text": ECHEVERRIA, "dry_run": True}
    ).json()
    assert preview["data"]["preview"]["sheet_duplicates"] == 1
    assert TestingSession().query(Sale).count() == 1  # dry run wrote nothing

    r = client.post("/sales/capture", json={"text": ECHEVERRIA})
    assert r.status_code == 200, r.text
    sale = r.json()["data"]["sale"]
    assert sale["sheet_tab"] == "WHATSAPP" and sale["folio"] == "011026DGO"
    assert (
        sale["amount"] == 700
        and sale["paid_amount"] == 40
        and sale["pending_amount"] == 660
    )
    assert sale["reference"] == "Venta 01_10_2026"
    assert sale["payments"][0]["method"] == "efectivo"
    sheet = TestingSession().query(Sale).filter(Sale.sheet_tab == "VENTAS_2026").one()
    assert sheet.quarantined and "WhatsApp" in sheet.quarantine_reason

    # Actualización updates the same row.
    r = client.post("/sales/capture", json={"text": ECHEVERRIA_UPDATE})
    assert r.json()["data"]["preview"]["action"] == "updated"
    rows = TestingSession().query(Sale).filter(Sale.sheet_tab == "WHATSAPP").all()
    assert len(rows) == 1
    assert rows[0].amount == Decimal("4595.00") and rows[0].pending_amount == 0
    assert "[Actualización]" in rows[0].notes

    stats = client.get("/sales/stats").json()
    assert stats["receivable"] == {"count": 0, "total": 0.0}


def test_capture_closes_the_customers_quote(client):
    db = TestingSession()
    q = Quote(
        quote_number="COT-IMPAG-350926DGO",
        status="accepted",
        customer_name="Jesús Montañez",  # the Venta says "Miguel Montañez"
        customer_phone="S/N",
        total=Decimal("3550"),
        subtotal=Decimal("0"),
        iva_amount=Decimal("0"),
        sent_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        created_by="t",
    )
    db.add(q)
    db.commit()
    qid = q.id

    preview = client.post(
        "/sales/capture", json={"text": MONTANEZ, "dry_run": True}
    ).json()
    assert preview["data"]["preview"]["quote"]["quote_number"] == "COT-IMPAG-350926DGO"
    sale = client.post("/sales/capture", json={"text": MONTANEZ}).json()["data"]["sale"]
    assert sale["quote_id"] == qid
    quote = TestingSession().get(Quote, qid)
    assert quote.status == "accepted"
    assert "Venta 02_10_2026" in quote.notes


def test_capture_accepts_an_open_quote_and_fills_its_total(client):
    db = TestingSession()
    q = Quote(
        quote_number="COT-IMPAG-370926DGO",
        status="sent",
        customer_name="Alejandro Echeverria",
        customer_phone="S/N",
        total=Decimal("0"),
        subtotal=Decimal("0"),
        iva_amount=Decimal("0"),
        sent_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        created_by="t",
    )
    other = Quote(
        quote_number="COT-IMPAG-990926DGO",
        status="sent",
        customer_name="Pedro Echeverria",  # same surname, other amount: not it
        customer_phone="S/N",
        total=Decimal("1"),
        subtotal=Decimal("0"),
        iva_amount=Decimal("0"),
        sent_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        created_by="t",
    )
    db.add_all([q, other])
    db.commit()
    qid = q.id
    client.post("/sales/capture", json={"text": ECHEVERRIA})
    assert TestingSession().get(Quote, qid).total == Decimal("700.00")
    client.post("/sales/capture", json={"text": ECHEVERRIA_UPDATE})
    quote = TestingSession().get(Quote, qid)
    assert quote.status == "accepted"
    assert quote.total == Decimal("4595.00")  # follows the updated Venta
    assert TestingSession().get(Quote, other.id).status == "sent"


def test_pending_counts_as_receivable(client):
    client.post("/sales/capture", json={"text": ECHEVERRIA})
    stats = client.get("/sales/stats").json()
    assert stats["receivable"] == {"count": 1, "total": 660.0}


# ---------- sheet sync after the cutover ----------


def test_sync_quarantines_sheet_rows_after_the_cutover_and_whatsapp_duplicates(client):
    client.post("/sales/capture", json={"text": ECHEVERRIA})
    db = TestingSession()
    parsed = [
        _sheet_row(
            1, "160926DGO", date(2026, 9, 30), "580", "TRINIDAD"
        ),  # before: counts
        _sheet_row(
            2, "011026DGO", date(2026, 10, 1), "700", "ECHEVERRIA"
        ),  # WA duplicate
        _sheet_row(
            3, "031026DGO", date(2026, 10, 3), "900", "OTRO"
        ),  # after, not in app
    ]
    upsert_sales(db, parsed, {})
    db.commit()
    rows = {
        s.source_row: s for s in db.query(Sale).filter(Sale.sheet_tab == "VENTAS_2026")
    }
    assert not rows[1].quarantined
    assert (
        "duplicado: registrada desde WhatsApp (Venta 01_10_2026)"
        in rows[2].quarantine_reason
    )
    assert "falta registrar" in rows[3].quarantine_reason


def test_pre_cutover_venta_already_in_the_sheet_is_not_counted_twice(client):
    db = TestingSession()
    db.add(Sale(**_sheet_row(1, "160926DGO", date(2026, 9, 30), "580", "TRINIDAD")))
    db.commit()
    r = client.post("/sales/capture", json={"text": TRINIDAD}).json()["data"]
    assert r["sale"]["quarantined"] is True
    assert any("antes del corte" in w for w in r["warnings"])
    upsert_sales(
        db, [_sheet_row(1, "160926DGO", date(2026, 9, 30), "580", "TRINIDAD")], {}
    )
    db.commit()
    sheet = db.query(Sale).filter(Sale.sheet_tab == "VENTAS_2026").one()
    assert not sheet.quarantined  # the sheet keeps counting it
