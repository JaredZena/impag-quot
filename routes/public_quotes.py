from html import escape
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timezone, timedelta
from models import get_db, Quote, Notification
from services.web_orders import NOTES_BLOCK_RE
from services.web_quotes import is_web_quote

router = APIRouter(prefix="/public/quote", tags=["public"])

WHATSAPP_NUMBER = "526771197737"
IVA_RATE = 0.16


def _h(value):
    """HTML-escape one value for these pages. Quote fields can hold text a web
    buyer typed (routes/storefront_orders.py), and this page is served on the
    storefront's own domain."""
    return escape("" if value is None else str(value))


# Web quotes carry machine notes for staff: the "[Pedido web]" JSON block in
# quote.notes and "Tienda en línea: <handle> …" on each item. The buyer sees
# only the human text around them.
STAFF_ITEM_NOTE_PREFIX = "Tienda en línea:"


def _public_notes(notes):
    return NOTES_BLOCK_RE.sub("", notes or "").strip() or None


def _public_item_note(note):
    return None if (note or "").startswith(STAFF_ITEM_NOTE_PREFIX) else note


# ?pago=<state> on the public page: where the storefront's /api/quote-checkout
# and Mercado Pago's back_urls send the buyer. Anything else is ignored.
PAY_RETURN_MESSAGES = {
    "exito": ("ok", "¡Gracias! Recibimos tu pago. En cuanto Mercado Pago lo confirme te escribimos para coordinar la entrega."),
    "pendiente": ("wait", "Tu pago está en proceso (OXXO, SPEI o revisión). Te avisamos en cuanto se acredite."),
    "error": ("bad", "El pago no se completó. Puedes intentarlo de nuevo o escribirnos por WhatsApp."),
    "no-disponible": ("bad", "No pudimos iniciar el pago en este momento. Intenta de nuevo en unos minutos o escríbenos por WhatsApp."),
}
BANNER_STYLES = {
    "ok": "background:#E8F5E9;border:1px solid #4CAF50;color:#2E7D32;",
    "wait": "background:#FFF8E1;border:1px solid #FFB300;color:#8D6E00;",
    "bad": "background:#FFEBEE;border:1px solid #E53935;color:#B71C1C;",
}
# Payments in flight or done: a web quote with one of these never expires.
PAYMENT_HOLDS = ("pending", "approved", "mismatch")


def _web_quote_state(quote):
    """(banner kind, message) for a storefront quote from its own state, or None."""
    payment_status = getattr(quote, "payment_status", None)
    if payment_status == "approved":
        return "ok", "Cotización pagada. Gracias por tu compra: te contactamos para coordinar la entrega."
    if payment_status == "pending":
        return "wait", "Tu pago está en proceso. Te avisamos en cuanto se acredite."
    if payment_status == "mismatch":
        return "wait", "Recibimos tu pago y lo estamos verificando. Te contactamos en breve."
    if quote.status == "draft":
        return "wait", (
            "Tu cotización está en revisión: un ingeniero confirma precios y envío y te avisa "
            "por WhatsApp. Este mismo enlace mostrará el botón de pago cuando esté lista."
        )
    return None


def _pay_form(quote):
    total = f"${float(quote.total):,.2f}"
    return f"""
        <form method="POST" action="/api/quote-checkout" style="text-align:center;margin:32px 0;">
            <input type="hidden" name="token" value="{_h(quote.access_token)}">
            <button type="submit" style="background:linear-gradient(135deg,#4CAF50,#00897B);color:#fff;border:none;padding:16px 40px;font-size:18px;font-weight:700;border-radius:8px;cursor:pointer;letter-spacing:0.5px;">
                ACEPTAR Y PAGAR {total} MXN
            </button>
            <p style="font-size:13px;color:#666;margin-top:12px;">
                Pago seguro con Mercado Pago: tarjeta, transferencia SPEI u OXXO.<br>
                Al pagar aceptas esta cotización y los <a href="/terminos" style="color:#00897B;">términos y condiciones</a>.
            </p>
        </form>"""


def render_quote_page(quote, status_message=None, show_accept=True, pay_return=None):
    """Render the customer-facing quote HTML."""
    notes = _public_notes(quote.notes)
    items_html = ""
    for item in sorted(quote.items, key=lambda x: x.sort_order):
        line_total = float(item.quantity) * float(item.unit_price)
        iva_badge = ""
        item_note = _public_item_note(item.notes)
        if item.iva_applicable:
            iva_badge = '<span style="font-size:11px;color:#666;margin-left:4px;">+ IVA</span>'
        items_html += f"""
        <tr>
            <td style="padding:12px 8px;border-bottom:1px solid #eee;">
                <strong>{_h(item.description)}</strong>
                {f'<br><span style="font-size:12px;color:#888;">SKU: {_h(item.sku)}</span>' if item.sku else ''}
                {f'<br><span style="font-size:12px;color:#666;">{_h(item_note)}</span>' if item_note else ''}
            </td>
            <td style="padding:12px 8px;border-bottom:1px solid #eee;text-align:center;">{float(item.quantity):g} {_h(item.unit)}</td>
            <td style="padding:12px 8px;border-bottom:1px solid #eee;text-align:right;">${float(item.unit_price):,.2f}{iva_badge}</td>
            <td style="padding:12px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:600;">${line_total:,.2f}</td>
        </tr>"""

    expiry_date = ""
    if quote.sent_at and quote.validity_days:
        exp = quote.sent_at + timedelta(days=quote.validity_days)
        expiry_date = exp.strftime("%d/%m/%Y")

    engineer_name = quote.assigned_to or quote.created_by
    engineer_display = engineer_name.split("@")[0].replace(".", " ").title() if engineer_name else "IMPAG"

    accept_button = ""
    web = is_web_quote(quote)
    payable = (
        web
        and quote.status in ("sent", "viewed")
        and getattr(quote, "payment_status", None) not in PAYMENT_HOLDS
        and bool(quote.items)
        and all(float(i.unit_price) > 0 for i in quote.items)
        and float(quote.total) > 0
    )
    if web:
        state = _web_quote_state(quote)
        if pay_return in PAY_RETURN_MESSAGES and not (state and state[0] == "ok"):
            state = PAY_RETURN_MESSAGES[pay_return]
        if state and not status_message:
            status_message = state
        if payable and show_accept:
            accept_button = _pay_form(quote)
    elif show_accept and quote.status in ("sent", "viewed"):
        accept_button = f"""
        <form method="POST" style="text-align:center;margin:32px 0;">
            <p style="font-size:14px;color:#666;margin-bottom:16px;">
                Al aceptar, un ingeniero se pondrá en contacto para coordinar el pago y entrega.
            </p>
            <button type="submit" style="background:linear-gradient(135deg,#4CAF50,#00897B);color:#fff;border:none;padding:16px 48px;font-size:18px;font-weight:700;border-radius:8px;cursor:pointer;letter-spacing:0.5px;">
                ACEPTAR COTIZACIÓN
            </button>
        </form>"""

    status_banner = ""
    if status_message:
        kind, text = status_message if isinstance(status_message, tuple) else ("ok", status_message)
        status_banner = f'<div style="{BANNER_STYLES.get(kind, BANNER_STYLES["ok"])}border-radius:8px;padding:16px;text-align:center;margin-bottom:24px;font-weight:600;">{_h(text)}</div>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cotización {_h(quote.quote_number)} | IMPAG</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; color:#1a1a1a; background:#f5f5f5; }}
        .container {{ max-width:680px; margin:0 auto; padding:16px; }}
        .card {{ background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,0.08); padding:24px; margin-bottom:16px; }}
        .header {{ text-align:center; padding:24px 0; }}
        .logo {{ max-width:180px; height:auto; }}
        table {{ width:100%; border-collapse:collapse; }}
        th {{ text-align:left; padding:8px; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; color:#888; border-bottom:2px solid #e0e0e0; }}
        th:nth-child(2), th:nth-child(3), th:nth-child(4) {{ text-align:center; }}
        th:nth-child(3), th:nth-child(4) {{ text-align:right; }}
        .totals {{ margin-top:16px; text-align:right; }}
        .totals .row {{ display:flex; justify-content:flex-end; gap:24px; padding:6px 0; font-size:15px; }}
        .totals .total {{ font-size:20px; font-weight:700; color:#1a1a1a; border-top:2px solid #1a1a1a; padding-top:8px; margin-top:8px; }}
        .whatsapp {{ display:inline-flex; align-items:center; gap:8px; background:#25D366; color:#fff; text-decoration:none; padding:12px 24px; border-radius:8px; font-weight:600; font-size:15px; }}
        .footer {{ text-align:center; padding:24px 0; font-size:12px; color:#999; }}
        @media (max-width:480px) {{
            .card {{ padding:16px; }}
            table {{ font-size:13px; }}
            th, td {{ padding:8px 4px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <img src="/static/impag-logo.png" alt="IMPAG" class="logo">
        </div>

        {status_banner}

        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:20px;">
                <div>
                    <div style="font-size:12px;color:#888;text-transform:uppercase;letter-spacing:1px;">Cotización</div>
                    <div style="font-size:20px;font-weight:700;">{_h(quote.quote_number)}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:13px;color:#666;">Fecha: {quote.sent_at.strftime('%d/%m/%Y') if quote.sent_at else quote.created_at.strftime('%d/%m/%Y')}</div>
                    {f'<div style="font-size:13px;color:#666;">Válida hasta: {expiry_date}</div>' if expiry_date else ''}
                </div>
            </div>

            <div style="background:#fafafa;border-radius:8px;padding:12px;margin-bottom:20px;">
                <div style="font-size:13px;color:#888;">Para:</div>
                <div style="font-weight:600;">{_h(quote.customer_name)}</div>
                {f'<div style="font-size:13px;color:#666;">{_h(quote.customer_location)}</div>' if quote.customer_location else ''}
            </div>

            <table>
                <thead>
                    <tr>
                        <th>Descripción</th>
                        <th>Cant.</th>
                        <th>Precio Unit.</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                    {items_html}
                </tbody>
            </table>

            <div class="totals">
                <div class="row"><span style="color:#888;">Subtotal:</span> <span>${float(quote.subtotal):,.2f}</span></div>
                <div class="row"><span style="color:#888;">IVA (16%):</span> <span>${float(quote.iva_amount):,.2f}</span></div>
                <div class="row total"><span>Total MXN:</span> <span>${float(quote.total):,.2f}</span></div>
            </div>
        </div>

        {f'<div class="card"><p style="font-size:14px;color:#555;white-space:pre-wrap;">{_h(notes)}</p></div>' if notes else ''}

        {accept_button}

        <div style="text-align:center;margin:24px 0;">
            <a href="https://wa.me/{WHATSAPP_NUMBER}?text=Hola%2C%20tengo%20una%20pregunta%20sobre%20la%20cotización%20{url_quote(str(quote.quote_number), safe='')}" class="whatsapp">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/><path d="M12 0C5.373 0 0 5.373 0 12c0 2.625.846 5.059 2.284 7.034L.789 23.492l4.644-1.217A11.95 11.95 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm0 21.75c-2.115 0-4.13-.657-5.828-1.9l-.418-.25-2.756.723.735-2.686-.274-.436A9.724 9.724 0 012.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75z"/></svg>
                Contactar al ingeniero
            </a>
        </div>

        <div class="footer">
            <p>Atendido por: {_h(engineer_display)}</p>
            <p style="margin-top:8px;">IMPAG TECH S.A.P.I. de C.V. | RFC: ITE210716D9A</p>
            <p>Nuevo Ideal, Durango | Texcoco, Edo. de México | Durango, Dgo.</p>
            <p style="margin-top:8px;">WhatsApp: +52 677 119 7737 | impagtodoparaelcampo@gmail.com</p>
        </div>
    </div>
</body>
</html>"""


@router.get("/{access_token}", response_class=HTMLResponse)
def view_quote(access_token: str, pago: str | None = None, db: Session = Depends(get_db)):
    """Customer-facing quote view. No auth required."""
    quote = (
        db.query(Quote)
        .options(joinedload(Quote.items))
        .filter(Quote.access_token == access_token)
        .first()
    )

    if not quote:
        return HTMLResponse(content=render_not_found(), status_code=404)

    # Check if expired
    if quote.status not in ("accepted", "rejected") and quote.payment_status not in PAYMENT_HOLDS:
        if quote.sent_at and quote.validity_days:
            expiry = quote.sent_at + timedelta(days=quote.validity_days)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expiry:
                quote.status = "expired"
                quote.expired_at = datetime.now(timezone.utc)
                db.commit()

    if quote.status == "expired":
        return HTMLResponse(content=render_expired(quote))

    pay_return = pago if pago in PAY_RETURN_MESSAGES else None
    if quote.status == "accepted" and not is_web_quote(quote):
        return HTMLResponse(
            content=render_quote_page(
                quote,
                status_message=f"Cotización aceptada el {quote.accepted_at.strftime('%d/%m/%Y') if quote.accepted_at else ''}",
                show_accept=False,
            )
        )

    # A storefront quote: the buyer made it and is looking at it right now, so
    # a "viewed" notification is noise; payment events notify instead.
    if is_web_quote(quote):
        if not quote.viewed_at and quote.status == "sent":
            quote.viewed_at = datetime.now(timezone.utc)
            quote.status = "viewed"
            db.commit()
        return HTMLResponse(content=render_quote_page(quote, pay_return=pay_return))

    # Track first view
    if not quote.viewed_at and quote.status == "sent":
        quote.viewed_at = datetime.now(timezone.utc)
        quote.status = "viewed"
        db.commit()

        # Create notification
        engineer_email = quote.assigned_to or quote.created_by
        notification = Notification(
            recipient_email=engineer_email,
            quote_id=quote.id,
            event_type="quote_viewed",
            message=f"{quote.customer_name} vio la cotización {quote.quote_number}",
        )
        db.add(notification)
        db.commit()

        # Send email notification (async, don't block)
        try:
            from services.email_service import send_quote_notification_email
            send_quote_notification_email(engineer_email, quote, "viewed")
        except Exception:
            pass  # Don't fail the page render if email fails

    return HTMLResponse(content=render_quote_page(quote))


@router.post("/{access_token}", response_class=HTMLResponse)
def accept_quote(access_token: str, db: Session = Depends(get_db)):
    """Customer accepts a quote."""
    quote = (
        db.query(Quote)
        .options(joinedload(Quote.items))
        .filter(Quote.access_token == access_token)
        .first()
    )

    if not quote:
        return HTMLResponse(content=render_not_found(), status_code=404)

    # A storefront quote is accepted by paying it (services/web_orders.py marks
    # it accepted when Mercado Pago approves the payment).
    if is_web_quote(quote):
        return HTMLResponse(content=render_quote_page(quote))

    # Idempotent: if already accepted, just show success
    if quote.status == "accepted":
        return HTMLResponse(
            content=render_quote_page(
                quote,
                status_message=f"Cotización aceptada el {quote.accepted_at.strftime('%d/%m/%Y') if quote.accepted_at else ''}",
                show_accept=False,
            )
        )

    if quote.status == "expired":
        return HTMLResponse(content=render_expired(quote))

    # Accept
    quote.status = "accepted"
    quote.accepted_at = datetime.now(timezone.utc)
    db.commit()

    # Create notification
    engineer_email = quote.assigned_to or quote.created_by
    notification = Notification(
        recipient_email=engineer_email,
        quote_id=quote.id,
        event_type="quote_accepted",
        message=f"{quote.customer_name} aceptó la cotización {quote.quote_number} por ${float(quote.total):,.2f} MXN",
    )
    db.add(notification)
    db.commit()

    # Send email
    try:
        from services.email_service import send_quote_notification_email
        send_quote_notification_email(engineer_email, quote, "accepted")
    except Exception:
        pass

    return HTMLResponse(
        content=render_quote_page(
            quote,
            status_message="¡Cotización aceptada! Un ingeniero se pondrá en contacto contigo pronto.",
            show_accept=False,
        )
    )


def render_expired(quote):
    """Render expired quote page."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cotización Expirada | IMPAG</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#f5f5f5; display:flex; align-items:center; justify-content:center; min-height:100vh; padding:16px; }}
        .card {{ background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,0.08); padding:48px 32px; text-align:center; max-width:480px; }}
        .whatsapp {{ display:inline-flex; align-items:center; gap:8px; background:#25D366; color:#fff; text-decoration:none; padding:12px 24px; border-radius:8px; font-weight:600; margin-top:24px; }}
    </style>
</head>
<body>
    <div class="card">
        <img src="/static/impag-logo.png" alt="IMPAG" style="max-width:150px;margin-bottom:24px;">
        <h1 style="font-size:24px;margin-bottom:12px;">Cotización Expirada</h1>
        <p style="color:#666;margin-bottom:8px;">La cotización <strong>{_h(quote.quote_number)}</strong> ha expirado.</p>
        <p style="color:#666;">Contacte a su ingeniero para una cotización actualizada.</p>
        <a href="https://wa.me/{WHATSAPP_NUMBER}?text=Hola%2C%20mi%20cotización%20{url_quote(str(quote.quote_number), safe='')}%20expiró.%20¿Podrían%20actualizarla?" class="whatsapp">
            Solicitar nueva cotización
        </a>
    </div>
</body>
</html>"""


def render_not_found():
    """Render 404 page."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>No encontrada | IMPAG</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#f5f5f5; display:flex; align-items:center; justify-content:center; min-height:100vh; padding:16px; }}
        .card {{ background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,0.08); padding:48px 32px; text-align:center; max-width:480px; }}
        .whatsapp {{ display:inline-flex; align-items:center; gap:8px; background:#25D366; color:#fff; text-decoration:none; padding:12px 24px; border-radius:8px; font-weight:600; margin-top:24px; }}
    </style>
</head>
<body>
    <div class="card">
        <img src="/static/impag-logo.png" alt="IMPAG" style="max-width:150px;margin-bottom:24px;">
        <h1 style="font-size:24px;margin-bottom:12px;">Cotización No Encontrada</h1>
        <p style="color:#666;">El enlace no es válido o la cotización no existe.</p>
        <a href="https://wa.me/{WHATSAPP_NUMBER}" class="whatsapp">
            Contactar a IMPAG
        </a>
    </div>
</body>
</html>"""
