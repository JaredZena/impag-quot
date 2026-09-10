"""Import the HERRAMIENTAS tab (Operaciones_Comerciales_IMPAG) into tool / tool_movement.

Usage (from impag-quot/):
  venv/bin/python scripts/import_tools_from_sheet.py              # dry run: fetch + parse + report, no DB access
  venv/bin/python scripts/import_tools_from_sheet.py --json F     # dry run from a saved snapshot
  venv/bin/python scripts/import_tools_from_sheet.py --commit     # insert every NO not imported yet

Reads the tab with the same OAuth refresh-token flow as services/sales_sync.py
(GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_SHEETS_REFRESH_TOKEN)
and pulls cell background colours too, because the sheet marks
"HERRAMIENTA FUERA DEL LOCAL" by painting the row red. Each --commit run
snapshots the fetched rows to scripts/snapshots/ (audit trail, same as the
sales sync).

Idempotent, keyed on tool.sheet_no (the tab's NO column). Rows already
imported are SKIPPED, never overwritten: after the first import the app is
the source of truth, and edits made there must survive a re-run.

Safety: ALEMBIC_RUNNING=1 is set before `models` is imported so its
module-level create_all can never create tables. The tool tables must exist
(`alembic upgrade head`) before --commit. A dry run never opens a DB session.
"""

import os

os.environ.setdefault("ALEMBIC_RUNNING", "1")

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sales_sync import (
    get_sheets_access_token,
    parse_money,
    parse_spanish_date,
)

SPREADSHEET_ID = os.getenv(
    "OPERACIONES_SPREADSHEET_ID", "1U4DvVXSEkMUnM8V4NA5hvhdFWy7yBB4KfJWaraNbo1Q"
)
TAB_RE = re.compile(r"^\s*HERRAMIENTAS\s*$", re.IGNORECASE)
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
IMPORTED_BY = "import-herramientas-hoja"
FUERA_DEL_LOCAL = "#ff0000"  # legend colour: HERRAMIENTA FUERA DEL LOCAL
TIMEOUT = 60
BUSINESS_TZ = ZoneInfo("America/Mexico_City")  # the store's business date

KINDS = {"HERRAMIENTA": "herramienta", "CONSUMIBLE": "consumible"}
# Estado column: "1. Pendiente Entrega" / "2. En el Local" / "3. Con el Cliente"
ESTADO_BY_DIGIT = {"1": "pendiente_entrega", "2": "en_local", "3": "con_cliente"}
# COMENTARIO holds the supplier for most rows ("TORNILLO" = Ferretería El Tornillo).
SUPPLIER_RE = re.compile(r"^(TORNILLO|FERRETER[IÍ]A\b.*)$", re.IGNORECASE)


def _norm(text) -> str:
    text = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", str(text or ""))
        if not unicodedata.combining(ch)
    )
    return " ".join(text.upper().split())


# ==================== Fetch ====================


def _get(url: str, token: str) -> dict:
    last = None
    for attempt in range(5):
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT
        )
        if resp.status_code == 200:
            return resp.json()
        last = resp
        if resp.status_code < 500:
            break
        time.sleep(3 * (attempt + 1))  # Sheets API returns transient 503s
    raise RuntimeError(f"Sheets API HTTP {last.status_code}: {last.text[:300]}")


def _hex(cell: dict) -> str | None:
    color = (cell.get("effectiveFormat") or {}).get("backgroundColor")
    if not color:
        return None
    red, green, blue = (round(color.get(k, 0) * 255) for k in ("red", "green", "blue"))
    return f"#{red:02x}{green:02x}{blue:02x}"


def fetch_rows() -> tuple[str, list[list[dict]]]:
    """(tab title, rows of {"v": formatted value, "bg": "#rrggbb"})."""
    token = get_sheets_access_token()
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
    meta = _get(f"{base}?fields=sheets.properties.title", token)
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    tab = next((t for t in titles if TAB_RE.match(t)), None)
    if tab is None:
        raise RuntimeError(f"No HERRAMIENTAS tab found; tabs: {titles}")
    rng = quote(f"'{tab}'!A1:N1000", safe="")
    grid = _get(
        f"{base}?ranges={rng}&includeGridData=true"
        "&fields=sheets.data.rowData.values(formattedValue,effectiveFormat.backgroundColor)",
        token,
    )
    row_data = grid["sheets"][0]["data"][0].get("rowData", [])
    rows = [
        [{"v": c.get("formattedValue"), "bg": _hex(c)} for c in r.get("values", [])]
        for r in row_data
    ]
    return tab, rows


def snapshot(rows: list[list[dict]]) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"herramientas_{datetime.now(BUSINESS_TZ):%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False))
    return path


# ==================== Parse ====================


def _find_header(rows: list[list[dict]]) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(rows):
        names = [_norm(c.get("v")) for c in row]
        if "DESCRIPCION" in names and "NO" in names:
            cols: dict[str, int] = {}
            for j, name in enumerate(names):
                if name in (
                    "CARACTER",
                    "NO",
                    "DESCRIPCION",
                    "CANTIDAD",
                    "UNIDAD",
                    "IMPORTE",
                ):
                    cols[name] = j
                elif name.startswith("PRECIO"):
                    cols["PRECIO"] = j
                elif name.startswith("FECHA INICIO"):
                    cols["FECHA"] = j
                elif name.startswith("ESTADO"):
                    cols["ESTADO"] = j
                elif name.startswith("COMENTARIO"):
                    cols["COMENTARIO"] = j
            return i, cols
    raise ValueError("Header row (NO / DESCRIPCION) not found in the HERRAMIENTAS tab")


def _estado(raw: str) -> str:
    text = _norm(raw)
    match = re.match(r"^(\d)", text)
    if match and match.group(1) in ESTADO_BY_DIGIT:
        return ESTADO_BY_DIGIT[match.group(1)]
    if "CLIENTE" in text:
        return "con_cliente"
    if "PENDIENTE" in text:
        return "pendiente_entrega"
    return "en_local"


def parse_rows(rows: list[list[dict]]) -> tuple[list[dict], list[str]]:
    """Parse the tab into tool dicts. Returns (items, skipped-row notes)."""
    header_idx, cols = _find_header(rows)

    def cell(row, key):
        j = cols.get(key)
        if j is None or j >= len(row):
            return "", None
        c = row[j] or {}
        return " ".join(str(c.get("v") or "").split()), c.get("bg")

    items: list[dict] = []
    skipped: list[str] = []
    for offset, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        no_text, _ = cell(row, "NO")
        name, name_bg = cell(row, "DESCRIPCION")
        if not no_text.isdigit() or not name:
            if any((c or {}).get("v") for c in row):
                skipped.append(f"fila {offset}: sin NO numérico o sin descripción")
            continue

        qty_raw, _ = cell(row, "CANTIDAD")
        quantity = parse_money(qty_raw)
        notes: list[str] = []
        if quantity is None or quantity <= 0:
            notes.append(f"Cantidad en la hoja: '{qty_raw}'. Se registró 1.")
            quantity = Decimal(1)
        unit = cell(row, "UNIDAD")[0].upper() or "PIEZA"
        unit_cost = parse_money(cell(row, "PRECIO")[0])
        importe = parse_money(cell(row, "IMPORTE")[0])
        status = _estado(cell(row, "ESTADO")[0])

        supplier = None
        comment = cell(row, "COMENTARIO")[0]
        if comment and SUPPLIER_RE.match(comment):
            supplier = comment.upper()
        elif comment and comment != "-":
            notes.append(f"Comentario en la hoja: {comment}")
        if name_bg == FUERA_DEL_LOCAL:
            notes.append("En la hoja estaba marcada como herramienta fuera del local.")
        mismatch = (
            unit_cost is not None
            and importe is not None
            and abs(quantity * unit_cost - importe) > 1
        )
        if mismatch:
            notes.append(
                f"Revisar: en la hoja el importe era ${importe:,.2f} para "
                f"{quantity.normalize():f} {unit}."
            )

        items.append(
            {
                "sheet_no": int(no_text),
                "name": name,
                "kind": KINDS.get(_norm(cell(row, "CARACTER")[0]), "herramienta"),
                "quantity": quantity.quantize(Decimal("0.01")),
                "unit": unit,
                "unit_cost": (
                    unit_cost.quantize(Decimal("0.01"))
                    if unit_cost is not None
                    else None
                ),
                "purchase_date": parse_spanish_date(cell(row, "FECHA")[0]),
                "supplier_name": supplier,
                "status": status,
                # One store; the tab is its tool list.
                "location": "Nuevo Ideal" if status == "en_local" else None,
                # The API requires a holder for tools with a client; the sheet never says who.
                "holder": "Por confirmar" if status == "con_cliente" else None,
                "notes": "\n".join(notes) or None,
                "sheet_importe": importe,
                "flags": {
                    "fuera_del_local": name_bg == FUERA_DEL_LOCAL,
                    "importe_mismatch": mismatch,
                },
            }
        )
    return items, skipped


# ==================== Report + commit ====================


def report(items: list[dict], skipped: list[str]) -> None:
    by_status: dict[str, list[dict]] = {}
    for it in items:
        by_status.setdefault(it["status"], []).append(it)
    value = sum((it["quantity"] * (it["unit_cost"] or 0) for it in items), Decimal(0))
    sheet_total = sum((it["sheet_importe"] or 0 for it in items), Decimal(0))
    print(f"Filas de herramienta: {len(items)} | omitidas: {len(skipped)}")
    for status, rows in sorted(by_status.items()):
        sub = sum((r["quantity"] * (r["unit_cost"] or 0) for r in rows), Decimal(0))
        print(f"  {status:<18} {len(rows):>4}  ${sub:,.2f}")
    kinds = {
        k: sum(1 for it in items if it["kind"] == k)
        for k in ("herramienta", "consumible")
    }
    print(f"  tipos: {kinds}")
    print(f"Valor en la app (cantidad × costo): ${value:,.2f}")
    print(f"Suma de IMPORTE en la hoja:         ${sheet_total:,.2f}")
    for it in items:
        if it["flags"]["importe_mismatch"]:
            print(
                f"  REVISAR NO {it['sheet_no']}: {it['name']} → {it['notes'].splitlines()[-1]}"
            )
    red = [it["sheet_no"] for it in items if it["flags"]["fuera_del_local"]]
    print(f"Marcadas fuera del local (rojo): {red}")
    print(f"Con nota: {[it['sheet_no'] for it in items if it['notes']]}")
    for line in skipped:
        print(f"  omitida: {line}")


def commit(items: list[dict]) -> None:
    from models import SessionLocal, Tool, ToolMovement

    db = SessionLocal()
    try:
        existing = {
            n for (n,) in db.query(Tool.sheet_no).filter(Tool.sheet_no.isnot(None))
        }
        created = 0
        for it in items:
            if it["sheet_no"] in existing:
                continue
            fields = {
                k: v for k, v in it.items() if k not in ("sheet_importe", "flags")
            }
            tool = Tool(**fields, created_by=IMPORTED_BY)
            db.add(tool)
            db.flush()
            db.add(
                ToolMovement(
                    tool_id=tool.id,
                    kind="alta",
                    to_status=tool.status,
                    holder=tool.holder,
                    note=f"Importada de la hoja HERRAMIENTAS (NO {it['sheet_no']})",
                    occurred_on=it["purchase_date"] or datetime.now(BUSINESS_TZ).date(),
                    created_by=IMPORTED_BY,
                )
            )
            created += 1
        db.commit()  # all-or-nothing
        print(f"Importadas: {created} | ya existían: {len(items) - created}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", help="parse a saved snapshot instead of fetching")
    parser.add_argument("--commit", action="store_true", help="write to the database")
    args = parser.parse_args()

    if args.json:
        rows = json.loads(Path(args.json).read_text())
    else:
        tab, rows = fetch_rows()
        print(f"Pestaña '{tab}': {len(rows)} filas")
    items, skipped = parse_rows(rows)
    report(items, skipped)
    if args.commit:
        if not args.json:
            # Audit trail of exactly what was imported (dry runs leave no files).
            print(f"Snapshot: {snapshot(rows)}")
        commit(items)
    else:
        print("\nDry run: nada se escribió. Usa --commit para importar.")


if __name__ == "__main__":
    main()
