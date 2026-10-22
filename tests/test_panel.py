"""
Hermetic tests for the "Cómo vamos" panel: Google email parsing, the
business_metric upsert, GET /panel, and owners-only finance (OWNER_EMAILS).
SQLite in-memory, Gmail/Search Console stubbed — no network, no real DB.

Run: venv/bin/python -m pytest tests/test_panel.py -q
"""

import os

os.environ["DATABASE_URL"] = "postgresql://test:test@panel-tests.invalid/testdb"
os.environ["ALEMBIC_RUNNING"] = "1"
os.environ["DISABLE_AUTH"] = "true"
os.environ.setdefault("ALLOWED_EMAILS", "dev@local.test")

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth
import routes.panel as panel_routes
from models import Base, BusinessMetric, Quote, Sale, get_db
from services import business_metrics as bm

TABLES = [BusinessMetric.__table__, Sale.__table__, Quote.__table__]
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
TODAY = date(2026, 10, 22)

SEP_2026 = (
    "Informe de rendimiento mensual 27 personas vieron IMPAG el último mes. "
    "Rendimiento de tu empresa del septiembre de 2026: 27 interacciones "
    "Un resumen de tu rendimiento 0 llamadas 1 clics en el chat Sin cambios "
    "26 personas solicitaron instrucciones sobre cómo llegar -31% 0 visitas al "
    "sitio web desde el perfil 61 vistas del perfil -4% Un resumen de tu "
    "rendimiento 0 llamadas 1 clics en el chat"
)
OCT_2024 = (
    "Un resumen de tu rendimiento 0 llamadas 0 mensajes 59 personas solicitaron "
    "instrucciones sobre cómo llegar -26% 3 visitas al sitio web desde el perfil "
    "12 vistas del perfil +20%"
)
SEP_2025 = (
    "Un resumen de tu rendimiento 1 llamada 60 personas solicitaron instrucciones "
    "sobre cómo llegar -6% 1 visita al sitio web desde el perfil 56 vistas del perfil"
)
REVIEW = (
    "Felicitaciones, tienes una nueva opinión de 5 estrellas Leer la opinión Luis "
    "(Translated by Google) We highly recommend them"
)


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    session = TestingSession()
    yield session
    session.close()


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(panel_routes, "_today", lambda: TODAY)
    app = FastAPI()
    app.include_router(panel_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# ── parsing ──────────────────────────────────────────────────────────────────


def test_report_month_from_subject():
    subject = "IMPAG, aquí está tu informe de rendimiento para septiembre de 2026"
    assert bm.report_month(subject) == date(2026, 9, 1)
    assert bm.report_month("IMPAG, ¿abres en Nochebuena?") is None


def test_parse_report_current_format():
    assert bm.parse_report(SEP_2026) == {
        "calls": 0,
        "chat_clicks": 1,
        "directions": 26,
        "website_clicks": 0,
        "profile_views": 61,
        "interactions": 27,
    }


def test_parse_report_older_formats():
    # 2024: "mensajes" instead of "clics en el chat"
    assert bm.parse_report(OCT_2024)["chat_clicks"] == 0
    assert bm.parse_report(OCT_2024)["website_clicks"] == 3
    # 2025: singular "1 llamada", "1 visita", no chat line at all
    sep = bm.parse_report(SEP_2025)
    assert sep["calls"] == 1 and sep["website_clicks"] == 1
    assert "chat_clicks" not in sep
    assert bm.parse_report("sin resumen") == {}


def test_parse_review():
    assert bm.parse_review("Luis dejó una opinión sobre IMPAG", REVIEW) == ("Luis", 5)
    assert bm.parse_review("IMPAG, aquí está tu informe", REVIEW) is None


def test_html_to_text_strips_preheader_padding():
    raw = "<style>.x{}</style><p>27‍ ‍͏ personas</p>"
    assert bm.html_to_text(raw) == "27 personas"


# ── sync ─────────────────────────────────────────────────────────────────────


def _stub_gmail(monkeypatch, messages):
    """messages: {id: (subject, text, received)}; query → ids by subject kind."""

    def ids(token, query):
        want_review = "opinión" in query
        return [
            mid
            for mid, (subject, _, _) in messages.items()
            if ("dejó una opinión" in subject) == want_review
        ]

    monkeypatch.setattr(bm, "_gmail_ids", ids)
    monkeypatch.setattr(bm, "_gmail_message", lambda token, mid: messages[mid])


def test_sync_business_profile_is_idempotent(db, monkeypatch):
    _stub_gmail(
        monkeypatch,
        {
            "m1": (
                "IMPAG, aquí está tu informe de rendimiento para septiembre de 2026",
                SEP_2026,
                date(2026, 10, 8),
            ),
            "m2": (
                "IMPAG, aquí está tu informe de rendimiento para octubre de 2024",
                OCT_2024,
                date(2024, 11, 6),
            ),
            "r1": ("Luis dejó una opinión sobre IMPAG", REVIEW, date(2026, 2, 11)),
        },
    )
    first = bm.sync_business_profile(db, "tok")
    db.commit()
    assert first["months"] == 2 and first["reviews"] == 1
    assert first["latest_month"] == "2026-09-01"
    assert first["new"] == 6 + 6 + 1  # two months × 6 metrics + one review

    second = bm.sync_business_profile(db, "tok")
    db.commit()
    assert second["new"] == 0 and second["updated"] == 0
    assert db.query(BusinessMetric).count() == 13


def test_sync_google_metrics_keeps_profile_when_search_console_fails(db, monkeypatch):
    monkeypatch.setattr(bm, "get_metrics_access_token", lambda: "tok")
    monkeypatch.setattr(
        bm,
        "sync_business_profile",
        lambda db, token: (
            db.add(
                BusinessMetric(
                    source="gbp",
                    metric="calls",
                    period="month",
                    period_start=date(2026, 9, 1),
                    value=Decimal(0),
                )
            )
            or {"months": 1}
        ),
    )

    def boom(db, token):
        raise RuntimeError("insufficient scope")

    monkeypatch.setattr(bm, "sync_search_console", boom)
    result = bm.sync_google_metrics(db)
    assert result["business_profile"] == {"months": 1}
    assert "insufficient scope" in result["search_console"]["error"]
    assert db.query(BusinessMetric).count() == 1


def test_dry_run_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(bm, "get_metrics_access_token", lambda: "tok")
    _stub_gmail(
        monkeypatch,
        {
            "m1": (
                "IMPAG, aquí está tu informe de rendimiento para septiembre de 2026",
                SEP_2026,
                date(2026, 10, 8),
            )
        },
    )
    monkeypatch.setattr(bm, "sync_search_console", lambda db, token: {"days": 0})
    result = bm.sync_google_metrics(db, dry_run=True)
    assert result["business_profile"]["new"] == 6
    assert db.query(BusinessMetric).count() == 0


# ── GET /panel ───────────────────────────────────────────────────────────────


def _metric(db, source, metric, period, start, value, label=None, ref=""):
    db.add(
        BusinessMetric(
            source=source,
            metric=metric,
            period=period,
            period_start=start,
            value=Decimal(str(value)),
            label=label,
            ref=ref,
        )
    )


def _sale(db, day, amount, pending=0, quarantined=False, row=0):
    db.add(
        Sale(
            sheet_tab="VENTAS 2026",
            source_row=row,
            sale_date=day,
            customer_name=f"Cliente {row}",
            amount=Decimal(str(amount)),
            pending_amount=Decimal(str(pending)),
            quarantined=quarantined,
        )
    )


def test_panel_google_numbers(client, db):
    _metric(db, "gbp", "directions", "month", date(2026, 8, 1), 38)
    _metric(db, "gbp", "directions", "month", date(2026, 9, 1), 26)
    _metric(db, "gbp", "calls", "month", date(2026, 9, 1), 0)
    _metric(db, "gbp_review", "stars", "event", date(2026, 2, 9), 5, "Francisco", "r2")
    _metric(db, "gbp_review", "stars", "event", date(2026, 2, 11), 4, "Luis", "r1")
    # Search Console: 56 days of 2 clicks, last day 2026-10-19
    for i in range(56):
        _metric(db, "gsc", "clicks", "day", date(2026, 8, 25) + timedelta(days=i), 2)
    db.commit()

    g = client.get("/panel").json()["data"]["google"]
    assert g["reviews"]["count"] == 2
    assert g["reviews"]["avg_stars"] == 4.5
    assert g["reviews"]["latest"][0]["author"] == "Luis"
    assert g["profile"]["month"] == "2026-09-01"
    assert g["profile"]["values"]["directions"] == 26
    assert g["profile"]["previous"]["directions"] == 38
    assert g["search"]["last_day"] == "2026-10-19"
    assert g["search"]["clicks_28d"] == 56
    assert g["search"]["clicks_prev_28d"] == 56
    assert g["search"]["goal"] == 130


def test_panel_month_sales(client, db):
    _sale(db, date(2026, 10, 3), 1000, row=1)
    _sale(db, date(2026, 10, 20), 500, pending=200, row=2)
    _sale(db, date(2026, 10, 21), 9999, quarantined=True, row=3)
    _sale(db, date(2026, 9, 10), 700, row=4)
    _sale(db, date(2026, 9, 28), 800, row=5)  # after Sep 22: not "same days"
    _sale(db, date(2026, 8, 1), 300, pending=300, row=6)
    db.commit()

    d = client.get("/panel").json()["data"]
    assert d["ventas"] == {
        "month": "2026-10-01",
        "total": 1500.0,
        "count": 2,
        "prev_same_days_total": 700.0,
        "prev_same_days_count": 1,
    }
    assert d["is_owner"] is True  # DISABLE_AUTH dev user


def test_panel_empty_db(client):
    d = client.get("/panel").json()["data"]
    assert d["google"]["reviews"]["count"] == 0
    assert d["google"]["profile"]["month"] is None
    assert d["google"]["search"]["clicks_28d"] is None
    assert d["ventas"]["total"] == 0


# ── owners ───────────────────────────────────────────────────────────────────


def test_require_owner(monkeypatch):
    monkeypatch.setattr(auth, "DISABLE_AUTH", False)
    monkeypatch.setattr(auth, "OWNER_EMAILS", {"jared@impag.test"})
    assert auth.is_owner({"email": "Jared@impag.test"})
    assert not auth.is_owner({"email": "hernan@impag.test"})
    assert auth.require_owner({"email": "jared@impag.test"})["email"]
    with pytest.raises(HTTPException) as e:
        auth.require_owner({"email": "hernan@impag.test"})
    assert e.value.status_code == 403


def test_no_owner_emails_means_everyone(monkeypatch):
    # Decision 2026-10-22: the whole team sees finance until OWNER_EMAILS is set.
    monkeypatch.setattr(auth, "DISABLE_AUTH", False)
    monkeypatch.setattr(auth, "OWNER_EMAILS", set())
    assert auth.is_owner({"email": "hernan@impag.test"})


def test_finance_router_is_owners_only():
    import routes.finance as finance_routes

    deps = [d.dependency for d in finance_routes.router.dependencies]
    assert auth.require_owner in deps


# ── Umami + tienda ───────────────────────────────────────────────────────────

PAGEVIEWS = {
    "pageviews": [
        {"x": "2026-10-20 00:00:00", "y": 40},
        {"x": "2026-10-21 00:00:00", "y": 50},
    ],
    "sessions": [
        {"x": "2026-10-20 00:00:00", "y": 12},
        {"x": "2026-10-21T00:00:00Z", "y": 15},
    ],
}
EVENTS = [
    {"x": "whatsapp-click", "t": "2026-10-20 00:00:00", "y": 2},
    {"x": "quote-add", "t": "2026-10-21 00:00:00", "y": 1},
    {"x": "whatsapp-click", "t": "2026-10-21 00:00:00", "y": 3},
]


def test_parse_umami_series():
    pv = bm.parse_umami_pageviews(PAGEVIEWS)
    assert pv[date(2026, 10, 20)] == {"pageviews": 40, "visits": 12}
    assert pv[date(2026, 10, 21)]["visits"] == 15
    ev = bm.parse_umami_events(EVENTS)
    assert ev[date(2026, 10, 21)] == {"quote-add": 1, "whatsapp-click": 3}
    assert bm.parse_umami_events({"data": EVENTS})[date(2026, 10, 20)] == {
        "whatsapp-click": 2
    }
    assert bm.parse_umami_events([]) == {}


def test_sync_umami_skips_without_key(db, monkeypatch):
    monkeypatch.delenv("UMAMI_API_KEY", raising=False)
    assert "skipped" in bm.sync_umami(db)


def test_sync_umami_upserts(db, monkeypatch):
    monkeypatch.setenv("UMAMI_API_KEY", "k")
    calls = []

    def fake_get(path, key, **params):
        calls.append((path, key, params["unit"], params["timezone"]))
        return PAGEVIEWS if path.endswith("/pageviews") else EVENTS

    monkeypatch.setattr(bm, "_umami_get", fake_get)
    first = bm.sync_umami(db)
    db.commit()
    assert first["days"] == 2 and first["latest_day"] == "2026-10-21"
    assert first["new"] == 2 + 2 + 3  # pageviews+visits ×2 days, 3 event cells
    assert bm.sync_umami(db)["new"] == 0
    assert calls[0][1:] == ("k", "day", "America/Mexico_City")


def test_google_unconfigured_still_runs_umami(db, monkeypatch):
    def unconfigured():
        raise bm.MetricsNotConfigured("Falta GOOGLE_METRICS_REFRESH_TOKEN")

    monkeypatch.setattr(bm, "get_metrics_access_token", unconfigured)
    monkeypatch.setattr(bm, "sync_umami", lambda db: {"days": 3})
    result = bm.sync_google_metrics(db)
    assert "Falta" in result["business_profile"]["error"]
    assert "Falta" in result["search_console"]["error"]
    assert result["umami"] == {"days": 3}


def _web_quote(db, n, created, total, status=None):
    db.add(
        Quote(
            quote_number=f"WEB-{n}",
            customer_name="Web",
            customer_phone="6770000000",
            status="sent",
            total=Decimal(str(total)),
            created_by="tienda-web",
            payment_status=status,
            created_at=created,
        )
    )


def test_panel_tienda(client, db):
    from datetime import datetime as dt

    from services.quote_capture import BUSINESS_TZ

    # Umami: 56 complete days of 3 visits + 1 WhatsApp click, plus today (ignored)
    for i in range(57):
        day = TODAY - timedelta(days=i)
        _metric(db, "umami", "visits", "day", day, 100 if i == 0 else 3)
        _metric(db, "umami", "whatsapp-click", "day", day, 1)
    _web_quote(db, 1, dt(2026, 10, 20, 12, tzinfo=BUSINESS_TZ), 1000, "approved")
    _web_quote(db, 2, dt(2026, 10, 5, 12, tzinfo=BUSINESS_TZ), 500)
    _web_quote(db, 3, dt(2026, 9, 10, 12, tzinfo=BUSINESS_TZ), 700, "cancelled")
    _web_quote(db, 4, dt(2026, 8, 1, 12, tzinfo=BUSINESS_TZ), 300, "mismatch")
    db.add(
        Quote(
            quote_number="Q-1",
            customer_name="Mostrador",
            customer_phone="6770000001",
            status="sent",
            total=Decimal("9999"),
            created_by="hernan@impag.test",
            payment_status="approved",
            created_at=dt(2026, 10, 20, 12, tzinfo=BUSINESS_TZ),
        )
    )
    db.commit()

    t = client.get("/panel").json()["data"]["tienda"]
    assert t["last_day"] == "2026-10-21"
    assert t["visits_28d"] == 84 and t["visits_prev_28d"] == 84
    assert t["whatsapp_28d"] == 28
    assert t["web_quotes_28d"] == 2 and t["web_quotes_prev_28d"] == 1
    assert t["paid_orders_28d"] == 1 and t["paid_total_28d"] == 1000.0
    assert t["paid_orders_all"] == 2 and t["paid_total_all"] == 1300.0


def test_panel_tienda_without_umami(client):
    t = client.get("/panel").json()["data"]["tienda"]
    assert t["last_day"] is None and t["visits_28d"] is None
    assert t["web_quotes_28d"] == 0
