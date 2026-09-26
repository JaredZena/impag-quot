"""
Daily sync of the HERRAMIENTAS tab (Operaciones_Comerciales_IMPAG) into
tool / tool_movement.

The team keeps working in the sheet, so the SHEET is the source of truth for
every tool it lists (keyed on tool.sheet_no = the tab's NO column):
  - new NO            -> tool created + "alta" movement
  - status changed    -> status/holder/location updated + a movement row with
                         the same kind the app would record (salida, regreso,
                         recibida, ...), so the lifecycle trail stays complete
  - other fields      -> name, kind, quantity, unit, cost, date, supplier
                         overwritten from the sheet
  - NO gone from sheet -> reported only, never retired automatically
Tools created in the app (sheet_no NULL), notes and images are never touched.
Fetch + parse are shared with scripts/import_tools_from_sheet.py.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from models import Tool, ToolMovement
from routes.tools import HOLDER_OPTIONAL_STATUSES, OUT_STATUSES, _movement_kind
from scripts.import_tools_from_sheet import BUSINESS_TZ, fetch_rows, parse_rows

SYNCED_BY = "sync-hoja-herramientas"
SYNC_FIELDS = (
    "name",
    "kind",
    "quantity",
    "unit",
    "unit_cost",
    "purchase_date",
    "supplier_name",
)


def _status_fields(item: dict, tool: Tool) -> dict:
    """status/holder/location for the sheet's status, keeping an app-entered
    holder when the tool is still out."""
    status = item["status"]
    if status in OUT_STATUSES:
        holder = (
            tool.holder
            if tool.status in OUT_STATUSES and tool.holder
            else item["holder"]
        )
    elif status in HOLDER_OPTIONAL_STATUSES:
        holder = tool.holder
    else:
        holder = None
    return {"status": status, "holder": holder, "location": item["location"]}


def sync_tools(db: Session, dry_run: bool = True, rows=None) -> dict:
    """Apply the sheet to the tool table. `rows` = pre-fetched grid (tests)."""
    if rows is None:
        _, rows = fetch_rows()
    items, skipped = parse_rows(rows)
    if not items:
        raise RuntimeError("HERRAMIENTAS: 0 rows parsed — refusing to sync")

    today = datetime.now(BUSINESS_TZ).date()
    by_no = {t.sheet_no: t for t in db.query(Tool).filter(Tool.sheet_no.isnot(None))}
    created, status_changes, updated = [], [], []
    try:
        for it in items:
            tool = by_no.get(it["sheet_no"])
            if tool is None:
                fields = {
                    k: v for k, v in it.items() if k not in ("sheet_importe", "flags")
                }
                created.append(
                    {"no": it["sheet_no"], "name": it["name"], "status": it["status"]}
                )
                if not dry_run:
                    tool = Tool(**fields, created_by=SYNCED_BY)
                    db.add(tool)
                    db.flush()
                    db.add(
                        ToolMovement(
                            tool_id=tool.id,
                            kind="alta",
                            to_status=tool.status,
                            holder=tool.holder,
                            note=f"Nueva en la hoja HERRAMIENTAS (NO {it['sheet_no']})",
                            occurred_on=it["purchase_date"] or today,
                            created_by=SYNCED_BY,
                        )
                    )
                continue

            if tool.status == "baja":
                continue  # retired in the app; a stale sheet row can't revive it

            changed = [f for f in SYNC_FIELDS if getattr(tool, f) != it[f]]
            if changed:
                updated.append(
                    {"no": it["sheet_no"], "name": it["name"], "fields": changed}
                )
                for f in changed:
                    setattr(tool, f, it[f])

            if tool.status != it["status"]:
                before = tool.status
                status_changes.append(
                    {
                        "no": it["sheet_no"],
                        "name": it["name"],
                        "from": before,
                        "to": it["status"],
                    }
                )
                for f, v in _status_fields(it, tool).items():
                    setattr(tool, f, v)
                db.add(
                    ToolMovement(
                        tool_id=tool.id,
                        kind=_movement_kind(before, it["status"]),
                        from_status=before,
                        to_status=it["status"],
                        holder=tool.holder,
                        note="Cambio registrado en la hoja HERRAMIENTAS",
                        occurred_on=today,
                        created_by=SYNCED_BY,
                    )
                )

        sheet_nos = {it["sheet_no"] for it in items}
        missing = [
            {"no": no, "name": t.name}
            for no, t in sorted(by_no.items())
            if no not in sheet_nos and t.status != "baja"
        ]
        if dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "dry_run": dry_run,
        "rows_parsed": len(items),
        "created": created,
        "status_changes": status_changes,
        "updated": updated,
        "missing_from_sheet": missing,
        "skipped": skipped,
    }
