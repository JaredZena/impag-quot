"""
Hermetic tests for product units: KIT, CAJA and PAQUETE are accepted on
POST /products and PUT /products/{id}, in any case. Own SQLite file behind a
get_db override; no real DB, no network.

Run: venv/bin/python -m pytest tests/test_product_units.py -q
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import verify_google_token
from main import app
from models import Base, Product, ProductUnit, get_db

_tmpdir = tempfile.mkdtemp(prefix="product_units_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'units.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

client = TestClient(app)

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
    app.dependency_overrides[verify_google_token] = lambda: {
        "email": "hernan@example.com"
    }


def teardown_module(module):
    for dep in (get_db, verify_google_token):
        app.dependency_overrides.pop(dep, None)
    app.dependency_overrides.update(_saved_overrides)


@pytest.fixture(autouse=True)
def _clean():
    db = TestingSession()
    db.query(Product).delete()
    db.commit()
    db.close()
    yield


def _make(unit=ProductUnit.PIEZA) -> int:
    db = TestingSession()
    p = Product(name="Kit de riego", sku="KIT-RIEGO", unit=unit)
    db.add(p)
    db.commit()
    pid = p.id
    db.close()
    return pid


def test_enum_has_the_units_hernan_asked_for():
    assert {"KIT", "CAJA", "PAQUETE"} <= {u.value for u in ProductUnit}


@pytest.mark.parametrize("unit", ["KIT", "CAJA", "PAQUETE"])
def test_create_with_new_unit(unit):
    res = client.post(
        "/products", json={"name": "Grapa", "sku": f"G-{unit}", "unit": unit}
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["unit"] == unit


@pytest.mark.parametrize(
    "sent,saved", [("caja", "CAJA"), (" Kit ", "KIT"), ("Paquete", "PAQUETE")]
)
def test_update_accepts_any_case(sent, saved):
    pid = _make()
    res = client.put(f"/products/{pid}", json={"unit": sent})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["unit"] == saved
    db = TestingSession()
    assert db.get(Product, pid).unit.value == saved
    db.close()


def test_unknown_unit_is_rejected_and_row_kept():
    pid = _make(ProductUnit.ROLLO)
    res = client.put(f"/products/{pid}", json={"unit": "BULTO"})
    assert res.status_code == 422
    db = TestingSession()
    assert db.get(Product, pid).unit == ProductUnit.ROLLO
    db.close()


def test_update_without_unit_keeps_it():
    pid = _make(ProductUnit.KIT)
    res = client.put(f"/products/{pid}", json={"name": "Kit de riego 2"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["unit"] == "KIT"
