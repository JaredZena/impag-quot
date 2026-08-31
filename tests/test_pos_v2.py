"""
Hermetic tests for POS v2 (cost snapshot + margin, vendedor + comisiones,
factura details). SQLite file DB in a temp dir — no network, no real DB
(the exchange-rate service is monkeypatched on USD tests).

Run: venv/bin/python -m pytest tests/test_pos_v2.py tests/test_pos.py -q

When both POS test files run in one pytest process they share ONE engine
(models.py builds it at first import), so this module cleans up everything
it created in teardown_module — tests/test_pos.py asserts absolute counts
(first folio 01MMYYDGO, sale totals, stock movements) and must still start
from a clean register.
"""

import os
import tempfile

# Must run BEFORE any project import: importing models.py creates the engine
# from DATABASE_URL and runs Base.metadata.create_all against it (config's
# load_dotenv does not override already-set environment variables, so these
# assignments win over the repo .env, which points at PRODUCTION).
_tmpdir = tempfile.mkdtemp(prefix="pos_v2_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmpdir, 'pos_v2_test.db')}"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

assert os.environ["DATABASE_URL"].startswith("sqlite"), "not sqlite — abort"

from decimal import Decimal

from fastapi.testclient import TestClient

from models import (
    CashMovement,
    CashSession,
    PosFolioCounter,
    PosSale,
    PosSaleItem,
    Product,
    ProductCategory,
    ProductUnit,
    Sale,
    SessionLocal,
    StockMovement,
    Supplier,
    SupplierProduct,
)

from main import app  # noqa: E402

client = TestClient(app)
from routes.pos import _business_today  # noqa: E402

TODAY = _business_today().isoformat()
DEV_EMAIL = "dev@local.test"  # what verify_google_token returns with DISABLE_AUTH

# Cross-test state (ids minted at seed time, used by the tests).
state: dict = {}


def _seed():
    db = SessionLocal()
    try:
        cat = ProductCategory(name="General v2", slug="general-v2")
        db.add(cat)
        db.flush()

        # Product A: MXN supplier cost 80 + DIRECT shipping 20 → unit_cost 100
        prod_a = Product(
            name="Acolchado plateado v2",
            sku="V2-ACOL-01",
            base_sku="V2-ACOL",
            category_id=cat.id,
            unit=ProductUnit.ROLLO,
            iva=True,
            price=Decimal("232.00"),
            stock=5,
            is_active=True,
        )
        # Product B: no supplier cost at all → cost unknown
        prod_b = Product(
            name="Grapa jardinera v2",
            sku="V2-GRAPA-01",
            base_sku="V2-GRAPA",
            category_id=cat.id,
            unit=ProductUnit.PIEZA,
            iva=True,
            price=Decimal("50.00"),
            stock=100,
            is_active=True,
        )
        # Product C: USD supplier cost 10 (no shipping) → needs the FX rate
        prod_c = Product(
            name="Valvula importada v2",
            sku="V2-VALV-01",
            base_sku="V2-VALV",
            category_id=cat.id,
            unit=ProductUnit.PIEZA,
            iva=True,
            price=Decimal("350.00"),
            stock=20,
            is_active=True,
        )
        db.add_all([prod_a, prod_b, prod_c])
        db.flush()

        acme = Supplier(name="ACME")
        globex = Supplier(name="GLOBEX")
        db.add_all([acme, globex])
        db.flush()

        sp_a_cheap = SupplierProduct(
            supplier_id=acme.id,
            product_id=prod_a.id,
            cost=Decimal("80.00"),
            currency="MXN",
            shipping_method="DIRECT",
            shipping_cost_direct=Decimal("20.00"),
            is_active=True,
        )
        # More expensive active alternative — must NOT be picked
        sp_a_expensive = SupplierProduct(
            supplier_id=globex.id,
            product_id=prod_a.id,
            cost=Decimal("150.00"),
            currency="MXN",
            shipping_method="DIRECT",
            shipping_cost_direct=Decimal("0.00"),
            is_active=True,
        )
        # Cheaper but INACTIVE — must NOT be picked
        sp_a_inactive = SupplierProduct(
            supplier_id=globex.id,
            product_id=prod_a.id,
            cost=Decimal("1.00"),
            currency="MXN",
            shipping_method="DIRECT",
            shipping_cost_direct=Decimal("0.00"),
            is_active=False,
        )
        sp_c_usd = SupplierProduct(
            supplier_id=globex.id,
            product_id=prod_c.id,
            cost=Decimal("10.00"),
            currency="USD",
            shipping_method="DIRECT",
            shipping_cost_direct=Decimal("0.00"),
            is_active=True,
        )
        db.add_all([sp_a_cheap, sp_a_expensive, sp_a_inactive, sp_c_usd])
        db.commit()

        state["category_id"] = cat.id
        state["product_a"] = prod_a.id
        state["product_b"] = prod_b.id
        state["product_c"] = prod_c.id
        state["sp_a_cheap"] = sp_a_cheap.id
        state["sp_c_usd"] = sp_c_usd.id
    finally:
        db.close()


_seed()


def teardown_module(module):
    """Wipe everything this module created so tests/test_pos.py (sharing the
    same engine when both files run in one process) starts from a clean
    register: empty pos tables, folio counter reset, no suppliers/costs."""
    db = SessionLocal()
    try:
        db.query(StockMovement).delete(synchronize_session=False)
        db.query(CashMovement).delete(synchronize_session=False)
        db.query(CashSession).delete(synchronize_session=False)
        db.query(Sale).filter(Sale.sheet_tab == "POS").delete(synchronize_session=False)
        db.query(PosSaleItem).delete(synchronize_session=False)
        db.query(PosSale).delete(synchronize_session=False)
        db.query(PosFolioCounter).delete(synchronize_session=False)
        db.query(SupplierProduct).delete(synchronize_session=False)
        db.query(Supplier).delete(synchronize_session=False)
        db.query(Product).filter(
            Product.id.in_([state["product_a"], state["product_b"], state["product_c"]])
        ).delete(synchronize_session=False)
        db.query(ProductCategory).filter(
            ProductCategory.id == state["category_id"]
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _sale(items: list[dict], **extra) -> dict:
    body = {"payment_method": "transferencia", "items": items} | extra
    r = client.post("/pos/sales", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── §6.1 cost snapshot: cheapest active supplier, margin, vendedor default ───


def test_mxn_cost_snapshot_and_margin():
    s = _sale(
        [
            {
                "product_id": state["product_a"],
                "description": "Acolchado plateado v2",
                "quantity": 2,
                "unit_price": 232.0,
                "iva": True,
            }
        ]
    )
    assert s["total"] == 464.0

    item = s["items"][0]
    # Cheapest ACTIVE supplier wins (80 + 20 DIRECT shipping = 100), not the
    # more expensive active one nor the cheaper inactive one.
    assert item["supplier_product_id"] == state["sp_a_cheap"]
    assert item["supplier_name"] == "ACME"
    assert item["unit_cost"] == 100.0
    assert item["cost_currency"] == "MXN"
    assert item["exchange_rate"] == 1.0
    assert item["line_cost_mxn"] == 200.0

    assert s["cost_total"] == 200.0
    assert s["cost_complete"] is True
    # margin = total − cost_total = 464 − 200
    assert s["margin_amount"] == 264.0

    # vendedor defaults to the authenticated email
    assert s["vendedor"] == DEV_EMAIL
    assert s["created_by"] == DEV_EMAIL


# ── §6.2 mixed sale: partial cost sum stored, margin NULL ────────────────────


def test_mixed_sale_partial_cost_no_margin():
    s = _sale(
        [
            {
                "product_id": state["product_a"],
                "description": "Acolchado plateado v2",
                "quantity": 2,
                "unit_price": 232.0,
                "iva": True,
            },
            {
                "description": "Artículo libre",
                "quantity": 1,
                "unit_price": 100.0,
                "iva": True,
            },
        ]
    )
    assert s["total"] == 564.0
    # Known line cost is stored even when the rollup is incomplete
    assert s["items"][0]["line_cost_mxn"] == 200.0
    free = s["items"][1]
    for field in (
        "supplier_product_id",
        "supplier_name",
        "unit_cost",
        "cost_currency",
        "exchange_rate",
        "line_cost_mxn",
    ):
        assert free[field] is None, field
    assert s["cost_total"] == 200.0
    assert s["cost_complete"] is False
    assert s["margin_amount"] is None


def test_no_supplier_cost_product_all_null():
    s = _sale(
        [
            {
                "product_id": state["product_b"],
                "description": "Grapa jardinera v2",
                "quantity": 4,
                "unit_price": 50.0,
                "iva": True,
            }
        ]
    )
    item = s["items"][0]
    assert item["supplier_product_id"] is None
    assert item["unit_cost"] is None
    assert item["line_cost_mxn"] is None
    assert s["cost_total"] is None
    assert s["cost_complete"] is False
    assert s["margin_amount"] is None


# ── §6.3 USD path: mocked rate, rate-unavailable, service raising ────────────


def test_usd_cost_with_mocked_rate(monkeypatch):
    monkeypatch.setattr(
        "routes.pos.exchange_rate_service.get_exchange_rate",
        lambda from_currency, to_currency: 17.5,
    )
    s = _sale(
        [
            {
                "product_id": state["product_c"],
                "description": "Valvula importada v2",
                "quantity": 3,
                "unit_price": 350.0,
                "iva": True,
            }
        ]
    )
    item = s["items"][0]
    assert item["supplier_product_id"] == state["sp_c_usd"]
    assert item["supplier_name"] == "GLOBEX"
    assert item["unit_cost"] == 10.0
    assert item["cost_currency"] == "USD"
    assert item["exchange_rate"] == 17.5
    # 3 × 10 × 17.5
    assert item["line_cost_mxn"] == 525.0
    assert s["cost_total"] == 525.0
    assert s["cost_complete"] is True
    # margin = 1050 − 525
    assert s["margin_amount"] == 525.0


def test_usd_rate_unavailable_sale_still_ok(monkeypatch):
    monkeypatch.setattr(
        "routes.pos.exchange_rate_service.get_exchange_rate",
        lambda from_currency, to_currency: None,
    )
    s = _sale(
        [
            {
                "product_id": state["product_c"],
                "description": "Valvula importada v2",
                "quantity": 2,
                "unit_price": 350.0,
                "iva": True,
            }
        ]
    )
    item = s["items"][0]
    # Raw cost snapshot kept; conversion unknown
    assert item["supplier_product_id"] == state["sp_c_usd"]
    assert item["unit_cost"] == 10.0
    assert item["cost_currency"] == "USD"
    assert item["exchange_rate"] is None
    assert item["line_cost_mxn"] is None
    assert s["cost_total"] is None
    assert s["cost_complete"] is False
    assert s["margin_amount"] is None


def test_usd_rate_service_raising_sale_still_ok(monkeypatch):
    def _boom(from_currency, to_currency):
        raise RuntimeError("FX API down")

    monkeypatch.setattr("routes.pos.exchange_rate_service.get_exchange_rate", _boom)
    s = _sale(
        [
            {
                "product_id": state["product_c"],
                "description": "Valvula importada v2",
                "quantity": 1,
                "unit_price": 350.0,
                "iva": True,
            }
        ]
    )
    item = s["items"][0]
    assert item["exchange_rate"] is None
    assert item["line_cost_mxn"] is None
    assert s["cost_complete"] is False
    assert s["margin_amount"] is None


# ── §6.4 explicit vendedor, ?vendedor= filter, /pos/stats/vendedores ─────────


def test_vendedor_filter_and_stats():
    ana = _sale(
        [
            {
                "product_id": state["product_a"],
                "description": "Acolchado plateado v2",
                "quantity": 1,
                "unit_price": 232.0,
                "iva": True,
            }
        ],
        vendedor="ana@impag.mx",
    )
    assert ana["vendedor"] == "ana@impag.mx"
    _sale(
        [{"description": "Flete local", "quantity": 1, "unit_price": 100.0}],
        vendedor="beto@impag.mx",
    )

    r = client.get("/pos/sales", params={"vendedor": "ana@impag.mx"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["folio"] == ana["folio"]
    assert body["items"][0]["vendedor"] == "ana@impag.mx"

    r = client.get(
        "/pos/stats/vendedores", params={"date_from": TODAY, "date_to": TODAY}
    )
    assert r.status_code == 200, r.text
    rows = {row["vendedor"]: row for row in r.json()["items"]}

    ana_row = rows["ana@impag.mx"]
    assert ana_row["sales_count"] == 1
    assert ana_row["total"] == 232.0
    # 232 − 100 cost, the only cost_complete sale for ana
    assert ana_row["margin_total"] == 132.0
    assert ana_row["margin_known_count"] == 1

    beto_row = rows["beto@impag.mx"]
    assert beto_row["sales_count"] == 1
    assert beto_row["total"] == 100.0
    # No cost_complete sales: margin_total is 0.0 with margin_known_count 0
    # (the frontend renders '—' off the count, not off margin_total)
    assert beto_row["margin_total"] == 0.0
    assert beto_row["margin_known_count"] == 0

    # Default-vendedor sales from the earlier tests roll up under the dev user
    assert rows[DEV_EMAIL]["sales_count"] >= 1


# ── §6.5 factura (CFDI) details round-trip ───────────────────────────────────


def test_factura_fields_round_trip():
    s = _sale(
        [{"description": "Artículo libre", "quantity": 1, "unit_price": 116.0}],
        requires_invoice=True,
        rfc="  XAXX010101000 ",
        razon_social="Público en General",
        uso_cfdi="G03",
        cfdi_email="facturas@cliente.mx",
    )
    assert s["requires_invoice"] is True
    assert s["rfc"] == "XAXX010101000"  # trimmed, stored verbatim
    assert s["razon_social"] == "Público en General"
    assert s["uso_cfdi"] == "G03"
    assert s["cfdi_email"] == "facturas@cliente.mx"

    r = client.get(f"/pos/sales/{s['id']}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["rfc"] == "XAXX010101000"
    assert detail["razon_social"] == "Público en General"
    assert detail["uso_cfdi"] == "G03"
    assert detail["cfdi_email"] == "facturas@cliente.mx"


# ── Mixed-currency suppliers: normalized comparison picks the true cheapest ──


def _seed_mixed_currency_product() -> int:
    """Product with a USD supplier (10 USD ≈ 175 MXN @17.5) and an MXN
    supplier at 150 — the MXN one is the true cheapest."""
    db = SessionLocal()
    try:
        prod = Product(
            name="Bomba mixta v2",
            sku=f"MIX-{os.urandom(3).hex()}",
            category_id=state["category_id"],
            unit=ProductUnit.PIEZA,
            iva=True,
            price=Decimal("580.00"),
            stock=5,
            is_active=True,
        )
        db.add(prod)
        db.flush()
        usd = SupplierProduct(
            supplier_id=db.query(Supplier).filter_by(name="GLOBEX").first().id,
            product_id=prod.id,
            cost=Decimal("10.00"),
            currency="USD",
            shipping_method="DIRECT",
            shipping_cost_direct=Decimal("0.00"),
            is_active=True,
        )
        mxn = SupplierProduct(
            supplier_id=db.query(Supplier).filter_by(name="ACME").first().id,
            product_id=prod.id,
            cost=Decimal("150.00"),
            currency="MXN",
            shipping_method="DIRECT",
            shipping_cost_direct=Decimal("0.00"),
            is_active=True,
        )
        db.add_all([usd, mxn])
        db.commit()
        state["sp_mixed_mxn"] = mxn.id
        return prod.id
    finally:
        db.close()


def test_mixed_currency_picks_true_cheapest(monkeypatch):
    monkeypatch.setattr(
        "routes.pos.exchange_rate_service.get_exchange_rate",
        lambda from_currency, to_currency: 17.5,
    )
    pid = _seed_mixed_currency_product()
    s = _sale(
        [
            {
                "product_id": pid,
                "description": "Bomba mixta",
                "quantity": 1,
                "unit_price": 580.0,
            }
        ]
    )
    item = s["items"][0]
    # 150 MXN beats 10 USD (≈175 MXN) despite the smaller raw number
    assert item["supplier_product_id"] == state["sp_mixed_mxn"]
    assert item["cost_currency"] == "MXN"
    assert item["unit_cost"] == 150.0
    assert s["cost_complete"] is True
    assert s["margin_amount"] == 430.0  # 580 − 150


def test_mixed_currency_rate_unavailable_prefers_mxn(monkeypatch):
    monkeypatch.setattr(
        "routes.pos.exchange_rate_service.get_exchange_rate",
        lambda from_currency, to_currency: None,
    )
    pid = _seed_mixed_currency_product()
    s = _sale(
        [
            {
                "product_id": pid,
                "description": "Bomba mixta 2",
                "quantity": 1,
                "unit_price": 580.0,
            }
        ]
    )
    item = s["items"][0]
    # No rate → USD candidates rank after every MXN row; the bookable MXN wins
    assert item["cost_currency"] == "MXN"
    assert item["unit_cost"] == 150.0
    assert s["cost_complete"] is True
