"""
Apoyos gubernamentales — admin, jobs and storefront-feed routers.

- router (/apoyos): admin CRUD/review/settings/runs, Google auth.
- jobs_router (/jobs/apoyos): tick/step, called by the storefront's
  apoyos.yml GitHub Action workflow, guarded by verify_job_token.
- storefront_router (/storefront): the published feed the storefront's
  scripts/apoyos_sync.py pulls, guarded by the same X-API-Key as
  GET /storefront/products.

Logic: services/apoyos.py (CRUD, serialization, settings, feed) and
services/apoyos_discovery.py (runs, topics, the Claude step).
"""

import os
import secrets
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import ApoyoRun, get_db
from routes.jobs import verify_job_token
from routes.storefront import (
    GITHUB_API_BASE,
    GITHUB_STOREFRONT_REPO,
    GITHUB_TIMEOUT_SECONDS,
    _github_headers,
)
from services import app_settings
from services.apoyos import (
    ApoyoInput,
    ApoyoNotFound,
    ApoyosSettings,
    DeleteNotAllowed,
    create_apoyo,
    delete_apoyo,
    feed,
    get_apoyo,
    get_stats,
    list_apoyos,
    serialize_admin,
    serialize_run,
    set_status,
    settings_options,
    update_apoyo,
)
from services.apoyos_discovery import ApoyoRunNotFound, step, tick

router = APIRouter(prefix="/apoyos", tags=["apoyos"])
jobs_router = APIRouter(prefix="/jobs/apoyos", tags=["jobs"])
storefront_router = APIRouter(prefix="/storefront", tags=["storefront"])

APOYOS_WORKFLOW_FILE = "apoyos.yml"


def _problem(status: int, detail):
    raise HTTPException(status_code=status, detail=detail)


# ── admin: list / stats / detail ────────────────────────────────────────────


@router.get("")
def get_apoyos(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
    status: Annotated[str, Query()] = "all",
    q: Annotated[str | None, Query(max_length=200)] = None,
    state: Annotated[str | None, Query()] = None,
    sector: Annotated[str | None, Query()] = None,
    window: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if status not in ("draft", "published", "rejected", "archived", "all"):
        _problem(422, "invalid status filter")
    data = list_apoyos(
        db,
        status=status,
        q=q,
        state=state,
        sector=sector,
        window=window,
        limit=limit,
        offset=offset,
    )
    return {"success": True, "data": data}


@router.get("/stats")
def get_apoyos_stats(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    settings = app_settings.read(db, "apoyos", ApoyosSettings)
    return {"success": True, "data": get_stats(db, settings)}


@router.get("/settings")
def get_apoyos_settings(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    settings = app_settings.read(db, "apoyos", ApoyosSettings)
    info = app_settings.updated_info(db, "apoyos")
    return {
        "success": True,
        "data": {
            "settings": settings.model_dump(mode="json"),
            "defaults": ApoyosSettings().model_dump(mode="json"),
            **info,
            "options": settings_options(),
        },
    }


@router.put("/settings")
def put_apoyos_settings(
    body: dict[str, Any],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    try:
        settings = app_settings.write(
            db, "apoyos", ApoyosSettings, body, user.get("email")
        )
    except ValidationError as exc:
        _problem(422, exc.errors())
    info = app_settings.updated_info(db, "apoyos")
    return {
        "success": True,
        "data": {
            "settings": settings.model_dump(mode="json"),
            "defaults": ApoyosSettings().model_dump(mode="json"),
            **info,
            "options": settings_options(),
        },
    }


@router.get("/runs")
def get_apoyos_runs(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    rows = db.query(ApoyoRun).order_by(ApoyoRun.created_at.desc()).limit(limit).all()
    return {"success": True, "data": [serialize_run(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_apoyos_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    run = db.get(ApoyoRun, run_id)
    if run is None:
        _problem(404, "run not found")
    return {"success": True, "data": serialize_run(run)}


@router.post("/runs")
def post_apoyos_run(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    existing = (
        db.query(ApoyoRun).filter(ApoyoRun.status.in_(("queued", "running"))).first()
    )
    if existing is not None:
        _problem(409, "a run is already queued or running")

    run = ApoyoRun(
        trigger="manual", status="queued", topics=[], triggered_by=user.get("email")
    )
    db.add(run)
    db.commit()

    data = serialize_run(run)
    token = os.getenv("GITHUB_STOREFRONT_TOKEN")
    if not token:
        data["dispatch_error"] = "GITHUB_STOREFRONT_TOKEN not configured"
        return {"success": True, "data": data}
    try:
        resp = requests.post(
            f"{GITHUB_API_BASE}/repos/{GITHUB_STOREFRONT_REPO}/dispatches",
            json={"event_type": "apoyos-discover"},
            headers=_github_headers(token),
            timeout=GITHUB_TIMEOUT_SECONDS,
        )
        if resp.status_code != 204:
            data["dispatch_error"] = (
                f"GitHub dispatch failed with status {resp.status_code}"
            )
    except requests.RequestException as exc:
        data["dispatch_error"] = f"GitHub dispatch failed: {exc}"
    return {"success": True, "data": data}


@router.post("/runs/{run_id}/cancel")
def post_apoyos_run_cancel(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    run = db.get(ApoyoRun, run_id)
    if run is None:
        _problem(404, "run not found")
    if run.status in ("queued", "running"):
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
    return {"success": True, "data": serialize_run(run)}


@router.post("/site/publish")
def post_apoyos_site_publish(user: Annotated[dict, Depends(verify_google_token)]):
    token = os.getenv("GITHUB_STOREFRONT_TOKEN")
    if not token:
        _problem(503, "GITHUB_STOREFRONT_TOKEN not configured")
    try:
        resp = requests.post(
            f"{GITHUB_API_BASE}/repos/{GITHUB_STOREFRONT_REPO}/dispatches",
            json={"event_type": "apoyos-publish"},
            headers=_github_headers(token),
            timeout=GITHUB_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        _problem(502, f"GitHub dispatch failed: {exc}")
    if resp.status_code != 204:
        _problem(502, f"GitHub dispatch failed with status {resp.status_code}")
    return {"success": True, "data": {"status": "dispatched"}}


@router.get("/site/status")
def get_apoyos_site_status(user: Annotated[dict, Depends(verify_google_token)]):
    token = os.getenv("GITHUB_STOREFRONT_TOKEN")
    if not token:
        return {"success": True, "data": {"configured": False}}

    no_runs = {
        "configured": True,
        "status": None,
        "conclusion": None,
        "created_at": None,
        "html_url": None,
    }
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{GITHUB_STOREFRONT_REPO}"
            f"/actions/workflows/{APOYOS_WORKFLOW_FILE}/runs",
            params={"per_page": 1},
            headers=_github_headers(token),
            timeout=GITHUB_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        _problem(502, f"GitHub status check failed: {exc}")
    if resp.status_code == 404:
        return {"success": True, "data": no_runs}
    if resp.status_code != 200:
        _problem(502, f"GitHub status check failed with status {resp.status_code}")
    try:
        payload = resp.json() or {}
    except ValueError:
        _problem(502, "GitHub status response was not valid JSON")
    runs = payload.get("workflow_runs") or []
    if not runs:
        return {"success": True, "data": no_runs}
    run = runs[0]
    return {
        "success": True,
        "data": {
            "configured": True,
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("created_at"),
            "html_url": run.get("html_url"),
        },
    }


@router.get("/{apoyo_id}")
def get_apoyo_detail(
    apoyo_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    try:
        apoyo = get_apoyo(db, apoyo_id)
    except ApoyoNotFound:
        _problem(404, "apoyo not found")
    return {"success": True, "data": serialize_admin(apoyo)}


@router.post("")
def post_apoyo(
    body: ApoyoInput,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    if not body.title or not body.title.strip():
        _problem(422, "title is required")
    apoyo = create_apoyo(db, body, user.get("email"))
    return {"success": True, "data": serialize_admin(apoyo)}


@router.put("/{apoyo_id}")
def put_apoyo(
    apoyo_id: int,
    body: ApoyoInput,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    try:
        apoyo = update_apoyo(db, apoyo_id, body, user.get("email"))
    except ApoyoNotFound:
        _problem(404, "apoyo not found")
    except ValueError as exc:
        _problem(409, str(exc))
    return {"success": True, "data": serialize_admin(apoyo)}


class StatusBody(BaseModel):
    status: Literal["published", "draft", "rejected", "archived"]


@router.post("/{apoyo_id}/status")
def post_apoyo_status(
    apoyo_id: int,
    body: StatusBody,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    try:
        apoyo = set_status(db, apoyo_id, body.status, user.get("email"))
    except ApoyoNotFound:
        _problem(404, "apoyo not found")
    return {"success": True, "data": serialize_admin(apoyo)}


@router.delete("/{apoyo_id}")
def delete_apoyo_route(
    apoyo_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(verify_google_token)],
):
    try:
        delete_apoyo(db, apoyo_id)
    except ApoyoNotFound:
        _problem(404, "apoyo not found")
    except DeleteNotAllowed:
        _problem(409, "only draft or rejected apoyos can be deleted")
    return {"success": True, "data": {"deleted": True}}


# ── jobs ─────────────────────────────────────────────────────────────────────


@jobs_router.post("/tick", dependencies=[Depends(verify_job_token)])
def post_jobs_apoyos_tick(db: Annotated[Session, Depends(get_db)]):
    return tick(db)


class StepBody(BaseModel):
    run_id: int


@jobs_router.post("/step", dependencies=[Depends(verify_job_token)])
def post_jobs_apoyos_step(body: StepBody, db: Annotated[Session, Depends(get_db)]):
    try:
        return step(db, body.run_id)
    except ApoyoRunNotFound:
        _problem(404, "run not found")


# ── storefront feed ──────────────────────────────────────────────────────────


def _storefront_key_ok(x_api_key: str | None) -> bool:
    expected_key = os.getenv("STOREFRONT_API_KEY")
    if not expected_key or not x_api_key:
        return False
    return secrets.compare_digest(
        x_api_key.encode("utf-8", "replace"), expected_key.encode("utf-8")
    )


@storefront_router.get("/apoyos")
def get_storefront_apoyos(
    db: Annotated[Session, Depends(get_db)],
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    expected_key = os.getenv("STOREFRONT_API_KEY")
    if not expected_key:
        _problem(503, "STOREFRONT_API_KEY not configured")
    if not _storefront_key_ok(x_api_key):
        _problem(401, "Invalid or missing API key")
    settings = app_settings.read(db, "apoyos", ApoyosSettings)
    return {
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": feed(db, settings),
    }
