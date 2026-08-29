"""
Hermetic tests for the POS API (routes/pos.py + POST /customers + sales_sync
folio-dedup guard). SQLite file DB in a temp dir — no network, no real DB.

Run: venv/bin/python -m pytest tests/test_pos.py -q

Tests in this module are ORDERED and share state (folio sequence, stock,
cash session) — they mirror a full register shift. pytest runs them in
definition order; do not reorder or run individual tests in isolation.
"""

import os
import tempfile

# Must run BEFORE any project import: importing models.py creates the engine
# from DATABASE_URL and runs Base.metadata.create_all against it (config's
# load_dotenv does not override already-set environment variables, so these
# assignments win over the repo .env, which points at PRODUCTION).
_tmpdir = tempfile.mkdtemp(prefix="pos_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmpdir, 'pos_test.db')}"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

assert os.environ["DATABASE_URL"].startswith("sqlite"), "not sqlite — abort"

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from models import (
    CashMovement,
    Customer,
    PosSale,
    Product,
    ProductCategory,
    ProductUnit,
    Sale,
    SessionLocal,
    StockMovement,
)
from services.sales_sync import upsert_sales

from main import app  # noqa: E402

client = TestClient(app)
# Folios are minted on the BUSINESS date (Durango local), not the server clock
from routes.pos import _business_today  # noqa: E402

MMYY = _business_today().strftime("%m%y")

# Cross-test state (ids minted by earlier tests, used by later ones).
state: dict = {}


def _seed():
    db = SessionLocal()
    try:
        cat = ProductCategory(name="General", slug="general")
        db.add(cat)
        db.flush()
        prod = Product(
            name="Rollo malla sombra",
            sku="MALLA-01",
            base_sku="MALLA",
            category_id=cat.id,
            unit=ProductUnit.ROLLO,
            iva=True,
            price=Decimal("116.00"),
            stock=10,
            is_active=True,
        )
        db.add(prod)
        cust = Customer(
            display_name="Juan Pérez",
            name_normalized="juan pérez",
            phone_e164="+526180000001",
            source="whatsapp",
        )
        db.add(cust)
        db.commit()
        state["product_id"] = prod.id
        state["customer_id"] = cust.id
    finally:
        db.close()


_seed()


# ── product search ───────────────────────────────────────────────────────────


def test_products_search():
    r = client.get("/pos/products", params={"q": "malla"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == state["product_id"]
    assert item["price"] == 116.0
    assert item["currency"] == "MXN"
    assert item["iva"] is True
    assert item["stock"] == 10
    assert item["unit"] == "ROLLO"


# ── §6.1 sale create: IVA math, folio format, stock, projection ──────────────


def test_sale_create_math_folio_stock_projection():
    r = client.post(
        "/pos/sales",
        json={
            "payment_method": "efectivo",
            "customer_id": state["customer_id"],
            "amount_tendered": 300,
            "items": [
                {
                    "product_id": state["product_id"],
                    "description": "Rollo malla sombra",
                    "unit": "ROLLO",
                    "quantity": 2,
                    "unit_price": 116.0,
                    "iva": True,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    s = r.json()
    state["sale1"] = s

    # Folio format 01MMYYDGO
    assert s["folio"] == f"01{MMYY}DGO"
    # 116.00/unit IVA-included → per unit base 100.00, iva 16.00; qty 2
    assert s["total"] == 232.0
    assert s["subtotal"] == 200.0
    assert s["iva_amount"] == 32.0
    assert s["change_given"] == 68.0
    # Customer snapshot from the linked customer
    assert s["customer_name"] == "Juan Pérez"
    assert s["customer_phone"] == "+526180000001"
    # No open caja yet
    assert s["cash_session_id"] is None
    assert s["status"] == "completada"
    assert len(s["items"]) == 1
    assert s["items"][0]["line_total"] == 232.0

    db = SessionLocal()
    try:
        # Stock 10 → 8
        assert db.query(Product).get(state["product_id"]).stock == 8
        # Projection row into the sale ledger
        proj = db.query(Sale).filter(Sale.sheet_tab == "POS").all()
        assert len(proj) == 1
        p = proj[0]
        assert float(p.amount) == 232.0
        assert p.source_row == s["items"][0]["id"]
        assert p.folio == s["folio"]
        assert p.reference == "POS"
        assert p.delivery_status == "entregado"
        assert p.registered is None
        assert p.quarantined is False
        # Stock movement recorded
        sm = db.query(StockMovement).all()
        assert len(sm) == 1
        assert sm[0].delta == -2
        assert sm[0].reason == "venta"
        assert sm[0].product_id == state["product_id"]
    finally:
        db.close()


def test_sale_rejects_bad_payment_method():
    r = client.post(
        "/pos/sales",
        json={
            "payment_method": "tarjeta",
            "items": [{"description": "x", "quantity": 1, "unit_price": 1.0}],
        },
    )
    assert r.status_code == 422, r.text


# ── §6.2 folio seeding from hand-assigned ledger folios ──────────────────────


def test_folio_seeded_from_ledger():
    db = SessionLocal()
    try:
        db.add(Sale(sheet_tab="VENTAS_2026", source_row=999, folio=f"07{MMYY}DGO"))
        db.commit()
    finally:
        db.close()
    r = client.post(
        "/pos/sales",
        json={
            "payment_method": "transferencia",
            "items": [
                {"description": "Artículo libre", "quantity": 1, "unit_price": 116.0}
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["folio"] == f"08{MMYY}DGO"


# ── §6.3 efectivo + open session: cash movement + change math ────────────────


def test_open_session_and_efectivo_sale():
    r = client.post("/pos/cash-sessions/open", json={"opening_float": 500})
    assert r.status_code == 200, r.text
    sess_id = r.json()["session"]["id"]
    state["session_id"] = sess_id

    # Second open for same branch → 409
    r = client.post("/pos/cash-sessions/open", json={"opening_float": 1})
    assert r.status_code == 409, r.text

    r = client.post(
        "/pos/sales",
        json={
            "payment_method": "efectivo",
            "amount_tendered": 200,
            "items": [
                {
                    "product_id": state["product_id"],
                    "description": "Rollo malla sombra",
                    "quantity": 1,
                    "unit_price": 116.0,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    s = r.json()
    state["sale_efectivo"] = s
    # tendered 200 − total 116 → change 84.00
    assert s["change_given"] == 84.0
    assert s["cash_session_id"] == sess_id

    db = SessionLocal()
    try:
        mv = db.query(CashMovement).filter(CashMovement.pos_sale_id == s["id"]).all()
        assert len(mv) == 1
        assert mv[0].kind == "venta"
        assert float(mv[0].amount) == 116.0
        assert mv[0].cash_session_id == sess_id
    finally:
        db.close()


def test_manual_movements_and_current_totals():
    sess_id = state["session_id"]
    r = client.post(
        f"/pos/cash-sessions/{sess_id}/movements",
        json={"kind": "entrada", "amount": 50, "description": "Cambio inicial"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/pos/cash-sessions/{sess_id}/movements",
        json={"kind": "salida", "amount": 20, "description": "Compra insumos"},
    )
    assert r.status_code == 200, r.text

    r = client.get("/pos/cash-sessions/current")
    assert r.status_code == 200, r.text
    cur = r.json()
    assert cur["session"]["id"] == sess_id
    totals = cur["totals"]
    assert totals["ventas_efectivo"] == 116.0
    assert totals["entradas"] == 50.0
    assert totals["salidas"] == 20.0
    # 500 + 116 + 50 − 20 − 0
    assert totals["expected_cash"] == 646.0


# ── §6.4 cancel: stock restored, projection deleted, movement, 409 ───────────


def test_cancel_sale():
    s = state["sale_efectivo"]
    r = client.post(
        f"/pos/sales/{s['id']}/cancel", json={"reason": "cliente se arrepintió"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelada"

    db = SessionLocal()
    try:
        # Stock 7 → 8 (restored)
        assert db.query(Product).get(state["product_id"]).stock == 8
        # Projection rows deleted from the ledger
        assert (
            db.query(Sale)
            .filter(Sale.sheet_tab == "POS", Sale.folio == s["folio"])
            .count()
            == 0
        )
        canc_mv = (
            db.query(CashMovement)
            .filter(
                CashMovement.pos_sale_id == s["id"],
                CashMovement.kind == "cancelacion",
            )
            .all()
        )
        assert len(canc_mv) == 1
        assert float(canc_mv[0].amount) == 116.0
        canc_sm = (
            db.query(StockMovement)
            .filter(
                StockMovement.pos_sale_id == s["id"],
                StockMovement.reason == "cancelacion",
            )
            .all()
        )
        assert len(canc_sm) == 1
        assert canc_sm[0].delta == 1
    finally:
        db.close()

    # Second cancel → 409
    r = client.post(f"/pos/sales/{s['id']}/cancel", json={"reason": "otra vez"})
    assert r.status_code == 409, r.text


# ── §6.5 caja close: expected/difference math ────────────────────────────────


def test_close_session_math():
    sess_id = state["session_id"]
    r = client.post(f"/pos/cash-sessions/{sess_id}/close", json={"counted_cash": 500})
    assert r.status_code == 200, r.text
    closed = r.json()["session"]
    # expected = 500 fondo + 116 ventas + 50 entradas − 20 salidas − 116 cancelaciones = 530
    assert closed["expected_cash"] == 530.0
    # difference = counted − expected = 500 − 530 = −30
    assert closed["difference"] == -30.0
    assert closed["status"] == "cerrada"

    # Second close → 409
    r = client.post(f"/pos/cash-sessions/{sess_id}/close", json={"counted_cash": 1})
    assert r.status_code == 409, r.text
    # Movement on closed session → 409
    r = client.post(
        f"/pos/cash-sessions/{sess_id}/movements",
        json={"kind": "entrada", "amount": 5, "description": "x"},
    )
    assert r.status_code == 409, r.text


def test_listing_endpoints():
    r = client.get("/pos/sales")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["items"][0]["item_count"] == 1

    r = client.get(f"/pos/sales/{state['sale1']['id']}")
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1

    r = client.get("/pos/cash-sessions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert "totals" in body["items"][0]


# ── §6.7 POST /customers: normalized phone + duplicate 409 ───────────────────


def test_create_customer_and_duplicate_phone():
    r = client.post(
        "/customers",
        json={
            "display_name": "María López",
            "phone": "618 123 4567",
            "location": "Durango",
        },
    )
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["phone_e164"] == "+526181234567"
    state["new_customer_id"] = c["id"]

    # Same number written with the WhatsApp 521 prefix → 409 + existing id
    r = client.post(
        "/customers", json={"display_name": "Otra", "phone": "5216181234567"}
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["customer_id"] == state["new_customer_id"]

    db = SessionLocal()
    try:
        created = db.query(Customer).get(state["new_customer_id"])
        assert created.source == "manual"
        assert created.name_normalized == "maría lópez"
        assert created.first_seen_at is not None
    finally:
        db.close()


# ── §6.6 sales_sync folio-dedup guard ────────────────────────────────────────


def test_sync_guard_quarantines_active_pos_folio():
    db = SessionLocal()
    try:
        active_folio = (
            db.query(PosSale).filter(PosSale.status == "completada").first().folio
        )
        parsed = [
            {
                "sheet_tab": "VENTAS_2026",
                "source_row": 50,
                "sale_date": date.today(),
                "customer_name": "Juan Pérez",
                "description": "Rollo malla sombra",
                "amount": Decimal("232.00"),
                "folio": active_folio,
                "quarantined": False,
                "quarantine_reason": None,
            },
            {
                "sheet_tab": "VENTAS_2026",
                "source_row": 51,
                "sale_date": date.today(),
                "customer_name": "Otro Cliente",
                "description": "Otra cosa",
                "amount": Decimal("100.00"),
                "folio": "990101XXX",
                "quarantined": False,
                "quarantine_reason": None,
            },
        ]
        counts = upsert_sales(db, parsed, {})
        db.commit()
        row = (
            db.query(Sale)
            .filter(Sale.sheet_tab == "VENTAS_2026", Sale.source_row == 50)
            .one()
        )
        clean = (
            db.query(Sale)
            .filter(Sale.sheet_tab == "VENTAS_2026", Sale.source_row == 51)
            .one()
        )
        assert row.quarantined is True
        assert row.quarantine_reason == "duplicado: capturado en POS"
        assert clean.quarantined is False
        assert counts["quarantined"] == 1
    finally:
        db.close()


def test_sync_guard_exempts_cancelled_pos_folio():
    cancelled_folio = state["sale_efectivo"]["folio"]
    db = SessionLocal()
    try:
        parsed = [
            {
                "sheet_tab": "VENTAS_2026",
                "source_row": 52,
                "sale_date": date.today(),
                "customer_name": "X",
                "description": "Y",
                "amount": Decimal("10.00"),
                "folio": cancelled_folio,
                "quarantined": False,
                "quarantine_reason": None,
            }
        ]
        upsert_sales(db, parsed, {})
        db.commit()
        row = (
            db.query(Sale)
            .filter(Sale.sheet_tab == "VENTAS_2026", Sale.source_row == 52)
            .one()
        )
        assert row.quarantined is False
    finally:
        db.close()
