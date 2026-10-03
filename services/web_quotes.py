"""
Self-serve quotes from the todoparaelcampo.com.mx storefront ("Cotizar").

A buyer fills a quote cart on the storefront, which prices every line from its
own catalog on the server and calls POST /storefront/quote-requests
(routes/storefront_quotes.py). The quote is recorded as a WEB ORDER that has
not been paid yet, so paying it later reuses everything services/web_orders.py
already does for a Mercado Pago payment (amount check, CRM customer, tasks,
notifications, buyer confirmation):

- Quote: quote_number is a storefront-style reference (WEB-YYMMDD-XXXXXX),
  created_by "tienda-web", access_token set at once so the buyer lands on the
  public quote page (/cotizacion/<token>).
- status "sent" when the quote is complete (every line has a price and the
  buyer picks the order up): the buyer can accept and pay right away.
- status "draft" when it needs an engineer: a line has no price, or the buyer
  wants delivery (freight is quoted by hand). Staff finish it in the admin and
  send it; routes/quotes.py keeps the same access_token, so the link the buyer
  already has starts showing the pay button.
- quote.notes carries the same "[Pedido web ...]" JSON block as a web order
  (delivery, invoice) plus "origin": "cotizacion"; record_order() reads it
  back when the payment arrives. It has no "lines": the charged lines are
  rebuilt from the QuoteItems at payment time, because staff may edit them.
- payment_status stays NULL until the buyer starts paying (quote_checkout sets
  "checkout"); from then on the web-order payment ladder applies.

Paying: the public page posts to the storefront's /api/quote-checkout, which
calls POST /storefront/quotes/<token>/checkout here for the amount, creates a
Mercado Pago preference with external_reference = quote_number and no order
metadata, and the storefront webhook then sends an order-less payment_update
that record_order() applies to this quote.
"""

import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Product, Quote, QuoteItem, Task, get_next_task_number
from services.quote_followup import _resolve_system_user_id
from services.web_orders import (
    BUSINESS_TZ,
    CP_RE,
    CREATED_BY,
    EMAIL_RE,
    IVA_16,
    MARKUP_RE,
    REGIMENES_FISCALES,
    RFC_RE,
    USO_CFDI_RE,
    _address_dict,
    _assignee_email,
    _category_id,
    _clean,
    _dec,
    _notify,
    _notify_emails,
    _round2,
    _task_exists,
    _task_user_id,
    _valid_phone,
    read_notes_block,
    write_notes_block,
)

logger = logging.getLogger(__name__)

ORIGIN = "cotizacion"
# Same alphabet as the storefront's references (no 0/O/1/I/L/U).
REF_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
DEFAULT_VALIDITY_DAYS = 7
DEFAULT_MAX_PER_HOUR = 60
MAX_PER_PHONE_PER_HOUR = 5
REVIEW_CATEGORY = "Seguimiento a cotizaciones"

EVENT_CREATED = "web_quote_created"
EVENT_REVIEW = "web_quote_review"

REVIEW_LABELS = {
    "unpriced_items": "hay productos sin precio publicado",
    "delivery": "el cliente pide envío (cotizar flete)",
    # Cotizador solar (services/solar_quotes.py)
    "solar_no_fit": "ningún kit del catálogo cubre lo que pide el cliente: arma la propuesta",
    "solar_specs": "la bomba de superficie no tiene altura/caudal publicados: confirma el modelo",
    "solar_large": "sistema interconectado grande: confirma la ingeniería y el precio",
    "solar_media_tension": "tarifa de media tensión: requiere ingeniería",
    "solar_install_outside": "instalación fuera de Durango: agrega traslado y viáticos",
}


class QuoteRequestRejected(Exception):
    def __init__(self, problems: list[str]):
        super().__init__(", ".join(problems))
        self.problems = problems


class QuoteLimitReached(Exception):
    pass


class QuoteNotPayable(Exception):
    def __init__(self, reason: str, status_code: int = 409):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def validity_days() -> int:
    try:
        days = int(os.getenv("WEB_QUOTE_VALIDITY_DAYS", DEFAULT_VALIDITY_DAYS))
    except ValueError:
        return DEFAULT_VALIDITY_DAYS
    return days if days > 0 else DEFAULT_VALIDITY_DAYS


def _max_per_hour() -> int:
    try:
        return int(os.getenv("WEB_QUOTES_MAX_PER_HOUR", DEFAULT_MAX_PER_HOUR))
    except ValueError:
        return DEFAULT_MAX_PER_HOUR


def is_web_quote(quote: Quote) -> bool:
    return quote.created_by == CREATED_BY


def is_self_serve(quote: Quote) -> bool:
    block = read_notes_block(quote.notes) or {}
    return is_web_quote(quote) and block.get("origin") == ORIGIN


def quote_url(quote: Quote) -> str:
    return f"/cotizacion/{quote.access_token}"


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def expires_at(quote: Quote) -> datetime | None:
    if not quote.sent_at or not quote.validity_days:
        return None
    return _utc(quote.sent_at) + timedelta(days=quote.validity_days)


# ── request validation ───────────────────────────────────────────────────────


def request_problems(req: Any) -> list[str]:
    """Problems that refuse a quote request (buyer input, nothing paid yet)."""
    problems: list[str] = []
    buyer = req.customer
    if not _valid_phone(buyer.phone):
        problems.append("invalid_phone")
    if buyer.email and not EMAIL_RE.match(buyer.email):
        problems.append("invalid_email")
    typed = [
        ("customer.name", buyer.name),
        ("customer.email", buyer.email),
        ("customer.location", buyer.location),
        ("notes", req.notes),
    ]
    address = req.delivery.address
    if address is not None:
        typed += [
            (f"address.{field}", getattr(address, field, None))
            for field in (
                "street",
                "number",
                "colonia",
                "cp",
                "municipio",
                "estado",
                "references",
            )
        ]
    if req.delivery.method != "recoger" and not _address_dict(address):
        problems.append("missing_address")
    invoice = req.invoice
    if invoice is not None and invoice.requires_invoice:
        typed += [
            ("invoice.razon_social", invoice.razon_social),
            ("invoice.email", invoice.email),
        ]
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
    problems += [
        f"markup_in:{name}"
        for name, value in typed
        if value and MARKUP_RE.search(value)
    ]
    handles = [item.handle for item in req.items]
    if len(set(handles)) != len(handles):
        problems.append("duplicate_items")
    return problems


def review_reasons(req: Any) -> list[str]:
    reasons: list[str] = []
    if any(_dec(item.unit_price) <= 0 for item in req.items):
        reasons.append("unpriced_items")
    if req.delivery.method != "recoger":
        reasons.append("delivery")
    return reasons


# ── creation ─────────────────────────────────────────────────────────────────


def _new_reference(now: datetime) -> str:
    day = now.astimezone(BUSINESS_TZ).strftime("%y%m%d")
    suffix = "".join(secrets.choice(REF_ALPHABET) for _ in range(6))
    return f"WEB-{day}-{suffix}"


def _check_limits(db: Session, phone: str | None, now: datetime) -> None:
    """Anyone can ask for a quote: cap the flood a script could send."""
    since = now - timedelta(hours=1)
    recent = db.query(func.count(Quote.id)).filter(
        Quote.created_by == CREATED_BY,
        Quote.payment_status.is_(None),
        Quote.created_at >= since,
    )
    limit = _max_per_hour()
    if limit > 0 and recent.scalar() >= limit:
        logger.warning("web quotes: limit of %s per hour reached", limit)
        raise QuoteLimitReached()
    if phone:
        same_phone = recent.filter(Quote.customer_phone == phone).scalar()
        if same_phone >= MAX_PER_PHONE_PER_HOUR:
            raise QuoteLimitReached()


def _items(db: Session, req: Any) -> list[QuoteItem]:
    lines: list[QuoteItem] = []
    for index, item in enumerate(req.items):
        product = db.get(Product, item.product_id) if item.product_id else None
        unit_price = _round2(item.unit_price)
        note = f"Tienda en línea: {item.handle}"
        if unit_price <= 0:
            note += " · SIN PRECIO: capturar antes de enviar"
        lines.append(
            QuoteItem(
                product_id=product.id if product is not None else None,
                description=item.description.strip()[:500],
                sku=(
                    item.sku or (product.sku if product is not None else None) or None
                ),
                quantity=_dec(item.quantity),
                unit=(item.unit_label or "")[:50] or None,
                unit_price=unit_price,
                iva_applicable=_dec(item.iva_rate) > 0,
                notes=note,
                sort_order=index,
            )
        )
    return lines


def _totals(items: list[QuoteItem]) -> tuple[Decimal, Decimal, Decimal]:
    """Same arithmetic as routes/quotes.recalculate_totals, rounded to centavos."""
    subtotal = Decimal(0)
    iva = Decimal(0)
    for item in items:
        line = _dec(item.quantity) * _dec(item.unit_price)
        subtotal += line
        if item.iva_applicable:
            iva += line * IVA_16
    subtotal = _round2(subtotal)
    iva = _round2(iva)
    return subtotal, iva, subtotal + iva


def _review_task(
    db: Session, quote: Quote, reasons: list[str], now: datetime, warnings: list[str]
) -> None:
    prefix = f"Cotización web {quote.quote_number}"
    if _task_exists(db, prefix):
        return
    try:
        system_user_id = _resolve_system_user_id(db)
    except RuntimeError:
        warnings.append("no_task_user")
        return
    why = "; ".join(REVIEW_LABELS.get(r, r) for r in reasons)
    lines = "\n".join(
        f"- {float(i.quantity):g} × {i.description} — "
        + (
            f"${float(i.unit_price):,.2f} + IVA"
            if i.unit_price and i.unit_price > 0
            else "SIN PRECIO"
        )
        for i in quote.items
    )
    description = (
        f"El cliente pidió una cotización en la tienda en línea y necesita revisión: {why}.\n\n"
        f"Cliente: {quote.customer_name} · {quote.customer_phone}"
        + (f" · {quote.customer_email}" if quote.customer_email else "")
        + (f"\nUbicación: {quote.customer_location}" if quote.customer_location else "")
        + f"\n\n{lines}\n\n"
        "Completa precios / agrega el flete en Cotizaciones y pulsa Enviar: el cliente "
        "ya tiene el enlace y verá el botón para pagar con Mercado Pago."
    )
    db.add(
        Task(
            title=f"{prefix} — revisar: {quote.customer_name}"[:300],
            description=description,
            status="pending",
            priority="high",
            due_date=now.astimezone(BUSINESS_TZ).date(),
            category_id=_category_id(db, REVIEW_CATEGORY),
            created_by=system_user_id,
            assigned_to=_task_user_id(db, _assignee_email()) or system_user_id,
            task_number=get_next_task_number(db),
        )
    )
    db.flush()


def create_quote_request(
    db: Session,
    req: Any,
    now: datetime | None = None,
    *,
    extra_reasons: list[str] | None = None,
    block_extra: dict | None = None,
    notes_text: str | None = None,
) -> dict:
    """Record one storefront quote request and commit. Raises
    QuoteRequestRejected (bad input) or QuoteLimitReached (flood cap).

    The cotizador solar (services/solar_quotes.py) adds its own review
    reasons, its data under block_extra, and notes_text: the explanation the
    buyer reads on the public quote page (shown instead of the comments)."""
    now = _utc(now or datetime.now(timezone.utc))
    problems = request_problems(req)
    if problems:
        raise QuoteRequestRejected(problems)
    phone = _valid_phone(req.customer.phone)
    _check_limits(db, phone, now)

    reasons = review_reasons(req) + [
        r for r in extra_reasons or [] if r not in review_reasons(req)
    ]
    instant = not reasons
    warnings: list[str] = []
    location = _clean(req.customer.location)

    delivery = {
        "method": req.delivery.method,
        "address": _address_dict(req.delivery.address),
        "cost_total": 0.0,
    }
    invoice = None
    if req.invoice is not None:
        from services.web_orders import _invoice_dict

        invoice = _invoice_dict(req.invoice)

    buyer_notes = (_clean(req.notes) or "")[:2000]
    quote = None
    for _attempt in range(5):
        ref = _new_reference(now)
        items = _items(db, req)
        subtotal, iva, total = _totals(items)
        block = {
            "v": 1,
            "ref": ref,
            "origin": ORIGIN,
            "delivery": delivery,
            "invoice": invoice,
            "payment": None,
            "warnings": [],
            "review": reasons,
            **(block_extra or {}),
        }
        candidate = Quote(
            quote_number=ref,
            status="sent" if instant else "draft",
            customer_name=req.customer.name.strip()[:200],
            customer_phone=phone or req.customer.phone.strip()[:30],
            customer_email=(_clean(req.customer.email) or "")[:255] or None,
            customer_location=(location or "")[:300] or None,
            notes=write_notes_block(
                notes_text
                or (
                    f"Comentarios del cliente:\n{buyer_notes}" if buyer_notes else None
                ),
                ref,
                block,
            ),
            validity_days=validity_days(),
            subtotal=subtotal,
            iva_amount=iva,
            total=total,
            sent_at=now if instant else None,
            created_by=CREATED_BY,
            assigned_to=_assignee_email(),
            access_token=str(uuid.uuid4()),
        )
        candidate.items = items
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            quote = candidate
            break
        except IntegrityError:
            continue  # reference collision: draw another
    if quote is None:
        raise RuntimeError("could not allocate a quote reference")

    who = f"{quote.customer_name} ({quote.customer_phone})"
    if instant:
        _notify(
            db,
            quote,
            _notify_emails(),
            EVENT_CREATED,
            f"Cotización web {quote.quote_number}: {who} cotizó ${float(total):,.2f} MXN en la tienda",
        )
    else:
        _notify(
            db,
            quote,
            _notify_emails(),
            EVENT_REVIEW,
            f"Cotización web {quote.quote_number} por revisar: {who} — "
            + "; ".join(REVIEW_LABELS.get(r, r) for r in reasons),
        )
        _review_task(db, quote, reasons, now, warnings)
    db.commit()
    return {
        "success": True,
        "quote_number": quote.quote_number,
        "access_token": quote.access_token,
        "status": quote.status,
        "needs_review": not instant,
        "review_reasons": reasons,
        "url": quote_url(quote),
        "subtotal": float(subtotal),
        "iva_amount": float(iva),
        "total": float(total),
        "warnings": warnings,
    }


# ── paying ───────────────────────────────────────────────────────────────────


def expire_if_due(db: Session, quote: Quote, now: datetime | None = None) -> bool:
    """Lazily expire an open quote (the public page does the same)."""
    now = _utc(now or datetime.now(timezone.utc))
    if quote.status in ("sent", "viewed"):
        deadline = expires_at(quote)
        if (
            deadline
            and now > deadline
            and quote.payment_status not in ("pending", "approved", "mismatch")
        ):
            quote.status = "expired"
            quote.expired_at = now
            db.commit()
            return True
    return quote.status == "expired"


def quote_checkout(db: Session, token: str, now: datetime | None = None) -> dict:
    """What the storefront needs to create the Mercado Pago preference for a
    web quote, or QuoteNotPayable. Marks the quote payment_status "checkout"."""
    quote = (
        db.query(Quote).filter(Quote.access_token == token).with_for_update().first()
    )
    if quote is None or not is_web_quote(quote):
        raise QuoteNotPayable("not_found", 404)
    if quote.payment_status in ("approved", "mismatch"):
        raise QuoteNotPayable("already_paid")
    if quote.status == "draft":
        raise QuoteNotPayable("in_review")
    if quote.status in ("rejected",):
        raise QuoteNotPayable("closed")
    if expire_if_due(db, quote, now):
        raise QuoteNotPayable("expired", 410)
    # "accepted" without an online payment means it was closed another way
    # (in store at the POS, or by hand): never offer to charge it again.
    if quote.status not in ("sent", "viewed"):
        raise QuoteNotPayable("closed")
    if not quote.items or any(_dec(i.unit_price) <= 0 for i in quote.items):
        raise QuoteNotPayable("in_review")
    total = _round2(quote.total)
    if total <= 0:
        raise QuoteNotPayable("in_review")
    if quote.payment_status is None:
        quote.payment_status = "checkout"
    db.commit()
    first = min(quote.items, key=lambda i: i.sort_order or 0)
    return {
        "quote_number": quote.quote_number,
        "total": f"{total:.2f}",
        "item_count": len(quote.items),
        "first_item": first.description[:200],
        "customer": {
            "name": quote.customer_name,
            "phone": quote.customer_phone,
            "email": quote.customer_email,
        },
        "expires_at": (expires_at(quote).isoformat() if expires_at(quote) else None),
    }
