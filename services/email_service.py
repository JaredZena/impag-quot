"""Email notification service for quote events using Resend."""
import os
import re
from html import escape

from services import mailer

FROM_EMAIL = os.getenv("QUOTE_FROM_EMAIL", "cotizaciones@todoparaelcampo.com.mx")


def _h(value) -> str:
    return escape("" if value is None else str(value))


def build_quote_notification_email(quote, event_type: str):
    """(subject, html) for a viewed/accepted quote, or None for other events.

    Every quote field is escaped: the customer's name, phone and location can
    be text a web buyer typed (routes/storefront_orders.py)."""
    name = " ".join(str(quote.customer_name or "").split())
    location_row = (
        f'<tr><td style="color:#888;">Ubicación:</td><td>{_h(quote.customer_location)}</td></tr>'
        if quote.customer_location
        else ""
    )
    common_rows = f"""<tr><td style="color:#888;">Teléfono:</td><td>{_h(quote.customer_phone)}</td></tr>
                    {location_row}"""
    if event_type == "viewed":
        subject = f"👁️ {name} vio tu cotización {quote.quote_number}"
        body = f"""
            <div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;">
                <h2 style="color:#1a1a1a;">Cotización Vista</h2>
                <p><strong>{_h(name)}</strong> abrió la cotización <strong>{_h(quote.quote_number)}</strong>.</p>
                <table style="width:100%;margin:16px 0;">
                    <tr><td style="color:#888;">Total:</td><td style="font-weight:700;">${float(quote.total):,.2f} MXN</td></tr>
                    {common_rows}
                </table>
                <p style="color:#666;font-size:14px;">Es buen momento para dar seguimiento.</p>
            </div>
            """
    elif event_type == "accepted":
        subject = f"✅ {name} aceptó tu cotización {quote.quote_number}"
        # Digits only: a web buyer's phone is free text until validated.
        wa_number = re.sub(r"\D", "", str(quote.customer_phone or ""))
        body = f"""
            <div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;">
                <h2 style="color:#2E7D32;">¡Cotización Aceptada!</h2>
                <p><strong>{_h(name)}</strong> aceptó la cotización <strong>{_h(quote.quote_number)}</strong>.</p>
                <table style="width:100%;margin:16px 0;">
                    <tr><td style="color:#888;">Total:</td><td style="font-weight:700;font-size:20px;">${float(quote.total):,.2f} MXN</td></tr>
                    {common_rows}
                </table>
                <p style="font-weight:600;">Contacta al cliente para coordinar pago y entrega.</p>
                <a href="https://wa.me/{wa_number}" style="display:inline-block;background:#25D366;color:#fff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;margin-top:12px;">
                    WhatsApp al cliente
                </a>
            </div>
            """
    else:
        return None
    return subject, body


def send_quote_notification_email(engineer_email: str, quote, event_type: str):
    """Email the engineer when a quote is viewed or accepted (services/mailer.py
    picks Resend or Gmail; nothing is sent while neither is configured)."""
    if not mailer.configured():
        print(f"[EMAIL] Skipping email (no email transport): {event_type} for quote {quote.quote_number}")
        return
    built = build_quote_notification_email(quote, event_type)
    if built is None:
        return
    subject, body = built
    mailer.send(
        {"from": FROM_EMAIL, "to": [engineer_email], "subject": subject, "html": body},
        tag=f"quote {quote.quote_number} {event_type} notification",
    )
