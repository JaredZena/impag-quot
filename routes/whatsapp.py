"""
WhatsApp sales-agent routes.

- GET  /whatsapp/webhook            Meta verification handshake (public).
- POST /whatsapp/webhook            Inbound messages (public, signature-verified).
- GET  /whatsapp/conversations      Queue: conversations with a pending draft (auth).
- GET  /whatsapp/conversations/{id}/messages   Thread (auth).
- GET  /whatsapp/drafts             Pending approval queue (auth).
- PUT  /whatsapp/drafts/{id}        Edit the draft text (auth).
- POST /whatsapp/drafts/{id}/approve   Approve → send (subject to the send gate) (auth).
- POST /whatsapp/drafts/{id}/reject     Reject (auth).

The webhook is intentionally NOT behind the Google-auth router dependency (Meta
calls it) — it is protected by the X-Hub-Signature-256 check instead.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import config
from auth import verify_google_token
from models import get_db, WAConversation, WAMessage, WADraft
from services.whatsapp_webhook import (
    verify_webhook, verify_signature, process_webhook_payload, MAX_WEBHOOK_BODY_BYTES,
)
from services.whatsapp_sender import send_text_message

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


# ── Webhook (public) ─────────────────────────────────────────────────────────

@router.get("/webhook")
async def webhook_verify(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    result = verify_webhook(hub_mode, hub_verify_token, hub_challenge)
    if result is None:
        raise HTTPException(status_code=403, detail="Verification failed")
    return PlainTextResponse(str(result))


@router.post("/webhook")
async def webhook_receive(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    if not verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        payload = {}
    # Always 200 quickly; Meta retries on non-200.
    summary = process_webhook_payload(payload, db)
    return {"status": "ok", **summary}


# ── Approval queue (authenticated) ───────────────────────────────────────────

def _draft_dict(d: WADraft, conv: WAConversation = None):
    return {
        "id": d.id,
        "conversation_id": d.conversation_id,
        "draft_text": d.draft_text,
        "edited_text": d.edited_text,
        "ai_context": d.ai_context,
        "status": d.status,
        "reviewed_by": d.reviewed_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "customer_phone": conv.customer_phone if conv else None,
        "customer_name": conv.customer_name if conv else None,
    }


@router.get("/drafts")
def list_pending_drafts(db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    rows = (db.query(WADraft, WAConversation)
            .join(WAConversation, WADraft.conversation_id == WAConversation.id)
            .filter(WADraft.status == "pending")
            .order_by(WADraft.created_at.asc()).all())
    return [_draft_dict(d, c) for d, c in rows]


@router.get("/conversations")
def list_conversations(db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    convs = (db.query(WAConversation)
             .filter(WAConversation.status == "active")
             .order_by(WAConversation.last_message_at.desc().nullslast()).all())
    pending = {cid for (cid,) in db.query(WADraft.conversation_id).filter(WADraft.status == "pending").all()}
    return [{
        "id": c.id, "customer_phone": c.customer_phone, "customer_name": c.customer_name,
        "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        "has_pending_draft": c.id in pending,
    } for c in convs]


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: int, db: Session = Depends(get_db),
                          user: dict = Depends(verify_google_token)):
    msgs = (db.query(WAMessage).filter(WAMessage.conversation_id == conversation_id)
            .order_by(WAMessage.created_at.asc()).all())
    return [{
        "id": m.id, "direction": m.direction, "content": m.content,
        "message_type": m.message_type, "status": m.status,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    } for m in msgs]


class DraftEdit(BaseModel):
    edited_text: str = Field(min_length=1, max_length=4096)


@router.put("/drafts/{draft_id}")
def edit_draft(draft_id: int, data: DraftEdit, db: Session = Depends(get_db),
               user: dict = Depends(verify_google_token)):
    draft = db.query(WADraft).filter(WADraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    # Only editable while awaiting review; allow re-editing an already-edited draft.
    if draft.status not in ("pending", "edited"):
        raise HTTPException(status_code=409, detail=f"Draft already {draft.status}")
    draft.edited_text = data.edited_text
    draft.status = "edited"
    db.commit()
    return _draft_dict(draft)


@router.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: int, db: Session = Depends(get_db),
                  user: dict = Depends(verify_google_token)):
    """Approve a draft and attempt to send it. Honors the global send gate:
    with sending disabled the reply is recorded but NOT delivered (dry run)."""
    # Lock the row so concurrent approvals can't both send. Only a fresh
    # (pending/edited) or previously-failed draft may be (re)approved — an
    # already sent/approved/rejected draft is terminal, preventing double-send
    # and duplicate outbound records.
    draft = db.query(WADraft).filter(WADraft.id == draft_id).with_for_update().first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status not in ("pending", "edited", "failed"):
        raise HTTPException(status_code=409, detail=f"Draft already {draft.status}")

    conv = db.query(WAConversation).filter(WAConversation.id == draft.conversation_id).first()
    # Capture before the intermediate commit expires these attributes.
    customer_phone = conv.customer_phone
    conversation_id = conv.id
    text_to_send = draft.edited_text or draft.draft_text
    was_human_edited = bool(draft.edited_text)
    now = datetime.now(timezone.utc)

    # Claim the draft before the external call so a blocked concurrent request
    # sees a non-approvable status ("sending") when it acquires the lock.
    draft.status = "sending"
    db.commit()

    result = send_text_message(customer_phone, text_to_send)
    sent = result.get("sent", False)

    msg = WAMessage(
        conversation_id=conversation_id, wa_message_id=result.get("wa_message_id"),
        direction="outbound", content=text_to_send, message_type="text",
        status="sent" if sent else ("approved" if result.get("dry_run") else "failed"),
        drafted_by="human" if was_human_edited else "ai",
        approved_by=user.get("email"), sent_at=now if sent else None,
    )
    db.add(msg)
    draft.status = "sent" if sent else ("approved" if result.get("dry_run") else "failed")
    draft.reviewed_by = user.get("email")
    draft.reviewed_at = now
    if sent:
        conv.last_message_at = now
    db.commit()

    return {"draft": _draft_dict(draft), "send_result": result,
            "gate_open": config.wa_sending_enabled}


@router.post("/drafts/{draft_id}/reject")
def reject_draft(draft_id: int, db: Session = Depends(get_db),
                 user: dict = Depends(verify_google_token)):
    draft = db.query(WADraft).filter(WADraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    # Can't reject something already sent or resolved.
    if draft.status in ("sent", "rejected", "sending"):
        raise HTTPException(status_code=409, detail=f"Draft already {draft.status}")
    draft.status = "rejected"
    draft.reviewed_by = user.get("email")
    draft.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return _draft_dict(draft)
