"""
Apoyos gubernamentales: government support programs for Todo Para El Campo
producers (/apoyos on the storefront).

This module owns settings (ApoyosSettings), the admin-editable program model
(ApoyoInput), slug/fingerprint generation, serialization (ApoyoAdmin /
ApoyoPublic / Run), the admin list/filter/stats queries, status transitions
and the storefront feed query. The discovery job itself (runs, topics, the
Claude step) lives in services/apoyos_discovery.py.
"""

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import Apoyo, ApoyoRun
from services import ai_usage
from services.ai_usage import Effort, ModelChoice
from services.campo_taxonomy import (
    SECTORS,
    STATES,
    WINDOW_LABELS,
    LevelSlug,
    SectorSlug,
    StateSlug,
    StoreCategorySlug,
    SupportTypeSlug,
    days_left,
    options,
    today_mx,
    window_of,
)
from services.web_orders import EMAIL_RE, MARKUP_RE

# ── frequency ────────────────────────────────────────────────────────────────

Frequency = Literal[
    "daily", "every_2_days", "every_3_days", "weekly", "biweekly", "monthly", "paused"
]

# Minimum hours between scheduled runs, a little under the nominal interval so
# a daily cron never slips a day.
FREQUENCY_HOURS: dict[str, int | None] = {
    "daily": 20,
    "every_2_days": 44,
    "every_3_days": 68,
    "weekly": 164,
    "biweekly": 332,
    "monthly": 716,
    "paused": None,
}
FREQUENCY_LABELS: dict[str, str] = {
    "daily": "Diario",
    "every_2_days": "Cada 2 días",
    "every_3_days": "Cada 3 días",
    "weekly": "Semanal",
    "biweekly": "Quincenal",
    "monthly": "Mensual",
    "paused": "Pausado",
}


# ── settings ─────────────────────────────────────────────────────────────────


class ApoyosSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    frequency: Frequency = "weekly"
    states: list[StateSlug] = Field(
        default_factory=lambda: [
            "durango",
            "chihuahua",
            "coahuila",
            "zacatecas",
            "sinaloa",
            "nayarit",
        ]
    )
    include_national: bool = True
    sectors: list[SectorSlug] = Field(default_factory=lambda: list(SECTORS.keys()))
    extra_queries: list[str] = Field(
        default_factory=lambda: [
            "Producción para el Bienestar 2026",
            "Sembrando Vida 2026",
            "Tecnificación de riego 2026",
            "FIRA financiamiento agropecuario",
            "Financiera para el Bienestar crédito al campo",
        ],
        max_length=20,
    )
    auto_publish: bool = False
    model: ModelChoice = "claude-opus-5"
    effort: Effort = "medium"
    max_searches_per_topic: int = Field(default=5, ge=1, le=10)
    max_fetches_per_topic: int = Field(default=4, ge=0, le=10)
    max_programs_per_topic: int = Field(default=8, ge=1, le=15)
    monthly_budget_usd: float = Field(default=40, ge=0, le=1000)
    recheck_days: int = Field(default=14, ge=1, le=90)
    keep_closed_days: int = Field(default=60, ge=0, le=365)
    notify_emails: list[str] = Field(default_factory=list, max_length=50)
    blocked_domains: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("extra_queries")
    @classmethod
    def _queries_bounded(cls, value: list[str]) -> list[str]:
        for q in value:
            if not q or not q.strip() or len(q) > 200:
                raise ValueError("extra_queries items must be 1..200 chars")
        return [q.strip() for q in value]

    @field_validator("notify_emails")
    @classmethod
    def _emails_valid(cls, value: list[str]) -> list[str]:
        for e in value:
            if not EMAIL_RE.match((e or "").strip()):
                raise ValueError(f"invalid email: {e}")
        return [e.strip() for e in value]

    @field_validator("blocked_domains")
    @classmethod
    def _domains_bounded(cls, value: list[str]) -> list[str]:
        for d in value:
            if not d or len(d) > 200:
                raise ValueError("blocked_domains items must be 1..200 chars")
        return [d.strip().lower() for d in value]


def settings_options() -> dict:
    return {
        "models": ai_usage.MODEL_LABELS,
        "frequencies": [{"value": k, "label": v} for k, v in FREQUENCY_LABELS.items()],
        "states": options(STATES),
        "sectors": options(SECTORS),
    }


# ── slug / fingerprint ──────────────────────────────────────────────────────


def slugify(text: str | None) -> str:
    raw = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return slug or "programa"


def fingerprint_of(title: str | None, organism: str | None, year: int | None) -> str:
    """Normalized organism + title + year, accents stripped — used to match a
    re-discovered program to an existing row when the AI didn't report
    existing_slug (a different run, a slight title wording change)."""
    return f"{slugify(organism)}|{slugify(title)}|{year or ''}"


SLUG_MAX = 80


def generate_slug(
    db: Session,
    title: str,
    organism: str | None = None,
    year: int | None = None,
    exclude_id: int | None = None,
) -> str:
    # A short, readable URL (/apoyos/produccion-para-el-bienestar-2026): the
    # title, plus the year when the title doesn't carry it, cut at a word
    # boundary. The organism is left out (it made 150-character URLs); a
    # clash gets -2, -3… below.
    base = slugify(title)
    if year and str(year) not in base:
        base = f"{base}-{year}"
    if len(base) > SLUG_MAX:
        base = base[:SLUG_MAX].rsplit("-", 1)[0]
    base = base or "programa"

    def taken(candidate: str) -> bool:
        query = db.query(Apoyo.id).filter(Apoyo.slug == candidate)
        if exclude_id is not None:
            query = query.filter(Apoyo.id != exclude_id)
        return query.first() is not None

    slug = base
    n = 2
    while taken(slug):
        suffix = f"-{n}"
        slug = f"{base[: 160 - len(suffix)]}{suffix}"
        n += 1
    return slug


# ── admin input validation ──────────────────────────────────────────────────


class SourceItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    url: str = Field(min_length=1, max_length=1000)
    title: str | None = Field(default=None, max_length=300)

    @field_validator("url")
    @classmethod
    def _http(cls, value: str) -> str:
        if not re.match(r"^https?://", value, re.IGNORECASE):
            raise ValueError("source url must be http(s)")
        return value

    @field_validator("title")
    @classmethod
    def _no_markup(cls, value: str | None) -> str | None:
        if value and MARKUP_RE.search(value):
            raise ValueError("markup not allowed")
        return value


_TEXT_FIELDS = (
    "title",
    "program",
    "organism",
    "summary",
    "description",
    "beneficiaries",
    "amount_text",
    "deadline_text",
)


class ApoyoInput(BaseModel):
    """Editable columns of an Apoyo. All optional so PUT can send a partial
    patch (use model_dump(exclude_unset=True)); POST checks title itself."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    program: str | None = Field(default=None, max_length=300)
    organism: str | None = Field(default=None, max_length=200)
    level: LevelSlug | None = None
    states: list[StateSlug] | None = Field(default=None, max_length=35)
    sectors: list[SectorSlug] | None = Field(default=None, max_length=10)
    support_types: list[SupportTypeSlug] | None = Field(default=None, max_length=10)
    summary: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=20000)
    beneficiaries: str | None = Field(default=None, max_length=2000)
    amount_text: str | None = Field(default=None, max_length=500)
    amount_max_mxn: float | None = Field(default=None, ge=0, le=1_000_000_000)
    requirements: list[str] | None = Field(default=None, max_length=30)
    how_to_apply: list[str] | None = Field(default=None, max_length=30)
    opens_at: date | None = None
    closes_at: date | None = None
    deadline_text: str | None = Field(default=None, max_length=300)
    permanent: bool | None = None
    year: int | None = Field(default=None, ge=2000, le=2100)
    source_url: str | None = Field(default=None, max_length=1000)
    sources: list[SourceItem] | None = Field(default=None, max_length=10)
    category_slugs: list[StoreCategorySlug] | None = Field(default=None, max_length=12)
    featured: bool | None = None
    slug: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator(*_TEXT_FIELDS)
    @classmethod
    def _no_markup(cls, value: str | None) -> str | None:
        if value and MARKUP_RE.search(value):
            raise ValueError("markup not allowed")
        return value

    @field_validator("requirements", "how_to_apply")
    @classmethod
    def _no_markup_list(cls, value: list[str] | None) -> list[str] | None:
        if value:
            for item in value:
                if len(item) > 500:
                    raise ValueError("item too long")
                if MARKUP_RE.search(item):
                    raise ValueError("markup not allowed")
        return value

    @field_validator("source_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value and not re.match(r"^https?://", value, re.IGNORECASE):
            raise ValueError("source_url must be http(s)")
        return value

    @field_validator("slug")
    @classmethod
    def _slug_format(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().lower()
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", value):
            raise ValueError("slug must be kebab-case")
        return value


# ── serialization ────────────────────────────────────────────────────────────


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_admin(apoyo: Apoyo, today: date | None = None) -> dict:
    today = today or today_mx()
    window = window_of(apoyo.opens_at, apoyo.closes_at, apoyo.permanent, today)
    return {
        "id": apoyo.id,
        "slug": apoyo.slug,
        "status": apoyo.status,
        "title": apoyo.title,
        "program": apoyo.program,
        "organism": apoyo.organism,
        "level": apoyo.level,
        "states": apoyo.states or [],
        "sectors": apoyo.sectors or [],
        "support_types": apoyo.support_types or [],
        "summary": apoyo.summary,
        "description": apoyo.description,
        "beneficiaries": apoyo.beneficiaries,
        "amount_text": apoyo.amount_text,
        "amount_max_mxn": (
            float(apoyo.amount_max_mxn) if apoyo.amount_max_mxn is not None else None
        ),
        "requirements": apoyo.requirements or [],
        "how_to_apply": apoyo.how_to_apply or [],
        "opens_at": apoyo.opens_at.isoformat() if apoyo.opens_at else None,
        "closes_at": apoyo.closes_at.isoformat() if apoyo.closes_at else None,
        "deadline_text": apoyo.deadline_text,
        "permanent": apoyo.permanent,
        "year": apoyo.year,
        "source_url": apoyo.source_url,
        "sources": apoyo.sources or [],
        "category_slugs": apoyo.category_slugs or [],
        "confidence": apoyo.confidence,
        "ai_notes": apoyo.ai_notes,
        "fingerprint": apoyo.fingerprint,
        "featured": apoyo.featured,
        "discovered_run_id": apoyo.discovered_run_id,
        "first_seen_at": _iso(apoyo.first_seen_at),
        "last_seen_at": _iso(apoyo.last_seen_at),
        "verified_at": _iso(apoyo.verified_at),
        "verified_by": apoyo.verified_by,
        "published_at": _iso(apoyo.published_at),
        "created_at": _iso(apoyo.created_at),
        "updated_at": _iso(apoyo.updated_at),
        "updated_by": apoyo.updated_by,
        "window": window,
        "window_label": WINDOW_LABELS[window],
        "days_left": days_left(apoyo.closes_at, today),
    }


def serialize_public(apoyo: Apoyo) -> dict:
    return {
        "slug": apoyo.slug,
        "title": apoyo.title,
        "program": apoyo.program,
        "organism": apoyo.organism,
        "level": apoyo.level,
        "states": apoyo.states or [],
        "sectors": apoyo.sectors or [],
        "support_types": apoyo.support_types or [],
        "summary": apoyo.summary,
        "description": apoyo.description,
        "beneficiaries": apoyo.beneficiaries,
        "amount_text": apoyo.amount_text,
        "amount_max_mxn": (
            float(apoyo.amount_max_mxn) if apoyo.amount_max_mxn is not None else None
        ),
        "requirements": apoyo.requirements or [],
        "how_to_apply": apoyo.how_to_apply or [],
        "opens_at": apoyo.opens_at.isoformat() if apoyo.opens_at else None,
        "closes_at": apoyo.closes_at.isoformat() if apoyo.closes_at else None,
        "deadline_text": apoyo.deadline_text,
        "permanent": apoyo.permanent,
        "year": apoyo.year,
        "source_url": apoyo.source_url,
        "sources": apoyo.sources or [],
        "category_slugs": apoyo.category_slugs or [],
        "featured": apoyo.featured,
        "verified_at": _iso(apoyo.verified_at),
        "published_at": _iso(apoyo.published_at),
        "updated_at": _iso(apoyo.updated_at),
    }


def serialize_run(run: ApoyoRun) -> dict:
    topics = run.topics or []
    done = sum(1 for t in topics if t.get("status") in ("done", "error", "skipped"))
    return {
        "id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "topics": topics,
        "found_count": run.found_count,
        "new_count": run.new_count,
        "updated_count": run.updated_count,
        "closed_count": run.closed_count,
        "cost_usd": float(run.cost_usd or 0),
        "web_searches": run.web_searches,
        "error": run.error,
        "triggered_by": run.triggered_by,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "progress": {"done": done, "total": len(topics)},
    }


# ── queries ──────────────────────────────────────────────────────────────────

STATUS_KEYS = ("draft", "published", "rejected", "archived")


def _status_counts(db: Session) -> dict:
    rows = db.query(Apoyo.status, func.count(Apoyo.id)).group_by(Apoyo.status).all()
    counts = {k: 0 for k in STATUS_KEYS}
    for status, n in rows:
        if status in counts:
            counts[status] = n
    return counts


def list_apoyos(
    db: Session,
    *,
    status: str = "all",
    q: str | None = None,
    state: str | None = None,
    sector: str | None = None,
    window: str | None = None,
    limit: int = 50,
    offset: int = 0,
    today: date | None = None,
) -> dict:
    today = today or today_mx()
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    query = db.query(Apoyo)
    if status != "all":
        query = query.filter(Apoyo.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Apoyo.title.ilike(like),
                Apoyo.organism.ilike(like),
                Apoyo.program.ilike(like),
            )
        )
    rows = query.all()
    if state:
        rows = [r for r in rows if state in (r.states or [])]
    if sector:
        rows = [r for r in rows if sector in (r.sectors or [])]
    if window:
        rows = [
            r
            for r in rows
            if window_of(r.opens_at, r.closes_at, r.permanent, today) == window
        ]

    def sort_key(r: Apoyo):
        draft_first = 0 if r.status == "draft" else 1
        closes = r.closes_at or date.max
        updated = r.updated_at or datetime.min.replace(tzinfo=timezone.utc)
        updated_cmp = (
            updated if updated.tzinfo else updated.replace(tzinfo=timezone.utc)
        )
        return (draft_first, closes, -updated_cmp.timestamp())

    rows.sort(key=sort_key)
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "items": [serialize_admin(r, today) for r in page],
        "total": total,
        "counts": _status_counts(db),
    }


def get_stats(db: Session, settings: ApoyosSettings, today: date | None = None) -> dict:
    today = today or today_mx()
    counts = _status_counts(db)
    published = db.query(Apoyo).filter(Apoyo.status == "published").all()
    published_by_window = {k: 0 for k in WINDOW_LABELS}
    by_state: dict[str, int] = {}
    by_sector: dict[str, int] = {}
    closing_soon: list[Apoyo] = []
    for a in published:
        w = window_of(a.opens_at, a.closes_at, a.permanent, today)
        published_by_window[w] += 1
        for s in a.states or []:
            by_state[s] = by_state.get(s, 0) + 1
        for s in a.sectors or []:
            by_sector[s] = by_sector.get(s, 0) + 1
        if w == "abierta":
            dleft = days_left(a.closes_at, today)
            if dleft is not None and dleft <= 30:
                closing_soon.append(a)
    closing_soon.sort(key=lambda a: a.closes_at or date.max)

    month_spend = float(ai_usage.spent_usd(db, "apoyos", ai_usage.month_start()))
    last_run = db.query(ApoyoRun).order_by(ApoyoRun.created_at.desc()).first()
    return {
        "counts": counts,
        "published_by_window": published_by_window,
        "by_state": [
            {"key": k, "label": STATES.get(k, k), "count": v}
            for k, v in sorted(by_state.items(), key=lambda x: -x[1])
        ],
        "by_sector": [
            {"key": k, "label": SECTORS.get(k, k), "count": v}
            for k, v in sorted(by_sector.items(), key=lambda x: -x[1])
        ],
        "closing_soon": [serialize_admin(a, today) for a in closing_soon[:20]],
        "month_spend_usd": month_spend,
        "monthly_budget_usd": settings.monthly_budget_usd,
        "last_run": serialize_run(last_run) if last_run else None,
        "next_run_due_at": next_run_due_at(db, settings),
        "frequency": settings.frequency,
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def last_finished_schedule_run(db: Session) -> ApoyoRun | None:
    return (
        db.query(ApoyoRun)
        .filter(ApoyoRun.trigger == "schedule")
        .filter(ApoyoRun.status.in_(("done", "failed", "cancelled")))
        .filter(ApoyoRun.finished_at.isnot(None))
        .order_by(ApoyoRun.finished_at.desc())
        .first()
    )


def next_run_due_at(db: Session, settings: ApoyosSettings) -> str | None:
    if settings.frequency == "paused":
        return None
    hours = FREQUENCY_HOURS[settings.frequency]
    last = last_finished_schedule_run(db)
    if last is None or last.finished_at is None:
        return None
    return (_aware(last.finished_at) + timedelta(hours=hours)).isoformat()


# ── storefront feed ──────────────────────────────────────────────────────────

_WINDOW_ORDER = {
    "abierta": 0,
    "proxima": 1,
    "permanente": 2,
    "sin-fecha": 3,
    "cerrada": 4,
}


def feed(
    db: Session, settings: ApoyosSettings, today: date | None = None
) -> list[dict]:
    today = today or today_mx()
    rows = db.query(Apoyo).filter(Apoyo.status == "published").all()
    cutoff = today - timedelta(days=settings.keep_closed_days)
    visible = [r for r in rows if r.closes_at is None or r.closes_at >= cutoff]

    def sort_key(r: Apoyo):
        window = window_of(r.opens_at, r.closes_at, r.permanent, today)
        return (
            0 if r.featured else 1,
            _WINDOW_ORDER.get(window, 9),
            r.closes_at or date.max,
            r.title or "",
        )

    visible.sort(key=sort_key)
    return [serialize_public(r) for r in visible]


# ── CRUD / status ────────────────────────────────────────────────────────────


class ApoyoNotFound(Exception):
    pass


class DeleteNotAllowed(Exception):
    pass


def get_apoyo(db: Session, apoyo_id: int) -> Apoyo:
    apoyo = db.get(Apoyo, apoyo_id)
    if apoyo is None:
        raise ApoyoNotFound()
    return apoyo


def create_apoyo(db: Session, data: ApoyoInput, user_email: str | None) -> Apoyo:
    payload = data.model_dump(exclude_unset=True, exclude={"slug"})
    title = payload.pop("title", None) or ""
    slug = data.slug or generate_slug(
        db, title, payload.get("organism"), payload.get("year")
    )
    sources = payload.pop("sources", None)
    apoyo = Apoyo(
        slug=slug,
        status="draft",
        title=title,
        fingerprint=fingerprint_of(title, payload.get("organism"), payload.get("year")),
        updated_by=(user_email or None),
        **payload,
    )
    if sources is not None:
        apoyo.sources = [
            s.model_dump() if hasattr(s, "model_dump") else s for s in sources
        ]
    db.add(apoyo)
    db.commit()
    return apoyo


def update_apoyo(
    db: Session, apoyo_id: int, data: ApoyoInput, user_email: str | None
) -> Apoyo:
    apoyo = get_apoyo(db, apoyo_id)
    payload = data.model_dump(exclude_unset=True)
    sources = payload.pop("sources", None)
    if payload.get("slug"):
        new_slug = payload.pop("slug")
        if new_slug != apoyo.slug:
            taken = (
                db.query(Apoyo.id)
                .filter(Apoyo.slug == new_slug, Apoyo.id != apoyo_id)
                .first()
            )
            if taken:
                raise ValueError("slug already in use")
            apoyo.slug = new_slug
    else:
        payload.pop("slug", None)
    for key, value in payload.items():
        setattr(apoyo, key, value)
    if sources is not None:
        apoyo.sources = [
            s.model_dump() if hasattr(s, "model_dump") else s for s in sources
        ]
    if payload.get("title") or payload.get("organism") or payload.get("year"):
        apoyo.fingerprint = fingerprint_of(
            payload.get("title", apoyo.title),
            payload.get("organism", apoyo.organism),
            payload.get("year", apoyo.year),
        )
    apoyo.updated_by = user_email or apoyo.updated_by
    db.commit()
    return apoyo


VALID_STATUSES = ("draft", "published", "rejected", "archived")


def set_status(
    db: Session, apoyo_id: int, status: str, user_email: str | None
) -> Apoyo:
    if status not in VALID_STATUSES:
        raise ValueError("invalid status")
    apoyo = get_apoyo(db, apoyo_id)
    apoyo.status = status
    now = datetime.now(timezone.utc)
    if status == "published":
        if apoyo.published_at is None:
            apoyo.published_at = now
        apoyo.verified_at = now
        apoyo.verified_by = user_email
    apoyo.updated_by = user_email or apoyo.updated_by
    db.commit()
    return apoyo


def delete_apoyo(db: Session, apoyo_id: int) -> None:
    apoyo = get_apoyo(db, apoyo_id)
    if apoyo.status not in ("draft", "rejected"):
        raise DeleteNotAllowed()
    db.delete(apoyo)
    db.commit()
