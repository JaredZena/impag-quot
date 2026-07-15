"""
Hermetic tests for the WhatsApp agent pipeline: webhook ingest -> AI draft ->
approval queue -> gated approve. SQLite in-memory, auth overridden, drafter
mocked (no LLM/embeddings), sender exercised through the real send gate.

Run: venv/bin/python -m pytest test_whatsapp_agent.py -v
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
import config
import services.whatsapp_drafter as drafter
from auth import verify_google_token
from models import Base, get_db, WAConversation, WAMessage, WADraft

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)
for m in (WAConversation, WAMessage, WADraft):
    m.__table__.create(bind=engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


main.app.dependency_overrides[get_db] = override_get_db
main.app.dependency_overrides[verify_google_token] = lambda: {"email": "operator@impag.mx"}
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def mock_drafter(monkeypatch):
    monkeypatch.setattr(drafter, "draft_whatsapp_reply",
                        lambda msg, history=None, name=None: {
                            "draft_text": "Hola! La cintilla está disponible, ¿cuántos metros necesita?",
                            "product_context": "CINTILLA RO-DRIP ... $4,080",
                        })


@pytest.fixture(autouse=True)
def gate_closed(monkeypatch):
    # Sending must be OFF for these tests — assert the default and enforce it.
    monkeypatch.setattr(config, "wa_sending_enabled", False)
    # No app secret in the test env → the webhook fails closed unless the
    # explicit local-sandbox opt-in is set. Simulate that opt-in here so the
    # unsigned test posts are accepted.
    monkeypatch.setattr(config, "wa_allow_unsigned_webhook", True)


def _inbound(wamid="wamid.T1", body="Que precio tiene la cintilla?"):
    return {"entry": [{"changes": [{"value": {
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": "5216771234567", "profile": {"name": "Cliente Test"}}],
        "messages": [{"from": "5216771234567", "id": wamid, "type": "text", "text": {"body": body}}],
    }}]}]}


def test_webhook_verification_handshake():
    r = client.get("/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": config.wa_verify_token, "hub.challenge": "31415"})
    assert r.status_code == 200 and r.text == "31415"
    bad = client.get("/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "31415"})
    assert bad.status_code == 403


def test_inbound_creates_conversation_message_and_draft():
    r = client.post("/whatsapp/webhook", json=_inbound())
    assert r.status_code == 200
    assert r.json()["ingested"] == 1 and r.json()["drafted"] == 1

    db = TestingSession()
    conv = db.query(WAConversation).filter_by(customer_phone="5216771234567").one()
    assert conv.customer_name == "Cliente Test"
    assert db.query(WAMessage).filter_by(conversation_id=conv.id, direction="inbound").count() == 1
    draft = db.query(WADraft).filter_by(conversation_id=conv.id).one()
    assert draft.status == "pending" and "cintilla" in draft.draft_text.lower()
    db.close()


def test_webhook_dedups_repeated_message_id():
    client.post("/whatsapp/webhook", json=_inbound(wamid="wamid.DUP"))
    second = client.post("/whatsapp/webhook", json=_inbound(wamid="wamid.DUP"))
    assert second.json()["ingested"] == 0 and second.json()["skipped"] == 1


def test_pending_queue_and_gated_approve():
    client.post("/whatsapp/webhook", json=_inbound(wamid="wamid.APPROVE"))
    drafts = client.get("/whatsapp/drafts").json()
    assert len(drafts) >= 1
    target = next(d for d in drafts if d["customer_phone"] == "5216771234567")

    resp = client.post(f"/whatsapp/drafts/{target['id']}/approve")
    assert resp.status_code == 200
    body = resp.json()
    # GATE: recorded, but NOT sent
    assert body["gate_open"] is False
    assert body["send_result"]["dry_run"] is True and body["send_result"]["sent"] is False
    assert body["draft"]["status"] == "approved"

    db = TestingSession()
    out = db.query(WAMessage).filter_by(direction="outbound").order_by(WAMessage.id.desc()).first()
    assert out.status == "approved" and out.sent_at is None  # never sent while gated
    db.close()


def test_double_approve_is_blocked():
    client.post("/whatsapp/webhook", json=_inbound(wamid="wamid.DBL"))
    d = next(x for x in client.get("/whatsapp/drafts").json() if x["customer_phone"] == "5216771234567")

    def outbound_count():
        db = TestingSession()
        try:
            return db.query(WAMessage).filter_by(
                conversation_id=d["conversation_id"], direction="outbound").count()
        finally:
            db.close()

    assert client.post(f"/whatsapp/drafts/{d['id']}/approve").status_code == 200
    after_first = outbound_count()
    assert client.post(f"/whatsapp/drafts/{d['id']}/approve").status_code == 409  # no double-send
    assert outbound_count() == after_first  # blocked approve created NO new outbound row


def test_signature_fails_closed_without_opt_in(monkeypatch):
    monkeypatch.setattr(config, "wa_allow_unsigned_webhook", False)
    monkeypatch.setattr(config, "wa_app_secret", None)
    r = client.post("/whatsapp/webhook", json=_inbound(wamid="wamid.NOSIG"))
    assert r.status_code == 403  # unsigned + no secret + no opt-in => rejected


def test_edit_then_approve_marks_human_drafted():
    client.post("/whatsapp/webhook", json=_inbound(wamid="wamid.EDIT"))
    d = next(x for x in client.get("/whatsapp/drafts").json() if x["customer_phone"] == "5216771234567" and x["status"] == "pending")
    client.put(f"/whatsapp/drafts/{d['id']}", json={"edited_text": "Le confirmo el precio en un momento."})
    resp = client.post(f"/whatsapp/drafts/{d['id']}/approve").json()
    assert resp["send_result"]["dry_run"] is True

    db = TestingSession()
    out = db.query(WAMessage).filter_by(direction="outbound", content="Le confirmo el precio en un momento.").one()
    assert out.drafted_by == "human"
    db.close()
