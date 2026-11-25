"""
Hermetic tests for «Nueva página»: POST/GET /storefront/pages/new and DELETE
/storefront/pages/new/{handle} (admin, Google auth), GET /storefront/new-pages
(the storefront sync, X-API-Key) in routes/storefront.py. Own SQLite file
behind a get_db override; no real DB, no network.

Run: venv/bin/python -m pytest tests/test_storefront_new_pages.py -q
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
from models import (
    Base,
    Product,
    StorefrontNewPage,
    StorefrontPageLink,
    StorefrontPageVisibility,
    get_db,
)

_tmpdir = tempfile.mkdtemp(prefix="storefront_new_pages_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'new_pages.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

client = TestClient(app)

EDITOR = "hernan@example.com"
FEED_KEY = "sync-key"
HANDLE = "plasticos-verde-clorofila-al-30-de-sombra-ancho-6-2"

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
def _clean(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    db = TestingSession()
    for model in (
        StorefrontNewPage,
        StorefrontPageLink,
        StorefrontPageVisibility,
        Product,
    ):
        db.query(model).delete()
    db.commit()
    db.close()
    yield


def _product(images=("product-images/1/a.webp",), active=True, archived=False) -> int:
    db = TestingSession()
    p = Product(
        name="PLÁSTICOS VERDE CLOROFILA AL 30% DE SOMBRA ANCHO 6.2",
        sku=f"PV-{datetime.now(timezone.utc).timestamp()}",
        images=list(images),
        is_active=active,
        archived_at=datetime.now(timezone.utc) if archived else None,
    )
    db.add(p)
    db.commit()
    pid = p.id
    db.close()
    return pid


def _create(pid, handle=HANDLE, category="invernaderos-cubiertas"):
    return client.post(
        "/storefront/pages/new",
        json={"handle": handle, "product_id": pid, "category_slug": category},
    )


def test_create_lists_for_admin_and_feed():
    pid = _product()
    res = _create(pid)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["handle"] == HANDLE
    assert body["product_id"] == pid
    assert body["category_slug"] == "invernaderos-cubiertas"
    assert body["created_by"] == EDITOR
    assert body["product_name"].startswith("PLÁSTICOS VERDE")
    assert body["title"] == body["product_name"]

    rows = client.get("/storefront/pages/new").json()["pages"]
    assert [r["handle"] for r in rows] == [HANDLE]

    feed = client.get("/storefront/new-pages", headers={"X-API-Key": FEED_KEY})
    assert feed.status_code == 200
    assert feed.json() == {
        "pages": [
            {
                "handle": HANDLE,
                "product_id": pid,
                "category_slug": "invernaderos-cubiertas",
            }
        ]
    }


def test_feed_needs_the_key():
    assert client.get("/storefront/new-pages").status_code == 401
    assert (
        client.get("/storefront/new-pages", headers={"X-API-Key": "nope"}).status_code
        == 401
    )


def test_product_without_photo_is_refused():
    pid = _product(images=())
    res = _create(pid)
    assert res.status_code == 422
    assert res.json()["detail"] == "product_has_no_photo"


@pytest.mark.parametrize("kwargs", [{"active": False}, {"archived": True}])
def test_inactive_or_archived_product_is_refused(kwargs):
    pid = _product(**kwargs)
    assert _create(pid).status_code == 404


def test_unknown_product_is_refused():
    assert _create(999999).status_code == 404


def test_one_page_per_product():
    pid = _product()
    assert _create(pid).status_code == 201
    res = _create(pid, handle="otra-direccion")
    assert res.status_code == 409
    assert res.json()["detail"] == "product_has_page"


def test_handle_taken_by_another_new_page_or_an_existing_page():
    a, b = _product(), _product()
    assert _create(a).status_code == 201
    res = _create(b)
    assert res.status_code == 409
    assert res.json()["detail"] == "handle_taken"

    db = TestingSession()
    db.add(StorefrontPageVisibility(handle="pagina-de-shopify", hidden=False))
    db.commit()
    db.close()
    res = _create(b, handle="pagina-de-shopify")
    assert res.status_code == 409
    assert res.json()["detail"] == "handle_taken"


@pytest.mark.parametrize(
    "handle",
    [
        "Con-Mayusculas",
        "con espacio",
        "calefactor-k175®",
        "-empieza",
        "termina-",
        "a--b",
        "../x",
        "ab",
    ],
)
def test_bad_handles_are_refused(handle):
    pid = _product()
    assert _create(pid, handle=handle).status_code == 422


@pytest.mark.parametrize("category", ["", "Invernaderos", "riego/../x", "a"])
def test_bad_categories_are_refused(category):
    pid = _product()
    assert _create(pid, category=category).status_code == 422


def test_delete_cancels_and_frees_the_address():
    pid = _product()
    assert _create(pid).status_code == 201
    db = TestingSession()
    db.add(StorefrontPageVisibility(handle=HANDLE, hidden=True))
    db.add(StorefrontPageLink(handle=HANDLE, product_id=pid))
    db.commit()
    db.close()

    res = client.delete(f"/storefront/pages/new/{HANDLE}")
    assert res.status_code == 200
    assert res.json() == {"deleted": HANDLE}
    assert client.get("/storefront/pages/new").json()["pages"] == []
    db = TestingSession()
    assert db.get(StorefrontPageVisibility, HANDLE) is None
    assert db.get(StorefrontPageLink, HANDLE) is None
    db.close()
    # Same product, same address: works again.
    assert _create(pid).status_code == 201


def test_delete_unknown_is_404():
    assert client.delete("/storefront/pages/new/no-existe").status_code == 404


def test_deleting_the_product_removes_its_page_row():
    pid = _product()
    assert _create(pid).status_code == 201
    db = TestingSession()
    db.query(StorefrontNewPage).filter(StorefrontNewPage.product_id == pid).delete()
    db.commit()
    db.close()
    assert client.get("/storefront/pages/new").json()["pages"] == []
