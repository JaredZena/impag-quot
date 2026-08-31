"""
Punto de Venta (POS) routes.

- GET  /pos/products                    per-keystroke product search for the register
- POST /pos/sales                       capture a ticket (folio minted server-side)
- GET  /pos/sales                       ticket listing (newest first)
- GET  /pos/stats/vendedores            per-vendedor totals (comisiones base)
- GET  /pos/sales/{sale_id}             full ticket detail (reprint)
- POST /pos/sales/{sale_id}/cancel      cancel + reverse stock/cash/projection
- GET  /pos/cash-sessions/current       open session + running totals + movements
- POST /pos/cash-sessions/open          open the drawer (one per branch)
- GET  /pos/cash-sessions               session history with per-session totals
- POST /pos/cash-sessions/{id}/movements  manual entrada/salida
- POST /pos/cash-sessions/{id}/close    close + expected/difference

POS sales live in pos_sale/pos_sale_item (source of truth) and each item is
ALSO projected as one row in the `sale` ledger (sheet_tab='POS',
source_row=pos_sale_item.id) so the existing Ventas dashboard picks them up
with zero frontend changes. The Sheets importer only touches VENTAS_* tabs,
so 'POS' rows are safe from overwrite/prune.
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import (
    CashMovement,
    CashSession,
    Customer,
    PosFolioCounter,
    PosSale,
    PosSaleItem,
    Product,
    Sale,
    StockMovement,
    Supplier,
    SupplierProduct,
    get_db,
)
from services.exchange_rate_service import exchange_rate_service

# NO router-level auth: every endpoint takes its own Depends(verify_google_token)
# because created_by needs the user's email.
router = APIRouter(prefix="/pos", tags=["pos"])

DEFAULT_BRANCH = "DGO"
PAYMENT_METHODS = {"efectivo", "transferencia", "deposito", "terminal"}
IVA_DIVISOR = Decimal("1.16")  # unit prices are FINAL (IVA-included) on IVA lines
TWO_PLACES = Decimal("0.01")
_BRANCH_RE = re.compile(r"^[A-Z]{2,4}$")
# Business date for sale_date / folio MMYY: the store runs on Durango local
# time, the container clock is UTC — an evening sale must not roll into
# tomorrow (or next month's folio series on the last evening of a month).
BUSINESS_TZ = ZoneInfo("America/Mexico_City")
MAX_MONEY = Decimal("9999999999.99")  # Numeric(12,2) ceiling


def _business_today() -> date:
    return datetime.now(BUSINESS_TZ).date()


def _escape_like(text: str) -> str:
    """Escape LIKE/ILIKE metacharacters (backslash default escape char)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _q2(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _clean_branch(raw: str | None) -> str:
    branch = (raw or DEFAULT_BRANCH).strip().upper() or DEFAULT_BRANCH
    if not _BRANCH_RE.match(branch):
        raise HTTPException(status_code=422, detail="branch inválido (2-4 letras)")
    return branch


def _strip_or_none(value: str | None) -> str | None:
    """Trim free-form optional text; empty/whitespace-only becomes None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _qty_int(quantity: Decimal) -> int:
    """Stock deltas are integers: exact when the quantity is integral,
    half-up rounded otherwise (the ACTUAL delta applied is what gets
    recorded in stock_movement)."""
    return int(quantity.to_integral_value(rounding=ROUND_HALF_UP))


# ── Folio minting (server-side, race-safe) ───────────────────────────────────


def mint_folio(db: Session, branch: str, today: date) -> str:
    """Mint the next NN+MMYY+BRANCH folio (e.g. '100826DGO') inside the
    caller's transaction. No commit here.

    A pos_folio_counter row per (MMYY, branch) is locked with SELECT ... FOR
    UPDATE (no-op on sqlite — fine for tests). The next seq also seeds from
    the max NN already present in sale.folio / pos_sale.folio for that month
    so it never collides with hand-assigned sheet folios.
    """
    mmyy = today.strftime("%m%y")

    def _locked_counter():
        return (
            db.query(PosFolioCounter)
            .filter(
                PosFolioCounter.month_year == mmyy,
                PosFolioCounter.branch == branch,
            )
            .with_for_update()
            .first()
        )

    counter = _locked_counter()
    if counter is None:
        # Portable get-or-create (postgres + sqlite): INSERT under a savepoint
        # so a concurrent insert's IntegrityError (uq_pos_folio_counter)
        # doesn't poison the outer transaction, then re-SELECT FOR UPDATE.
        try:
            with db.begin_nested():
                db.add(PosFolioCounter(month_year=mmyy, branch=branch, last_seq=0))
        except IntegrityError:
            pass
        counter = _locked_counter()

    # Seed from folios already in the ledger and in pos_sale. LIKE narrows the
    # scan; the regex in Python is the real filter and tolerates NN > 2 digits.
    pattern = f"%{mmyy}{branch}"
    seq_re = re.compile(rf"^(\d+){mmyy}{re.escape(branch)}$")
    candidates = [
        f for (f,) in db.query(Sale.folio).filter(Sale.folio.like(pattern)).all()
    ] + [
        f for (f,) in db.query(PosSale.folio).filter(PosSale.folio.like(pattern)).all()
    ]
    db_max = 0
    for folio in candidates:
        m = seq_re.match(folio or "")
        if m:
            db_max = max(db_max, int(m.group(1)))

    seq = max(counter.last_seq or 0, db_max) + 1
    counter.last_seq = seq
    return f"{seq:02d}{mmyy}{branch}"


# ── Cost snapshot (cheapest active supplier; admin-only, never on ticket) ────


def _supplier_shipping_case():
    """Per-unit shipping cost expression: DIRECT → shipping_cost_direct,
    anything else → the 4 OCURRE stages summed."""
    return case(
        (
            SupplierProduct.shipping_method == "DIRECT",
            func.coalesce(SupplierProduct.shipping_cost_direct, 0),
        ),
        else_=(
            func.coalesce(SupplierProduct.shipping_stage1_cost, 0)
            + func.coalesce(SupplierProduct.shipping_stage2_cost, 0)
            + func.coalesce(SupplierProduct.shipping_stage3_cost, 0)
            + func.coalesce(SupplierProduct.shipping_stage4_cost, 0)
        ),
    )


def _cost_snapshot_map(db: Session, product_ids: list[int]) -> dict[int, dict]:
    """Cheapest ACTIVE supplier cost per product in ONE batched query
    (cost NOT NULL and > 0; unit_cost = per-unit cost + shipping in the
    supplier's own currency). First row per product wins — query is ordered."""
    if not product_ids:
        return {}
    shipping = _supplier_shipping_case()
    rows = (
        db.query(
            SupplierProduct.product_id,
            SupplierProduct.id,
            Supplier.name,
            SupplierProduct.currency,
            (SupplierProduct.cost + shipping).label("total_cost"),
        )
        .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
        .filter(
            SupplierProduct.product_id.in_(product_ids),
            SupplierProduct.is_active.is_(True),
            SupplierProduct.cost.isnot(None),
            SupplierProduct.cost > 0,
        )
        .order_by(SupplierProduct.product_id, (SupplierProduct.cost + shipping).asc())
        .all()
    )
    # Reduce in Python, normalizing currencies so a USD cost can't win on its
    # raw number (10 USD is NOT cheaper than 150 MXN). The USD→MXN rate is
    # fetched lazily only when a product actually mixes currencies; if the
    # rate is unavailable, MXN candidates are preferred (their cost is the
    # one we can actually book), falling back to raw order otherwise.
    by_product: dict[int, list] = {}
    for row in rows:
        by_product.setdefault(row[0], []).append(row)

    usd_rate: Decimal | None = None
    usd_rate_fetched = False

    def _mxn_key(currency: str, cost: Decimal):
        nonlocal usd_rate, usd_rate_fetched
        if currency != "USD":
            return (0, cost)
        if not usd_rate_fetched:
            usd_rate = _usd_mxn_rate()
            usd_rate_fetched = True
        if usd_rate is not None:
            return (0, cost * usd_rate)
        return (1, cost)  # rate unknown: rank all USD after every MXN row

    snapshot: dict[int, dict] = {}
    for product_id, candidates in by_product.items():
        currencies = {(c[3] or "MXN").upper() for c in candidates}
        if len(currencies) > 1:
            candidates = sorted(
                candidates,
                key=lambda c: _mxn_key((c[3] or "MXN").upper(), Decimal(str(c[4]))),
            )
        _pid, sp_id, supplier_name, currency_code, total_cost = candidates[0]
        snapshot[product_id] = {
            "supplier_product_id": sp_id,
            "supplier_name": supplier_name[:200] if supplier_name else None,
            "unit_cost": _q2(Decimal(str(total_cost))),
            "cost_currency": (currency_code or "MXN").upper(),
        }
    return snapshot


def _usd_mxn_rate() -> Decimal | None:
    """USD→MXN via the cached exchange-rate service. A failure here must
    NEVER fail the sale — None just means cost unknown for USD lines."""
    try:
        rate = exchange_rate_service.get_exchange_rate("USD", "MXN")
    except Exception:
        return None
    if rate is None:
        return None
    return Decimal(str(rate)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# ── Serializers (Decimal→float, date/datetime→isoformat) ─────────────────────


def _num(value):
    return float(value) if value is not None else None


def _item_to_dict(it: PosSaleItem) -> dict:
    return {
        "id": it.id,
        "product_id": it.product_id,
        "description": it.description,
        "unit": it.unit,
        "quantity": _num(it.quantity),
        "unit_price": _num(it.unit_price),
        "iva": it.iva,
        "line_total": _num(it.line_total),
        # cost snapshot (admin-only; NEVER printed on the customer ticket)
        "supplier_product_id": it.supplier_product_id,
        "supplier_name": it.supplier_name,
        "unit_cost": _num(it.unit_cost),
        "cost_currency": it.cost_currency,
        "exchange_rate": _num(it.exchange_rate),
        "line_cost_mxn": _num(it.line_cost_mxn),
    }


def _sale_header_dict(s: PosSale) -> dict:
    return {
        "id": s.id,
        "folio": s.folio,
        "branch": s.branch,
        "sale_date": s.sale_date.isoformat() if s.sale_date else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "created_by": s.created_by,
        "vendedor": s.vendedor,
        "customer_id": s.customer_id,
        "customer_name": s.customer_name,
        "customer_phone": s.customer_phone,
        "payment_method": s.payment_method,
        "amount_tendered": _num(s.amount_tendered),
        "change_given": _num(s.change_given),
        "subtotal": _num(s.subtotal),
        "iva_amount": _num(s.iva_amount),
        "total": _num(s.total),
        # cost snapshot rollup (admin-only; NEVER printed on the ticket)
        "cost_total": _num(s.cost_total),
        "margin_amount": _num(s.margin_amount),
        "cost_complete": s.cost_complete,
        "requires_invoice": s.requires_invoice,
        # factura (CFDI) details — free-form capture
        "rfc": s.rfc,
        "razon_social": s.razon_social,
        "uso_cfdi": s.uso_cfdi,
        "cfdi_email": s.cfdi_email,
        "delivery_place": s.delivery_place,
        "notes": s.notes,
        "status": s.status,
        "cancelled_at": s.cancelled_at.isoformat() if s.cancelled_at else None,
        "cancelled_by": s.cancelled_by,
        "cancel_reason": s.cancel_reason,
        "cash_session_id": s.cash_session_id,
    }


def _sale_detail_dict(s: PosSale) -> dict:
    return _sale_header_dict(s) | {"items": [_item_to_dict(it) for it in s.items]}


def _session_to_dict(cs: CashSession) -> dict:
    return {
        "id": cs.id,
        "branch": cs.branch,
        "status": cs.status,
        "opened_at": cs.opened_at.isoformat() if cs.opened_at else None,
        "opened_by": cs.opened_by,
        "opening_float": _num(cs.opening_float),
        "closed_at": cs.closed_at.isoformat() if cs.closed_at else None,
        "closed_by": cs.closed_by,
        "expected_cash": _num(cs.expected_cash),
        "counted_cash": _num(cs.counted_cash),
        "difference": _num(cs.difference),
        "notes": cs.notes,
    }


def _movement_to_dict(mv: CashMovement) -> dict:
    return {
        "id": mv.id,
        "cash_session_id": mv.cash_session_id,
        "kind": mv.kind,
        "amount": _num(mv.amount),
        "description": mv.description,
        "pos_sale_id": mv.pos_sale_id,
        "created_at": mv.created_at.isoformat() if mv.created_at else None,
        "created_by": mv.created_by,
    }


def _session_totals_map(db: Session, sessions: list[CashSession]) -> dict[int, dict]:
    """Per-session movement sums in ONE grouped query.

    expected_cash = opening_float + ventas efectivo + entradas − salidas
    − cancelaciones (amounts are always positive; sign implied by kind).
    """
    sums: dict[int, dict[str, Decimal]] = {
        cs.id: {
            "venta": Decimal("0"),
            "entrada": Decimal("0"),
            "salida": Decimal("0"),
            "cancelacion": Decimal("0"),
        }
        for cs in sessions
    }
    if sums:
        rows = (
            db.query(
                CashMovement.cash_session_id,
                CashMovement.kind,
                func.coalesce(func.sum(CashMovement.amount), 0),
            )
            .filter(CashMovement.cash_session_id.in_(list(sums)))
            .group_by(CashMovement.cash_session_id, CashMovement.kind)
            .all()
        )
        for session_id, kind, amount in rows:
            if kind in sums.get(session_id, {}):
                sums[session_id][kind] = Decimal(str(amount or 0))

    totals: dict[int, dict] = {}
    for cs in sessions:
        s = sums[cs.id]
        expected = (
            Decimal(str(cs.opening_float or 0))
            + s["venta"]
            + s["entrada"]
            - s["salida"]
            - s["cancelacion"]
        )
        totals[cs.id] = {
            "ventas_efectivo": float(s["venta"]),
            "entradas": float(s["entrada"]),
            "salidas": float(s["salida"]),
            "cancelaciones": float(s["cancelacion"]),
            "expected_cash": float(_q2(expected)),
        }
    return totals


def _session_totals(db: Session, session: CashSession) -> dict:
    return _session_totals_map(db, [session])[session.id]


def _open_session_for_branch(db: Session, branch: str) -> CashSession | None:
    return (
        db.query(CashSession)
        .filter(CashSession.branch == branch, CashSession.status == "abierta")
        .order_by(CashSession.id.desc())
        .first()
    )


# ── Pydantic bodies ──────────────────────────────────────────────────────────


class PosSaleItemCreate(BaseModel):
    product_id: int | None = None
    description: str = Field(min_length=1)
    unit: str | None = Field(None, max_length=30)
    quantity: float = Field(gt=0, le=999_999, allow_inf_nan=False)
    # FINAL price per unit (IVA-included when iva=True)
    unit_price: float = Field(ge=0, le=99_999_999, allow_inf_nan=False)
    iva: bool = True


class PosSaleCreate(BaseModel):
    branch: str = Field(DEFAULT_BRANCH, max_length=4)
    # who made the sale (email); defaults to the authenticated user
    vendedor: str | None = Field(None, max_length=120)
    customer_id: int | None = None
    customer_name: str | None = Field(None, max_length=200)
    customer_phone: str | None = Field(None, max_length=20)
    payment_method: str = Field(max_length=30)
    amount_tendered: float | None = Field(
        None, ge=0, le=9_999_999_999, allow_inf_nan=False
    )  # efectivo only
    requires_invoice: bool = False
    # factura (CFDI) details — free-form capture, no validation beyond lengths
    rfc: str | None = Field(None, max_length=20)
    razon_social: str | None = Field(None, max_length=200)
    uso_cfdi: str | None = Field(None, max_length=10)
    cfdi_email: str | None = Field(None, max_length=255)
    delivery_place: str | None = Field(None, max_length=200)
    notes: str | None = None
    items: list[PosSaleItemCreate] = Field(min_length=1)


class PosSaleCancel(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class CashSessionOpen(BaseModel):
    branch: str = Field(DEFAULT_BRANCH, max_length=4)
    opening_float: float = Field(ge=0, le=9_999_999_999, allow_inf_nan=False)
    notes: str | None = None


class CashMovementCreate(BaseModel):
    kind: str = Field(max_length=15)  # entrada | salida
    amount: float = Field(gt=0, le=9_999_999_999, allow_inf_nan=False)
    description: str = Field(min_length=1, max_length=300)


class CashSessionClose(BaseModel):
    counted_cash: float = Field(ge=0, le=9_999_999_999, allow_inf_nan=False)
    notes: str | None = None


# ── Product search ───────────────────────────────────────────────────────────


@router.get("/products")
def search_pos_products(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Per-keystroke register search: plain ILIKE, no embeddings/LLM."""
    query = db.query(Product).filter(
        Product.is_active.is_(True), Product.archived_at.is_(None)
    )
    if q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        query = query.filter(
            or_(
                Product.name.ilike(pattern),
                Product.sku.ilike(pattern),
                Product.base_sku.ilike(pattern),
            )
        )
    rows = query.order_by(Product.name.asc()).limit(limit).all()

    # Effective selling price = price ?? calculated_price (same fallback as
    # routes/storefront.py). Currency kept cheap AND correct: a manual price
    # is MXN by convention; calculated prices inherit the currency of the
    # cheapest active supplier (USD products must NOT be prefilled by the
    # register). One batched query for the whole page — this endpoint fires
    # per keystroke, no per-row supplier lookups.
    calc_ids = [
        p.id for p in rows if p.price is None and p.calculated_price is not None
    ]
    currency_by_product: dict[int, str] = {}
    if calc_ids:
        shipping = _supplier_shipping_case()
        supplier_rows = (
            db.query(
                SupplierProduct.product_id,
                SupplierProduct.currency,
                (SupplierProduct.cost + shipping).label("total_cost"),
            )
            .filter(
                SupplierProduct.product_id.in_(calc_ids),
                SupplierProduct.is_active.is_(True),
                SupplierProduct.cost.isnot(None),
                SupplierProduct.cost > 0,
            )
            .order_by(
                SupplierProduct.product_id, (SupplierProduct.cost + shipping).asc()
            )
            .all()
        )
        for product_id, currency_code, _cost in supplier_rows:
            # first row per product = cheapest supplier (query is ordered)
            currency_by_product.setdefault(product_id, currency_code or "MXN")

    items = []
    for p in rows:
        price = p.price if p.price is not None else p.calculated_price
        if p.price is not None:
            currency = "MXN"
        elif p.calculated_price is not None:
            currency = currency_by_product.get(p.id, "MXN")
        else:
            currency = None
        items.append(
            {
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "unit": p.unit.value if p.unit else None,
                "price": _num(price),
                "currency": currency,
                "iva": p.iva if p.iva is not None else True,
                "stock": p.stock if p.stock is not None else 0,
            }
        )
    return {"items": items}


# ── Sales ────────────────────────────────────────────────────────────────────


@router.post("/sales")
def create_pos_sale(
    body: PosSaleCreate,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Capture a ticket. ONE transaction: recompute money server-side, mint
    the folio, decrement stock, register the cash movement (open session +
    efectivo), and project each item into the `sale` ledger."""
    email = user.get("email", "")
    branch = _clean_branch(body.branch)
    # who made the sale — defaults to the authenticated user
    vendedor = _strip_or_none(body.vendedor) or (email or None)

    payment_method = body.payment_method.strip().lower()
    if payment_method not in PAYMENT_METHODS:
        raise HTTPException(
            status_code=422,
            detail=(
                "payment_method debe ser uno de: " + ", ".join(sorted(PAYMENT_METHODS))
            ),
        )

    # Server-side money recompute in Decimal (floats only at the JSON boundary)
    line_items: list[tuple[PosSaleItemCreate, str, Decimal, Decimal, Decimal]] = []
    total = Decimal("0")
    iva_sum = Decimal("0")
    for item in body.items:
        description = re.sub(r"\s+", " ", item.description).strip()
        if not description:
            raise HTTPException(status_code=422, detail="description es requerida")
        quantity = Decimal(str(item.quantity))
        unit_price = Decimal(str(item.unit_price))
        line_total = _q2(quantity * unit_price)
        total += line_total
        if item.iva:
            # unit prices are FINAL: base = line_total / 1.16
            iva_sum += line_total - (line_total / IVA_DIVISOR)
        line_items.append((item, description, quantity, unit_price, line_total))
    total = _q2(total)
    if total > MAX_MONEY:
        raise HTTPException(
            status_code=422, detail="El total excede el máximo permitido"
        )
    iva_amount = _q2(iva_sum)
    subtotal = total - iva_amount

    amount_tendered = None
    change_given = None
    if payment_method == "efectivo" and body.amount_tendered is not None:
        amount_tendered = _q2(Decimal(str(body.amount_tendered)))
        if amount_tendered >= total:
            change_given = amount_tendered - total

    # Validate product ids up front so the stock UPDATE / FKs can't half-fail
    product_ids = [it.product_id for it in body.items if it.product_id is not None]
    if product_ids:
        found = {
            pid
            for (pid,) in db.query(Product.id).filter(Product.id.in_(product_ids)).all()
        }
        missing = [pid for pid in product_ids if pid not in found]
        if missing:
            raise HTTPException(
                status_code=404, detail=f"Producto {missing[0]} no encontrado"
            )

    # ── Cost snapshot (admin-only): cheapest active supplier per product in
    # ONE batched query; USD converted via the cached exchange-rate service.
    # A missing cost or rate NEVER fails the sale — the line stays cost-unknown.
    cost_map = _cost_snapshot_map(db, product_ids)
    usd_rate: Decimal | None = None
    if any(c["cost_currency"] == "USD" for c in cost_map.values()):
        usd_rate = _usd_mxn_rate()
    line_costs: list[dict] = []
    for item, _description, quantity, _unit_price, _line_total in line_items:
        fields: dict = {
            "supplier_product_id": None,
            "supplier_name": None,
            "unit_cost": None,
            "cost_currency": None,
            "exchange_rate": None,
            "line_cost_mxn": None,
        }
        cost = cost_map.get(item.product_id) if item.product_id is not None else None
        if cost is not None:
            rate = Decimal("1.0000") if cost["cost_currency"] == "MXN" else usd_rate
            fields.update(
                supplier_product_id=cost["supplier_product_id"],
                supplier_name=cost["supplier_name"],
                unit_cost=cost["unit_cost"],
                cost_currency=cost["cost_currency"],
                exchange_rate=rate,  # None → rate unavailable, cost unknown
            )
            if rate is not None:
                fields["line_cost_mxn"] = _q2(quantity * cost["unit_cost"] * rate)
        line_costs.append(fields)

    # Header rollup: cost_total sums the KNOWN line costs (NULL when none);
    # margin only exists when EVERY line has a known MXN cost.
    known_costs = [
        c["line_cost_mxn"] for c in line_costs if c["line_cost_mxn"] is not None
    ]
    cost_total = _q2(sum(known_costs, Decimal("0"))) if known_costs else None
    cost_complete = len(known_costs) == len(line_costs)
    margin_amount = total - cost_total if cost_complete else None

    customer = None
    if body.customer_id is not None:
        customer = db.query(Customer).filter(Customer.id == body.customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
    # Snapshot at sale time; explicit values win over the linked customer's
    customer_name = body.customer_name or (customer.display_name if customer else None)
    customer_phone = body.customer_phone or (customer.phone_e164 if customer else None)

    today = _business_today()
    folio = mint_folio(db, branch, today)
    session = _open_session_for_branch(db, branch)

    sale = PosSale(
        folio=folio,
        branch=branch,
        sale_date=today,
        created_by=email,
        vendedor=vendedor,
        customer_id=customer.id if customer else None,
        customer_name=customer_name[:200] if customer_name else None,
        customer_phone=customer_phone[:20] if customer_phone else None,
        payment_method=payment_method,
        amount_tendered=amount_tendered,
        change_given=change_given,
        subtotal=subtotal,
        iva_amount=iva_amount,
        total=total,
        cost_total=cost_total,
        margin_amount=margin_amount,
        cost_complete=cost_complete,
        requires_invoice=body.requires_invoice,
        rfc=_strip_or_none(body.rfc),
        razon_social=_strip_or_none(body.razon_social),
        uso_cfdi=_strip_or_none(body.uso_cfdi),
        cfdi_email=_strip_or_none(body.cfdi_email),
        delivery_place=body.delivery_place,
        notes=body.notes,
        status="completada",
        cash_session_id=session.id if session else None,
    )
    db.add(sale)
    db.flush()  # sale.id

    for (item, description, quantity, unit_price, line_total), cost_fields in zip(
        line_items, line_costs
    ):
        row = PosSaleItem(
            pos_sale_id=sale.id,
            product_id=item.product_id,
            description=description,
            unit=item.unit,
            quantity=quantity,
            unit_price=unit_price,
            iva=item.iva,
            line_total=line_total,
            supplier_product_id=cost_fields["supplier_product_id"],
            supplier_name=cost_fields["supplier_name"],
            unit_cost=cost_fields["unit_cost"],
            cost_currency=cost_fields["cost_currency"],
            exchange_rate=cost_fields["exchange_rate"],
            line_cost_mxn=cost_fields["line_cost_mxn"],
        )
        db.add(row)
        db.flush()  # row.id is the projection's source_row

        if item.product_id is not None:
            delta = _qty_int(quantity)
            if delta:
                # Atomic single-UPDATE decrement (never read-modify-write);
                # negative stock is allowed — a sale is never blocked.
                db.query(Product).filter(Product.id == item.product_id).update(
                    {Product.stock: func.coalesce(Product.stock, 0) - delta},
                    synchronize_session=False,
                )
                db.add(
                    StockMovement(
                        product_id=item.product_id,
                        delta=-delta,
                        reason="venta",
                        pos_sale_id=sale.id,
                        created_by=email,
                    )
                )

        # Projection into the Ventas ledger (one row per item)
        db.add(
            Sale(
                sheet_tab="POS",
                source_row=row.id,
                sale_date=today,
                customer_name=customer_name[:200] if customer_name else None,
                customer_id=customer.id if customer else None,
                description=description,
                unit=item.unit[:30] if item.unit else None,
                quantity=quantity,
                unit_price=unit_price,
                amount=line_total,
                concept=None,
                payment_method=payment_method,
                delivery_place=(
                    body.delivery_place[:200] if body.delivery_place else None
                ),
                reference="POS",
                folio=folio,
                delivery_status="entregado",
                requires_invoice=body.requires_invoice,
                registered=None,
                quarantined=False,
            )
        )

    if session is not None and payment_method == "efectivo":
        db.add(
            CashMovement(
                cash_session_id=session.id,
                kind="venta",
                amount=total,
                description=f"Venta {folio}"[:300],
                pos_sale_id=sale.id,
                created_by=email,
            )
        )

    db.commit()
    db.refresh(sale)
    return _sale_detail_dict(sale)


@router.get("/sales")
def list_pos_sales(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None, max_length=20),
    vendedor: str | None = Query(default=None, max_length=120),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Ticket listing, newest first."""
    query = db.query(PosSale)
    if date_from is not None:
        query = query.filter(PosSale.sale_date >= date_from)
    if date_to is not None:
        query = query.filter(PosSale.sale_date <= date_to)
    if status:
        query = query.filter(PosSale.status == status.strip().lower())
    if vendedor:
        query = query.filter(PosSale.vendedor == vendedor)
    if q:
        pattern = f"%{_escape_like(q.strip())}%"
        query = query.filter(
            or_(PosSale.folio.ilike(pattern), PosSale.customer_name.ilike(pattern))
        )

    total = query.count()
    rows = (
        query.order_by(PosSale.sale_date.desc(), PosSale.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # item counts in ONE grouped query (no N+1)
    counts: dict[int, int] = {}
    if rows:
        counts = dict(
            db.query(PosSaleItem.pos_sale_id, func.count(PosSaleItem.id))
            .filter(PosSaleItem.pos_sale_id.in_([s.id for s in rows]))
            .group_by(PosSaleItem.pos_sale_id)
            .all()
        )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            _sale_header_dict(s) | {"item_count": counts.get(s.id, 0)} for s in rows
        ],
    }


# Static path declared BEFORE the /sales/{sale_id}-style int routes.
@router.get("/stats/vendedores")
def vendedor_stats(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Per-vendedor comisiones base numbers over COMPLETED sales, in ONE
    grouped query. margin_total sums margin_amount over cost_complete rows
    only (margin_known_count says how many contributed); vendedor NULL is its
    own row (frontend shows 'Sin vendedor')."""
    total_sum = func.coalesce(func.sum(PosSale.total), 0)
    query = db.query(
        PosSale.vendedor,
        func.count(PosSale.id).label("sales_count"),
        total_sum.label("total"),
        # no else_: non-cost_complete rows yield NULL, which SUM ignores
        func.sum(case((PosSale.cost_complete.is_(True), PosSale.margin_amount))).label(
            "margin_total"
        ),
        func.sum(case((PosSale.cost_complete.is_(True), 1), else_=0)).label(
            "margin_known_count"
        ),
    ).filter(PosSale.status == "completada")
    if date_from is not None:
        query = query.filter(PosSale.sale_date >= date_from)
    if date_to is not None:
        query = query.filter(PosSale.sale_date <= date_to)
    rows = query.group_by(PosSale.vendedor).order_by(total_sum.desc()).all()
    return {
        "items": [
            {
                "vendedor": vendedor,
                "sales_count": int(sales_count or 0),
                "total": float(total or 0),
                "margin_total": float(margin_total or 0),
                "margin_known_count": int(margin_known_count or 0),
            }
            for vendedor, sales_count, total, margin_total, margin_known_count in rows
        ]
    }


@router.get("/sales/{sale_id}")
def get_pos_sale(
    sale_id: int,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Full ticket detail (used for reprint)."""
    sale = db.query(PosSale).filter(PosSale.id == sale_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Venta no encontrada")
    return _sale_detail_dict(sale)


@router.post("/sales/{sale_id}/cancel")
def cancel_pos_sale(
    sale_id: int,
    body: PosSaleCancel,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Cancel a ticket in ONE transaction: restore stock (reversing the
    ACTUAL recorded deltas), delete the ledger projection rows, and record a
    'cancelacion' cash movement when the efectivo sale's session is open."""
    email = user.get("email", "")
    sale = db.query(PosSale).filter(PosSale.id == sale_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Venta no encontrada")
    if sale.status == "cancelada":
        raise HTTPException(status_code=409, detail="La venta ya está cancelada")

    # Reverse exactly what the sale recorded (deltas are negative on 'venta')
    venta_movements = (
        db.query(StockMovement)
        .filter(StockMovement.pos_sale_id == sale.id, StockMovement.reason == "venta")
        .all()
    )
    for mv in venta_movements:
        db.query(Product).filter(Product.id == mv.product_id).update(
            {Product.stock: func.coalesce(Product.stock, 0) - mv.delta},
            synchronize_session=False,
        )
        db.add(
            StockMovement(
                product_id=mv.product_id,
                delta=-mv.delta,
                reason="cancelacion",
                pos_sale_id=sale.id,
                created_by=email,
            )
        )

    # Drop the Ventas-ledger projection rows so stats stop counting the sale
    item_ids = [it.id for it in sale.items]
    if item_ids:
        db.query(Sale).filter(
            Sale.sheet_tab == "POS", Sale.source_row.in_(item_ids)
        ).delete(synchronize_session=False)

    # Money out of the drawer only if the cash actually went in this session
    if sale.payment_method == "efectivo" and sale.cash_session_id is not None:
        session = (
            db.query(CashSession).filter(CashSession.id == sale.cash_session_id).first()
        )
        if session and session.status == "abierta":
            db.add(
                CashMovement(
                    cash_session_id=session.id,
                    kind="cancelacion",
                    amount=sale.total,
                    description=f"Cancelación venta {sale.folio}"[:300],
                    pos_sale_id=sale.id,
                    created_by=email,
                )
            )

    sale.status = "cancelada"
    sale.cancelled_at = datetime.now(timezone.utc)
    sale.cancelled_by = email
    sale.cancel_reason = body.reason.strip()
    db.commit()
    db.refresh(sale)
    return _sale_detail_dict(sale)


# ── Caja (cash sessions) ─────────────────────────────────────────────────────


@router.get("/cash-sessions/current")
def current_cash_session(
    branch: str = Query(default=DEFAULT_BRANCH, max_length=4),
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """The open session for the branch with running totals, or {session: null}."""
    session = _open_session_for_branch(db, _clean_branch(branch))
    if session is None:
        return {"session": None}
    return {
        "session": _session_to_dict(session),
        "totals": _session_totals(db, session),
        "movements": [_movement_to_dict(m) for m in session.movements],
    }


@router.post("/cash-sessions/open")
def open_cash_session(
    body: CashSessionOpen,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    branch = _clean_branch(body.branch)
    if _open_session_for_branch(db, branch) is not None:
        raise HTTPException(
            status_code=409, detail=f"Ya hay una caja abierta para {branch}"
        )
    session = CashSession(
        branch=branch,
        status="abierta",
        opened_by=user.get("email", ""),
        opening_float=_q2(Decimal(str(body.opening_float))),
        notes=body.notes,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {
        "session": _session_to_dict(session),
        "totals": _session_totals(db, session),
        "movements": [],
    }


@router.get("/cash-sessions")
def list_cash_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Session history, newest first, with per-session totals."""
    query = db.query(CashSession)
    total = query.count()
    rows = query.order_by(CashSession.id.desc()).offset(offset).limit(limit).all()
    totals_map = _session_totals_map(db, rows)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_session_to_dict(cs) | {"totals": totals_map[cs.id]} for cs in rows],
    }


@router.post("/cash-sessions/{session_id}/movements")
def create_cash_movement(
    session_id: int,
    body: CashMovementCreate,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Manual drawer movement (entrada = money in, salida = money out)."""
    kind = body.kind.strip().lower()
    if kind not in {"entrada", "salida"}:
        raise HTTPException(
            status_code=422, detail="kind debe ser 'entrada' o 'salida'"
        )
    session = db.query(CashSession).filter(CashSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sesión de caja no encontrada")
    if session.status != "abierta":
        raise HTTPException(status_code=409, detail="La caja ya está cerrada")

    movement = CashMovement(
        cash_session_id=session.id,
        kind=kind,
        amount=_q2(Decimal(str(body.amount))),
        description=body.description.strip()[:300],
        created_by=user.get("email", ""),
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)
    return {
        "movement": _movement_to_dict(movement),
        "totals": _session_totals(db, session),
    }


@router.post("/cash-sessions/{session_id}/close")
def close_cash_session(
    session_id: int,
    body: CashSessionClose,
    user: dict = Depends(verify_google_token),
    db: Session = Depends(get_db),
):
    """Close the drawer: expected = fondo + ventas efectivo + entradas −
    salidas − cancelaciones; difference = counted − expected."""
    session = db.query(CashSession).filter(CashSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sesión de caja no encontrada")
    if session.status == "cerrada":
        raise HTTPException(status_code=409, detail="La caja ya está cerrada")

    totals = _session_totals(db, session)
    expected = Decimal(str(totals["expected_cash"]))
    counted = _q2(Decimal(str(body.counted_cash)))

    session.expected_cash = expected
    session.counted_cash = counted
    session.difference = counted - expected
    session.status = "cerrada"
    session.closed_at = datetime.now(timezone.utc)
    session.closed_by = user.get("email", "")
    if body.notes is not None:
        session.notes = body.notes
    db.commit()
    db.refresh(session)
    return {"session": _session_to_dict(session), "totals": totals}
