"""
Web orders from the todoparaelcampo.com.mx storefront (Mercado Pago Checkout Pro).

POST /storefront/orders (routes/storefront_orders.py) hands every checkout and
payment event to record_order(). Everything lands in EXISTING tables, so there
is no migration:

- Quote: quote_number is the storefront's external_reference (WEB-YYMMDD-XXXXXX)
  and created_by is "tienda-web". payment_status / payment_method /
  payment_reference carry the Mercado Pago state. quote.status only takes
  existing values: a new order starts as "draft" and becomes "accepted" once a
  payment for the full amount is approved. Nothing here ever changes it
  otherwise, so a web draft someone deliberately sent stays "sent".
- QuoteItem: the cart, written once when the order is first recorded and never
  touched again (someone may have edited it in the admin since).
- Customer: matched by normalized phone; source "web"; fills empty fields only,
  and only once a payment is approved. checkout_created is unauthenticated
  buyer input (anyone can start a checkout), so it never touches the CRM.
- quote.notes: a machine-owned JSON block (delivery, invoice, payment, warnings)
  between "[Pedido web ...]" and "[/Pedido web]" marker lines. The admin app's
  "Pedido web" panel parses it; human text outside the block is preserved.
- Notification: web_order_paid / web_order_pending / web_order_problem for each
  WEB_ORDER_NOTIFY_EMAILS address, never duplicated per (quote, event).
- Task: on approval, one in "Por enviar" (plus one in "Solicitud de facturas"
  when an invoice was requested), assigned to WEB_ORDER_ASSIGNEE and created
  by the system task user.

- A payment_update may arrive without its order, when the storefront could not
  rebuild it from the payment's metadata. It then updates the draft stored at
  checkout and checks the amount against the stored total. If there is no
  draft either, it records a placeholder quote with no items, flagged "pedido
  sin datos". The placeholder is never marked paid, because nothing says what
  to deliver.
- Buyer confirmation: on approval the buyer gets one email with the order, the
  póliza de garantía and the revocation right (services/web_order_email.py).
  It is sent after the commit.

Mercado Pago retries webhooks and may deliver them out of order, so all of this
is idempotent: a replay changes nothing. A payment state never moves backwards
(PAYMENT_RANK), except along the lifecycle of the payment already on record
(SAME_PAYMENT_TRANSITIONS): an OXXO ticket that expires, a review that ends in
a rejection, or a dispute resolved in the seller's favour.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import (
    Customer,
    Notification,
    Product,
    Quote,
    QuoteItem,
    Task,
    TaskCategory,
    TaskUser,
    get_next_task_number,
)
from services import web_order_email
from services.quote_followup import _resolve_system_user_id

logger = logging.getLogger(__name__)

CREATED_BY = "tienda-web"
BUSINESS_TZ = ZoneInfo("America/Mexico_City")  # same business clock as routes/pos.py

CENT = Decimal("0.01")
MONEY_TOLERANCE = Decimal("0.01")
IVA_16 = Decimal("0.16")
IVA_RATES = (Decimal(0), IVA_16)
SHIPPING_HANDLE = "envio"

# A payment whose order never reached the backend (no draft stored at checkout,
# no order data in the event) is recorded under this name, with no items.
PLACEHOLDER_CUSTOMER_NAME = "Pedido web sin datos del cliente"

# Unpaid drafts (checkout_created) accepted per hour before the endpoint
# answers 429. checkout_created is unauthenticated buyer input; this caps a
# flood of it. Payments are never capped.
DEFAULT_MAX_DRAFTS_PER_HOUR = 100

FULFILLMENT_CATEGORY = "Por enviar"
INVOICE_CATEGORY = "Solicitud de facturas"

EVENT_PAID = "web_order_paid"
EVENT_PENDING = "web_order_pending"
EVENT_PROBLEM = "web_order_problem"

# Mercado Pago payment status -> quote.payment_status.
MP_STATUS_MAP = {
    "pending": "pending",
    "in_process": "pending",
    "authorized": "pending",
    "approved": "approved",  # "mismatch" instead when the amount is off
    "rejected": "rejected",
    "cancelled": "cancelled",
    "refunded": "refunded",
    "charged_back": "charged_back",
    # The buyer opened a dispute on an approved payment. Treated like a
    # chargeback (a problem: don't deliver) until it is resolved in MP.
    "in_mediation": "charged_back",
}

# A payment state only moves up this ladder; a lower-ranked event is stale.
PAYMENT_RANK = {
    "checkout": 0,
    "rejected": 1,
    "cancelled": 1,
    "pending": 2,
    "approved": 3,
    "mismatch": 3,
    "refunded": 4,
    "charged_back": 4,
}

# Moves in the lifecycle of ONE Mercado Pago payment, as raw MP statuses, that
# PAYMENT_RANK alone would call stale. The storefront re-reads the payment
# from MP, so when the payment on record moves like this it is news, not a
# late event:
# - an OXXO or SPEI ticket expires or is cancelled;
# - a manual review ends in a rejection;
# - a dispute (in_mediation, stored as charged_back) is resolved in the
#   seller's favour, and the payment is approved again.
# An event for a different payment id still follows the rank ladder.
SAME_PAYMENT_TRANSITIONS = frozenset(
    {
        (before, after)
        for before in ("pending", "in_process", "authorized")
        for after in ("rejected", "cancelled")
    }
    | {("in_mediation", "approved")}
)

# Problem kind -> stable wording after "Revisar pago WEB-... — ". The resulting
# prefix is also what de-duplicates web_order_problem notifications per kind.
PROBLEM_LABELS = {
    "mismatch": "monto distinto",
    "refunded": "reembolso",
    "charged_back": "contracargo",
    "in_mediation": "disputa",
    "duplicate_payment": "cobro duplicado",
    "missing_order": "pedido sin datos",
}

PAYMENT_TYPE_LABELS = {
    "credit_card": "tarjeta de crédito",
    "debit_card": "tarjeta de débito",
    "prepaid_card": "tarjeta prepagada",
    "ticket": "efectivo con ficha de pago",
    "atm": "cajero o ventanilla bancaria",
    "bank_transfer": "transferencia SPEI",
    "account_money": "saldo de Mercado Pago",
}

DELIVERY_LABELS = {
    "recoger": "recoger en tienda",
    "paqueteria": "envío por paquetería",
    "flete": "flete",
}
SHIPPING_LINE_LABELS = {"paqueteria": "Envío por paquetería", "flete": "Flete"}

ADDRESS_FIELDS = (
    "street",
    "number",
    "colonia",
    "cp",
    "municipio",
    "estado",
    "references",
)

PHONE_MX_RE = re.compile(r"^\+52[0-9]{10}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$")
CP_RE = re.compile(r"^[0-9]{5}$")
USO_CFDI_RE = re.compile(r"^[A-Z]{1,2}[0-9]{2}$")
# SAT c_RegimenFiscal, catalog 2026-09-03 (personas físicas and morales).
# fmt: off
REGIMENES_FISCALES = frozenset(
    {
        "601", "603", "605", "606", "607", "608", "610", "611", "612", "614",
        "615", "616", "620", "621", "622", "623", "624", "625", "626",
    }
)
# fmt: on
INVOICE_PROBLEMS = frozenset(
    {
        "invalid_rfc",
        "missing_razon_social",
        "invalid_regimen_fiscal",
        "invalid_cp_fiscal",
        "invalid_uso_cfdi",
        "invalid_invoice_email",
    }
)

NOTES_BLOCK_RE = re.compile(
    r"^\[Pedido web[^\]\n]*\][ \t]*\n(?P<body>.*?)\n\[/Pedido web\][ \t]*$",
    re.MULTILINE | re.DOTALL,
)

# Buyer-typed text never needs angle brackets. They only show up in markup
# aimed at the HTML pages and emails that display these fields.
MARKUP_RE = re.compile(r"[<>]")

CFDI_UNIT_QUANTUM = Decimal("0.000001")  # CFDI 4.0 ValorUnitario: up to 6 decimals


class OrderRejected(Exception):
    """A checkout the storefront must not record (HTTP 422)."""

    def __init__(self, problems: list[str]):
        super().__init__(", ".join(problems))
        self.problems = problems


class DraftLimitReached(Exception):
    """Too many unpaid web drafts in the last hour (HTTP 429)."""

    def __init__(self, limit: int):
        super().__init__(f"more than {limit} unpaid web drafts in the last hour")
        self.limit = limit


# ── env (read per request, so App Runner changes apply without a deploy) ────


def _env_emails(name: str) -> list[str]:
    seen: list[str] = []
    for part in os.getenv(name, "").split(","):
        email = part.strip().lower()
        if email and email not in seen:
            seen.append(email)
    return seen


def _assignee_email() -> str | None:
    emails = _env_emails("WEB_ORDER_ASSIGNEE")
    return emails[0] if emails else None


def _notify_emails() -> list[str]:
    # Falls back to the assignee so a paid order never lands with nobody told.
    return _env_emails("WEB_ORDER_NOTIFY_EMAILS") or _env_emails("WEB_ORDER_ASSIGNEE")


def _test_payments_allowed() -> bool:
    return os.getenv("WEB_ORDERS_ALLOW_TEST", "").strip().lower() == "true"


def _max_drafts_per_hour() -> int:
    """WEB_ORDERS_MAX_DRAFTS_PER_HOUR (default 100); 0 or less turns the cap off."""
    raw = os.getenv("WEB_ORDERS_MAX_DRAFTS_PER_HOUR", "").strip()
    try:
        return int(raw) if raw else DEFAULT_MAX_DRAFTS_PER_HOUR
    except ValueError:
        return DEFAULT_MAX_DRAFTS_PER_HOUR


# ── small helpers ────────────────────────────────────────────────────────────


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _round2(value: Any) -> Decimal:
    return _dec(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _money(value: Any) -> str:
    return f"${_round2(value):,.2f}"


def _qty(value: Any) -> str:
    return format(_dec(value).normalize(), "f")


def _json_number(value: Any) -> int | float:
    number = _dec(value)
    return int(number) if number == number.to_integral_value() else float(number)


def _utc(dt: datetime) -> datetime:
    """Aware UTC. sqlite hands DateTime(timezone=True) back naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _normalize_phone(raw: str) -> str | None:
    """E.164, via the same normalizer the customer backfill used (dedupe key)."""
    from scripts.backfill_customers import normalize_phone  # lazy, as in quote_followup

    return normalize_phone(raw)


def _valid_phone(raw: str) -> str | None:
    phone = _normalize_phone(raw)
    return phone if phone and PHONE_MX_RE.match(phone) else None


def _has_shipping_line(order: Any) -> bool:
    return any(item.handle == SHIPPING_HANDLE for item in order.items)


def _invoice_requested(invoice_data: Any) -> bool:
    return (
        isinstance(invoice_data, dict) and invoice_data.get("requires_invoice") is True
    )


# ── validation ───────────────────────────────────────────────────────────────


def expected_total(order: Any) -> Decimal:
    """What the buyer is charged: Σ unit_total × qty, plus shipping unless the
    storefront already sent it as its own "envio" line."""
    total = sum(
        (_dec(item.unit_total) * _dec(item.quantity) for item in order.items),
        Decimal(0),
    )
    if not _has_shipping_line(order):
        total += _dec(order.delivery.cost_total)
    return _round2(total)


def order_problems(order: Any) -> list[str]:
    """Inconsistencies in a validated payload, as codes (empty when clean)."""
    problems: list[str] = []
    buyer = order.customer
    if not _valid_phone(buyer.phone):
        problems.append("invalid_phone")
    if buyer.email and not EMAIL_RE.match(buyer.email):
        problems.append("invalid_email")

    for item in order.items:
        with_iva = _round2(_dec(item.unit_price) * (1 + _dec(item.iva_rate)))
        if abs(with_iva - _dec(item.unit_total)) > MONEY_TOLERANCE:
            problems.append(f"unit_total_mismatch:{item.handle}")
    totals = order.totals
    if abs(expected_total(order) - _dec(totals.total)) > MONEY_TOLERANCE:
        problems.append("totals_mismatch")
    breakdown = _dec(totals.subtotal) + _dec(totals.iva_amount)
    if abs(breakdown - _dec(totals.total)) > MONEY_TOLERANCE:
        problems.append("iva_breakdown_mismatch")

    delivery = order.delivery
    if delivery.method in ("paqueteria", "flete") and not _address_dict(
        delivery.address
    ):
        problems.append("missing_address")

    invoice = order.invoice
    if invoice is not None and invoice.requires_invoice:
        if not RFC_RE.match((invoice.rfc or "").strip().upper()):
            problems.append("invalid_rfc")
        if not _clean(invoice.razon_social):
            problems.append("missing_razon_social")
        if (invoice.regimen_fiscal or "").strip() not in REGIMENES_FISCALES:
            problems.append("invalid_regimen_fiscal")
        if not CP_RE.match((invoice.cp_fiscal or "").strip()):
            problems.append("invalid_cp_fiscal")
        if not USO_CFDI_RE.match((invoice.uso_cfdi or "").strip().upper()):
            problems.append("invalid_uso_cfdi")
        if not EMAIL_RE.match((invoice.email or "").strip()):
            problems.append("invalid_invoice_email")
    problems += [f"markup_in:{field}" for field in _markup_fields(order)]
    return problems


def _markup_fields(order: Any) -> list[str]:
    """Buyer-typed fields that contain < or >. These fields end up in HTML
    pages (the public quote page) and emails."""
    buyer = order.customer
    candidates = [
        ("customer.name", buyer.name),
        ("customer.email", buyer.email),
        ("customer.location", buyer.location),
    ]
    address = order.delivery.address
    if address is not None:
        candidates += [
            (f"address.{field}", getattr(address, field, None))
            for field in ADDRESS_FIELDS
        ]
    invoice = order.invoice
    if invoice is not None:
        candidates += [
            ("invoice.razon_social", invoice.razon_social),
            ("invoice.email", invoice.email),
        ]
    return [name for name, value in candidates if value and MARKUP_RE.search(value)]


# ── the JSON block in quote.notes ────────────────────────────────────────────


def _address_dict(address: Any) -> dict | None:
    if address is None:
        return None
    data = {field: _clean(getattr(address, field, None)) for field in ADDRESS_FIELDS}
    return data if any(data.values()) else None


def _delivery_dict(delivery: Any) -> dict:
    return {
        "method": delivery.method,
        "address": _address_dict(delivery.address),
        "cost_total": float(_round2(delivery.cost_total)),
    }


def _invoice_dict(invoice: Any) -> dict:
    if not invoice.requires_invoice:
        return {"requires_invoice": False}
    return {
        "requires_invoice": True,
        "rfc": _clean((invoice.rfc or "").upper()),
        "razon_social": _clean(invoice.razon_social),
        "regimen_fiscal": _clean(invoice.regimen_fiscal),
        "cp_fiscal": _clean(invoice.cp_fiscal),
        "uso_cfdi": _clean((invoice.uso_cfdi or "").upper()),
        "email": _clean(invoice.email),
    }


def _payment_dict(mp: Any) -> dict:
    return {
        "provider": "mercadopago",
        "preference_id": mp.preference_id,
        "payment_id": mp.payment_id,
        "status": mp.status,
        "status_detail": mp.status_detail,
        "payment_type_id": mp.payment_type_id,
        "payment_method_id": mp.payment_method_id,
        "transaction_amount": (
            float(_round2(mp.transaction_amount))
            if mp.transaction_amount is not None
            else None
        ),
        "date_approved": mp.date_approved,
        "live_mode": mp.live_mode,
    }


def read_notes_block(notes: str | None) -> dict | None:
    """The web-order JSON block in quote.notes, or None."""
    if not notes:
        return None
    match = NOTES_BLOCK_RE.search(notes)
    if not match:
        return None
    try:
        data = json.loads(match.group("body"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def write_notes_block(notes: str | None, ref: str, data: dict) -> str:
    """Replace (or prepend) the block; text outside it is left as it was."""
    block = (
        f"[Pedido web {ref}]\n"
        f"{json.dumps(data, ensure_ascii=False, indent=2)}\n"
        "[/Pedido web]"
    )
    if notes and NOTES_BLOCK_RE.search(notes):
        return NOTES_BLOCK_RE.sub(lambda _match: block, notes, count=1)
    rest = (notes or "").strip()
    return f"{block}\n\n{rest}" if rest else block


def _block_data(
    existing: dict,
    ref: str,
    delivery_data: dict | None,
    invoice_data: Any,
    payment_data: dict | None,
    warnings: list[str],
    *,
    lines: list[dict] | None = None,
    buyer_confirmation: dict | None = None,
) -> dict:
    previous = existing.get("warnings")
    if not isinstance(previous, list):
        previous = []
    merged = {w for w in previous if isinstance(w, str)} | set(warnings)
    data = {
        "v": 1,
        "ref": ref,
        "delivery": delivery_data,
        "invoice": invoice_data,
        "payment": (
            payment_data if payment_data is not None else existing.get("payment")
        ),
        "warnings": sorted(merged)[:30],
    }
    # What the buyer was charged per line (_charged_lines), written when the
    # order is first recorded and carried forward on every later event.
    kept_lines = lines if lines is not None else existing.get("lines")
    if isinstance(kept_lines, list) and kept_lines:
        data["lines"] = kept_lines
    confirmation = buyer_confirmation or existing.get("buyer_confirmation")
    if isinstance(confirmation, dict):
        data["buyer_confirmation"] = confirmation
    return data


# ── quote upsert ─────────────────────────────────────────────────────────────


def _find_quote(db: Session, ref: str) -> Quote | None:
    # FOR UPDATE serializes concurrent events for the same order on Postgres
    # (sqlite ignores it; its writers are serialized anyway).
    return db.query(Quote).filter(Quote.quote_number == ref).with_for_update().first()


def _cart_lines(db: Session, order: Any, warnings: list[str]) -> list[QuoteItem]:
    lines: list[QuoteItem] = []
    for index, item in enumerate(order.items):
        product = db.get(Product, item.product_id) if item.product_id else None
        if item.product_id and product is None:
            warnings.append(f"unmapped_product:{item.product_id}")
        iva_applicable = _dec(item.iva_rate) > 0
        iva_known = product is not None and product.iva is not None
        if iva_known and bool(product.iva) != iva_applicable:
            warnings.append(f"iva_mismatch:{product.id}")
        unit = item.unit_label or (
            product.unit.value if product is not None and product.unit else None
        )
        lines.append(
            QuoteItem(
                product_id=product.id if product is not None else None,
                description=item.description.strip()[:500],
                sku=product.sku if product is not None else None,
                quantity=_dec(item.quantity),
                unit=unit[:50] if unit else None,
                unit_price=_round2(item.unit_price),
                iva_applicable=iva_applicable,
                notes=f"Tienda en línea: {item.handle} · {_money(item.unit_total)} c/u con IVA",
                sort_order=index,
            )
        )
    shipping = _round2(order.delivery.cost_total)
    if shipping > 0 and not _has_shipping_line(order):
        lines.append(
            QuoteItem(
                description=SHIPPING_LINE_LABELS.get(order.delivery.method, "Envío"),
                quantity=Decimal(1),
                unit_price=_round2(shipping / (1 + IVA_16)),
                iva_applicable=True,
                notes=f"Envío con IVA incluido: {_money(shipping)}",
                sort_order=len(lines),
            )
        )
    return lines


LINE_KEYS = frozenset(
    {"description", "quantity", "iva_rate", "unit_total", "line_total"}
)


def _charged_lines(order: Any) -> list[dict]:
    """What the buyer was charged per line, IVA included, priced the way the
    storefront prices it (DESIGN §2: unit_total = round2(price × (1 + IVA)) and
    line_total = unit_total × qty).

    Kept in the notes block because QuoteItem stores only the pre-IVA price, and
    a shipping line's IVA-inclusive total can't always be rebuilt from that to
    the centavo. The invoice task and the buyer confirmation read it back."""
    lines: list[dict] = []
    for item in order.items:
        quantity = _dec(item.quantity)
        unit_total = _round2(item.unit_total)
        lines.append(
            {
                "description": item.description.strip()[:500],
                "unit_label": _clean(item.unit_label),
                "quantity": _json_number(quantity),
                "iva_rate": float(_dec(item.iva_rate)),
                "unit_total": float(unit_total),
                "line_total": float(_round2(unit_total * quantity)),
            }
        )
    shipping = _round2(order.delivery.cost_total)
    if shipping > 0 and not _has_shipping_line(order):
        lines.append(
            {
                "description": SHIPPING_LINE_LABELS.get(order.delivery.method, "Envío"),
                "unit_label": None,
                "quantity": 1,
                "iva_rate": float(IVA_16),
                "unit_total": float(shipping),
                "line_total": float(shipping),
            }
        )
    return lines


def _lines_from_items(quote: Quote) -> list[dict]:
    """The same per-line figures, rebuilt from the QuoteItems for a notes block
    that has none. Uses the storefront's rule; a shipping line can come out a
    centavo off, and the invoice task's total check flags that."""
    lines: list[dict] = []
    for item in sorted(quote.items, key=lambda i: i.sort_order or 0):
        rate = IVA_16 if item.iva_applicable else Decimal(0)
        quantity = _dec(item.quantity)
        unit_total = _round2(_dec(item.unit_price) * (1 + rate))
        lines.append(
            {
                "description": item.description,
                "unit_label": item.unit,
                "quantity": _json_number(quantity),
                "iva_rate": float(rate),
                "unit_total": float(unit_total),
                "line_total": float(_round2(unit_total * quantity)),
            }
        )
    return lines


def _order_lines(quote: Quote, block: dict) -> list[dict]:
    lines = block.get("lines")
    if (
        isinstance(lines, list)
        and lines
        and all(isinstance(line, dict) and LINE_KEYS <= line.keys() for line in lines)
    ):
        return lines
    return _lines_from_items(quote)


def _order_fields(order: Any) -> dict:
    buyer = order.customer
    # An invalid phone was already flagged; keep what the buyer typed.
    phone = _valid_phone(buyer.phone) or buyer.phone.strip()
    return {
        "customer_name": buyer.name.strip()[:200],
        "customer_phone": phone[:30],
        "customer_email": (_clean(buyer.email) or "")[:255] or None,
        "customer_location": (_clean(buyer.location) or "")[:300] or None,
        "subtotal": _round2(order.totals.subtotal),
        "iva_amount": _round2(order.totals.iva_amount),
        "total": _round2(order.totals.total),
    }


def _new_quote(db: Session, order: Any, warnings: list[str]) -> Quote:
    common = {
        "quote_number": order.external_reference,
        "status": "draft",
        "created_by": CREATED_BY,
        "assigned_to": _assignee_email(),
    }
    if not order.has_order_data:
        # A payment for an order the backend never saw: no draft stored at
        # checkout and no order data in the event. It is recorded so the money
        # isn't lost. It has no items, so _classify_payment never marks it paid.
        return Quote(
            **common,
            customer_name=PLACEHOLDER_CUSTOMER_NAME,
            customer_phone="",
            subtotal=Decimal(0),
            iva_amount=Decimal(0),
            total=Decimal(0),
            items=[],
        )
    quote = Quote(**common, **_order_fields(order))
    quote.items = _cart_lines(db, order, warnings)
    return quote


def _adopt_order(db: Session, quote: Quote, order: Any, warnings: list[str]) -> None:
    """A placeholder (a quote with no items) takes the order from the first
    later event that carries it, for example a checkout_created that timed out
    on the storefront and was committed after the payment arrived."""
    for field, value in _order_fields(order).items():
        setattr(quote, field, value)
    quote.items = _cart_lines(db, order, warnings)


def _check_draft_limit(db: Session, now: datetime) -> None:
    """Refuse a new unpaid draft past WEB_ORDERS_MAX_DRAFTS_PER_HOUR. Anyone can
    start a checkout, so a script could otherwise bury the real web orders in
    junk drafts."""
    limit = _max_drafts_per_hour()
    if limit <= 0:
        return
    recent = (
        db.query(func.count(Quote.id))
        .filter(
            Quote.created_by == CREATED_BY,
            Quote.payment_status == "checkout",
            Quote.created_at >= now - timedelta(hours=1),
        )
        .scalar()
    )
    if recent >= limit:
        logger.warning(
            "web drafts: %s unpaid in the last hour (limit %s); refusing checkout_created",
            recent,
            limit,
        )
        raise DraftLimitReached(limit)


def _get_or_create_quote(
    db: Session, order: Any, warnings: list[str], now: datetime
) -> tuple[Quote, bool]:
    ref = order.external_reference
    quote = _find_quote(db, ref)
    if quote is not None:
        return quote, False
    if order.event == "checkout_created":
        _check_draft_limit(db, now)  # never for a payment: money is never refused
    try:
        with db.begin_nested():
            quote = _new_quote(db, order, warnings)
            db.add(quote)
            db.flush()
    except IntegrityError:
        # A concurrent event for the same order (MP sends payment.created and
        # payment.updated almost together) inserted it first: update that row.
        quote = _find_quote(db, ref)
        if quote is None:
            raise
        return quote, False
    return quote, True


def _fill_contact(quote: Quote, order: Any) -> None:
    buyer = order.customer
    if not quote.customer_email and _clean(buyer.email):
        quote.customer_email = _clean(buyer.email)[:255]
    if not quote.customer_location and _clean(buyer.location):
        quote.customer_location = _clean(buyer.location)[:300]


# ── payment state ────────────────────────────────────────────────────────────


def _classify_payment(quote: Quote, mp: Any) -> tuple[str, str | None]:
    """(payment_status, problem kind or None) for a Mercado Pago payment."""
    payment_status = MP_STATUS_MAP[mp.status]
    if payment_status == "approved":
        if not quote.items:
            # No order recorded (see _new_quote), so there is no total to check
            # the amount against and nothing to deliver: never "paid".
            return "mismatch", "missing_order"
        amount = mp.transaction_amount
        if amount is None or abs(_dec(amount) - _dec(quote.total)) > MONEY_TOLERANCE:
            return "mismatch", "mismatch"
        return "approved", None
    if payment_status in ("refunded", "charged_back"):
        return payment_status, mp.status  # refunded | charged_back | in_mediation
    return payment_status, None


def _recorded_mp_status(quote: Quote, block: dict) -> str | None:
    """The raw MP status of the payment on record. payment_status can't give it:
    in_process and authorized are stored as "pending", in_mediation as
    "charged_back". The notes block keeps the raw value."""
    payment = block.get("payment")
    if (
        isinstance(payment, dict)
        and payment.get("payment_id") == quote.payment_reference
        and isinstance(payment.get("status"), str)
    ):
        return payment["status"]
    return "approved" if quote.payment_status == "mismatch" else quote.payment_status


def _decide(
    current: str | None,
    new: str,
    transition: tuple[str | None, str | None] | None = None,
) -> str:
    """'apply' (moves forward), 'same' (the current state again) or 'stale'.

    `transition` is (recorded, new) raw MP status when the event is for the
    payment already on record. A move in SAME_PAYMENT_TRANSITIONS then applies
    even if it ranks lower."""
    if current is None:
        return "apply"
    if new == current:
        return "same"
    if transition in SAME_PAYMENT_TRANSITIONS:
        return "apply"
    current_rank = PAYMENT_RANK.get(current, -1)
    new_rank = PAYMENT_RANK[new]
    if new_rank > current_rank:
        return "apply"
    if new_rank < current_rank or current == "approved":
        return "stale"  # an equal-rank mismatch never demotes a paid order
    return "apply"  # rejected<->cancelled, mismatch->approved, refunded<->charged_back


def _mark_accepted(quote: Quote, mp: Any, now: datetime, warnings: list[str]) -> None:
    quote.status = "accepted"
    if quote.accepted_at is None:
        approved_at = _parse_datetime(mp.date_approved)
        if mp.date_approved and approved_at is None:
            warnings.append("unparseable_date_approved")
        quote.accepted_at = approved_at or now


# ── customer ─────────────────────────────────────────────────────────────────


def _find_customer(db: Session, phone: str) -> Customer | None:
    return db.query(Customer).filter(Customer.phone_e164 == phone).first()


def _buyer(order: Any, quote: Quote) -> dict:
    """Who paid: from the event's order data, or else from what checkout stored
    on the quote."""
    if order.has_order_data:
        buyer = order.customer
        return {
            "name": buyer.name,
            "phone": buyer.phone,
            "email": buyer.email,
            "location": buyer.location,
        }
    return {
        "name": quote.customer_name,
        "phone": quote.customer_phone,
        "email": quote.customer_email,
        "location": quote.customer_location,
    }


def _link_customer(
    db: Session,
    quote: Quote,
    buyer: dict,
    invoice_data: Any,
    now: datetime,
) -> None:
    """Match or create the CRM customer of a PAID order; fill empty fields only.

    Only an approved payment reaches this. checkout_created is unauthenticated
    buyer input: if it could create customers, or fill an existing customer's
    empty email or RFC by phone number, anyone could write into the CRM."""
    phone = _valid_phone(buyer.get("phone") or "")
    if phone is None:
        return  # already flagged invalid_phone; nothing reliable to match on
    customer = _find_customer(db, phone)
    if customer is None:
        try:
            with db.begin_nested():
                customer = Customer(phone_e164=phone, source="web")
                db.add(customer)
                db.flush()
        except IntegrityError:
            # Created concurrently by another event for the same buyer.
            customer = _find_customer(db, phone)
            if customer is None:
                raise

    from scripts.backfill_customers import _touch

    # The backfill's fill-empty-only semantics. Timestamps are handled below:
    # sqlite reads them back naive, so compare in UTC explicitly.
    email = buyer.get("email")
    if not (email and EMAIL_RE.match(email) and not MARKUP_RE.search(email)):
        email = None
    _touch(
        customer,
        name=buyer.get("name"),
        email=email,
        location=buyer.get("location"),
        source="web",
        purchased=True,
    )
    rfc = invoice_data.get("rfc") if isinstance(invoice_data, dict) else None
    if rfc and RFC_RE.match(rfc) and not customer.rfc:
        customer.rfc = rfc
    if customer.first_seen_at is None or _utc(customer.first_seen_at) > now:
        customer.first_seen_at = now
    if customer.last_activity_at is None or _utc(customer.last_activity_at) < now:
        customer.last_activity_at = now
    if quote.customer_id is None:
        quote.customer_id = customer.id


# ── notifications ────────────────────────────────────────────────────────────


def _payment_label(mp: Any) -> str:
    kind = (mp.payment_type_id or "").strip()
    return PAYMENT_TYPE_LABELS.get(kind, kind or "Mercado Pago")


def _delivery_label(delivery_data: Any) -> str:
    method = (delivery_data or {}).get("method") or ""
    return DELIVERY_LABELS.get(method, method or "sin especificar")


def _order_total_text(quote: Quote, mp: Any) -> str:
    """The order's total, or Mercado Pago's amount when no order was recorded."""
    if quote.items:
        return f"{_money(quote.total)} MXN"
    if mp is not None and mp.transaction_amount is not None:
        return f"{_money(mp.transaction_amount)} MXN según Mercado Pago"
    return "monto desconocido"


def _paid_message(
    quote: Quote, mp: Any, delivery_data: dict, invoice_data: Any, warnings: list[str]
) -> str:
    text = (
        f"Pedido web {quote.quote_number} pagado: {quote.customer_name}, "
        f"{_money(quote.total)} MXN ({_payment_label(mp)}). "
        f"Entrega: {_delivery_label(delivery_data)}."
    )
    if _invoice_requested(invoice_data):
        text += " Requiere factura."
    if warnings:
        text += " Revisa las notas del pedido."
    return text


def _pending_message(quote: Quote, mp: Any) -> str:
    return (
        f"Pedido web {quote.quote_number} con pago pendiente: {quote.customer_name}, "
        f"{_order_total_text(quote, mp)} ({_payment_label(mp)}). "
        "No entregar hasta que Mercado Pago acredite el pago."
    )


def _dispute_resolved_prefix(quote: Quote) -> str:
    return f"Pedido web {quote.quote_number} — disputa resuelta:"


def _problem_prefix(kind: str, quote: Quote, mp: Any) -> str:
    label = PROBLEM_LABELS[kind]
    if kind == "duplicate_payment":
        label = f"{label} #{mp.payment_id}"
    return f"Revisar pago {quote.quote_number} — {label}:"


def _problem_detail(kind: str, quote: Quote, mp: Any) -> str:
    name, total = quote.customer_name, _order_total_text(quote, mp)
    charged = (
        f"{_money(mp.transaction_amount)} MXN"
        if mp.transaction_amount is not None
        else "un monto desconocido"
    )
    if kind == "mismatch":
        return (
            f"Mercado Pago aprobó {charged} y el pedido suma {total}. "
            "No entregar hasta aclararlo."
        )
    if kind == "missing_order":
        return (
            f"Mercado Pago aprobó el pago #{mp.payment_id} por {charged}, pero el "
            "pedido llegó sin sus datos (cliente, productos, entrega). No entregar: "
            f"busca la referencia {quote.quote_number} en Mercado Pago para "
            "identificar al comprador y completa la cotización."
        )
    if kind == "refunded":
        return f"Mercado Pago reembolsó el pago de {name} ({total}). No entregar."
    if kind == "charged_back":
        return (
            f"{name} desconoció el cargo con su banco ({total}). "
            "No entregar y guarda la evidencia de entrega."
        )
    if kind == "in_mediation":
        return (
            f"{name} abrió una disputa en Mercado Pago ({total}). "
            "No entregar hasta resolverla."
        )
    return (
        f"Mercado Pago aprobó otro pago para el mismo pedido de {name}. "
        "Revisa si hay que reembolsar uno."
    )


def _notify(
    db: Session,
    quote: Quote,
    recipients: list[str],
    event_type: str,
    message: str,
    dedupe_prefix: str | None = None,
) -> int:
    """One notification per recipient, unless that (quote, event) exists."""
    created = 0
    for email in recipients:
        existing = db.query(Notification.id).filter(
            Notification.quote_id == quote.id,
            Notification.event_type == event_type,
            Notification.recipient_email == email,
        )
        if dedupe_prefix:
            existing = existing.filter(
                Notification.message.startswith(dedupe_prefix, autoescape=True)
            )
        if existing.first() is None:
            db.add(
                Notification(
                    recipient_email=email,
                    quote_id=quote.id,
                    event_type=event_type,
                    message=message,
                )
            )
            created += 1
    if created:
        db.flush()
    return created


# ── tasks ────────────────────────────────────────────────────────────────────


def _category_id(db: Session, name: str) -> int | None:
    row = (
        db.query(TaskCategory.id)
        .filter(TaskCategory.name == name)
        .order_by(TaskCategory.id)
        .first()
    )
    return row[0] if row else None


def _task_user_id(db: Session, email: str | None) -> int | None:
    if not email:
        return None
    row = (
        db.query(TaskUser.id)
        .filter(func.lower(TaskUser.email) == email, TaskUser.is_active.is_(True))
        .first()
    )
    return row[0] if row else None


def _task_exists(db: Session, title_prefix: str) -> bool:
    return (
        db.query(Task.id)
        .filter(Task.title.startswith(title_prefix, autoescape=True))
        .first()
        is not None
    )


def _address_text(address: Any) -> str:
    if not isinstance(address, dict):
        return ""
    street = " ".join(p for p in (address.get("street"), address.get("number")) if p)
    place = ", ".join(p for p in (address.get("municipio"), address.get("estado")) if p)
    parts = [
        street,
        f"Col. {address['colonia']}" if address.get("colonia") else "",
        f"CP {address['cp']}" if address.get("cp") else "",
        place,
    ]
    text = ", ".join(p for p in parts if p)
    if address.get("references"):
        text += f" (referencias: {address['references']})"
    return text


def _fulfillment_description(
    quote: Quote,
    mp: Any,
    delivery_data: dict | None,
    invoice_data: Any,
    warnings: list[str],
    confirmation_note: str | None = None,
) -> str:
    delivery_data = delivery_data or {}
    lines = [
        (
            f"Pedido pagado en línea con Mercado Pago ({_payment_label(mp)}), "
            f"pago #{mp.payment_id}."
        ),
        (
            f"Cliente: {quote.customer_name} · Tel: {quote.customer_phone} · "
            f"Correo: {quote.customer_email or '—'}"
        ),
        f"Entrega: {_delivery_label(delivery_data)}",
    ]
    address = _address_text(delivery_data.get("address"))
    if address:
        lines.append(f"Dirección: {address}")
    lines += ["", "Productos:"]
    for item in quote.items:
        unit = f" ({item.unit})" if item.unit else ""
        lines.append(f"- {_qty(item.quantity)} × {item.description}{unit}")
    lines += [
        "",
        (
            f"Total cobrado: {_money(quote.total)} MXN "
            f"(incluye IVA de {_money(quote.iva_amount)})."
        ),
    ]
    if _invoice_requested(invoice_data):
        lines.append(f'Pidió factura: ver la tarea "Factura {quote.quote_number}".')
    if confirmation_note:
        lines.append(confirmation_note)
    if warnings:
        lines.append(f"Revisar: {', '.join(sorted(set(warnings)))}")
    return "\n".join(lines)


def _rate_label(rate: Decimal) -> str:
    return "16%" if rate > 0 else "tasa 0%"


def _cfdi_concepts(
    lines: list[dict],
) -> tuple[list[tuple[dict, Decimal, Decimal]], Decimal, Decimal]:
    """CFDI 4.0 concepts that add up to what was charged.

    The charge rounds IVA per unit (DESIGN §2), so a 16% CFDI built on the
    stored pre-IVA price can't reproduce it once qty > 1. For example, 10 ×
    $52.39 was charged $523.90, but base $451.60 + 16% = $523.86. A
    ValorUnitario of unit_total / (1 + rate) at 6 decimals, with IVA on that
    base, lands on the charged total.

    Returns [(line, valor_unitario, rate)], CFDI subtotal, CFDI IVA."""
    rows = []
    importe_sum = Decimal(0)
    iva_sum = Decimal(0)
    for line in lines:
        rate = _dec(line["iva_rate"])
        quantity = _dec(line["quantity"])
        unit_value = (_dec(line["unit_total"]) / (1 + rate)).quantize(
            CFDI_UNIT_QUANTUM, rounding=ROUND_HALF_UP
        )
        importe = unit_value * quantity
        importe_sum += importe
        iva_sum += importe * rate
        rows.append((line, unit_value, rate))
    return rows, _round2(importe_sum), _round2(iva_sum)


def _invoice_description(
    quote: Quote,
    mp: Any,
    invoice_data: dict,
    order_lines: list[dict],
    warnings: list[str],
) -> str:
    lines = [
        (
            f"Factura para el pedido web {quote.quote_number}, pagado en línea con "
            f"Mercado Pago ({_payment_label(mp)}), pago #{mp.payment_id}."
        ),
        f"RFC: {invoice_data.get('rfc') or '—'}",
        f"Razón social: {invoice_data.get('razon_social') or '—'}",
        f"Régimen fiscal: {invoice_data.get('regimen_fiscal') or '—'}",
        f"C.P. fiscal: {invoice_data.get('cp_fiscal') or '—'}",
        f"Uso del CFDI: {invoice_data.get('uso_cfdi') or '—'}",
        f"Correo para enviar la factura: {invoice_data.get('email') or '—'}",
        "",
        (
            "Conceptos (captura el valor unitario sin IVA con sus 6 decimales y "
            "calcula el IVA sobre ese importe; así el CFDI suma lo cobrado):"
        ),
    ]
    concepts, cfdi_subtotal, cfdi_iva = _cfdi_concepts(order_lines)
    for line, unit_value, rate in concepts:
        unit = f" ({line['unit_label']})" if line.get("unit_label") else ""
        lines.append(
            f"- {_qty(line['quantity'])} × {line['description']}{unit} · "
            f"valor unitario sin IVA {unit_value} · IVA {_rate_label(rate)} · "
            f"importe con IVA {_money(line['line_total'])}"
        )
    cfdi_total = cfdi_subtotal + cfdi_iva
    charged = _round2(quote.total)
    lines.append(
        f"CFDI: subtotal {_money(cfdi_subtotal)} + IVA {_money(cfdi_iva)} = "
        f"total {_money(cfdi_total)} MXN."
    )
    if cfdi_total == charged:
        lines.append(
            f"Coincide con lo cobrado por Mercado Pago: {_money(charged)} MXN."
        )
    else:
        lines.append(
            f"Ojo: Mercado Pago cobró {_money(charged)} MXN; la diferencia de "
            f"{_money(abs(cfdi_total - charged))} es de redondeo. Revísala con el "
            "contador antes de timbrar."
        )
    flagged = sorted(INVOICE_PROBLEMS.intersection(warnings))
    if flagged:
        lines.append(f"Revisar datos fiscales: {', '.join(flagged)}")
    return "\n".join(lines)


def _ensure_tasks(
    db: Session,
    quote: Quote,
    mp: Any,
    delivery_data: dict | None,
    invoice_data: Any,
    now: datetime,
    warnings: list[str],
    order_lines: list[dict],
    confirmation_note: str | None = None,
) -> int:
    """Fulfillment (+ invoice) Tasks for a paid order, each created only once."""
    try:
        system_user_id = _resolve_system_user_id(db)
    except RuntimeError:
        warnings.append("no_task_user")
        return 0
    assignee_id = _task_user_id(db, _assignee_email()) or system_user_id
    ref = quote.quote_number
    action = (
        "preparar para recoger"
        if (delivery_data or {}).get("method") == "recoger"
        else "preparar envío"
    )
    wanted = [
        (
            f"Pedido web {ref}",
            f"Pedido web {ref} — {action}: {quote.customer_name}",
            FULFILLMENT_CATEGORY,
            "high",
            _fulfillment_description(
                quote, mp, delivery_data, invoice_data, warnings, confirmation_note
            ),
        )
    ]
    if _invoice_requested(invoice_data):
        wanted.append(
            (
                f"Factura {ref}",
                f"Factura {ref} — {invoice_data.get('razon_social') or quote.customer_name}",
                INVOICE_CATEGORY,
                "medium",
                _invoice_description(quote, mp, invoice_data, order_lines, warnings),
            )
        )

    created = 0
    for prefix, title, category, priority, description in wanted:
        if _task_exists(db, prefix):
            continue
        db.add(
            Task(
                title=title[:300],
                description=description,
                status="pending",
                priority=priority,
                due_date=now.astimezone(BUSINESS_TZ).date(),
                category_id=_category_id(db, category),
                created_by=system_user_id,
                assigned_to=assignee_id,
                task_number=get_next_task_number(db),
            )
        )
        # Flush so the next get_next_task_number() sees this number as taken.
        db.flush()
        created += 1
    return created


def _side_effects(
    db: Session,
    quote: Quote,
    mp: Any,
    problem: str | None,
    delivery_data: dict | None,
    invoice_data: Any,
    now: datetime,
    warnings: list[str],
    *,
    order_lines: list[dict] | None = None,
    confirmation_note: str | None = None,
    dispute_resolved: bool = False,
) -> tuple[int, int]:
    """Notifications and Tasks for the order's current payment state.

    Safe to repeat: every row is de-duplicated, so a replayed event only fills
    in what an earlier attempt missed."""
    status = quote.payment_status
    recipients = _notify_emails()
    if not recipients and (status in ("approved", "pending") or problem):
        warnings.append("no_notification_recipients")
    notifications = tasks = 0
    if status == "approved":
        notifications += _notify(
            db,
            quote,
            recipients,
            EVENT_PAID,
            _paid_message(quote, mp, delivery_data, invoice_data, warnings),
        )
        if dispute_resolved:
            prefix = _dispute_resolved_prefix(quote)
            notifications += _notify(
                db,
                quote,
                recipients,
                EVENT_PAID,
                (
                    f"{prefix} Mercado Pago volvió a acreditar el pago de "
                    f"{quote.customer_name} ({_order_total_text(quote, mp)})."
                ),
                dedupe_prefix=prefix,
            )
        tasks += _ensure_tasks(
            db,
            quote,
            mp,
            delivery_data,
            invoice_data,
            now,
            warnings,
            order_lines if order_lines is not None else _lines_from_items(quote),
            confirmation_note,
        )
    elif status == "pending":
        notifications += _notify(
            db, quote, recipients, EVENT_PENDING, _pending_message(quote, mp)
        )
    if problem:
        prefix = _problem_prefix(problem, quote, mp)
        notifications += _notify(
            db,
            quote,
            recipients,
            EVENT_PROBLEM,
            f"{prefix} {_problem_detail(problem, quote, mp)}",
            dedupe_prefix=prefix,
        )
    return notifications, tasks


# ── buyer confirmation ───────────────────────────────────────────────────────


def _plan_buyer_confirmation(
    quote: Quote,
    block: dict,
    order_lines: list[dict],
    delivery_data: dict | None,
    invoice_data: Any,
    mp: Any,
    now: datetime,
    warnings: list[str],
) -> tuple[dict | None, dict | None, str]:
    """(marker for the notes block, message to send, note for the task).

    The buyer gets one confirmation per order: the marker in the notes block
    stops replays, and a later retry sends it if the config was missing the
    first time."""
    sent = block.get("buyer_confirmation")
    if isinstance(sent, dict) and sent.get("to"):
        return None, None, f"Confirmación del pedido enviada al cliente a {sent['to']}."
    email = _clean(quote.customer_email)
    if not email or not EMAIL_RE.match(email) or MARKUP_RE.search(email):
        warnings.append("buyer_confirmation_not_sent:no_email")
        return (
            None,
            None,
            (
                "Confirmación al cliente: NO se envió porque no hay un correo "
                "válido. Mándale por WhatsApp el resumen del pedido y la póliza "
                "de garantía."
            ),
        )
    if web_order_email.missing_config():
        warnings.append("buyer_confirmation_not_sent:not_configured")
        return (
            None,
            None,
            (
                "Confirmación al cliente: NO se envió porque el correo de "
                "confirmación aún no está configurado. Mándale por correo o "
                "WhatsApp el resumen del pedido y la póliza de garantía."
            ),
        )
    delivery_data = delivery_data or {}
    accepted_at = _utc(quote.accepted_at) if quote.accepted_at else now
    message = web_order_email.build_buyer_confirmation(
        ref=quote.quote_number,
        to=email,
        customer_name=quote.customer_name,
        lines=order_lines,
        subtotal=quote.subtotal,
        iva_amount=quote.iva_amount,
        total=quote.total,
        delivery_method=delivery_data.get("method"),
        delivery_address=_address_text(delivery_data.get("address")) or None,
        invoice=invoice_data,
        payment_label=_payment_label(mp),
        payment_id=mp.payment_id,
        paid_at=accepted_at,
    )
    marker = {"to": email, "queued_at": now.isoformat()}
    return marker, message, f"Confirmación del pedido enviada al cliente a {email}."


# ── entry point ──────────────────────────────────────────────────────────────


def _response(
    quote: Quote | None,
    ref: str,
    *,
    duplicate: bool,
    warnings: list[str],
    ignored: str | None = None,
) -> dict:
    body = {
        "success": True,
        "quote_id": quote.id if quote is not None else None,
        "quote_number": quote.quote_number if quote is not None else ref,
        "status": quote.status if quote is not None else None,
        "payment_status": quote.payment_status if quote is not None else None,
        "duplicate": duplicate,
        "warnings": sorted(set(warnings)),
    }
    if ignored:
        body["ignored"] = ignored
    return body


def _has_any_order_part(order: Any) -> bool:
    return bool(order.items) or any(
        getattr(order, part) is not None for part in ("customer", "delivery", "totals")
    )


def record_order(
    db: Session,
    order: Any,
    now: datetime | None = None,
    outbox: list[dict] | None = None,
) -> dict:
    """Upsert the web order for one storefront event, then commit.

    `order` is a validated routes.storefront_orders.StorefrontOrder. Raises:
    - OrderRejected when a checkout_created payload fails order_problems();
    - DraftLimitReached when a new draft would exceed the unpaid-draft cap.

    When `outbox` is a list, a buyer confirmation to send after the commit is
    appended to it; the route sends it in a background task.
    """
    now = _utc(now or datetime.now(timezone.utc))
    ref = order.external_reference
    mp = order.mercadopago
    if mp is not None and mp.live_mode is False and not _test_payments_allowed():
        # TEST credentials (a preview deploy, say) must never create orders in
        # the production admin. Logged so a tester can see why nothing shows up.
        logger.warning(
            "web order %s: %s with live_mode=false ignored "
            "(set WEB_ORDERS_ALLOW_TEST=true to record test payments)",
            ref,
            order.event,
        )
        return _response(None, ref, duplicate=False, warnings=[], ignored="test_mode")

    has_order = order.has_order_data
    warnings = list(order.parse_warnings)
    if has_order:
        problems = order_problems(order)
        if problems and order.event == "checkout_created":
            raise OrderRejected(problems)
        # Past checkout the buyer may already have paid: never drop the order
        # over a bad field. Record it and flag it for review instead.
        warnings += problems
    elif _has_any_order_part(order):
        warnings.append("incomplete_order_data")

    quote, created = _get_or_create_quote(db, order, warnings, now)
    adopted = False
    if not created and has_order:
        if not quote.items:
            _adopt_order(db, quote, order, warnings)
            adopted = True
        else:
            _fill_contact(quote, order)
            if abs(_dec(order.totals.total) - _dec(quote.total)) > MONEY_TOLERANCE:
                warnings.append("totals_differ_from_recorded_order")

    existing_block = read_notes_block(quote.notes) or {}
    # Without order data in this event, keep what an earlier event stored.
    delivery_data = (
        _delivery_dict(order.delivery) if has_order else existing_block.get("delivery")
    )
    # A payment_update without invoice data keeps what checkout stored.
    invoice_data = (
        _invoice_dict(order.invoice)
        if order.invoice is not None
        else existing_block.get("invoice")
    )
    new_lines = _charged_lines(order) if has_order and (created or adopted) else None

    same_payment = False
    if order.event == "payment_update":
        new_status, problem = _classify_payment(quote, mp)
        same_payment = (
            quote.payment_reference is not None
            and quote.payment_reference == mp.payment_id
        )
    else:
        new_status, problem = "checkout", None
    transition = (
        (_recorded_mp_status(quote, existing_block), mp.status)
        if same_payment
        else None
    )
    decision = _decide(quote.payment_status, new_status, transition)
    dispute_resolved = (
        decision == "apply"
        and new_status == "approved"
        and transition == ("in_mediation", "approved")
    )

    record_payment = decision == "apply"
    if decision == "same" and order.event == "payment_update":
        if quote.payment_reference and not same_payment:
            # Another payment in the state already on record: keep the one on
            # record. A second approved payment means the buyer paid twice.
            if new_status == "approved":
                problem = "duplicate_payment"
                warnings.append(f"additional_approved_payment:{mp.payment_id}")
        else:
            record_payment = True

    if decision == "apply":
        quote.payment_status = new_status
        if order.event == "payment_update":
            method = "mercadopago"
            if mp.payment_type_id:
                method = f"mercadopago:{mp.payment_type_id}"
            quote.payment_method = method[:50]
            quote.payment_reference = mp.payment_id[:255]
        if new_status == "approved":
            _mark_accepted(quote, mp, now, warnings)

    paid = (
        order.event == "payment_update"
        and quote.payment_status == "approved"
        and decision != "stale"
    )
    order_lines = None
    confirmation_marker = message = confirmation_note = None
    if paid:
        # Only a real payment backs the buyer's details (see _link_customer).
        _link_customer(db, quote, _buyer(order, quote), invoice_data, now)
        order_lines = (
            new_lines if new_lines is not None else _order_lines(quote, existing_block)
        )
        if outbox is not None:
            confirmation_marker, message, confirmation_note = _plan_buyer_confirmation(
                quote,
                existing_block,
                order_lines,
                delivery_data,
                invoice_data,
                mp,
                now,
                warnings,
            )

    notifications = tasks = 0
    if order.event == "payment_update" and decision != "stale":
        notifications, tasks = _side_effects(
            db,
            quote,
            mp,
            problem,
            delivery_data,
            invoice_data,
            now,
            warnings,
            order_lines=order_lines,
            confirmation_note=confirmation_note,
            dispute_resolved=dispute_resolved,
        )

    payment_data = (
        _payment_dict(mp)
        if record_payment and order.event == "payment_update"
        else None
    )
    notes = write_notes_block(
        quote.notes,
        ref,
        _block_data(
            existing_block,
            ref,
            delivery_data,
            invoice_data,
            payment_data,
            warnings,
            lines=new_lines,
            buyer_confirmation=confirmation_marker,
        ),
    )
    if notes != quote.notes:
        quote.notes = notes

    db.commit()
    if message is not None and outbox is not None:
        outbox.append(message)
    duplicate = (
        not created
        and not adopted
        and decision != "apply"
        and notifications == 0
        and tasks == 0
    )
    return _response(quote, ref, duplicate=duplicate, warnings=warnings)
