from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timezone, timedelta
from models import get_db, Quote, Notification

router = APIRouter(prefix="/public/quote", tags=["public"])

WHATSAPP_NUMBER = "526771197737"
IVA_RATE = 0.16


def render_quote_page(quote, status_message=None, show_accept=True):
    """Render the customer-facing quote HTML."""
    items_html = ""
    for item in sorted(quote.items, key=lambda x: x.sort_order):
        line_total = float(item.quantity) * float(item.unit_price)
        iva_badge = ""
        if item.iva_applicable:
            iva_badge = '<span style="font-size:11px;color:#666;margin-left:4px;">+ IVA</span>'
        items_html += f"""
        <tr>
            <td style="padding:12px 8px;border-bottom:1px solid #eee;">
                <strong>{item.description}</strong>
                {f'<br><span style="font-size:12px;color:#888;">SKU: {item.sku}</span>' if item.sku else ''}
                {f'<br><span style="font-size:12px;color:#666;">{item.notes}</span>' if item.notes else ''}
            </td>
            <td style="padding:12px 8px;border-bottom:1px solid #eee;text-align:center;">{float(item.quantity):g} {item.unit or ''}</td>
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
    if show_accept and quote.status in ("sent", "viewed"):
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
        status_banner = f'<div style="background:#E8F5E9;border:1px solid #4CAF50;border-radius:8px;padding:16px;text-align:center;margin-bottom:24px;font-weight:600;color:#2E7D32;">{status_message}</div>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cotización {quote.quote_number} | IMPAG</title>
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
                    <div style="font-size:20px;font-weight:700;">{quote.quote_number}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:13px;color:#666;">Fecha: {quote.sent_at.strftime('%d/%m/%Y') if quote.sent_at else quote.created_at.strftime('%d/%m/%Y')}</div>
                    {f'<div style="font-size:13px;color:#666;">Válida hasta: {expiry_date}</div>' if expiry_date else ''}
                </div>
            </div>

            <div style="background:#fafafa;border-radius:8px;padding:12px;margin-bottom:20px;">
                <div style="font-size:13px;color:#888;">Para:</div>
                <div style="font-weight:600;">{quote.customer_name}</div>
                {f'<div style="font-size:13px;color:#666;">{quote.customer_location}</div>' if quote.customer_location else ''}
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

        {f'<div class="card"><p style="font-size:14px;color:#555;white-space:pre-wrap;">{quote.notes}</p></div>' if quote.notes else ''}

        {accept_button}

        <div style="text-align:center;margin:24px 0;">
            <a href="https://wa.me/{WHATSAPP_NUMBER}?text=Hola%2C%20tengo%20una%20pregunta%20sobre%20la%20cotización%20{quote.quote_number}" class="whatsapp">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/><path d="M12 0C5.373 0 0 5.373 0 12c0 2.625.846 5.059 2.284 7.034L.789 23.492l4.644-1.217A11.95 11.95 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm0 21.75c-2.115 0-4.13-.657-5.828-1.9l-.418-.25-2.756.723.735-2.686-.274-.436A9.724 9.724 0 012.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75z"/></svg>
                Contactar al ingeniero
            </a>
        </div>

        <div class="footer">
            <p>Atendido por: {engineer_display}</p>
            <p style="margin-top:8px;">IMPAG TECH S.A.P.I. de C.V. | RFC: ITE210716D9A</p>
            <p>Nuevo Ideal, Durango | Texcoco, Edo. de México | Durango, Dgo.</p>
            <p style="margin-top:8px;">WhatsApp: +52 677 119 7737 | impagtodoparaelcampo@gmail.com</p>
        </div>
    </div>
</body>
</html>"""


@router.get("/{access_token}", response_class=HTMLResponse)
def view_quote(access_token: str, db: Session = Depends(get_db)):
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
    if quote.status not in ("accepted", "rejected"):
        if quote.sent_at and quote.validity_days:
            expiry = quote.sent_at + timedelta(days=quote.validity_days)
            if datetime.now(timezone.utc) > expiry:
                quote.status = "expired"
                quote.expired_at = datetime.now(timezone.utc)
                db.commit()

    if quote.status == "expired":
        return HTMLResponse(content=render_expired(quote))

    if quote.status == "accepted":
        return HTMLResponse(
            content=render_quote_page(
                quote,
                status_message=f"Cotización aceptada el {quote.accepted_at.strftime('%d/%m/%Y') if quote.accepted_at else ''}",
                show_accept=False,
            )
        )

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
        <p style="color:#666;margin-bottom:8px;">La cotización <strong>{quote.quote_number}</strong> ha expirado.</p>
        <p style="color:#666;">Contacte a su ingeniero para una cotización actualizada.</p>
        <a href="https://wa.me/{WHATSAPP_NUMBER}?text=Hola%2C%20mi%20cotización%20{quote.quote_number}%20expiró.%20¿Podrían%20actualizarla?" class="whatsapp">
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
