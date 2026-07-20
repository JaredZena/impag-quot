"""
Hermetic tests for the stalled-quote follow-up sweep (services/quote_followup).
SQLite in-memory, drafter mocked (no LLM). Covers candidate selection, idempotency,
expiry awareness, the whitespace-name poison pill, WA-draft create+dedup, and
per-quote SAVEPOINT isolation.

Run: venv/bin/python -m pytest test_quote_followup.py -v
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.quote_followup as qf
from models import (
    Base, Quote, Task, TaskCategory, TaskUser, WAConversation, WADraft,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine)
for m in (TaskUser, TaskCategory, Task, Quote, WAConversation, WADraft):
    m.__table__.create(bind=engine)

# Naive UTC: SQLite drops tz info on round-trip, so keep the injected clock naive
# to match what the stored sent_at reads back as. Production (Postgres, tz=True)
# runs fully tz-aware and is verified separately against the real DB.
NOW = datetime(2026, 7, 20, 12, 0)


@pytest.fixture(autouse=True)
def mock_drafter(monkeypatch):
    # Echo the inputs so tests can assert expiry/name handling without an LLM.
    monkeypatch.setattr(qf, "draft_quote_followup",
                        lambda **kw: {"draft_text":
                            f"nudge {kw['quote_number']} expired={kw['is_expired']}"})


@pytest.fixture
def db():
    s = TestingSession()
    # base rows: system user (id matches SYSTEM_TASK_USER_ID default 2) + category
    s.add(TaskUser(id=2, email="jared@impag.mx", display_name="Jared",
                   role="admin", is_active=True))
    s.add(TaskCategory(id=8, name="Seguimiento a cotizaciones", created_by=2))
    s.commit()
    yield s
    for M in (WADraft, Task, Quote, WAConversation, TaskCategory, TaskUser):
        s.query(M).delete()
    s.commit()
    s.close()


def _quote(s, num, **kw):
    defaults = dict(quote_number=num, status="sent", customer_name="Cliente X",
                    customer_phone="+525500000000", created_by="jared@impag.mx",
                    total=1000, validity_days=15, sent_at=NOW - timedelta(days=10))
    defaults.update(kw)
    q = Quote(**defaults)
    s.add(q); s.commit()
    return q


def test_selects_only_open_stale_quotes(db):
    _quote(db, "Q-SENT")                                        # candidate
    _quote(db, "Q-VIEWED", status="viewed", viewed_at=NOW - timedelta(days=4))  # candidate
    _quote(db, "Q-ACCEPTED", accepted_at=NOW - timedelta(days=1))  # excluded
    _quote(db, "Q-DRAFT", status="draft", sent_at=None)        # excluded (never sent)
    _quote(db, "Q-FRESH", sent_at=NOW - timedelta(days=1))     # excluded (too fresh)
    _quote(db, "Q-DEAD", sent_at=NOW - timedelta(days=60))     # excluded (past DEAD)
    nums = {q.quote_number for q in qf.find_stale_quotes(db, now=NOW)}
    assert nums == {"Q-SENT", "Q-VIEWED"}


def test_creates_task_and_marks_idempotent(db):
    _quote(db, "Q1")
    s = qf.sweep_stale_quotes(db, dry_run=False, now=NOW)
    assert s["tasks_created"] == 1 and s["errors"] == 0
    t = db.query(Task).one()
    assert t.category_id == 8 and t.created_by == 2 and t.status == "pending"
    # re-run immediately -> nudged too recently, no new candidate
    assert qf.sweep_stale_quotes(db, dry_run=False, now=NOW)["candidates"] == 0
    q = db.query(Quote).one()
    assert q.followup_count == 1 and q.last_followup_at is not None


def test_dry_run_writes_nothing(db):
    _quote(db, "Q1")
    s = qf.sweep_stale_quotes(db, dry_run=True, now=NOW)
    assert s["candidates"] == 1
    assert db.query(Task).count() == 0 and db.query(Quote).one().followup_count == 0


def test_expired_flag_passed_to_drafter(db):
    _quote(db, "Q-EXP", sent_at=NOW - timedelta(days=20), validity_days=15)  # expired
    qf.sweep_stale_quotes(db, dry_run=False, now=NOW)
    d = db.query(Task).one().description
    assert "expired=True" in d and "vencida" in d


def test_whitespace_name_does_not_abort_batch(db):
    _quote(db, "Q-BAD", customer_name="   ")   # poison-pill input
    _quote(db, "Q-OK", customer_name="Ana")
    s = qf.sweep_stale_quotes(db, dry_run=False, now=NOW)
    assert s["tasks_created"] == 2 and s["errors"] == 0   # neither aborts the other


def test_wa_draft_created_then_deduped(db):
    conv = WAConversation(customer_phone="5215598887777", status="active")
    db.add(conv); db.commit()
    q = _quote(db, "Q-WA", customer_phone="+525598887777")
    s1 = qf.sweep_stale_quotes(db, dry_run=False, now=NOW)
    assert s1["wa_drafts_created"] == 1
    d = db.query(WADraft).one()
    assert d.trigger_message_id is None and d.status == "pending"
    # make re-eligible; a pending draft already exists -> must not stack
    q.last_followup_at = NOW - timedelta(days=30); db.commit()
    s2 = qf.sweep_stale_quotes(db, dry_run=False, now=NOW)
    assert s2["wa_drafts_created"] == 0
    assert db.query(WADraft).filter(WADraft.status == "pending").count() == 1


def test_task_only_when_no_conversation(db):
    _quote(db, "Q-NOWA", customer_phone="+525511112222")   # no matching conversation
    s = qf.sweep_stale_quotes(db, dry_run=False, now=NOW)
    assert s["tasks_created"] == 1 and s["wa_drafts_created"] == 0
