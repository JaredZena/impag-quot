"""
Hermetic tests for «Ligar producto»: PUT /storefront/pages/link and GET
/storefront/pages/links (admin, Google auth) and GET /storefront/links (the
storefront sync, X-API-Key) in routes/storefront.py. Own SQLite file behind a
get_db override; no real DB, no network.

Run: venv/bin/python -m pytest tests/test_storefront_links.py -q
"""

import os
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import verify_google_token
from main import app
from models import Base, Product, get_db

_tmpdir = tempfile.mkdtemp(prefix="storefront_links_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'links.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

client = TestClient(app)

EDITOR = "hernan@example.com"
FEED_KEY = "sync-key"
PLANTADORA = "plantadora-manual"
HEATER = "calefactor-diesel-l-b-white-k175®"

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


def teardown_module(module):
    for dep in (get_db, verify_google_token):
        app.dependency_overrides.pop(dep, None)
    app.dependency_overrides.update(_saved_overrides)


@pytest.fixture(scope="module")
def products():
    db = TestingSession()
    rows = {
        "active": Product(name="PLANTADORA MANUAL 7.5", sku="PLANT-MAN", price=2500),
        "other": Product(name="Calefactor K175", sku="CAL-K175", price=9000),
        "inactive": Product(name="Viejo", sku="VIEJO", price=1, is_active=False),
        "archived": Product(
            name="Archivado",
            sku="ARCH",
            price=1,
            archived_at=datetime.now(timezone.utc),
        ),
    }
    db.add_all(rows.values())
    db.commit()
    ids = {k: v.id for k, v in rows.items()}
    db.close()
    return ids


def _put(body):
    return client.put("/storefront/pages/link", json=body)


def _feed(key=FEED_KEY):
    return client.get("/storefront/links", headers={"X-API-Key": key} if key else {})


def test_link_relink_unlink_and_feed(monkeypatch, products):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    assert _feed().json() == {"links": []}

    res = _put({"handle": f"  {PLANTADORA} ", "product_id": products["active"]})
    assert res.status_code == 200
    body = res.json()
    assert body["handle"] == PLANTADORA
    assert body["product_id"] == products["active"]
    assert body["product_name"] == "PLANTADORA MANUAL 7.5"
    assert body["updated_by"] == EDITOR
    assert body["updated_at"]

    # The same page linked again to another product: one row, the new product.
    res = _put({"handle": PLANTADORA, "product_id": products["other"]})
    assert res.json()["product_id"] == products["other"]

    # A wrong match removed: the row stays and tells the sync "no product".
    res = _put({"handle": HEATER, "product_id": None})
    assert res.status_code == 200
    assert res.json()["product_id"] is None
    assert res.json()["product_name"] is None

    assert _feed().json() == {
        "links": [
            {"handle": HEATER, "product_id": None},
            {"handle": PLANTADORA, "product_id": products["other"]},
        ]
    }
    rows = client.get("/storefront/pages/links").json()["links"]
    assert [(r["handle"], r["product_name"]) for r in rows] == [
        (HEATER, None),
        (PLANTADORA, "Calefactor K175"),
    ]


def test_only_active_products_can_price_a_page(products):
    for key in ("inactive", "archived"):
        assert _put({"handle": "x", "product_id": products[key]}).status_code == 404
    assert _put({"handle": "x", "product_id": 999999}).status_code == 404
    assert _put({"handle": "x", "product_id": 0}).status_code == 422
    assert _put({"handle": "x", "product_id": "abc"}).status_code == 422
    assert _put({"handle": "x"}).status_code == 422  # product_id is required


def test_rejects_bad_handles(products):
    for handle in ["", "   ", "a/b", "../etc", "Plantadora", "con espacio", "x" * 256]:
        res = _put({"handle": handle, "product_id": products["active"]})
        assert res.status_code == 422, handle


def test_feed_needs_the_key(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    assert _feed(key=None).status_code == 401
    assert _feed(key="wrong").status_code == 401
    monkeypatch.delenv("STOREFRONT_API_KEY")
    assert _feed().status_code == 503
