"""
"Hoy": the day in one call, for the admin's home screen and the HOY report
Hernán posts at ~18:00 (*HOY dd/mm/yyyy*: Logrado Hoy / Ventas / Bloqueantes /
Prioritario Mañana).

- GET /hoy?day=YYYY-MM-DD   (Google auth) everything the report counts that
  the app knows: sales registered that day, quotes sent, new requests,
  follow-ups recorded on quotes, pendientes closed, what clients still owe,
  and the open pendientes flagged urgent/high for tomorrow. Numbers the app
  does not have (e.g. "Atención al cliente") are left to the person.
"""

import re
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import Quote, Sale, Task, get_db
from services import pendientes
from services.quote_capture import BUSINESS_TZ

router = APIRouter(prefix="/hoy", tags=["hoy"])

# Quote-note lines that mean someone followed up that day
# (services/quote_capture.py and routes/quotes.change_quote_status).
FOLLOWUP_TAGS = ("Estado", "Reenvío", "Actualización", "Contraoferta", "Total")


def _material(notes: str | None) -> str | None:
    m = re.search(r"^Material/Proyecto:\s*(.+)$", notes or "", re.M)
    value = m.group(1).strip() if m else None
    return None if value in (None, "", "—") else value


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=BUSINESS_TZ)
    return start, start + timedelta(days=1)


@router.get("")
def hoy(
    day: date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    day = day or datetime.now(BUSINESS_TZ).date()
    start, end = _day_bounds(day)
    stamp = day.strftime("%d/%m/%Y")

    sales = (
        db.query(Sale)
        .filter(Sale.sale_date == day, Sale.quarantined.is_(False))
        .order_by(Sale.id)
        .all()
    )
    sent = (
        db.query(Quote)
        .filter(Quote.sent_at >= start, Quote.sent_at < end, Quote.status != "draft")
        .order_by(Quote.sent_at)
        .all()
    )
    requested = (
        db.query(Quote)
        .filter(
            Quote.status == "requested",
            Quote.created_at >= start,
            Quote.created_at < end,
        )
        .all()
    )
    followup_re = re.compile(
        rf"^\[(?:{'|'.join(FOLLOWUP_TAGS)})\] {re.escape(stamp)} (.+)$", re.M
    )
    followups = []
    for q in db.query(Quote).filter(Quote.notes.like(f"%] {stamp} %")):
        for line in followup_re.findall(q.notes or ""):
            followups.append(
                {
                    "quote_id": q.id,
                    "quote_number": q.quote_number,
                    "customer_name": q.customer_name,
                    "material": _material(q.notes),
                    "detail": re.sub(r"\s*\([^()]*@[^()]*\)\s*$", "", line).strip(),
                }
            )
    closed = (
        db.query(Task)
        .filter(Task.completed_at >= start, Task.completed_at < end)
        .order_by(Task.completed_at)
        .all()
    )
    receivable = (
        db.query(Sale)
        .filter(Sale.quarantined.is_(False), Sale.pending_amount > 0)
        .order_by(Sale.sale_date)
        .all()
    )
    board = pendientes.board(db)
    priority = [
        t.title
        for tasks in board.values()
        for t in tasks
        if t.priority in ("urgent", "high")
    ]

    return {
        "success": True,
        "data": {
            "day": day.isoformat(),
            "sales": [
                {
                    "id": s.id,
                    "reference": s.reference or s.folio,
                    "customer_name": s.customer_name,
                    "description": s.description,
                    "amount": float(s.amount or 0),
                    "pending": float(s.pending_amount or 0),
                    "source": s.sheet_tab,
                }
                for s in sales
            ],
            "quotes_sent": [
                {
                    "id": q.id,
                    "quote_number": q.quote_number,
                    "customer_name": q.customer_name,
                    "material": _material(q.notes),
                    "total": float(q.total or 0),
                }
                for q in sent
            ],
            "requests": [
                {
                    "id": q.id,
                    "quote_number": q.quote_number,
                    "customer_name": q.customer_name,
                    "material": _material(q.notes),
                }
                for q in requested
            ],
            "followups": followups,
            "closed_tasks": [{"id": t.id, "title": t.title} for t in closed],
            "receivable": {
                "total": float(sum(s.pending_amount for s in receivable)),
                "rows": [
                    {
                        "customer_name": s.customer_name,
                        "pending": float(s.pending_amount),
                        "reference": s.reference or s.folio,
                    }
                    for s in receivable
                ],
            },
            "open_by_section": {
                pendientes.CATEGORY_NAME[k]: len(v) for k, v in board.items()
            },
            "priority": priority,
        },
    }
