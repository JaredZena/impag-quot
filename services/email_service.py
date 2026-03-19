"""Email notification service for quote events using Resend."""
import os

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("QUOTE_FROM_EMAIL", "cotizaciones@todoparaelcampo.com.mx")


def send_quote_notification_email(engineer_email: str, quote, event_type: str):
    """Send an email notification to the engineer when a quote is viewed or accepted.

    Args:
        engineer_email: Engineer's email address
        quote: Quote model instance
        event_type: 'viewed' or 'accepted'
    """
    if not RESEND_API_KEY:
        print(f"[EMAIL] Skipping email (no RESEND_API_KEY): {event_type} for quote {quote.quote_number}")
        return

    try:
        import resend
        resend.api_key = RESEND_API_KEY

        if event_type == "viewed":
            subject = f"👁️ {quote.customer_name} vio tu cotización {quote.quote_number}"
            body = f"""
            <div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;">
                <h2 style="color:#1a1a1a;">Cotización Vista</h2>
                <p><strong>{quote.customer_name}</strong> abrió la cotización <strong>{quote.quote_number}</strong>.</p>
                <table style="width:100%;margin:16px 0;">
                    <tr><td style="color:#888;">Total:</td><td style="font-weight:700;">${float(quote.total):,.2f} MXN</td></tr>
                    <tr><td style="color:#888;">Teléfono:</td><td>{quote.customer_phone}</td></tr>
                    {f'<tr><td style="color:#888;">Ubicación:</td><td>{quote.customer_location}</td></tr>' if quote.customer_location else ''}
                </table>
                <p style="color:#666;font-size:14px;">Es buen momento para dar seguimiento.</p>
            </div>
            """
        elif event_type == "accepted":
            subject = f"✅ {quote.customer_name} aceptó tu cotización {quote.quote_number}"
            body = f"""
            <div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;">
                <h2 style="color:#2E7D32;">¡Cotización Aceptada!</h2>
                <p><strong>{quote.customer_name}</strong> aceptó la cotización <strong>{quote.quote_number}</strong>.</p>
                <table style="width:100%;margin:16px 0;">
                    <tr><td style="color:#888;">Total:</td><td style="font-weight:700;font-size:20px;">${float(quote.total):,.2f} MXN</td></tr>
                    <tr><td style="color:#888;">Teléfono:</td><td>{quote.customer_phone}</td></tr>
                    {f'<tr><td style="color:#888;">Ubicación:</td><td>{quote.customer_location}</td></tr>' if quote.customer_location else ''}
                </table>
                <p style="font-weight:600;">Contacta al cliente para coordinar pago y entrega.</p>
                <a href="https://wa.me/{quote.customer_phone.replace('+', '')}" style="display:inline-block;background:#25D366;color:#fff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;margin-top:12px;">
                    WhatsApp al cliente
                </a>
            </div>
            """
        else:
            return

        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": [engineer_email],
            "subject": subject,
            "html": body,
        })
        print(f"[EMAIL] Sent {event_type} notification for {quote.quote_number} to {engineer_email}")

    except Exception as e:
        print(f"[EMAIL] Failed to send {event_type} notification: {e}")
