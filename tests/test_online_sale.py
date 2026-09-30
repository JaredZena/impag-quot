"""
Hermetic tests for the "Vender en línea" switch: PUT/DELETE
/products/{id}/online-sale (routes/products.py) and the online_sale field on
the admin product list and the storefront feed (routes/storefront.py). Uses
its own SQLite file behind a get_db override; no real DB, no network. (The
root conftest.py also forces DATABASE_URL to sqlite before any project import.)

Run: venv/bin/python -m pytest tests/test_online_sale.py -q
"""

import os
import tempfile
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import verify_google_token
from main import app
from models import Base, Product, ProductCategory, ProductUnit, get_db

_tmpdir = tempfile.mkdtemp(prefix="online_sale_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'online_sale.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

client = TestClient(app)

EDITOR = "hernan@example.com"
LIVE_ID = 371
UNTOUCHED_ID = 54
ARCHIVED_ID = 900
FEED_KEY = "sync-key"

VALID = {
    "enabled": True,
    "unit_label": "  pieza ",
    "delivery": ["recoger", "paqueteria"],
    "min_qty": 1,
    "max_qty": 50,
    "stock_status": "in_stock",
}

_saved_overrides: dict = {}


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


def setup_module(module):
    for dep in (get_db, verify_google_token):
        if dep in app.dependency_overrides:
            _saved_overrides[dep] = app.dependency_overrides[dep]
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[verify_google_token] = lambda: {"email": EDITOR}
    db = TestingSession()
    try:
        category = ProductCategory(name="Trampas", slug="trampas")
        db.add(category)
        db.flush()
        db.add_all(
            [
                Product(
                    id=LIVE_ID,
                    name="Trampa amarilla",
                    sku="TRAMPA-01",
                    category_id=category.id,
                    unit=ProductUnit.PIEZA,
                    iva=True,
                    price=Decimal("150.55"),
                    stock=7,
                    is_active=True,
                ),
                Product(
                    id=UNTOUCHED_ID,
                    name="Semilla",
                    sku="SEM-01",
                    category_id=category.id,
                    unit=ProductUnit.KG,
                    iva=False,
                    price=Decimal("80.00"),
                    is_active=True,
                ),
                Product(
                    id=ARCHIVED_ID,
                    name="Descontinuado",
                    sku="OLD-01",
                    category_id=category.id,
                    unit=ProductUnit.PIEZA,
                    is_active=True,
                    archived_at=datetime(2026, 1, 1),  # noqa: DTZ001
                ),
            ]
        )
        db.commit()
    finally:
        db.close()


def teardown_module(module):
    for dep in (get_db, verify_google_token):
        app.dependency_overrides.pop(dep, None)
    app.dependency_overrides.update(_saved_overrides)


def _stored(product_id: int):
    db = TestingSession()
    try:
        return db.get(Product, product_id).online_sale
    finally:
        db.close()


def test_put_saves_the_whole_object_with_editor_and_timestamp():
    r = client.put(f"/products/{LIVE_ID}/online-sale", json=VALID)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == LIVE_ID
    sale = body["online_sale"]
    assert sale["enabled"] is True
    assert sale["unit_label"] == "pieza"  # stripped
    assert sale["delivery"] == ["recoger", "paqueteria"]
    assert sale["min_qty"] == 1 and sale["max_qty"] == 50
    assert sale["stock_status"] == "in_stock"
    assert sale["updated_by"] == EDITOR
    assert datetime.fromisoformat(sale["updated_at"]).utcoffset().total_seconds() == 0
    assert _stored(LIVE_ID) == sale


def test_put_replaces_instead_of_merging():
    r = client.put(
        f"/products/{LIVE_ID}/online-sale",
        json={
            **VALID,
            "enabled": False,
            "delivery": ["flete"],
            "stock_status": "backorder",
        },
    )
    assert r.status_code == 200, r.text
    sale = _stored(LIVE_ID)
    assert sale["enabled"] is False
    assert sale["delivery"] == ["flete"]
    assert sale["stock_status"] == "backorder"
    # client-sent audit fields are ignored; the server sets them
    r = client.put(
        f"/products/{LIVE_ID}/online-sale",
        json={**VALID, "updated_by": "spoof@example.com", "extra": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["online_sale"]["updated_by"] == EDITOR
    assert "extra" not in _stored(LIVE_ID)


@pytest.mark.parametrize(
    "patch",
    [
        {"delivery": ["dron"]},
        {"delivery": []},
        {"delivery": ["recoger", "recoger"]},
        {"min_qty": 5, "max_qty": 2},
        {"min_qty": 0},
        {"max_qty": 10000},
        {"unit_label": "   "},
        {"unit_label": "x" * 61},
        {"stock_status": "agotado"},
        {"enabled": None},
    ],
)
def test_put_rejects_invalid_bodies(patch):
    before = _stored(LIVE_ID)
    r = client.put(f"/products/{LIVE_ID}/online-sale", json={**VALID, **patch})
    assert r.status_code == 422, r.text
    assert _stored(LIVE_ID) == before


@pytest.mark.parametrize("field", list(VALID))
def test_put_requires_every_field(field):
    body = {k: v for k, v in VALID.items() if k != field}
    r = client.put(f"/products/{LIVE_ID}/online-sale", json=body)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("product_id", [123456, ARCHIVED_ID])
def test_missing_or_archived_product_is_404(product_id):
    r = client.put(f"/products/{product_id}/online-sale", json=VALID)
    assert r.status_code == 404, r.text
    r = client.delete(f"/products/{product_id}/online-sale")
    assert r.status_code == 404, r.text


def test_admin_list_exposes_online_sale():
    r = client.get("/products", params={"limit": 100})
    assert r.status_code == 200, r.text
    rows = {p["id"]: p for p in r.json()["data"]}
    assert rows[LIVE_ID]["online_sale"] == _stored(LIVE_ID)
    assert rows[UNTOUCHED_ID]["online_sale"] is None


def test_storefront_feed_exposes_online_sale(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    r = client.get("/storefront/products", headers={"X-API-Key": FEED_KEY})
    assert r.status_code == 200, r.text
    rows = {p["id"]: p for p in r.json()["data"]}
    assert rows[LIVE_ID]["online_sale"] == _stored(LIVE_ID)
    assert rows[LIVE_ID]["online_sale"]["unit_label"] == "pieza"
    # never configured in the admin → null, so the sync uses its own config
    assert rows[UNTOUCHED_ID]["online_sale"] is None


def test_delete_clears_back_to_null():
    r = client.delete(f"/products/{LIVE_ID}/online-sale")
    assert r.status_code == 200, r.text
    assert r.json() == {"id": LIVE_ID, "online_sale": None}
    assert _stored(LIVE_ID) is None
