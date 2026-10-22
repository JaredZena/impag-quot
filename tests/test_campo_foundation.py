"""Shared Campo IA foundation: settings store and the AI usage ledger."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, ValidationError

import main
from models import AIUsage, AppSetting, Base, SessionLocal, engine
from services import ai_usage, app_settings


@pytest.fixture(autouse=True)
def _clean():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(AIUsage).delete()
    db.query(AppSetting).delete()
    db.commit()
    db.close()
    yield


class DemoSettings(BaseModel):
    enabled: bool = True
    limit: int = Field(default=5, ge=1, le=10)
    names: list[str] = []


def test_settings_defaults_partial_and_stale_rows():
    db = SessionLocal()
    assert app_settings.read(db, "demo", DemoSettings) == DemoSettings()
    db.add(AppSetting(key="demo", value={"limit": 99, "names": ["a"], "gone": 1}))
    db.commit()
    s = app_settings.read(db, "demo", DemoSettings)
    assert s.limit == 5 and s.names == [
        "a"
    ]  # invalid field falls back, unknown dropped
    db.close()


def test_settings_write_validates_and_merges():
    db = SessionLocal()
    app_settings.write(db, "demo", DemoSettings, {"limit": 7}, "a@b.mx")
    s = app_settings.write(db, "demo", DemoSettings, {"enabled": False}, "a@b.mx")
    assert s.limit == 7 and s.enabled is False
    with pytest.raises(ValidationError):
        app_settings.write(db, "demo", DemoSettings, {"limit": 0}, "a@b.mx")
    assert app_settings.read(db, "demo", DemoSettings).limit == 7
    assert app_settings.updated_info(db, "demo")["updated_by"] == "a@b.mx"
    db.close()


def test_cost_per_model_prefix_and_web_search():
    t = ai_usage.Tokens(
        input=1_000_000, output=100_000, cache_read=1_000_000, web_searches=3
    )
    assert ai_usage.cost_usd("claude-opus-5", t) == Decimal(
        "8.030000"
    )  # 5 + 2.5 + 0.5 + 0.03
    assert ai_usage.cost_usd("claude-opus-5-5", t) == Decimal(
        "6.230000"
    )  # 4 + 2 + 0.2 + 0.03
    assert ai_usage.cost_usd("claude-haiku-4-5-20251001", t) == Decimal("1.630000")


def test_tokens_of_reads_server_tool_use():
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=2,
        server_tool_use=SimpleNamespace(web_search_requests=4),
    )
    t = ai_usage.tokens_of(usage)
    assert (t.input, t.output, t.cache_write, t.cache_read, t.web_searches) == (
        10,
        5,
        0,
        2,
        4,
    )


def test_record_spend_and_summary_route():
    ai_usage.record(
        feature="agroproyectos.plan",
        model="claude-opus-5",
        requested_model="claude-opus-5",
        tokens=ai_usage.Tokens(input=2000, output=1000),
        duration_ms=1500,
    )
    ai_usage.record(
        feature="apoyos.discovery",
        model=None,
        requested_model="claude-opus-5",
        tokens=ai_usage.Tokens(),
        duration_ms=10,
        status="error",
        error_code="ai_timeout",
    )
    db = SessionLocal()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    assert ai_usage.spent_usd(db, "agroproyectos", since) == Decimal("0.035")
    db.close()
    client = TestClient(main.app)
    data = client.get("/ai-usage/summary?days=7").json()["data"]
    assert data["total"]["calls"] == 2 and data["total"]["errors"] == 1
    assert {r["feature"] for r in data["by_feature"]} == {
        "agroproyectos.plan",
        "apoyos.discovery",
    }
    events = client.get("/ai-usage/events?feature=apoyos").json()["data"]
    assert len(events) == 1 and events[0]["error_code"] == "ai_timeout"


def test_web_tools_versions():
    assert (
        ai_usage.web_tools("claude-opus-5", max_searches=3, max_fetches=2)[0]["type"]
        == "web_search_20260209"
    )
    assert (
        ai_usage.web_tools("claude-haiku-4-5", max_searches=3, max_fetches=0)[0]["type"]
        == "web_search_20250305"
    )
