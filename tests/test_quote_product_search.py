"""
Hermetic tests for the prices and stock Hernán sees:
- GET /quotes/product-search/query matches word by word and prices with the
  product's "Precio de venta" (Product.price) before the calculated price.
- The AI cotizador's _compute_final_price prefers Product.price too.
- GET /products returns supplier_stock (sum of the supplier rows) and filters
  min_stock/max_stock on it.
SQLite file behind a get_db override; the cotizador's Pinecone/LLM/embedding
modules are stubbed so nothing reaches the network.

Run: venv/bin/python -m pytest tests/test_quote_product_search.py -q
"""

import os
import sys
import tempfile
import types
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import verify_google_token
from main import app
from models import Base, Product, ProductUnit, Supplier, SupplierProduct, get_db

_tmpdir = tempfile.mkdtemp(prefix="quote_search_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'quote_search.db')}",
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
    app.dependency_overrides[verify_google_token] = lambda: {"email": "hernan@example.com"}

    db = TestingSession()
    try:
        popusa = Supplier(name="POPUSA")
        paula = Supplier(name="Doña Paula")
        db.add_all([popusa, paula])
        db.flush()
        malla = Product(
            id=10, name="Malla sombra 50% raschel 4.20 x 100 m", sku="MS50",
            price=Decimal("6853.45"), iva=True, unit=ProductUnit.ROLLO, stock=0,
        )
        bolsa = Product(id=11, name="Bolsa vivero 17x17", sku="BV17", price=None, iva=True,
                        unit=ProductUnit.PAQUETE, stock=9)
        db.add_all([malla, bolsa])
        db.flush()
        db.add_all([
            SupplierProduct(supplier_id=popusa.id, product_id=10, name="Malla sombra 50% raschel 4.20x100",
                            sku="MS50-P", cost=Decimal("5600"), shipping_cost_direct=Decimal("450"),
                            default_margin=Decimal("0.20"), stock=4, unit="ROLLO"),
            SupplierProduct(supplier_id=paula.id, product_id=10, name="Malla sombra 50% 4.2 x 100",
                            sku="MS50-D", cost=Decimal("5900"), default_margin=Decimal("0.20"), stock=1,
                            unit="ROLLO"),
            SupplierProduct(supplier_id=popusa.id, product_id=11, name="Bolsa vivero 17x17",
                            sku="BV17-P", cost=Decimal("750"), shipping_cost_direct=Decimal("0"),
                            default_margin=Decimal("0.25"), stock=0, unit="PAQUETE"),
        ])
        db.commit()
    finally:
        db.close()


def teardown_module(module):
    for dep in (get_db, verify_google_token):
        app.dependency_overrides.pop(dep, None)
    app.dependency_overrides.update(_saved_overrides)


def _search(q):
    res = client.get("/quotes/product-search/query", params={"q": q})
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_a_sentence_finds_the_product_word_by_word():
    rows = _search("malla sombra de 50% para invernadero")
    assert [r["product_id"] for r in rows] == [10]


def test_precio_de_venta_wins_and_the_product_is_listed_once():
    rows = _search("malla")
    assert len(rows) == 1
    assert rows[0]["display_price"] == 6853.45
    assert rows[0]["price_source"] == "precio_de_venta"
    assert rows[0]["unit"] == "ROLLO"


def test_without_precio_de_venta_the_price_is_calculated():
    rows = _search("bolsa 17x17")
    assert len(rows) == 1
    assert rows[0]["price_source"] == "calculado"
    assert rows[0]["display_price"] == 1000.0  # 750 / (1 - 0.25)


def test_products_list_shows_supplier_stock_and_filters_on_it():
    res = client.get("/products", params={"limit": 50})
    by_id = {p["id"]: p for p in res.json()["data"]}
    assert by_id[10]["supplier_stock"] == 5
    assert by_id[11]["supplier_stock"] == 0

    in_stock = client.get("/products", params={"min_stock": 1}).json()["data"]
    assert [p["id"] for p in in_stock] == [10]
    sold_out = client.get("/products", params={"max_stock": 0}).json()["data"]
    assert [p["id"] for p in sold_out] == [11]


def _import_cotizador():
    # rag_system imports Pinecone, the LLM and the embedder at module level.
    for name, attrs in {
        "rag_system_moved.embeddings": {"generate_embeddings": lambda texts: [[0.0] for _ in texts]},
        "rag_system_moved.pinecone_setup": {"index": None},
        "rag_system_moved.claude_llm_setup": {"llm": None},
    }.items():
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__dict__.update(attrs)
            sys.modules[name] = module
    from rag_system_moved import rag_system

    return rag_system


def test_cotizador_prefers_precio_de_venta():
    rag_system = _import_cotizador()
    db = TestingSession()
    try:
        malla_sp = db.query(SupplierProduct).filter_by(sku="MS50-P").one()
        price, cost_basis, margin, source, _ = rag_system._compute_final_price(malla_sp, 30.0)
        assert price == 6853.45
        assert cost_basis == 6050.0
        assert source == "PRECIO DE VENTA"
        assert round(margin, 1) == 11.7

        bolsa_sp = db.query(SupplierProduct).filter_by(sku="BV17-P").one()
        price, *_ = rag_system._compute_final_price(bolsa_sp, 30.0)
        assert price == 1000.0
    finally:
        db.close()
