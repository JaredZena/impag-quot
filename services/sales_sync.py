"""
Sales ledger sync — mirrors the VENTAS Google Sheet into the `sale` table.

Operational snapshot only, NOT accounting books ("instantánea operativa — no
libros contables").

Flow (sync_all):
  1. OAuth refresh-token exchange -> access token (get_sheets_access_token)
  2. GET each tab's raw values (fetch_tab) and snapshot them to
     scripts/snapshots/ventas_{TAB}.json for auditability
  3. parse_tab: year-aware column mapping, robust normalizers; bad rows are
     QUARANTINED (quarantined=True + reason), never dropped and never raised
  4. upsert by (sheet_tab, source_row); exact-match customer linking

Tab layouts (0-based column indexes; header row is found dynamically as the
first row containing a "CLIENTE" cell):
  VENTAS 2026 / VENTAS 2025:
    C=2 CLIENTE, D=3 FECHA ("miércoles, 19 de agosto de 2026"), E=4 Mes,
    F=5 DESCRIPCION, G=6 UNIDAD, H=7 CANTIDAD, I=8 PRECIO UNITARIO,
    J=9 IMPORTE, K=10 Concepto, L=11 FORMA DE PAGO, M=12 LUGAR DE ENTREGA,
    N=13 REFERENCIA, O=14 NOTA DE COMPRA, P=15 ESTADO ENTREGA,
    Q=16 REQUIERE REGISTRO P/FACTURA, R=17 REGISTRADA
  VENTAS 2024 (SHIFTED, plus repeated header rows inline in the data):
    C=2 FECHA ("26/12/2024"), D=3 Mes, E=4 DESCRIPCION, F=5 UNIDAD,
    G=6 CANTIDAD, H=7 PRECIO UNITARIO, I=8 IMPORTE, J=9 CLIENTE,
    K=10 FORMA DE PAGO, L=11 LUGAR DE ENTREGA, M=12 REFERENCIA,
    N=13 NOTA DE COMPRA, O=14 FECHA(dup, ignored), P=15 ESTADO ENTREGA,
    Q=16 REQUIERE FACTURA, R=17 REGISTRADA  (no Concepto column)
"""

import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

import requests
from sqlalchemy.orm import Session

from models import Customer, Sale

SHEETS_TIMEOUT_SECONDS = 60
TABS = ["VENTAS 2026", "VENTAS 2025", "VENTAS 2024"]

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "snapshots"

# 2025/2026 layout (CLIENTE first, at column C)
COLUMNS_MODERN = {
    "customer_name": 2,
    "sale_date": 3,
    "month_label": 4,
    "description": 5,
    "unit": 6,
    "quantity": 7,
    "unit_price": 8,
    "amount": 9,
    "concept": 10,
    "payment_method": 11,
    "delivery_place": 12,
    "reference": 13,
    "folio": 14,
    "delivery_status": 15,
    "requires_invoice": 16,
    "registered": 17,
}

# 2024 layout (FECHA first, CLIENTE at column J, no Concepto)
COLUMNS_2024 = {
    "sale_date": 2,
    "month_label": 3,
    "description": 4,
    "unit": 5,
    "quantity": 6,
    "unit_price": 7,
    "amount": 8,
    "customer_name": 9,
    "payment_method": 10,
    "delivery_place": 11,
    "reference": 12,
    "folio": 13,
    # column 14 is a duplicate FECHA — ignored
    "delivery_status": 15,
    "requires_invoice": 16,
    "registered": 17,
}


# ── Google Sheets access ─────────────────────────────────────────────────────


def get_sheets_access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_SHEETS_REFRESH_TOKEN")
    missing = [
        name
        for name, value in (
            ("GOOGLE_OAUTH_CLIENT_ID", client_id),
            ("GOOGLE_OAUTH_CLIENT_SECRET", client_secret),
            ("GOOGLE_SHEETS_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Google Sheets sync is not configured — missing env vars: "
            + ", ".join(missing)
        )

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=SHEETS_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Google OAuth token refresh failed (HTTP {resp.status_code}): "
            f"{resp.text[:300]}"
        )
    token = (resp.json() or {}).get("access_token")
    if not token:
        raise RuntimeError("Google OAuth token refresh returned no access_token")
    return token


def fetch_tab(tab: str, access_token: str | None = None) -> list[list[str]]:
    """Raw values (list of rows) for one tab of the VENTAS spreadsheet."""
    spreadsheet_id = os.getenv("VENTAS_SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("VENTAS_SPREADSHEET_ID env var is not set")
    if access_token is None:
        access_token = get_sheets_access_token()

    a1_range = quote(f"{tab}!A1:Z3000", safe="")
    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        f"/values/{a1_range}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=SHEETS_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Sheets fetch for tab {tab!r} failed (HTTP {resp.status_code}): "
            f"{resp.text[:300]}"
        )
    return (resp.json() or {}).get("values", [])


def snapshot_tab(tab: str, values: list[list[str]]) -> Path:
    """Write the raw fetched values to scripts/snapshots/ (audit trail)."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"ventas_{tab.replace(' ', '_')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False)
    return path


# ── Cell parsers / normalizers ───────────────────────────────────────────────

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_SLASH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_LONG_DATE_RE = re.compile(r"(\d{1,2})\s+de\s+([a-záéíóúüñ]+)\s+de\s+(\d+)", re.I)
_FOLIO_TOKEN_RE = re.compile(r"\d{6}[A-Z]{2,4}")
_NA_VALUES = {"", "N/A", "NA", "-", "S/N"}

PAYMENT_METHODS = {"efectivo", "transferencia", "deposito", "terminal", "shopify"}


def _strip_accents(text: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def _cell(row: list, idx: int | None) -> str:
    """Whitespace-collapsed cell text; '' when the column is absent/short."""
    if idx is None or idx >= len(row) or row[idx] is None:
        return ""
    return re.sub(r"\s+", " ", str(row[idx])).strip()


def parse_spanish_date(raw: str, month_hint: int | None = None) -> date | None:
    """'miércoles, 19 de agosto de 2026' or '26/12/2024' -> date, else None.

    Typo years seen in the sheet (e.g. '20206', '20226') are rejected so the
    row gets quarantined for a human instead of storing a garbage date.

    Slash dates in the 2024 tab MIX dd/mm/yyyy ('26/12/2024') with US-style
    mm/dd/yyyy ('10/16/2024', '4/12/2024'). Disambiguation: whichever part is
    >12 must be the day; when both could be the month, the tab's "Mes" column
    (month_hint) decides; dd/mm (the sheet's es-MX locale) is the fallback.
    """
    if raw is None:
        return None
    s = re.sub(r"\s+", " ", str(raw)).strip().lower()
    if not s:
        return None

    m = _SLASH_DATE_RE.match(s)
    if m:
        first, second, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not 2000 <= year <= 2100:
            return None
        if first > 12:
            day, month = first, second  # dd/mm
        elif second > 12:
            day, month = second, first  # mm/dd
        elif month_hint and first == month_hint and second != month_hint:
            day, month = second, first  # mm/dd, month matches the Mes column
        else:
            day, month = first, second  # dd/mm default
        try:
            return date(year, month, day)
        except ValueError:
            return None

    m = _LONG_DATE_RE.search(s)
    if m:
        day = int(m.group(1))
        month = SPANISH_MONTHS.get(_strip_accents(m.group(2)))
        year = int(m.group(3))
        if month is None or not 2000 <= year <= 2100:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def parse_money(raw: str) -> Decimal | None:
    """'$1,250.00' / ' $ 110.00 ' / '(1,250.00)' -> Decimal, else None."""
    if raw is None:
        return None
    s = str(raw).replace("$", "").replace(",", "").replace(" ", "").strip()
    if s.upper() in _NA_VALUES:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_qty(raw: str) -> Decimal | None:
    """'300.00' / '10' -> Decimal, else None."""
    return parse_money(raw)


def normalize_payment_method(raw: str) -> str | None:
    """Efectivo/transferencia/DEPOSITO/... -> canonical lowercase, else None."""
    if not raw:
        return None
    s = _strip_accents(str(raw)).strip().lower()
    return s if s in PAYMENT_METHODS else None


def normalize_delivery_status(raw: str) -> str | None:
    """ENTREGADO/Entregado -> 'entregado'; PENDIENTE-ish ('PAGOS PENDIENTES.')
    -> 'pendiente'; anything else -> None."""
    if not raw:
        return None
    s = _strip_accents(str(raw)).upper()
    if "ENTREGADO" in s:
        return "entregado"
    if "PENDIENTE" in s:
        return "pendiente"
    return None


def parse_si_no(raw: str) -> bool | None:
    """SI/Si/si -> True, NO/No/no -> False, N/A/blank/other -> None."""
    if raw is None:
        return None
    s = _strip_accents(str(raw)).strip().upper()
    if s in _NA_VALUES:
        return None
    if s in {"SI", "S", "YES"}:
        return True
    if s in {"NO", "N"}:
        return False
    return None


def normalize_folio(raw: str) -> str | None:
    """NOTA DE COMPRA -> canonical folio.

    'NOT-IMPAG-121225DGO-RAFAEL IBARRA (PDF)' -> '121225DGO'
    '080826DGO'                               -> '080826DGO'
    Strategy: strip + uppercase, drop the NOT-IMPAG- prefix, then extract the
    ddmmyy+branch token; unrecognized non-empty values are kept truncated so
    nothing is silently lost.
    """
    if raw is None:
        return None
    s = re.sub(r"\s+", " ", str(raw)).strip().upper()
    if s in _NA_VALUES:
        return None
    if s.startswith("NOT-IMPAG-"):
        s = s[len("NOT-IMPAG-") :]
    m = _FOLIO_TOKEN_RE.search(s)
    if m:
        return m.group(0)
    m = re.search(r"\d{6}", s)
    if m:
        return m.group(0)
    return s[:40]


def normalize_customer_name(name: str) -> str:
    """Matching key used against Customer.name_normalized (exact match only)."""
    return re.sub(r"\s+", " ", name).strip().lower()


# ── Row / tab parsing ────────────────────────────────────────────────────────


def _find_header_row(values: list[list[str]]) -> int | None:
    """Index of the first row containing a CLIENTE cell (the header row)."""
    for i, row in enumerate(values):
        for cell in row:
            if str(cell).strip().upper() == "CLIENTE":
                return i
    return None


def _column_map_for_header(header_row: list[str]) -> dict:
    """Pick the year-specific column map from where CLIENTE sits."""
    for i, cell in enumerate(header_row):
        if str(cell).strip().upper() == "CLIENTE":
            return (
                COLUMNS_2024 if i == COLUMNS_2024["customer_name"] else (COLUMNS_MODERN)
            )
    return COLUMNS_MODERN


def _is_repeated_header(row: list) -> bool:
    """2024 repeats its header inline between month blocks — skip those."""
    return any(str(cell).strip().upper() == "FORMA DE PAGO" for cell in row)


def parse_tab(tab: str, values: list[list[str]]) -> list[dict]:
    """Parse one tab's raw values into Sale-shaped dicts.

    A row is a candidate when CLIENTE or DESCRIPCION or IMPORTE is non-empty
    and it is not a repeated header row. Bad candidates are quarantined
    (quarantined=True + quarantine_reason), never dropped; a row can NEVER
    raise out of this function.
    """
    header_idx = _find_header_row(values)
    if header_idx is None:
        return []
    colmap = _column_map_for_header(values[header_idx])

    parsed: list[dict] = []
    for i, row in enumerate(values[header_idx + 1 :], start=header_idx + 1):
        try:
            record = _parse_row(tab, i, row, colmap)
        except Exception as e:  # defensive: a bad row must never kill the sync
            record = {
                "sheet_tab": tab,
                "source_row": i,
                "quarantined": True,
                "quarantine_reason": f"parser error: {e}"[:200],
            }
        if record is not None:
            parsed.append(record)
    return parsed


def _parse_row(tab: str, row_index: int, row: list, colmap: dict) -> dict | None:
    if _is_repeated_header(row):
        return None

    customer_raw = _cell(row, colmap.get("customer_name"))
    description_raw = _cell(row, colmap.get("description"))
    amount_raw = _cell(row, colmap.get("amount"))
    if not (customer_raw or description_raw or amount_raw):
        return None  # blank / structural row (month TOTAL separators etc.)

    month_label = _cell(row, colmap.get("month_label"))
    month_hint = SPANISH_MONTHS.get(_strip_accents(month_label).strip().lower())
    date_raw = _cell(row, colmap.get("sale_date"))
    sale_date = parse_spanish_date(date_raw, month_hint=month_hint)
    amount = parse_money(amount_raw)

    reasons = []
    if sale_date is None:
        reasons.append(
            "missing/unparseable date" + (f" ({date_raw})" if date_raw else "")
        )
    else:
        # Year typos that still parse (e.g. "…de 2027") would pollute phantom
        # monthly buckets; a hand-logged sale can never be in the future.
        if sale_date > date.today() + timedelta(days=7):
            reasons.append(f"future date ({sale_date.isoformat()})")
        # Cross-year rows (e.g. a 2024 date in the 2025 tab) stay CLEAN: the
        # sheet's own totals count them in their tab, and quarantining them
        # would silently diverge our grand total from the sheet's.
    if amount is None or amount == 0:
        reasons.append("missing/zero amount")
    if not customer_raw and not description_raw:
        reasons.append("missing customer and description")

    return {
        "sheet_tab": tab,
        "source_row": row_index,
        "sale_date": sale_date,
        "month_label": month_label[:20] or None,
        "customer_name": customer_raw[:200] or None,
        "description": description_raw or None,
        "unit": _cell(row, colmap.get("unit"))[:30] or None,
        "quantity": parse_qty(_cell(row, colmap.get("quantity"))),
        "unit_price": parse_money(_cell(row, colmap.get("unit_price"))),
        "amount": amount,
        "concept": _cell(row, colmap.get("concept"))[:100] or None,
        "payment_method": normalize_payment_method(
            _cell(row, colmap.get("payment_method"))
        ),
        "delivery_place": _cell(row, colmap.get("delivery_place"))[:200] or None,
        "reference": _cell(row, colmap.get("reference"))[:200] or None,
        "folio": normalize_folio(_cell(row, colmap.get("folio"))),
        "delivery_status": normalize_delivery_status(
            _cell(row, colmap.get("delivery_status"))
        ),
        "requires_invoice": parse_si_no(_cell(row, colmap.get("requires_invoice"))),
        "registered": parse_si_no(_cell(row, colmap.get("registered"))),
        "quarantined": bool(reasons),
        "quarantine_reason": ("; ".join(reasons))[:200] if reasons else None,
    }


# ── DB upsert + customer linking ─────────────────────────────────────────────


def _customer_map(db: Session) -> dict[str, int]:
    """name_normalized -> customer.id (exact-match linking only)."""
    rows = (
        db.query(Customer.id, Customer.name_normalized)
        .filter(Customer.name_normalized.isnot(None))
        .all()
    )
    return {name: cid for cid, name in rows if name}


def upsert_sales(db: Session, parsed: list[dict], customer_map: dict[str, int]) -> dict:
    """Upsert parsed rows by (sheet_tab, source_row). Returns counts."""
    if not parsed:
        return {"rows": 0, "inserted": 0, "updated": 0, "quarantined": 0, "deleted": 0}

    tab = parsed[0]["sheet_tab"]
    existing = {
        s.source_row: s for s in db.query(Sale).filter(Sale.sheet_tab == tab).all()
    }

    now = datetime.now(timezone.utc)
    inserted = updated = quarantined = 0
    for record in parsed:
        record = dict(record)
        name = record.get("customer_name")
        record["customer_id"] = (
            customer_map.get(normalize_customer_name(name)) if name else None
        )
        record["imported_at"] = now
        if record.get("quarantined"):
            quarantined += 1

        current = existing.get(record["source_row"])
        if current is not None:
            for key, value in record.items():
                setattr(current, key, value)
            updated += 1
        else:
            db.add(Sale(**record))
            inserted += 1

    # Rows deleted (or emptied) in the sheet shift every later key up one —
    # without pruning, the old tail row survives with a duplicate of the
    # previous last sale and stats double-count it forever. `parsed` is
    # non-empty here, so a failed/empty fetch can never wipe a tab.
    parsed_keys = {r["source_row"] for r in parsed}
    deleted = (
        db.query(Sale)
        .filter(Sale.sheet_tab == tab, Sale.source_row.notin_(parsed_keys))
        .delete(synchronize_session=False)
    )

    return {
        "rows": len(parsed),
        "inserted": inserted,
        "updated": updated,
        "quarantined": quarantined,
        "deleted": deleted,
    }


def sync_all(db: Session) -> dict:
    """Fetch + snapshot + parse + upsert every VENTAS tab. Single commit at
    the end so a mid-run failure leaves the ledger untouched."""
    token = get_sheets_access_token()
    customer_map = _customer_map(db)

    per_tab: dict[str, dict] = {}
    totals = {"rows": 0, "inserted": 0, "updated": 0, "quarantined": 0, "deleted": 0}
    for tab in TABS:
        values = fetch_tab(tab, token)
        snapshot_tab(tab, values)
        parsed = parse_tab(tab, values)
        counts = upsert_sales(db, parsed, customer_map)
        per_tab[tab] = counts
        for key in totals:
            totals[key] += counts.get(key, 0)

    db.commit()
    return {"per_tab": per_tab, "totals": totals}
