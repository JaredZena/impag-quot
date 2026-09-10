"""Inventario de herramientas (uso interno).

- GET    /tools                     list + summary (filters: status, kind, q, include_retired)
- GET    /tools/summary             totals by status (feeds the Hoy / INFORMACION numbers)
- POST   /tools                     register a tool ("Registrar herramienta")
- GET    /tools/{id}                detail + photos + movement history
- PUT    /tools/{id}                edit descriptive fields (status changes go through movements)
- DELETE /tools/{id}                delete a record entered by mistake
- POST   /tools/{id}/movements      lifecycle change: recibida, salida, regreso, baja, reactivar
- POST   /tools/{id}/images         upload one photo (multipart)
- DELETE /tools/{id}/images         remove one photo by key
- PUT    /tools/{id}/images/order   reorder photos (first = portada)

Tools live in their own tables (tool, tool_movement), apart from the sale
catalog, so they never show up in POS, quotes or the storefront feed. The
HERRAMIENTAS tab of Operaciones_Comerciales_IMPAG is imported once by
scripts/import_tools_from_sheet.py (keyed on tool.sheet_no).

Photos go to the PRIVATE documents bucket under tool-images/{id}/ and are
served as short-lived presigned URLs (product photos are public because the
storefront shows them; tool photos are internal). Uploads reuse the product
image pipeline: EXIF-aware, alpha flattened, longest side <= 1600 px, WEBP.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from auth import verify_google_token
from models import Tool, ToolMovement, get_db
from routes.product_images import (
    ALLOWED_CONTENT_TYPES,
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_SIZE,
    ImageDeleteRequest,
    ImageOrderRequest,
    ImageTooManyPixelsError,
    process_product_image,
)
from services.r2_storage import (
    delete_file as r2_delete,
)
from services.r2_storage import (
    generate_presigned_view_url,
)
from services.r2_storage import (
    upload_file as r2_upload,
)

router = APIRouter(
    prefix="/tools",
    tags=["tools"],
    dependencies=[Depends(verify_google_token)],
)

BUSINESS_TZ = ZoneInfo("America/Mexico_City")
IMAGE_URL_TTL = 3600  # seconds; the UI refetches the tool once when a URL expires
MAX_IMAGES_PER_TOOL = 12

# Labels use the team's own vocabulary from the HERRAMIENTAS tab.
STATUS_LABELS = {
    "pendiente_entrega": "Pendiente entrega",
    "en_local": "En el local",
    "en_obra": "En obra",
    "con_cliente": "Con el cliente",
    "baja": "Baja",
}
# Someone outside the store has the tool: a holder (obra/cliente/persona) is required.
OUT_STATUSES = {"en_obra", "con_cliente"}
# Pendiente entrega: the tool isn't in the store (not delivered yet, or not
# handed back). Who has it is optional context — the sheet's comments say
# things like "JARED", "LENCHO", "preguntar a adrian".
HOLDER_OPTIONAL_STATUSES = {"pendiente_entrega"}
# A tool can be registered already out (bought straight for an install), never retired.
CREATE_STATUSES = set(STATUS_LABELS) - {"baja"}
KINDS = {"herramienta", "consumible"}
MOVEMENT_LABELS = {
    "compra": "Registrada",
    "alta": "Importada de la hoja",
    "recibida": "Recibida en el local",
    "salida": "Salida",
    "regreso": "Regresó al local",
    "baja": "Baja",
    "reactivar": "Reactivada",
    "ajuste": "Ajuste",
}
MAX_QUANTITY = Decimal(100000000)
MAX_COST = Decimal(10000000000)
CENTS = Decimal("0.01")


# ==================== Schemas ====================


class _ToolFields(BaseModel):
    name: str | None = Field(None, max_length=255)
    kind: str | None = None
    quantity: Decimal | None = None
    unit: str | None = Field(None, max_length=20)
    unit_cost: Decimal | None = None
    purchase_date: date | None = None
    supplier_name: str | None = Field(None, max_length=200)
    invoice_ref: str | None = Field(None, max_length=120)
    location: str | None = Field(None, max_length=60)
    holder: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=4000)


class ToolCreate(_ToolFields):
    name: str = Field(..., max_length=255)
    status: str = "en_local"


class ToolUpdate(_ToolFields):
    """Every field optional; only the fields present in the body change."""


class MovementCreate(BaseModel):
    to_status: str
    holder: str | None = Field(None, max_length=200)
    location: str | None = Field(None, max_length=60)
    note: str | None = Field(None, max_length=2000)
    occurred_on: date | None = None


# ==================== Helpers ====================


def _ok(data=None) -> dict:
    return {"success": True, "data": data, "error": None, "message": None}


def _business_today() -> date:
    """Durango business date (the container clock is UTC)."""
    return datetime.now(BUSINESS_TZ).date()


def _one_line(value: str | None) -> str | None:
    """Collapse whitespace; empty -> None."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _escape_like(text: str) -> str:
    """Escape LIKE/ILIKE metacharacters (backslash is the escape char)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fmt_qty(value: Decimal | None) -> str:
    """Decimal('2.00') -> '2', Decimal('1.50') -> '1.5'."""
    return f"{Decimal(value or 0).normalize():f}"


def _clean_fields(data: dict, *, creating: bool) -> dict:
    """Validate + normalize the descriptive fields present in `data`."""
    out: dict = {}
    if "name" in data:
        name = _one_line(data["name"])
        if not name:
            raise HTTPException(400, "Escribe el nombre de la herramienta")
        out["name"] = name
    if "kind" in data:
        kind = (data["kind"] or "herramienta").strip().lower()
        if kind not in KINDS:
            raise HTTPException(400, "Tipo inválido: usa herramienta o consumible")
        out["kind"] = kind
    if "quantity" in data:
        qty = data["quantity"]
        if qty is None:
            if not creating:
                raise HTTPException(400, "La cantidad es obligatoria")
            qty = Decimal(1)
        if creating and qty <= 0:
            raise HTTPException(400, "La cantidad debe ser mayor a cero")
        if qty < 0:
            raise HTTPException(400, "La cantidad no puede ser negativa")
        if qty >= MAX_QUANTITY:
            raise HTTPException(400, "La cantidad es demasiado grande")
        out["quantity"] = qty.quantize(CENTS)
    if "unit" in data:
        out["unit"] = (_one_line(data["unit"]) or "PIEZA").upper()
    if "unit_cost" in data:
        cost = data["unit_cost"]
        if cost is not None:
            if cost < 0:
                raise HTTPException(400, "El costo no puede ser negativo")
            if cost >= MAX_COST:
                raise HTTPException(400, "El costo es demasiado grande")
            cost = cost.quantize(CENTS)
        out["unit_cost"] = cost
    if "purchase_date" in data:
        out["purchase_date"] = data["purchase_date"]
    for key in ("supplier_name", "invoice_ref", "location", "holder"):
        if key in data:
            out[key] = _one_line(data[key])
    if "notes" in data:
        notes = data["notes"]
        out["notes"] = (notes.strip() or None) if notes is not None else None
    return out


def _num(value) -> float | None:
    return float(value) if value is not None else None


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _line_value(tool: Tool) -> Decimal | None:
    """Cantidad × costo; None when the tool has no cost on file."""
    if tool.unit_cost is None:
        return None
    return Decimal(tool.quantity or 0) * Decimal(tool.unit_cost)


def _image_url(key: str) -> str:
    return generate_presigned_view_url(key, "image/webp", IMAGE_URL_TTL)


def _movement_dict(m: ToolMovement) -> dict:
    return {
        "id": m.id,
        "kind": m.kind,
        "kind_label": MOVEMENT_LABELS.get(m.kind, m.kind),
        "from_status": m.from_status,
        "to_status": m.to_status,
        "holder": m.holder,
        "note": m.note,
        "occurred_on": _iso(m.occurred_on),
        "created_at": _iso(m.created_at),
        "created_by": m.created_by,
    }


def _brief(t: Tool) -> dict:
    value = _line_value(t)
    images = t.images or []
    return {
        "id": t.id,
        "name": t.name,
        "kind": t.kind,
        "quantity": _num(t.quantity),
        "unit": t.unit,
        "unit_cost": _num(t.unit_cost),
        "total_value": round(float(value), 2) if value is not None else None,
        "status": t.status,
        "status_label": STATUS_LABELS.get(t.status, t.status),
        "location": t.location,
        "holder": t.holder,
        "supplier_name": t.supplier_name,
        "purchase_date": _iso(t.purchase_date),
        "image_count": len(images),
        "primary_image_url": _image_url(images[0]) if images else None,
        "retired_at": _iso(t.retired_at),
        "updated_at": _iso(t.updated_at or t.created_at),
    }


def _detail(t: Tool) -> dict:
    data = _brief(t)
    data.update(
        {
            "invoice_ref": t.invoice_ref,
            "notes": t.notes,
            "retired_reason": t.retired_reason,
            "sheet_no": t.sheet_no,
            "images": [{"key": k, "url": _image_url(k)} for k in (t.images or [])],
            "created_at": _iso(t.created_at),
            "created_by": t.created_by,
            "movements": [
                _movement_dict(m)
                for m in sorted(t.movements, key=lambda m: m.id, reverse=True)
            ],
        }
    )
    return data


def _summary(db: Session) -> dict:
    """Counts and value (cantidad × costo) per status; totals exclude bajas."""
    rows = (
        db.query(
            Tool.status,
            func.count(Tool.id),
            func.sum(Tool.quantity * Tool.unit_cost),
            func.count(Tool.unit_cost),
        )
        .group_by(Tool.status)
        .all()
    )
    by_status = {
        s: {"label": label, "count": 0, "value": 0.0}
        for s, label in STATUS_LABELS.items()
    }
    active_count = 0
    total_value = Decimal(0)
    missing_cost = 0
    for status, count, value, costed in rows:
        value = Decimal(str(value or 0))
        entry = by_status.setdefault(
            status, {"label": status, "count": 0, "value": 0.0}
        )
        entry["count"] = count
        entry["value"] = round(float(value), 2)
        if status != "baja":
            active_count += count
            total_value += value
            missing_cost += count - costed
    return {
        "by_status": by_status,
        "active_count": active_count,
        "total_value": round(float(total_value), 2),
        "missing_cost_count": missing_cost,
        "retired_count": by_status["baja"]["count"],
    }


def _get_tool_or_404(tool_id: int, db: Session) -> Tool:
    tool = db.query(Tool).filter(Tool.id == tool_id).first()
    if tool is None:
        raise HTTPException(status_code=404, detail="Herramienta no encontrada")
    return tool


def _movement_kind(from_status: str, to_status: str) -> str:
    if to_status == "baja":
        return "baja"
    if from_status == "baja":
        return "reactivar"
    if to_status in OUT_STATUSES:
        return "salida"
    if from_status in OUT_STATUSES and to_status == "en_local":
        return "regreso"
    if from_status == "pendiente_entrega" and to_status == "en_local":
        return "recibida"
    return "ajuste"


# ==================== Tools ====================


@router.get("")
def list_tools(
    status: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    include_retired: bool = False,
    db: Session = Depends(get_db),
):
    """List tools. Bajas are hidden unless include_retired or status=baja."""
    query = db.query(Tool)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        unknown = [s for s in statuses if s not in STATUS_LABELS]
        if unknown:
            raise HTTPException(400, f"Estado desconocido: {', '.join(unknown)}")
        query = query.filter(Tool.status.in_(statuses))
    elif not include_retired:
        query = query.filter(Tool.status != "baja")
    if kind:
        if kind not in KINDS:
            raise HTTPException(400, "Tipo inválido: usa herramienta o consumible")
        query = query.filter(Tool.kind == kind)
    term = _one_line(q)
    if term:
        like = f"%{_escape_like(term)}%"
        query = query.filter(
            or_(
                Tool.name.ilike(like, escape="\\"),
                Tool.holder.ilike(like, escape="\\"),
                Tool.supplier_name.ilike(like, escape="\\"),
                Tool.notes.ilike(like, escape="\\"),
            )
        )
    tools = query.order_by(func.lower(Tool.name), Tool.id).all()
    return _ok(
        {
            "items": [_brief(t) for t in tools],
            "total": len(tools),
            "summary": _summary(db),
        }
    )


@router.get("/summary")
def tools_summary(db: Session = Depends(get_db)):
    return _ok(_summary(db))


@router.post("")
def create_tool(
    body: ToolCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    data = body.model_dump()
    status = (data.pop("status") or "en_local").strip()
    if status not in CREATE_STATUSES:
        raise HTTPException(400, "Estado inicial inválido")
    fields = _clean_fields(data, creating=True)
    if status in OUT_STATUSES:
        if not fields.get("holder"):
            raise HTTPException(
                400, "Indica la obra, el cliente o la persona que tiene la herramienta"
            )
    elif status not in HOLDER_OPTIONAL_STATUSES:
        fields["holder"] = None

    email = user.get("email") or None
    tool = Tool(**fields, status=status, created_by=email)
    db.add(tool)
    db.flush()
    db.add(
        ToolMovement(
            tool_id=tool.id,
            kind="compra",
            from_status=None,
            to_status=status,
            holder=fields.get("holder"),
            occurred_on=fields.get("purchase_date") or _business_today(),
            created_by=email,
        )
    )
    db.commit()
    db.refresh(tool)
    return _ok(_detail(tool))


@router.get("/{tool_id}")
def get_tool(tool_id: int, db: Session = Depends(get_db)):
    return _ok(_detail(_get_tool_or_404(tool_id, db)))


@router.put("/{tool_id}")
def update_tool(
    tool_id: int,
    body: ToolUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Edit descriptive fields. Status is NOT editable here — use movements."""
    tool = _get_tool_or_404(tool_id, db)
    fields = _clean_fields(body.model_dump(exclude_unset=True), creating=False)

    if "holder" in fields:
        if tool.status in OUT_STATUSES:
            if not fields["holder"]:
                raise HTTPException(400, "Indica quién tiene la herramienta")
        elif tool.status not in HOLDER_OPTIONAL_STATUSES:
            if fields["holder"]:
                raise HTTPException(
                    400,
                    "Solo una herramienta fuera del local tiene responsable; registra una salida",
                )
            fields.pop("holder")

    old_qty = tool.quantity
    for key, value in fields.items():
        setattr(tool, key, value)
    if "quantity" in fields and Decimal(old_qty or 0) != fields["quantity"]:
        db.add(
            ToolMovement(
                tool_id=tool.id,
                kind="ajuste",
                from_status=tool.status,
                to_status=tool.status,
                note=f"Cantidad {_fmt_qty(old_qty)} → {_fmt_qty(fields['quantity'])}",
                occurred_on=_business_today(),
                created_by=user.get("email") or None,
            )
        )
    db.commit()
    db.refresh(tool)
    return _ok(_detail(tool))


@router.delete("/{tool_id}")
def delete_tool(tool_id: int, db: Session = Depends(get_db)):
    """Delete a record entered by mistake. Retiring a real tool is a baja."""
    tool = _get_tool_or_404(tool_id, db)
    keys = list(tool.images or [])
    db.delete(tool)
    db.commit()
    # Best-effort R2 cleanup: a missing object must not fail the delete.
    for key in keys:
        try:
            r2_delete(key)
        except Exception as e:
            print(f"Warning: failed to delete R2 object {key}: {e}")
    return _ok()


@router.post("/{tool_id}/movements")
def add_movement(
    tool_id: int,
    body: MovementCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Change a tool's status and log it (salida, regreso, recibida, baja...)."""
    tool = _get_tool_or_404(tool_id, db)
    to_status = (body.to_status or "").strip()
    if to_status not in STATUS_LABELS:
        raise HTTPException(400, "Estado desconocido")
    from_status = tool.status
    if to_status == from_status:
        raise HTTPException(400, "La herramienta ya está en ese estado")

    holder = _one_line(body.holder)
    location = _one_line(body.location)
    note = (body.note or "").strip() or None
    if to_status in OUT_STATUSES and not holder:
        raise HTTPException(
            400, "Indica la obra, el cliente o la persona que tiene la herramienta"
        )
    if to_status == "baja" and not note:
        raise HTTPException(400, "Indica el motivo de la baja")

    kind = _movement_kind(from_status, to_status)
    tool.status = to_status
    keeps_holder = to_status in OUT_STATUSES or to_status in HOLDER_OPTIONAL_STATUSES
    if keeps_holder:
        tool.holder = holder
    elif to_status == "en_local":
        tool.holder = None
    # A baja keeps the last holder: "se perdió en la obra X" stays traceable.
    if location:
        tool.location = location
    if to_status == "baja":
        tool.retired_at = datetime.now(timezone.utc)
        tool.retired_reason = note[:300]
    elif from_status == "baja":
        tool.retired_at = None
        tool.retired_reason = None

    db.add(
        ToolMovement(
            tool_id=tool.id,
            kind=kind,
            from_status=from_status,
            to_status=to_status,
            holder=holder if keeps_holder else None,
            note=note,
            occurred_on=body.occurred_on or _business_today(),
            created_by=user.get("email") or None,
        )
    )
    db.commit()
    db.refresh(tool)
    return _ok(_detail(tool))


# ==================== Photos ====================


@router.post("/{tool_id}/images")
def upload_tool_image(
    tool_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload one photo; appended at the end (first photo = portada).

    Sync `def` on purpose: Pillow and boto3 are blocking work, so FastAPI runs
    this in the threadpool and keeps the event loop free.
    """
    tool = _get_tool_or_404(tool_id, db)
    if len(tool.images or []) >= MAX_IMAGES_PER_TOOL:
        raise HTTPException(400, f"Máximo {MAX_IMAGES_PER_TOOL} fotos por herramienta")
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            415, "Tipo de archivo no permitido: usa JPG, PNG, WEBP o GIF"
        )

    too_large = f"La foto pesa más de {MAX_IMAGE_SIZE // (1024 * 1024)} MB"
    if file.size is not None and file.size > MAX_IMAGE_SIZE:
        raise HTTPException(413, too_large)
    content = file.file.read(MAX_IMAGE_SIZE + 1)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(413, too_large)

    try:
        webp_bytes = process_product_image(content)
    except ImageTooManyPixelsError:
        raise HTTPException(
            400,
            f"La foto tiene demasiados pixeles (máximo {MAX_IMAGE_PIXELS // 1_000_000} MP)",
        )
    except Exception:
        raise HTTPException(400, "El archivo no es una imagen válida")

    key = f"tool-images/{tool_id}/{uuid4().hex[:12]}.webp"
    try:
        r2_upload(key, webp_bytes, "image/webp")  # default = private documents bucket
    except Exception as e:
        raise HTTPException(500, f"No se pudo guardar la foto: {e}")

    # JSON columns don't detect in-place mutation: assign a new list AND flag.
    tool.images = (tool.images or []) + [key]
    flag_modified(tool, "images")
    db.commit()
    return _ok({"key": key, "url": _image_url(key)})


@router.delete("/{tool_id}/images")
def delete_tool_image(
    tool_id: int,
    request: ImageDeleteRequest,
    db: Session = Depends(get_db),
):
    tool = _get_tool_or_404(tool_id, db)
    current = tool.images or []
    if request.key not in current:
        raise HTTPException(404, "La foto no pertenece a esta herramienta")
    tool.images = [k for k in current if k != request.key]
    flag_modified(tool, "images")
    db.commit()
    try:
        r2_delete(request.key)
    except Exception as e:
        print(f"Warning: failed to delete R2 object {request.key}: {e}")
    return _ok()


@router.put("/{tool_id}/images/order")
def reorder_tool_images(
    tool_id: int,
    request: ImageOrderRequest,
    db: Session = Depends(get_db),
):
    tool = _get_tool_or_404(tool_id, db)
    current = tool.images or []
    if sorted(request.keys) != sorted(current):
        raise HTTPException(400, "El orden debe incluir exactamente las fotos actuales")
    tool.images = list(request.keys)
    flag_modified(tool, "images")
    db.commit()
    return _ok({"images": tool.images})
