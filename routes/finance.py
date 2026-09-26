"""Gastos fijos mensuales y punto de equilibrio (venta mínima para no perder).

- GET    /finance/concepts              recurring expense templates
- POST   /finance/concepts              add a concept
- PUT    /finance/concepts/{id}         edit (name, category, default, active...)
- DELETE /finance/concepts/{id}         delete (past months keep their lines)
- GET    /finance/months/{YYYY-MM}      that month's expense lines
- POST   /finance/months/{YYYY-MM}/open copy active concepts into the month
                                        (last month's amount, else the default);
                                        idempotent — existing lines are kept
- POST   /finance/months/{YYYY-MM}/expenses  add a line (one-off or concept)
- PUT    /finance/expenses/{id}         edit a line (amount, paid, notes...)
- DELETE /finance/expenses/{id}         remove a line
- GET    /finance/dashboard             break-even per month + scenarios for
                                        the selected month + open-quote pipeline

Break-even sales = fixed expenses / gross margin. The margin is measured from
the BALANCES DE VENTA tabs that reconciled against the ledger
(sum sheet_profit / sum sheet_sale_total) and can be overridden per request.
Sales come from the `sale` ledger (non-quarantined), same as /sales/stats —
an operational snapshot, NOT accounting books.
"""

import calendar
from datetime import date, datetime
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import ExpenseConcept, MonthlyExpense, Quote, Sale, SaleBalance, get_db

router = APIRouter(
    prefix="/finance",
    tags=["finance"],
    dependencies=[Depends(verify_google_token)],
)

BUSINESS_TZ = ZoneInfo("America/Mexico_City")
CATEGORIES = ("operativo", "financiamiento", "otro")
FIXED_CATEGORIES = ("operativo", "financiamiento")
OPEN_QUOTE_STATUSES = ("sent", "viewed")
FALLBACK_MARGIN = 0.225  # used only when no BALANCES tab has reconciled yet


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _today() -> date:
    return datetime.now(BUSINESS_TZ).date()


def _parse_month(value: str) -> date:
    try:
        year, month = value.split("-")
        return date(int(year), int(month), 1)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail="Mes inválido; usa YYYY-MM")


def _add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _num(value) -> float:
    return float(value) if value is not None else 0.0


def _check_category(category: str) -> str:
    category = (category or "").strip().lower()
    if category not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Categoría inválida; usa una de: {', '.join(CATEGORIES)}",
        )
    return category


def _concept_dict(c: ExpenseConcept) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "category": c.category,
        "default_amount": _num(c.default_amount),
        "active": c.active,
        "sort_order": c.sort_order,
        "notes": c.notes,
    }


def _expense_dict(e: MonthlyExpense) -> dict:
    return {
        "id": e.id,
        "month": _month_key(e.month),
        "concept_id": e.concept_id,
        "name": e.name,
        "category": e.category,
        "amount": _num(e.amount),
        "paid": e.paid,
        "paid_on": e.paid_on.isoformat() if e.paid_on else None,
        "notes": e.notes,
    }


def measured_margin(db: Session) -> dict:
    """Gross margin over every reconciled BALANCES tab."""
    row = (
        db.query(
            func.count(SaleBalance.id),
            func.sum(SaleBalance.sheet_profit),
            func.sum(SaleBalance.sheet_sale_total),
        )
        .filter(
            SaleBalance.match_status == "reconciled",
            SaleBalance.sheet_profit.isnot(None),
            SaleBalance.sheet_sale_total > 0,
        )
        .one()
    )
    count, profit, total = row
    if not count or not total:
        return {"pct": None, "sample": 0}
    return {"pct": float(profit) / float(total), "sample": count}


def monthly_sales(db: Session, start: date, end_exclusive: date) -> dict[str, float]:
    """Sum of the sale ledger per month, keyed YYYY-MM."""
    rows = (
        db.query(Sale.sale_date, Sale.amount)
        .filter(
            Sale.quarantined.is_(False),
            Sale.sale_date.isnot(None),
            Sale.sale_date >= start,
            Sale.sale_date < end_exclusive,
        )
        .all()
    )
    totals: dict[str, float] = {}
    for sale_date, amount in rows:
        key = _month_key(sale_date)
        totals[key] = totals.get(key, 0.0) + _num(amount)
    return totals


def _breakeven(expenses: float, margin: float) -> float | None:
    if margin <= 0:
        return None
    return round(expenses / margin, 2)


# --------------------------------------------------------------------------- #
# concepts
# --------------------------------------------------------------------------- #


class ConceptIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = "operativo"
    default_amount: Decimal = Field(default=Decimal(0), ge=0)
    active: bool = True
    sort_order: int = 0
    notes: str | None = None


class ConceptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = None
    default_amount: Decimal | None = Field(default=None, ge=0)
    active: bool | None = None
    sort_order: int | None = None
    notes: str | None = None


@router.get("/concepts")
def list_concepts(db: Session = Depends(get_db)):
    rows = (
        db.query(ExpenseConcept)
        .order_by(ExpenseConcept.sort_order, ExpenseConcept.id)
        .all()
    )
    return [_concept_dict(c) for c in rows]


@router.post("/concepts", status_code=201)
def create_concept(body: ConceptIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if (
        db.query(ExpenseConcept)
        .filter(func.lower(ExpenseConcept.name) == name.lower())
        .first()
    ):
        raise HTTPException(
            status_code=409, detail="Ya existe un concepto con ese nombre"
        )
    concept = ExpenseConcept(
        name=name,
        category=_check_category(body.category),
        default_amount=body.default_amount,
        active=body.active,
        sort_order=body.sort_order,
        notes=body.notes,
    )
    db.add(concept)
    db.commit()
    db.refresh(concept)
    return _concept_dict(concept)


@router.put("/concepts/{concept_id}")
def update_concept(concept_id: int, body: ConceptUpdate, db: Session = Depends(get_db)):
    concept = db.get(ExpenseConcept, concept_id)
    if not concept:
        raise HTTPException(status_code=404, detail="Concepto no encontrado")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        name = data["name"].strip()
        clash = (
            db.query(ExpenseConcept)
            .filter(
                func.lower(ExpenseConcept.name) == name.lower(),
                ExpenseConcept.id != concept_id,
            )
            .first()
        )
        if clash:
            raise HTTPException(
                status_code=409, detail="Ya existe un concepto con ese nombre"
            )
        data["name"] = name
    if "category" in data:
        data["category"] = _check_category(data["category"])
    for key, value in data.items():
        setattr(concept, key, value)
    db.commit()
    db.refresh(concept)
    return _concept_dict(concept)


@router.delete("/concepts/{concept_id}")
def delete_concept(concept_id: int, db: Session = Depends(get_db)):
    concept = db.get(ExpenseConcept, concept_id)
    if not concept:
        raise HTTPException(status_code=404, detail="Concepto no encontrado")
    # Past months keep their lines (concept_id is SET NULL by the FK; do it
    # explicitly too so sqlite tests behave the same).
    db.query(MonthlyExpense).filter(MonthlyExpense.concept_id == concept_id).update(
        {MonthlyExpense.concept_id: None}
    )
    db.delete(concept)
    db.commit()
    return {"deleted": concept_id}


# --------------------------------------------------------------------------- #
# month lines
# --------------------------------------------------------------------------- #


class ExpenseIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    concept_id: int | None = None
    category: str | None = None
    amount: Decimal = Field(default=Decimal(0), ge=0)
    paid: bool = False
    paid_on: date | None = None
    notes: str | None = None


class ExpenseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    paid: bool | None = None
    paid_on: date | None = None
    notes: str | None = None


def _month_lines(db: Session, month: date) -> list[MonthlyExpense]:
    return (
        db.query(MonthlyExpense)
        .outerjoin(ExpenseConcept, ExpenseConcept.id == MonthlyExpense.concept_id)
        .filter(MonthlyExpense.month == month)
        .order_by(
            func.coalesce(ExpenseConcept.sort_order, 9999),
            MonthlyExpense.id,
        )
        .all()
    )


@router.get("/months/{month}")
def get_month(month: str, db: Session = Depends(get_db)):
    m = _parse_month(month)
    lines = _month_lines(db, m)
    return {
        "month": _month_key(m),
        "opened": bool(lines),
        "items": [_expense_dict(e) for e in lines],
    }


@router.post("/months/{month}/open")
def open_month(
    month: str,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Copy every active concept into the month. Amount = the same concept's
    line in the closest earlier month if any, else the concept default."""
    m = _parse_month(month)
    existing = {
        e.concept_id
        for e in db.query(MonthlyExpense.concept_id).filter(MonthlyExpense.month == m)
    }
    concepts = (
        db.query(ExpenseConcept)
        .filter(ExpenseConcept.active.is_(True))
        .order_by(ExpenseConcept.sort_order, ExpenseConcept.id)
        .all()
    )
    created = 0
    for c in concepts:
        if c.id in existing:
            continue
        previous = (
            db.query(MonthlyExpense.amount)
            .filter(MonthlyExpense.concept_id == c.id, MonthlyExpense.month < m)
            .order_by(MonthlyExpense.month.desc())
            .first()
        )
        db.add(
            MonthlyExpense(
                month=m,
                concept_id=c.id,
                name=c.name,
                category=c.category,
                amount=previous[0] if previous else c.default_amount,
                paid=False,
                created_by=user.get("email"),
            )
        )
        created += 1
    db.commit()
    return {
        "month": _month_key(m),
        "created": created,
        "items": [_expense_dict(e) for e in _month_lines(db, m)],
    }


@router.post("/months/{month}/expenses", status_code=201)
def add_expense(
    month: str,
    body: ExpenseIn,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    m = _parse_month(month)
    concept = None
    if body.concept_id is not None:
        concept = db.get(ExpenseConcept, body.concept_id)
        if not concept:
            raise HTTPException(status_code=404, detail="Concepto no encontrado")
        dup = (
            db.query(MonthlyExpense)
            .filter(MonthlyExpense.month == m, MonthlyExpense.concept_id == concept.id)
            .first()
        )
        if dup:
            raise HTTPException(
                status_code=409, detail="Ese concepto ya está en el mes"
            )
    name = (body.name or (concept.name if concept else "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="Falta el nombre del gasto")
    category = _check_category(
        body.category or (concept.category if concept else "otro")
    )
    line = MonthlyExpense(
        month=m,
        concept_id=concept.id if concept else None,
        name=name,
        category=category,
        amount=body.amount,
        paid=body.paid,
        paid_on=body.paid_on or (_today() if body.paid else None),
        notes=body.notes,
        created_by=user.get("email"),
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    return _expense_dict(line)


@router.put("/expenses/{expense_id}")
def update_expense(expense_id: int, body: ExpenseUpdate, db: Session = Depends(get_db)):
    line = db.get(MonthlyExpense, expense_id)
    if not line:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")
    data = body.model_dump(exclude_unset=True)
    if "category" in data:
        data["category"] = _check_category(data["category"])
    if "name" in data:
        data["name"] = data["name"].strip()
    if data.get("paid") is True and "paid_on" not in data and not line.paid_on:
        data["paid_on"] = _today()
    if data.get("paid") is False:
        data["paid_on"] = None
    for key, value in data.items():
        setattr(line, key, value)
    db.commit()
    db.refresh(line)
    return _expense_dict(line)


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int, db: Session = Depends(get_db)):
    line = db.get(MonthlyExpense, expense_id)
    if not line:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")
    db.delete(line)
    db.commit()
    return {"deleted": expense_id}


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #


def _sum_by_category(amounts: list[tuple[str, float]]) -> dict[str, float]:
    totals = {c: 0.0 for c in CATEGORIES}
    for category, amount in amounts:
        totals[category if category in totals else "otro"] += amount
    return totals


@router.get("/dashboard")
def dashboard(
    month: str | None = Query(
        default=None, description="YYYY-MM; default = mes actual"
    ),
    months: int = Query(default=12, ge=1, le=36),
    margin_pct: float | None = Query(
        default=None, gt=0, lt=100, description="Override del margen bruto (%)"
    ),
    db: Session = Depends(get_db),
):
    today = _today()
    current = date(today.year, today.month, 1)
    selected = _parse_month(month) if month else current

    measured = measured_margin(db)
    if margin_pct is not None:
        margin, margin_source = margin_pct / 100, "manual"
    elif measured["pct"] is not None:
        margin, margin_source = measured["pct"], "medido"
    else:
        margin, margin_source = FALLBACK_MARGIN, "supuesto"

    start = _add_months(selected, -(months - 1))
    end_exclusive = _add_months(selected, 1)
    sales = monthly_sales(db, start, end_exclusive)

    lines = (
        db.query(MonthlyExpense)
        .filter(MonthlyExpense.month >= start, MonthlyExpense.month < end_exclusive)
        .all()
    )
    by_month: dict[str, list[MonthlyExpense]] = {}
    for line in lines:
        by_month.setdefault(_month_key(line.month), []).append(line)

    # Months nobody opened fall back to the active concepts' defaults so the
    # history still shows a (flagged) break-even line.
    template = _sum_by_category(
        [
            (c.category, _num(c.default_amount))
            for c in db.query(ExpenseConcept).filter(ExpenseConcept.active.is_(True))
        ]
    )

    series = []
    m = start
    while m < end_exclusive:
        key = _month_key(m)
        month_lines = by_month.get(key)
        if month_lines:
            cats = _sum_by_category([(e.category, _num(e.amount)) for e in month_lines])
            unpaid = sum(_num(e.amount) for e in month_lines if not e.paid)
            source = "registrado"
        else:
            cats = dict(template)
            unpaid = 0.0
            source = "plantilla" if any(template.values()) else "sin_datos"
        fixed = cats["operativo"] + cats["financiamiento"]
        month_sales = round(sales.get(key, 0.0), 2)
        gross_profit = round(month_sales * margin, 2)
        series.append(
            {
                "month": key,
                "expenses_source": source,
                "operativo": round(cats["operativo"], 2),
                "financiamiento": round(cats["financiamiento"], 2),
                "otro": round(cats["otro"], 2),
                "fixed_total": round(fixed, 2),
                "unpaid": round(unpaid, 2),
                "sales": month_sales,
                "gross_profit": gross_profit,
                "breakeven_operativo": _breakeven(cats["operativo"], margin),
                "breakeven_fixed": _breakeven(fixed, margin),
                "result": round(gross_profit - fixed - cats["otro"], 2),
                "is_partial": m == current,
            }
        )
        m = _add_months(m, 1)

    sel = series[-1]
    sel_lines = _month_lines(db, selected)

    # Adeudos: lines still unpaid from months before the selected one.
    arrears_rows = (
        db.query(MonthlyExpense)
        .filter(MonthlyExpense.month < selected, MonthlyExpense.paid.is_(False))
        .order_by(MonthlyExpense.month, MonthlyExpense.id)
        .all()
    )
    arrears_total = sum(_num(e.amount) for e in arrears_rows)

    scenarios = [
        {
            "key": "operativo",
            "label": "Solo gastos operativos",
            "expenses": sel["operativo"],
        },
        {
            "key": "fijo",
            "label": "+ pagos de financiamiento",
            "expenses": sel["fixed_total"],
        },
        {
            "key": "al_corriente",
            "label": "+ ponerse al corriente (adeudos)",
            "expenses": round(sel["fixed_total"] + arrears_total, 2),
        },
    ]
    for s in scenarios:
        s["breakeven"] = _breakeven(s["expenses"], margin)
        s["gap"] = (
            round(max(s["breakeven"] - sel["sales"], 0), 2)
            if s["breakeven"] is not None
            else None
        )

    # Pace: only meaningful for the month in progress.
    days_in_month = calendar.monthrange(selected.year, selected.month)[1]
    if selected == current:
        days_elapsed = today.day
    elif selected < current:
        days_elapsed = days_in_month
    else:
        days_elapsed = 0
    projection = (
        round(sel["sales"] / days_elapsed * days_in_month, 2) if days_elapsed else 0.0
    )

    # Reference pace: completed months of the selected year (ledger only).
    year_start = date(selected.year, 1, 1)
    ytd = monthly_sales(db, year_start, min(selected, current))
    completed = [v for k, v in sorted(ytd.items())]
    avg_completed = round(sum(completed) / len(completed), 2) if completed else None
    median_completed = round(median(completed), 2) if completed else None
    target = scenarios[1]["breakeven"]
    months_below = (
        sum(1 for v in completed if target is not None and v < target)
        if completed
        else 0
    )

    open_count, open_total = (
        db.query(func.count(Quote.id), func.coalesce(func.sum(Quote.total), 0))
        .filter(Quote.status.in_(OPEN_QUOTE_STATUSES))
        .one()
    )
    open_total = _num(open_total)
    gap_fixed = scenarios[1]["gap"] or 0.0
    pipeline = {
        "open_count": open_count,
        "open_total": round(open_total, 2),
        "gross_profit_if_all_close": round(open_total * margin, 2),
        "share_needed_to_cover_gap": (
            round(gap_fixed / open_total, 4) if open_total else None
        ),
    }

    return {
        "label": "instantánea operativa — no libros contables",
        "month": sel["month"],
        "today": today.isoformat(),
        "margin": {
            "pct": round(margin, 4),
            "source": margin_source,
            "measured_pct": (
                round(measured["pct"], 4) if measured["pct"] is not None else None
            ),
            "sample": measured["sample"],
        },
        "selected": {
            **sel,
            "opened": bool(sel_lines),
            "items": [_expense_dict(e) for e in sel_lines],
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
            "projection": projection,
            "projection_gap": (
                round(max(target - projection, 0), 2) if target is not None else None
            ),
        },
        "scenarios": scenarios,
        "arrears": {
            "total": round(arrears_total, 2),
            "items": [_expense_dict(e) for e in arrears_rows],
        },
        "reference": {
            "year": selected.year,
            "completed_months": len(completed),
            "avg_sales": avg_completed,
            "median_sales": median_completed,
            "months_below_breakeven": months_below,
        },
        "pipeline": pipeline,
        "series": series,
    }
