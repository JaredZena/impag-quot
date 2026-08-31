"""
Hermetic tests for the POS ↔ quote link (venta cierra cotización).
SQLite file DB in a temp dir — no network, no real DB.

Run: venv/bin/python -m pytest tests/test_pos_quote_link.py -q

When the POS test files run in one pytest process they share ONE engine
(models.py builds it at first import), so this module cleans up everything
it created in teardown_module — tests/test_pos_v2.py runs after this file
and must still start from a clean register (empty pos tables, folio counter
reset, no leftover quotes).
"""

import os
import tempfile

# Must run BEFORE any project import: importing models.py creates the engine
# from DATABASE_URL and runs Base.metadata.create_all against it (config's
# load_dotenv does not override already-set environment variables, so these
# assignments win over the repo .env, which points at PRODUCTION).
_tmpdir = tempfile.mkdtemp(prefix="pos_quote_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmpdir, 'pos_quote_test.db')}"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

assert os.environ["DATABASE_URL"].startswith("sqlite"), "not sqlite — abort"

from datetime import datetime

from fastapi.testclient import TestClient

from models import (
    CashMovement,
    CashSession,
    PosFolioCounter,
    PosSale,
    PosSaleItem,
    Quote,
    Sale,
    SessionLocal,
    StockMovement,
)

from main import app  # noqa: E402

client = TestClient(app)

DEV_EMAIL = "dev@local.test"  # what verify_google_token returns with DISABLE_AUTH

# Quote ids created by this module (wiped in teardown_module).
_quote_ids: list[int] = []


def _make_quote(
    number: str,
    status: str,
    accepted_at: datetime | None = None,
    notes: str | None = None,
) -> int:
    db = SessionLocal()
    try:
        quote = Quote(
            quote_number=number,
            status=status,
            customer_name="Cliente Prueba POS",
            customer_phone="+526181112233",
            created_by=DEV_EMAIL,
            accepted_at=accepted_at,
            notes=notes,
        )
        db.add(quote)
        db.commit()
        _quote_ids.append(quote.id)
        return quote.id
    finally:
        db.close()


def _get_quote(quote_id: int) -> Quote:
    """Fresh read (detached row — loaded attributes stay readable)."""
    db = SessionLocal()
    try:
        return db.query(Quote).filter(Quote.id == quote_id).one()
    finally:
        db.close()


def _post_sale(quote_id: int | None = None, **extra):
    body = {
        "payment_method": "transferencia",
        "items": [
            {
                "description": "Artículo libre",
                "quantity": 1,
                "unit_price": 116.0,
                "iva": True,
            }
        ],
    } | extra
    if quote_id is not None:
        body["quote_id"] = quote_id
    return client.post("/pos/sales", json=body)


def _cancel(sale_id: int):
    return client.post(
        f"/pos/sales/{sale_id}/cancel", json={"reason": "prueba cancelación"}
    )


def teardown_module(module):
    """Wipe everything this module created so tests/test_pos_v2.py (sharing
    the same engine when the POS files run in one process) starts from a
    clean register."""
    db = SessionLocal()
    try:
        db.query(StockMovement).delete(synchronize_session=False)
        db.query(CashMovement).delete(synchronize_session=False)
        db.query(CashSession).delete(synchronize_session=False)
        db.query(Sale).filter(Sale.sheet_tab == "POS").delete(synchronize_session=False)
        db.query(PosSaleItem).delete(synchronize_session=False)
        db.query(PosSale).delete(synchronize_session=False)
        db.query(PosFolioCounter).delete(synchronize_session=False)
        if _quote_ids:
            db.query(Quote).filter(Quote.id.in_(_quote_ids)).delete(
                synchronize_session=False
            )
        db.commit()
    finally:
        db.close()


# ── attach + complete marks the quote accepted ───────────────────────────────


def test_attach_open_quote_marks_accepted():
    quote_id = _make_quote("PQL-2026-0001", "sent")
    r = _post_sale(quote_id=quote_id)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["quote_id"] == quote_id
    assert s["quote_number"] == "PQL-2026-0001"

    quote = _get_quote(quote_id)
    assert quote.status == "accepted"
    assert quote.accepted_at is not None

    # detail + list both serialize the link (the UI renders it from either)
    detail = client.get(f"/pos/sales/{s['id']}").json()
    assert detail["quote_id"] == quote_id
    assert detail["quote_number"] == "PQL-2026-0001"
    listed = client.get("/pos/sales").json()
    row = next(item for item in listed["items"] if item["id"] == s["id"])
    assert row["quote_id"] == quote_id
    assert row["quote_number"] == "PQL-2026-0001"


def test_attach_viewed_quote_marks_accepted():
    quote_id = _make_quote("PQL-2026-0002", "viewed")
    r = _post_sale(quote_id=quote_id)
    assert r.status_code == 200, r.text
    quote = _get_quote(quote_id)
    assert quote.status == "accepted"
    assert quote.accepted_at is not None


def test_sale_without_quote_still_works():
    r = _post_sale()
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["quote_id"] is None
    assert s["quote_number"] is None


# ── already-accepted attach is idempotent (accepted_at untouched) ────────────


def test_attach_accepted_quote_keeps_accepted_at():
    # naive on purpose: sqlite's DateTime(timezone=True) round-trips naive
    original = datetime(2026, 1, 5, 12, 0, 0)  # noqa: DTZ001
    quote_id = _make_quote("PQL-2026-0003", "accepted", accepted_at=original)
    r = _post_sale(quote_id=quote_id)
    assert r.status_code == 200, r.text
    quote = _get_quote(quote_id)
    assert quote.status == "accepted"
    # naive compare: sqlite loses tzinfo on DateTime(timezone=True)
    assert quote.accepted_at.replace(tzinfo=None) == original


# ── invalid statuses / missing quote ─────────────────────────────────────────


def test_attach_invalid_status_400():
    for i, status in enumerate(("draft", "rejected", "expired")):
        quote_id = _make_quote(f"PQL-2026-010{i}", status)
        r = _post_sale(quote_id=quote_id)
        assert r.status_code == 400, r.text
        assert "cotización" in r.json()["detail"].lower()
        # the quote is left exactly as it was
        quote = _get_quote(quote_id)
        assert quote.status == status
        assert quote.accepted_at is None


def test_attach_missing_quote_404():
    r = _post_sale(quote_id=99_999_999)
    assert r.status_code == 404, r.text
    assert "cotización" in r.json()["detail"].lower()


# ── cancel reverts the quote (only when no other live sale references it) ────


def test_cancel_reverts_quote_and_appends_audit_note():
    quote_id = _make_quote("PQL-2026-0004", "sent", notes="nota original")
    s = _post_sale(quote_id=quote_id).json()
    assert _get_quote(quote_id).status == "accepted"

    r = _cancel(s["id"])
    assert r.status_code == 200, r.text
    quote = _get_quote(quote_id)
    assert quote.status == "sent"
    assert quote.accepted_at is None
    assert quote.notes.startswith("nota original\n")
    assert f"[POS] venta {s['folio']} cancelada" in quote.notes
    assert "cotización reabierta" in quote.notes


def test_cancel_keeps_quote_accepted_while_another_live_sale_references_it():
    quote_id = _make_quote("PQL-2026-0005", "sent")
    s1 = _post_sale(quote_id=quote_id).json()
    # second ticket re-attaches the (now accepted) quote — allowed, idempotent
    s2 = _post_sale(quote_id=quote_id).json()
    first_accepted_at = _get_quote(quote_id).accepted_at
    assert first_accepted_at is not None

    # cancel ticket 1: ticket 2 still closes the quote → stays accepted
    assert _cancel(s1["id"]).status_code == 200
    quote = _get_quote(quote_id)
    assert quote.status == "accepted"
    assert quote.accepted_at == first_accepted_at
    assert quote.notes is None  # no reopen audit line was written

    # cancel ticket 2: no live sale left → the quote reopens
    assert _cancel(s2["id"]).status_code == 200
    quote = _get_quote(quote_id)
    assert quote.status == "sent"
    assert quote.accepted_at is None
    assert f"[POS] venta {s2['folio']} cancelada" in quote.notes


def test_cancel_respects_manual_status_change():
    """If someone moved the quote off 'accepted' by hand, cancel leaves it."""
    quote_id = _make_quote("PQL-2026-0006", "sent")
    s = _post_sale(quote_id=quote_id).json()

    db = SessionLocal()
    try:
        db.query(Quote).filter(Quote.id == quote_id).update(
            {Quote.status: "rejected"}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()

    assert _cancel(s["id"]).status_code == 200
    quote = _get_quote(quote_id)
    assert quote.status == "rejected"
    assert quote.notes is None


# ── picker endpoint ──────────────────────────────────────────────────────────


def test_pos_quote_picker_filters_attachable_and_matches_text():
    ids = {
        "sent": _make_quote("PKR-2026-0001", "sent"),
        "viewed": _make_quote("PKR-2026-0002", "viewed"),
        "accepted": _make_quote("PKR-2026-0003", "accepted"),
        "draft": _make_quote("PKR-2026-0004", "draft"),
        "rejected": _make_quote("PKR-2026-0005", "rejected"),
        "expired": _make_quote("PKR-2026-0006", "expired"),
    }
    r = client.get("/pos/quotes", params={"q": "PKR-2026"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    got = {item["id"] for item in items}
    assert got == {ids["sent"], ids["viewed"], ids["accepted"]}
    by_id = {item["id"]: item for item in items}
    assert by_id[ids["sent"]]["quote_number"] == "PKR-2026-0001"
    assert by_id[ids["sent"]]["status"] == "sent"
    assert by_id[ids["sent"]]["customer_name"] == "Cliente Prueba POS"

    # narrower text match (quote_number substring)
    r = client.get("/pos/quotes", params={"q": "PKR-2026-0002"})
    assert {item["id"] for item in r.json()["items"]} == {ids["viewed"]}
