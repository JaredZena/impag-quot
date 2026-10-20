"""
Hermetic tests for "Especificaciones en la página" (Product.storefront_description):
saved through PUT /products/{id}, returned by GET /products/{id} and served to
the storefront sync by GET /storefront/products. SQLite in-memory.

Run: venv/bin/python -m pytest tests/test_storefront_description.py -q
"""

import os

os.environ["DATABASE_URL"] = "postgresql://test:test@storefront-description.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routes.products as products_routes
import routes.storefront as storefront_routes
from models import Base, Product, get_db

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
KEY = "sync-test-key"

app = FastAPI()
app.include_router(products_routes.router)
app.include_router(storefront_routes.router)


def _db():
    s = Session()
    try:
        yield s
    finally:
        s.close()


app.dependency_overrides[get_db] = _db
client = TestClient(app)


@pytest.fixture(autouse=True)
def product(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", KEY)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = Session()
    p = Product(name="Trampa gusano barrenador", sku="TRAMPA-GB", price=150)
    s.add(p)
    s.commit()
    pid = p.id
    s.close()
    return pid


def _feed_row(pid):
    rows = client.get("/storefront/products", headers={"X-API-Key": KEY}).json()["data"]
    return next(r for r in rows if r["id"] == pid)


def test_saved_text_is_normalized_and_reaches_the_feed(product):
    res = client.put(
        f"/products/{product}",
        json={
            "storefront_description": (
                "  Trampa para   mosca.\r\n\n\n\nEspecificaciones:\n"
                "Material:   PET \n- Reutilizable  \n\n"
            )
        },
    )
    assert res.json()["success"] is True
    expected = "Trampa para mosca.\n\nEspecificaciones:\nMaterial: PET\n- Reutilizable"
    assert res.json()["data"]["storefront_description"] == expected
    assert client.get(f"/products/{product}").json()["data"]["storefront_description"] == expected
    assert _feed_row(product)["storefront_description"] == expected


def test_blank_text_clears_it(product):
    client.put(f"/products/{product}", json={"storefront_description": "Material: PET"})
    client.put(f"/products/{product}", json={"storefront_description": " \n  \n"})
    assert client.get(f"/products/{product}").json()["data"]["storefront_description"] is None
    assert _feed_row(product)["storefront_description"] is None


def test_other_edits_leave_it_alone(product):
    client.put(f"/products/{product}", json={"storefront_description": "Material: PET"})
    client.put(f"/products/{product}", json={"storefront_title": "Trampa Azul"})
    data = client.get(f"/products/{product}").json()["data"]
    assert data["storefront_title"] == "Trampa Azul"
    assert data["storefront_description"] == "Material: PET"


def test_too_long_text_is_rejected(product):
    res = client.put(
        f"/products/{product}",
        json={"storefront_description": "x" * (products_routes.STOREFRONT_DESCRIPTION_MAX + 1)},
    )
    assert res.status_code == 422
