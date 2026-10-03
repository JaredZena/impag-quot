"""
Emails for storefront quotes (services/web_quotes.py).

- Staff alert, on every new web quote, to WEB_ORDER_NOTIFY_EMAILS: who asked,
  what, and, for a quote in review, what is missing. Before this the only
  signal was the admin bell, and a quote could wait days unseen.
- Buyer, on creation (when they left an email): the quote link. Instant
  quotes can be paid from it; quotes in review say an engineer completes them.
- Buyer, when staff complete a quote and press Enviar: "lista para pagar".

Built in the request, sent after the commit in a FastAPI background task
through services/mailer.py, so a slow or missing email setup never delays or
breaks the storefront. Every quote field is escaped: buyers type them.
"""

import logging
import os
import re
from html import escape
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session, joinedload

from models import Quote
from services import mailer
from services.web_orders import (
    BUSINESS_TZ,
    EMAIL_RE,
    _notify_emails,
    read_notes_block,
)
from services.web_quotes import REVIEW_LABELS, expires_at, is_web_quote

logger = logging.getLogger(__name__)

DEFAULT_FROM = "Todo Para El Campo <cotizaciones@todoparaelcampo.com.mx>"
# The address the public quote page shows buyers; replies must reach a person.
REPLY_TO = "impagtodoparaelcampo@gmail.com"
STORE_WHATSAPP = "526771197737"
DEFAULT_SITE_URL = "https://www.todoparaelcampo.com.mx"
DEFAULT_ADMIN_URL = "https://impag-admin-app.vercel.app"

DELIVERY_NAMES = {
    "recoger": "Recoger en sucursal",
    "paqueteria": "Envío por paquetería",
    "flete": "Envío a domicilio (flete por cotizar)",
}
# What the buyer reads while a quote is in review (REVIEW_LABELS is for staff).
BUYER_REVIEW_LABELS = {
    "unpriced_items": "confirma el precio de los productos que aún no lo tienen",
    "delivery": "cotiza el flete a tu domicilio",
}

GREEN = "#2E7D32"


def _env(name: str, default: str) -> str:
    return ((os.getenv(name) or "").strip() or default).rstrip("/")


def _h(value: Any) -> str:
    return escape("" if value is None else str(value))


def _money(value: Any) -> str:
    return f"${float(value or 0):,.2f}"


def _qty(value: Any) -> str:
    return f"{float(value or 0):g}"


def _name(quote: Quote) -> str:
    return " ".join(str(quote.customer_name or "").split())


def public_url(quote: Quote) -> str:
    return f"{_env('STOREFRONT_SITE_URL', DEFAULT_SITE_URL)}/cotizacion/{quote.access_token}"


def admin_url(quote: Quote) -> str:
    return f"{_env('ADMIN_APP_URL', DEFAULT_ADMIN_URL)}/quotes/{quote.id}"


def _from() -> str:
    return _env("WEB_QUOTE_FROM_EMAIL", DEFAULT_FROM)


def _buyer_email(quote: Quote) -> str | None:
    email = (quote.customer_email or "").strip()
    return email if email and EMAIL_RE.match(email) else None


def _items_rows(quote: Quote) -> str:
    rows = ""
    for item in sorted(quote.items, key=lambda x: x.sort_order or 0):
        price = float(item.unit_price or 0)
        unit = f" {_h(item.unit)}" if item.unit else ""
        if price > 0:
            each = f"{_money(price)}{' + IVA' if item.iva_applicable else ''}"
            total = _money(price * float(item.quantity or 0))
        else:
            each, total = '<span style="color:#b45309;">por confirmar</span>', "—"
        rows += f"""
        <tr>
          <td style="padding:8px 4px;border-bottom:1px solid #eee;">{_h(item.description)}</td>
          <td style="padding:8px 4px;border-bottom:1px solid #eee;text-align:center;white-space:nowrap;">{_qty(item.quantity)}{unit}</td>
          <td style="padding:8px 4px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;">{each}</td>
          <td style="padding:8px 4px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;">{total}</td>
        </tr>"""
    return f"""
      <table style="width:100%;border-collapse:collapse;font-size:14px;margin:16px 0;">
        <tr style="color:#888;font-size:12px;text-align:left;">
          <th style="padding:4px;">Producto</th><th style="padding:4px;text-align:center;">Cant.</th>
          <th style="padding:4px;text-align:right;">Precio</th><th style="padding:4px;text-align:right;">Total</th>
        </tr>{rows}
      </table>
      <table style="width:100%;font-size:14px;">
        <tr><td style="color:#888;">Subtotal</td><td style="text-align:right;">{_money(quote.subtotal)}</td></tr>
        <tr><td style="color:#888;">IVA</td><td style="text-align:right;">{_money(quote.iva_amount)}</td></tr>
        <tr><td style="font-weight:700;">Total MXN</td><td style="text-align:right;font-weight:700;">{_money(quote.total)}</td></tr>
      </table>"""


def _button(href: str, label: str, color: str = GREEN) -> str:
    return (
        f'<a href="{_h(href)}" style="display:inline-block;background:{color};color:#fff;'
        "text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:600;"
        f'margin:8px 8px 0 0;">{_h(label)}</a>'
    )


def _page(body: str) -> str:
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;'
        f'margin:0 auto;color:#1a1a1a;line-height:1.5;">{body}</div>'
    )


def _seller_footer() -> str:
    return (
        '<p style="color:#888;font-size:12px;margin-top:24px;">IMPAG TECH S.A.P.I. de C.V. · '
        "Todo Para El Campo · Nuevo Ideal, Durango<br>WhatsApp +52 677 119 7737 · "
        f"{REPLY_TO}</p>"
    )


def _address_text(address: Any) -> str:
    if not isinstance(address, dict):
        return ""
    parts = [
        " ".join(p for p in (address.get("street"), address.get("number")) if p),
        address.get("colonia"),
        " ".join(p for p in (address.get("cp"), address.get("municipio")) if p),
        address.get("estado"),
    ]
    text = ", ".join(p for p in parts if p)
    if address.get("references"):
        text += f" (Ref.: {address['references']})"
    return text


def _buyer_comments(quote: Quote) -> str | None:
    marker = "Comentarios del cliente:"
    notes = quote.notes or ""
    if marker not in notes:
        return None
    return notes.split(marker, 1)[1].strip() or None


# ── messages ─────────────────────────────────────────────────────────────────


def build_staff_alert(quote: Quote) -> dict | None:
    """The alert for one new web quote, or None when nobody is configured."""
    recipients = _notify_emails()
    if not recipients:
        return None
    block = read_notes_block(quote.notes) or {}
    reasons = block.get("review") or []
    delivery = block.get("delivery") or {}
    invoice = block.get("invoice") or {}
    name = _name(quote)
    wa_number = re.sub(r"\D", "", str(quote.customer_phone or ""))

    if reasons:
        subject = f"🔔 Cotización web por revisar {quote.quote_number}: {name} — {_money(quote.total)}"
        heading = "Cotización web por revisar"
        missing = "; ".join(REVIEW_LABELS.get(r, r) for r in reasons)
        callout = (
            '<p style="background:#FEF3C7;border:1px solid #FCD34D;border-radius:8px;padding:12px;">'
            f"<strong>Falta:</strong> {_h(missing)}.<br>Complétala en el admin y pulsa "
            "<strong>Enviar</strong>: el cliente recibe el enlace para pagar.</p>"
        )
    else:
        subject = (
            f"🛒 Cotización web {quote.quote_number}: {name} — {_money(quote.total)}"
        )
        heading = "Nueva cotización en la tienda"
        callout = (
            '<p style="background:#ECFDF5;border:1px solid #A7F3D0;border-radius:8px;padding:12px;">'
            "El cliente ya puede pagarla en línea. Dale seguimiento por WhatsApp.</p>"
        )

    rows = [
        ("Cliente", name),
        ("Teléfono", quote.customer_phone),
        ("Correo", quote.customer_email),
        ("Ubicación", quote.customer_location),
        ("Entrega", DELIVERY_NAMES.get(delivery.get("method"), delivery.get("method"))),
        ("Dirección", _address_text(delivery.get("address"))),
        (
            "Factura",
            (
                f"Sí · {invoice.get('rfc') or ''} {invoice.get('razon_social') or ''}".strip()
                if invoice.get("requires_invoice")
                else None
            ),
        ),
        ("Comentarios", _buyer_comments(quote)),
    ]
    facts = "".join(
        f'<tr><td style="color:#888;padding:2px 12px 2px 0;vertical-align:top;">{label}</td>'
        f'<td style="white-space:pre-wrap;">{_h(value)}</td></tr>'
        for label, value in rows
        if value
    )
    buttons = _button(admin_url(quote), "Abrir en el admin")
    if wa_number:
        buttons += _button(
            f"https://wa.me/{wa_number}", "WhatsApp al cliente", "#25D366"
        )
    buttons += _button(public_url(quote), "Ver como el cliente", "#555")
    html = _page(
        f'<h2 style="margin:0 0 4px;">{heading}</h2>'
        f'<p style="color:#888;margin:0 0 12px;">{_h(quote.quote_number)}</p>'
        f"{callout}"
        f'<table style="font-size:14px;margin:12px 0;">{facts}</table>'
        f"{_items_rows(quote)}"
        f'<div style="margin-top:16px;">{buttons}</div>'
    )
    return {"from": _from(), "to": recipients, "subject": subject, "html": html}


def build_buyer_created(quote: Quote) -> dict | None:
    """The buyer's copy of a quote they just requested, or None (no email)."""
    to = _buyer_email(quote)
    if not to:
        return None
    block = read_notes_block(quote.notes) or {}
    reasons = block.get("review") or []
    name = _name(quote).split(" ")[0]
    if reasons:
        subject = f"Recibimos tu solicitud de cotización {quote.quote_number}"
        todo = " y ".join(
            BUYER_REVIEW_LABELS.get(r, "revisa tu solicitud") for r in reasons
        )
        intro = (
            f"Recibimos tu solicitud. Un ingeniero {_h(todo)} y te avisa por WhatsApp, "
            "normalmente el mismo día hábil. Cuando esté lista, este mismo enlace "
            "mostrará el botón para pagar en línea."
        )
        label = "Ver mi cotización"
    else:
        subject = f"Tu cotización {quote.quote_number} de Todo Para El Campo"
        intro = (
            f"Aquí está tu cotización por <strong>{_money(quote.total)} MXN</strong> (IVA incluido)"
            f"{_validity(quote)}. Puedes revisarla y pagarla en línea con Mercado Pago "
            "(tarjeta, SPEI u OXXO)."
        )
        label = "Ver y pagar mi cotización"
    return _buyer_message(quote, to, subject, name, intro, label)


def build_buyer_ready(quote: Quote) -> dict | None:
    """Staff completed and sent a web quote: it can be paid now."""
    to = _buyer_email(quote)
    if not to or not is_web_quote(quote):
        return None
    name = _name(quote).split(" ")[0]
    intro = (
        f"Tu cotización está lista: <strong>{_money(quote.total)} MXN</strong> (IVA incluido)"
        f"{_validity(quote)}. Ya puedes pagarla en línea con Mercado Pago "
        "(tarjeta, SPEI u OXXO)."
    )
    subject = f"Tu cotización {quote.quote_number} está lista para pagar"
    return _buyer_message(quote, to, subject, name, intro, "Ver y pagar mi cotización")


def _validity(quote: Quote) -> str:
    until = expires_at(quote)
    if not until:
        return ""
    return f", válida hasta el {until.astimezone(BUSINESS_TZ):%d/%m/%Y}"


def _buyer_message(quote, to, subject, first_name, intro, label) -> dict:
    html = _page(
        f"<p>Hola{' ' + _h(first_name) if first_name else ''}:</p><p>{intro}</p>"
        f"{_button(public_url(quote), label)}"
        f"{_items_rows(quote)}"
        '<p style="font-size:14px;">¿Dudas? Escríbenos por '
        f'<a href="https://wa.me/{STORE_WHATSAPP}">WhatsApp</a> o responde a este correo.</p>'
        f"{_seller_footer()}"
    )
    return {
        "from": _from(),
        "to": [to],
        "reply_to": REPLY_TO,
        "subject": subject,
        "html": html,
    }


# ── queueing (called by the routes after their commit) ───────────────────────


def queue_created(db: Session, quote_number: str, tasks: BackgroundTasks) -> None:
    """Queue the staff alert and the buyer's copy of a new web quote."""
    try:
        quote = (
            db.query(Quote)
            .options(joinedload(Quote.items))
            .filter(Quote.quote_number == quote_number)
            .first()
        )
        if quote is None:
            return
        staff = build_staff_alert(quote)
        if staff:
            tasks.add_task(
                mailer.send,
                staff,
                tag=f"web quote {quote_number} staff alert",
                idempotency_key=f"web-quote/{quote_number}/staff",
            )
        buyer = build_buyer_created(quote)
        if buyer:
            tasks.add_task(
                mailer.send,
                buyer,
                tag=f"web quote {quote_number} buyer copy",
                idempotency_key=f"web-quote/{quote_number}/buyer",
            )
    except Exception as exc:  # noqa: BLE001 - the quote is saved; email is extra
        logger.error(
            "web quote %s: emails not queued: %s", quote_number, type(exc).__name__
        )


def queue_ready(quote: Quote, tasks: BackgroundTasks) -> None:
    """Queue the buyer's "lista para pagar" when staff send a web quote."""
    try:
        message = build_buyer_ready(quote)
        if message:
            sent = quote.sent_at.isoformat() if quote.sent_at else ""
            tasks.add_task(
                mailer.send,
                message,
                tag=f"web quote {quote.quote_number} ready",
                idempotency_key=f"web-quote/{quote.quote_number}/ready/{sent}",
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "web quote %s: ready email not queued: %s",
            quote.quote_number,
            type(exc).__name__,
        )
