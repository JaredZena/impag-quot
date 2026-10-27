"""
Hermetic tests for «Mostrar en la tienda»: PUT/GET /storefront/pages/visibility
(admin, Google auth) and GET /storefront/visibility (the storefront sync,
X-API-Key) in routes/storefront.py. Own SQLite file behind a get_db override;
no real DB, no network.

Run: venv/bin/python -m pytest tests/test_storefront_visibility.py -q
"""

import os
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import verify_google_token
from main import app
from models import Base, get_db

_tmpdir = tempfile.mkdtemp(prefix="storefront_visibility_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'visibility.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

client = TestClient(app)

EDITOR = "hernan@example.com"
FEED_KEY = "sync-key"
HEATER = "calefactor-l-b-white-hl400"
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


def _put(body):
    return client.put("/storefront/pages/visibility", json=body)


def _feed(key=FEED_KEY):
    return client.get(
        "/storefront/visibility", headers={"X-API-Key": key} if key else {}
    )


def test_hide_show_and_feed(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    assert _feed().json() == {"hidden": []}

    res = _put(
        {"handle": f"  {HEATER} ", "hidden": True, "note": "  ya no lo   manejamos "}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["handle"] == HEATER
    assert body["hidden"] is True
    assert body["note"] == "ya no lo manejamos"
    assert body["updated_by"] == EDITOR
    assert body["updated_at"]

    assert _put({"handle": MARKED, "hidden": True}).status_code == 200
    assert _feed().json() == {"hidden": sorted([HEATER, MARKED])}

    # Shown again: the row stays (who/when), the feed drops it.
    res = _put({"handle": HEATER, "hidden": False, "note": ""})
    assert res.status_code == 200
    assert res.json()["hidden"] is False
    assert res.json()["note"] is None
    assert _feed().json() == {"hidden": [MARKED]}

    pages = client.get("/storefront/pages/visibility").json()["pages"]
    assert [(p["handle"], p["hidden"]) for p in pages] == sorted(
        [(HEATER, False), (MARKED, True)]
    )


def test_rejects_bad_handles():
    for handle in [
        "",
        "   ",
        "a/b",
        "../etc",
        "Calefactor",
        "con espacio",
        "<b>x</b>",
        "x" * 256,
    ]:
        assert _put({"handle": handle, "hidden": True}).status_code == 422, handle
    assert _put({"handle": HEATER}).status_code == 422  # hidden is required
    assert (
        _put({"handle": HEATER, "hidden": True, "note": "x" * 301}).status_code == 422
    )


def test_feed_needs_the_key(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", FEED_KEY)
    assert _feed(key=None).status_code == 401
    assert _feed(key="wrong").status_code == 401
    monkeypatch.delenv("STOREFRONT_API_KEY")
    assert _feed().status_code == 503
