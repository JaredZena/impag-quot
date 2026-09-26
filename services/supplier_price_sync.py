"""
Supplier price sync — mirrors Juan Daniel's "Concentrado de Precios" Google
Sheet (invernaderos y cubiertas agrícolas) into `supplier_product`.

The sheet is the source of truth for these suppliers' costs; the team updates
it by hand as new supplier quotes arrive.

Layout:
  - One tab per supplier (POPUSA, Insumo Forestal, HTA Invernaderos, …). The
    header row is found dynamically (first row with a "Material" cell and a
    "Precio U" cell); columns are located by header name, since every tab
    puts them at a different offset.
  - The "Cotizador" tab is the comparison sheet. Its per-supplier price cells
    are formulas pointing back at a supplier-tab row (='ICUSA Plasticos'!H5),
    and each Cotizador row carries that product's shipping. Following those
    formulas is how shipping lands on the right supplier row — no fuzzy text
    matching.

Shipping: the team prices on "PAQUETERÍA OPCIÓN 2" (the worst case:
Texcoco→TG Dgo + TG Dgo→Ditra + Ditra→local), so rows linked from the
Cotizador get shipping_method=OCURRE with those three stages;
shipping_cost_direct keeps option 1 for reference.

Ownership: rows written here are tagged supplier_sku = "gsheet:<hash>" where
the hash covers (tab, material, descripción, unidad). The sync only ever
touches its own rows. Legacy rows for the same suppliers (earlier quote
imports) are left alone. A sheet row that disappears is soft-archived, unless
its tab shrank by more than half (a broken tab must not wipe a supplier).
"""

import hashlib
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import requests
from sqlalchemy.orm import Session

from models import Supplier, SupplierProduct
from services.sales_sync import SHEETS_TIMEOUT_SECONDS, get_sheets_access_token

SPREADSHEET_ID = os.getenv(
    "CONCENTRADO_SPREADSHEET_ID", "1VpRZPZVWJWqv-18-Y1rKfRBU3yb5mXkco7mNNFyHJLM"
)
COTIZADOR_TAB = "Cotizador Invernaderos y Cubiertas Agricolas"
SKU_PREFIX = "gsheet:"

# Tab name -> existing supplier id. Tabs missing here get a supplier created
# (by tab name) on apply. Ids chosen as the canonical row among duplicates.
TAB_SUPPLIER_IDS = {
    "POPUSA": 2,  # Grupo Industrial Popusa
    "Insumo Forestal": 99,
    "Hydroenviroment": 101,
    "HTA Invernaderos": 11,  # HTA de Mexico
    "ICUSA Plasticos": 96,
    "Prosagri": 97,
    "Hanlob": 98,
    "TOP SIRG": 15,  # MATERIALES Y RECURSOS TOPSIRG
    "Grupo Textiles": 104,
    "Tornillo": 81,  # El Tornillo
}

# Cotizador shipping columns (0-based), located by header text.
SHIP_HEADERS = {
    "direct": "paqueteria texcoco/local",
    "stage1": "paqueteria texcoco/tg dgo",
    "stage2": "tg dgo/ditra",
    "stage3": "ditra/local",
}

REF_RE = re.compile(r"^=\+?'?([^'!]+?)'?!\$?([A-Z]+)\$?(\d+)\s*$")


def _norm(text) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return Decimal(cleaned).quantize(Decimal("0.01")) if cleaned else None
    except InvalidOperation:
        return None


@dataclass
class SheetRow:
    tab: str
    row: int  # 1-based sheet row
    material: str
    description: str
    unit: str
    cost: Decimal
    updated_label: str = ""
    display_name: str = ""
    shipping: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        raw = "|".join(
            _norm(x) for x in (self.tab, self.material, self.description, self.unit)
        )
        return SKU_PREFIX + hashlib.sha1(raw.encode()).hexdigest()[:16]


# ── Sheets access ─────────────────────────────────────────────────────────────


def _get(url: str, token: str) -> dict:
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=SHEETS_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Sheets fetch failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    return resp.json() or {}


def fetch_workbook(token: str | None = None) -> tuple[dict[str, list], list]:
    """({tab: unformatted values}, cotizador formulas)."""
    token = token or get_sheets_access_token()
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
    meta = _get(f"{base}?fields=sheets.properties.title", token)
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    ranges = "&".join("ranges=" + quote(t) for t in titles)
    values = _get(
        f"{base}/values:batchGet?{ranges}&valueRenderOption=UNFORMATTED_VALUE"
        "&dateTimeRenderOption=FORMATTED_STRING",
        token,
    )
    tabs = {
        t: vr.get("values", []) for t, vr in zip(titles, values.get("valueRanges", []))
    }
    formulas = _get(
        f"{base}/values/{quote(COTIZADOR_TAB)}?valueRenderOption=FORMULA", token
    ).get("values", [])
    return tabs, formulas


# ── Parsing ───────────────────────────────────────────────────────────────────


def _find_header(
    rows: list, required: tuple[str, ...]
) -> tuple[int, dict[str, int]] | None:
    for i, row in enumerate(rows[:15]):
        cols = {_norm(c): j for j, c in enumerate(row) if str(c).strip()}
        if all(any(h.startswith(r) for h in cols) for r in required):
            return i, cols
    return None


def _col(cols: dict[str, int], prefix: str) -> int | None:
    return next((j for h, j in cols.items() if h.startswith(prefix)), None)


def parse_supplier_tab(tab: str, rows: list) -> tuple[list[SheetRow], list[str]]:
    """Rows with a positive unit price; everything else goes to `skipped`."""
    found = _find_header(rows, ("material", "precio u"))
    if not found:
        return [], [f"{tab}: header row not found"]
    h, cols = found
    c_mat, c_desc = _col(cols, "material"), _col(cols, "descripcion")
    c_unit, c_price = _col(cols, "unidad"), _col(cols, "precio u")
    c_date = _col(cols, "ultima actualizacion")

    def cell(row, j):
        return row[j] if j is not None and j < len(row) else ""

    out, skipped = [], []
    for i in range(h + 1, len(rows)):
        row = rows[i]
        material = str(cell(row, c_mat)).strip()
        desc = str(cell(row, c_desc)).strip()
        if not material and not desc:
            continue
        cost = _decimal(cell(row, c_price))
        if not cost or cost <= 0:
            skipped.append(f"{tab}!{i + 1} {material} — sin precio")
            continue
        out.append(
            SheetRow(
                tab=tab,
                row=i + 1,
                material=material,
                description=desc,
                unit=str(cell(row, c_unit)).strip(),
                cost=cost,
                updated_label=str(cell(row, c_date)).strip(),
            )
        )
    return out, skipped


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def link_cotizador(values: list, formulas: list) -> dict[tuple[str, int], dict]:
    """(tab, row) -> {"name": Cotizador material, "shipping": {...}} for every
    supplier-tab row the Cotizador references. When several Cotizador rows
    point at one supplier row, the costliest shipping wins (worst case)."""
    found = _find_header(values, ("material", "descripcion"))
    if not found:
        return {}
    h, cols = found
    c_mat = _col(cols, "material")
    ship_cols = {k: _col(cols, v) for k, v in SHIP_HEADERS.items()}
    links: dict[tuple[str, int], dict] = {}
    for i in range(h + 1, min(len(values), len(formulas))):
        vrow, frow = values[i], formulas[i]
        name = (
            str(vrow[c_mat]).strip() if c_mat is not None and c_mat < len(vrow) else ""
        )
        ship = {
            k: (_decimal(vrow[j]) if j is not None and j < len(vrow) else None)
            or Decimal(0)
            for k, j in ship_cols.items()
        }
        for cell in frow:
            m = REF_RE.match(str(cell))
            if not m:
                continue
            key = (m.group(1).strip(), int(m.group(3)))
            total = ship["stage1"] + ship["stage2"] + ship["stage3"]
            prev = links.get(key)
            prev_total = (
                prev["shipping"]["stage1"]
                + prev["shipping"]["stage2"]
                + prev["shipping"]["stage3"]
                if prev
                else Decimal(-1)
            )
            if total > prev_total:
                links[key] = {"name": name, "shipping": ship}
    return links


def parse_workbook(
    tabs: dict[str, list], formulas: list
) -> tuple[list[SheetRow], list[str]]:
    links = link_cotizador(tabs.get(COTIZADOR_TAB, []), formulas)
    rows, skipped = [], []
    for tab, values in tabs.items():
        if tab == COTIZADOR_TAB:
            continue
        parsed, sk = parse_supplier_tab(tab, values)
        skipped += sk
        for r in parsed:
            link = links.get((tab, r.row))
            if link:
                r.display_name = link["name"]
                r.shipping = link["shipping"]
            rows.append(r)
    # Identical (tab, material, desc, unit) rows would share a key; keep the first.
    seen, unique = set(), []
    for r in rows:
        if r.key in seen:
            skipped.append(f"{r.tab}!{r.row} {r.material} — fila duplicada")
            continue
        seen.add(r.key)
        unique.append(r)
    return unique, skipped


# ── DB upsert ─────────────────────────────────────────────────────────────────


def _name_for(r: SheetRow) -> str:
    if r.display_name:
        return r.display_name[:255]
    if _norm(r.description).startswith(_norm(r.material)):
        return r.description[:255]
    return f"{r.material} {r.description}".strip()[:255]


def _supplier_for(db: Session, tab: str, create: bool) -> Supplier | None:
    sid = TAB_SUPPLIER_IDS.get(tab)
    if sid:
        return db.get(Supplier, sid)
    existing = (
        db.query(Supplier)
        .filter(Supplier.name.ilike(tab), Supplier.archived_at.is_(None))
        .first()
    )
    if existing or not create:
        return existing
    supplier = Supplier(
        name=tab, description="Creado por sync de Concentrado de Precios"
    )
    db.add(supplier)
    db.flush()
    return supplier


def _apply_fields(sp: SupplierProduct, r: SheetRow) -> None:
    sp.name = _name_for(r)
    sp.description = r.description
    sp.unit = r.unit or sp.unit
    sp.cost = r.cost
    sp.currency = "MXN"
    note = f"Concentrado de Precios · {r.tab}!{r.row}"
    if r.updated_label:
        note += f" · actualizado {r.updated_label}"
    sp.notes = note
    if r.shipping:
        sp.shipping_method = "OCURRE"
        sp.shipping_stage1_cost = r.shipping["stage1"]
        sp.shipping_stage2_cost = r.shipping["stage2"]
        sp.shipping_stage3_cost = r.shipping["stage3"]
        sp.shipping_stage4_cost = Decimal(0)
        sp.shipping_cost_direct = r.shipping["direct"]
        sp.shipping_notes = "Paquetería opción 2 (peor escenario) desde el Cotizador"
    if sp.archived_at is not None:
        sp.archived_at = None
        sp.is_active = True


def sync_supplier_prices(db: Session, dry_run: bool = True, workbook=None) -> dict:
    """Upsert sheet rows into supplier_product. dry_run computes the full diff
    and rolls back. `workbook` = (tabs, formulas) for tests/snapshots."""
    tabs, formulas = workbook or fetch_workbook()
    rows, skipped = parse_workbook(tabs, formulas)

    by_tab: dict[str, list[SheetRow]] = defaultdict(list)
    for r in rows:
        by_tab[r.tab].append(r)

    created, updated, unchanged, archived, guarded = [], [], 0, [], []
    try:
        for tab, tab_rows in by_tab.items():
            supplier = _supplier_for(db, tab, create=not dry_run)
            sid = supplier.id if supplier else None
            existing = {}
            if sid:
                existing = {
                    sp.supplier_sku: sp
                    for sp in db.query(SupplierProduct).filter(
                        SupplierProduct.supplier_id == sid,
                        SupplierProduct.supplier_sku.like(SKU_PREFIX + "%"),
                    )
                }
            live_keys = set()
            for r in tab_rows:
                live_keys.add(r.key)
                sp = existing.get(r.key)
                if sp is None:
                    created.append(
                        {
                            "tab": tab,
                            "row": r.row,
                            "name": _name_for(r),
                            "cost": float(r.cost),
                            "shipping": float(
                                sum(
                                    r.shipping.get(k, 0)
                                    for k in ("stage1", "stage2", "stage3")
                                )
                            ),
                        }
                    )
                    if sid:
                        sp = SupplierProduct(
                            supplier_id=sid, supplier_sku=r.key, is_active=True
                        )
                        _apply_fields(sp, r)
                        db.add(sp)
                    continue
                old_cost = sp.cost
                # total_shipping_cost is a DB-generated column, not mapped.
                old_ship = sum(
                    (
                        sp.shipping_stage1_cost or 0,
                        sp.shipping_stage2_cost or 0,
                        sp.shipping_stage3_cost or 0,
                    ),
                    Decimal(0),
                )
                was_archived = sp.archived_at is not None
                _apply_fields(sp, r)
                new_ship = (
                    sum(r.shipping.get(k, 0) for k in ("stage1", "stage2", "stage3"))
                    if r.shipping
                    else old_ship
                )
                if (
                    old_cost != r.cost
                    or was_archived
                    or (r.shipping and old_ship != new_ship)
                ):
                    updated.append(
                        {
                            "tab": tab,
                            "row": r.row,
                            "id": sp.id,
                            "name": sp.name,
                            "old_cost": float(old_cost or 0),
                            "new_cost": float(r.cost),
                            "old_shipping": float(old_ship or 0),
                            "new_shipping": float(new_ship or 0),
                        }
                    )
                else:
                    unchanged += 1

            gone = [
                sp
                for k, sp in existing.items()
                if k not in live_keys and sp.archived_at is None
            ]
            live_before = sum(1 for sp in existing.values() if sp.archived_at is None)
            if gone and live_before and len(gone) > live_before / 2:
                guarded.append(
                    f"{tab}: {len(gone)}/{live_before} filas desaparecieron — no se archivan"
                )
                continue
            now = datetime.now(timezone.utc)
            for sp in gone:
                sp.archived_at = now
                sp.is_active = False
                archived.append({"tab": tab, "id": sp.id, "name": sp.name})

        if dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "dry_run": dry_run,
        "rows_parsed": len(rows),
        "linked_to_cotizador": sum(1 for r in rows if r.shipping),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "archived": archived,
        "archive_guarded": guarded,
        "skipped": skipped,
    }
