"""
Hermetic tests for POST /storefront/orders (routes/storefront_orders.py +
services/web_orders.py), plus the storefront feed and quote serializer fields
the checkout relies on. Uses its own SQLite file behind a get_db override, with
network access blocked: no real DB, no Mercado Pago. (The root conftest.py also
forces DATABASE_URL to sqlite before any project import.)

Run: venv/bin/python -m pytest tests/test_storefront_orders.py -q
"""

import json
import os
import socket
import tempfile
from datetime import datetime
from decimal import Decimal
from typing import get_args

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
from routes.storefront_orders import MpStatus
from services import web_orders

_tmpdir = tempfile.mkdtemp(prefix="storefront_orders_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'orders.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

client = TestClient(app)

KEY = "orders-test-key"
HEADERS = {"X-API-Key": KEY}
SYSTEM_USER_ID = 2
HERNAN_ID = 5
HERNAN = "hernan@example.com"
JARED = "jared@example.com"
POR_ENVIAR_ID = 10
FACTURAS_ID = 11

UNIT_PRICE = Decimal("150.55")
UNIT_TOTAL = Decimal("174.64")  # round2(150.55 × 1.16)
PAID = Decimal("349.28")  # 2 × 174.64
# sqlite round-trips DateTime(timezone=True) naive, so these stay naive.
APPROVED_AT_UTC = datetime(2026, 9, 10, 18, 31)  # noqa: DTZ001 (12:31 -06:00)
SENT_AT = datetime(2026, 9, 10, 20, 0)  # noqa: DTZ001

VALID_INVOICE = {
    "requires_invoice": True,
    "rfc": "pepj800101ab1",
    "razon_social": "JUAN PEREZ PEREZ",
    "regimen_fiscal": "612",
    "cp_fiscal": "34410",
    "uso_cfdi": "g03",
    "email": "facturas@example.com",
}


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
                    id=SYSTEM_USER_ID,
                    email="jared@impag.mx",
                    display_name="Jared",
                    role="admin",
                    is_active=True,
                ),
                TaskUser(
                    id=HERNAN_ID,
                    email="Hernan@Example.com",
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
                    id=POR_ENVIAR_ID, name="Por enviar", created_by=SYSTEM_USER_ID
                ),
                TaskCategory(
                    id=FACTURAS_ID,
                    name="Solicitud de facturas",
                    created_by=SYSTEM_USER_ID,
                ),
            ]
        )
        category = ProductCategory(name="Trampas", slug="trampas")
        db.add(category)
        db.flush()
        db.add_all(
            [
                Product(
                    id=371,
                    name="Trampa azul POPUSA",
                    sku="TRAMPA-AZUL-20",
                    category_id=category.id,
                    unit=ProductUnit.PIEZA,
                    package_size=20,
                    iva=True,
                    price=UNIT_PRICE,
                    stock=7,
                    is_active=True,
                ),
                Product(
                    id=54,
                    name="Perlita",
                    sku="PERLITA-100L",
                    category_id=category.id,
                    unit=ProductUnit.PIEZA,
                    iva=False,
                    price=Decimal("300.00"),
                    stock=0,
                    is_active=True,
                ),
            ]
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
    # spaces, mixed case and a repeat: recipients are normalized and de-duplicated
    monkeypatch.setenv(
        "WEB_ORDER_NOTIFY_EMAILS", f" {HERNAN}, Jared@Example.com ,{HERNAN}"
    )
    monkeypatch.setenv("WEB_ORDER_ASSIGNEE", HERNAN)
    monkeypatch.delenv("WEB_ORDERS_ALLOW_TEST", raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a hermetic test")

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ── helpers ──────────────────────────────────────────────────────────────────

_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_counter = iter(range(1, 100_000))


def _ref() -> str:
    n = next(_counter)
    suffix = ""
    for _ in range(5):
        n, r = divmod(n, len(_ALPHABET))
        suffix = _ALPHABET[r] + suffix
    return f"WEB-260910-T{suffix}"


def _order(
    ref,
    event="checkout_created",
    *,
    qty=2,
    phone="6181234567",
    name="Juan Pérez",
    email="juan@example.com",
    invoice=None,
    delivery=None,
    items=None,
    totals=None,
    payment=None,
):
    total = UNIT_TOTAL * qty
    subtotal = UNIT_PRICE * qty
    return {
        "event": event,
        "external_reference": ref,
        "customer": {
            "name": name,
            "phone": phone,
            "email": email,
            "location": "Nuevo Ideal, Durango",
        },
        "delivery": delivery or {"method": "recoger", "address": None, "cost_total": 0},
        "invoice": invoice,
        "items": items
        or [
            {
                "handle": "trampa-azul-popusa-paquete-con-20-piezas",
                "product_id": 371,
                "description": "Trampa azul POPUSA, paquete con 20 piezas",
                "unit_label": "paquete de 20 piezas",
                "quantity": qty,
                "unit_price": float(UNIT_PRICE),
                "iva_rate": 0.16,
                "unit_total": float(UNIT_TOTAL),
            }
        ],
        "totals": totals
        or {
            "subtotal": float(subtotal),
            "iva_amount": float(total - subtotal),
            "total": float(total),
            "currency": "MXN",
        },
        "mercadopago": payment
        or {
            "preference_id": None,
            "payment_id": None,
            "status": None,
            "status_detail": None,
            "payment_type_id": None,
            "payment_method_id": None,
            "transaction_amount": None,
            "date_approved": None,
            "live_mode": True,
        },
    }


def _payment(
    status,
    *,
    amount=PAID,
    payment_id=1234567890,  # MP sends a JSON number
    payment_type="credit_card",
    live_mode=True,
):
    return {
        "preference_id": "123456-pref",
        "payment_id": payment_id,
        "status": status,
        "status_detail": "accredited" if status == "approved" else status,
        "payment_type_id": payment_type,
        "payment_method_id": "visa" if payment_type.endswith("card") else "oxxo",
        "transaction_amount": float(amount) if amount is not None else None,
        "date_approved": (
            "2026-09-10T12:31:00.000-06:00" if status == "approved" else None
        ),
        "live_mode": live_mode,
    }


def _post(body, headers=HEADERS):
    return client.post("/storefront/orders", json=body, headers=headers)


def _checkout(ref, **order_kw):
    r = _post(_order(ref, **order_kw))
    assert r.status_code == 200, r.text
    return r.json()


def _pay(ref, status, *, order_kw=None, **payment_kw):
    body = _order(
        ref,
        "payment_update",
        payment=_payment(status, **payment_kw),
        **(order_kw or {}),
    )
    r = _post(body)
    assert r.status_code == 200, r.text
    return r.json()


def _find(ref):
    db = TestingSession()
    try:
        return db.query(Quote).filter(Quote.quote_number == ref).first()
    finally:
        db.close()


def _quote(ref) -> Quote:
    """Fresh detached read, items loaded."""
    db = TestingSession()
    try:
        quote = db.query(Quote).filter(Quote.quote_number == ref).one()
        list(quote.items)
        return quote
    finally:
        db.close()


def _notifications(quote_id, event_type=None):
    db = TestingSession()
    try:
        query = db.query(Notification).filter(Notification.quote_id == quote_id)
        if event_type:
            query = query.filter(Notification.event_type == event_type)
        return query.order_by(Notification.id).all()
    finally:
        db.close()


def _tasks(ref):
    db = TestingSession()
    try:
        return db.query(Task).filter(Task.title.contains(ref)).order_by(Task.id).all()
    finally:
        db.close()


def _customers(phone_e164):
    db = TestingSession()
    try:
        return db.query(Customer).filter(Customer.phone_e164 == phone_e164).all()
    finally:
        db.close()


def _count_quotes(ref):
    db = TestingSession()
    try:
        return db.query(Quote).filter(Quote.quote_number == ref).count()
    finally:
        db.close()


# ── contract ─────────────────────────────────────────────────────────────────


def test_route_statuses_match_the_service_map():
    assert set(get_args(MpStatus)) == set(web_orders.MP_STATUS_MAP)


# ── auth ─────────────────────────────────────────────────────────────────────


def test_503_while_orders_key_is_unset(monkeypatch):
    monkeypatch.delenv("STOREFRONT_ORDERS_API_KEY")
    ref = _ref()
    assert _post(_order(ref)).status_code == 503
    assert _find(ref) is None


def test_401_on_missing_wrong_non_ascii_or_sync_key(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", "sync-key")
    ref = _ref()
    for headers in (
        {},
        {"X-API-Key": "wrong"},
        {"X-API-Key": "clavé".encode()},  # non-ASCII: 401, not 500
        {"X-API-Key": "sync-key"},  # the feed/sync key never opens this endpoint
    ):
        assert _post(_order(ref), headers=headers).status_code == 401
    # the key is checked before the body is validated
    r = client.post(
        "/storefront/orders", json={"event": "nope"}, headers={"X-API-Key": "x"}
    )
    assert r.status_code == 401
    assert _find(ref) is None


# ── validation ───────────────────────────────────────────────────────────────


def _set(path, value):
    def mutate(body):
        target = body
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        _set(("external_reference",), "TEC-2026-0001"),
        _set(
            ("external_reference",), "WEB-260910-AB01CD"
        ),  # 0/1 aren't in the alphabet
        _set(("event",), "order_paid"),
        _set(("items",), []),
        _set(("items", 0, "quantity"), 0),
        _set(("items", 0, "quantity"), -1),
        _set(("items", 0, "unit_price"), -5),
        _set(("items", 0, "iva_rate"), 0.08),
        _set(("totals", "currency"), "USD"),
        _set(("customer", "name"), "   "),
        _set(("customer", "phone"), None),
        _set(("delivery", "method"), "dron"),
        _set(("mercadopago", "status"), "accredited"),  # not an MP status
        _set(("event",), "payment_update"),  # a payment_update without a payment
    ],
)
def test_422_on_invalid_body(mutate):
    body = _order(_ref())
    mutate(body)
    r = _post(body)
    assert r.status_code == 422, r.text
    assert _find(body["external_reference"]) is None


@pytest.mark.parametrize(
    "old, new",
    [
        ('"unit_price": 150.55', '"unit_price": NaN'),
        ('"total": 349.28', '"total": Infinity'),
    ],
)
def test_422_on_nan_or_infinity(old, new):
    ref = _ref()
    raw = json.dumps(_order(ref))
    assert old in raw
    r = client.post(
        "/storefront/orders",
        content=raw.replace(old, new),
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert r.status_code == 422, r.text
    assert _find(ref) is None


@pytest.mark.parametrize(
    "order_kw, problem",
    [
        ({"phone": "12345"}, "invalid_phone"),
        (
            {
                "totals": {
                    "subtotal": 301.10,
                    "iva_amount": 48.18,
                    "total": 350.00,
                    "currency": "MXN",
                }
            },
            "totals_mismatch",
        ),
        ({"invoice": {**VALID_INVOICE, "rfc": "NOPE"}}, "invalid_rfc"),
        (
            {"invoice": {**VALID_INVOICE, "regimen_fiscal": "999"}},
            "invalid_regimen_fiscal",
        ),
        ({"invoice": {**VALID_INVOICE, "cp_fiscal": "3441"}}, "invalid_cp_fiscal"),
        (
            {"delivery": {"method": "paqueteria", "address": None, "cost_total": 0}},
            "missing_address",
        ),
    ],
)
def test_checkout_with_an_inconsistent_order_is_422(order_kw, problem):
    ref = _ref()
    r = _post(_order(ref, **order_kw))
    assert r.status_code == 422, r.text
    assert problem in r.json()["detail"]["problems"]
    assert _find(ref) is None


# ── checkout_created ─────────────────────────────────────────────────────────


def test_checkout_created_records_a_draft_web_order():
    ref = _ref()
    res = _checkout(ref, phone="618 555 0101")
    assert res["success"] is True and res["quote_number"] == ref
    assert res["status"] == "draft" and res["payment_status"] == "checkout"
    assert res["duplicate"] is False and res["warnings"] == []

    quote = _quote(ref)
    assert quote.created_by == "tienda-web" and quote.assigned_to == HERNAN
    assert quote.status == "draft" and quote.payment_status == "checkout"
    assert quote.payment_method is None and quote.payment_reference is None
    assert quote.access_token is None and quote.sent_at is None
    assert quote.customer_phone == "+526185550101"
    assert (quote.subtotal, quote.iva_amount, quote.total) == (
        Decimal("301.10"),
        Decimal("48.18"),
        Decimal("349.28"),
    )
    [item] = quote.items
    assert item.product_id == 371 and item.sku == "TRAMPA-AZUL-20"
    assert item.quantity == 2 and item.unit_price == Decimal("150.55")
    assert item.iva_applicable is True and item.unit == "paquete de 20 piezas"

    [customer] = _customers("+526185550101")
    assert customer.source == "web" and customer.display_name == "Juan Pérez"
    assert customer.has_purchased is False and quote.customer_id == customer.id

    # the JSON block the admin "Pedido web" panel parses
    assert quote.notes.startswith(f"[Pedido web {ref}]\n{{")
    assert quote.notes.rstrip().endswith("[/Pedido web]")
    block = web_orders.read_notes_block(quote.notes)
    assert block["ref"] == ref and block["payment"] is None and block["invoice"] is None
    assert block["delivery"] == {
        "method": "recoger",
        "address": None,
        "cost_total": 0.0,
    }

    assert _notifications(quote.id) == [] and _tasks(ref) == []


def test_checkout_replay_is_idempotent():
    ref = _ref()
    first = _checkout(ref, phone="6185550102")
    again = _checkout(ref, phone="6185550102")
    assert again["quote_id"] == first["quote_id"] and again["duplicate"] is True
    assert _count_quotes(ref) == 1
    assert len(_quote(ref).items) == 1
    assert len(_customers("+526185550102")) == 1


# ── approved ─────────────────────────────────────────────────────────────────


def test_approved_payment_accepts_the_quote_and_fires_side_effects_once():
    ref, phone = _ref(), "6185550103"
    _checkout(ref, phone=phone)
    res = _pay(ref, "approved", order_kw={"phone": phone})
    assert res["status"] == "accepted" and res["payment_status"] == "approved"
    assert res["duplicate"] is False

    quote = _quote(ref)
    assert quote.status == "accepted"
    # date_approved 12:31 -06:00, stored in UTC (sqlite hands it back naive)
    assert quote.accepted_at.replace(tzinfo=None) == APPROVED_AT_UTC
    assert quote.payment_method == "mercadopago:credit_card"
    assert quote.payment_reference == "1234567890"
    assert _customers("+526185550103")[0].has_purchased is True

    paid = _notifications(quote.id, "web_order_paid")
    assert sorted(n.recipient_email for n in paid) == [HERNAN, JARED]
    assert paid[0].message.startswith(
        f"Pedido web {ref} pagado: Juan Pérez, $349.28 MXN"
    )
    assert "tarjeta de crédito" in paid[0].message
    assert "Requiere factura" not in paid[0].message
    assert len(_notifications(quote.id)) == 2

    [task] = _tasks(ref)
    assert task.category_id == POR_ENVIAR_ID and task.status == "pending"
    assert task.created_by == SYSTEM_USER_ID and task.assigned_to == HERNAN_ID
    assert task.title.startswith(f"Pedido web {ref} — preparar para recoger")
    assert "2 × Trampa azul POPUSA" in task.description
    assert "$349.28" in task.description

    block = web_orders.read_notes_block(quote.notes)
    assert block["payment"]["status"] == "approved"
    assert block["payment"]["payment_id"] == "1234567890"
    assert block["payment"]["transaction_amount"] == 349.28

    # MP retries the same webhook: nothing new
    again = _pay(ref, "approved", order_kw={"phone": phone})
    assert again["duplicate"] is True and again["status"] == "accepted"
    assert len(_notifications(quote.id)) == 2 and len(_tasks(ref)) == 1
    assert _count_quotes(ref) == 1 and len(_quote(ref).items) == 1


def test_payment_without_a_prior_checkout_creates_the_order():
    ref = _ref()
    res = _pay(ref, "approved")
    assert res["status"] == "accepted" and res["duplicate"] is False
    quote = _quote(ref)
    assert quote.created_by == "tienda-web" and len(quote.items) == 1
    assert len(_notifications(quote.id, "web_order_paid")) == 2
    assert len(_tasks(ref)) == 1


def test_offline_payment_pending_then_approved():
    ref = _ref()
    _checkout(ref)
    res = _pay(ref, "pending", payment_type="ticket")
    assert res["status"] == "draft" and res["payment_status"] == "pending"
    quote = _quote(ref)
    assert quote.payment_method == "mercadopago:ticket"
    pending = _notifications(quote.id, "web_order_pending")
    assert len(pending) == 2 and "pago pendiente" in pending[0].message
    assert _tasks(ref) == []

    _pay(ref, "pending", payment_type="ticket")  # replay
    assert len(_notifications(quote.id, "web_order_pending")) == 2

    res = _pay(ref, "approved", payment_type="ticket")
    assert res["status"] == "accepted" and res["payment_status"] == "approved"
    assert len(_notifications(quote.id, "web_order_paid")) == 2
    assert len(_tasks(ref)) == 1


# ── out of order / stale ─────────────────────────────────────────────────────


def test_out_of_order_events_never_move_backwards():
    ref = _ref()
    _pay(ref, "approved")

    res = _pay(ref, "pending", payment_type="ticket")  # late, same payment id
    assert res["payment_status"] == "approved" and res["status"] == "accepted"
    assert res["duplicate"] is True

    res = _pay(ref, "rejected", payment_id=999)  # a late failed attempt
    assert res["payment_status"] == "approved"

    r = _post(_order(ref))  # a late checkout_created
    assert r.status_code == 200 and r.json()["payment_status"] == "approved"

    quote = _quote(ref)
    assert quote.status == "accepted" and quote.payment_reference == "1234567890"
    assert quote.payment_method == "mercadopago:credit_card"
    assert web_orders.read_notes_block(quote.notes)["payment"]["status"] == "approved"
    assert _notifications(quote.id, "web_order_pending") == []


def test_late_events_never_demote_a_web_draft_someone_sent():
    ref = _ref()
    _checkout(ref)
    db = TestingSession()
    try:
        quote = db.query(Quote).filter(Quote.quote_number == ref).one()
        quote.status = "sent"
        quote.sent_at = SENT_AT
        db.commit()
    finally:
        db.close()
    res = _pay(ref, "rejected")
    assert res["payment_status"] == "rejected" and res["status"] == "sent"


def test_equal_rank_alternatives_follow_the_latest_event():
    ref = _ref()
    _pay(ref, "rejected")
    assert _pay(ref, "cancelled", payment_id=2)["payment_status"] == "cancelled"


# ── problems ─────────────────────────────────────────────────────────────────


def test_amount_mismatch_is_a_problem_not_a_sale():
    ref = _ref()
    _checkout(ref)
    res = _pay(ref, "approved", amount=Decimal("300.00"))
    assert res["status"] == "draft" and res["payment_status"] == "mismatch"

    quote = _quote(ref)
    assert quote.accepted_at is None
    problems = _notifications(quote.id, "web_order_problem")
    assert len(problems) == 2
    assert problems[0].message.startswith(f"Revisar pago {ref} — monto distinto:")
    assert "$300.00" in problems[0].message and "$349.28" in problems[0].message
    assert _notifications(quote.id, "web_order_paid") == [] and _tasks(ref) == []

    _pay(ref, "approved", amount=Decimal("300.00"))  # replay
    assert len(_notifications(quote.id, "web_order_problem")) == 2

    # a later payment for the right amount does settle it
    res = _pay(ref, "approved", payment_id=777)
    assert res["status"] == "accepted" and res["payment_status"] == "approved"
    assert _quote(ref).payment_reference == "777"


def test_approved_without_an_amount_is_a_mismatch():
    ref = _ref()
    res = _pay(ref, "approved", amount=None)
    assert res["payment_status"] == "mismatch" and res["status"] == "draft"


@pytest.mark.parametrize(
    "status, payment_status, label",
    [
        ("refunded", "refunded", "reembolso"),
        ("charged_back", "charged_back", "contracargo"),
        ("in_mediation", "charged_back", "disputa"),
    ],
)
def test_refund_chargeback_or_dispute_after_approval_is_a_problem(
    status, payment_status, label
):
    ref = _ref()
    _pay(ref, "approved")
    res = _pay(ref, status)
    assert res["payment_status"] == payment_status
    assert res["status"] == "accepted"  # quote.status is left as it was

    quote = _quote(ref)
    problems = _notifications(quote.id, "web_order_problem")
    assert len(problems) == 2
    assert all(n.message.startswith(f"Revisar pago {ref} — {label}:") for n in problems)

    _pay(ref, status)  # replay
    assert len(_notifications(quote.id, "web_order_problem")) == 2

    res = _pay(ref, "approved")  # a stale approval never reopens the sale
    assert res["payment_status"] == payment_status
    assert len(_tasks(ref)) == 1  # the fulfillment task from the approval only


def test_dispute_then_chargeback_notifies_each_problem_once():
    ref = _ref()
    for status in ("approved", "in_mediation", "charged_back", "charged_back"):
        _pay(ref, status)
    messages = [n.message for n in _notifications(_quote(ref).id, "web_order_problem")]
    assert len(messages) == 4
    assert sum("— disputa:" in m for m in messages) == 2
    assert sum("— contracargo:" in m for m in messages) == 2


def test_second_approved_payment_is_flagged_as_a_double_charge():
    ref = _ref()
    _pay(ref, "approved")
    res = _pay(ref, "approved", payment_id=555)
    assert res["payment_status"] == "approved"
    assert "additional_approved_payment:555" in res["warnings"]
    quote = _quote(ref)
    assert quote.payment_reference == "1234567890"  # the payment on record stays
    problems = _notifications(quote.id, "web_order_problem")
    assert len(problems) == 2 and "cobro duplicado #555" in problems[0].message
    _pay(ref, "approved", payment_id=555)  # replay
    assert len(_notifications(quote.id, "web_order_problem")) == 2


# ── customers ────────────────────────────────────────────────────────────────


def test_customer_is_matched_by_normalized_phone_and_only_empty_fields_filled():
    db = TestingSession()
    try:
        db.add(
            Customer(
                phone_e164="+526187770000",
                display_name="Juan de Nuevo Ideal",
                source="whatsapp",
            )
        )
        db.commit()
    finally:
        db.close()

    refs = [_ref() for _ in range(3)]
    for ref, phone in zip(refs, ("618 777 0000", "+52 1 618 777 0000", "526187770000")):
        _checkout(ref, phone=phone)

    [customer] = _customers("+526187770000")
    assert customer.display_name == "Juan de Nuevo Ideal"  # not overwritten
    assert customer.source == "whatsapp"  # not overwritten
    assert customer.email == "juan@example.com"  # was empty: filled
    assert customer.location == "Nuevo Ideal, Durango"
    assert customer.first_seen_at is not None and customer.last_activity_at is not None
    assert {_quote(ref).customer_id for ref in refs} == {customer.id}
    assert {_quote(ref).customer_phone for ref in refs} == {"+526187770000"}


# ── invoice + delivery block ─────────────────────────────────────────────────


def test_invoice_request_is_persisted_and_gets_its_own_task():
    ref, phone = _ref(), "6184440000"
    _checkout(ref, phone=phone, invoice=VALID_INVOICE)
    block = web_orders.read_notes_block(_quote(ref).notes)
    assert block["invoice"] == {
        "requires_invoice": True,
        "rfc": "PEPJ800101AB1",
        "razon_social": "JUAN PEREZ PEREZ",
        "regimen_fiscal": "612",
        "cp_fiscal": "34410",
        "uso_cfdi": "G03",
        "email": "facturas@example.com",
    }

    # a payment_update that carries no invoice data keeps what checkout stored
    _pay(ref, "approved", order_kw={"phone": phone})
    quote = _quote(ref)
    assert web_orders.read_notes_block(quote.notes)["invoice"]["rfc"] == "PEPJ800101AB1"
    assert _customers("+526184440000")[0].rfc == "PEPJ800101AB1"

    tasks = _tasks(ref)
    assert sorted(t.category_id for t in tasks) == [POR_ENVIAR_ID, FACTURAS_ID]
    invoice_task = next(t for t in tasks if t.category_id == FACTURAS_ID)
    assert invoice_task.title == f"Factura {ref} — JUAN PEREZ PEREZ"
    assert invoice_task.assigned_to == HERNAN_ID
    for text in ("RFC: PEPJ800101AB1", "Régimen fiscal: 612", "Uso del CFDI: G03"):
        assert text in invoice_task.description
    fulfillment = next(t for t in tasks if t.category_id == POR_ENVIAR_ID)
    assert f'"Factura {ref}"' in fulfillment.description
    [paid] = _notifications(quote.id, "web_order_paid")[:1]
    assert "Requiere factura." in paid.message

    _pay(ref, "approved", order_kw={"phone": phone})  # replay
    assert len(_tasks(ref)) == 2


def test_no_invoice_task_when_the_buyer_declined_one():
    ref = _ref()
    _pay(ref, "approved", order_kw={"invoice": {"requires_invoice": False}})
    assert [t.category_id for t in _tasks(ref)] == [POR_ENVIAR_ID]
    block = web_orders.read_notes_block(_quote(ref).notes)
    assert block["invoice"] == {"requires_invoice": False}


def test_shipping_cost_is_recorded_as_its_own_line():
    ref = _ref()
    delivery = {
        "method": "paqueteria",
        "address": {
            "street": "Av. Juárez",
            "number": "12",
            "colonia": "Centro",
            "cp": "34410",
            "municipio": "Nuevo Ideal",
            "estado": "Durango",
            "references": "Frente a la plaza",
        },
        "cost_total": 116,
    }
    totals = {
        "subtotal": 401.10,
        "iva_amount": 64.18,
        "total": 465.28,
        "currency": "MXN",
    }
    _checkout(ref, delivery=delivery, totals=totals)
    quote = _quote(ref)
    assert quote.total == Decimal("465.28") and len(quote.items) == 2
    shipping = quote.items[1]
    assert shipping.description == "Envío por paquetería"
    assert shipping.unit_price == Decimal("100.00") and shipping.iva_applicable is True
    assert (
        web_orders.read_notes_block(quote.notes)["delivery"]["address"]["cp"] == "34410"
    )

    res = _pay(
        ref,
        "approved",
        amount=Decimal("465.28"),
        order_kw={"delivery": delivery, "totals": totals},
    )
    assert res["status"] == "accepted"
    [task] = _tasks(ref)
    assert "preparar envío" in task.title
    assert (
        "Av. Juárez 12, Col. Centro, CP 34410, Nuevo Ideal, Durango" in task.description
    )


def test_shipping_sent_as_an_envio_item_is_not_added_twice():
    ref = _ref()
    delivery = {
        "method": "paqueteria",
        "address": {"street": "Av. Juárez", "cp": "34410"},
        "cost_total": 116,
    }
    items = _order(ref)["items"] + [
        {
            "handle": "envio",
            "product_id": None,
            "description": "Envío por paquetería",
            "quantity": 1,
            "unit_price": 100.00,
            "iva_rate": 0.16,
            "unit_total": 116.00,
        }
    ]
    totals = {
        "subtotal": 401.10,
        "iva_amount": 64.18,
        "total": 465.28,
        "currency": "MXN",
    }
    _checkout(ref, delivery=delivery, items=items, totals=totals)
    assert len(_quote(ref).items) == 2


def test_human_notes_outside_the_block_survive_updates():
    ref = _ref()
    _checkout(ref)
    db = TestingSession()
    try:
        quote = db.query(Quote).filter(Quote.quote_number == ref).one()
        quote.notes += "\n\nCliente pasa el sábado por la mañana."
        db.commit()
    finally:
        db.close()
    _pay(ref, "approved")
    notes = _quote(ref).notes
    assert notes.endswith("Cliente pasa el sábado por la mañana.")
    assert notes.count(f"[Pedido web {ref}]") == 1
    assert web_orders.read_notes_block(notes)["payment"]["status"] == "approved"


# ── money already taken is never rejected ────────────────────────────────────


def test_paid_order_with_bad_fields_is_recorded_with_warnings():
    ref = _ref()
    res = _pay(
        ref,
        "approved",
        order_kw={"phone": "12345", "invoice": {**VALID_INVOICE, "rfc": "NOPE"}},
    )
    assert res["status"] == "accepted"
    assert {"invalid_phone", "invalid_rfc"} <= set(res["warnings"])
    quote = _quote(ref)
    assert quote.customer_id is None  # nothing reliable to match on
    assert quote.customer_phone == "12345"
    assert "invalid_rfc" in web_orders.read_notes_block(quote.notes)["warnings"]
    invoice_task = next(t for t in _tasks(ref) if t.category_id == FACTURAS_ID)
    assert "Revisar datos fiscales: invalid_rfc" in invoice_task.description
    [paid] = _notifications(quote.id, "web_order_paid")[:1]
    assert "Revisa las notas del pedido." in paid.message


def test_unknown_product_or_iva_disagreement_keeps_the_line_with_a_warning():
    ref = _ref()
    items = _order(ref)["items"]
    items[0]["product_id"] = 999999
    res = _checkout(ref, items=items)
    assert "unmapped_product:999999" in res["warnings"]
    [item] = _quote(ref).items
    assert item.product_id is None and item.sku is None
    assert item.description == "Trampa azul POPUSA, paquete con 20 piezas"

    ref = _ref()
    perlita = {
        "handle": "perlita",
        "product_id": 54,  # backend says no IVA
        "description": "Perlita 100 L",
        "unit_label": None,
        "quantity": 1,
        "unit_price": 300.00,
        "iva_rate": 0.16,
        "unit_total": 348.00,
    }
    totals = {
        "subtotal": 300.00,
        "iva_amount": 48.00,
        "total": 348.00,
        "currency": "MXN",
    }
    res = _checkout(ref, items=[perlita], totals=totals)
    assert "iva_mismatch:54" in res["warnings"]
    assert _quote(ref).items[0].unit == "PIEZA"  # falls back to the product unit


# ── concurrency / test mode ──────────────────────────────────────────────────


def test_insert_race_falls_back_to_the_existing_row(monkeypatch):
    ref = _ref()
    _checkout(ref)
    real_find = web_orders._find_quote
    calls = {"n": 0}

    def lookup_misses_once(db, reference):
        # as if a concurrent event committed the row right after our lookup
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(db, reference)

    monkeypatch.setattr(web_orders, "_find_quote", lookup_misses_once)
    res = _pay(ref, "approved")
    assert calls["n"] == 2  # the insert hit the unique index, then re-read
    assert res["status"] == "accepted" and res["payment_status"] == "approved"
    assert _count_quotes(ref) == 1 and len(_quote(ref).items) == 1
    assert len(_tasks(ref)) == 1


def test_test_mode_payments_are_ignored_unless_allowed(monkeypatch):
    ref = _ref()
    res = _pay(ref, "approved", live_mode=False)
    assert res["ignored"] == "test_mode" and res["quote_id"] is None
    assert _find(ref) is None

    monkeypatch.setenv("WEB_ORDERS_ALLOW_TEST", "true")
    res = _pay(ref, "approved", live_mode=False)
    assert res["status"] == "accepted" and "ignored" not in res


# ── storefront feed + quote serializer ───────────────────────────────────────


def test_products_feed_exposes_the_checkout_gate_fields(monkeypatch):
    monkeypatch.setenv("STOREFRONT_API_KEY", "sync-key")
    r = client.get("/storefront/products", headers={"X-API-Key": "sync-key"})
    assert r.status_code == 200, r.text
    rows = {p["id"]: p for p in r.json()["data"]}
    assert rows[371]["iva"] is True and rows[371]["unit"] == "PIEZA"
    assert rows[371]["package_size"] == 20 and rows[371]["stock"] == 7
    assert rows[54]["iva"] is False and rows[54]["package_size"] is None
    # the orders key does not open the feed
    r = client.get("/storefront/products", headers=HEADERS)
    assert r.status_code == 401


def test_quote_serializer_returns_the_payment_fields():
    ref = _ref()
    _pay(ref, "approved", order_kw={"phone": "6182223333"})
    quote = _quote(ref)
    r = client.get(f"/quotes/{quote.id}")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["payment_status"] == "approved"
    assert data["payment_method"] == "mercadopago:credit_card"
    assert data["payment_reference"] == "1234567890"
    assert data["customer_id"] == quote.customer_id is not None
