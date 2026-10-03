"""
Buyer confirmation email for web orders (todoparaelcampo.com.mx checkout).

When a web order's Mercado Pago payment is approved for the full amount,
services/web_orders.py queues ONE confirmation to the buyer. It is the
consumer's written record of the purchase (LFPC art. 52: provider name and
address, the goods, the price and the warranties) and it carries:
- the warranty póliza (LFPC arts. 77-78: scope, duration, conditions, how to
  claim and the address for claims);
- the 5-business-day revocation right (LFPC art. 56);
- the invoice status.
The wording mirrors the storefront's policy pages (legal.md §5.2-5.4). Change
both together.

It stays dark until configured. Nothing is sent unless RESEND_API_KEY and
WEB_ORDER_STORE_ADDRESS (the store's physical address for pickups, claims and
warranties) are set. Without them, the paid order's fulfillment task tells
staff to send the order summary and the póliza themselves.

Sending runs after the database commit, in a FastAPI background task. It has
an explicit timeout and a per-order Resend Idempotency-Key, so a slow email
provider never delays the Mercado Pago webhook and a retry never sends twice.
"""

import logging
import os
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

import requests

from services import mailer

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
SEND_TIMEOUT_SECONDS = 10
BUSINESS_TZ = ZoneInfo("America/Mexico_City")
DEFAULT_FROM = "Todo Para El Campo <ventas@todoparaelcampo.com.mx>"

# Seller facts as the storefront shows them (src/lib/store.ts, STORE).
SELLER_LEGAL_NAME = "IMPAG TECH S.A.P.I. de C.V."
SELLER_TRADE_NAME = "Todo Para El Campo"
SELLER_RFC = "ITE210716D9A"
SELLER_PHONE = "+52 677 119 7737"
SELLER_EMAIL = "ventas@todoparaelcampo.com.mx"
WARRANTY_DAYS = 90  # LFPC art. 77: never less than 90 days from delivery
REVOCATION_BUSINESS_DAYS = 5  # LFPC art. 56

DELIVERY_NAMES = {
    "recoger": "Recoger en tienda",
    "paqueteria": "Envío por paquetería",
    "flete": "Envío por flete",
}

CENT = Decimal("0.01")


# ── configuration (read per call, so App Runner changes apply without a deploy)


def _env(name: str) -> str | None:
    return (os.getenv(name) or "").strip() or None


def store_address() -> str | None:
    return _env("WEB_ORDER_STORE_ADDRESS")


def return_address() -> str | None:
    return _env("WEB_ORDER_RETURN_ADDRESS") or store_address()


def missing_config() -> list[str]:
    """Env vars the confirmation still needs (empty when it can be sent)."""
    missing = []
    if not mailer.configured():
        missing.append("RESEND_API_KEY o GMAIL_SMTP_APP_PASSWORD")
    if not store_address():
        missing.append("WEB_ORDER_STORE_ADDRESS")
    return missing


# ── formatting ───────────────────────────────────────────────────────────────


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Any) -> str:
    return f"${_dec(value).quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"


def _qty(value: Any) -> str:
    return format(_dec(value).normalize(), "f")


def _rate(value: Any) -> str:
    return "IVA 16% incluido" if _dec(value) > 0 else "tasa 0% de IVA"


def _line_text(line: dict) -> str:
    unit = f" ({line['unit_label']})" if line.get("unit_label") else ""
    return (
        f"{_qty(line['quantity'])} × {line['description']}{unit}: "
        f"{_money(line['unit_total'])} c/u ({_rate(line['iva_rate'])}) = "
        f"{_money(line['line_total'])}"
    )


# ── the message ──────────────────────────────────────────────────────────────


def build_buyer_confirmation(
    *,
    ref: str,
    to: str,
    customer_name: str,
    lines: list[dict],
    subtotal: Any,
    iva_amount: Any,
    total: Any,
    delivery_method: str | None,
    delivery_address: str | None,
    invoice: Any,
    payment_label: str,
    payment_id: str,
    paid_at: datetime | None,
) -> dict:
    """The confirmation for one paid order: {ref, to, from, reply_to, subject,
    text, html}. `lines` are the notes block's charged lines (IVA included)."""
    store = store_address() or ""
    returns = return_address() or store

    intro = [
        f"Hola {customer_name}:",
        (
            f"Recibimos tu pago del pedido {ref}. Este correo es tu comprobante "
            "de compra y tu póliza de garantía: guárdalo."
        ),
    ]
    facts = [f"Pedido: {ref}"]
    if paid_at is not None:
        stamp = paid_at.astimezone(BUSINESS_TZ).strftime("%d/%m/%Y %H:%M")
        facts.append(f"Fecha de pago: {stamp} (hora del centro de México)")
    facts.append(
        f"Pagado con: Mercado Pago · {payment_label} · operación #{payment_id}"
    )
    totals = [
        ("Subtotal sin IVA", _money(subtotal)),
        ("IVA", _money(iva_amount)),
        ("Total pagado", f"{_money(total)} MXN (IVA incluido)"),
    ]

    method_name = DELIVERY_NAMES.get(delivery_method or "", "Entrega")
    if delivery_method == "recoger":
        delivery = (
            f"{method_name}: {store}. Te avisamos por WhatsApp cuando tu pedido "
            "esté listo."
        )
    elif delivery_address:
        delivery = (
            f"{method_name} a: {delivery_address}. Te contactamos por WhatsApp "
            "para confirmar la fecha de entrega."
        )
    else:
        delivery = (
            f"{method_name}. Te contactamos por WhatsApp para coordinar la entrega."
        )

    if isinstance(invoice, dict) and invoice.get("requires_invoice") is True:
        invoice_text = (
            "Pediste factura (CFDI 4.0) a nombre de "
            f"{invoice.get('razon_social') or '—'}, RFC {invoice.get('rfc') or '—'}. "
            f"Te la enviamos a {invoice.get('email') or to}."
        )
    else:
        invoice_text = (
            "No pediste factura, así que tu compra se incluye en la factura global "
            "a público en general. Si la necesitas, envía tu número de pedido y tu "
            f"Constancia de Situación Fiscal a {SELLER_EMAIL} o por WhatsApp al "
            f"{SELLER_PHONE} a más tardar el último día del mes en que compraste."
        )

    sections = [
        ("Entrega", [delivery], False),
        ("Factura", [invoice_text], False),
        (
            "Cancelación de tu compra",
            [
                (
                    f"Tienes {REVOCATION_BUSINESS_DAYS} días hábiles contados a partir "
                    "de que recibes tu pedido para revocar tu compra sin "
                    "responsabilidad (artículo 56 de la Ley Federal de Protección al "
                    "Consumidor)."
                ),
                (
                    f"Avísanos por WhatsApp al {SELLER_PHONE} o a {SELLER_EMAIL} con "
                    "tu número de pedido, o devuélvenos el producto completo en "
                    f"{returns}. La fecha de tu aviso es la que cuenta."
                ),
                (
                    "Te reembolsamos el precio pagado al mismo medio de pago. El "
                    "costo del envío de regreso corre por tu cuenta, como marca la ley."
                ),
                (
                    "¿Aún no enviamos tu pedido? Puedes cancelarlo sin costo y te "
                    "devolvemos el total, incluido el envío."
                ),
            ],
            True,
        ),
        (
            "Póliza de garantía",
            [
                (
                    f"{SELLER_LEGAL_NAME} garantiza los productos de este pedido por "
                    f"un mínimo de {WARRANTY_DAYS} días contra defectos de "
                    "fabricación, contados desde la entrega (artículo 77 de la Ley "
                    "Federal de Protección al Consumidor)."
                ),
                (
                    "Si un producto llega dañado, con defecto o no corresponde a su "
                    "descripción, eliges reposición o reembolso, y nosotros pagamos "
                    "el envío."
                ),
                (
                    "Insumos (charolas, trampas, sustratos y similares): garantizamos "
                    "que el producto corresponde a su etiqueta y especificaciones; el "
                    "resultado en campo depende del manejo y de las condiciones del "
                    "cultivo."
                ),
                (
                    "Cómo hacerla válida: envíanos tu número de pedido, fotos o video "
                    f"y una breve descripción por WhatsApp al {SELLER_PHONE} o a "
                    f"{SELLER_EMAIL}. Si necesitamos revisar el producto, coordinamos "
                    "la recolección o el envío sin costo para ti."
                ),
                f"Domicilio para hacer válida la garantía: {returns}.",
                (
                    "El tiempo de reparación no cuenta dentro del plazo de garantía; "
                    "si reponemos el producto, la garantía empieza de nuevo."
                ),
                (
                    "Si no quedas satisfecho, escríbenos primero. También puedes "
                    "acudir a la Procuraduría Federal del Consumidor (PROFECO)."
                ),
            ],
            True,
        ),
    ]
    seller = [
        f"Vendedor: {SELLER_LEGAL_NAME} ({SELLER_TRADE_NAME}) · RFC {SELLER_RFC}",
        f"Domicilio: {store}",
        f"Tel./WhatsApp {SELLER_PHONE} · {SELLER_EMAIL}",
    ]

    return {
        "ref": ref,
        "to": to,
        "from": _env("WEB_ORDER_FROM_EMAIL") or DEFAULT_FROM,
        "reply_to": SELLER_EMAIL,
        "subject": f"Confirmación de tu pedido {ref} · {SELLER_TRADE_NAME}",
        "text": _text(intro, facts, lines, totals, sections, seller),
        "html": _html(ref, intro, facts, lines, totals, sections, seller),
    }


def _text(intro, facts, lines, totals, sections, seller) -> str:
    out = [*intro, "", *facts, "", "Productos"]
    out += [f"- {_line_text(line)}" for line in lines]
    out += ["", *(f"{label}: {value}" for label, value in totals)]
    for title, paragraphs, as_list in sections:
        out += ["", title]
        out += [f"- {p}" if as_list else p for p in paragraphs]
    out += ["", *seller]
    return "\n".join(out)


def _html(ref, intro, facts, lines, totals, sections, seller) -> str:
    """Every value is escaped: names and addresses are buyer-typed text."""
    h = escape
    cell = "padding:8px;border-bottom:1px solid #eee;"
    parts = [
        (
            '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,'
            'Roboto,sans-serif;max-width:600px;margin:0 auto;color:#1a1a1a;">'
        ),
        f'<h1 style="font-size:20px;">Pedido {h(ref)} pagado</h1>',
        *(f"<p>{h(p)}</p>" for p in intro),
        f"<p>{'<br>'.join(h(f) for f in facts)}</p>",
        '<table style="width:100%;border-collapse:collapse;font-size:14px;">',
        (
            "<thead><tr>"
            f'<th style="{cell}text-align:left;">Producto</th>'
            f'<th style="{cell}text-align:center;">Cant.</th>'
            f'<th style="{cell}text-align:right;">Precio unitario</th>'
            f'<th style="{cell}text-align:right;">Importe</th>'
            "</tr></thead><tbody>"
        ),
    ]
    for line in lines:
        unit = f" ({line['unit_label']})" if line.get("unit_label") else ""
        parts.append(
            "<tr>"
            f'<td style="{cell}">{h(str(line["description"]) + unit)}</td>'
            f'<td style="{cell}text-align:center;">{h(_qty(line["quantity"]))}</td>'
            f'<td style="{cell}text-align:right;">{h(_money(line["unit_total"]))}'
            f'<br><span style="color:#888;font-size:12px;">{h(_rate(line["iva_rate"]))}'
            "</span></td>"
            f'<td style="{cell}text-align:right;">{h(_money(line["line_total"]))}</td>'
            "</tr>"
        )
    parts.append("</tbody></table>")
    parts.append('<p style="text-align:right;">')
    parts.append(
        "<br>".join(
            f"{h(label)}: <strong>{h(value)}</strong>" for label, value in totals
        )
    )
    parts.append("</p>")
    for title, paragraphs, as_list in sections:
        parts.append(f'<h2 style="font-size:16px;margin-top:24px;">{h(title)}</h2>')
        if as_list:
            parts.append(
                "<ul>" + "".join(f"<li>{h(p)}</li>" for p in paragraphs) + "</ul>"
            )
        else:
            parts += [f"<p>{h(p)}</p>" for p in paragraphs]
    parts.append(
        '<p style="color:#666;font-size:13px;margin-top:24px;">'
        + "<br>".join(h(s) for s in seller)
        + "</p></div>"
    )
    return "".join(parts)


# ── sending ──────────────────────────────────────────────────────────────────


def send_buyer_confirmation(message: dict) -> bool:
    """POST the confirmation to Resend. Never raises: this runs in a background
    task after the order is committed. Logs the reference, never the address
    or the key."""
    ref = message.get("ref")
    api_key = _env("RESEND_API_KEY")
    if not api_key and mailer.transport() == "gmail":
        return mailer.send(
            {**message, "to": [message["to"]]},
            tag=f"web order {ref} buyer confirmation",
        )
    if not api_key:
        logger.warning(
            "web order %s: buyer confirmation not sent, RESEND_API_KEY unset", ref
        )
        return False
    try:
        response = requests.post(
            RESEND_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                # A retried order (or a re-queued confirmation) never sends twice.
                "Idempotency-Key": f"web-order-confirmation/{ref}",
            },
            json={
                "from": message["from"],
                "to": [message["to"]],
                "reply_to": message["reply_to"],
                "subject": message["subject"],
                "text": message["text"],
                "html": message["html"],
            },
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a background task must never raise
        logger.error(
            "web order %s: buyer confirmation failed: %s", ref, type(exc).__name__
        )
        return False
    if response.status_code >= 300:
        logger.error(
            "web order %s: buyer confirmation rejected by Resend (HTTP %s)",
            ref,
            response.status_code,
        )
        return False
    logger.info("web order %s: buyer confirmation sent", ref)
    return True
