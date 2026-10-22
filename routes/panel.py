"""
"Cómo vamos": the panel at the top of /hoy, one call.

- GET /panel   (Google auth) for everyone on the team:
  - google: reviews (goal REVIEWS_GOAL), Business Profile last reported month
    vs the one before + 12-month history, Search Console clicks in the last
    28 days vs the 28 before (goal SEARCH_CLICKS_GOAL) + daily history.
    Filled by POST /jobs/google-metrics-sync (services/business_metrics.py).
  - ventas: this month so far vs the same days of last month (sale ledger).
  What clients owe and open quotes are already on /hoy (GET /hoy,
  /quotes/pipeline-summary), so they are not repeated here.
  - is_owner: owners also load /finance/dashboard (punto de equilibrio,
    estado de resultados). Everyone is an owner until OWNER_EMAILS is set.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import is_owner, verify_google_token
from models import BusinessMetric, Sale, get_db
from services.quote_capture import BUSINESS_TZ

router = APIRouter(prefix="/panel", tags=["panel"])

# Plan "Google: reseñas y perfil" (Obsidian, 2026-10-21): goals for 2026-11-30.
GOALS_DUE = "2026-11-30"
REVIEWS_GOAL = 15
SEARCH_CLICKS_GOAL = 130
PROFILE_METRICS = (
    "profile_views",
    "calls",
    "directions",
    "website_clicks",
    "chat_clicks",
)


def _today() -> date:
    return datetime.now(BUSINESS_TZ).date()


def _num(value) -> float:
    return float(value or 0)


def _month_start(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def _google(db: Session, today: date) -> dict:
    reviews = (
        db.query(BusinessMetric)
        .filter_by(source="gbp_review")
        .order_by(BusinessMetric.period_start.desc())
        .all()
    )
    rated = [_num(r.value) for r in reviews if _num(r.value) > 0]

    monthly = defaultdict(dict)
    for r in db.query(BusinessMetric).filter_by(source="gbp", period="month"):
        monthly[r.period_start][r.metric] = _num(r.value)
    months = sorted(monthly)
    latest = months[-1] if months else None
    previous = months[-2] if len(months) > 1 else None

    since = today - timedelta(days=120)
    daily = defaultdict(dict)
    for r in db.query(BusinessMetric).filter(
        BusinessMetric.source == "gsc",
        BusinessMetric.period == "day",
        BusinessMetric.period_start >= since,
    ):
        daily[r.period_start][r.metric] = _num(r.value)
    days = sorted(daily)
    # Search Console runs ~2-3 days behind: window ends at its last day.
    last_day = days[-1] if days else None

    def window(end: date | None, offset: int, metric: str) -> float | None:
        if end is None:
            return None
        hi = end - timedelta(days=offset)
        lo = hi - timedelta(days=27)
        return sum(daily[d].get(metric, 0) for d in days if lo <= d <= hi)

    updated = db.query(func.max(BusinessMetric.fetched_at)).scalar()
    return {
        "updated_at": updated.isoformat() if updated else None,
        "goals_due": GOALS_DUE,
        "reviews": {
            "count": len(reviews),
            "goal": REVIEWS_GOAL,
            "avg_stars": round(sum(rated) / len(rated), 1) if rated else None,
            "latest": [
                {
                    "author": r.label,
                    "stars": int(_num(r.value)),
                    "date": r.period_start.isoformat(),
                }
                for r in reviews[:3]
            ],
        },
        "profile": {
            "month": latest.isoformat() if latest else None,
            "values": monthly.get(latest, {}) if latest else {},
            "previous": monthly.get(previous, {}) if previous else {},
            "series": [
                {
                    "month": m.isoformat(),
                    **{k: monthly[m].get(k, 0) for k in PROFILE_METRICS},
                }
                for m in months[-12:]
            ],
        },
        "search": {
            "last_day": last_day.isoformat() if last_day else None,
            "clicks_28d": window(last_day, 0, "clicks"),
            "clicks_prev_28d": window(last_day, 28, "clicks"),
            "impressions_28d": window(last_day, 0, "impressions"),
            "goal": SEARCH_CLICKS_GOAL,
            "series": [
                {"day": d.isoformat(), "clicks": daily[d].get("clicks", 0)}
                for d in days[-90:]
            ],
        },
    }


def _ventas(db: Session, today: date) -> dict:
    start = _month_start(today)
    prev_start = _month_start(today, 1)
    # Same days of last month (Oct 1-22 vs Sep 1-22); clamp to its length.
    prev_end = min(
        prev_start + timedelta(days=today.day - 1), start - timedelta(days=1)
    )

    def total(lo: date, hi: date) -> tuple[float, int]:
        amount, count = (
            db.query(func.coalesce(func.sum(Sale.amount), 0), func.count(Sale.id))
            .filter(
                Sale.quarantined.is_(False),
                Sale.sale_date >= lo,
                Sale.sale_date <= hi,
            )
            .one()
        )
        return round(_num(amount), 2), count

    month_total, month_count = total(start, today)
    prev_total, prev_count = total(prev_start, prev_end)
    return {
        "month": start.isoformat(),
        "total": month_total,
        "count": month_count,
        "prev_same_days_total": prev_total,
        "prev_same_days_count": prev_count,
    }


@router.get("")
def panel(db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    today = _today()
    return {
        "success": True,
        "data": {
            "today": today.isoformat(),
            "is_owner": is_owner(user),
            "google": _google(db, today),
            "ventas": _ventas(db, today),
        },
    }
