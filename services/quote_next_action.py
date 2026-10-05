"""Siguiente paso of a quote: what the team does next, who does it and by when.

Shared by PUT /quotes/{id}/next-action (routes/quotes.py) and
scripts/load_next_actions.py so both write the same fields and the same audit
line in quote.notes:

    [Siguiente paso] 05/10/2026 hernan@...: Llamar para cerrar · Hernán · 07/10
    [Siguiente paso] 06/10/2026 hernan@...: hecho
"""

from datetime import date, datetime, timezone
from typing import Optional

from models import Quote
from services.quote_capture import BUSINESS_TZ, _append_note

MAX_ACTION = 200
MAX_OWNER = 50


def clean(text: Optional[str]) -> Optional[str]:
    """Trimmed text, or None for blank."""
    text = (text or "").strip()
    return text or None


def audit_line(
    who: str,
    next_action: Optional[str],
    owner: Optional[str],
    due: Optional[date],
    now: Optional[datetime] = None,
) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(BUSINESS_TZ)
    head = f"[Siguiente paso] {stamp:%d/%m/%Y} {who}"
    if next_action is None:
        return f"{head}: hecho"
    parts = [next_action, owner, f"{due:%d/%m}" if due else None]
    return f"{head}: {' · '.join(p for p in parts if p)}"


def set_next_action(
    quote: Quote,
    next_action: Optional[str],
    owner: Optional[str],
    due: Optional[date],
    *,
    who: str,
    now: Optional[datetime] = None,
) -> bool:
    """Set the quote's next step (already cleaned and validated) and log it in
    the notes. next_action None = "Hecho": owner and due are cleared too.
    Returns False, writing nothing, when the quote already has these values."""
    if next_action is None:
        owner = due = None
    current = (quote.next_action, quote.next_action_owner, quote.next_action_due)
    if current == (next_action, owner, due):
        return False
    quote.next_action = next_action
    quote.next_action_owner = owner
    quote.next_action_due = due
    _append_note(quote, audit_line(who, next_action, owner, due, now))
    return True
