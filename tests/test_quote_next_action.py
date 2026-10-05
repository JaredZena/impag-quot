"""
Hermetic tests for the quote's Siguiente paso (next step, owner, due date):
- PUT /quotes/{id}/next-action sets / clears it and logs one audit line in the
  notes (services/quote_next_action.py).
- serialize_quote returns next_action, next_action_owner, next_action_due.
- GET /quotes filters next_action_due_to / has_next_action and sorts
  order=next_action.
- scripts/load_next_actions.py parses rows and applies them to a session.
SQLite file behind get_db / verify_google_token overrides; no network, no real DB.

Run: venv/bin/python -m pytest tests/test_quote_next_action.py -q
"""

import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import routes.quotes as quotes_routes
from auth import verify_google_token
from models import Base, Quote, QuoteItem, get_db
from scripts.load_next_actions import LOADED_BY, RowError, apply_rows, parse_row
from services.quote_next_action import audit_line

USER_EMAIL = "hernan@example.com"

_tmpdir = tempfile.mkdtemp(prefix="quote_next_action_tests_")
engine = create_engine(
    f"sqlite:///{os.path.join(_tmpdir, 'quote_next_action.db')}",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=engine)
TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

app = FastAPI()
app.include_router(quotes_routes.router)


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
app.dependency_overrides[verify_google_token] = lambda: {"email": USER_EMAIL}
client = TestClient(app)

STAMP = r"\d{2}/\d{2}/\d{4}"


@pytest.fixture(autouse=True)
def _clean_quotes():
    db = TestingSession()
    db.query(QuoteItem).delete()
    db.query(Quote).delete()
    db.commit()
    db.close()


def _add_quote(number, *, sent_days_ago=1, **fields) -> int:
    db = TestingSession()
    q = Quote(
        quote_number=number,
        status=fields.pop("status", "sent"),
        customer_name=fields.pop("customer_name", "Miguel Cordero"),
        customer_phone="S/N",
        created_by="backfill",
        subtotal=Decimal("1000"),
        iva_amount=Decimal("0"),
        total=Decimal("1000"),
        sent_at=datetime.now(timezone.utc) - timedelta(days=sent_days_ago),
        **fields,
    )
    db.add(q)
    db.commit()
    quote_id = q.id
    db.close()
    return quote_id


def _get(quote_id) -> Quote:
    db = TestingSession()
    q = db.query(Quote).filter(Quote.id == quote_id).first()
    db.expunge(q)
    db.close()
    return q


def _put(quote_id, **body):
    return client.put(f"/quotes/{quote_id}/next-action", json=body)


def _audit_lines(quote_id):
    notes = _get(quote_id).notes or ""
    return [line for line in notes.splitlines() if line.startswith("[Siguiente paso]")]


# ==================== PUT /quotes/{id}/next-action ====================


def test_set_next_action_trims_and_logs_one_line():
    qid = _add_quote("COT-IMPAG-400926DGO", notes="Contexto: bolsa 17x17")
    res = _put(
        qid, next_action="  Llamar para cerrar  ", owner=" Hernán ", due="2026-10-07"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    data = body["data"]
    assert data["next_action"] == "Llamar para cerrar"
    assert data["next_action_owner"] == "Hernán"
    assert data["next_action_due"] == "2026-10-07"

    notes = _get(qid).notes.splitlines()
    assert notes[0] == "Contexto: bolsa 17x17"
    assert len(notes) == 2
    assert re.fullmatch(
        rf"\[Siguiente paso\] {STAMP} {USER_EMAIL}: Llamar para cerrar · Hernán · 07/10",
        notes[1],
    )


def test_owner_and_due_are_optional_and_blank_owner_is_null():
    qid = _add_quote("COT-IMPAG-410926DGO")
    res = _put(qid, next_action="Mandar fotos del kit", owner="   ", due=None)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["next_action_owner"] is None
    assert data["next_action_due"] is None
    assert re.fullmatch(
        rf"\[Siguiente paso\] {STAMP} {USER_EMAIL}: Mandar fotos del kit",
        _audit_lines(qid)[-1],
    )


def test_blank_next_action_is_hecho_and_clears_owner_and_due():
    qid = _add_quote("COT-IMPAG-420926DGO")
    assert (
        _put(qid, next_action="Llamar", owner="JD", due="2026-10-08").status_code == 200
    )

    res = _put(qid, next_action="   ", owner="JD", due="2026-10-09")
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["next_action"] is None
    assert data["next_action_owner"] is None
    assert data["next_action_due"] is None
    lines = _audit_lines(qid)
    assert len(lines) == 2
    assert re.fullmatch(rf"\[Siguiente paso\] {STAMP} {USER_EMAIL}: hecho", lines[-1])

    # null body fields mean the same; nothing left to clear, so no new line.
    assert _put(qid, next_action=None).status_code == 200
    assert len(_audit_lines(qid)) == 2


def test_empty_due_string_is_null():
    qid = _add_quote("COT-IMPAG-430926DGO")
    res = _put(qid, next_action="Visitar el predio", owner="Daniel", due="")
    assert res.status_code == 200, res.text
    assert res.json()["data"]["next_action_due"] is None


def test_same_values_add_no_audit_line():
    qid = _add_quote("COT-IMPAG-440926DGO")
    for _ in range(2):
        res = _put(qid, next_action="Llamar", owner="JD", due="2026-10-08")
        assert res.status_code == 200, res.text
    assert len(_audit_lines(qid)) == 1


def test_too_long_is_422_in_spanish_and_changes_nothing():
    qid = _add_quote("COT-IMPAG-450926DGO")
    res = _put(qid, next_action="x" * 201, owner="JD")
    assert res.status_code == 422
    assert "demasiado largo" in res.json()["detail"]
    res = _put(qid, next_action="Llamar", owner="y" * 51)
    assert res.status_code == 422
    assert "responsable" in res.json()["detail"]
    q = _get(qid)
    assert q.next_action is None and q.notes is None

    # 200 after trimming is fine.
    res = _put(qid, next_action=f"  {'x' * 200}  ")
    assert res.status_code == 200, res.text
    assert res.json()["data"]["next_action"] == "x" * 200


def test_unknown_quote_is_404():
    res = _put(999999, next_action="Llamar")
    assert res.status_code == 404
    assert res.json()["detail"] == "No se encontró la cotización"


def test_serialize_includes_next_action_fields():
    qid = _add_quote("COT-IMPAG-460926DGO")
    data = client.get(f"/quotes/{qid}").json()["data"]
    assert data["next_action"] is None
    assert data["next_action_owner"] is None
    assert data["next_action_due"] is None

    _put(qid, next_action="Llamar", owner="Jared", due="2026-10-10")
    data = client.get(f"/quotes/{qid}").json()["data"]
    assert (
        data["next_action"],
        data["next_action_owner"],
        data["next_action_due"],
    ) == (
        "Llamar",
        "Jared",
        "2026-10-10",
    )


def test_audit_line_format():
    now = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)  # 12:00 in Durango/CDMX
    assert (
        audit_line("jd@x.mx", "Cerrar venta", "JD", date(2026, 10, 7), now)
        == "[Siguiente paso] 05/10/2026 jd@x.mx: Cerrar venta · JD · 07/10"
    )
    assert audit_line("jd@x.mx", None, None, None, now) == (
        "[Siguiente paso] 05/10/2026 jd@x.mx: hecho"
    )
    # 02:00 UTC on the 6th is still the 5th in business time.
    late = datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc)
    assert audit_line("a", "b", None, None, late).startswith(
        "[Siguiente paso] 05/10/2026"
    )


# ==================== GET /quotes filters & order ====================


def _seed_list():
    ids = {
        "overdue": _add_quote("COT-IMPAG-010926DGO", sent_days_ago=9),
        "later": _add_quote("COT-IMPAG-020926DGO", sent_days_ago=8),
        "nodue_new": _add_quote("COT-IMPAG-030926DGO", sent_days_ago=1),
        "none_mid": _add_quote("COT-IMPAG-040926DGO", sent_days_ago=3),
        "nodue_old": _add_quote("COT-IMPAG-050926DGO", sent_days_ago=6),
    }
    _put(ids["overdue"], next_action="Llamar", owner="Hernán", due="2026-10-03")
    _put(ids["later"], next_action="Mandar PDF", owner="JD", due="2026-10-08")
    _put(ids["nodue_new"], next_action="Esperar respuesta")
    _put(ids["nodue_old"], next_action="Revisar precio")
    return ids


def _list_ids(**params):
    res = client.get("/quotes", params=params)
    assert res.status_code == 200, res.text
    body = res.json()
    ids = [q["id"] for q in body["data"]]
    if "limit" not in params:
        assert body["total"] == len(ids)
    return ids


def test_list_default_order_unchanged():
    ids = _seed_list()
    assert _list_ids() == [
        ids["nodue_new"],
        ids["none_mid"],
        ids["nodue_old"],
        ids["later"],
        ids["overdue"],
    ]


def test_list_next_action_due_to():
    ids = _seed_list()
    assert _list_ids(next_action_due_to="2026-10-05") == [ids["overdue"]]
    assert set(_list_ids(next_action_due_to="2026-10-08")) == {
        ids["overdue"],
        ids["later"],
    }
    assert _list_ids(next_action_due_to="2026-10-01") == []


def test_list_has_next_action():
    ids = _seed_list()
    assert set(_list_ids(has_next_action="true")) == {
        ids["overdue"],
        ids["later"],
        ids["nodue_new"],
        ids["nodue_old"],
    }
    assert _list_ids(has_next_action="false") == [ids["none_mid"]]


def test_list_order_next_action():
    ids = _seed_list()
    # Due date ascending, no due date last; ties by newest send.
    assert _list_ids(order="next_action") == [
        ids["overdue"],
        ids["later"],
        ids["nodue_new"],
        ids["none_mid"],
        ids["nodue_old"],
    ]
    assert _list_ids(order="next_action", has_next_action="true", limit=2) == [
        ids["overdue"],
        ids["later"],
    ]


# ==================== scripts/load_next_actions.py ====================


def test_parse_row_cleans_truncates_and_validates():
    row = parse_row(
        {
            "quote_number": " COT-IMPAG-400926DGO ",
            "next_action": "  " + "a" * 250,
            "owner": " Hernán ",
            "due": "2026-10-07",
            "evidence": "ignored",
        }
    )
    assert row["quote_number"] == "COT-IMPAG-400926DGO"
    assert len(row["next_action"]) == 200 and row["next_action"].endswith("…")
    assert row["owner"] == "Hernán"
    assert row["due"] == date(2026, 10, 7)

    assert parse_row(
        {"quote_number": "X", "next_action": "", "owner": "JD", "due": "2026-10-07"}
    ) == {
        "quote_number": "X",
        "next_action": None,
        "owner": None,
        "due": None,
    }
    assert (
        parse_row({"quote_number": "X", "next_action": "Llamar", "due": ""})["due"]
        is None
    )
    with pytest.raises(RowError):
        parse_row({"quote_number": "X", "next_action": "Llamar", "due": "07/10/2026"})
    with pytest.raises(RowError):
        parse_row({"next_action": "Llamar"})
    with pytest.raises(RowError):
        parse_row("COT-IMPAG-400926DGO")


def test_apply_rows_dry_run_then_commit(capsys):
    a = _add_quote("COT-IMPAG-400926DGO", notes="Contexto: peletizadora")
    b = _add_quote("COT-IMPAG-410926DGO")
    _put(b, next_action="Llamar", owner="JD", due="2026-10-06")
    rows = [
        {
            "quote_number": "COT-IMPAG-400926DGO",
            "next_action": "Mandar cotización ajustada",
            "owner": "Hernán",
            "due": "2026-10-07",
            "chat": "ignored",
        },
        {"quote_number": "COT-IMPAG-410926DGO", "next_action": None},
        {"quote_number": "COT-IMPAG-999999DGO", "next_action": "Llamar"},
        {"quote_number": "COT-IMPAG-400926DGO", "next_action": "x", "due": "mañana"},
    ]

    db = TestingSession()
    try:
        counts = apply_rows(db, rows)
    finally:
        db.close()
    assert counts == {"updated": 2, "unchanged": 0, "missing": 1, "invalid": 1}
    out = capsys.readouterr().out
    assert "COT-IMPAG-400926DGO" in out and "sent" in out
    assert "— → Mandar cotización ajustada · Hernán · 2026-10-07" in out
    assert "Llamar · JD · 2026-10-06 → —" in out
    assert "COT-IMPAG-999999DGO: no existe" in out
    assert _get(a).next_action is None  # dry run wrote nothing
    assert _get(b).next_action == "Llamar"

    db = TestingSession()
    try:
        counts = apply_rows(db, rows, commit=True)
    finally:
        db.close()
    assert counts["updated"] == 2
    qa, qb = _get(a), _get(b)
    assert (qa.next_action, qa.next_action_owner, qa.next_action_due) == (
        "Mandar cotización ajustada",
        "Hernán",
        date(2026, 10, 7),
    )
    assert qa.notes.splitlines()[0] == "Contexto: peletizadora"
    assert re.fullmatch(
        rf"\[Siguiente paso\] {STAMP} {LOADED_BY}: Mandar cotización ajustada · Hernán · 07/10",
        qa.notes.splitlines()[-1],
    )
    assert (qb.next_action, qb.next_action_owner, qb.next_action_due) == (
        None,
        None,
        None,
    )
    assert re.fullmatch(
        rf"\[Siguiente paso\] {STAMP} {LOADED_BY}: hecho", qb.notes.splitlines()[-1]
    )

    # A re-run finds nothing to change and adds no notes.
    db = TestingSession()
    try:
        counts = apply_rows(db, rows, commit=True)
    finally:
        db.close()
    assert counts == {"updated": 0, "unchanged": 2, "missing": 1, "invalid": 1}
    assert _get(a).notes == qa.notes
