"""
Hermetic tests for self-serve storefront quotes: POST /storefront/quote-requests,
POST /storefront/quotes/{token}/checkout, the public quote page's pay button,
and paying the quote through the existing POST /storefront/orders
payment_update path. SQLite behind a get_db override, network blocked.

Run: venv/bin/python -m pytest tests/test_storefront_quotes.py -q
"""

import os
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import app
from models import (
    Base,
    Customer,
    Notification,
    Product,
    ProductCategory,
    ProductUnit,
    Quote,
    Task,
    TaskCategory,
    TaskUser,
    get_db,
)
from routes import storefront_orders as orders_route
from services import mailer, web_orders
from services.web_quote_email import build_staff_alert
from services.web_quotes import MAX_PER_PHONE_PER_HOUR

_tmpdir = tempfile.mkdtemp(prefix="storefront_quotes_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'quotes.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
client = TestClient(app)

KEY = "orders-test-key"
HEADERS = {"X-API-Key": KEY}
HERNAN = "hernan@example.com"
SEGUIMIENTO_ID = 8


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


def setup_module(module):
    app.dependency_overrides[get_db] = _override_get_db
    db = TestingSession()
    try:
        db.add_all(
            [
                TaskUser(
                    id=2,
                    email="jared@impag.mx",
                    display_name="Jared",
                    role="admin",
                    is_active=True,
                ),
                TaskUser(
                    id=5,
                    email=HERNAN,
                    display_name="Hernán",
                    role="member",
                    is_active=True,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                TaskCategory(
                    id=SEGUIMIENTO_ID, name="Seguimiento a cotizaciones", created_by=2
                ),
                TaskCategory(id=10, name="Por enviar", created_by=2),
            ]
        )
        category = ProductCategory(name="Bombeo", slug="bombeo")
        db.add(category)
        db.flush()
        db.add(
            Product(
                id=900,
                name="Kit bombeo solar",
                sku="KIT-SOLAR-1",
                category_id=category.id,
                unit=ProductUnit.PIEZA,
                iva=True,
                price=Decimal("22500.00"),
                stock=0,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()


def teardown_module(module):
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("STOREFRONT_ORDERS_API_KEY", KEY)
    monkeypatch.setenv("WEB_ORDER_NOTIFY_EMAILS", HERNAN)
    monkeypatch.setenv("WEB_ORDER_ASSIGNEE", HERNAN)
    monkeypatch.delenv("WEB_QUOTES_MAX_PER_HOUR", raising=False)
    monkeypatch.delenv("WEB_QUOTE_VALIDITY_DAYS", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("GMAIL_SMTP_USER", raising=False)
    monkeypatch.delenv("GMAIL_SMTP_APP_PASSWORD", raising=False)
    monkeypatch.setattr(orders_route, "send_buyer_confirmation", lambda message: None)


OUTBOX: list[dict] = []


@pytest.fixture(autouse=True)
def _outbox(monkeypatch):
    """Capture what services/web_quote_email.py hands the mailer."""
    OUTBOX.clear()

    def capture(message, *, tag, idempotency_key=None):
        OUTBOX.append({**message, "tag": tag, "key": idempotency_key})
        return True

    monkeypatch.setattr(mailer, "send", capture)
    return OUTBOX


def _mail_to(address):
    return [m for m in OUTBOX if address in m["to"]]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a hermetic test")

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


_phones = iter(range(6180000001, 6189999999))


def _request(*, phone=None, items=None, delivery=None, **extra):
    body = {
        "customer": {
            "name": "Rodrigo Herrera",
            "phone": str(phone or next(_phones)),
            "email": "rodrigo@example.com",
            "location": "Canatlán, Durango",
        },
        "delivery": delivery or {"method": "recoger"},
        "items": items
        or [
            {
                "handle": "kit-bombeo-solar-kolos-1hp",
                "product_id": 900,
                "sku": "KIT-SOLAR-1",
                "description": "Kit bombeo solar Kolos 1 HP",
                "unit_label": "kit",
                "quantity": 2,
                "unit_price": 22500.55,
                "iva_rate": 0.16,
            },
            {
                "handle": "semilla-avena",
                "description": "Semilla de avena (bulto)",
                "quantity": 3,
                "unit_price": 100,
                "iva_rate": 0,
            },
        ],
    }
    body.update(extra)
    return body


def _create(**kw):
    r = client.post("/storefront/quote-requests", json=_request(**kw), headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


def _quote(ref) -> Quote:
    db = TestingSession()
    try:
        quote = db.query(Quote).filter(Quote.quote_number == ref).one()
        list(quote.items)
        return quote
    finally:
        db.close()


def _price_all(quote, price=250):
    for item in quote.items:
        if item.unit_price <= 0:
            r = client.put(
                f"/quotes/{quote.id}/items/{item.id}", json={"unit_price": price}
            )
            assert r.status_code == 200, r.text


def _pay(ref, status="approved", amount=None, payment_id=555001):
    quote = _quote(ref)
    body = {
        "event": "payment_update",
        "external_reference": ref,
        "customer": None,
        "delivery": None,
        "invoice": None,
        "items": [],
        "totals": None,
        "mercadopago": {
            "preference_id": "pref-1",
            "payment_id": payment_id,
            "status": status,
            "status_detail": "accredited",
            "payment_type_id": "credit_card",
            "payment_method_id": "visa",
            "transaction_amount": float(amount if amount is not None else quote.total),
            "date_approved": (
                "2026-09-29T12:00:00.000-06:00" if status == "approved" else None
            ),
            "live_mode": True,
        },
    }
    r = client.post("/storefront/orders", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


# ── auth ─────────────────────────────────────────────────────────────────────


def test_quote_request_needs_the_orders_key():
    assert client.post("/storefront/quote-requests", json=_request()).status_code == 401
    assert (
        client.post(
            "/storefront/quote-requests", json=_request(), headers={"X-API-Key": "nope"}
        ).status_code
        == 401
    )


# ── creation ─────────────────────────────────────────────────────────────────


def test_instant_quote_is_sent_with_totals_and_a_link():
    data = _create()
    assert data["status"] == "sent" and data["needs_review"] is False
    assert data["quote_number"].startswith("WEB-")
    assert data["url"] == f"/cotizacion/{data['access_token']}"
    # 2 × 22,500.55 at 16% + 3 × 100 at 0%
    assert data["subtotal"] == 45301.10
    assert data["iva_amount"] == 7200.18
    assert data["total"] == 52501.28

    quote = _quote(data["quote_number"])
    assert quote.created_by == "tienda-web"
    assert quote.assigned_to == HERNAN
    assert quote.payment_status is None
    assert quote.sent_at is not None and quote.validity_days == 7
    assert [i.iva_applicable for i in quote.items] == [True, False]
    assert quote.items[0].product_id == 900
    block = web_orders.read_notes_block(quote.notes)
    assert block["origin"] == "cotizacion" and block["delivery"]["method"] == "recoger"
    assert "lines" not in block  # rebuilt from the items at payment time

    db = TestingSession()
    try:
        events = [
            n.event_type
            for n in db.query(Notification).filter(Notification.quote_id == quote.id)
        ]
        assert events == ["web_quote_created"]
    finally:
        db.close()


def test_unpriced_line_or_delivery_goes_to_review_with_a_task():
    items = [
        {
            "handle": "kit-drone-agras-t40",
            "description": "Kit drone Agras T40",
            "quantity": 1,
            "unit_price": 0,
            "iva_rate": 0.16,
        }
    ]
    data = _create(items=items)
    assert data["status"] == "draft" and data["review_reasons"] == ["unpriced_items"]

    shipped = _create(
        delivery={
            "method": "flete",
            "address": {
                "street": "Av. Juárez",
                "number": "10",
                "cp": "34000",
                "municipio": "Durango",
                "estado": "Durango",
            },
        }
    )
    assert shipped["status"] == "draft" and shipped["review_reasons"] == ["delivery"]

    db = TestingSession()
    try:
        tasks = db.query(Task).filter(Task.title.contains(data["quote_number"])).all()
        assert len(tasks) == 1
        assert tasks[0].category_id == SEGUIMIENTO_ID and tasks[0].assigned_to == 5
        assert "SIN PRECIO" in tasks[0].description
    finally:
        db.close()


@pytest.mark.parametrize(
    "mutate,problem",
    [
        (lambda b: b["customer"].update(phone="12"), "invalid_phone"),
        (
            lambda b: b["customer"].update(name="<script>x</script>"),
            "markup_in:customer.name",
        ),
        (lambda b: b.update(delivery={"method": "paqueteria"}), "missing_address"),
        (lambda b: b["items"].append(dict(b["items"][0])), "duplicate_items"),
    ],
)
def test_bad_requests_are_refused(mutate, problem):
    body = _request()
    mutate(body)
    r = client.post("/storefront/quote-requests", json=body, headers=HEADERS)
    assert r.status_code == 422, r.text
    assert problem in r.json()["detail"]["problems"]


def test_per_phone_flood_cap():
    phone = next(_phones)
    for _ in range(MAX_PER_PHONE_PER_HOUR):
        _create(phone=phone)
    r = client.post(
        "/storefront/quote-requests", json=_request(phone=phone), headers=HEADERS
    )
    assert r.status_code == 429


# ── public page + checkout + payment ─────────────────────────────────────────


def test_public_page_shows_pay_button_and_no_view_notification():
    data = _create()
    page = client.get(data["url"].replace("/cotizacion/", "/public/quote/"))
    assert page.status_code == 200
    assert 'action="/api/quote-checkout"' in page.text
    assert "ACEPTAR Y PAGAR $52,501.28 MXN" in page.text
    quote = _quote(data["quote_number"])
    assert quote.status == "viewed"
    db = TestingSession()
    try:
        events = [
            n.event_type
            for n in db.query(Notification).filter(Notification.quote_id == quote.id)
        ]
        assert "quote_viewed" not in events
    finally:
        db.close()
    # a plain POST can no longer accept a web quote without paying
    client.post(data["url"].replace("/cotizacion/", "/public/quote/"))
    assert _quote(data["quote_number"]).status == "viewed"


def test_review_quote_page_has_no_pay_button():
    data = _create(
        items=[
            {
                "handle": "x",
                "description": "Sin precio",
                "quantity": 1,
                "unit_price": 0,
                "iva_rate": 0.16,
            }
        ]
    )
    page = client.get(f"/public/quote/{data['access_token']}")
    assert "en revisión" in page.text and "quote-checkout" not in page.text
    r = client.post(
        f"/storefront/quotes/{data['access_token']}/checkout", headers=HEADERS
    )
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "in_review"


def test_checkout_returns_amount_then_payment_accepts_the_quote():
    data = _create()
    token, ref = data["access_token"], data["quote_number"]
    r = client.post(f"/storefront/quotes/{token}/checkout", headers=HEADERS)
    assert r.status_code == 200, r.text
    info = r.json()["data"]
    assert info["quote_number"] == ref and info["total"] == "52501.28"
    assert info["customer"]["email"] == "rodrigo@example.com"
    assert _quote(ref).payment_status == "checkout"

    result = _pay(ref)
    assert result["success"] is True if "success" in result else True
    quote = _quote(ref)
    assert quote.status == "accepted" and quote.payment_status == "approved"
    assert quote.customer_id is not None
    block = web_orders.read_notes_block(quote.notes)
    assert block["origin"] == "cotizacion"

    db = TestingSession()
    try:
        tasks = db.query(Task).filter(Task.title.contains(ref)).all()
        assert any(t.title.startswith(f"Pedido web {ref}") for t in tasks)
        events = {
            n.event_type
            for n in db.query(Notification).filter(Notification.quote_id == quote.id)
        }
        assert "web_order_paid" in events
        assert (
            db.query(Customer).filter(Customer.id == quote.customer_id).one().source
            == "web"
        )
    finally:
        db.close()

    # paid: the page says so and there is nothing left to pay
    page = client.get(f"/public/quote/{token}")
    assert "Cotización pagada" in page.text and "quote-checkout" not in page.text
    r = client.post(f"/storefront/quotes/{token}/checkout", headers=HEADERS)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "already_paid"


def test_wrong_amount_is_flagged_not_paid():
    data = _create()
    client.post(f"/storefront/quotes/{data['access_token']}/checkout", headers=HEADERS)
    _pay(data["quote_number"], amount=10, payment_id=555002)
    quote = _quote(data["quote_number"])
    assert quote.payment_status == "mismatch" and quote.status != "accepted"


def test_expired_quote_cannot_be_paid():
    data = _create()
    db = TestingSession()
    try:
        quote = db.query(Quote).filter(Quote.quote_number == data["quote_number"]).one()
        quote.sent_at = datetime.now(timezone.utc) - timedelta(days=30)
        db.commit()
    finally:
        db.close()
    r = client.post(
        f"/storefront/quotes/{data['access_token']}/checkout", headers=HEADERS
    )
    assert r.status_code == 410
    assert _quote(data["quote_number"]).status == "expired"


def test_pay_return_banner_is_whitelisted():
    data = _create()
    page = client.get(f"/public/quote/{data['access_token']}?pago=error")
    assert "El pago no se completó" in page.text
    page = client.get(f"/public/quote/{data['access_token']}?pago=<b>x</b>")
    assert "<b>x</b>" not in page.text


def test_staff_send_keeps_the_buyer_link():
    data = _create(
        items=[
            {
                "handle": "x",
                "description": "Sin precio",
                "quantity": 1,
                "unit_price": 0,
                "iva_rate": 0.16,
            }
        ]
    )
    quote = _quote(data["quote_number"])
    _price_all(quote)
    r = client.post(f"/quotes/{quote.id}/send")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["access_token"] == data["access_token"]


def test_quote_closed_at_the_pos_is_not_payable_online():
    data = _create()
    db = TestingSession()
    try:
        quote = db.query(Quote).filter(Quote.quote_number == data["quote_number"]).one()
        quote.status = "accepted"  # what POST /pos/sales does with quote_id
        db.commit()
    finally:
        db.close()
    page = client.get(f"/public/quote/{data['access_token']}")
    assert "quote-checkout" not in page.text
    r = client.post(
        f"/storefront/quotes/{data['access_token']}/checkout", headers=HEADERS
    )
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "closed"


# ── emails (services/web_quote_email.py) ─────────────────────────────────────


def test_new_quote_alerts_staff_and_sends_the_buyer_the_link():
    data = _create(phone=6181110001)
    quote = _quote(data["quote_number"])
    [staff] = _mail_to(HERNAN)
    assert (
        staff["subject"].startswith("🛒") and data["quote_number"] in staff["subject"]
    )
    assert f"/quotes/{quote.id}" in staff["html"]
    assert "https://wa.me/526181110001" in staff["html"]
    assert data["access_token"] in staff["html"]
    assert staff["key"] == f"web-quote/{data['quote_number']}/staff"
    [buyer] = _mail_to("rodrigo@example.com")
    assert (
        buyer["subject"]
        == f"Tu cotización {data['quote_number']} de Todo Para El Campo"
    )
    assert (
        f"https://www.todoparaelcampo.com.mx/cotizacion/{data['access_token']}"
        in buyer["html"]
    )
    assert "Ver y pagar mi cotización" in buyer["html"]
    assert buyer["reply_to"] == "impagtodoparaelcampo@gmail.com"
    # the buyer never sees staff notes
    assert "Pedido web" not in buyer["html"] and "Tienda en línea" not in buyer["html"]


def test_quote_in_review_tells_staff_what_is_missing_and_the_buyer_to_wait():
    data = _create(
        delivery={
            "method": "flete",
            "address": {
                "street": "Av. Juárez",
                "number": "10",
                "cp": "34000",
                "municipio": "Durango",
                "estado": "Durango",
            },
        },
        notes="Lo necesito antes del 15",
    )
    [staff] = _mail_to(HERNAN)
    assert staff["subject"].startswith("🔔 Cotización web por revisar")
    assert "cotizar flete" in staff["html"] and "Av. Juárez 10" in staff["html"]
    assert "Lo necesito antes del 15" in staff["html"]
    [buyer] = _mail_to("rodrigo@example.com")
    assert (
        buyer["subject"]
        == f"Recibimos tu solicitud de cotización {data['quote_number']}"
    )
    assert "cotiza el flete" in buyer["html"] and "Ver mi cotización" in buyer["html"]


def test_no_buyer_email_without_an_address():
    body = _request()
    body["customer"]["email"] = None
    r = client.post("/storefront/quote-requests", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert [m["to"] for m in OUTBOX] == [[HERNAN]]


def test_staff_send_emails_the_buyer_that_the_quote_is_ready():
    data = _create(
        items=[
            {
                "handle": "x",
                "description": "Sin precio",
                "quantity": 1,
                "unit_price": 0,
                "iva_rate": 0.16,
            }
        ]
    )
    quote = _quote(data["quote_number"])
    _price_all(quote)
    OUTBOX.clear()
    r = client.post(f"/quotes/{quote.id}/send")
    assert r.status_code == 200, r.text
    [ready] = OUTBOX
    assert ready["to"] == ["rodrigo@example.com"]
    assert (
        ready["subject"]
        == f"Tu cotización {data['quote_number']} está lista para pagar"
    )
    assert data["access_token"] in ready["html"]


def test_staff_alert_escapes_buyer_fields():
    from types import SimpleNamespace

    item = SimpleNamespace(
        description="<script>x</script>",
        unit="pz",
        quantity=1,
        unit_price=10,
        iva_applicable=True,
        sort_order=0,
    )
    quote = SimpleNamespace(
        id=7,
        quote_number="WEB-261003-AAAAAA",
        customer_name='Juan<img src=x onerror="alert(1)">',
        customer_phone="+526181234567",
        customer_email=None,
        customer_location="<b>Durango</b>",
        access_token="5f0c7d1e-3a52-4c1b-9f0e-2b8a6d4e1c37",
        notes='[Pedido web WEB-261003-AAAAAA]\n{"delivery": {"method": "flete", "address": '
        '{"street": "</td><svg onload=alert(1)>"}}, "review": ["delivery"]}\n[/Pedido web]',
        items=[item],
        subtotal=10,
        iva_amount=1.6,
        total=11.6,
    )
    html = build_staff_alert(quote)["html"]
    for payload in ("<script>", "<img", "<b>Durango", "<svg"):
        assert payload not in html, payload


# ── staff complete a web quote in the admin (routes/quotes.py items) ─────────

UNPRICED = {
    "handle": "x",
    "description": "Sin precio",
    "quantity": 2,
    "unit_price": 0,
    "iva_rate": 0.16,
}


def test_unpriced_web_quote_cannot_be_sent_until_staff_price_it():
    data = _create(items=[UNPRICED])
    quote = _quote(data["quote_number"])
    r = client.post(f"/quotes/{quote.id}/send")
    assert r.status_code == 400 and "precio" in r.json()["detail"]
    [item] = quote.items
    r = client.put(f"/quotes/{quote.id}/items/{item.id}", json={"unit_price": 500})
    assert r.status_code == 200, r.text
    priced = _quote(data["quote_number"])
    assert float(priced.total) == 1160.0  # 2 × 500 + 16%
    assert "SIN PRECIO" not in priced.items[0].notes
    assert client.post(f"/quotes/{quote.id}/send").status_code == 200
    r = client.post(
        f"/storefront/quotes/{data['access_token']}/checkout", headers=HEADERS
    )
    assert r.status_code == 200 and r.json()["data"]["total"] == "1160.00"


def test_staff_add_the_flete_and_the_buyer_pays_it():
    data = _create(
        delivery={
            "method": "flete",
            "address": {
                "street": "Av. Juárez",
                "number": "10",
                "cp": "34000",
                "municipio": "Durango",
                "estado": "Durango",
            },
        }
    )
    before = float(_quote(data["quote_number"]).total)
    quote = _quote(data["quote_number"])
    r = client.post(
        f"/quotes/{quote.id}/items",
        json={
            "description": "Flete a Durango, Dgo.",
            "quantity": 1,
            "unit_price": 1500,
            "iva_applicable": True,
            "sort_order": 9,
        },
    )
    assert r.status_code == 200, r.text
    assert client.post(f"/quotes/{quote.id}/send").status_code == 200
    r = client.post(
        f"/storefront/quotes/{data['access_token']}/checkout", headers=HEADERS
    )
    assert float(r.json()["data"]["total"]) == round(before + 1740, 2)
    page = client.get(f"/public/quote/{data['access_token']}")
    assert "Flete a Durango, Dgo." in page.text


def test_items_are_locked_once_the_quote_is_paid():
    data = _create()
    _pay(data["quote_number"])
    quote = _quote(data["quote_number"])
    item = quote.items[0]
    line = {"description": "Extra", "quantity": 1, "unit_price": 10}
    assert client.post(f"/quotes/{quote.id}/items", json=line).status_code == 409
    assert (
        client.put(
            f"/quotes/{quote.id}/items/{item.id}", json={"unit_price": 1}
        ).status_code
        == 409
    )
    assert client.delete(f"/quotes/{quote.id}/items/{item.id}").status_code == 409


def test_item_payloads_are_validated():
    data = _create(items=[UNPRICED])
    quote = _quote(data["quote_number"])
    [item] = quote.items
    bad = [
        {"description": "", "quantity": 1, "unit_price": 1},
        {"description": "x", "quantity": 0, "unit_price": 1},
        {"description": "x", "quantity": 1, "unit_price": -1},
    ]
    for line in bad:
        assert client.post(f"/quotes/{quote.id}/items", json=line).status_code == 422
    r = client.put(f"/quotes/{quote.id}/items/{item.id}", json={"quantity": 0})
    assert r.status_code == 422
