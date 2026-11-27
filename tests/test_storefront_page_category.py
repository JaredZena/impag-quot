"""
Hermetic tests for «Colección en la tienda»: PUT/GET /storefront/pages/category
(admin, Google auth) and GET /storefront/page-categories (the storefront sync,
X-API-Key) in routes/storefront.py. Own SQLite file behind a get_db override;
no real DB, no network.

Run: venv/bin/python -m pytest tests/test_storefront_page_category.py -q
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import verify_google_token
from main import app
from models import (
    Base,
    Product,
    StorefrontNewPage,
    StorefrontPageCategory,
    get_db,
)

_tmpdir = tempfile.mkdtemp(prefix="storefront_page_category_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'category.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

client = TestClient(app)

EDITOR = "hernan@example.com"
FEED_KEY = "sync-key"
HULE = "rollo-de-hule-negro-6-m"
MARKED = "calefactor-diesel-l-b-white-k175®"

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


@pytest.fixture(autouse=True)
def _clean():
    db = TestingSession()
    db.query(StorefrontPageCategory).delete()
    db.query(StorefrontNewPage).delete()
    db.query(Product).delete()
    db.commit()
    db.close()
    yield


def _put(body):
    return client.put("/storefront/pages/category", json=body)


def _feed(key=FEED_KEY):
    return client.get(
        "/storefront/page-categories", headers={"X-API-Key": key} if key else {}
    )


def test_move_list_and_feed(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    assert _feed().json() == {"categories": []}

    res = _put({"handle": f" {HULE} ", "category_slug": " invernaderos-cubiertas "})
    assert res.status_code == 200
    body = res.json()
    assert body["handle"] == HULE
    assert body["category_slug"] == "invernaderos-cubiertas"
    assert body["updated_by"] == EDITOR
    assert body["updated_at"]

    # Picked again: one row per page, the last pick wins.
    assert _put({"handle": HULE, "category_slug": "geotanques"}).status_code == 200
    assert _put({"handle": MARKED, "category_slug": "climatizacion-agricola"}).status_code == 200
    assert _feed().json() == {
        "categories": sorted(
            [
                {"handle": HULE, "category_slug": "geotanques"},
                {"handle": MARKED, "category_slug": "climatizacion-agricola"},
            ],
            key=lambda r: r["handle"],
        )
    }
    pages = client.get("/storefront/pages/categories").json()["pages"]
    assert [(p["handle"], p["category_slug"]) for p in pages] == sorted(
        [(HULE, "geotanques"), (MARKED, "climatizacion-agricola")]
    )


def test_admin_made_page_moves_on_its_own_row(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    db = TestingSession()
    p = Product(name="Malacate", sku="MAL-1200LB")
    db.add(p)
    db.flush()
    db.add(StorefrontNewPage(handle="malacate-1200", product_id=p.id, category_slug="geotanques"))
    db.commit()
    db.close()

    res = _put({"handle": "malacate-1200", "category_slug": "invernaderos-cubiertas"})
    assert res.status_code == 200
    assert res.json()["category_slug"] == "invernaderos-cubiertas"
    db = TestingSession()
    assert db.get(StorefrontNewPage, "malacate-1200").category_slug == "invernaderos-cubiertas"
    db.close()
    # No second source for the same page: the new-pages feed carries it.
    assert _feed().json() == {"categories": []}


def test_rejects_bad_input():
    for handle in ["", "  ", "a/b", "../etc", "Hule", "con espacio", "<b>x</b>", "x" * 256]:
        assert _put({"handle": handle, "category_slug": "geotanques"}).status_code == 422, handle
    for slug in ["", "x", "Geotanques", "geo tanques", "../x", "geo_tanques", "x" * 101]:
        assert _put({"handle": HULE, "category_slug": slug}).status_code == 422, slug
    assert _put({"handle": HULE}).status_code == 422
    assert client.get("/storefront/pages/categories").json() == {"pages": []}


def test_feed_needs_the_key(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    assert _feed(key=None).status_code == 401
    assert _feed(key="wrong").status_code == 401
    monkeypatch.delenv("STOREFRONT_API_KEY")
    assert _feed().status_code == 503
