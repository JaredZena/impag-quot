"""
Apoyos discovery: the AI job that finds government support programs.

App Runner closes every HTTP request at 120 s, and a Claude call with web
search can run well past that (confirmed live: a single un-paused topic took
141-186 s). So a topic's Claude work runs on services.background's thread
pool, not inline in the request:

- tick() (POST /jobs/apoyos/tick) decides whether a run should start now:
  recover a stalled run, start a queued manual run, or start a new scheduled
  run when the frequency says it's due and the monthly budget allows it.
- step() (POST /jobs/apoyos/step) is non-blocking: if a topic is already
  running (submitted, not finished) it returns busy:true immediately; a
  topic stuck "running" past STALE_TOPIC_MINUTES (lost to a deploy/crash) is
  marked "error" and the next pending topic is submitted in the same call;
  when nothing is pending or running, the run is finalized and done:true.

The storefront's apoyos.yml workflow calls tick once, then polls step every
~15 s until done=true. Each topic is isolated: an error marks that topic
"error" and the run continues; when the monthly budget runs out mid-run,
every remaining topic is marked "skipped" and the run finishes.
"""

import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models import AIUsage, Apoyo, ApoyoRun
from services import ai_usage, app_settings, background, mailer
from services.apoyos import (
    FREQUENCY_HOURS,
    ApoyosSettings,
    fingerprint_of,
    generate_slug,
    last_finished_schedule_run,
)
from services.campo_taxonomy import (
    LEVELS,
    SECTORS,
    STATES,
    STORE_CATEGORIES,
    SUPPORT_TYPES,
    today_mx,
)
from services.web_orders import _notify_emails

logger = logging.getLogger(__name__)

STALE_RUN_HOURS = 3  # tick(): a run with no topic activity this old is stalled
STALE_TOPIC_MINUTES = (
    6  # step(): a topic still "running" this old was lost to a deploy/crash
)
TOPIC_BUDGET_SECONDS = 240.0  # per topic, across pause_turn continuations
PER_CALL_TIMEOUT_SECONDS = (
    110.0  # one create_message call, now off the HTTP request path
)
MAX_CONTINUATIONS = 3
RECHECK_BATCH = 6
DEFAULT_ADMIN_URL = "https://impag-admin-app.vercel.app"


class ApoyoRunNotFound(Exception):
    pass


def _admin_apoyos_url() -> str:
    base = (os.getenv("ADMIN_APP_URL") or DEFAULT_ADMIN_URL).strip().rstrip("/")
    return f"{base}/apoyos"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


# ── topics ───────────────────────────────────────────────────────────────────


def _blank_topic(key: str, label: str, query: str | None, kind: str, **extra) -> dict:
    return {
        "key": key,
        "label": label,
        "query": query,
        "kind": kind,
        "status": "pending",
        "found": 0,
        "new": 0,
        "updated": 0,
        "closed": 0,
        "error": None,
        "cost_usd": 0.0,
        "web_searches": 0,
        "started_at": None,
        "finished_at": None,
        **extra,
    }


def _recheck_candidate_ids(db: Session, settings: ApoyosSettings) -> list[int]:
    cutoff = _now() - timedelta(days=settings.recheck_days)
    rows = db.query(Apoyo).filter(Apoyo.status == "published").all()

    def effective_seen(a: Apoyo) -> datetime | None:
        dates = [_aware(d) for d in (a.last_seen_at, a.verified_at) if d]
        return max(dates) if dates else None

    stale = [a for a in rows if effective_seen(a) is None or effective_seen(a) < cutoff]
    stale.sort(
        key=lambda a: effective_seen(a) or datetime.min.replace(tzinfo=timezone.utc)
    )
    return [a.id for a in stale]


def build_topics(db: Session, settings: ApoyosSettings) -> list[dict]:
    topics: list[dict] = []
    if settings.include_national:
        for sector in settings.sectors:
            label = SECTORS.get(sector, sector)
            topics.append(
                _blank_topic(
                    f"nacional:{sector}",
                    f"Nacional · {label}",
                    f"programas federales de apoyo a {label.lower()} 2026 SADER CONAFOR CONAPESCA",
                    "nacional",
                )
            )
    for state in settings.states:
        label = STATES.get(state, state)
        topics.append(
            _blank_topic(
                f"estado:{state}",
                f"Estado · {label}",
                f"apoyos estatales al campo {label} 2026: agricultura, ganadería, forestal",
                "estado",
            )
        )
    for i, q in enumerate(settings.extra_queries, start=1):
        topics.append(_blank_topic(f"query:{i}", q[:80], q, "query"))

    recheck_ids = _recheck_candidate_ids(db, settings)
    for i, batch in enumerate(_chunks(recheck_ids, RECHECK_BATCH), start=1):
        topics.append(
            _blank_topic(
                f"recheck:{i}",
                f"Verificación {i}",
                None,
                "recheck",
                apoyo_ids=batch,
            )
        )
    return topics


def _known_apoyos_for_topic(db: Session, topic: dict) -> list[dict]:
    rows = db.query(Apoyo).filter(Apoyo.status.in_(("draft", "published"))).all()
    kind = topic["kind"]
    if kind == "nacional":
        sector = topic["key"].split(":", 1)[1]
        rows = [
            r
            for r in rows
            if sector in (r.sectors or []) and "nacional" in (r.states or [])
        ]
    elif kind == "estado":
        state = topic["key"].split(":", 1)[1]
        rows = [r for r in rows if state in (r.states or [])]
    else:
        rows = []
    rows = rows[:40]
    return [
        {
            "slug": r.slug,
            "title": r.title,
            "organism": r.organism,
            "closes_at": r.closes_at.isoformat() if r.closes_at else None,
        }
        for r in rows
    ]


# ── Claude schemas ───────────────────────────────────────────────────────────


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


PROGRAM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "program": _nullable({"type": "string"}),
        "organism": _nullable({"type": "string"}),
        "level": _nullable({"type": "string", "enum": list(LEVELS)}),
        "states": {"type": "array", "items": {"type": "string", "enum": list(STATES)}},
        "sectors": {
            "type": "array",
            "items": {"type": "string", "enum": list(SECTORS)},
        },
        "support_types": {
            "type": "array",
            "items": {"type": "string", "enum": list(SUPPORT_TYPES)},
        },
        "summary": _nullable({"type": "string"}),
        "description": _nullable({"type": "string"}),
        "beneficiaries": _nullable({"type": "string"}),
        "amount_text": _nullable({"type": "string"}),
        "amount_max_mxn": _nullable({"type": "number"}),
        "requirements": {"type": "array", "items": {"type": "string"}},
        "how_to_apply": {"type": "array", "items": {"type": "string"}},
        "opens_at": _nullable({"type": "string"}),
        "closes_at": _nullable({"type": "string"}),
        "deadline_text": _nullable({"type": "string"}),
        "permanent": {"type": "boolean"},
        "year": _nullable({"type": "integer"}),
        "source_url": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": _nullable({"type": "string"}),
                },
                "required": ["url", "title"],
                "additionalProperties": False,
            },
        },
        "category_slugs": {
            "type": "array",
            "items": {"type": "string", "enum": list(STORE_CATEGORIES)},
        },
        "confidence": {"type": "string", "enum": ["alta", "media", "baja"]},
        "notes": _nullable({"type": "string"}),
        "existing_slug": _nullable({"type": "string"}),
    },
    "required": [
        "title",
        "program",
        "organism",
        "level",
        "states",
        "sectors",
        "support_types",
        "summary",
        "description",
        "beneficiaries",
        "amount_text",
        "amount_max_mxn",
        "requirements",
        "how_to_apply",
        "opens_at",
        "closes_at",
        "deadline_text",
        "permanent",
        "year",
        "source_url",
        "sources",
        "category_slugs",
        "confidence",
        "notes",
        "existing_slug",
    ],
    "additionalProperties": False,
}

SAVE_TOOL: dict = {
    "name": "guardar_programas",
    "description": (
        "Guarda la lista final de programas de apoyo encontrados para este tema "
        "(puede ser una lista vacía si no encontraste ninguno)."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"programas": {"type": "array", "items": PROGRAM_SCHEMA}},
        "required": ["programas"],
        "additionalProperties": False,
    },
}

RECHECK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "still_open": {"type": "boolean"},
        "closes_at": _nullable({"type": "string"}),
        "deadline_text": _nullable({"type": "string"}),
        "notes": _nullable({"type": "string"}),
    },
    "required": ["slug", "still_open", "closes_at", "deadline_text", "notes"],
    "additionalProperties": False,
}

RECHECK_SAVE_TOOL: dict = {
    "name": "guardar_verificaciones",
    "description": (
        "Guarda el resultado de la verificación de cada programa recibido "
        "(una entrada por cada slug)."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"verificaciones": {"type": "array", "items": RECHECK_SCHEMA}},
        "required": ["verificaciones"],
        "additionalProperties": False,
    },
}


def _system_prompt(today: date) -> str:
    return (
        "Eres investigador de programas de apoyo gubernamental para productores del campo, "
        "para IMPAG / Todo Para El Campo (Nuevo Ideal, Durango), que vende equipo de riego, "
        "bombeo solar, invernaderos, drones, geomembranas y equipo para ganadería.\n\n"
        f"Hoy es {today.isoformat()}. Busca programas de apoyo (subsidios, créditos, garantías, "
        "seguros, capacitación, asistencia técnica, insumos, transferencias directas, "
        "infraestructura) para agricultura, ganadería, forestal, pesca y acuacultura, "
        "apicultura, agroindustria y desarrollo rural, que estén actualmente abiertos o por "
        "abrir en 2026, o que sean permanentes. Usa solo fuentes oficiales (gob.mx, sitios de "
        "gobiernos estatales, FIRA, Financiera para el Bienestar, CONAFOR, CONAPESCA, CONAGUA). "
        "Nunca inventes montos ni fechas: usa null cuando no los encuentres con certeza. "
        "Prefiere como source_url la página oficial de la convocatoria o las Reglas de "
        "Operación. Escribe resúmenes y descripciones en español sencillo, para un productor "
        "del campo sin formación técnica. Asigna category_slugs solo cuando el programa "
        "realmente financie ese tipo de equipo (p. ej. sistemas de riego, invernaderos, "
        "energía solar) — no los asignes por adivinanza.\n\n"
        "Termina llamando a guardar_programas exactamente una vez (puede ser con una lista "
        "vacía); no la llames más de una vez."
    )


def _topic_prompt(topic: dict, settings: ApoyosSettings, known: list[dict]) -> str:
    lines = [f"Tema: {topic['label']}", f"Búsqueda sugerida: {topic['query']}"]
    lines.append(f"Reporta como máximo {settings.max_programs_per_topic} programas.")
    if known:
        lines.append(
            "Programas ya conocidos para este tema (si encuentras uno de estos, usa su "
            "existing_slug en vez de reportarlo como nuevo; solo reporta datos que hayan "
            "cambiado):"
        )
        for k in known:
            lines.append(
                f"- slug={k['slug']} | {k['title']} | {k['organism'] or 'sin organismo'} | "
                f"cierra: {k['closes_at'] or 'sin fecha'}"
            )
    return "\n".join(lines)


# ── cleaning AI output into Apoyo columns ───────────────────────────────────


def _clean_text(value, maxlen: int) -> str | None:
    if not value or not isinstance(value, str):
        return None
    cleaned = value.replace("<", "").replace(">", "").strip()
    return cleaned[:maxlen] or None


def _clean_list(values, maxlen: int = 500, maxitems: int = 30) -> list[str] | None:
    if not isinstance(values, list):
        return None
    out = []
    for v in values:
        if isinstance(v, str):
            cleaned = v.replace("<", "").replace(">", "").strip()
            if cleaned:
                out.append(cleaned[:maxlen])
    return out[:maxitems] or None


def _clean_url(value) -> str | None:
    if isinstance(value, str) and re.match(r"^https?://", value.strip(), re.IGNORECASE):
        return value.strip()[:1000]
    return None


def _clean_sources(values) -> list[dict] | None:
    if not isinstance(values, list):
        return None
    out = []
    for v in values:
        if isinstance(v, dict):
            url = _clean_url(v.get("url"))
            if url:
                out.append({"url": url, "title": _clean_text(v.get("title"), 300)})
    return out[:10] or None


def _clean_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _parse_date(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return (urlparse(url).hostname or "").lower() or None
    except ValueError:
        return None


def _clean_program(p: dict) -> dict:
    return {
        "title": (_clean_text(p.get("title"), 300) or "")[:300],
        "program": _clean_text(p.get("program"), 300),
        "organism": _clean_text(p.get("organism"), 200),
        "level": p.get("level") if p.get("level") in LEVELS else None,
        "states": [s for s in (p.get("states") or []) if s in STATES] or None,
        "sectors": [s for s in (p.get("sectors") or []) if s in SECTORS] or None,
        "support_types": [
            s for s in (p.get("support_types") or []) if s in SUPPORT_TYPES
        ]
        or None,
        "summary": _clean_text(p.get("summary"), 2000),
        "description": _clean_text(p.get("description"), 20000),
        "beneficiaries": _clean_text(p.get("beneficiaries"), 2000),
        "amount_text": _clean_text(p.get("amount_text"), 500),
        "amount_max_mxn": _clean_number(p.get("amount_max_mxn")),
        "requirements": _clean_list(p.get("requirements")),
        "how_to_apply": _clean_list(p.get("how_to_apply")),
        "opens_at": _parse_date(p.get("opens_at")),
        "closes_at": _parse_date(p.get("closes_at")),
        "deadline_text": _clean_text(p.get("deadline_text"), 300),
        "permanent": bool(p.get("permanent")),
        "year": p.get("year") if isinstance(p.get("year"), int) else None,
        "source_url": _clean_url(p.get("source_url")),
        "sources": _clean_sources(p.get("sources")),
        "category_slugs": [
            s for s in (p.get("category_slugs") or []) if s in STORE_CATEGORIES
        ]
        or None,
        "confidence": (
            p.get("confidence")
            if p.get("confidence") in ("alta", "media", "baja")
            else None
        ),
        "ai_notes": _clean_text(p.get("notes"), 4000),
    }


def _suggested_changes_text(existing: Apoyo, clean: dict) -> str | None:
    diffs = []
    for field, label in (
        ("closes_at", "fecha de cierre"),
        ("deadline_text", "fecha límite"),
        ("amount_text", "monto"),
        ("summary", "resumen"),
    ):
        new = clean.get(field)
        old = getattr(existing, field)
        if new and str(new) != str(old or ""):
            diffs.append(f"{label}: {new}")
    return "; ".join(diffs) or None


def _merge_one(
    db: Session, run: ApoyoRun, p: dict, settings: ApoyosSettings, now: datetime
) -> tuple[Apoyo | None, bool]:
    title = (p.get("title") or "").strip()
    if not title:
        return None, False

    existing: Apoyo | None = None
    existing_slug = (p.get("existing_slug") or "").strip() or None
    if existing_slug:
        existing = db.query(Apoyo).filter(Apoyo.slug == existing_slug).first()
    fp = fingerprint_of(title, p.get("organism"), p.get("year"))
    if existing is None:
        existing = db.query(Apoyo).filter(Apoyo.fingerprint == fp).first()

    clean = _clean_program(p)

    if existing is None:
        slug = generate_slug(db, title, p.get("organism"), p.get("year"))
        host = _host(clean.get("source_url"))
        auto_pub = (
            settings.auto_publish
            and clean.get("confidence") == "alta"
            and host is not None
            and (host == "gob.mx" or host.endswith(".gob.mx"))
        )
        apoyo = Apoyo(
            slug=slug,
            status="published" if auto_pub else "draft",
            fingerprint=fp,
            discovered_run_id=run.id,
            first_seen_at=now,
            last_seen_at=now,
            **clean,
        )
        if auto_pub:
            apoyo.published_at = now
            apoyo.verified_at = now
            apoyo.verified_by = "apoyos.discovery"
        db.add(apoyo)
        db.flush()
        return apoyo, True

    existing.last_seen_at = now
    if existing.updated_by:
        suggestion = _suggested_changes_text(existing, clean)
        if suggestion:
            note = f"Cambios sugeridos ({now.date().isoformat()}): {suggestion}"
            existing.ai_notes = (
                f"{existing.ai_notes}\n{note}" if existing.ai_notes else note
            )[:4000]
        return existing, False

    for key, value in clean.items():
        setattr(existing, key, value)
    existing.discovered_run_id = run.id
    return existing, False


def _merge_programs(
    db: Session, run: ApoyoRun, programs: list[dict], settings: ApoyosSettings
) -> dict:
    new_count = 0
    updated_count = 0
    now = _now()
    for p in programs[: settings.max_programs_per_topic]:
        if not isinstance(p, dict):
            continue
        try:
            apoyo, is_new = _merge_one(db, run, p, settings, now)
        except Exception:
            logger.exception("apoyos run %s: could not merge a program", run.id)
            continue
        if is_new:
            new_count += 1
        elif apoyo is not None:
            updated_count += 1
    db.commit()
    return {"new": new_count, "updated": updated_count}


# ── Claude calls ─────────────────────────────────────────────────────────────


def _continue_until_tool(
    *,
    feature: str,
    settings: ApoyosSettings,
    system: list[dict],
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    ref_id: int,
    started: float,
):
    """Run create_message, resuming pause_turn stops (a long server-tool loop)
    within the topic's total budget, up to MAX_CONTINUATIONS times. Now off
    the HTTP request path (the caller runs in services.background), so the
    per-call timeout can be generous — a single un-paused call with several
    web searches has been observed taking 140-190 s in production even with
    a 95 s requested timeout; the `timeout` kwarg does not appear to bound
    total wall-clock when Claude chains server-tool calls within one turn.
    Returns (last_response, total_web_searches, total_cost_usd)."""
    web_searches = 0
    cost = Decimal(0)
    response = ai_usage.create_message(
        feature=feature,
        model=settings.model,
        messages=messages,
        max_tokens=max_tokens,
        timeout=min(
            PER_CALL_TIMEOUT_SECONDS,
            max(5.0, TOPIC_BUDGET_SECONDS - (time.monotonic() - started)),
        ),
        effort=settings.effort,
        system=system,
        tools=tools,
        ref_type="apoyo_run",
        ref_id=ref_id,
        allow_pause=True,
    )
    web_searches += ai_usage.tokens_of(response.usage).web_searches
    cost += ai_usage.response_cost(response, settings.model)

    continuations = 0
    while (
        response.stop_reason == "pause_turn"
        and continuations < MAX_CONTINUATIONS
        and (time.monotonic() - started) < TOPIC_BUDGET_SECONDS
    ):
        messages.append({"role": "assistant", "content": response.content})
        remaining = max(5.0, TOPIC_BUDGET_SECONDS - (time.monotonic() - started))
        response = ai_usage.create_message(
            feature=feature,
            model=settings.model,
            messages=messages,
            max_tokens=max_tokens,
            timeout=min(PER_CALL_TIMEOUT_SECONDS, remaining),
            effort=settings.effort,
            system=system,
            tools=tools,
            ref_type="apoyo_run",
            ref_id=ref_id,
            allow_pause=True,
        )
        web_searches += ai_usage.tokens_of(response.usage).web_searches
        cost += ai_usage.response_cost(response, settings.model)
        continuations += 1
    return response, web_searches, cost


def _run_discovery_topic(
    db: Session, run: ApoyoRun, topic: dict, settings: ApoyosSettings
) -> dict:
    today = today_mx()
    known = _known_apoyos_for_topic(db, topic)
    system = [
        {
            "type": "text",
            "text": _system_prompt(today),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages = [{"role": "user", "content": _topic_prompt(topic, settings, known)}]
    tools = ai_usage.web_tools(
        settings.model,
        max_searches=settings.max_searches_per_topic,
        max_fetches=settings.max_fetches_per_topic,
        blocked_domains=settings.blocked_domains or None,
    ) + [SAVE_TOOL]

    response, web_searches, cost = _continue_until_tool(
        feature="apoyos.discovery",
        settings=settings,
        system=system,
        messages=messages,
        tools=tools,
        max_tokens=16000,
        ref_id=run.id,
        started=time.monotonic(),
    )

    data = ai_usage.tool_input(response, "guardar_programas") or {"programas": []}
    programs = [p for p in (data.get("programas") or []) if isinstance(p, dict)]
    counts = _merge_programs(db, run, programs, settings)
    return {
        "found": len(programs),
        "new": counts["new"],
        "updated": counts["updated"],
        "closed": 0,
        "cost_usd": float(cost),
        "web_searches": web_searches,
    }


def _run_recheck_topic(
    db: Session, run: ApoyoRun, topic: dict, settings: ApoyosSettings
) -> dict:
    ids = topic.get("apoyo_ids") or []
    rows = db.query(Apoyo).filter(Apoyo.id.in_(ids)).all() if ids else []
    if not rows:
        return {
            "found": 0,
            "new": 0,
            "updated": 0,
            "closed": 0,
            "cost_usd": 0.0,
            "web_searches": 0,
        }

    today = today_mx()
    lines = [
        f"- slug={r.slug} | {r.title} | {r.organism or 'sin organismo'} | "
        f"fuente: {r.source_url or 'sin fuente'} | "
        f"cierre registrado: {r.closes_at.isoformat() if r.closes_at else (r.deadline_text or 'sin fecha')}"
        for r in rows
    ]
    system = [
        {
            "type": "text",
            "text": (
                "Eres investigador de programas de apoyo gubernamental para productores del "
                f"campo. Hoy es {today.isoformat()}. Verifica, contra la fuente oficial, si cada "
                "programa de la lista sigue abierto. Si la fuente dice que ya cerró, pon "
                "still_open=false y, si la fuente la indica, la nueva fecha de cierre en "
                "closes_at (YYYY-MM-DD). No inventes fechas ni montos: usa null cuando no los "
                "encuentres con certeza. Termina llamando a guardar_verificaciones exactamente "
                "una vez, con una entrada por cada slug recibido."
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages = [
        {"role": "user", "content": "Verifica estos programas:\n" + "\n".join(lines)}
    ]
    tools = ai_usage.web_tools(
        settings.model,
        max_searches=settings.max_searches_per_topic,
        max_fetches=settings.max_fetches_per_topic,
        blocked_domains=settings.blocked_domains or None,
    ) + [RECHECK_SAVE_TOOL]

    response, web_searches, cost = _continue_until_tool(
        feature="apoyos.recheck",
        settings=settings,
        system=system,
        messages=messages,
        tools=tools,
        max_tokens=8000,
        ref_id=run.id,
        started=time.monotonic(),
    )

    data = ai_usage.tool_input(response, "guardar_verificaciones") or {
        "verificaciones": []
    }
    results = {
        v.get("slug"): v
        for v in (data.get("verificaciones") or [])
        if isinstance(v, dict) and v.get("slug")
    }
    now = _now()
    closed = 0
    updated = 0
    for r in rows:
        r.last_seen_at = now
        v = results.get(r.slug)
        if v is None:
            continue
        changed = False
        if v.get("still_open") is False:
            new_close = _parse_date(v.get("closes_at")) or r.closes_at or now.date()
            if r.closes_at != new_close:
                r.closes_at = new_close
                changed = True
            closed += 1
        elif v.get("closes_at"):
            new_close = _parse_date(v.get("closes_at"))
            if new_close and new_close != r.closes_at:
                r.closes_at = new_close
                changed = True
        if v.get("deadline_text"):
            cleaned = _clean_text(v["deadline_text"], 300)
            if cleaned and cleaned != r.deadline_text:
                r.deadline_text = cleaned
                changed = True
        if v.get("notes"):
            note = f"Verificación {today.isoformat()}: {_clean_text(v['notes'], 1000) or ''}"
            r.ai_notes = (f"{r.ai_notes}\n{note}" if r.ai_notes else note)[:4000]
        if changed:
            updated += 1
    db.commit()
    return {
        "found": len(rows),
        "new": 0,
        "updated": updated,
        "closed": closed,
        "cost_usd": float(cost),
        "web_searches": web_searches,
    }


# ── tick / step ──────────────────────────────────────────────────────────────


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def _last_topic_activity(run: ApoyoRun) -> datetime | None:
    """The newest started_at/finished_at across a run's topics — a long
    multi-topic run (each topic can now take minutes in the background)
    should only be judged stalled against its own last activity, not its
    (possibly hours-old) started_at."""
    times = []
    for t in run.topics or []:
        for key in ("started_at", "finished_at"):
            dt = _parse_iso(t.get(key))
            if dt:
                times.append(dt)
    if times:
        return max(times)
    return _aware(run.started_at) if run.started_at else None


def tick(db: Session) -> dict:
    running = db.query(ApoyoRun).filter(ApoyoRun.status == "running").all()
    now = _now()
    stalled = [
        r
        for r in running
        if (_last_topic_activity(r) or now) < now - timedelta(hours=STALE_RUN_HOURS)
    ]
    for run in stalled:
        run.status = "failed"
        run.error = "stalled"
        run.finished_at = now
    if stalled:
        db.commit()

    queued = (
        db.query(ApoyoRun)
        .filter(ApoyoRun.status == "queued")
        .order_by(ApoyoRun.created_at.asc())
        .first()
    )
    if queued is not None:
        queued.status = "running"
        queued.started_at = _now()
        db.commit()
        return {"due": True, "run_id": queued.id, "reason": "queued"}

    settings = app_settings.read(db, "apoyos", ApoyosSettings)
    if not settings.enabled or settings.frequency == "paused":
        return {"due": False, "run_id": None, "reason": "paused"}

    last = last_finished_schedule_run(db)
    hours = FREQUENCY_HOURS[settings.frequency]
    due_by_time = last is None or (
        _now() - _aware(last.finished_at) >= timedelta(hours=hours)
    )
    if not due_by_time:
        return {"due": False, "run_id": None, "reason": "not_due"}

    if settings.monthly_budget_usd > 0:
        spent = ai_usage.spent_usd(db, "apoyos", ai_usage.month_start())
        if spent >= Decimal(str(settings.monthly_budget_usd)):
            return {"due": False, "run_id": None, "reason": "budget_exhausted"}

    topics = build_topics(db, settings)
    run = ApoyoRun(
        trigger="schedule",
        status="running",
        topics=topics,
        started_at=_now(),
        triggered_by="schedule",
    )
    db.add(run)
    db.commit()
    return {"due": True, "run_id": run.id, "reason": "scheduled"}


def _finalize_run(db: Session, run: ApoyoRun) -> None:
    topics = run.topics or []
    run.found_count = sum(t.get("found") or 0 for t in topics)
    run.new_count = sum(t.get("new") or 0 for t in topics)
    run.updated_count = sum(t.get("updated") or 0 for t in topics)
    run.closed_count = sum(t.get("closed") or 0 for t in topics)
    run.status = "done"
    run.finished_at = _now()
    db.commit()

    # The ledger (ai_usage) is the authoritative source for cost/searches —
    # every create_message() call during this run recorded a row against
    # ref_type="apoyo_run", ref_id=run.id, so re-sum from there rather than
    # trust the topic-level running totals (which can miss a crash mid-topic).
    ledger = (
        db.query(
            func.coalesce(func.sum(AIUsage.cost_usd), 0),
            func.coalesce(func.sum(AIUsage.web_search_requests), 0),
        )
        .filter(AIUsage.ref_type == "apoyo_run", AIUsage.ref_id == run.id)
        .one()
    )
    run.cost_usd = Decimal(str(ledger[0] or 0))
    run.web_searches = int(ledger[1] or 0)
    db.commit()

    if run.new_count > 0:
        try:
            _notify_new_finds(db, run)
        except Exception:
            logger.exception("apoyos run %s: finish email not sent", run.id)


def _process_topic_job(run_id: int, topic_key: str, settings: ApoyosSettings):
    """The background.submit() job: processes exactly one topic (already
    marked "running" by step()) in its own session, then writes its result
    and status. Never finalizes the run itself — the next step() call does
    that once no topic is pending or running."""

    def job(db: Session) -> None:
        run = db.get(ApoyoRun, run_id)
        if run is None:
            return
        topics = run.topics or []
        idx = next((i for i, t in enumerate(topics) if t.get("key") == topic_key), None)
        if idx is None:
            return
        topic = topics[idx]
        try:
            if topic["kind"] == "recheck":
                result = _run_recheck_topic(db, run, topic, settings)
            else:
                result = _run_discovery_topic(db, run, topic, settings)
            topic.update(result)
            topic["status"] = "done"
        except Exception as exc:
            db.rollback()
            logger.exception("apoyos run %s topic %s failed", run_id, topic_key)
            run = db.get(ApoyoRun, run_id)
            if run is None:
                return
            topics = run.topics or []
            idx = next(
                (i for i, t in enumerate(topics) if t.get("key") == topic_key), None
            )
            if idx is None:
                return
            topic = topics[idx]
            topic["status"] = "error"
            topic["error"] = str(exc)[:300]
        topic["finished_at"] = _now().isoformat()
        topics[idx] = topic
        run.topics = topics
        flag_modified(run, "topics")
        db.commit()

    return job


def step(db: Session, run_id: int) -> dict:
    """Advance a running discovery run by one topic.

    A topic's Claude call runs on services.background's thread pool — a step
    call never blocks on it. While a topic is "running" (submitted, not yet
    finished), repeated step() calls (the storefront workflow polls every
    ~15 s) return busy:true and do nothing; a topic stuck "running" past
    STALE_TOPIC_MINUTES (lost to a deploy/crash) is marked "error" and the
    next pending topic starts in the same call. When nothing is pending or
    running, the run is finalized and done:true is returned."""
    run = db.get(ApoyoRun, run_id)
    if run is None:
        raise ApoyoRunNotFound()

    topics = run.topics or []

    def _progress(ts: list[dict]) -> dict:
        done = sum(1 for t in ts if t.get("status") in ("done", "error", "skipped"))
        return {"done": done, "total": len(ts)}

    if run.status != "running":
        return {
            "run_id": run.id,
            "done": True,
            "status": run.status,
            "topic": None,
            "progress": _progress(topics),
        }

    running_idx = next(
        (i for i, t in enumerate(topics) if t.get("status") == "running"), None
    )
    if running_idx is not None:
        topic = topics[running_idx]
        started = _parse_iso(topic.get("started_at"))
        if started is not None and _now() - started < timedelta(
            minutes=STALE_TOPIC_MINUTES
        ):
            return {
                "run_id": run.id,
                "done": False,
                "busy": True,
                "status": run.status,
                "topic": topic,
                "progress": _progress(topics),
            }
        # Lost to a deploy/crash: free the run to move on to the next topic.
        topic["status"] = "error"
        topic["error"] = "stalled"
        topic["finished_at"] = _now().isoformat()
        topics[running_idx] = topic
        run.topics = topics
        flag_modified(run, "topics")
        db.commit()

    pending_idx = next(
        (i for i, t in enumerate(topics) if t.get("status") == "pending"), None
    )
    if pending_idx is None:
        _finalize_run(db, run)
        topics = run.topics or []
        return {
            "run_id": run.id,
            "done": True,
            "status": run.status,
            "topic": None,
            "progress": _progress(topics),
        }

    settings = app_settings.read(db, "apoyos", ApoyosSettings)
    if settings.monthly_budget_usd > 0:
        spent = ai_usage.spent_usd(db, "apoyos", ai_usage.month_start())
        if spent >= Decimal(str(settings.monthly_budget_usd)):
            for t in topics[pending_idx:]:
                if t.get("status") == "pending":
                    t["status"] = "skipped"
                    t["finished_at"] = _now().isoformat()
            run.topics = topics
            flag_modified(run, "topics")
            db.commit()
            skipped_topic = topics[pending_idx]
            _finalize_run(db, run)
            topics = run.topics or []
            return {
                "run_id": run.id,
                "done": True,
                "status": run.status,
                "topic": skipped_topic,
                "progress": _progress(topics),
            }

    topic = topics[pending_idx]
    topic["status"] = "running"
    topic["started_at"] = _now().isoformat()
    topics[pending_idx] = topic
    run.topics = topics
    flag_modified(run, "topics")
    db.commit()

    background.submit(
        f"apoyos-run-{run.id}-{topic['key']}",
        _process_topic_job(run.id, topic["key"], settings),
    )

    return {
        "run_id": run.id,
        "done": False,
        "busy": True,
        "status": run.status,
        "started": topic["key"],
        "topic": topic,
        "progress": _progress(topics),
    }


# ── notify ───────────────────────────────────────────────────────────────────


def _notify_new_finds(db: Session, run: ApoyoRun) -> None:
    settings = app_settings.read(db, "apoyos", ApoyosSettings)
    recipients = settings.notify_emails or _notify_emails()
    if not recipients:
        return
    new_rows = (
        db.query(Apoyo)
        .filter(Apoyo.discovered_run_id == run.id)
        .filter(Apoyo.first_seen_at.isnot(None))
        .order_by(Apoyo.first_seen_at.desc())
        .limit(20)
        .all()
    )
    if not new_rows:
        return
    items = "".join(
        f"<li>{escape(r.title or '')} — {escape(r.organism or 'sin organismo')}</li>"
        for r in new_rows
    )
    admin_link = _admin_apoyos_url()
    html = (
        f"<p>{run.new_count} apoyos nuevos por revisar.</p>"
        f"<ul>{items}</ul>"
        f'<p><a href="{escape(admin_link)}">Abrir en el admin</a></p>'
    )
    message = {
        "from": "Todo Para El Campo <cotizaciones@todoparaelcampo.com.mx>",
        "to": recipients,
        "subject": f"{run.new_count} apoyos nuevos por revisar",
        "html": html,
    }
    mailer.send(
        message,
        tag=f"apoyos run {run.id} found",
        idempotency_key=f"apoyos-run/{run.id}/found",
    )
