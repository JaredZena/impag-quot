"""
Bombeo solar sheet sync — mirrors Juan Daniel's "2. Concentrado de Precios_
Bombeo Solar Agricola" Google Sheet into the catalog.

The sheet is the source of truth; the team keeps editing it by hand.

Layout:
  - "PRECIOS VDE": the Villarreal División Equipos (VDE) price list, one
    section per part family, each with its own header row (Producto … Precio
    unitario MXN). Every part becomes a supplier_product of VDE.
  - "Villarreal Division Equipos": one block per kit (CODIGO, NO, DESCRIPCION,
    CANTIDAD, UNIDAD, PRECIO UNITARIO, IMPORTE) whose prices XLOOKUP the price
    list, closed by a "Subtotal" =SUM(IMPORTE range) cell. Some blocks add a
    "% Ganancia" and a "Precio De Venta" cell below the subtotal.
  - "Cotizador Bombeo Solar Agricola": one row per kit; its VDE cost cell is a
    formula pointing into a kit block (='Villarreal Division Equipos'!H22).
    Following that formula down to the =SUM(...) subtotal gives the kit's
    cost and its component list. A cell that points at a "Precio De Venta"
    (=+H132/(1-H133)) is followed to the subtotal it marks up, so the cost
    never carries the block's own margin.

Each kit becomes a KIT product sold on todoparaelcampo.com.mx at a fixed 30%
margin on the sale price (cost / (1 - 0.30)); its cost lives in a VDE
supplier_product linked to it. Ownership: rows written here are tagged
supplier_sku = "gsheet:vde:<code>" (parts) or "gsheet:kit:<pump code>"
(kits); the sync only ever touches its own rows and the products they link
to. "Vender en línea" is switched on once, when a kit product is created;
after that the admin owns it, except that a kit leaving the sheet is switched
off so it is never sold at a stale price.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy.orm import Session

from models import Product, ProductUnit, SupplierProduct
from services.sales_sync import get_sheets_access_token
from services.supplier_price_sync import (
    REF_RE,
    _col,
    _col_index,
    _decimal,
    _find_header,
    _get,
    _norm,
)

SPREADSHEET_ID = os.getenv(
    "BOMBEO_SPREADSHEET_ID", "1iajqKglj4-wbcnBKA97l042clI8VT6gqhWv4kFwMlmM"
)
PARTS_TAB = "PRECIOS VDE"
KITS_TAB = "Villarreal Division Equipos"
COTIZADOR_TAB = "Cotizador Bombeo Solar Agricola"

SUPPLIER_ID = 37  # Villarreal Division Equipos (VDE)
CATEGORY_ID = 7  # Equipos de Bombeo
KIT_MARGIN = Decimal("0.30")  # owner's call 2026-10-03 for online sale
PART_PREFIX = "gsheet:vde:"
KIT_PREFIX = "gsheet:kit:"
SYNC_USER = "sync:bombeo-sheet"

# "Vender en línea" for a newly created kit: pickup only (the store has no
# parcel/freight rates yet) and made to order from VDE.
NEW_KIT_ONLINE_SALE = {
    "enabled": True,
    "unit_label": "Kit",
    "delivery": ["recoger"],
    "min_qty": 1,
    "max_qty": 10,
    "stock_status": "backorder",
}

# Cotizador "PAQUETERIA OPCION 2" stages, located by header text.
SHIP_HEADERS = {
    "direct": "paqueteria texcoco/local",
    "stage1": "paqueteria texcoco/tg durang",
    "stage2": "tg dgo/manzanita dgo",
    "stage3": "manzanita dgo/manzanita ni",
    "stage4": "manzanita ni/local",
}

PUMP_RE = re.compile(r"^KOL", re.I)
SUM_RE = re.compile(r"SUM\(\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)\)", re.I)
CELL_RE = re.compile(r"(?<![A-Z!'])\$?([A-Z]{1,3})\$?(\d+)")

# Offsets from the IMPORTE column inside a kit block.
OFF_CODE, OFF_DESC, OFF_QTY, OFF_UNIT = -6, -4, -3, -2


@dataclass
class Part:
    code: str
    name: str
    description: str
    unit: str
    cost: Decimal  # MXN, the sheet's own conversion
    cost_usd: Decimal | None
    row: int


@dataclass
class KitLine:
    code: str
    description: str
    quantity: Decimal
    unit: str


@dataclass
class Kit:
    name: str
    cost: Decimal
    cotizador_row: int
    block_cell: str  # e.g. "H22"
    lines: list[KitLine]
    shipping: dict = field(default_factory=dict)

    @property
    def pump_code(self) -> str:
        """The kit's identity: its Kolosal pump (not always the first line)."""
        pump = next((ln.code for ln in self.lines if PUMP_RE.match(ln.code)), None)
        return pump or self.lines[0].code

    @property
    def key(self) -> str:
        return KIT_PREFIX + self.pump_code.upper()


def _cell(rows: list, r: int, c: int):
    return rows[r][c] if 0 <= r < len(rows) and 0 <= c < len(rows[r]) else ""


def _a1(col: int, row: int) -> str:
    letters, n = "", col + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row + 1}"


# ── Sheets access ─────────────────────────────────────────────────────────────


def fetch_workbook(token: str | None = None) -> dict:
    """{"values": {tab: unformatted rows}, "formulas": {tab: formula rows}}."""
    token = token or get_sheets_access_token()
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
    tabs = (PARTS_TAB, KITS_TAB, COTIZADOR_TAB)
    ranges = "&".join("ranges=" + quote(t) for t in tabs)
    out = {}
    for kind, render in (("values", "UNFORMATTED_VALUE"), ("formulas", "FORMULA")):
        payload = _get(
            f"{base}/values:batchGet?{ranges}&valueRenderOption={render}"
            "&dateTimeRenderOption=FORMATTED_STRING",
            token,
        )
        out[kind] = {
            t: vr.get("values", [])
            for t, vr in zip(tabs, payload.get("valueRanges", []))
        }
    return out


# ── Parsing ───────────────────────────────────────────────────────────────────


def parse_parts(rows: list) -> tuple[dict[str, Part], list[str]]:
    """Every priced part of the VDE price list, by code. Sections repeat the
    header row; the first occurrence of a code wins (as XLOOKUP does)."""
    found = _find_header(rows, ("producto", "precio unitario mxn"))
    if not found:
        return {}, [f"{PARTS_TAB}: header row not found"]
    h, cols = found
    c_code, c_name = _col(cols, "producto"), _col(cols, "nombre")
    c_desc, c_unit = _col(cols, "descripcion"), _col(cols, "unidad")
    c_mxn, c_usd = _col(cols, "precio unitario mxn"), _col(cols, "precio unitario usd")

    parts, skipped = {}, []
    for i in range(h + 1, len(rows)):
        code = str(_cell(rows, i, c_code)).strip()
        if not code or _norm(code) == "producto":
            continue
        cost = _decimal(_cell(rows, i, c_mxn))
        if not cost or cost <= 0:
            # Section titles ("KIt Empate") have no price at all; only a
            # priced-in-USD row missing its MXN price is worth reporting.
            if _decimal(_cell(rows, i, c_usd)):
                skipped.append(f"{PARTS_TAB}!{i + 1} {code} — sin precio MXN")
            continue
        if code.upper() in parts:
            skipped.append(f"{PARTS_TAB}!{i + 1} {code} — código repetido")
            continue
        parts[code.upper()] = Part(
            code=code,
            name=str(_cell(rows, i, c_name)).strip(),
            description=str(_cell(rows, i, c_desc)).strip(),
            unit=str(_cell(rows, i, c_unit)).strip(),
            cost=cost,
            cost_usd=_decimal(_cell(rows, i, c_usd)),
            row=i + 1,
        )
    return parts, skipped


def _subtotal_cell(formulas: list, col: int, row: int, depth: int = 0):
    """Follow a kit-block cell to its =SUM(...) subtotal: (col, row, sum match)."""
    f = str(_cell(formulas, row, col))
    m = SUM_RE.search(f)
    if m:
        return col, row, m
    ref = CELL_RE.search(f.lstrip("=+")) if f.startswith("=") else None
    if ref and depth < 3:
        return _subtotal_cell(
            formulas, _col_index(ref.group(1)), int(ref.group(2)) - 1, depth + 1
        )
    return None


def _kit_lines(values: list, imp_col: int, r0: int, r1: int) -> list[KitLine] | None:
    """Component rows of a kit block; None when the block layout is unexpected."""
    header_ok = any(
        _norm(_cell(values, r, imp_col + OFF_DESC)) == "descripcion"
        and _norm(_cell(values, r, imp_col + OFF_QTY)) == "cantidad"
        for r in range(max(0, r0 - 8), r0 + 1)
    )
    if not header_ok:
        return None
    lines = []
    for r in range(r0, r1 + 1):
        code = str(_cell(values, r, imp_col + OFF_CODE)).strip()
        qty = _decimal(_cell(values, r, imp_col + OFF_QTY))
        if not code or not qty or qty <= 0:
            continue
        lines.append(
            KitLine(
                code=code,
                description=str(_cell(values, r, imp_col + OFF_DESC)).strip(),
                quantity=qty,
                unit=str(_cell(values, r, imp_col + OFF_UNIT)).strip(),
            )
        )
    return lines


def parse_kits(workbook: dict) -> tuple[list[Kit], list[str]]:
    values = workbook["values"].get(COTIZADOR_TAB, [])
    formulas = workbook["formulas"].get(COTIZADOR_TAB, [])
    k_values = workbook["values"].get(KITS_TAB, [])
    k_formulas = workbook["formulas"].get(KITS_TAB, [])

    found = _find_header(values, ("material", "descripcion"))
    if not found:
        return [], [f"{COTIZADOR_TAB}: header row not found"]
    h, cols = found
    c_desc, c_cost = _col(cols, "descripcion"), _col(cols, "vde")
    ship_cols = {k: _col(cols, v) for k, v in SHIP_HEADERS.items()}
    if c_cost is None:
        return [], [f"{COTIZADOR_TAB}: no 'VDE' cost column"]

    kits, skipped = [], []
    for i in range(h + 1, len(values)):
        name = str(_cell(values, i, c_desc)).strip()
        if not name:
            continue
        cost_formula = str(_cell(formulas, i, c_cost))
        m = REF_RE.match(cost_formula)
        if not m or m.group(1).strip() != KITS_TAB:
            # The lower block repeats the kits at 18% (=R7); only rows that
            # point into the kit blocks are kits.
            if not cost_formula.strip():
                skipped.append(f"{COTIZADOR_TAB}!{i + 1} {name} — sin precio")
            continue
        target = _subtotal_cell(k_formulas, _col_index(m.group(2)), int(m.group(3)) - 1)
        if not target:
            skipped.append(f"{COTIZADOR_TAB}!{i + 1} {name} — sin subtotal =SUM")
            continue
        s_col, s_row, sm = target
        cost = _decimal(_cell(k_values, s_row, s_col))
        lines = _kit_lines(
            k_values,
            _col_index(sm.group(1)),
            int(sm.group(2)) - 1,
            int(sm.group(4)) - 1,
        )
        if not cost or cost <= 0 or not lines:
            skipped.append(
                f"{COTIZADOR_TAB}!{i + 1} {name} — bloque {_a1(s_col, s_row)} ilegible"
            )
            continue
        kits.append(
            Kit(
                name=name,
                cost=cost,
                cotizador_row=i + 1,
                block_cell=_a1(s_col, s_row),
                lines=lines,
                shipping={
                    k: (_decimal(_cell(values, i, j)) if j is not None else None)
                    or Decimal(0)
                    for k, j in ship_cols.items()
                },
            )
        )

    seen, unique = set(), []
    for k in kits:
        if k.key in seen:
            skipped.append(
                f"{COTIZADOR_TAB}!{k.cotizador_row} {k.name} — bomba repetida ({k.pump_code})"
            )
            continue
        seen.add(k.key)
        unique.append(k)
    return unique, skipped


# ── DB upsert ─────────────────────────────────────────────────────────────────


def kit_price(cost: Decimal) -> Decimal:
    return (cost / (Decimal(1) - KIT_MARGIN)).quantize(Decimal("0.01"))


def _kit_total_cost(k: Kit) -> Decimal:
    s = k.shipping
    return k.cost + s["stage1"] + s["stage2"] + s["stage3"] + s["stage4"]


def _kit_display_name(k: Kit) -> str:
    if _norm(k.name).startswith("kit"):
        return k.name[:255]
    return f"Kit Bombeo Solar de Superficie {k.name}"[:255]


def _kit_description(k: Kit) -> str:
    lines = "\n".join(
        f"• {ln.quantity.normalize():f} {ln.unit} — {ln.description} ({ln.code})"
        for ln in k.lines
    )
    return f"Kit armado con:\n{lines}"


def _kit_specs(k: Kit) -> dict:
    return {
        "fuente": "Concentrado de Precios_Bombeo Solar Agricola",
        "bomba": k.pump_code,
        "componentes": [
            {
                "codigo": ln.code,
                "descripcion": ln.description,
                "cantidad": float(ln.quantity),
                "unidad": ln.unit,
            }
            for ln in k.lines
        ],
    }


def _unique_sku(db: Session, base: str) -> str:
    sku, n = base[:100], 2
    while db.query(Product.id).filter(Product.sku == sku).first():
        sku = f"{base[:95]}-{n}"
        n += 1
    return sku


def _apply_part(sp: SupplierProduct, p: Part) -> None:
    sp.name = f"{p.code} {p.name}".strip()[:255]
    sp.description = p.description
    sp.unit = p.unit or sp.unit
    sp.cost = p.cost
    sp.currency = "MXN"
    usd = f" · USD {p.cost_usd}" if p.cost_usd else ""
    sp.notes = f"Concentrado Bombeo Solar · {PARTS_TAB}!{p.row}{usd}"
    if sp.archived_at is not None:
        sp.archived_at = None
        sp.is_active = True


def _apply_kit_sp(sp: SupplierProduct, k: Kit) -> None:
    s = k.shipping
    sp.name = _kit_display_name(k)
    sp.description = _kit_description(k)
    sp.unit = "KIT"
    sp.cost = k.cost
    sp.currency = "MXN"
    sp.specifications = _kit_specs(k)
    sp.shipping_method = "OCURRE"
    sp.shipping_cost_direct = s["direct"]
    sp.shipping_stage1_cost = s["stage1"]
    sp.shipping_stage2_cost = s["stage2"]
    sp.shipping_stage3_cost = s["stage3"]
    sp.shipping_stage4_cost = s["stage4"]
    sp.shipping_notes = "Paquetería opción 2 desde el Cotizador"
    sp.notes = (
        f"Concentrado Bombeo Solar · {COTIZADOR_TAB}!F{k.cotizador_row}"
        f" · {KITS_TAB}!{k.block_cell}"
    )
    if sp.archived_at is not None:
        sp.archived_at = None
        sp.is_active = True


def _apply_kit_product(product: Product, k: Kit, now: datetime) -> None:
    price = kit_price(_kit_total_cost(k))
    product.name = _kit_display_name(k)
    product.description = _kit_description(k)
    product.specifications = _kit_specs(k)
    # Prod's product_unit enum has no KIT value (models.py does), so a kit is
    # one PIEZA; customers see the "Kit" unit_label from online_sale.
    product.unit = ProductUnit.PIEZA
    product.default_margin = KIT_MARGIN
    product.price = price
    product.calculated_price = price
    product.calculated_price_updated_at = now


def _sync_parts(db, parts, now) -> dict:
    existing = {
        sp.supplier_sku: sp
        for sp in db.query(SupplierProduct).filter(
            SupplierProduct.supplier_id == SUPPLIER_ID,
            SupplierProduct.supplier_sku.like(PART_PREFIX + "%"),
        )
    }
    created, updated, unchanged, archived, guarded = [], [], 0, [], []
    live = set()
    for p in parts.values():
        key = PART_PREFIX + p.code.upper()
        live.add(key)
        sp = existing.get(key)
        if sp is None:
            sp = SupplierProduct(
                supplier_id=SUPPLIER_ID,
                supplier_sku=key,
                category_id=CATEGORY_ID,
                is_active=True,
            )
            _apply_part(sp, p)
            db.add(sp)
            created.append({"code": p.code, "cost": float(p.cost)})
            continue
        old, was_archived = sp.cost, sp.archived_at is not None
        _apply_part(sp, p)
        if old != p.cost or was_archived:
            updated.append(
                {"code": p.code, "old_cost": float(old or 0), "new_cost": float(p.cost)}
            )
        else:
            unchanged += 1

    gone = [
        sp for k, sp in existing.items() if k not in live and sp.archived_at is None
    ]
    live_before = sum(1 for sp in existing.values() if sp.archived_at is None)
    if gone and live_before and len(gone) > live_before / 2:
        guarded.append(
            f"{len(gone)}/{live_before} piezas desaparecieron — no se archivan"
        )
    else:
        for sp in gone:
            sp.archived_at, sp.is_active = now, False
            archived.append({"id": sp.id, "name": sp.name})
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "archived": archived,
        "archive_guarded": guarded,
    }


def _sync_kits(db, kits, now) -> dict:
    existing = {
        sp.supplier_sku: sp
        for sp in db.query(SupplierProduct).filter(
            SupplierProduct.supplier_id == SUPPLIER_ID,
            SupplierProduct.supplier_sku.like(KIT_PREFIX + "%"),
        )
    }
    created, updated, unchanged, archived = [], [], 0, []
    live = set()
    for k in kits:
        live.add(k.key)
        price = kit_price(_kit_total_cost(k))
        sp = existing.get(k.key)
        product = db.get(Product, sp.product_id) if sp and sp.product_id else None
        if product is None:
            product = Product(
                sku=_unique_sku(db, f"KIT-{k.pump_code.upper()}"),
                category_id=CATEGORY_ID,
                iva=True,
                is_active=True,
                stock=0,
                online_sale={
                    **NEW_KIT_ONLINE_SALE,
                    "updated_by": SYNC_USER,
                    "updated_at": now.isoformat(),
                },
            )
            _apply_kit_product(product, k, now)
            db.add(product)
            db.flush()
            created.append(
                {
                    "kit": k.name,
                    "pump": k.pump_code,
                    "sku": product.sku,
                    "product_id": product.id,
                    "cost": float(k.cost),
                    "price": float(price),
                    "components": len(k.lines),
                }
            )
        else:
            old_price = product.price
            _apply_kit_product(product, k, now)
            if old_price != price or (sp and sp.archived_at is not None):
                updated.append(
                    {
                        "kit": k.name,
                        "product_id": product.id,
                        "old_price": float(old_price or 0),
                        "new_price": float(price),
                    }
                )
            else:
                unchanged += 1
        if sp is None:
            sp = SupplierProduct(
                supplier_id=SUPPLIER_ID,
                supplier_sku=k.key,
                category_id=CATEGORY_ID,
                is_active=True,
            )
            db.add(sp)
        sp.product_id = product.id
        _apply_kit_sp(sp, k)

    # A kit that left the sheet: archive its cost and stop selling it online.
    for key, sp in existing.items():
        if key in live or sp.archived_at is not None:
            continue
        sp.archived_at, sp.is_active = now, False
        product = db.get(Product, sp.product_id) if sp.product_id else None
        if product is not None and isinstance(product.online_sale, dict):
            product.online_sale = {
                **product.online_sale,
                "enabled": False,
                "updated_by": SYNC_USER,
                "updated_at": now.isoformat(),
            }
        archived.append({"kit": sp.name, "product_id": sp.product_id})
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "archived": archived,
    }


def sync_bombeo_sheet(db: Session, dry_run: bool = True, workbook=None) -> dict:
    """Upsert the VDE parts and the kits. dry_run computes the full diff and
    rolls back. `workbook` = fetch_workbook() output, for tests/snapshots."""
    workbook = workbook or fetch_workbook()
    parts, skipped = parse_parts(workbook["values"].get(PARTS_TAB, []))
    kits, k_skipped = parse_kits(workbook)
    skipped += k_skipped
    now = datetime.now(timezone.utc)
    try:
        part_report = _sync_parts(db, parts, now)
        kit_report = _sync_kits(db, kits, now)
        if dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "dry_run": dry_run,
        "parts_parsed": len(parts),
        "kits_parsed": len(kits),
        "parts": part_report,
        "kits": kit_report,
        "skipped": skipped,
    }
