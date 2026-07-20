"""
Stalled-quote follow-up sweep (roadmap P2, Week 2).

Finds trackable quotes that were delivered to a customer but went cold, and for
each one:
  * creates a Task (category "Seguimiento a cotizaciones") routed to the quote's
    engineer, with a ready-to-send drafted nudge in the body — ALWAYS; this is the
    universal, channel-agnostic artifact.
  * additionally drops a PROACTIVE WhatsApp draft into the human approval queue
    IFF that customer already has a WhatsApp conversation (so it's a real, sendable
    thread). Sending itself stays behind the existing WA_SENDING_ENABLED gate.

Idempotency lives on the Quote row: last_followup_at (don't re-nudge within the
reminder interval) + followup_count (never exceed MAX_FOLLOWUPS). No inbound
message is required — drafts are created with trigger_message_id=NULL.

Side-effect free until dry_run=False. Callable from a CLI script or an HTTP job
endpoint; the trigger (local cron / EventBridge / GitHub Actions) is external.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import (
    Quote, Task, TaskCategory, TaskUser, WAConversation, WADraft,
    get_next_task_number,
)
from services.whatsapp_drafter import draft_quote_followup

# Tunables (env-overridable). Sensible defaults for a solo shop.
STALE_DAYS = int(os.getenv("FOLLOWUP_STALE_DAYS", "3"))            # nudge N days after send
REMINDER_INTERVAL_DAYS = int(os.getenv("FOLLOWUP_INTERVAL_DAYS", "4"))  # gap between nudges
MAX_FOLLOWUPS = int(os.getenv("FOLLOWUP_MAX", "3"))               # hard cap per quote
DEAD_AFTER_DAYS = int(os.getenv("FOLLOWUP_DEAD_DAYS", "45"))      # stop; the lead is cold
FOLLOWUP_CATEGORY_NAME = "Seguimiento a cotizaciones"
# Fallback Task.created_by (NOT NULL FK task_user.id) when the quote's engineer
# email doesn't map to a task_user. 2 = Jared in this DB.
SYSTEM_TASK_USER_ID = int(os.getenv("FOLLOWUP_SYSTEM_USER_ID", "2"))


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _is_expired(quote: Quote, now: datetime) -> bool:
    """A quote is past its validity window (sent_at + validity_days). The DB status
    only lazily flips to 'expired' on a public re-open, so we recompute it here."""
    if not quote.sent_at:
        return False
    return quote.sent_at + timedelta(days=quote.validity_days or 15) < now


def find_stale_quotes(db: Session, now: Optional[datetime] = None) -> List[Quote]:
    """Delivered-but-unresolved quotes due for a nudge.

    Deliberately INCLUDES quotes past their per-quote validity window (up to the
    flat DEAD_AFTER_DAYS cutoff): an expired quote is a strong re-engagement lead.
    The drafter is told when a quote is expired so the message offers to refresh the
    pricing instead of pretending it's still valid (see _is_expired / sweep)."""
    now = _now(now)
    stale_before = now - timedelta(days=STALE_DAYS)
    reminder_before = now - timedelta(days=REMINDER_INTERVAL_DAYS)
    dead_before = now - timedelta(days=DEAD_AFTER_DAYS)

    return (
        db.query(Quote)
        .filter(
            Quote.status.in_(("sent", "viewed")),   # delivered, not accepted/expired/draft
            Quote.sent_at.isnot(None),
            Quote.accepted_at.is_(None),
            Quote.sent_at <= stale_before,           # old enough to nudge
            Quote.sent_at >= dead_before,            # but not a dead lead
            Quote.followup_count < MAX_FOLLOWUPS,     # under the anti-spam cap
            or_(Quote.last_followup_at.is_(None),
                Quote.last_followup_at <= reminder_before),  # not nudged too recently
        )
        .order_by(Quote.sent_at.asc())
        .all()
    )


def _resolve_system_user_id(db: Session) -> int:
    """The NOT-NULL task_user that owns auto-created follow-up tasks. Validate the
    configured id exists; else fall back to any active user; else fail loudly BEFORE
    the loop rather than FK-500 on every Task insert."""
    if db.query(TaskUser.id).filter(TaskUser.id == SYSTEM_TASK_USER_ID).first():
        return SYSTEM_TASK_USER_ID
    u = (db.query(TaskUser).filter(TaskUser.is_active.is_(True))
         .order_by(TaskUser.id).first())
    if u:
        return u.id
    raise RuntimeError("No task_user exists to own follow-up tasks; create one first.")


def _resolve_task_users(db: Session, quote: Quote, system_user_id: int) -> Dict[str, int]:
    """Map the quote's engineer email -> task_user.id for assignment; fall back to
    the validated system user for the NOT-NULL created_by."""
    engineer_email = (quote.assigned_to or quote.created_by or "").strip().lower()
    assignee = None
    if engineer_email:
        # Exact, case-insensitive match — NOT ilike(), whose _/% would be treated
        # as wildcards on emails like "juan_perez@impag.com".
        u = (db.query(TaskUser)
             .filter(func.lower(TaskUser.email) == engineer_email,
                     TaskUser.is_active.is_(True))
             .first())
        if u:
            assignee = u.id
    return {"created_by": system_user_id, "assigned_to": assignee or system_user_id}


def _category_id(db: Session) -> Optional[int]:
    cat = db.query(TaskCategory).filter(TaskCategory.name == FOLLOWUP_CATEGORY_NAME).first()
    return cat.id if cat else None


def sweep_stale_quotes(db: Session, dry_run: bool = True,
                       now: Optional[datetime] = None) -> Dict:
    """Create follow-up Tasks (+ optional WA drafts) for stale quotes.

    dry_run=True lists candidates WITHOUT drafting (no LLM cost) or writing.
    """
    now = _now(now)
    candidates = find_stale_quotes(db, now=now)

    summary = {"dry_run": dry_run, "now": now.isoformat(),
               "candidates": len(candidates), "tasks_created": 0,
               "wa_drafts_created": 0, "errors": 0, "items": []}

    if not candidates:
        return summary

    # Validate the system task-user up front so a bad id fails once, loudly, rather
    # than FK-500'ing on every insert. Only needed when we actually write.
    system_user_id = None if dry_run else _resolve_system_user_id(db)
    category_id = _category_id(db)
    # phone -> WAConversation lookup, for the optional WA draft. WAConversation
    # stores the raw WhatsApp wa_id (e.g. "5215951123625", no '+'), so normalize
    # BOTH sides to the same E.164 key or the match silently misses.
    from scripts.backfill_customers import normalize_phone
    conv_by_phone = {}
    for c in db.query(WAConversation).all():
        k = normalize_phone(c.customer_phone) if c.customer_phone else None
        if k:
            conv_by_phone.setdefault(k, c)

    for q in candidates:
        days_since_sent = max(0, (now - q.sent_at).days)
        was_viewed = q.status == "viewed" or q.viewed_at is not None
        expired = _is_expired(q, now)
        norm_phone = normalize_phone(q.customer_phone) if q.customer_phone else None
        conv = conv_by_phone.get(norm_phone) if norm_phone else None

        item = {"quote_id": q.id, "quote_number": q.quote_number,
                "customer": q.customer_name, "days_since_sent": days_since_sent,
                "viewed": was_viewed, "expired": expired,
                "followup_count": q.followup_count,
                "will_create_wa_draft": conv is not None}

        if dry_run:
            summary["items"].append(item)
            continue

        # Isolate each quote in a SAVEPOINT: one bad row (drafter, FK, …) rolls back
        # only itself and the sweep carries on, instead of aborting the whole batch.
        try:
            created_wa = False
            with db.begin_nested():
                total = float(q.total) if q.total is not None else None
                nudge = draft_quote_followup(
                    quote_number=q.quote_number, total=total,
                    customer_name=q.customer_name, days_since_sent=days_since_sent,
                    was_viewed=was_viewed, is_expired=expired,
                )["draft_text"]

                # 1) Task — always.
                users = _resolve_task_users(db, q, system_user_id)
                estado = ("vencida (pasó su vigencia)" if expired else
                          "revisada por el cliente" if was_viewed else
                          "enviada, sin confirmación de apertura")
                monto = f"${total:,.2f}" if total is not None else "(monto no disponible)"
                desc = (
                    f"La cotización {q.quote_number} ({estado}) lleva {days_since_sent} días "
                    f"sin respuesta.\n"
                    f"Cliente: {q.customer_name} · Tel: {q.customer_phone or '—'} · Monto: {monto}\n"
                    f"Seguimiento #{q.followup_count + 1}.\n\n"
                    f"Mensaje sugerido (revísalo/edítalo antes de enviar):\n{nudge}"
                )
                db.add(Task(
                    title=f"Seguimiento: {q.quote_number} — {q.customer_name}"[:300],
                    description=desc,
                    status="pending",
                    priority="high" if (was_viewed or expired) else "medium",
                    due_date=now.date(),
                    category_id=category_id,
                    created_by=users["created_by"],
                    assigned_to=users["assigned_to"],
                    task_number=get_next_task_number(db),
                ))
                # Flush so the NEXT get_next_task_number() sees this number as taken
                # (else every task claims the same lowest-free number and collides).
                db.flush()

                # 2) WhatsApp draft — only when a real thread exists AND there isn't
                # already a pending proactive draft on it (don't stack the queue).
                if conv is not None:
                    dup = (db.query(WADraft.id)
                           .filter(WADraft.conversation_id == conv.id,
                                   WADraft.status == "pending").first())
                    if not dup:
                        estado_wa = "vencida" if expired else q.status
                        db.add(WADraft(
                            conversation_id=conv.id,
                            trigger_message_id=None,
                            draft_text=nudge,
                            status="pending",
                            ai_context=(f"Seguimiento automático: cotización {q.quote_number} "
                                        f"estancada {days_since_sent} días (estado: {estado_wa})."),
                        ))
                        created_wa = True

                # 3) Mark nudged (idempotency).
                q.last_followup_at = now
                q.followup_count = (q.followup_count or 0) + 1

            # savepoint committed cleanly
            summary["tasks_created"] += 1
            if created_wa:
                summary["wa_drafts_created"] += 1
            item["will_create_wa_draft"] = created_wa
            summary["items"].append(item)
        except Exception as e:
            print(f"followup sweep: quote {q.quote_number} failed, skipped: {e}")
            summary["errors"] += 1
            continue

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return summary
