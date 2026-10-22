"""
"Cómo vamos": the panel at the top of /hoy, one call.

- GET /panel   (Google auth) for everyone on the team:
  - google: reviews (goal REVIEWS_GOAL), Business Profile last reported month
    vs the one before + 12-month history, Search Console clicks in the last
    28 days vs the 28 before (goal SEARCH_CLICKS_GOAL) + daily history.
    Filled by POST /jobs/google-metrics-sync (services/business_metrics.py).
  - ventas: this month so far vs the same days of last month (sale ledger).
  - tienda: todoparaelcampo.com.mx in the last 28 days vs the 28 before:
    visits and WhatsApp clicks (Umami rows, complete days only) and the web
    quotes / paid web orders the storefront recorded (created_by tienda-web).
  What clients owe and open quotes are already on /hoy (GET /hoy,
  /quotes/pipeline-summary), so they are not repeated here.
  - is_owner: owners also load /finance/dashboard (punto de equilibrio,
    estado de resultados). Everyone is an owner until OWNER_EMAILS is set.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import is_owner, verify_google_token
from models import BusinessMetric, Quote, Sale, get_db
from services.quote_capture import BUSINESS_TZ

router = APIRouter(prefix="/panel", tags=["panel"])

# Plan "Google: reseñas y perfil" (Obsidian, 2026-10-21): goals for 2026-11-30.
GOALS_DUE = "2026-11-30"
REVIEWS_GOAL = 15
SEARCH_CLICKS_GOAL = 130
WEB_CREATED_BY = "tienda-web"  # services/web_orders.py, services/web_quotes.py
PAID_STATUSES = ("approved", "mismatch")  # mismatch = approved, amount off
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


def _tienda(db: Session, today: date) -> dict:
    daily = defaultdict(dict)
    for r in db.query(BusinessMetric).filter(
        BusinessMetric.source == "umami",
        BusinessMetric.period == "day",
        BusinessMetric.period_start >= today - timedelta(days=120),
    ):
        daily[r.period_start][r.metric] = _num(r.value)
    # Today is still running: windows end on the last complete day.
    days = sorted(d for d in daily if d < today)
    last_day = days[-1] if days else None

    def window(metric: str, offset: int) -> float | None:
        if last_day is None:
            return None
        hi = last_day - timedelta(days=offset)
        lo = hi - timedelta(days=27)
        return sum(daily[d].get(metric, 0) for d in days if lo <= d <= hi)

    def web(lo_days: int, hi_days: int, paid: bool = False) -> tuple[int, float]:
        lo = datetime.combine(today - timedelta(days=lo_days), time(0), BUSINESS_TZ)
        hi = datetime.combine(today - timedelta(days=hi_days), time(0), BUSINESS_TZ)
        q = db.query(
            func.count(Quote.id), func.coalesce(func.sum(Quote.total), 0)
        ).filter(
            Quote.created_by == WEB_CREATED_BY,
            Quote.created_at >= lo,
            Quote.created_at < hi,
        )
        if paid:
            q = q.filter(Quote.payment_status.in_(PAID_STATUSES))
        count, total = q.one()
        return count, round(_num(total), 2)

    web_28, _ = web(27, -1)
    web_prev, _ = web(55, 27)
    paid_28, paid_total_28 = web(27, -1, paid=True)
    paid_all, paid_total_all = (
        db.query(func.count(Quote.id), func.coalesce(func.sum(Quote.total), 0))
        .filter(
            Quote.created_by == WEB_CREATED_BY,
            Quote.payment_status.in_(PAID_STATUSES),
        )
        .one()
    )
    return {
        "last_day": last_day.isoformat() if last_day else None,
        "visits_28d": window("visits", 0),
        "visits_prev_28d": window("visits", 28),
        "whatsapp_28d": window("whatsapp-click", 0),
        "whatsapp_prev_28d": window("whatsapp-click", 28),
        "series": [
            {"day": d.isoformat(), "visits": daily[d].get("visits", 0)}
            for d in days[-91:]
        ],
        "web_quotes_28d": web_28,
        "web_quotes_prev_28d": web_prev,
        "paid_orders_28d": paid_28,
        "paid_total_28d": paid_total_28,
        "paid_orders_all": paid_all,
        "paid_total_all": round(_num(paid_total_all), 2),
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
            "tienda": _tienda(db, today),
        },
    }
