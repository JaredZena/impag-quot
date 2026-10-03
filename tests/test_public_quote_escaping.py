"""
Buyer-typed text never reaches the quote HTML unescaped.

A web order (routes/storefront_orders.py) stores what the buyer typed as the
quote's customer name, location and notes (the delivery address JSON block).
When staff send such a draft, routes/public_quotes.py serves it as HTML on
the storefront's own domain (vercel.json proxies /cotizacion/:token), and
services/email_service.py puts the same fields in the engineer's email. Both
must escape every quote field.

No database: the renderers take any object with the Quote attributes.
"""

import re
from datetime import datetime, timezone
from html import escape
from types import SimpleNamespace

from routes.public_quotes import render_expired, render_quote_page
from services.email_service import build_quote_notification_email
from services.web_order_email import build_buyer_confirmation

NAME = 'Juan<img src=x onerror="alert(1)">'
LOCATION = "<b>Durango</b>"
REFERENCES = "</p><img src=x onerror=alert(1)>"
PHONE = '+52618"><script>alert(1)</script>'
DESCRIPTION = "Trampa <script src=//x.co/a></script>"
SKU = "<i>SKU</i>"
ITEM_NOTES = "</td><svg onload=alert(1)>"
UNIT = "<u>pz</u>"
PAYLOADS = (NAME, LOCATION, REFERENCES, PHONE, DESCRIPTION, SKU, ITEM_NOTES, UNIT)


def _quote(**overrides):
    item = SimpleNamespace(
        description=DESCRIPTION,
        sku=SKU,
        notes=ITEM_NOTES,
        unit=UNIT,
        quantity=2,
        unit_price=150.55,
        iva_applicable=True,
        sort_order=0,
    )
    fields = {
        "quote_number": "WEB-260910-7K3QX9",
        "status": "sent",
        "customer_name": NAME,
        "customer_phone": PHONE,
        "customer_location": LOCATION,
        "notes": f'[Pedido web WEB-260910-7K3QX9]\n{{"references": "{REFERENCES}"}}\n[/Pedido web]',
        "items": [item],
        "sent_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
        "created_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
        "accepted_at": None,
        "validity_days": 15,
        "assigned_to": "hernan@example.com",
        "created_by": "tienda-web",
        "payment_status": None,
        "access_token": "5f0c7d1e-3a52-4c1b-9f0e-2b8a6d4e1c37",
        "subtotal": 301.10,
        "iva_amount": 48.18,
        "total": 349.28,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _assert_escaped(html, payloads):
    for payload in payloads:
        assert payload not in html, payload
        assert escape(payload) in html, payload


def test_public_quote_page_escapes_every_buyer_field():
    html = render_quote_page(_quote())
    _assert_escaped(html, (NAME, LOCATION, DESCRIPTION, SKU, ITEM_NOTES, UNIT))
    assert "<script" not in html  # (the page has its own <svg> icon)


def test_public_quote_page_hides_staff_notes_from_the_buyer():
    item = SimpleNamespace(
        description="Calefactor",
        sku=None,
        unit="pieza",
        quantity=1,
        unit_price=28500,
        iva_applicable=True,
        sort_order=0,
        notes="Tienda en línea: calefactor-l-b-white-bloom-400® · SIN PRECIO: capturar antes de enviar",
    )
    quote = _quote(
        notes='[Pedido web WEB-261002-KM2CG2]\n{"invoice": {"rfc": "PEPJ800101AB1"}}\n[/Pedido web]\n\n'
        "Comentarios del cliente:\nPara invernadero de 1 ha",
        items=[item],
    )
    html = render_quote_page(quote)
    for hidden in ("Pedido web", "PEPJ800101AB1", "Tienda en línea", "SIN PRECIO"):
        assert hidden not in html, hidden
    assert "Para invernadero de 1 ha" in html
    # a staff quote's own notes still show
    assert "Entrega en 3 días" in render_quote_page(
        _quote(notes="Entrega en 3 días", items=[item])
    )
    # block only: no empty notes card
    assert "white-space:pre-wrap" not in render_quote_page(_quote(items=[item]))


def test_public_quote_page_escapes_the_status_banner_and_reference():
    quote = _quote(quote_number='<b>"X"</b>')
    html = render_quote_page(quote, status_message="<em>ok</em>", show_accept=False)
    assert "<b>" not in html and "<em>" not in html
    assert "&lt;em&gt;ok&lt;/em&gt;" in html
    expired = render_expired(quote)
    assert "<b>" not in expired and "&lt;b&gt;&quot;X&quot;&lt;/b&gt;" in expired


def test_engineer_emails_escape_buyer_fields():
    for event in ("viewed", "accepted"):
        _subject, html = build_quote_notification_email(_quote(), event)
        _assert_escaped(html, (NAME, LOCATION))
        assert "<script" not in html
    _, accepted = build_quote_notification_email(_quote(), "accepted")
    # the WhatsApp link keeps digits only, so the phone can't break out of href
    [number] = re.findall(r'href="https://wa\.me/([^"]*)"', accepted)
    assert number == "526181"
    assert build_quote_notification_email(_quote(), "other") is None


def test_buyer_confirmation_escapes_buyer_fields(monkeypatch):
    monkeypatch.setenv("WEB_ORDER_STORE_ADDRESS", "Calle Ejemplo 10, Nuevo Ideal")
    message = build_buyer_confirmation(
        ref="WEB-260910-7K3QX9",
        to="juan@example.com",
        customer_name=NAME,
        lines=[
            {
                "description": DESCRIPTION,
                "unit_label": UNIT,
                "quantity": 2,
                "iva_rate": 0.16,
                "unit_total": 174.64,
                "line_total": 349.28,
            }
        ],
        subtotal=301.10,
        iva_amount=48.18,
        total=349.28,
        delivery_method="paqueteria",
        delivery_address=REFERENCES,
        invoice={
            "requires_invoice": True,
            "razon_social": LOCATION,
            "rfc": "PEPJ800101AB1",
            "email": "f@example.com",
        },
        payment_label="tarjeta de crédito",
        payment_id="1",
        paid_at=None,
    )
    _assert_escaped(message["html"], (NAME, DESCRIPTION, UNIT, REFERENCES, LOCATION))
    assert "<script" not in message["html"] and "<img" not in message["html"]
