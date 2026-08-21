"""
Sales ledger routes (mirror of the VENTAS Google Sheet).

- POST /sales/sync   pull all VENTAS tabs into the `sale` table. Auth: Google
                     bearer token OR X-API-Key == STOREFRONT_API_KEY (the
                     daily GitHub Action calls it machine-to-machine).
- GET  /sales/stats  dashboard aggregates (Google auth)
- GET  /sales        filtered row listing (Google auth)

The ledger is an operational snapshot — NOT accounting books.
"""

import os
import secrets
from datetime import date

import requests

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import extract, func, or_
from sqlalchemy.orm import Session

from auth import DISABLE_AUTH, verify_google_token
from models import Sale, SaleBalance, get_db
from services.balance_sync import sync_balances
from services.sales_sync import sync_all

# NOTE: like routes/storefront.py, this router must NOT apply
# verify_google_token at router level — POST /sync below is also called by a
# GitHub Action authenticated with X-API-Key, not by a person.
router = APIRouter(prefix="/sales", tags=["sales"])

STATS_LABEL = "instantánea operativa — no libros contables"


def _api_key_matches(x_api_key: str | None) -> bool:
    expected_key = os.getenv("STOREFRONT_API_KEY")
    if not expected_key or not x_api_key:
        return False
    # Compare as bytes: str compare_digest raises TypeError on non-ASCII input,
    # which would turn a garbage header into a 500 instead of a 401.
    return secrets.compare_digest(
        x_api_key.encode("utf-8", "replace"), expected_key.encode("utf-8")
    )


def verify_sync_auth(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> dict:
    """EITHER a valid X-API-Key (GitHub Action) OR a Google bearer token."""
    if _api_key_matches(x_api_key):
        return {"email": "github-action@sales-sync", "auth": "api-key"}
    if authorization and authorization.lower().startswith("bearer "):
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=authorization[7:]
        )
        return verify_google_token(credentials)
    if DISABLE_AUTH:
        return verify_google_token(None)
    raise HTTPException(
        status_code=401,
        detail="Provide a Google bearer token or a valid X-API-Key",
    )


@router.post("/sync")
def sync_sales(
    user: dict = Depends(verify_sync_auth),
    db: Session = Depends(get_db),
):
    """Fetch all VENTAS tabs from Google Sheets and upsert into the ledger,
    then refresh the per-sale margin table from the BALANCES DE VENTA sheet
    (matching folios against the just-synced ledger)."""
    try:
        summary = sync_all(db)
    except (RuntimeError, requests.RequestException) as e:
        # Config/network problems (missing env, OAuth failure, Sheets error)
        raise HTTPException(status_code=502, detail=str(e))

    # Margins are best-effort: a balances failure must not fail the ledger
    # sync the GitHub Action depends on (sync_all already committed).
    balances: dict
    if os.getenv("BALANCES_SPREADSHEET_ID"):
        try:
            balances = {"success": True, **sync_balances(db)}
        except (RuntimeError, requests.RequestException) as e:
            db.rollback()
            balances = {"success": False, "error": str(e)[:300]}
    else:
        balances = {"success": False, "error": "BALANCES_SPREADSHEET_ID not set"}
    return {"success": True, **summary, "balances": balances}


def _row_to_dict(s: Sale) -> dict:
    return {
        "id": s.id,
        "sheet_tab": s.sheet_tab,
        "source_row": s.source_row,
        "sale_date": s.sale_date.isoformat() if s.sale_date else None,
        "month_label": s.month_label,
        "customer_name": s.customer_name,
        "customer_id": s.customer_id,
        "description": s.description,
        "unit": s.unit,
        "quantity": float(s.quantity) if s.quantity is not None else None,
        "unit_price": float(s.unit_price) if s.unit_price is not None else None,
        "amount": float(s.amount) if s.amount is not None else None,
        "concept": s.concept,
        "payment_method": s.payment_method,
        "delivery_place": s.delivery_place,
        "reference": s.reference,
        "folio": s.folio,
        "delivery_status": s.delivery_status,
        "requires_invoice": s.requires_invoice,
        "registered": s.registered,
        "quarantined": s.quarantined,
        "quarantine_reason": s.quarantine_reason,
        "imported_at": s.imported_at.isoformat() if s.imported_at else None,
    }


def _escape_like(text: str) -> str:
    """Escape LIKE/ILIKE metacharacters (backslash default escape char)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/stats")
def sales_stats(
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Dashboard aggregates. Quarantined rows are excluded from every
    aggregate except the quarantined count itself."""
    clean = db.query(Sale).filter(Sale.quarantined.is_(False))

    year_expr = extract("year", Sale.sale_date)
    month_expr = extract("month", Sale.sale_date)

    monthly_rows = (
        db.query(
            year_expr.label("year"),
            month_expr.label("month"),
            func.sum(Sale.amount).label("total"),
            func.count(Sale.id).label("count"),
        )
        .filter(Sale.quarantined.is_(False), Sale.sale_date.isnot(None))
        .group_by(year_expr, month_expr)
        .order_by(year_expr, month_expr)
        .all()
    )
    monthly = [
        {
            "year": int(r.year),
            "month": int(r.month),
            "total": float(r.total or 0),
            "count": r.count,
        }
        for r in monthly_rows
    ]

    payment_rows = (
        db.query(
            Sale.payment_method,
            func.sum(Sale.amount).label("total"),
            func.count(Sale.id).label("count"),
        )
        .filter(Sale.quarantined.is_(False))
        .group_by(Sale.payment_method)
        .all()
    )
    by_payment_method = {
        (r.payment_method or "desconocido"): {
            "total": float(r.total or 0),
            "count": r.count,
        }
        for r in payment_rows
    }

    concept_rows = (
        db.query(
            Sale.concept,
            func.sum(Sale.amount).label("total"),
            func.count(Sale.id).label("count"),
        )
        .filter(Sale.quarantined.is_(False), Sale.concept.isnot(None))
        .group_by(Sale.concept)
        .order_by(func.sum(Sale.amount).desc())
        .limit(10)
        .all()
    )
    by_concept = [
        {"concept": r.concept, "total": float(r.total or 0), "count": r.count}
        for r in concept_rows
    ]

    customer_rows = (
        db.query(
            Sale.customer_name,
            func.sum(Sale.amount).label("total"),
            func.count(Sale.id).label("count"),
        )
        .filter(Sale.quarantined.is_(False), Sale.customer_name.isnot(None))
        .group_by(Sale.customer_name)
        .order_by(func.sum(Sale.amount).desc())
        .limit(10)
        .all()
    )
    top_customers = [
        {
            "customer_name": r.customer_name,
            "total": float(r.total or 0),
            "count": r.count,
        }
        for r in customer_rows
    ]

    pending_count, pending_total = (
        db.query(func.count(Sale.id), func.coalesce(func.sum(Sale.amount), 0))
        .filter(Sale.quarantined.is_(False), Sale.delivery_status == "pendiente")
        .one()
    )

    invoice_count, invoice_total = (
        db.query(func.count(Sale.id), func.coalesce(func.sum(Sale.amount), 0))
        .filter(
            Sale.quarantined.is_(False),
            Sale.requires_invoice.is_(True),
            Sale.registered.is_(False),
        )
        .one()
    )

    quarantined_count = (
        db.query(func.count(Sale.id)).filter(Sale.quarantined.is_(True)).scalar()
    )

    grand_total = (
        db.query(func.coalesce(func.sum(Sale.amount), 0))
        .filter(Sale.quarantined.is_(False))
        .scalar()
    )

    current_year = date.today().year
    ytd_total = (
        db.query(func.coalesce(func.sum(Sale.amount), 0))
        .filter(
            Sale.quarantined.is_(False),
            Sale.sale_date.isnot(None),
            extract("year", Sale.sale_date) == current_year,
        )
        .scalar()
    )

    # ── Margins (from BALANCES DE VENTA, reconciled tabs only) ──
    reconciled = db.query(SaleBalance).filter(SaleBalance.match_status == "reconciled")
    recon_count, recon_revenue, recon_cost = (
        db.query(
            func.count(SaleBalance.id),
            func.coalesce(func.sum(SaleBalance.ledger_revenue), 0),
            func.coalesce(func.sum(SaleBalance.cost_total), 0),
        )
        .filter(SaleBalance.match_status == "reconciled")
        .one()
    )
    recon_revenue = float(recon_revenue or 0)
    recon_cost = float(recon_cost or 0)
    margin_total = recon_revenue - recon_cost

    margin_year_rows = (
        db.query(
            extract("year", SaleBalance.folio_month).label("year"),
            func.sum(SaleBalance.ledger_revenue).label("revenue"),
            func.sum(SaleBalance.cost_total).label("cost"),
            func.count(SaleBalance.id).label("count"),
        )
        .filter(
            SaleBalance.match_status == "reconciled",
            SaleBalance.folio_month.isnot(None),
        )
        .group_by(extract("year", SaleBalance.folio_month))
        .order_by(extract("year", SaleBalance.folio_month))
        .all()
    )
    margin_by_year = []
    for r in margin_year_rows:
        revenue = float(r.revenue or 0)
        cost = float(r.cost or 0)
        margin_by_year.append(
            {
                "year": int(r.year),
                "revenue": revenue,
                "cost": cost,
                "margin": revenue - cost,
                "margin_pct": (100 * (revenue - cost) / revenue) if revenue else None,
                "count": r.count,
            }
        )

    status_rows = (
        db.query(SaleBalance.match_status, func.count(SaleBalance.id))
        .group_by(SaleBalance.match_status)
        .all()
    )
    margin_status_counts = {status: count for status, count in status_rows}

    best_rows = (
        reconciled.order_by(SaleBalance.margin_pct.desc().nullslast()).limit(5).all()
    )
    worst_rows = (
        reconciled.order_by(SaleBalance.margin_pct.asc().nullslast()).limit(5).all()
    )

    def _margin_brief(b: SaleBalance) -> dict:
        return {
            "tab_title": b.tab_title,
            "folios": b.folios or [],
            "customer_name": b.customer_name,
            "revenue": (
                float(b.ledger_revenue) if b.ledger_revenue is not None else None
            ),
            "cost_total": float(b.cost_total) if b.cost_total is not None else None,
            "margin_amount": (
                float(b.margin_amount) if b.margin_amount is not None else None
            ),
            "margin_pct": float(b.margin_pct) if b.margin_pct is not None else None,
        }

    margins = {
        "reconciled_count": recon_count,
        "reconciled_revenue": recon_revenue,
        "reconciled_cost": recon_cost,
        "margin_total": margin_total,
        "margin_pct": (100 * margin_total / recon_revenue) if recon_revenue else None,
        "by_year": margin_by_year,
        "status_counts": margin_status_counts,
        "best": [_margin_brief(b) for b in best_rows],
        "worst": [_margin_brief(b) for b in worst_rows],
    }

    return {
        "monthly": monthly,
        "by_payment_method": by_payment_method,
        "by_concept": by_concept,
        "top_customers": top_customers,
        "margins": margins,
        "delivery_pending": {
            "count": pending_count,
            "total": float(pending_total or 0),
        },
        "invoice_pending_registration": {
            "count": invoice_count,
            "total": float(invoice_total or 0),
        },
        "quarantined": {"count": quarantined_count},
        "grand_total": float(grand_total or 0),
        "ytd_total": float(ytd_total or 0),
        "label": STATS_LABEL,
    }


@router.get("/margins")
def list_margins(
    year: int | None = Query(default=None, ge=2000, le=2100),
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Per-sale margin rows (one per BALANCES tab), newest folio month first."""
    query = db.query(SaleBalance)
    if year is not None:
        query = query.filter(extract("year", SaleBalance.folio_month) == year)
    if status:
        query = query.filter(SaleBalance.match_status == status.strip().lower())

    total = query.count()
    rows = (
        query.order_by(
            SaleBalance.folio_month.desc().nullslast(), SaleBalance.id.desc()
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    def _num(value):
        return float(value) if value is not None else None

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": b.id,
                "tab_title": b.tab_title,
                "folios": b.folios or [],
                "folio_month": b.folio_month.isoformat() if b.folio_month else None,
                "customer_name": b.customer_name,
                "item_count": b.item_count,
                "cost_subtotal": _num(b.cost_subtotal),
                "shipping_total": _num(b.shipping_total),
                "cost_total": _num(b.cost_total),
                "sheet_sale_total": _num(b.sheet_sale_total),
                "sheet_profit": _num(b.sheet_profit),
                "ledger_revenue": _num(b.ledger_revenue),
                "margin_amount": _num(b.margin_amount),
                "margin_pct": _num(b.margin_pct),
                "match_status": b.match_status,
                "recon_delta": _num(b.recon_delta),
                "synced_at": b.synced_at.isoformat() if b.synced_at else None,
            }
            for b in rows
        ],
    }


@router.get("")
def list_sales(
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    payment_method: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    customer_id: int | None = Query(default=None),
    quarantined: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Filtered listing of ledger rows, newest sale first."""
    query = db.query(Sale)

    if year is not None:
        query = query.filter(extract("year", Sale.sale_date) == year)
    if month is not None:
        query = query.filter(extract("month", Sale.sale_date) == month)
    if payment_method:
        query = query.filter(Sale.payment_method == payment_method.strip().lower())
    if customer_id is not None:
        query = query.filter(Sale.customer_id == customer_id)
    if quarantined is not None:
        query = query.filter(Sale.quarantined.is_(quarantined))
    if q:
        pattern = f"%{_escape_like(q.strip())}%"
        query = query.filter(
            or_(
                Sale.customer_name.ilike(pattern),
                Sale.description.ilike(pattern),
                Sale.folio.ilike(pattern),
            )
        )

    total = query.count()
    rows = (
        query.order_by(Sale.sale_date.desc().nullslast(), Sale.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_row_to_dict(s) for s in rows],
    }
