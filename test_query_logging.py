"""
Hermetic tests for /query logging + feedback endpoint.
SQLite in-memory DB, auth overridden, RAG function monkeypatched — no
external APIs. Run: venv/bin/python -m pytest test_query_logging.py -v
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from auth import verify_google_token
from models import Base, Query, get_db

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine)
# create_all trips on a duplicate index name elsewhere in the schema
# (ix_task_category_id declared by two tables) — create only what we test.
Query.__table__.create(bind=engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


main.app.dependency_overrides[get_db] = override_get_db
main.app.dependency_overrides[verify_google_token] = lambda: {"email": "tester@impag.mx"}
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fake_rag(monkeypatch):
    def fake_fn(query, chat_history=None, customer_name=None, customer_location=None):
        return {
            "quotation": "COTIZACIÓN DE PRUEBA",
            "complexity_tier": "sencilla",
            "retrieved_chunk_ids": ["doc-1-chunk-0", "doc-2-chunk-3"],
        }
    monkeypatch.setattr(main, "get_rag_query_function", lambda: fake_fn)


def test_query_logs_all_fields():
    resp = client.post("/query", json={"query": "cotización de prueba de cintilla"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_id"] > 0

    db = TestingSession()
    row = db.query(Query).filter(Query.id == body["query_id"]).one()
    assert row.user_email == "tester@impag.mx"
    assert row.retrieved_chunk_ids == ["doc-1-chunk-0", "doc-2-chunk-3"]
    assert row.latency_ms is not None and row.latency_ms >= 0
    assert row.complexity_tier == "sencilla"
    assert row.feedback is None
    db.close()


def test_feedback_roundtrip():
    query_id = client.post("/query", json={"query": "otra cotización"}).json()["query_id"]

    resp = client.post(f"/queries/{query_id}/feedback",
                       json={"feedback": -1, "feedback_text": "precio de cintilla incorrecto"})
    assert resp.status_code == 200

    db = TestingSession()
    row = db.query(Query).filter(Query.id == query_id).one()
    assert row.feedback == -1
    assert row.feedback_text == "precio de cintilla incorrecto"
    db.close()


def test_feedback_validation():
    assert client.post("/queries/999999/feedback", json={"feedback": 5}).status_code == 422
    assert client.post("/queries/999999/feedback", json={"feedback": 1}).status_code == 404
