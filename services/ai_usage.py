"""
Claude calls for the storefront AI tools, with a usage and cost ledger.

create_message() is the one door the Agroproyectos designer
(services/agroproyectos.py) and the Apoyos discovery job
(services/apoyos_discovery.py) use to call Claude: it builds the request for
the admin-chosen model (thinking, effort, web-tool versions and refusal
fallbacks differ per model), times it, and writes one models.AIUsage row per
request, success or failure, in its own session so a caller's rollback never
loses the record. The Cotizador solar records through record_response().

Costs are list prices in USD (PRICES, per million tokens; web search $10 per
1,000 searches; web fetch is not metered). They are an estimate of the bill,
not the invoice: negotiated discounts and taxes are not applied.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

import anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import claude_api_key
from models import AIUsage, SessionLocal

logger = logging.getLogger(__name__)

# Models the admin may choose for a tool. claude-opus-5 is the default.
ModelChoice = Literal[
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-sonnet-5",
    "claude-haiku-4-5",
    "claude-fable-5-1",
]
MODEL_LABELS: dict[str, str] = {
    "claude-opus-5": "Claude Opus 5 (recomendado)",
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5": "Claude Sonnet 5 (más rápido y económico)",
    "claude-haiku-4-5": "Claude Haiku 4.5 (el más económico, menos preciso)",
    "claude-fable-5-1": "Claude Fable 5.1 (el más capaz, el más caro)",
}
Effort = Literal["low", "medium", "high"]

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_USD = Decimal("0.01")


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_write: Decimal  # 5-minute TTL (1.25x input)
    cache_read: Decimal


def _p(i: str, o: str, w: str, r: str) -> Price:
    return Price(Decimal(i), Decimal(o), Decimal(w), Decimal(r))


# Longest prefix wins ("claude-opus-5-5" before "claude-opus-5"), so dated
# snapshot ids such as claude-haiku-4-5-20251001 price as their family.
PRICES: dict[str, Price] = {
    "claude-fable-5-1": _p("10", "50", "12.5", "0.25"),
    "claude-fable-5": _p("10", "50", "12.5", "1"),
    "claude-opus-5-5": _p("4", "20", "5", "0.2"),
    "claude-opus-5": _p("5", "25", "6.25", "0.5"),
    "claude-opus-4-8": _p("5", "25", "6.25", "0.5"),
    "claude-opus-4-7": _p("5", "25", "6.25", "0.5"),
    "claude-opus-4-6": _p("5", "25", "6.25", "0.5"),
    "claude-sonnet-5": _p("2", "10", "2.5", "0.2"),
    "claude-sonnet-4-6": _p("3", "15", "3.75", "0.3"),
    "claude-haiku-4-5": _p("1", "5", "1.25", "0.1"),
}
_DEFAULT_PRICE = PRICES["claude-opus-5"]
MILLION = Decimal(1_000_000)


def price_for(model: str | None) -> Price:
    name = (model or "").strip()
    for prefix in sorted(PRICES, key=len, reverse=True):
        if name.startswith(prefix):
            return PRICES[prefix]
    return _DEFAULT_PRICE


@dataclass
class Tokens:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    web_searches: int = 0
    web_fetches: int = 0


def tokens_of(usage: Any) -> Tokens:
    """Read an SDK usage object (or a dict) defensively."""
    if usage is None:
        return Tokens()

    def get(obj: Any, name: str) -> int:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    server = (
        usage.get("server_tool_use")
        if isinstance(usage, dict)
        else getattr(usage, "server_tool_use", None)
    )
    return Tokens(
        input=get(usage, "input_tokens"),
        output=get(usage, "output_tokens"),
        cache_write=get(usage, "cache_creation_input_tokens"),
        cache_read=get(usage, "cache_read_input_tokens"),
        web_searches=get(server, "web_search_requests") if server is not None else 0,
        web_fetches=get(server, "web_fetch_requests") if server is not None else 0,
    )


def cost_usd(model: str | None, tokens: Tokens) -> Decimal:
    price = price_for(model)
    cost = (
        Decimal(tokens.input) * price.input
        + Decimal(tokens.output) * price.output
        + Decimal(tokens.cache_write) * price.cache_write
        + Decimal(tokens.cache_read) * price.cache_read
    ) / MILLION + Decimal(tokens.web_searches) * WEB_SEARCH_USD
    return cost.quantize(Decimal("0.000001"))


# ── ledger ───────────────────────────────────────────────────────────────────


def record(
    *,
    feature: str,
    model: str | None,
    requested_model: str | None,
    tokens: Tokens,
    duration_ms: int | None,
    status: str = "ok",
    error_code: str | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
) -> Decimal:
    """Write one AIUsage row in its own session; returns the cost. Never raises."""
    cost = cost_usd(model or requested_model, tokens)
    db = SessionLocal()
    try:
        db.add(
            AIUsage(
                feature=feature[:60],
                model=(model or "")[:60] or None,
                requested_model=(requested_model or "")[:60] or None,
                input_tokens=tokens.input,
                output_tokens=tokens.output,
                cache_creation_input_tokens=tokens.cache_write,
                cache_read_input_tokens=tokens.cache_read,
                web_search_requests=tokens.web_searches,
                web_fetch_requests=tokens.web_fetches,
                cost_usd=cost,
                duration_ms=duration_ms,
                status=status[:20],
                error_code=(error_code or "")[:60] or None,
                ref_type=ref_type,
                ref_id=ref_id,
            )
        )
        db.commit()
    except Exception:  # the ledger must never break the tool it measures
        db.rollback()
        logger.exception("ai usage: could not record %s", feature)
    finally:
        db.close()
    return cost


def record_response(
    response: Any,
    *,
    feature: str,
    requested_model: str | None,
    started: float,
    ref_type: str | None = None,
    ref_id: int | None = None,
) -> Decimal:
    """Record a response produced outside create_message (Cotizador solar)."""
    stop = getattr(response, "stop_reason", None)
    status = "ok" if stop not in ("refusal", "max_tokens") else stop
    return record(
        feature=feature,
        model=getattr(response, "model", None),
        requested_model=requested_model,
        tokens=tokens_of(getattr(response, "usage", None)),
        duration_ms=int((time.monotonic() - started) * 1000),
        status=status,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def spent_usd(db: Session, feature_prefix: str, since: datetime) -> Decimal:
    total = (
        db.query(func.coalesce(func.sum(AIUsage.cost_usd), 0))
        .filter(AIUsage.feature.like(f"{feature_prefix}%"), AIUsage.created_at >= since)
        .scalar()
    )
    return Decimal(str(total or 0))


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ── the Claude request ───────────────────────────────────────────────────────


class AIUnavailable(Exception):
    """No key, an API error, a refusal or a truncated answer. `reason` is a
    short code safe to log and to return to the storefront."""

    def __init__(self, reason: str, response: Any = None):
        super().__init__(reason)
        self.reason = reason
        self.response = response


def _is_haiku(model: str) -> bool:
    return model.startswith("claude-haiku")


def _supports_fallbacks(model: str) -> bool:
    return model.startswith(("claude-opus-5", "claude-fable-5"))


def web_tools(
    model: str,
    *,
    max_searches: int,
    max_fetches: int,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict]:
    """Server-side web search/fetch definitions in the version `model` supports."""
    basic = _is_haiku(model)
    tools: list[dict] = []
    if max_searches > 0:
        search: dict = {
            "type": "web_search_20250305" if basic else "web_search_20260209",
            "name": "web_search",
            "max_uses": max_searches,
            "user_location": {
                "type": "approximate",
                "country": "MX",
                "region": "Durango",
                "timezone": "America/Monterrey",
            },
        }
        if allowed_domains:
            search["allowed_domains"] = allowed_domains
        elif blocked_domains:
            search["blocked_domains"] = blocked_domains
        tools.append(search)
    if max_fetches > 0:
        fetch: dict = {
            "type": "web_fetch_20250910" if basic else "web_fetch_20260209",
            "name": "web_fetch",
            "max_uses": max_fetches,
        }
        if blocked_domains and not allowed_domains:
            fetch["blocked_domains"] = blocked_domains
        tools.append(fetch)
    return tools


def _client() -> anthropic.Anthropic:
    if not claude_api_key:
        raise AIUnavailable("no_api_key")
    return anthropic.Anthropic(api_key=claude_api_key, max_retries=1)


def create_message(
    *,
    feature: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    timeout: float,
    effort: str | None = None,
    system: str | list[dict] | None = None,
    tools: list[dict] | None = None,
    output_schema: dict | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
    allow_pause: bool = False,
) -> Any:
    """One Claude request, recorded in the ledger. Returns the SDK message.

    Raises AIUnavailable on a missing key, an API/network error, a refusal or
    a max_tokens stop. A pause_turn stop (a long server-tool loop) is
    returned when allow_pause is True, so the caller can resume it."""
    client = _client()
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    output_config: dict = {}
    if effort and not _is_haiku(model):
        output_config["effort"] = effort
    if output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}
    if output_config:
        kwargs["output_config"] = output_config
    betas: list[str] = []
    if _supports_fallbacks(model):
        betas.append(FALLBACK_BETA)
        kwargs["fallbacks"] = "default"
    if betas:
        kwargs["betas"] = betas

    started = time.monotonic()
    try:
        response = client.with_options(timeout=timeout).beta.messages.create(**kwargs)
    except anthropic.RateLimitError as exc:
        _record_failure(feature, model, started, "ai_rate_limited", ref_type, ref_id)
        raise AIUnavailable("ai_rate_limited") from exc
    except anthropic.APIStatusError as exc:
        code = f"ai_http_{exc.status_code}"
        logger.error("%s: Claude HTTP %s", feature, exc.status_code)
        _record_failure(feature, model, started, code, ref_type, ref_id)
        raise AIUnavailable("ai_error") from exc
    except anthropic.APIConnectionError as exc:  # includes timeouts
        timed_out = isinstance(exc, anthropic.APITimeoutError)
        logger.error("%s: Claude unreachable: %s", feature, type(exc).__name__)
        _record_failure(
            feature,
            model,
            started,
            "ai_timeout" if timed_out else "ai_unreachable",
            ref_type,
            ref_id,
            status="timeout" if timed_out else "error",
        )
        raise AIUnavailable("ai_timeout" if timed_out else "ai_unreachable") from exc

    stop = response.stop_reason
    status = "ok" if stop not in ("refusal", "max_tokens") else stop
    record(
        feature=feature,
        model=getattr(response, "model", None),
        requested_model=model,
        tokens=tokens_of(response.usage),
        duration_ms=int((time.monotonic() - started) * 1000),
        status=status,
        ref_type=ref_type,
        ref_id=ref_id,
    )
    if stop in ("refusal", "max_tokens"):
        logger.error("%s: Claude stopped with %s", feature, stop)
        raise AIUnavailable(f"ai_{stop}", response)
    if stop == "pause_turn" and not allow_pause:
        raise AIUnavailable("ai_pause_turn", response)
    return response


def _record_failure(
    feature: str,
    model: str,
    started: float,
    code: str,
    ref_type: str | None,
    ref_id: int | None,
    status: str = "error",
) -> None:
    record(
        feature=feature,
        model=None,
        requested_model=model,
        tokens=Tokens(),
        duration_ms=int((time.monotonic() - started) * 1000),
        status=status,
        error_code=code,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def response_cost(response: Any, requested_model: str | None = None) -> Decimal:
    return cost_usd(
        getattr(response, "model", None) or requested_model, tokens_of(response.usage)
    )


def json_output(response: Any) -> dict:
    """The JSON document of a structured-output response (output_schema)."""
    text = next(
        (b.text for b in response.content if getattr(b, "type", None) == "text"), ""
    )
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AIUnavailable("ai_bad_json", response) from exc
    if not isinstance(data, dict):
        raise AIUnavailable("ai_bad_json", response)
    return data


def tool_input(response: Any, name: str) -> dict | None:
    """The input of the last call to the client tool `name`, or None."""
    found = None
    for block in response.content:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == name
        ):
            raw = block.input
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    continue
            if isinstance(raw, dict):
                found = raw
    return found


# ── admin summary (routes/ai_usage.py) ───────────────────────────────────────


def summary(db: Session, days: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    rows = db.query(AIUsage).filter(AIUsage.created_at >= since).all()

    def bucket() -> dict:
        return {
            "calls": 0,
            "errors": 0,
            "cost_usd": Decimal(0),
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "web_searches": 0,
            "duration_ms_total": 0,
        }

    total = bucket()
    by_feature: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    for row in rows:
        created = (
            row.created_at
            if row.created_at.tzinfo
            else row.created_at.replace(tzinfo=timezone.utc)
        )
        day = created.date().isoformat()
        for target in (
            total,
            by_feature.setdefault(row.feature, bucket()),
            by_model.setdefault(
                row.model or row.requested_model or "desconocido", bucket()
            ),
            by_day.setdefault(day, bucket()),
        ):
            target["calls"] += 1
            target["errors"] += 0 if row.status == "ok" else 1
            target["cost_usd"] += Decimal(str(row.cost_usd or 0))
            target["input_tokens"] += row.input_tokens or 0
            target["output_tokens"] += row.output_tokens or 0
            target["cache_read_input_tokens"] += row.cache_read_input_tokens or 0
            target["cache_creation_input_tokens"] += (
                row.cache_creation_input_tokens or 0
            )
            target["web_searches"] += row.web_search_requests or 0
            target["duration_ms_total"] += row.duration_ms or 0

    def out(b: dict) -> dict:
        calls = b["calls"] or 1
        return {
            "calls": b["calls"],
            "errors": b["errors"],
            "cost_usd": float(b["cost_usd"]),
            "input_tokens": b["input_tokens"],
            "output_tokens": b["output_tokens"],
            "cache_read_input_tokens": b["cache_read_input_tokens"],
            "cache_creation_input_tokens": b["cache_creation_input_tokens"],
            "web_searches": b["web_searches"],
            "avg_duration_ms": round(b["duration_ms_total"] / calls),
            "avg_cost_usd": float(b["cost_usd"] / calls),
        }

    days_list = []
    cursor = since.date()
    while cursor <= now.date():
        key = cursor.isoformat()
        days_list.append({"day": key, **out(by_day.get(key, bucket()))})
        cursor += timedelta(days=1)

    return {
        "since": since.isoformat(),
        "days": days,
        "total": out(total),
        "by_feature": sorted(
            ({"feature": k, **out(v)} for k, v in by_feature.items()),
            key=lambda x: -x["cost_usd"],
        ),
        "by_model": sorted(
            ({"model": k, **out(v)} for k, v in by_model.items()),
            key=lambda x: -x["cost_usd"],
        ),
        "by_day": days_list,
    }


def recent(db: Session, limit: int = 100, feature: str | None = None) -> list[dict]:
    query = db.query(AIUsage).order_by(AIUsage.created_at.desc(), AIUsage.id.desc())
    if feature:
        query = query.filter(AIUsage.feature.like(f"{feature}%"))
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "feature": r.feature,
            "model": r.model,
            "requested_model": r.requested_model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cache_read_input_tokens": r.cache_read_input_tokens,
            "cache_creation_input_tokens": r.cache_creation_input_tokens,
            "web_search_requests": r.web_search_requests,
            "web_fetch_requests": r.web_fetch_requests,
            "cost_usd": float(r.cost_usd or 0),
            "duration_ms": r.duration_ms,
            "status": r.status,
            "error_code": r.error_code,
            "ref_type": r.ref_type,
            "ref_id": r.ref_id,
        }
        for r in query.limit(max(1, min(limit, 500))).all()
    ]
