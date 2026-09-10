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
- Customer: matched by normalized phone; source "web"; fills empty fields only.
- quote.notes: a machine-owned JSON block (delivery, invoice, payment, warnings)
  between "[Pedido web ...]" and "[/Pedido web]" marker lines. The admin app's
  "Pedido web" panel parses it; human text outside the block is preserved.
- Notification: web_order_paid / web_order_pending / web_order_problem for each
  WEB_ORDER_NOTIFY_EMAILS address, never duplicated per (quote, event).
- Task: on approval, one in "Por enviar" (plus one in "Solicitud de facturas"
  when an invoice was requested), assigned to WEB_ORDER_ASSIGNEE and created
  by the system task user.

Mercado Pago retries webhooks and may deliver them out of order, so all of this
is idempotent: a replay changes nothing, and a payment state never moves
backwards (PAYMENT_RANK).
"""

import json
import os
import re
from datetime import datetime, timezone
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
from services.quote_followup import _resolve_system_user_id

CREATED_BY = "tienda-web"
BUSINESS_TZ = ZoneInfo("America/Mexico_City")  # same business clock as routes/pos.py

CENT = Decimal("0.01")
MONEY_TOLERANCE = Decimal("0.01")
IVA_16 = Decimal("0.16")
IVA_RATES = (Decimal(0), IVA_16)
SHIPPING_HANDLE = "envio"

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

# Problem kind -> stable wording after "Revisar pago WEB-... — ". The resulting
# prefix is also what de-duplicates web_order_problem notifications per kind.
PROBLEM_LABELS = {
    "mismatch": "monto distinto",
    "refunded": "reembolso",
    "charged_back": "contracargo",
    "in_mediation": "disputa",
    "duplicate_payment": "cobro duplicado",
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


class OrderRejected(Exception):
    """A checkout the storefront must not record (HTTP 422)."""

    def __init__(self, problems: list[str]):
        super().__init__(", ".join(problems))
        self.problems = problems


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


# ── small helpers ────────────────────────────────────────────────────────────


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _round2(value: Any) -> Decimal:
    return _dec(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _money(value: Any) -> str:
    return f"${_round2(value):,.2f}"


def _qty(value: Any) -> str:
    return format(_dec(value).normalize(), "f")


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
    return problems


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
    delivery_data: dict,
    invoice_data: Any,
    payment_data: dict | None,
    warnings: list[str],
) -> dict:
    previous = existing.get("warnings")
    if not isinstance(previous, list):
        previous = []
    merged = {w for w in previous if isinstance(w, str)} | set(warnings)
    return {
        "v": 1,
        "ref": ref,
        "delivery": delivery_data,
        "invoice": invoice_data,
        "payment": (
            payment_data if payment_data is not None else existing.get("payment")
        ),
        "warnings": sorted(merged)[:30],
    }


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


def _new_quote(db: Session, order: Any, warnings: list[str]) -> Quote:
    buyer = order.customer
    # An invalid phone was already flagged; keep what the buyer typed.
    phone = _valid_phone(buyer.phone) or buyer.phone.strip()
    quote = Quote(
        quote_number=order.external_reference,
        status="draft",
        customer_name=buyer.name.strip()[:200],
        customer_phone=phone[:30],
        customer_email=(_clean(buyer.email) or "")[:255] or None,
        customer_location=(_clean(buyer.location) or "")[:300] or None,
        subtotal=_round2(order.totals.subtotal),
        iva_amount=_round2(order.totals.iva_amount),
        total=_round2(order.totals.total),
        created_by=CREATED_BY,
        assigned_to=_assignee_email(),
    )
    quote.items = _cart_lines(db, order, warnings)
    return quote


def _get_or_create_quote(
    db: Session, order: Any, warnings: list[str]
) -> tuple[Quote, bool]:
    ref = order.external_reference
    quote = _find_quote(db, ref)
    if quote is not None:
        return quote, False
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
        amount = mp.transaction_amount
        if amount is None or abs(_dec(amount) - _dec(quote.total)) > MONEY_TOLERANCE:
            return "mismatch", "mismatch"
        return "approved", None
    if payment_status in ("refunded", "charged_back"):
        return payment_status, mp.status  # refunded | charged_back | in_mediation
    return payment_status, None


def _decide(current: str | None, new: str) -> str:
    """'apply' (moves forward), 'same' (the current state again) or 'stale'."""
    if current is None:
        return "apply"
    if new == current:
        return "same"
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


def _link_customer(
    db: Session,
    quote: Quote,
    order: Any,
    invoice_data: Any,
    now: datetime,
    purchased: bool,
) -> None:
    buyer = order.customer
    phone = _valid_phone(buyer.phone)
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
    email = buyer.email if buyer.email and EMAIL_RE.match(buyer.email) else None
    _touch(
        customer,
        name=buyer.name,
        email=email,
        location=buyer.location,
        source="web",
        purchased=purchased,
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
        f"{_money(quote.total)} MXN ({_payment_label(mp)}). "
        "No entregar hasta que Mercado Pago acredite el pago."
    )


def _problem_prefix(kind: str, quote: Quote, mp: Any) -> str:
    label = PROBLEM_LABELS[kind]
    if kind == "duplicate_payment":
        label = f"{label} #{mp.payment_id}"
    return f"Revisar pago {quote.quote_number} — {label}:"


def _problem_detail(kind: str, quote: Quote, mp: Any) -> str:
    name, total = quote.customer_name, _money(quote.total)
    if kind == "mismatch":
        charged = (
            _money(mp.transaction_amount)
            if mp.transaction_amount is not None
            else "un monto desconocido"
        )
        return (
            f"Mercado Pago aprobó {charged} y el pedido suma {total} MXN. "
            "No entregar hasta aclararlo."
        )
    if kind == "refunded":
        return f"Mercado Pago reembolsó el pago de {name} ({total} MXN). No entregar."
    if kind == "charged_back":
        return (
            f"{name} desconoció el cargo con su banco ({total} MXN). "
            "No entregar y guarda la evidencia de entrega."
        )
    if kind == "in_mediation":
        return (
            f"{name} abrió una disputa en Mercado Pago ({total} MXN). "
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
    quote: Quote, mp: Any, delivery_data: dict, invoice_data: Any, warnings: list[str]
) -> str:
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
    if warnings:
        lines.append(f"Revisar: {', '.join(sorted(set(warnings)))}")
    return "\n".join(lines)


def _invoice_description(
    quote: Quote, mp: Any, invoice_data: dict, warnings: list[str]
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
            f"Total: {_money(quote.total)} MXN "
            f"(subtotal {_money(quote.subtotal)} + IVA {_money(quote.iva_amount)})."
        ),
    ]
    flagged = sorted(INVOICE_PROBLEMS.intersection(warnings))
    if flagged:
        lines.append(f"Revisar datos fiscales: {', '.join(flagged)}")
    return "\n".join(lines)


def _ensure_tasks(
    db: Session,
    quote: Quote,
    mp: Any,
    delivery_data: dict,
    invoice_data: Any,
    now: datetime,
    warnings: list[str],
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
        if delivery_data.get("method") == "recoger"
        else "preparar envío"
    )
    wanted = [
        (
            f"Pedido web {ref}",
            f"Pedido web {ref} — {action}: {quote.customer_name}",
            FULFILLMENT_CATEGORY,
            "high",
            _fulfillment_description(quote, mp, delivery_data, invoice_data, warnings),
        )
    ]
    if _invoice_requested(invoice_data):
        wanted.append(
            (
                f"Factura {ref}",
                f"Factura {ref} — {invoice_data.get('razon_social') or quote.customer_name}",
                INVOICE_CATEGORY,
                "medium",
                _invoice_description(quote, mp, invoice_data, warnings),
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
    delivery_data: dict,
    invoice_data: Any,
    now: datetime,
    warnings: list[str],
) -> tuple[int, int]:
    """Notifications + Tasks for the order's current payment state.

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
        tasks += _ensure_tasks(
            db, quote, mp, delivery_data, invoice_data, now, warnings
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


def record_order(db: Session, order: Any, now: datetime | None = None) -> dict:
    """Upsert the web order for one storefront event, then commit.

    `order` is a validated routes.storefront_orders.StorefrontOrder. Raises
    OrderRejected when a checkout_created payload fails order_problems().
    """
    now = _utc(now or datetime.now(timezone.utc))
    ref = order.external_reference
    mp = order.mercadopago
    if mp is not None and mp.live_mode is False and not _test_payments_allowed():
        # TEST credentials (a preview deploy, say) must never create orders in
        # the production admin.
        return _response(None, ref, duplicate=False, warnings=[], ignored="test_mode")

    problems = order_problems(order)
    if problems and order.event == "checkout_created":
        raise OrderRejected(problems)
    # Past checkout the buyer may already have paid: never drop the order over
    # a bad field. Record it and flag it for review instead.
    warnings = list(problems)

    quote, created = _get_or_create_quote(db, order, warnings)
    if not created:
        _fill_contact(quote, order)
        if abs(_dec(order.totals.total) - _dec(quote.total)) > MONEY_TOLERANCE:
            warnings.append("totals_differ_from_recorded_order")

    existing_block = read_notes_block(quote.notes) or {}
    delivery_data = _delivery_dict(order.delivery)
    # A payment_update without invoice data keeps what checkout stored.
    invoice_data = (
        _invoice_dict(order.invoice)
        if order.invoice is not None
        else existing_block.get("invoice")
    )

    if order.event == "payment_update":
        new_status, problem = _classify_payment(quote, mp)
    else:
        new_status, problem = "checkout", None
    decision = _decide(quote.payment_status, new_status)

    record_payment = decision == "apply"
    if decision == "same" and order.event == "payment_update":
        if quote.payment_reference and quote.payment_reference != mp.payment_id:
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

    _link_customer(
        db,
        quote,
        order,
        invoice_data,
        now,
        purchased=quote.payment_status == "approved",
    )

    notifications = tasks = 0
    if order.event == "payment_update" and decision != "stale":
        notifications, tasks = _side_effects(
            db, quote, mp, problem, delivery_data, invoice_data, now, warnings
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
            existing_block, ref, delivery_data, invoice_data, payment_data, warnings
        ),
    )
    if notes != quote.notes:
        quote.notes = notes

    db.commit()
    duplicate = (
        not created and decision != "apply" and notifications == 0 and tasks == 0
    )
    return _response(quote, ref, duplicate=duplicate, warnings=warnings)
