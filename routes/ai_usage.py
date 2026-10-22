"""
AI usage and cost of the storefront AI tools, for the admin's "Uso de IA"
dashboard (impag-admin-app src/components/campo/AIUsagePage.tsx).

GET /ai-usage/summary?days=30   totals, per feature, per model, per day
GET /ai-usage/events?limit=100  the latest requests (feature= filters by prefix)

Costs are list-price estimates in USD (services/ai_usage.PRICES); the summary
also carries today's USD→MXN rate when the exchange service has one.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import get_db
from services import ai_usage
from services.exchange_rate_service import exchange_rate_service

router = APIRouter(prefix="/ai-usage", tags=["ai-usage"])

FEATURE_LABELS = {
    "agroproyectos.plan": "Agroproyectos · plan",
    "agroproyectos.productos": "Agroproyectos · productos",
    "agroproyectos.riego": "Agroproyectos · diseño de riego",
    "agroproyectos.chat": "Agroproyectos · preguntas",
    "apoyos.discovery": "Apoyos · búsqueda",
    "apoyos.recheck": "Apoyos · verificación",
    "solar.read_bill": "Cotizador solar · lectura de recibo",
    "solar.advice": "Cotizador solar · explicación",
}


def _usd_mxn() -> float | None:
    try:
        rate = exchange_rate_service.get_exchange_rate("USD", "MXN")
    except Exception:
        return None
    return float(rate) if rate else None


@router.get("/summary")
def get_summary(
    days: int = Query(default=30, ge=1, le=366),
    db: Session = Depends(get_db),
    user=Depends(verify_google_token),
):
    data = ai_usage.summary(db, days)
    for row in data["by_feature"]:
        row["label"] = FEATURE_LABELS.get(row["feature"], row["feature"])
    data["usd_mxn"] = _usd_mxn()
    data["model_labels"] = ai_usage.MODEL_LABELS
    return {"success": True, "data": data}


@router.get("/events")
def get_events(
    limit: int = Query(default=100, ge=1, le=500),
    feature: str | None = Query(default=None, max_length=60),
    db: Session = Depends(get_db),
    user=Depends(verify_google_token),
):
    rows = ai_usage.recent(db, limit=limit, feature=feature)
    for row in rows:
        row["label"] = FEATURE_LABELS.get(row["feature"], row["feature"])
    return {"success": True, "data": rows}
