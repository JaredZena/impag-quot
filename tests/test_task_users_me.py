"""
Hermetic tests for GET /task-users/me auto-creating the task user on first
visit (allowlisted accounts used to land on a 404 "User not found in task
system"). SQLite in-memory with only the task_user table; auth overridden.

Run: venv/bin/python -m pytest tests/test_task_users_me.py -q
"""

import os

os.environ["DATABASE_URL"] = "postgresql://test:test@task-users-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import verify_google_token
from models import Base, TaskUser, get_db
from routes import task_users

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
ADRIANA = {
    "email": "adriana@impag.test",
    "name": "Adriana García",
    "picture": None,
    "user_id": "g-9",
}


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine, tables=[TaskUser.__table__])
    Base.metadata.create_all(engine, tables=[TaskUser.__table__])
    app = FastAPI()
    app.include_router(task_users.router)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    current = {"user": dict(ADRIANA)}
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_google_token] = lambda: current["user"]
    c = TestClient(app)
    c.current = current
    return c


def _seed(**fields):
    db = TestingSession()
    try:
        db.add(TaskUser(**fields))
        db.commit()
    finally:
        db.close()


def test_first_visit_creates_the_task_user(client):
    resp = client.get("/task-users/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["email"] == "adriana@impag.test"
    assert data["display_name"] == "Adriana García"
    assert data["role"] == "member" and data["is_active"] is True
    again = client.get("/task-users/me").json()["data"]
    assert again["id"] == data["id"]  # no duplicate on the second visit


def test_existing_user_is_returned_unchanged(client):
    _seed(email="adriana@impag.test", display_name="Adri", role="admin", is_active=True)
    data = client.get("/task-users/me").json()["data"]
    assert data["display_name"] == "Adri" and data["role"] == "admin"


def test_deactivated_user_is_not_recreated(client):
    _seed(
        email="adriana@impag.test", display_name="Adri", role="member", is_active=False
    )
    assert client.get("/task-users/me").status_code == 403


def test_display_name_falls_back_to_email(client):
    client.current["user"] = {"email": "juan.perez@impag.test", "name": None}
    data = client.get("/task-users/me").json()["data"]
    assert data["display_name"] == "juan.perez"
