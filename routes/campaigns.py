"""
Campaign planner ("Campañas") routes.
- POST   /campaigns/generate                 topic -> strategy brief (LLM), persisted as draft
- POST   /campaigns/{id}/generate-plan       brief -> phases + items (LLM), replaces existing plan
- GET    /campaigns?status=                  list with items_total/items_done counts
- GET    /campaigns/{id}                     full nested campaign
- PATCH  /campaigns/{id}                     edit title/objective/status/dates/notes
- PATCH  /campaigns/items/{item_id}          edit item status/title/description/content/date
- POST   /campaigns/{id}/activate            create Tasks for task/whatsapp/research items
- POST   /campaigns/items/{item_id}/generate-post   generate a SocialPost for a post item
- DELETE /campaigns/{id}                     delete campaign + phases + items

Auth style: per-endpoint `user: dict = Depends(verify_google_token)` (like
routes/tasks_mgmt.py) — the generate/activate endpoints need the user email.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import (
    Campaign,
    CampaignItem,
    CampaignPhase,
    SocialPost,
    Task,
    TaskUser,
    get_db,
    get_next_task_number,
)
from routes.social_config import CHANNEL_FORMATS
from routes.social_topic import compute_topic_hash, normalize_topic
from services import campaign_planner
from services.campaign_planner import CampaignGenerationError
from services.quote_followup import _resolve_system_user_id

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

VALID_CAMPAIGN_STATUS = {"draft", "active", "completed", "archived"}
VALID_ITEM_STATUS = {"planned", "ready", "done", "skipped"}
TASKABLE_KINDS = ("task", "whatsapp", "research")


# ── Serializers (dates as ISO strings) ───────────────────────────────────────


def _iso(value):
    return value.isoformat() if value else None


def _item_dict(item: CampaignItem) -> dict:
    return {
        "id": item.id,
        "campaign_id": item.campaign_id,
        "phase_id": item.phase_id,
        "kind": item.kind,
        "channel": item.channel,
        "scheduled_date": _iso(item.scheduled_date),
        "title": item.title,
        "description": item.description,
        "content": item.content,
        "status": item.status,
        "task_id": item.task_id,
        "social_post_id": item.social_post_id,
        "sort_order": item.sort_order,
    }


def _phase_dict(phase: CampaignPhase, items: list) -> dict:
    return {
        "id": phase.id,
        "campaign_id": phase.campaign_id,
        "name": phase.name,
        "description": phase.description,
        "goal": phase.goal,
        "start_date": _iso(phase.start_date),
        "end_date": _iso(phase.end_date),
        "sort_order": phase.sort_order,
        "status": phase.status,
        "items": [_item_dict(i) for i in items],
    }


def _campaign_dict(c: Campaign) -> dict:
    return {
        "id": c.id,
        "topic": c.topic,
        "title": c.title,
        "objective": c.objective,
        "audience": c.audience,
        "size": c.size,
        "status": c.status,
        "start_date": _iso(c.start_date),
        "end_date": _iso(c.end_date),
        "goals": c.goals,
        "key_messages": c.key_messages,
        "channel_plan": c.channel_plan,
        "research": c.research,
        "notes": c.notes,
        "generation_model": c.generation_model,
        "created_by": c.created_by,
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
    }


def _campaign_items(db: Session, campaign_id: int) -> list:
    return (
        db.query(CampaignItem)
        .filter(CampaignItem.campaign_id == campaign_id)
        .order_by(CampaignItem.sort_order, CampaignItem.id)
        .all()
    )


def _campaign_full(db: Session, c: Campaign) -> dict:
    data = _campaign_dict(c)
    phases = (
        db.query(CampaignPhase)
        .filter(CampaignPhase.campaign_id == c.id)
        .order_by(CampaignPhase.sort_order, CampaignPhase.id)
        .all()
    )
    items = _campaign_items(db, c.id)
    by_phase = {}
    orphans = []
    for item in items:
        if item.phase_id is None:
            orphans.append(item)
        else:
            by_phase.setdefault(item.phase_id, []).append(item)
    data["phases"] = [_phase_dict(p, by_phase.get(p.id, [])) for p in phases]
    if orphans:
        # Items without a phase: materialized items (task_id/social_post_id
        # set) preserved through a plan regeneration, or items whose phase was
        # deleted (FK SET NULL) — keep them visible. NOTE: items_total below
        # includes them, while phases[].items does not.
        data["orphan_items"] = [_item_dict(i) for i in orphans]
    data["items_total"] = len(items)
    data["items_done"] = sum(1 for i in items if i.status == "done")
    return data


def _get_campaign_or_404(db: Session, campaign_id: int) -> Campaign:
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    return campaign


def _generation_error(e: CampaignGenerationError) -> HTTPException:
    return HTTPException(
        status_code=getattr(e, "status_code", 502) or 502, detail=str(e)
    )


# ── Pydantic bodies ──────────────────────────────────────────────────────────


class CampaignGenerateBody(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    start_date: date | None = None
    duration_weeks: int | None = Field(default=None, ge=1, le=52)
    notes: str | None = Field(default=None, max_length=2000)


class CampaignUpdateBody(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    objective: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class ItemUpdateBody(BaseModel):
    status: str | None = None
    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    content: str | None = None
    scheduled_date: date | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/generate")
def generate_campaign(
    body: CampaignGenerateBody,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic no puede estar vacío")

    start = body.start_date or date.today()
    try:
        brief = campaign_planner.generate_campaign_brief(
            db,
            topic,
            start,
            duration_weeks=body.duration_weeks,
            notes=body.notes,
            created_by=user.get("email"),
        )
    except CampaignGenerationError as e:
        raise _generation_error(e)

    campaign = Campaign(**brief)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _campaign_full(db, campaign)


@router.get("")
def list_campaigns(
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    query = db.query(Campaign)
    if status:
        if status not in VALID_CAMPAIGN_STATUS:
            raise HTTPException(
                status_code=422,
                detail=f"status debe ser uno de {sorted(VALID_CAMPAIGN_STATUS)}",
            )
        query = query.filter(Campaign.status == status)
    campaigns = query.order_by(Campaign.created_at.desc(), Campaign.id.desc()).all()

    result = []
    for c in campaigns:  # small data — a loop per campaign is fine
        items = _campaign_items(db, c.id)
        data = _campaign_dict(c)
        data["items_total"] = len(items)
        data["items_done"] = sum(1 for i in items if i.status == "done")
        result.append(data)
    return result


# NOTE: /items/... routes are declared before /{campaign_id} so the literal
# segment never has to compete with the int path param.


@router.patch("/items/{item_id}")
def update_item(
    item_id: int,
    body: ItemUpdateBody,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    item = db.query(CampaignItem).filter(CampaignItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in VALID_ITEM_STATUS:
        raise HTTPException(
            status_code=422,
            detail=f"status debe ser uno de {sorted(VALID_ITEM_STATUS)}",
        )
    if "title" in fields and not (fields["title"] or "").strip():
        raise HTTPException(status_code=422, detail="title no puede estar vacío")

    for key, value in fields.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return _item_dict(item)


@router.post("/items/{item_id}/generate-post")
def generate_post_for_item(
    item_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    item = db.query(CampaignItem).filter(CampaignItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    if item.kind != "post":
        raise HTTPException(status_code=409, detail="El item no es de tipo post")
    if item.social_post_id is not None:
        raise HTTPException(status_code=409, detail="El item ya tiene un post generado")

    campaign = _get_campaign_or_404(db, item.campaign_id)

    try:
        content = campaign_planner.generate_post_content(item, campaign)
    except CampaignGenerationError as e:
        raise _generation_error(e)

    # topic + topic_hash are NOT NULL on social_post — same helpers social.py uses.
    normalized_topic = normalize_topic(item.title)
    topic = normalized_topic or item.title.strip()
    topic_hash = compute_topic_hash(topic)
    date_for = item.scheduled_date or campaign.start_date or date.today()
    channel_fmt = CHANNEL_FORMATS.get(item.channel or "", {})

    post = SocialPost(
        date_for=date_for,
        caption=content["caption"],
        image_prompt=content.get("image_prompt"),
        post_type=content.get("post_type"),
        content_tone=content.get("content_tone"),
        status="planned",
        channel=item.channel,
        needs_music=bool(channel_fmt.get("needs_music", False)),
        topic=topic,
        topic_hash=topic_hash,
    )
    db.add(post)
    db.flush()

    item.social_post_id = post.id
    item.status = "ready"
    db.commit()
    db.refresh(item)
    return {"item": _item_dict(item), "social_post_id": post.id}


@router.get("/{campaign_id}")
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    campaign = _get_campaign_or_404(db, campaign_id)
    return _campaign_full(db, campaign)


@router.post("/{campaign_id}/generate-plan")
def generate_plan(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    campaign = _get_campaign_or_404(db, campaign_id)
    if campaign.status not in ("draft", "active"):
        raise HTTPException(
            status_code=409,
            detail="Solo se puede (re)generar el plan de campañas draft o active",
        )

    try:
        plan = campaign_planner.generate_campaign_plan(db, campaign)
    except CampaignGenerationError as e:
        raise _generation_error(e)

    # Regeneration replaces the existing plan (only after a successful call).
    # Items already materialized as Tasks/SocialPosts are preserved: deleting
    # them would reset the task_id/social_post_id idempotency keys, and a later
    # activate/generate-post would create duplicate rows for the same work.
    # They stay on the campaign without a phase (serialized as orphan_items).
    db.query(CampaignItem).filter(
        CampaignItem.campaign_id == campaign.id,
        CampaignItem.task_id.is_(None),
        CampaignItem.social_post_id.is_(None),
    ).delete()
    db.query(CampaignItem).filter(CampaignItem.campaign_id == campaign.id).update(
        {CampaignItem.phase_id: None}
    )
    db.query(CampaignPhase).filter(CampaignPhase.campaign_id == campaign.id).delete()

    for phase_data in plan["phases"]:
        items = phase_data.pop("items", [])
        phase = CampaignPhase(campaign_id=campaign.id, **phase_data)
        db.add(phase)
        db.flush()  # need phase.id for the items
        for item_data in items:
            db.add(
                CampaignItem(campaign_id=campaign.id, phase_id=phase.id, **item_data)
            )

    db.commit()
    db.refresh(campaign)
    return _campaign_full(db, campaign)


@router.patch("/{campaign_id}")
def update_campaign(
    campaign_id: int,
    body: CampaignUpdateBody,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    campaign = _get_campaign_or_404(db, campaign_id)

    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in VALID_CAMPAIGN_STATUS:
        raise HTTPException(
            status_code=422,
            detail=f"status debe ser uno de {sorted(VALID_CAMPAIGN_STATUS)}",
        )
    if "title" in fields and not (fields["title"] or "").strip():
        raise HTTPException(status_code=422, detail="title no puede estar vacío")

    for key, value in fields.items():
        setattr(campaign, key, value)
    db.commit()
    db.refresh(campaign)
    return _campaign_full(db, campaign)


@router.post("/{campaign_id}/activate")
def activate_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    campaign = _get_campaign_or_404(db, campaign_id)

    # Prefer the TaskUser matching the authenticated email; else the validated
    # system user (same pattern as services/quote_followup.py). Match the email
    # case-insensitively — task_user rows may store mixed-case emails (same
    # hazard quote_followup._resolve_task_users guards against).
    email = (user.get("email") or "").strip().lower()
    current = (
        db.query(TaskUser)
        .filter(func.lower(TaskUser.email) == email, TaskUser.is_active.is_(True))
        .first()
        if email
        else None
    )
    owner_id = current.id if current else _resolve_system_user_id(db)

    pending = (
        db.query(CampaignItem)
        .filter(
            CampaignItem.campaign_id == campaign.id,
            CampaignItem.kind.in_(TASKABLE_KINDS),
            CampaignItem.task_id.is_(None),  # idempotent: skip already-created
        )
        .order_by(CampaignItem.sort_order, CampaignItem.id)
        .all()
    )

    tasks_created = 0
    for item in pending:
        parts = []
        if item.description:
            parts.append(item.description)
        if item.scheduled_date:
            parts.append(f"Fecha objetivo: {item.scheduled_date.isoformat()}")
        if item.kind == "whatsapp" and item.content:
            parts.append(f"Mensaje propuesto:\n{item.content}")
        task = Task(
            title=f"[Campaña] {item.title}"[:300],
            description="\n".join(parts) or None,
            status="pending",
            priority="high" if item.kind == "whatsapp" else "medium",
            due_date=item.scheduled_date,
            created_by=owner_id,
            assigned_to=owner_id,
            task_number=get_next_task_number(db),
        )
        db.add(task)
        # Flush so the NEXT get_next_task_number() sees this number as taken
        # (otherwise every task claims the same lowest-free number).
        db.flush()
        item.task_id = task.id
        if item.status == "planned":
            item.status = "ready"
        tasks_created += 1

    campaign.status = "active"
    db.commit()
    db.refresh(campaign)
    return {"campaign": _campaign_full(db, campaign), "tasks_created": tasks_created}


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    campaign = _get_campaign_or_404(db, campaign_id)
    # Explicit deletes (children first) — Tasks/SocialPosts created from the
    # campaign are intentionally left alone.
    db.query(CampaignItem).filter(CampaignItem.campaign_id == campaign.id).delete()
    db.query(CampaignPhase).filter(CampaignPhase.campaign_id == campaign.id).delete()
    db.delete(campaign)
    db.commit()
    return Response(status_code=204)
