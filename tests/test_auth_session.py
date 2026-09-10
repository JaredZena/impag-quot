"""
Hermetic tests for app session tokens (auth.py) and POST /auth/session
(routes/auth_session.py). Google verification is stubbed — no network.

Run: venv/bin/python -m pytest tests/test_auth_session.py -q
"""

import os

# Must run BEFORE any project import: auth.py reads these at import time, and
# the fake DATABASE_URL keeps models.py away from production.
os.environ["DATABASE_URL"] = "postgresql://test:test@auth-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "false"
os.environ["ALLOWED_EMAILS"] = "hernan@impag.test,jd@impag.test"
os.environ["APP_SESSION_SECRET"] = "s" * 48

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import auth
from routes import auth_session

HERNAN = {
    "email": "hernan@impag.test",
    "name": "Hernán",
    "picture": None,
    "user_id": "g-123",
}


def _bearer(raw: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=raw)


def _flip_last(text: str) -> str:
    return text[:-1] + ("A" if text[-1] != "A" else "B")


def _tamper_signature(token: str) -> str:
    prefix, body, sig = token.split(".")
    return f"{prefix}.{body}.{_flip_last(sig)}"


def _tamper_payload(token: str) -> str:
    prefix, body, sig = token.split(".")
    return f"{prefix}.{_flip_last(body)}.{sig}"


def test_roundtrip_returns_user():
    token, expires_at = auth.issue_session_token(HERNAN, now=1_000)
    assert token.startswith("impag1.")
    assert expires_at == 1_000 + 30 * 86400
    assert auth.verify_session_token(token, now=2_000) == HERNAN
    # The regular dependency accepts a current token too, with no Google round-trip.
    fresh, _ = auth.issue_session_token(HERNAN)
    assert auth.verify_google_token(_bearer(fresh))["email"] == "hernan@impag.test"


@pytest.mark.parametrize(
    "mutate",
    [
        _tamper_signature,
        _tamper_payload,
        lambda t: t + ".extra",
        lambda t: "impag1.bm90LWpzb24.xx",
        lambda t: "impag1..",
    ],
)
def test_tampered_tokens_are_rejected(mutate):
    token, _ = auth.issue_session_token(HERNAN)
    with pytest.raises(HTTPException) as exc:
        auth.verify_google_token(_bearer(mutate(token)))
    assert exc.value.status_code == 401


def test_expired_token_is_rejected():
    token, expires_at = auth.issue_session_token(HERNAN, now=1_000)
    with pytest.raises(HTTPException) as exc:
        auth.verify_session_token(token, now=expires_at + 1)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Sesión expirada"


def test_removed_email_loses_access_immediately(monkeypatch):
    token, _ = auth.issue_session_token(HERNAN)
    monkeypatch.setattr(auth, "ALLOWED_EMAILS", {"jd@impag.test"})
    with pytest.raises(HTTPException) as exc:
        auth.verify_session_token(token)
    assert exc.value.status_code == 403


def test_rotating_or_removing_the_secret_invalidates_sessions(monkeypatch):
    token, _ = auth.issue_session_token(HERNAN)
    monkeypatch.setenv("APP_SESSION_SECRET", "t" * 48)
    with pytest.raises(HTTPException):
        auth.verify_session_token(token)
    monkeypatch.setenv("APP_SESSION_SECRET", "too-short")  # < 32 chars = off
    assert not auth.session_secret_configured()
    with pytest.raises(HTTPException):
        auth.verify_session_token(token)
    with pytest.raises(RuntimeError):
        auth.issue_session_token(HERNAN)


def test_ttl_is_configurable(monkeypatch):
    monkeypatch.setenv("APP_SESSION_TTL_DAYS", "7")
    _, expires_at = auth.issue_session_token(HERNAN, now=0)
    assert expires_at == 7 * 86400


# ==================== POST /auth/session ====================


def _fake_google(creds: HTTPAuthorizationCredentials) -> dict:
    if creds.credentials != "google-id-token":
        raise HTTPException(status_code=401, detail="Invalid token")
    return HERNAN


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(auth_session, "verify_google_token", _fake_google)
    app = FastAPI()
    app.include_router(auth_session.router)

    @app.get("/whoami")
    def whoami(user: dict = Depends(auth.verify_google_token)):  # noqa: B008
        return {"email": user["email"]}

    return TestClient(app)


def test_exchange_google_token_for_session(client):
    resp = client.post(
        "/auth/session", headers={"Authorization": "Bearer google-id-token"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["token"].startswith("impag1.")
    assert data["email"] == "hernan@impag.test"
    # The session token works on any protected endpoint.
    who = client.get("/whoami", headers={"Authorization": f"Bearer {data['token']}"})
    assert who.json() == {"email": "hernan@impag.test"}


def test_session_cannot_extend_itself(client):
    token, _ = auth.issue_session_token(HERNAN)
    resp = client.post("/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


def test_invalid_google_token_is_rejected(client):
    resp = client.post("/auth/session", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_feature_off_without_secret(client, monkeypatch):
    monkeypatch.delenv("APP_SESSION_SECRET")
    resp = client.post(
        "/auth/session", headers={"Authorization": "Bearer google-id-token"}
    )
    assert resp.status_code == 503


def test_missing_authorization_header(client):
    assert client.post("/auth/session").status_code in (401, 403)
