"""
Agroproyectos — storefront and admin routers.

Storefront (prefix /storefront, same STOREFRONT_ORDERS_API_KEY as
/storefront/orders and /storefront/quote-requests): the AI project designer at
todoparaelcampo.com.mx/agroproyectos. Admin (prefix /agroproyectos, Google
auth): the lead list, project detail, stats and settings.

Business logic: services/agroproyectos.py (settings, limits, budgets,
serialization, apoyos matching, quote, contact, events, stats) and
services/agroproyectos_ai.py (the Claude calls). See that module's docstring
for the status flow and why an AI failure is both recorded on the project AND
raised as an HTTP error (so the storefront's retry button and the admin's
error column agree).
"""

import logging
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import get_db
from routes.storefront_orders import require_orders_key
from services import agroproyectos, app_settings, campo_taxonomy
from services.ai_usage import MODEL_LABELS, AIUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agroproyectos", tags=["agroproyectos"])
storefront_router = APIRouter(prefix="/storefront", tags=["storefront"])

TOKEN_PATTERN = r"^[0-9a-f-]{36}$"


def _problem(
    status: int, reason: str, problems: list[str] | None = None
) -> HTTPException:
    detail: dict[str, Any] = {"reason": reason}
    if problems is not None:
        detail["problems"] = problems
    return HTTPException(status_code=status, detail=detail)


def _agro_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, agroproyectos.AgroDisabled):
        return _problem(503, "tool_disabled")
    if isinstance(exc, agroproyectos.AgroContactRequired):
        return _problem(422, "contact_required")
    if isinstance(exc, agroproyectos.AgroValidationRejected):
        return _problem(422, "invalid_request", exc.problems)
    if isinstance(exc, agroproyectos.AgroLimitReached):
        return _problem(429, "rate_limited")
    if isinstance(exc, agroproyectos.AgroBudgetExceeded):
        return _problem(429, "budget_exceeded")
    if isinstance(exc, agroproyectos.QuestionsExhausted):
        return _problem(429, "questions_exhausted")
    if isinstance(exc, agroproyectos.AgroNotReady):
        return _problem(409, "not_ready")
    if isinstance(exc, AIUnavailable):
        if exc.reason == "no_api_key":
            return _problem(503, "no_api_key")
        return _problem(502, "ai_unavailable")
    raise exc


AGRO_EXCEPTIONS = (
    agroproyectos.AgroDisabled,
    agroproyectos.AgroContactRequired,
    agroproyectos.AgroValidationRejected,
    agroproyectos.AgroLimitReached,
    agroproyectos.AgroBudgetExceeded,
    agroproyectos.QuestionsExhausted,
    agroproyectos.AgroNotReady,
    AIUnavailable,
)


# ── storefront request bodies ────────────────────────────────────────────────


class _Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class Contact(_Body):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=1, max_length=20)
    email: str | None = Field(default=None, max_length=255)


class Land(_Body):
    has_land: bool
    area: Decimal | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    unit: Literal["m2", "ha"]


class Utm(_Body):
    source: str | None = Field(default=None, max_length=200)
    medium: str | None = Field(default=None, max_length=200)
    campaign: str | None = Field(default=None, max_length=200)
    referrer: str | None = Field(default=None, max_length=200)
    apoyo: str | None = Field(default=None, max_length=200)


class AgroCreate(_Body):
    producer_type: Literal["nuevo", "mejorar", "diversificar"]
    idea: str = Field(min_length=10, max_length=2000)
    current_operation: str | None = Field(default=None, max_length=1500)
    state: str
    municipio: str | None = Field(default=None, max_length=120)
    land: Land
    water: Literal[
        "pozo", "rio-canal", "red", "presa-bordo", "temporal", "ninguna", "no-se"
    ]
    electricity: Literal["cfe", "no", "no-se"]
    experience: Literal["ninguna", "algo", "mucha"]
    goal: Literal["autoconsumo", "venta-local", "negocio", "no-se"]
    timeline: Literal["ya", "3-meses", "6-meses", "explorando"]
    budget_mxn: Decimal | None = Field(
        default=None, ge=0, le=100_000_000, allow_inf_nan=False
    )
    budget_unknown: bool
    wants_financing: bool
    contact: Contact | None = None
    utm: Utm | None = None

    @field_validator("state")
    @classmethod
    def _valid_state(cls, value: str) -> str:
        if value == "nacional" or value not in campo_taxonomy.STATES:
            raise ValueError("invalid state")
        return value


class Candidate(_Body):
    handle: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    category_slug: str = Field(max_length=100)
    price: Decimal | None = Field(
        default=None, ge=0, le=99_999_999, allow_inf_nan=False
    )
    price_total: Decimal | None = Field(
        default=None, ge=0, le=99_999_999, allow_inf_nan=False
    )
    iva_rate: Decimal = Field(allow_inf_nan=False)
    unit_label: str | None = Field(default=None, max_length=100)
    product_id: int | None = Field(default=None, ge=1)

    @field_validator("iva_rate")
    @classmethod
    def _known_iva_rate(cls, value: Decimal) -> Decimal:
        if value not in (Decimal(0), Decimal("0.16")):
            raise ValueError("iva_rate must be 0 or 0.16")
        return value


class CandidatesBody(_Body):
    candidates: list[Candidate] = Field(default_factory=list, max_length=600)


class QuoteLine(_Body):
    handle: str = Field(min_length=1, max_length=200)
    product_id: int | None = Field(default=None, ge=1)
    description: str = Field(min_length=1, max_length=500)
    unit_label: str | None = Field(default=None, max_length=100)
    quantity: Decimal = Field(gt=0, le=999_999, allow_inf_nan=False)
    unit_price: Decimal = Field(ge=0, le=99_999_999, allow_inf_nan=False)
    iva_rate: Decimal = Field(allow_inf_nan=False)

    @field_validator("iva_rate")
    @classmethod
    def _known_iva_rate(cls, value: Decimal) -> Decimal:
        if value not in (Decimal(0), Decimal("0.16")):
            raise ValueError("iva_rate must be 0 or 0.16")
        return value


class QuoteBody(_Body):
    items: list[QuoteLine] = Field(min_length=1, max_length=60)
    contact: Contact | None = None


class QuestionBody(_Body):
    question: str = Field(min_length=3, max_length=600)


class ContactBody(_Body):
    preferred: Literal["whatsapp", "llamada", "correo"]
    best_time: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=1000)
    contact: Contact | None = None


class EventBody(_Body):
    event: Literal[
        "whatsapp_click",
        "print",
        "share",
        "apoyo_click",
        "product_click",
        "quote_click",
    ]


# ── storefront endpoints ─────────────────────────────────────────────────────


def _get_project_or_404(db: Session, token: str) -> Any:
    project = agroproyectos.get_by_token(db, token)
    if project is None:
        raise _problem(404, "not_found")
    return project


@storefront_router.post(
    "/agro-projects", status_code=201, dependencies=[Depends(require_orders_key)]
)
def post_agro_project(body: AgroCreate, db: Annotated[Session, Depends(get_db)]):
    settings = agroproyectos.read_settings(db)
    try:
        return agroproyectos.create_project(db, body, settings)
    except AGRO_EXCEPTIONS as exc:
        db.rollback()
        raise _agro_http_error(exc)


@storefront_router.get(
    "/agro-projects/{token}", dependencies=[Depends(require_orders_key)]
)
def get_agro_project(
    token: Annotated[str, Path(pattern=TOKEN_PATTERN)],
    db: Annotated[Session, Depends(get_db)],
):
    settings = agroproyectos.read_settings(db)
    project = _get_project_or_404(db, token)
    return {"project": agroproyectos.agro_public(db, project, settings)}


@storefront_router.post(
    "/agro-projects/{token}/plan", dependencies=[Depends(require_orders_key)]
)
def post_agro_plan(
    token: Annotated[str, Path(pattern=TOKEN_PATTERN)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    body: Annotated[dict | None, Body()] = None,
):
    """Starts the plan step in the background (App Runner's 120s request cap
    is well under a real Claude call, see services/agroproyectos.py) and
    answers 202 at once, or 200 once the result already exists. The
    storefront polls GET until status/generating settle."""
    settings = agroproyectos.read_settings(db)
    project = _get_project_or_404(db, token)
    try:
        outcome = agroproyectos.run_plan(db, project, settings)
    except AGRO_EXCEPTIONS as exc:
        db.rollback()
        raise _agro_http_error(exc)
    response.status_code = 200 if outcome == agroproyectos.STEP_DONE else 202
    return {"project": agroproyectos.agro_public(db, project, settings)}


@storefront_router.post(
    "/agro-projects/{token}/productos", dependencies=[Depends(require_orders_key)]
)
def post_agro_productos(
    token: Annotated[str, Path(pattern=TOKEN_PATTERN)],
    body: CandidatesBody,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
):
    settings = agroproyectos.read_settings(db)
    project = _get_project_or_404(db, token)
    try:
        outcome = agroproyectos.run_productos(db, project, settings, body.candidates)
    except AGRO_EXCEPTIONS as exc:
        db.rollback()
        raise _agro_http_error(exc)
    response.status_code = 200 if outcome == agroproyectos.STEP_DONE else 202
    return {"project": agroproyectos.agro_public(db, project, settings)}


@storefront_router.post(
    "/agro-projects/{token}/riego", dependencies=[Depends(require_orders_key)]
)
def post_agro_riego(
    token: Annotated[str, Path(pattern=TOKEN_PATTERN)],
    body: CandidatesBody,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
):
    settings = agroproyectos.read_settings(db)
    project = _get_project_or_404(db, token)
    try:
        outcome = agroproyectos.run_riego(db, project, settings, body.candidates)
    except AGRO_EXCEPTIONS as exc:
        db.rollback()
        raise _agro_http_error(exc)
    response.status_code = 200 if outcome == agroproyectos.STEP_DONE else 202
    return {"project": agroproyectos.agro_public(db, project, settings)}


@storefront_router.post(
    "/agro-projects/{token}/quote",
    status_code=201,
    dependencies=[Depends(require_orders_key)],
)
def post_agro_quote(
    token: Annotated[str, Path(pattern=TOKEN_PATTERN)],
    body: QuoteBody,
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
):
    settings = agroproyectos.read_settings(db)
    project = _get_project_or_404(db, token)
    try:
        return agroproyectos.create_quote(
            db, project, settings, body.items, body.contact, background_tasks
        )
    except AGRO_EXCEPTIONS as exc:
        db.rollback()
        raise _agro_http_error(exc)


@storefront_router.post(
    "/agro-projects/{token}/questions", dependencies=[Depends(require_orders_key)]
)
def post_agro_question(
    token: Annotated[str, Path(pattern=TOKEN_PATTERN)],
    body: QuestionBody,
    db: Annotated[Session, Depends(get_db)],
):
    settings = agroproyectos.read_settings(db)
    project = _get_project_or_404(db, token)
    try:
        return agroproyectos.ask_question(db, project, settings, body.question)
    except AGRO_EXCEPTIONS as exc:
        db.rollback()
        raise _agro_http_error(exc)


@storefront_router.post(
    "/agro-projects/{token}/contact", dependencies=[Depends(require_orders_key)]
)
def post_agro_contact(
    token: Annotated[str, Path(pattern=TOKEN_PATTERN)],
    body: ContactBody,
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
):
    settings = agroproyectos.read_settings(db)
    project = _get_project_or_404(db, token)
    try:
        return agroproyectos.request_contact(
            db, project, settings, body, background_tasks
        )
    except AGRO_EXCEPTIONS as exc:
        db.rollback()
        raise _agro_http_error(exc)


@storefront_router.post(
    "/agro-projects/{token}/events", dependencies=[Depends(require_orders_key)]
)
def post_agro_event(
    token: Annotated[str, Path(pattern=TOKEN_PATTERN)],
    body: EventBody,
    db: Annotated[Session, Depends(get_db)],
):
    project = _get_project_or_404(db, token)
    agroproyectos.record_event(db, project, body.event)
    return {"ok": True}


# ── admin ────────────────────────────────────────────────────────────────────


class PatchProjectBody(_Body):
    lead_status: str | None = None
    staff_notes: str | None = Field(default=None, max_length=10_000)
    assigned_to: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _at_least_one(self):
        if (
            self.lead_status is None
            and self.staff_notes is None
            and self.assigned_to is None
        ):
            raise ValueError("nothing to patch")
        return self


@router.get("/projects")
def list_projects_route(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
    lead_status: Annotated[str | None, Query(max_length=30)] = None,
    status: Annotated[str | None, Query(max_length=30)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    state: Annotated[str | None, Query(max_length=60)] = None,
    sector: Annotated[str | None, Query(max_length=30)] = None,
    days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    has_contact: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    data = agroproyectos.list_projects(
        db,
        lead_status=lead_status,
        status=status,
        q=q,
        state=state,
        sector=sector,
        days=days,
        has_contact=has_contact,
        limit=limit,
        offset=offset,
    )
    return {"success": True, "data": data}


@router.get("/projects/{project_id}")
def get_project_route(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    project = agroproyectos.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"success": True, "data": agroproyectos.agro_admin(db, project)}


@router.patch("/projects/{project_id}")
def patch_project_route(
    project_id: int,
    body: PatchProjectBody,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    project = agroproyectos.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        agroproyectos.patch_project(
            db, project, body.model_dump(exclude_unset=True, exclude_none=False)
        )
    except agroproyectos.AgroValidationRejected as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"reason": "invalid_request", "problems": exc.problems},
        )
    return {"success": True, "data": agroproyectos.agro_admin(db, project)}


@router.get("/stats")
def get_stats_route(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
    days: Annotated[int, Query(ge=1, le=366)] = 30,
):
    return {"success": True, "data": agroproyectos.stats(db, days)}


@router.get("/settings")
def get_settings_route(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    settings = agroproyectos.read_settings(db)
    info = app_settings.updated_info(db, agroproyectos.SETTINGS_KEY)
    return {
        "success": True,
        "data": {
            "settings": settings.model_dump(mode="json"),
            "defaults": agroproyectos.AgroSettings().model_dump(mode="json"),
            **info,
            "options": {"models": MODEL_LABELS},
        },
    }


@router.put("/settings")
def put_settings_route(
    patch: dict,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    from pydantic import ValidationError

    try:
        settings = agroproyectos.write_settings(db, patch, user.get("email"))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())
    info = app_settings.updated_info(db, agroproyectos.SETTINGS_KEY)
    return {
        "success": True,
        "data": {
            "settings": settings.model_dump(mode="json"),
            "defaults": agroproyectos.AgroSettings().model_dump(mode="json"),
            **info,
            "options": {"models": MODEL_LABELS},
        },
    }
