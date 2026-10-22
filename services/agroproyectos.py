"""
Agroproyectos — the AI project designer at todoparaelcampo.com.mx/agroproyectos.

A visitor describes an idea, their situation and a budget (or "no sé"); Claude
(services/agroproyectos_ai.py) returns a starter plan, picks catalog products
and, when relevant, designs an irrigation system. The visitor can request a
formal quote (an ordinary self-serve web quote, services/web_quotes.py), ask
follow-up questions, and ask to be contacted. Unauthenticated buyer input: it
never creates or fills Customer rows until a quote is actually paid (that path
is services/web_orders.py, unchanged).

Status flow: pending → plan_ready → products_ready → done (or done directly
when the plan says no irrigation is needed) → riego → done. A failed AI step
sets status="error" and keeps whatever was already generated; retrying the
same step (calling the same endpoint again) is allowed and, once the project
already has that step's result, is idempotent.

Every AI step re-checks services.agroproyectos.AgroSettings.enabled and the
daily/monthly budget before spending anything — see routes/agroproyectos.py
for how AIUnavailable and the limit exceptions map to HTTP responses.

App Runner closes a request at 120 s, and a single Claude call can run well
past that (observed live: 140-200 s on this same ai_usage.create_message door,
see the project's #background-contract note). So plan/productos/riego never
run inside the HTTP request: run_plan/run_productos/run_riego check
enabled/budget/preconditions synchronously, mark the project "generating" in
its events JSON (`_generating` / `_generating_since`, hidden from the
serialized `events`) and hand the actual Claude call to
services.background.submit(), returning "accepted" at once; the caller
answers 202 and the storefront polls GET. A result that already exists makes
the step a no-op returning "done" (200). A "generating" mark older than
GENERATING_STALE_MINUTES is read as a dead job (a deploy or a crash took the
worker down mid-step): it serializes as status "error" / error "timeout" and
the step can be retried, which clears the mark for real. Chat stays
synchronous — it's a short, bounded call (timeout 45 s).
"""

import logging
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape as _h
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import BackgroundTasks
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models import (
    AgroProject,
    AgroProjectMessage,
    AIUsage,
    Apoyo,
    Quote,
    Task,
    get_next_task_number,
)
from services import (
    agroproyectos_ai,
    ai_usage,
    app_settings,
    background,
    campo_taxonomy,
    mailer,
    web_quote_email,
)
from services.ai_usage import AIUnavailable, Effort, ModelChoice
from services.campo_taxonomy import window_of
from services.quote_followup import _resolve_system_user_id
from services.web_orders import (
    EMAIL_RE,
    MARKUP_RE,
    _assignee_email,
    _category_id,
    _notify_emails,
    _task_exists,
    _task_user_id,
    _valid_phone,
)
from services.web_quotes import (
    REF_ALPHABET,
    QuoteLimitReached,
    QuoteRequestRejected,
    create_quote_request,
)

logger = logging.getLogger(__name__)

SETTINGS_KEY = "agroproyectos"
REVIEW_CATEGORY = "Seguimiento a cotizaciones"
DEFAULT_ADMIN_URL = "https://impag-admin-app.vercel.app"
NOTIFY_FROM = "Todo Para El Campo <cotizaciones@todoparaelcampo.com.mx>"
LEAD_STATUSES = [
    "nuevo",
    "contactado",
    "en_seguimiento",
    "cotizado",
    "ganado",
    "perdido",
    "descartado",
]


# ── settings ─────────────────────────────────────────────────────────────────


class AgroSettings(BaseModel):
    enabled: bool = True
    model: ModelChoice = "claude-opus-5"
    effort: Effort = "medium"
    chat_model: ModelChoice = "claude-opus-5"
    chat_effort: Effort = "low"
    contact_required: bool = True
    max_per_hour: int = Field(default=30, ge=1, le=1000)
    max_per_phone_per_day: int = Field(default=5, ge=1, le=50)
    max_questions: int = Field(default=10, ge=0, le=50)
    daily_budget_usd: float = Field(default=25, ge=0, le=1000)
    monthly_budget_usd: float = Field(default=300, ge=0, le=10000)
    web_search: bool = False
    max_searches: int = Field(default=3, ge=0, le=10)
    quote_mode: Literal["review", "instant"] = "review"
    include_knowledge: bool = True
    extra_instructions: str = Field(default="", max_length=4000)
    excluded_handles: list[str] = Field(default_factory=list)
    notify_new_projects: bool = True
    notify_emails: list[str] = Field(default_factory=list)
    assignee_email: str | None = None

    @field_validator("notify_emails")
    @classmethod
    def _valid_notify_emails(cls, value: list[str]) -> list[str]:
        for email in value:
            if not EMAIL_RE.match(email):
                raise ValueError(f"invalid email: {email}")
        return value

    @field_validator("assignee_email")
    @classmethod
    def _valid_assignee_email(cls, value: str | None) -> str | None:
        if value and not EMAIL_RE.match(value):
            raise ValueError("invalid email")
        return value or None

    @field_validator("excluded_handles")
    @classmethod
    def _clean_handles(cls, value: list[str]) -> list[str]:
        return [h.strip() for h in value if h and h.strip()][:300]


def read_settings(db: Session) -> AgroSettings:
    return app_settings.read(db, SETTINGS_KEY, AgroSettings)


def write_settings(db: Session, patch: dict, user: str | None) -> AgroSettings:
    return app_settings.write(db, SETTINGS_KEY, AgroSettings, patch, user)


# ── errors ───────────────────────────────────────────────────────────────────


class AgroDisabled(Exception):
    pass


class AgroContactRequired(Exception):
    pass


class AgroValidationRejected(Exception):
    def __init__(self, problems: list[str]):
        super().__init__(", ".join(problems))
        self.problems = problems


class AgroLimitReached(Exception):
    pass


class AgroBudgetExceeded(Exception):
    pass


class AgroNotReady(Exception):
    pass


class QuestionsExhausted(Exception):
    pass


# ── creation ─────────────────────────────────────────────────────────────────


def _typed_text_fields(body: Any) -> list[tuple[str, str | None]]:
    fields = [
        ("idea", body.idea),
        ("current_operation", body.current_operation),
        ("municipio", body.municipio),
    ]
    if body.contact is not None:
        fields += [
            ("contact.name", body.contact.name),
            ("contact.email", body.contact.email),
        ]
    return fields


def creation_problems(body: Any) -> list[str]:
    """Problems that refuse POST /storefront/agro-projects (buyer input)."""
    problems: list[str] = []
    if body.budget_mxn is None and not body.budget_unknown:
        problems.append("missing_budget")
    if body.budget_mxn is not None and body.budget_unknown:
        problems.append("conflicting_budget")
    if body.contact is not None:
        if _valid_phone(body.contact.phone) is None:
            problems.append("invalid_phone")
        if body.contact.email and not EMAIL_RE.match(body.contact.email):
            problems.append("invalid_email")
    problems += [
        f"markup_in:{name}"
        for name, value in _typed_text_fields(body)
        if value and MARKUP_RE.search(value)
    ]
    return problems


def _new_ref(now: datetime) -> str:
    day = now.strftime("%y%m%d")
    suffix = "".join(secrets.choice(REF_ALPHABET) for _ in range(4))
    return f"AGR-{day}-{suffix}"


def _check_create_limits(
    db: Session, phone: str | None, settings: AgroSettings, now: datetime
) -> None:
    since_hour = now - timedelta(hours=1)
    count = (
        db.query(func.count(AgroProject.id))
        .filter(AgroProject.created_at >= since_hour)
        .scalar()
    )
    if settings.max_per_hour and count >= settings.max_per_hour:
        raise AgroLimitReached()
    if phone:
        since_day = now - timedelta(hours=24)
        per_phone = (
            db.query(func.count(AgroProject.id))
            .filter(
                AgroProject.contact_phone == phone, AgroProject.created_at >= since_day
            )
            .scalar()
        )
        if (
            settings.max_per_phone_per_day
            and per_phone >= settings.max_per_phone_per_day
        ):
            raise AgroLimitReached()


def _assert_budget(
    db: Session, settings: AgroSettings, now: datetime | None = None
) -> None:
    now = now or datetime.now(timezone.utc)
    if settings.daily_budget_usd:
        spent = ai_usage.spent_usd(db, "agroproyectos", ai_usage.day_start(now))
        if spent >= Decimal(str(settings.daily_budget_usd)):
            raise AgroBudgetExceeded()
    if settings.monthly_budget_usd:
        spent = ai_usage.spent_usd(db, "agroproyectos", ai_usage.month_start(now))
        if spent >= Decimal(str(settings.monthly_budget_usd)):
            raise AgroBudgetExceeded()


def create_project(
    db: Session, body: Any, settings: AgroSettings, now: datetime | None = None
) -> dict:
    now = now or datetime.now(timezone.utc)
    if not settings.enabled:
        raise AgroDisabled()
    problems = creation_problems(body)
    if problems:
        raise AgroValidationRejected(problems)
    if settings.contact_required and body.contact is None:
        raise AgroContactRequired()
    phone = _valid_phone(body.contact.phone) if body.contact is not None else None
    _check_create_limits(db, phone, settings, now)
    _assert_budget(db, settings, now)

    token = str(uuid.uuid4())
    inputs = body.model_dump(mode="json")
    project: AgroProject | None = None
    for _attempt in range(5):
        ref = _new_ref(now)
        candidate = AgroProject(
            token=token,
            ref=ref,
            status="pending",
            lead_status="nuevo",
            producer_type=body.producer_type,
            idea=body.idea.strip(),
            current_operation=(body.current_operation or None),
            inputs=inputs,
            contact_name=(
                body.contact.name.strip() if body.contact is not None else None
            ),
            contact_phone=phone,
            contact_email=(body.contact.email if body.contact is not None else None),
            state=body.state,
            municipio=body.municipio,
            budget_mxn=body.budget_mxn,
            budget_unknown=body.budget_unknown,
            wants_financing=body.wants_financing,
            utm=(body.utm.model_dump(mode="json") if body.utm is not None else None),
            events={},
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            project = candidate
            break
        except IntegrityError:
            continue
    if project is None:
        raise RuntimeError("could not allocate an agroproyecto reference")
    db.commit()
    return {"token": project.token, "ref": project.ref, "status": project.status}


def get_by_token(db: Session, token: str) -> AgroProject | None:
    return db.query(AgroProject).filter(AgroProject.token == token).first()


# ── apoyos matching ──────────────────────────────────────────────────────────


def match_apoyos(db: Session, project: AgroProject) -> list[dict]:
    """ "Apoyos que podrían aplicar": published, open, matching state and
    sector. Queried directly against models.Apoyo (the Apoyos feature is
    owned by another agent in this worktree)."""
    from datetime import date as _date

    plan = project.plan or {}
    sectors = {s for s in (plan.get("apoyo_sectors") or []) if s}
    if project.sector:
        sectors.add(project.sector)
    if not sectors:
        return []
    today = campo_taxonomy.today_mx()
    rows = db.query(Apoyo).filter(Apoyo.status == "published").all()
    order_rank = {"abierta": 0, "proxima": 1, "permanente": 2, "sin-fecha": 3}
    matched: list[tuple[Apoyo, str]] = []
    for apoyo in rows:
        apoyo_sectors = set(apoyo.sectors or [])
        if not (apoyo_sectors & sectors):
            continue
        states = set(apoyo.states or [])
        if project.state and project.state not in states and "nacional" not in states:
            continue
        window = window_of(
            apoyo.opens_at, apoyo.closes_at, bool(apoyo.permanent), today
        )
        if window == "cerrada":
            continue
        matched.append((apoyo, window))
    matched.sort(
        key=lambda pair: (order_rank.get(pair[1], 9), pair[0].closes_at or _date.max)
    )
    return [
        {
            "slug": apoyo.slug,
            "title": apoyo.title,
            "organism": apoyo.organism,
            "window": window,
            "closes_at": apoyo.closes_at.isoformat() if apoyo.closes_at else None,
            "amount_text": apoyo.amount_text,
        }
        for apoyo, window in matched[:5]
    ]


# ── serialization ────────────────────────────────────────────────────────────


def _quote_summary(db: Session, quote_id: int | None) -> dict | None:
    if not quote_id:
        return None
    q = db.get(Quote, quote_id)
    if q is None:
        return None
    return {
        "id": q.id,
        "quote_number": q.quote_number,
        "status": q.status,
        "total": float(q.total or 0),
    }


def _messages(db: Session, project_id: int) -> list[dict]:
    rows = (
        db.query(AgroProjectMessage)
        .filter(AgroProjectMessage.project_id == project_id)
        .order_by(AgroProjectMessage.created_at.asc(), AgroProjectMessage.id.asc())
        .all()
    )
    return [
        {
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]


def agro_public(db: Session, project: AgroProject, settings: AgroSettings) -> dict:
    inputs = dict(project.inputs or {})
    inputs.pop("contact", None)
    inputs.pop("utm", None)
    quote = None
    if project.quote_id:
        summary = _quote_summary(db, project.quote_id)
        if summary:
            q = db.get(Quote, project.quote_id)
            quote = {
                "url": f"/cotizacion/{q.access_token}",
                "quote_number": summary["quote_number"],
            }
    first_name = (project.contact_name or "").split(" ")[0] or None
    generating, generating_since, status, error = _effective_view(project)
    return {
        "token": project.token,
        "ref": project.ref,
        "status": status,
        "error": error,
        "generating": generating,
        "generating_since": generating_since,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "inputs": inputs,
        "first_name": first_name,
        "plan": project.plan,
        "products": project.products,
        "irrigation": project.irrigation,
        "apoyos": match_apoyos(db, project),
        "quote": quote,
        "questions_remaining": max(
            0, settings.max_questions - (project.questions_count or 0)
        ),
        "messages": _messages(db, project.id),
        "contact_requested": project.contact_requested_at is not None,
        "settings": {
            "quote_mode": settings.quote_mode,
            "contact_required": settings.contact_required,
            "max_questions": settings.max_questions,
        },
    }


def agro_list_item(db: Session, project: AgroProject) -> dict:
    plan = project.plan or {}
    generating, generating_since, status, _ = _effective_view(project)
    return {
        "id": project.id,
        "ref": project.ref,
        "token": project.token,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "status": status,
        "generating": generating,
        "generating_since": generating_since,
        "lead_status": project.lead_status,
        "producer_type": project.producer_type,
        "title": project.title,
        "idea": (project.idea or "")[:200],
        "project_kind": project.project_kind,
        "sector": project.sector,
        "state": project.state,
        "municipio": project.municipio,
        "contact_name": project.contact_name,
        "contact_phone": project.contact_phone,
        "contact_email": project.contact_email,
        "budget_mxn": (
            float(project.budget_mxn) if project.budget_mxn is not None else None
        ),
        "budget_unknown": project.budget_unknown,
        "investment_min_mxn": (
            float(project.investment_min_mxn)
            if project.investment_min_mxn is not None
            else None
        ),
        "investment_max_mxn": (
            float(project.investment_max_mxn)
            if project.investment_max_mxn is not None
            else None
        ),
        "verdict": (plan.get("viability") or {}).get("verdict"),
        "quote": _quote_summary(db, project.quote_id),
        "contact_requested_at": (
            project.contact_requested_at.isoformat()
            if project.contact_requested_at
            else None
        ),
        "questions_count": project.questions_count,
        "cost_usd": float(project.cost_usd or 0),
    }


def _usage_rows(db: Session, project_id: int) -> list[dict]:
    rows = (
        db.query(AIUsage)
        .filter(AIUsage.ref_type == "agro_project", AIUsage.ref_id == project_id)
        .order_by(AIUsage.created_at.asc(), AIUsage.id.asc())
        .all()
    )
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
        }
        for r in rows
    ]


def agro_admin(db: Session, project: AgroProject) -> dict:
    base = agro_list_item(db, project)  # already carries generating/generating_since
    wa_digits = re.sub(r"\D", "", project.contact_phone or "")
    _, _, _status, error = _effective_view(project)
    base.update(
        {
            "inputs": project.inputs,
            "plan": project.plan,
            "products": project.products,
            "irrigation": project.irrigation,
            "messages": _messages(db, project.id),
            "events": _public_events(project.events),
            "contact_request": project.contact_request,
            "utm": project.utm,
            "staff_notes": project.staff_notes,
            "assigned_to": project.assigned_to,
            "error": error,
            "model": project.model,
            "generation_ms": project.generation_ms,
            "public_url": f"https://www.todoparaelcampo.com.mx/agroproyectos?p={project.token}",
            "whatsapp_url": f"https://wa.me/{wa_digits}" if wa_digits else None,
            "usage": _usage_rows(db, project.id),
            "apoyos": match_apoyos(db, project),
        }
    )
    return base


# ── the AI steps ─────────────────────────────────────────────────────────────

GENERATING_STALE_MINUTES = 6
STEP_DONE = "done"
STEP_ACCEPTED = "accepted"


def _events_dict(project: AgroProject) -> dict:
    return dict(project.events or {})


def _generating_state(project: AgroProject) -> tuple[str | None, datetime | None]:
    events = _events_dict(project)
    step = events.get("_generating")
    raw = events.get("_generating_since")
    since = None
    if isinstance(raw, str):
        try:
            since = datetime.fromisoformat(raw)
        except ValueError:
            since = None
        else:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
    return (step if isinstance(step, str) else None), since


def _is_stale(since: datetime | None, now: datetime | None = None) -> bool:
    if since is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - since) > timedelta(minutes=GENERATING_STALE_MINUTES)


def _set_generating(db: Session, project: AgroProject, step: str) -> None:
    events = _events_dict(project)
    events["_generating"] = step
    events["_generating_since"] = datetime.now(timezone.utc).isoformat()
    project.events = events
    flag_modified(project, "events")
    db.commit()


def _clear_generating_inplace(project: AgroProject) -> None:
    events = _events_dict(project)
    events.pop("_generating", None)
    events.pop("_generating_since", None)
    project.events = events
    flag_modified(project, "events")


def _public_events(events: dict | None) -> dict:
    return {k: v for k, v in (events or {}).items() if not str(k).startswith("_")}


def _effective_view(
    project: AgroProject,
) -> tuple[str | None, str | None, str, str | None]:
    """(generating, generating_since iso, status, error) — a "generating"
    mark older than GENERATING_STALE_MINUTES means the worker that owned it
    is gone (a deploy, a crash): it reads as resolved (status "error" / error
    "timeout", generating cleared) even before a retry POST writes that to
    the row for real."""
    step, since = _generating_state(project)
    if step is not None and _is_stale(since):
        return None, None, "error", "timeout"
    since_iso = since.isoformat() if (step and since) else None
    return step, since_iso, project.status, project.error


def _fail_step(db: Session, project: AgroProject, reason: str) -> None:
    _clear_generating_inplace(project)
    project.status = "error"
    project.error = (reason or "ai_error")[:120]
    db.commit()


def _check_step_start(
    db: Session, project: AgroProject, settings: AgroSettings, step: str
) -> str | None:
    """None when the caller should submit a fresh job (nothing running, or a
    stale mark was just resolved to status="error"); STEP_ACCEPTED when a
    live job is already running and the caller should just answer 202."""
    current, since = _generating_state(project)
    if current == step:
        if not _is_stale(since):
            return STEP_ACCEPTED
        _fail_step(db, project, "timeout")
    return None


def run_plan(db: Session, project: AgroProject, settings: AgroSettings) -> str:
    """Starts (or resumes watching) the plan step. Returns STEP_DONE when
    project.plan already exists, STEP_ACCEPTED once a job is running (newly
    submitted or already in flight) — the route maps these to 200 / 202."""
    if project.plan is not None:
        return STEP_DONE
    resumed = _check_step_start(db, project, settings, "plan")
    if resumed:
        return resumed
    if not settings.enabled:
        raise AgroDisabled()
    _assert_budget(db, settings)
    _set_generating(db, project, "plan")
    project_id = project.id

    def job(db_job: Session) -> None:
        job_project = db_job.get(AgroProject, project_id)
        if job_project is not None:
            _execute_plan(db_job, job_project, settings)

    background.submit(f"agro-plan-{project.ref}", job)
    return STEP_ACCEPTED


def _execute_plan(db: Session, project: AgroProject, settings: AgroSettings) -> None:
    started = time.monotonic()
    try:
        response = agroproyectos_ai.generate_plan(project, settings)
    except AIUnavailable as exc:
        _fail_step(db, project, exc.reason)
        return
    data = ai_usage.json_output(response)
    cost = ai_usage.response_cost(response, settings.model)
    duration_ms = int((time.monotonic() - started) * 1000)

    project.plan = data
    project.error = None
    project.status = "plan_ready"
    project.project_kind = data.get("project_kind")
    project.sector = data.get("sector")
    project.title = data.get("title")
    budget = data.get("budget") or {}
    project.investment_min_mxn = budget.get("investment_min_mxn")
    project.investment_max_mxn = budget.get("investment_max_mxn")
    project.model = settings.model
    project.cost_usd = (project.cost_usd or 0) + cost
    project.generation_ms = (project.generation_ms or 0) + duration_ms
    _clear_generating_inplace(project)
    db.commit()
    if settings.notify_new_projects:
        _send_plan_ready_email(project, settings)


def run_productos(
    db: Session, project: AgroProject, settings: AgroSettings, candidates: list[Any]
) -> str:
    if project.products is not None:
        return STEP_DONE
    if project.plan is None:
        raise AgroNotReady()
    resumed = _check_step_start(db, project, settings, "productos")
    if resumed:
        return resumed
    if not settings.enabled:
        raise AgroDisabled()
    _assert_budget(db, settings)
    _set_generating(db, project, "productos")
    project_id = project.id

    def job(db_job: Session) -> None:
        job_project = db_job.get(AgroProject, project_id)
        if job_project is not None:
            _execute_productos(db_job, job_project, settings, candidates)

    background.submit(f"agro-productos-{project.ref}", job)
    return STEP_ACCEPTED


def _execute_productos(
    db: Session, project: AgroProject, settings: AgroSettings, candidates: list[Any]
) -> None:
    started = time.monotonic()
    try:
        response = agroproyectos_ai.generate_productos(project, settings, candidates)
    except AIUnavailable as exc:
        _fail_step(db, project, exc.reason)
        return
    data = ai_usage.json_output(response)
    cost = ai_usage.response_cost(response, settings.model)
    duration_ms = int((time.monotonic() - started) * 1000)

    excluded = set(settings.excluded_handles)
    items, catalog_total = agroproyectos_ai.enrich_product_items(
        data.get("items") or [], candidates, excluded
    )
    project.products = {
        "items": items,
        "not_in_catalog": data.get("not_in_catalog") or [],
        "catalog_total_mxn": catalog_total,
        "notes": data.get("notes") or "",
    }
    project.error = None
    needs_irrigation = bool((project.plan or {}).get("needs_irrigation"))
    project.status = "products_ready" if needs_irrigation else "done"
    project.cost_usd = (project.cost_usd or 0) + cost
    project.generation_ms = (project.generation_ms or 0) + duration_ms
    _clear_generating_inplace(project)
    db.commit()


def run_riego(
    db: Session, project: AgroProject, settings: AgroSettings, candidates: list[Any]
) -> str:
    if project.irrigation is not None:
        return STEP_DONE
    plan = project.plan or {}
    if project.plan is None or not plan.get("needs_irrigation"):
        raise AgroNotReady()
    resumed = _check_step_start(db, project, settings, "riego")
    if resumed:
        return resumed
    if not settings.enabled:
        raise AgroDisabled()
    _assert_budget(db, settings)
    _set_generating(db, project, "riego")
    project_id = project.id

    def job(db_job: Session) -> None:
        job_project = db_job.get(AgroProject, project_id)
        if job_project is not None:
            _execute_riego(db_job, job_project, settings, candidates)

    background.submit(f"agro-riego-{project.ref}", job)
    return STEP_ACCEPTED


def _execute_riego(
    db: Session, project: AgroProject, settings: AgroSettings, candidates: list[Any]
) -> None:
    started = time.monotonic()
    try:
        response = agroproyectos_ai.generate_riego(project, settings, candidates)
    except AIUnavailable as exc:
        _fail_step(db, project, exc.reason)
        return
    data = ai_usage.json_output(response)
    cost = ai_usage.response_cost(response, settings.model)
    duration_ms = int((time.monotonic() - started) * 1000)

    data = agroproyectos_ai.sanity_check_irrigation(data)
    excluded = set(settings.excluded_handles)
    items, catalog_total = agroproyectos_ai.enrich_product_items(
        data.get("items") or [], candidates, excluded
    )
    irrigation = dict(data)
    irrigation["items"] = items
    irrigation["catalog_total_mxn"] = catalog_total
    irrigation["not_in_catalog"] = data.get("not_in_catalog") or []
    project.irrigation = irrigation
    project.error = None
    project.status = "done"
    project.cost_usd = (project.cost_usd or 0) + cost
    project.generation_ms = (project.generation_ms or 0) + duration_ms
    _clear_generating_inplace(project)
    db.commit()


def ask_question(
    db: Session, project: AgroProject, settings: AgroSettings, question: str
) -> dict:
    if (project.questions_count or 0) >= settings.max_questions:
        raise QuestionsExhausted()
    history_rows = (
        db.query(AgroProjectMessage)
        .filter(AgroProjectMessage.project_id == project.id)
        .order_by(AgroProjectMessage.created_at.desc(), AgroProjectMessage.id.desc())
        .limit(6)
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(history_rows)]
    response = agroproyectos_ai.ask(
        project, settings, question, history
    )  # AIUnavailable propagates
    answer = next(
        (b.text for b in response.content if getattr(b, "type", None) == "text"), ""
    ).strip()
    cost = ai_usage.response_cost(response, settings.chat_model)

    db.add(
        AgroProjectMessage(project_id=project.id, role="user", content=question[:600])
    )
    db.add(
        AgroProjectMessage(
            project_id=project.id, role="assistant", content=answer, cost_usd=cost
        )
    )
    project.questions_count = (project.questions_count or 0) + 1
    project.cost_usd = (project.cost_usd or 0) + cost
    db.commit()
    return {
        "answer": answer,
        "remaining": max(0, settings.max_questions - project.questions_count),
        "messages": _messages(db, project.id),
    }


# ── quote ────────────────────────────────────────────────────────────────────


def create_quote(
    db: Session,
    project: AgroProject,
    settings: AgroSettings,
    items: list[Any],
    contact: Any | None,
    background_tasks: BackgroundTasks,
) -> dict:
    if contact is not None:
        phone = _valid_phone(contact.phone)
        if phone is None:
            raise AgroValidationRejected(["invalid_phone"])
        if contact.email and not EMAIL_RE.match(contact.email):
            raise AgroValidationRejected(["invalid_email"])
        project.contact_name = contact.name.strip()
        project.contact_phone = phone
        project.contact_email = contact.email
        db.commit()
    if not project.contact_phone:
        raise AgroContactRequired()

    plan = project.plan or {}
    notes_lines = [plan.get("title") or project.title or "", plan.get("summary") or ""]
    key_numbers = plan.get("key_numbers") or []
    if key_numbers:
        notes_lines.append(
            "; ".join(
                f"{k.get('label')}: {k.get('value')}"
                for k in key_numbers
                if k.get("label")
            )
        )
    notes_lines.append(
        "Proyecto diseñado con IA en todoparaelcampo.com.mx/agroproyectos"
    )
    notes_text = "\n".join(line for line in notes_lines if line)

    req = SimpleNamespace(
        customer=SimpleNamespace(
            name=project.contact_name,
            phone=project.contact_phone,
            email=project.contact_email,
            location=project.municipio,
        ),
        delivery=SimpleNamespace(method="recoger", address=None),
        invoice=None,
        items=[
            SimpleNamespace(
                sku=None,
                handle=item.handle,
                product_id=item.product_id,
                description=item.description,
                unit_label=item.unit_label,
                quantity=item.quantity,
                unit_price=item.unit_price,
                iva_rate=item.iva_rate,
            )
            for item in items
        ],
        notes=None,
    )
    extra_reasons = ["agroproyecto"] if settings.quote_mode == "review" else []
    try:
        result = create_quote_request(
            db,
            req,
            extra_reasons=extra_reasons,
            block_extra={
                "agroproyecto": {
                    "ref": project.ref,
                    "token": project.token,
                    "id": project.id,
                }
            },
            notes_text=notes_text,
        )
    except QuoteLimitReached:
        raise AgroLimitReached()
    except QuoteRequestRejected as exc:
        raise AgroValidationRejected(exc.problems)

    quote_row = (
        db.query(Quote).filter(Quote.quote_number == result["quote_number"]).one()
    )
    project.quote_id = quote_row.id
    if project.lead_status in ("nuevo", "contactado"):
        project.lead_status = "cotizado"
    db.commit()
    web_quote_email.queue_created(db, result["quote_number"], background_tasks)
    return {
        "url": result["url"],
        "quote_number": result["quote_number"],
        "needs_review": result["needs_review"],
        "total": result["total"],
    }


# ── contact request ──────────────────────────────────────────────────────────


def _create_contact_task(
    db: Session, project: AgroProject, settings: AgroSettings, body: Any
) -> None:
    prefix = f"Agroproyecto {project.ref}"
    if _task_exists(db, prefix):
        return
    try:
        system_user_id = _resolve_system_user_id(db)
    except RuntimeError:
        return
    plan = project.plan or {}
    viability = plan.get("viability") or {}
    budget = plan.get("budget") or {}
    lines = [
        f"Idea: {project.idea}",
        f"Estado: {campo_taxonomy.STATES.get(project.state, project.state)}",
        (
            "Presupuesto: "
            + (
                "no especificado"
                if project.budget_unknown
                else (
                    f"${float(project.budget_mxn):,.0f} MXN"
                    if project.budget_mxn
                    else "sin dato"
                )
            )
        ),
    ]
    if plan:
        lines.append(
            f"Proyecto: {plan.get('title')} — veredicto {viability.get('verdict')}"
        )
        if budget.get("investment_min_mxn") is not None:
            lines.append(
                f"Inversión estimada: ${budget.get('investment_min_mxn', 0):,.0f}"
                f"–${budget.get('investment_max_mxn', 0):,.0f} MXN"
            )
    if body.best_time:
        lines.append(f"Mejor horario: {body.best_time}")
    if body.message:
        lines.append(f"Mensaje: {body.message}")
    lines.append(
        f"Contacto: {project.contact_phone or '—'} · {project.contact_email or '—'}"
    )
    lines.append(f"{DEFAULT_ADMIN_URL}/agroproyectos/{project.id}")
    assignee = settings.assignee_email or _assignee_email()
    db.add(
        Task(
            title=f"{prefix} — {project.contact_name} pide que lo contacten ({body.preferred})"[
                :300
            ],
            description="\n".join(lines),
            status="pending",
            priority="high",
            due_date=datetime.now(timezone.utc).date(),
            category_id=_category_id(db, REVIEW_CATEGORY),
            created_by=system_user_id,
            assigned_to=_task_user_id(db, assignee) or system_user_id,
            task_number=get_next_task_number(db),
        )
    )
    db.flush()


def _queue_contact_email(
    project: AgroProject,
    settings: AgroSettings,
    background_tasks: BackgroundTasks,
    body: Any,
) -> None:
    recipients = list(settings.notify_emails) or _notify_emails()
    if not recipients:
        return
    html = (
        f"<p>{_h(project.contact_name or '')} pide que lo contacten por {_h(body.preferred)}.</p>"
        f"<p>Agroproyecto {_h(project.ref)}: {_h(project.idea)}</p>"
        f'<p><a href="{DEFAULT_ADMIN_URL}/agroproyectos/{project.id}">Ver en el admin</a></p>'
    )
    background_tasks.add_task(
        mailer.send,
        {
            "from": NOTIFY_FROM,
            "to": recipients,
            "subject": f"Agroproyecto {project.ref} pide que lo contacten",
            "html": html,
        },
        tag=f"agro_contact {project.ref}",
        idempotency_key=f"agro/{project.ref}/contact",
    )


def request_contact(
    db: Session,
    project: AgroProject,
    settings: AgroSettings,
    body: Any,
    background_tasks: BackgroundTasks,
) -> dict:
    if body.contact is not None:
        phone = _valid_phone(body.contact.phone)
        if phone is None:
            raise AgroValidationRejected(["invalid_phone"])
        project.contact_name = body.contact.name.strip()
        project.contact_phone = phone
        project.contact_email = body.contact.email
    if not project.contact_phone:
        raise AgroContactRequired()

    is_new = project.contact_requested_at is None
    if is_new:
        project.contact_requested_at = datetime.now(timezone.utc)
    project.contact_request = {
        "preferred": body.preferred,
        "best_time": body.best_time,
        "message": body.message,
    }
    if project.lead_status == "nuevo":
        project.lead_status = "contactado"
    db.commit()
    if is_new:
        _create_contact_task(db, project, settings, body)
        db.commit()
        _queue_contact_email(project, settings, background_tasks, body)
    return {"ok": True}


def _send_plan_ready_email(project: AgroProject, settings: AgroSettings) -> None:
    """Called from inside the background job (_execute_plan) — already off
    the request thread, so this sends synchronously instead of queueing on a
    FastAPI BackgroundTasks that no longer exists by the time this runs."""
    if not project.contact_name:
        return
    recipients = list(settings.notify_emails) or _notify_emails()
    if not recipients:
        return
    plan = project.plan or {}
    title = plan.get("title") or project.title or (project.idea or "")[:60]
    verdict = (plan.get("viability") or {}).get("verdict")
    budget = plan.get("budget") or {}
    admin_link = f"{DEFAULT_ADMIN_URL}/agroproyectos/{project.id}"
    html = (
        "<p>Nuevo agroproyecto con plan listo.</p>"
        f"<p><strong>{_h(title)}</strong> — {_h(project.contact_name)} ({_h(project.contact_phone or '')})</p>"
        f"<p>Veredicto: {_h(verdict or '—')}<br>"
        f"Inversión estimada: ${budget.get('investment_min_mxn', 0):,.0f}"
        f"–${budget.get('investment_max_mxn', 0):,.0f} MXN</p>"
        f'<p><a href="{admin_link}">Ver en el admin</a></p>'
    )
    subject = f"Nuevo agroproyecto: {title} — {project.contact_name}"
    mailer.send(
        {"from": NOTIFY_FROM, "to": recipients, "subject": subject, "html": html},
        tag=f"agro_new {project.ref}",
        idempotency_key=f"agro/{project.ref}/plan-ready",
    )


# ── events ───────────────────────────────────────────────────────────────────


def record_event(db: Session, project: AgroProject, event: str) -> None:
    events = _events_dict(project)
    events[event] = datetime.now(timezone.utc).isoformat()
    project.events = events
    flag_modified(project, "events")
    db.commit()


# ── admin ────────────────────────────────────────────────────────────────────


def list_projects(
    db: Session,
    *,
    lead_status: str | None = None,
    status: str | None = None,
    q: str | None = None,
    state: str | None = None,
    sector: str | None = None,
    days: int | None = None,
    has_contact: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    query = db.query(AgroProject)
    if lead_status:
        query = query.filter(AgroProject.lead_status == lead_status)
    if status:
        query = query.filter(AgroProject.status == status)
    if state:
        query = query.filter(AgroProject.state == state)
    if sector:
        query = query.filter(AgroProject.sector == sector)
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(AgroProject.created_at >= since)
    if has_contact is not None:
        if has_contact:
            query = query.filter(AgroProject.contact_phone.isnot(None))
        else:
            query = query.filter(AgroProject.contact_phone.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                AgroProject.contact_name.ilike(like),
                AgroProject.contact_phone.ilike(like),
                AgroProject.idea.ilike(like),
                AgroProject.title.ilike(like),
                AgroProject.ref.ilike(like),
            )
        )
    total = query.count()
    rows = (
        query.order_by(AgroProject.created_at.desc()).offset(offset).limit(limit).all()
    )
    lead_counts = {
        ls: db.query(func.count(AgroProject.id))
        .filter(AgroProject.lead_status == ls)
        .scalar()
        for ls in LEAD_STATUSES
    }
    return {
        "items": [agro_list_item(db, r) for r in rows],
        "total": total,
        "lead_counts": lead_counts,
    }


def get_project(db: Session, project_id: int) -> AgroProject | None:
    return db.get(AgroProject, project_id)


def patch_project(db: Session, project: AgroProject, patch: dict) -> AgroProject:
    if "lead_status" in patch and patch["lead_status"] is not None:
        if patch["lead_status"] not in LEAD_STATUSES:
            raise AgroValidationRejected(["invalid_lead_status"])
        project.lead_status = patch["lead_status"]
    if "staff_notes" in patch:
        project.staff_notes = patch["staff_notes"]
    if "assigned_to" in patch:
        project.assigned_to = patch["assigned_to"]
    db.commit()
    return project


def stats(db: Session, days: int = 30) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.query(AgroProject).filter(AgroProject.created_at >= since).all()

    totals = {
        "projects": len(rows),
        "with_contact": sum(1 for r in rows if r.contact_phone),
        "plans_ready": sum(
            1 for r in rows if r.status in ("plan_ready", "products_ready", "done")
        ),
        "done": sum(1 for r in rows if r.status == "done"),
        "errors": sum(1 for r in rows if r.status == "error"),
        "quotes": sum(1 for r in rows if r.quote_id),
        "quotes_total_mxn": 0.0,
        "contact_requests": sum(1 for r in rows if r.contact_requested_at),
        "won": sum(1 for r in rows if r.lead_status == "ganado"),
        "lost": sum(1 for r in rows if r.lead_status == "perdido"),
        "questions": sum(r.questions_count or 0 for r in rows),
        "whatsapp_clicks": sum(
            1 for r in rows if (r.events or {}).get("whatsapp_click")
        ),
        "cost_usd": 0.0,
        "avg_cost_usd": 0.0,
        "avg_generation_ms": 0,
    }
    quote_ids = [r.quote_id for r in rows if r.quote_id]
    if quote_ids:
        total_mxn = (
            db.query(func.coalesce(func.sum(Quote.total), 0))
            .filter(Quote.id.in_(quote_ids))
            .scalar()
        )
        totals["quotes_total_mxn"] = float(total_mxn or 0)
    cost_total = ai_usage.spent_usd(db, "agroproyectos", since)
    totals["cost_usd"] = float(cost_total)
    totals["avg_cost_usd"] = float(cost_total / len(rows)) if rows else 0.0
    gen_rows = [r.generation_ms for r in rows if r.generation_ms]
    totals["avg_generation_ms"] = (
        round(sum(gen_rows) / len(gen_rows)) if gen_rows else 0
    )

    funnel_defs = [
        ("started", "Proyectos iniciados", lambda r: True),
        (
            "plan_ready",
            "Plan listo",
            lambda r: r.status in ("plan_ready", "products_ready", "done"),
        ),
        (
            "products_ready",
            "Productos",
            lambda r: r.status in ("products_ready", "done"),
        ),
        (
            "quoted_or_contact",
            "Pidió cotización/contacto",
            lambda r: bool(r.quote_id) or bool(r.contact_requested_at),
        ),
        ("won", "Ganado", lambda r: r.lead_status == "ganado"),
    ]
    funnel = [
        {"key": key, "label": label, "count": sum(1 for r in rows if pred(r))}
        for key, label, pred in funnel_defs
    ]

    by_day: dict[str, dict] = {}
    cursor = since.date()
    today = datetime.now(timezone.utc).date()
    while cursor <= today:
        by_day[cursor.isoformat()] = {
            "day": cursor.isoformat(),
            "projects": 0,
            "cost_usd": 0.0,
        }
        cursor += timedelta(days=1)
    for r in rows:
        day = r.created_at.date().isoformat() if r.created_at else None
        if day in by_day:
            by_day[day]["projects"] += 1
    cost_rows = (
        db.query(func.date(AIUsage.created_at), func.sum(AIUsage.cost_usd))
        .filter(AIUsage.feature.like("agroproyectos%"), AIUsage.created_at >= since)
        .group_by(func.date(AIUsage.created_at))
        .all()
    )
    for day, cost in cost_rows:
        key = str(day)
        if key in by_day:
            by_day[key]["cost_usd"] = float(cost or 0)
    by_day_list = [by_day[k] for k in sorted(by_day)]

    def _counter(
        key_fn, labels: dict | None = None, top: int | None = None
    ) -> list[dict]:
        counts: dict[str, int] = {}
        for r in rows:
            key = key_fn(r)
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + 1
        items = sorted(counts.items(), key=lambda kv: -kv[1])
        if top:
            items = items[:top]
        if labels is not None:
            return [{"key": k, "label": labels.get(k, k), "count": c} for k, c in items]
        return [{"key": k, "count": c} for k, c in items]

    by_kind = _counter(lambda r: r.project_kind, top=15)
    by_sector = _counter(lambda r: r.sector, labels=campo_taxonomy.SECTORS)
    by_state = _counter(lambda r: r.state, labels=campo_taxonomy.STATES)
    by_producer_type = _counter(
        lambda r: r.producer_type,
        labels={"nuevo": "Nuevo", "mejorar": "Mejorar", "diversificar": "Diversificar"},
    )

    def _budget_bucket(r: AgroProject) -> str:
        if r.budget_unknown or r.budget_mxn is None:
            return "Sin presupuesto"
        amount = float(r.budget_mxn)
        if amount < 50_000:
            return "< $50 mil"
        if amount < 200_000:
            return "$50–200 mil"
        if amount < 1_000_000:
            return "$200 mil–1 M"
        return "> $1 M"

    budget_order = [
        "Sin presupuesto",
        "< $50 mil",
        "$50–200 mil",
        "$200 mil–1 M",
        "> $1 M",
    ]
    budget_counts = {k: 0 for k in budget_order}
    for r in rows:
        budget_counts[_budget_bucket(r)] += 1
    budget_buckets = [{"label": k, "count": budget_counts[k]} for k in budget_order]

    lead_status_counts = {
        s: sum(1 for r in rows if r.lead_status == s) for s in LEAD_STATUSES
    }

    product_counts: dict[str, dict] = {}
    for r in rows:
        for item in (r.products or {}).get("items") or []:
            handle = item.get("handle")
            if not handle:
                continue
            entry = product_counts.setdefault(
                handle,
                {"handle": handle, "title": item.get("title") or handle, "count": 0},
            )
            entry["count"] += 1
    top_products = sorted(product_counts.values(), key=lambda e: -e["count"])[:10]

    verdict_labels = {
        "viable": "Viable",
        "viable-con-ajustes": "Viable con ajustes",
        "requiere-mas-presupuesto": "Requiere más presupuesto",
        "no-recomendado": "No recomendado",
    }
    verdicts = _counter(
        lambda r: ((r.plan or {}).get("viability") or {}).get("verdict"),
        labels=verdict_labels,
    )

    return {
        "totals": totals,
        "funnel": funnel,
        "by_day": by_day_list,
        "by_kind": by_kind,
        "by_sector": by_sector,
        "by_state": by_state,
        "by_producer_type": by_producer_type,
        "budget_buckets": budget_buckets,
        "lead_status_counts": lead_status_counts,
        "top_products": top_products,
        "verdicts": verdicts,
    }
