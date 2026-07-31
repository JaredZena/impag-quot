"""
Campaign planner — LLM generation for the "Campañas" feature.

Two-step generation:
  1. generate_campaign_brief(): topic -> strategy brief (objective, goals,
     audience, size, channel plan, research). One structured-output LLM call.
  2. generate_campaign_plan(): persisted brief -> phases with dated items
     (posts, WhatsApp messages, prep tasks, research). One LLM call.
Plus generate_post_content(): a focused call that turns a single "post" item
into caption + image prompt for a SocialPost row.

claude-opus-5 call rules (differ from the sonnet-4-6 code elsewhere in the
repo): NO temperature/top_p/top_k (HTTP 400), no `thinking` param (thinking is
on by default), structured outputs via output_config={"effort", "format"} —
every schema object sets additionalProperties=false and lists all properties
in `required`. The first text block is then guaranteed to be valid JSON.

All deterministic post-guards (frequency caps, channel allowlist, date
clamping, WhatsApp gating) live HERE in code, not in the prompt, so they are
enforced regardless of what the model returns.
"""

import json
import os
from datetime import date, timedelta

import anthropic
from sqlalchemy.orm import Session

from config import claude_api_key
from models import Product, ProductCategory
from routes.social_config import (
    CHANNEL_FORMATS,
    CONTACT_INFO,
    IMPAG_BRAND_CONTEXT,
    SPECIAL_DATES,
)
from routes.social_context import load_durango_context

CAMPAIGN_MODEL = os.getenv("CAMPAIGN_MODEL", "claude-opus-5")
CAMPAIGN_EFFORT = os.getenv("CAMPAIGN_EFFORT", "medium")
MAX_OUTPUT_TOKENS = 16000

# The 7 usable campaign channels (docs/social-calendar-channels.md).
KNOWN_CHANNELS = (
    "fb-post",
    "fb-reel",
    "fb-story",
    "tiktok",
    "wa-status",
    "wa-broadcast",
    "wa-message",
)
# Hard weekly frequency caps; channels missing here have no cap.
CHANNEL_WEEKLY_CAPS = {
    "fb-post": 4,
    "fb-reel": 3,
    "tiktok": 3,
    "wa-status": 7,
    "wa-broadcast": 2,
}
WA_CHANNELS = ("wa-status", "wa-broadcast", "wa-message")

VALID_SIZES = ("chica", "mediana", "grande")
VALID_ITEM_KINDS = ("post", "whatsapp", "task", "research")

DEFAULT_DURATION_DAYS = 28

# Hard channel rules, restated in-prompt (from docs/social-calendar-channels.md).
CHANNEL_RULES = """CANALES DISPONIBLES (usa EXACTAMENTE estos ids):
- fb-post: publicación en Facebook (se replica automáticamente a Instagram). Máx 4/semana.
- fb-reel: reel de FB/IG (video corto 15-90s). Máx 3/semana.
- fb-story: historia de FB/IG (efímera, visual). Sin tope estricto.
- tiktok: carrusel de 2-3 imágenes con texto grande (NUNCA video). Máx 3/semana.
- wa-status: estado de WhatsApp (efímero, visual). Máx 7/semana.
- wa-broadcast: difusión de WhatsApp a lista de clientes. Máx 2/semana.
- wa-message: mensaje directo de WhatsApp 1:1. Uso puntual.

REGLA DE WHATSAPP (whatsapp_notify):
- whatsapp_notify=true SOLO para campañas de alto impacto: tamaño "grande", o
  "mediana" con disparador urgente/estacional (promoción fuerte, llegada de
  stock, alerta antiheladas, oferta con fecha límite).
- El WA broadcast debe aportar valor, no spam: máximo 1-2 por semana, dirigido
  a clientes VIP.
- Campañas "chica": SOLO FB/IG, whatsapp_notify=false y sin canales wa-*.

CONVENCIONES DE CTA por canal:
- WhatsApp: invitar a responder "Sí" para recibir información.
- FB/IG: dirigir a DM o al enlace de todoparaelcampo.com.mx.
- TikTok: palabra clave en comentarios -> contacto por WhatsApp."""


class CampaignGenerationError(Exception):
    """Generation failed. `status_code` hints the HTTP status the route should map to."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


# ── LLM call plumbing ────────────────────────────────────────────────────────


def _call_claude(system_prompt: str, user_prompt: str, schema: dict) -> dict:
    """One structured-output call; returns the parsed JSON dict.

    Raises CampaignGenerationError on refusal, truncation, billing (402) or any
    other API error.
    """
    client = anthropic.Anthropic(api_key=claude_api_key)
    try:
        response = client.messages.create(
            model=CAMPAIGN_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={
                "effort": CAMPAIGN_EFFORT,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
    except anthropic.APIStatusError as e:
        if e.status_code == 402:
            raise CampaignGenerationError(
                "ANTHROPIC_CREDITS_EXHAUSTED: Los créditos de la API de Claude "
                "se agotaron. Recarga en https://console.anthropic.com/settings/billing",
                status_code=402,
            ) from e
        raise CampaignGenerationError(
            f"Error de la API de Claude ({e.status_code}): {str(e)[:200]}",
            status_code=502,
        ) from e

    if response.stop_reason == "refusal":
        raise CampaignGenerationError("El modelo rechazó la solicitud")
    if response.stop_reason == "max_tokens":
        raise CampaignGenerationError(
            "La respuesta del modelo fue truncada (max_tokens); intenta de nuevo"
        )

    text = next(
        (b.text for b in response.content if getattr(b, "type", None) == "text"),
        None,
    )
    if not text:
        raise CampaignGenerationError("El modelo no devolvió contenido de texto")
    try:
        return json.loads(text)
    except (ValueError, TypeError) as e:
        raise CampaignGenerationError(f"Respuesta JSON inválida del modelo: {e}") from e


# ── Structured-output schemas ────────────────────────────────────────────────
# Rules: every object sets additionalProperties=false and lists ALL its
# properties in `required`. No minLength/maximum constraints (unsupported).

_GOAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["goal", "metric", "target"],
    "properties": {
        "goal": {"type": "string"},
        "metric": {"type": "string"},
        "target": {"type": "string"},
    },
}

_CHANNEL_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["channel", "frequency_per_week", "rationale"],
    "properties": {
        "channel": {"type": "string", "enum": list(KNOWN_CHANNELS)},
        "frequency_per_week": {"type": "integer"},
        "rationale": {"type": "string"},
    },
}

_IMPORTANT_DATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["date", "name", "relevance"],
    "properties": {
        "date": {"type": "string"},
        "name": {"type": "string"},
        "relevance": {"type": "string"},
    },
}

BRIEF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "objective",
        "audience",
        "size",
        "size_rationale",
        "duration_days",
        "goals",
        "key_messages",
        "channel_plan",
        "research",
    ],
    "properties": {
        "title": {"type": "string"},
        "objective": {"type": "string"},
        "audience": {"type": "string"},
        "size": {"type": "string", "enum": list(VALID_SIZES)},
        "size_rationale": {"type": "string"},
        "duration_days": {"type": "integer"},
        "goals": {"type": "array", "items": _GOAL_SCHEMA},
        "key_messages": {"type": "array", "items": {"type": "string"}},
        "channel_plan": {
            "type": "object",
            "additionalProperties": False,
            "required": ["channels", "whatsapp_notify", "whatsapp_rationale"],
            "properties": {
                "channels": {"type": "array", "items": _CHANNEL_ENTRY_SCHEMA},
                "whatsapp_notify": {"type": "boolean"},
                "whatsapp_rationale": {"type": "string"},
            },
        },
        "research": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "seasonality_notes",
                "market_context",
                "important_dates",
                "product_focus",
            ],
            "properties": {
                "seasonality_notes": {"type": "string"},
                "market_context": {"type": "string"},
                "important_dates": {"type": "array", "items": _IMPORTANT_DATE_SCHEMA},
                "product_focus": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

_PLAN_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "kind",
        "channel",
        "scheduled_date",
        "title",
        "description",
        "content",
    ],
    "properties": {
        "kind": {"type": "string", "enum": list(VALID_ITEM_KINDS)},
        "channel": {"type": ["string", "null"]},
        "scheduled_date": {"type": ["string", "null"]},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "content": {"type": ["string", "null"]},
    },
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["phases"],
    "properties": {
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "description",
                    "goal",
                    "start_date",
                    "end_date",
                    "items",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "goal": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "items": {"type": "array", "items": _PLAN_ITEM_SCHEMA},
                },
            },
        },
    },
}

POST_CONTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["caption", "image_prompt", "post_type", "content_tone"],
    "properties": {
        "caption": {"type": "string"},
        "image_prompt": {"type": "string"},
        "post_type": {"type": "string"},
        "content_tone": {"type": "string"},
    },
}


# ── Context assembly ─────────────────────────────────────────────────────────


def _seasonality_context(month: int) -> str:
    try:
        return load_durango_context(month) or ""
    except Exception:
        return ""


def _active_category_names(db: Session) -> list[str]:
    """Distinct category names with at least one active product (cheap query)."""
    try:
        rows = (
            db.query(ProductCategory.name)
            .join(Product, Product.category_id == ProductCategory.id)
            .filter(Product.is_active.is_(True))
            .distinct()
            .order_by(ProductCategory.name)
            .all()
        )
        return [r[0] for r in rows if r[0]]
    except Exception:
        db.rollback()
        return []


def _special_dates_between(start: date, end: date) -> str:
    lines = []
    d = start
    while d <= end:
        special = SPECIAL_DATES.get((d.month, d.day))
        if special:
            lines.append(f"- {d.isoformat()}: {special['name']} ({special['type']})")
        d += timedelta(days=1)
    return "\n".join(lines) or "(ninguna fecha especial en el periodo)"


def _channel_catalog_brief() -> str:
    lines = []
    for ch in KNOWN_CHANNELS:
        fmt = CHANNEL_FORMATS.get(ch, {})
        cap = CHANNEL_WEEKLY_CAPS.get(ch)
        cap_txt = f"máx {cap}/semana" if cap else "sin tope"
        lines.append(f"- {ch}: {fmt.get('notes', '')} ({cap_txt})")
    return "\n".join(lines)


# ── Deterministic post-guards ────────────────────────────────────────────────


def _guard_channel_plan(channel_plan: dict, size: str) -> dict:
    """Clamp per-week frequencies to caps, drop unknown channels, and force
    whatsapp_notify off for campañas chicas."""
    plan = dict(channel_plan or {})
    channels = []
    for entry in plan.get("channels") or []:
        if not isinstance(entry, dict):
            continue
        channel = entry.get("channel")
        if channel not in KNOWN_CHANNELS:
            continue
        freq = entry.get("frequency_per_week")
        try:
            freq = int(freq)
        except (TypeError, ValueError):
            freq = 1
        freq = max(1, freq)
        cap = CHANNEL_WEEKLY_CAPS.get(channel)
        if cap is not None:
            freq = min(freq, cap)
        channels.append(
            {
                "channel": channel,
                "frequency_per_week": freq,
                "rationale": entry.get("rationale") or "",
            }
        )
    plan["channels"] = channels
    plan["whatsapp_notify"] = bool(plan.get("whatsapp_notify"))
    plan["whatsapp_rationale"] = plan.get("whatsapp_rationale") or ""
    if size == "chica":
        plan["whatsapp_notify"] = False
    return plan


def _parse_iso_date(value) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _clamp_date(d: date | None, start: date | None, end: date | None) -> date | None:
    if d is None:
        return None
    if start and d < start:
        return start
    if end and d > end:
        return end
    return d


def _guard_plan(raw: dict, campaign) -> list[dict]:
    """Apply the deterministic plan guards; returns normalized phase dicts."""
    channel_plan = campaign.channel_plan or {}
    whatsapp_notify = bool(channel_plan.get("whatsapp_notify"))
    # Item channels must come from the campaign's channel_plan (spec §3 fn 2),
    # not merely the global known set — otherwise the model can schedule posts
    # on channels the strategy never selected.
    allowed_channels = {
        entry.get("channel")
        for entry in channel_plan.get("channels") or []
        if isinstance(entry, dict)
    } & set(KNOWN_CHANNELS)
    start = campaign.start_date
    end = campaign.end_date

    phases = []
    for p in raw.get("phases") or []:
        if not isinstance(p, dict):
            continue
        items = []
        for it in p.get("items") or []:
            if not isinstance(it, dict):
                continue
            kind = it.get("kind")
            if kind not in VALID_ITEM_KINDS:
                continue
            channel = it.get("channel")
            if kind in ("post", "whatsapp"):
                if channel not in allowed_channels:
                    continue  # channel is required and must be in the channel_plan
            else:
                channel = None  # null-out channels for task/research kinds
            # WhatsApp gate: no wa-* items at all when notify is off.
            if channel in WA_CHANNELS and not whatsapp_notify:
                continue
            title = (it.get("title") or "").strip()
            if not title:
                continue
            scheduled = _clamp_date(
                _parse_iso_date(it.get("scheduled_date")), start, end
            )
            items.append(
                {
                    "kind": kind,
                    "channel": channel,
                    "scheduled_date": scheduled,
                    "title": title,
                    "description": it.get("description") or None,
                    "content": it.get("content") or None,
                }
            )
        phases.append(
            {
                "name": (p.get("name") or "Fase").strip() or "Fase",
                "description": p.get("description") or None,
                "goal": p.get("goal") or None,
                "start_date": _parse_iso_date(p.get("start_date")),
                "end_date": _parse_iso_date(p.get("end_date")),
                "items": items,
            }
        )

    # Phases sorted by start_date (None last) -> sort_order.
    phases.sort(key=lambda p: (p["start_date"] is None, p["start_date"] or date.max))
    for order, p in enumerate(phases):
        p["sort_order"] = order

    # Enforce weekly caps across the WHOLE campaign: group post/whatsapp items
    # by (channel, ISO week), keep the first N per cap, drop the rest.
    week_counts: dict = {}
    for p in phases:
        kept = []
        for it in p["items"]:
            channel = it["channel"]
            cap = CHANNEL_WEEKLY_CAPS.get(channel)
            if (
                it["kind"] in ("post", "whatsapp")
                and channel
                and cap is not None
                and it["scheduled_date"] is not None
            ):
                iso = it["scheduled_date"].isocalendar()
                key = (channel, iso[0], iso[1])
                if week_counts.get(key, 0) >= cap:
                    continue  # over the weekly cap — drop
                week_counts[key] = week_counts.get(key, 0) + 1
            kept.append(it)
        # Items get sort_order by (scheduled_date, index); undated prep items first.
        kept = [
            it
            for _, _, it in sorted(
                (it["scheduled_date"] or date.min, idx, it)
                for idx, it in enumerate(kept)
            )
        ]
        for order, it in enumerate(kept):
            it["sort_order"] = order
        p["items"] = kept

    return phases


# ── Function 1: strategy brief ───────────────────────────────────────────────

BRIEF_SYSTEM_PROMPT = f"""Eres el estratega de marketing de IMPAG. Diseñas campañas
de marketing completas, realistas y medibles para productores agrícolas mexicanos.
Respondes SIEMPRE en español (es-MX).

{IMPAG_BRAND_CONTEXT}

CONTACTO: WhatsApp {CONTACT_INFO["whatsapp"]} · {CONTACT_INFO["web"]} · {CONTACT_INFO["social"]}

{CHANNEL_RULES}

Reglas de contenido:
- No inventes precios, cifras ni estadísticas que no estén en el contexto.
- Metas medibles y alcanzables para una PyME agrícola (no cifras corporativas).
- El tamaño de campaña (chica/mediana/grande) debe justificarse con el tema y
  la temporada."""


def generate_campaign_brief(
    db: Session,
    topic: str,
    start_date: date,
    duration_weeks: int | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Generate the campaign strategy brief for a topic. Returns a validated
    dict ready to persist (the route creates the Campaign row)."""
    seasonality = _seasonality_context(start_date.month)
    categories = _active_category_names(db)
    horizon_days = (duration_weeks * 7) if duration_weeks else 90
    specials = _special_dates_between(
        start_date, start_date + timedelta(days=horizon_days)
    )

    duration_hint = (
        f"El usuario fijó la duración en {duration_weeks} semanas; usa duration_days={duration_weeks * 7}."
        if duration_weeks
        else "Propón una duración razonable en días (14-56) según el tamaño de la campaña."
    )
    user_prompt = f"""Diseña el brief estratégico de una campaña de marketing IMPAG.

TEMA DE LA CAMPAÑA (dado por el usuario):
{topic.strip()}

FECHA DE INICIO: {start_date.isoformat()}
{duration_hint}
NOTAS DEL USUARIO: {notes.strip() if notes else "(sin notas)"}

CONTEXTO DE TEMPORADA Y MERCADO (Durango, mes {start_date.month}):
{seasonality or "(sin contexto de temporada disponible)"}

FECHAS ESPECIALES EN EL PERIODO:
{specials}

CATEGORÍAS DE PRODUCTO ACTIVAS EN CATÁLOGO:
{", ".join(categories) if categories else "(catálogo no disponible)"}

CATÁLOGO DE CANALES:
{_channel_catalog_brief()}

Devuelve el brief completo en el formato JSON solicitado."""

    data = _call_claude(BRIEF_SYSTEM_PROMPT, user_prompt, BRIEF_SCHEMA)

    # Deterministic guards (code, not prompt).
    size = data.get("size") if data.get("size") in VALID_SIZES else "mediana"
    channel_plan = _guard_channel_plan(data.get("channel_plan") or {}, size)

    try:
        duration_days = int(data.get("duration_days"))
    except (TypeError, ValueError):
        duration_days = DEFAULT_DURATION_DAYS
    if duration_weeks:
        duration_days = duration_weeks * 7
    duration_days = max(1, duration_days)
    end_date = start_date + timedelta(days=duration_days - 1)

    return {
        "topic": topic.strip(),
        "title": (data.get("title") or topic.strip())[:300],
        "objective": data.get("objective"),
        "audience": data.get("audience"),
        "size": size,
        "status": "draft",
        "start_date": start_date,
        "end_date": end_date,
        "goals": data.get("goals") or [],
        "key_messages": data.get("key_messages") or [],
        "channel_plan": channel_plan,
        "research": data.get("research") or {},
        "notes": notes,
        "generation_model": CAMPAIGN_MODEL,
        "created_by": created_by,
    }


# ── Function 2: phases + items plan ──────────────────────────────────────────

PLAN_SYSTEM_PROMPT = f"""Eres el estratega de marketing de IMPAG. Conviertes un brief
de campaña en un plan operativo: fases con fechas y una lista de acciones
concretas (posts, mensajes de WhatsApp, tareas de preparación e investigación).
Respondes SIEMPRE en español (es-MX).

{IMPAG_BRAND_CONTEXT}

{CHANNEL_RULES}

Reglas del plan:
- 3 a 5 fases que cubran TODO el periodo [inicio, fin] sin huecos ni traslapes,
  con arco clásico (p. ej. Preparación → Expectativa → Lanzamiento → Refuerzo →
  Cierre/Seguimiento). Cada fase: name, description, goal, start_date, end_date.
- Cada item: kind (post|whatsapp|task|research); para post/whatsapp el channel es
  OBLIGATORIO y debe salir de los canales del channel_plan; para task/research
  channel=null. scheduled_date en formato YYYY-MM-DD dentro de la fase (null
  permitido para tareas de preparación e investigación).
- description = brief de contenido concreto: tipo de post, ángulo y CTA según
  las convenciones del canal.
- Para kind=whatsapp incluye también content = el texto propuesto del mensaje
  (2-4 frases, tono informal-profesional, sin inventar precios).
- Respeta los topes semanales por canal en TODO el periodo de la campaña.
- Items wa-broadcast SOLO si channel_plan.whatsapp_notify es true.
- Incluye tareas de preparación (diseñar artes, recopilar fotos/testimonios,
  verificar stock y precios) y 1-2 items de investigación al inicio."""

_WEEKDAYS_ES = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]


def generate_campaign_plan(db: Session, campaign) -> dict:
    """Generate phases with nested items for a persisted campaign brief.
    Returns {"phases": [...]} with normalized, guard-validated dicts."""
    start = campaign.start_date or date.today()
    end = campaign.end_date or (start + timedelta(days=DEFAULT_DURATION_DAYS - 1))
    channel_plan = campaign.channel_plan or {}

    user_prompt = f"""Genera el plan operativo de esta campaña IMPAG.

BRIEF PERSISTIDO:
- Título: {campaign.title}
- Objetivo: {campaign.objective or "(sin objetivo)"}
- Audiencia: {campaign.audience or "(sin audiencia)"}
- Tamaño: {campaign.size}
- Metas: {json.dumps(campaign.goals or [], ensure_ascii=False)}
- Mensajes clave: {json.dumps(campaign.key_messages or [], ensure_ascii=False)}
- Plan de canales: {json.dumps(channel_plan, ensure_ascii=False)}
- Investigación: {json.dumps(campaign.research or {}, ensure_ascii=False)}

PERIODO: del {start.isoformat()} ({_WEEKDAYS_ES[start.weekday()]}) al {end.isoformat()} ({_WEEKDAYS_ES[end.weekday()]}).
Hoy es {date.today().isoformat()} ({_WEEKDAYS_ES[date.today().weekday()]}).

Devuelve las fases con sus items en el formato JSON solicitado."""

    raw = _call_claude(PLAN_SYSTEM_PROMPT, user_prompt, PLAN_SCHEMA)
    return {"phases": _guard_plan(raw, campaign)}


# ── Function 3: post content for a single item ───────────────────────────────

POST_CONTENT_SYSTEM_PROMPT = f"""Eres el creador de contenido social de IMPAG.
Escribes captions listos para publicar y prompts de imagen para el equipo de
diseño. Respondes SIEMPRE en español (es-MX).

{IMPAG_BRAND_CONTEXT}

CONTACTO: WhatsApp {CONTACT_INFO["whatsapp"]} · {CONTACT_INFO["web"]} · {CONTACT_INFO["social"]}

Reglas:
- No inventes precios, cifras ni disponibilidad.
- Respeta el formato del canal (longitud del caption, prioridad visual).
- El image_prompt describe la imagen en detalle para un diseñador o un
  generador de imágenes (en español, específico, sin texto incrustado salvo
  que el canal lo requiera)."""


def generate_post_content(item, campaign) -> dict:
    """Focused generation for one kind='post' campaign item: caption + image
    prompt (+ post_type/content_tone) for the SocialPost row.

    NOTE: the existing social content engine (routes/social_content_engine.py)
    expects the topic/content strategy objects from its own pipeline, so direct
    reuse is awkward for a campaign item; this focused structured-output call
    is the cleaner path (choice allowed by the spec).
    """
    channel = item.channel or "fb-post"
    fmt = CHANNEL_FORMATS.get(channel, {})
    fmt_brief = (
        f"Canal: {channel}. Formato {fmt.get('aspect_ratio', '1:1')}, caption máx "
        f"{fmt.get('caption_max_chars', 2000)} caracteres. {fmt.get('notes', '')}"
    )
    user_prompt = f"""Genera el contenido de este post de campaña.

CAMPAÑA: {campaign.title}
Objetivo: {campaign.objective or "(sin objetivo)"}
Mensajes clave: {json.dumps(campaign.key_messages or [], ensure_ascii=False)}

ITEM DEL PLAN:
- Título: {item.title}
- Brief de contenido: {item.description or "(sin brief)"}
- Fecha objetivo: {item.scheduled_date.isoformat() if item.scheduled_date else "(sin fecha)"}

FORMATO DEL CANAL:
{fmt_brief}

Devuelve caption, image_prompt, post_type y content_tone en el formato JSON solicitado."""

    data = _call_claude(POST_CONTENT_SYSTEM_PROMPT, user_prompt, POST_CONTENT_SCHEMA)
    if not (data.get("caption") or "").strip():
        raise CampaignGenerationError("El modelo devolvió un caption vacío")
    return {
        "caption": data["caption"],
        "image_prompt": data.get("image_prompt") or None,
        "post_type": data.get("post_type") or None,
        "content_tone": data.get("content_tone") or None,
    }
