"""
WhatsApp Cloud API webhook processing.

- verify_webhook(): Meta's GET handshake.
- verify_signature(): validate the X-Hub-Signature-256 header (when WA_APP_SECRET set).
- process_webhook_payload(): parse an inbound payload, upsert the conversation,
  store the message (deduped by WhatsApp's message id), and generate a PENDING
  AI draft for the human approval queue. It never sends anything — sending only
  happens later on operator approval, and only when the send gate is open.
"""
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError

import config
from models import WAConversation, WAMessage, WADraft

# Defense-in-depth caps on a single webhook delivery. Real Meta batches are
# tiny; anything larger is abuse. Enforced regardless of signature validity.
MAX_MESSAGES_PER_REQUEST = 20
MAX_WEBHOOK_BODY_BYTES = 128 * 1024

# WhatsApp message types we turn into a text placeholder for drafting/context.
_MEDIA_PLACEHOLDER = {
    "image": "[imagen]",
    "document": "[documento]",
    "audio": "[nota de voz]",
    "video": "[video]",
    "sticker": "[sticker]",
    "location": "[ubicación]",
}


def verify_webhook(mode: Optional[str], token: Optional[str], challenge: Optional[str]):
    """Meta webhook verification handshake. Echoes the challenge string verbatim
    on success (Meta requires byte-for-byte fidelity — do not coerce to int)."""
    if mode == "subscribe" and token and config.wa_verify_token and token == config.wa_verify_token:
        return challenge
    return None


def verify_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Validate X-Hub-Signature-256. FAILS CLOSED: with no app secret configured
    we cannot verify, so we reject — unless WA_ALLOW_UNSIGNED_WEBHOOK is explicitly
    set for local sandbox testing. This prevents a single missing env var in
    production from silently turning the webhook into an open write endpoint."""
    if not config.wa_app_secret:
        return config.wa_allow_unsigned_webhook
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(config.wa_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.split("=", 1)[1])


def _extract_content(msg: Dict) -> (str, Optional[str]):
    mtype = msg.get("type", "text")
    if mtype == "text":
        return (msg.get("text", {}).get("body", "") or "").strip(), None
    if mtype == "interactive":
        inter = msg.get("interactive", {})
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return (reply.get("title") or "[respuesta interactiva]"), None
    if mtype in ("image", "document", "video", "audio", "sticker"):
        media = msg.get(mtype, {})
        caption = media.get("caption")
        placeholder = _MEDIA_PLACEHOLDER.get(mtype, f"[{mtype}]")
        content = f"{placeholder} {caption}".strip() if caption else placeholder
        return content, media.get("id")
    return _MEDIA_PLACEHOLDER.get(mtype, f"[{mtype}]"), None


def _upsert_conversation(db, phone: str, profile_name: Optional[str]) -> WAConversation:
    conv = db.query(WAConversation).filter(WAConversation.customer_phone == phone).first()
    if conv is None:
        # Conflict-safe insert: a concurrent first-contact delivery could race us
        # to create the same phone. Use a savepoint so a unique violation doesn't
        # poison the outer transaction — refetch the row the other tx created.
        try:
            with db.begin_nested():
                conv = WAConversation(customer_phone=phone, customer_name=profile_name, status="active")
                db.add(conv)
                db.flush()
        except IntegrityError:
            conv = db.query(WAConversation).filter(WAConversation.customer_phone == phone).one()
    if profile_name and not conv.customer_name:
        conv.customer_name = profile_name
    return conv


def _conversation_history(db, conversation_id: int, exclude_message_id: Optional[int] = None,
                          limit: int = 12) -> List[Dict]:
    q = db.query(WAMessage).filter(WAMessage.conversation_id == conversation_id)
    if exclude_message_id is not None:
        q = q.filter(WAMessage.id != exclude_message_id)  # exclude the trigger by identity
    rows = q.order_by(WAMessage.created_at.desc(), WAMessage.id.desc()).limit(limit).all()
    return [{"direction": m.direction, "content": m.content} for m in reversed(rows)]


def _generate_draft(db, conv: WAConversation, trigger: WAMessage):
    """Generate a pending AI draft for an inbound message. Isolated so a drafting
    failure never breaks webhook ingestion."""
    try:
        from services.whatsapp_drafter import draft_whatsapp_reply
        history = _conversation_history(db, conv.id, exclude_message_id=trigger.id)
        result = draft_whatsapp_reply(trigger.content, history, conv.customer_name)
        draft = WADraft(
            conversation_id=conv.id,
            trigger_message_id=trigger.id,
            draft_text=result.get("draft_text", ""),
            ai_context=(result.get("product_context") or "")[:4000],
            status="pending",
        )
        db.add(draft)
        db.commit()
        return draft
    except Exception as e:
        db.rollback()
        print(f"WhatsApp webhook: draft generation failed for conv {conv.id}: {e}")
        return None


def process_webhook_payload(payload: Dict, db) -> Dict:
    """Process an inbound Meta webhook. Returns a small summary; swallows per-item
    errors so the endpoint can always 200 fast (Meta retries on non-200)."""
    ingested, drafted, skipped, processed = 0, 0, 0, 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = {c.get("wa_id"): c for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                # Hard cap per delivery — bounds AI/DB fan-out even with a valid
                # signature. Real Meta batches are far below this.
                if processed >= MAX_MESSAGES_PER_REQUEST:
                    skipped += 1
                    continue
                processed += 1
                try:
                    wamid = msg.get("id")
                    phone = msg.get("from")
                    if not phone:
                        skipped += 1
                        continue
                    # Dedup: Meta re-delivers on our slow/failed responses.
                    if wamid and db.query(WAMessage.id).filter(WAMessage.wa_message_id == wamid).first():
                        skipped += 1
                        continue
                    profile = contacts.get(phone, {}).get("profile", {}).get("name")
                    content, media_id = _extract_content(msg)
                    conv = _upsert_conversation(db, phone, profile)
                    m = WAMessage(
                        conversation_id=conv.id, wa_message_id=wamid, direction="inbound",
                        content=content, message_type=msg.get("type", "text"),
                        media_url=media_id, status="received",
                    )
                    db.add(m)
                    conv.last_message_at = datetime.now(timezone.utc)
                    db.commit()
                    ingested += 1
                    if _generate_draft(db, conv, m):
                        drafted += 1
                except Exception as e:
                    db.rollback()
                    print(f"WhatsApp webhook: failed to process a message: {e}")
                    skipped += 1
            # 'statuses' events (delivered/read/sent) are ignored for now.
    return {"ingested": ingested, "drafted": drafted, "skipped": skipped}
