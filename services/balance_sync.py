"""
Sale-margin sync — mirrors the BALANCES DE VENTA Google Sheet into the
`sale_balance` table (one row per spreadsheet tab = one sale's cost breakdown).

Operational snapshot only, NOT accounting books.

The spreadsheet has ~136 tabs, one per sale, named after the sale's folio
('070826DGO', 'VENTA-IMPAG-010624DGO', '370525DGO-MARIA LUISA CORTEZ', or
unnamed 'Hoja N'). Layouts vary by era, so columns are mapped from each tab's
own header row instead of fixed indexes. Two hard-won parsing facts:

- Several columns come in unit/total pairs (same or near-same header repeated,
  e.g. 'Costo Real U' then 'Costo Total'); the TOTAL half is the usable one.
- The sheet's own 'P. Venta' cells are sometimes stale, so revenue truth is
  the sale ledger: a tab's folios are matched against `sale.folio` and margin
  is computed as ledger_revenue - cost_total, but ONLY when the sheet's own
  customer-facing total reconciles with the ledger (within tolerance).

Match statuses:
  reconciled      folio matched, sheet total ~= ledger revenue → margin trusted
  unverified      folio matched but the sheet has no verifiable total
  mismatch        folio matched but the totals disagree → margin withheld
  no_ledger_match tab names a folio the ledger doesn't have
  duplicate       every folio of the tab is already claimed by an earlier tab
  orphan          tab has no folio in its title ('Hoja 18')
"""

import json
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import os

import requests
from sqlalchemy.orm import Session

from models import Sale, SaleBalance
from services.sales_sync import SHEETS_TIMEOUT_SECONDS, get_sheets_access_token

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "snapshots"
    / "balances_de_venta.json"
)

BATCH_TABS = 15  # tabs per values:batchGet call (keeps URLs + quota sane)
STATES = ("DGO", "EDO", "VER", "PUE", "COA", "CHI", "ZAC", "NI")
SKIP_TABS_PREFIX = ("DEPOSITO",)
SKIP_TABS = {"Indice"}
# |sheet total - ledger revenue| within this → reconciled
RECON_TOLERANCE_ABS = Decimal("50")
RECON_TOLERANCE_PCT = Decimal("0.02")


# ── Sheet fetch ──────────────────────────────────────────────────────────────


def fetch_all_tabs(access_token: str | None = None) -> dict[str, list[list[str]]]:
    """Every data tab's raw values (A1:AG120), keyed by tab title."""
    spreadsheet_id = os.getenv("BALANCES_SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("BALANCES_SPREADSHEET_ID env var is not set")
    if access_token is None:
        access_token = get_sheets_access_token()

    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        params={"fields": "sheets(properties(title,gridProperties(rowCount)))"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=SHEETS_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Balances sheet metadata failed (HTTP {resp.status_code}): "
            f"{resp.text[:300]}"
        )
    tabs = [
        s["properties"]["title"]
        for s in (resp.json() or {}).get("sheets", [])
        # chart sheets have no rowCount
        if s["properties"].get("gridProperties", {}).get("rowCount")
    ]
    tabs = [
        t for t in tabs if t not in SKIP_TABS and not t.startswith(SKIP_TABS_PREFIX)
    ]

    data: dict[str, list[list[str]]] = {}
    for i in range(0, len(tabs), BATCH_TABS):
        chunk = tabs[i : i + BATCH_TABS]
        params = [("ranges", f"'{t}'!A1:AG120") for t in chunk] + [
            ("valueRenderOption", "FORMATTED_VALUE")
        ]
        resp = requests.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
            "/values:batchGet",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=SHEETS_TIMEOUT_SECONDS * 2,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Balances batchGet failed (HTTP {resp.status_code}): "
                f"{resp.text[:300]}"
            )
        for tab, value_range in zip(chunk, (resp.json() or {}).get("valueRanges", [])):
            data[tab] = value_range.get("values", [])
        time.sleep(0.5)  # stay under the per-minute read quota

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


# ── Cell / header helpers ────────────────────────────────────────────────────


def _norm(cell) -> str:
    """Accent-stripped, whitespace-collapsed, lowercased header/cell text."""
    s = unicodedata.normalize("NFD", str(cell or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def _money(cell) -> Decimal | None:
    s = str(cell or "").replace("$", "").replace(",", "").strip()
    if s in ("", "-", "—"):
        return None
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except Exception:
        return None


def _is_unit_col(header: str) -> bool:
    """Header names a per-unit value ('costo real u', 'precio u. de venta')."""
    return bool(re.search(r"(?:^|[ .])u\.?(?:$|[ .])", header) or "unitario" in header)


def folios_from_title(title: str) -> list[str]:
    """'070724DGO-BERNADO...' -> ['070724DGO']; handles 8-digit years, multi-
    folio titles ('200526 Y 010326') and missing state suffixes."""
    out = []
    for m in re.finditer(r"(\d{6,8})\s*-?\s*([A-Z]{2,4})?", title.upper()):
        digits, state = m.group(1), m.group(2) or ""
        if len(digits) == 8:  # ddmm2024 → ddmmyy
            digits = digits[:4] + digits[6:]
        if len(digits) != 6:
            continue
        out.append(digits + (state if state in STATES else ""))
    return out


def folio_month(folio: str) -> date | None:
    """Folio format is NN+MM+YY(+state): counter, month, 2-digit year."""
    m = re.match(r"\d{2}(\d{2})(\d{2})", folio)
    if not m:
        return None
    month, year = int(m.group(1)), 2000 + int(m.group(2))
    if not 1 <= month <= 12 or not 2020 <= year <= 2100:
        return None
    return date(year, month, 1)


# ── Tab parsing ──────────────────────────────────────────────────────────────


def parse_tab(title: str, rows: list[list[str]]) -> dict:
    """One BALANCES tab → SaleBalance-shaped dict (no DB fields).

    Never raises on malformed content: unparseable tabs come back with
    parse_ok=False and are stored as data-less rows for visibility.
    """
    result = {
        "tab_title": title[:120],
        "folios": folios_from_title(title),
        "customer_name": None,
        "parse_ok": False,
        "item_count": 0,
        "cost_subtotal": None,
        "shipping_total": None,
        "cost_total": None,
        "sheet_sale_total": None,
        "sheet_profit": None,
        "items": None,
    }
    try:
        _parse_tab_inner(result, rows)
    except Exception:  # defensive: one bad tab must never kill the sync
        result["parse_ok"] = False
    return result


def _parse_tab_inner(result: dict, rows: list[list[str]]) -> None:
    for row in rows[:8]:
        cells = [str(c).strip() for c in row]
        for i, cell in enumerate(cells):
            if _norm(cell) == "cliente" and i + 1 < len(cells) and cells[i + 1]:
                result["customer_name"] = cells[i + 1][:200]

    header_idx = None
    for i, row in enumerate(rows):
        normed = [_norm(c) for c in row]
        if any("descripcion" in c for c in normed) and any(
            c.startswith("cantidad") for c in normed
        ):
            header_idx = i
            break
    if header_idx is None:
        return  # empty / free-form tab
    header = [_norm(c) for c in rows[header_idx]]

    # The customer-facing copy block (FECHA DE COMPRA / CONCEPTO ... TOTAL) on
    # the right repeats sale figures; cost columns must come from its left.
    right_block = [
        i
        for i, h in enumerate(header)
        if h.startswith("fecha de compra") or h == "concepto"
    ]
    right_edge = min(right_block) if right_block else len(header)

    def find(pred, after=-1, before=None):
        edge = right_edge if before is None else before
        return [i for i, h in enumerate(header) if after < i < edge and h and pred(h)]

    desc_cols = find(lambda h: "descripcion" in h)
    if not desc_cols:
        return
    desc_idx = desc_cols[0]
    venta_cols = find(lambda h: "venta" in h)
    first_venta = venta_cols[0] if venta_cols else right_edge

    def dedup_unit_total(cols):
        """Adjacent unit/total column pairs (same or shared-prefix headers,
        one marked per-unit) → keep the total column."""
        kept = []
        for i in cols:
            if i + 1 in cols and (
                header[i + 1] == header[i]
                or (_is_unit_col(header[i]) and header[i + 1][:6] == header[i][:6])
            ):
                continue
            if _is_unit_col(header[i]) and (i - 1 in cols or i + 1 in cols):
                continue
            kept.append(i)
        return kept

    cost_importe = find(
        lambda h: h.startswith("importe"), after=desc_idx, before=first_venta
    )
    shipping = dedup_unit_total(
        find(
            lambda h: any(
                k in h
                for k in (
                    "env",
                    "paqueteria",
                    "guerras",
                    "mzta",
                    "mover",
                    "/local",
                    "flete",
                    "taxi",
                )
            )
        )
    )
    shipping_totals = [i for i in shipping if "total" in header[i]]
    if shipping_totals:  # explicit 'TOTAL DE PAQUETERIAS' beats leg columns
        shipping = shipping_totals
    # 'Importe Real' (one 2025 layout) is the all-in cost total, same role as
    # 'COSTO REAL TOTAL' — including it here also keeps it out of the
    # importe+shipping fallback, which would double-count the freight.
    cost_real = [
        i
        for i in dedup_unit_total(
            find(lambda h: h.startswith("costo") or "importe real" in h)
        )
        if not _is_unit_col(header[i])
    ]
    # 'COSTO REAL' + 'COSTO REAL TOTAL' side by side: the TOTAL one wins
    cost_real_totals = [i for i in cost_real if "total" in header[i]]
    if cost_real_totals:
        cost_real = cost_real_totals
    sale_cols = [i for i in venta_cols if not _is_unit_col(header[i])]
    trailing_importe = find(lambda h: h.startswith("importe"), after=first_venta)
    if not sale_cols and trailing_importe:
        sale_cols = [trailing_importe[0]]
    gain_cols = [
        i
        for i in find(lambda h: h.startswith("ganancia"))
        if not _is_unit_col(header[i])
    ]

    zero = Decimal("0")
    sums = {"imp": zero, "ship": zero, "cost": zero, "sale": zero, "gain": zero}
    seen = dict.fromkeys(sums, False)
    items = []
    total_row_value = None
    for row in rows[header_idx + 1 :]:
        normed = [_norm(c) for c in row]
        if any(c in ("total", "totales", "total gral") for c in normed):
            # customer-facing grand total: last money value on the totals row
            money_vals = [v for v in (_money(c) for c in row) if v is not None]
            if money_vals:
                total_row_value = money_vals[-1]
            break
        description = str(row[desc_idx]).strip() if desc_idx < len(row) else ""
        if not description:
            continue

        def col_value(cols, row=row):
            if cols and cols[0] < len(row):
                return _money(row[cols[0]])
            return None

        item = {
            "description": description[:300],
            "quantity": (
                str(row[desc_cols[0] + 1]).strip()[:20]
                if desc_cols[0] + 1 < len(row)
                else None
            ),
            "cost_importe": None,
            "cost_total": None,
            "sale_total": None,
            "profit": None,
        }
        for key, cols, item_key in (
            ("imp", cost_importe, "cost_importe"),
            ("cost", cost_real, "cost_total"),
            ("sale", sale_cols, "sale_total"),
            ("gain", gain_cols, "profit"),
        ):
            value = col_value(cols)
            if value is not None:
                sums[key] += value
                seen[key] = True
                item[item_key] = float(value)
        row_shipping = zero
        for i in shipping:
            value = _money(row[i]) if i < len(row) else None
            if value is not None:
                row_shipping += value
                seen["ship"] = True
        sums["ship"] += row_shipping
        items.append(item)

    if not items:
        return
    cost_total = (
        sums["cost"]
        if seen["cost"]
        else (sums["imp"] + sums["ship"] if seen["imp"] else None)
    )
    if cost_total is None:
        return
    result.update(
        parse_ok=True,
        item_count=len(items),
        cost_subtotal=sums["imp"] if seen["imp"] else None,
        shipping_total=sums["ship"] if seen["ship"] else None,
        cost_total=cost_total,
        # the totals row is authoritative; per-item sale sum is the fallback
        sheet_sale_total=(
            total_row_value
            if total_row_value is not None
            else (sums["sale"] if seen["sale"] else None)
        ),
        sheet_profit=sums["gain"] if seen["gain"] else None,
        items=items,
    )


# ── Ledger matching ──────────────────────────────────────────────────────────


def _ledger_revenue_by_folio(db: Session) -> dict[str, Decimal]:
    """folio -> summed clean-ledger revenue (a folio can span several rows)."""
    rows = (
        db.query(Sale.folio, Sale.amount)
        .filter(
            Sale.quarantined.is_(False),
            Sale.folio.isnot(None),
            Sale.folio != "",
        )
        .all()
    )
    by_folio: dict[str, Decimal] = {}
    for folio, amount in rows:
        key = folio.strip().upper()
        by_folio[key] = by_folio.get(key, Decimal("0")) + (amount or Decimal("0"))
    return by_folio


def match_ledger(parsed: list[dict], by_folio: dict[str, Decimal]) -> None:
    """Mutates each parsed dict: ledger_revenue, margin, match_status."""
    # '090726' (no state) must still find ledger folio '090726DGO' when unique
    by_core: dict[str, list[str]] = {}
    for folio in by_folio:
        by_core.setdefault(re.sub(r"[A-Z]+$", "", folio), []).append(folio)

    claimed: set[str] = set()
    for record in parsed:
        record.setdefault("ledger_revenue", None)
        record.setdefault("margin_amount", None)
        record.setdefault("margin_pct", None)
        record.setdefault("recon_delta", None)

        if not record["folios"]:
            record["match_status"] = "orphan"
            continue
        hits = []
        for folio in record["folios"]:
            if folio in by_folio:
                hits.append(folio)
            else:
                candidates = by_core.get(re.sub(r"[A-Z]+$", "", folio), [])
                if len(candidates) == 1:
                    hits.append(candidates[0])
        if not hits:
            record["match_status"] = "no_ledger_match"
            continue
        if all(folio in claimed for folio in hits):
            record["match_status"] = "duplicate"
            continue
        claimed.update(hits)

        revenue = sum((by_folio[f] for f in hits), Decimal("0"))
        record["ledger_revenue"] = revenue
        if not record["parse_ok"]:
            record["match_status"] = "unverified"
            continue

        sheet_total = record["sheet_sale_total"]
        if sheet_total is None:
            record["match_status"] = "unverified"
        else:
            tolerance = max(RECON_TOLERANCE_ABS, revenue * RECON_TOLERANCE_PCT)
            delta = sheet_total - revenue
            record["recon_delta"] = delta
            record["match_status"] = (
                "reconciled" if abs(delta) <= tolerance else "mismatch"
            )
        if (
            record["match_status"] in ("reconciled", "unverified")
            and revenue > 0
            and record["cost_total"] is not None
        ):
            margin = revenue - record["cost_total"]
            pct = (margin / revenue * 100).quantize(Decimal("0.01"))
            record["margin_amount"] = margin
            # Numeric(7,2) — a wildly unverified ratio must not abort the sync
            record["margin_pct"] = max(
                min(pct, Decimal("9999.99")), Decimal("-9999.99")
            )


# ── Upsert ───────────────────────────────────────────────────────────────────


def upsert_balances(db: Session, parsed: list[dict]) -> dict:
    """Upsert by tab_title; prune rows whose tab no longer exists. `parsed` is
    guaranteed non-empty by the caller, so a failed fetch can never wipe it."""
    existing = {b.tab_title: b for b in db.query(SaleBalance).all()}
    now = datetime.now(timezone.utc)

    inserted = updated = 0
    counts: dict[str, int] = {}
    for record in parsed:
        record = dict(record)
        record.pop("parse_ok", None)
        record["folio_month"] = next(
            (fm for fm in (folio_month(f) for f in record["folios"]) if fm), None
        )
        record["synced_at"] = now
        counts[record["match_status"]] = counts.get(record["match_status"], 0) + 1

        current = existing.get(record["tab_title"])
        if current is not None:
            for key, value in record.items():
                setattr(current, key, value)
            updated += 1
        else:
            db.add(SaleBalance(**record))
            inserted += 1

    parsed_titles = {r["tab_title"] for r in parsed}
    deleted = (
        db.query(SaleBalance)
        .filter(SaleBalance.tab_title.notin_(parsed_titles))
        .delete(synchronize_session=False)
    )
    return {
        "tabs": len(parsed),
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "by_status": counts,
    }


def sync_balances(db: Session) -> dict:
    """Fetch + parse + match + upsert the whole BALANCES sheet. Single commit
    at the end so a mid-run failure leaves the table untouched."""
    data = fetch_all_tabs()
    parsed = [parse_tab(title, rows) for title, rows in data.items()]
    parsed = [p for p in parsed if p["parse_ok"] or p["folios"]]
    if not parsed:
        raise RuntimeError("Balances sheet returned no parseable tabs — aborting")
    match_ledger(parsed, _ledger_revenue_by_folio(db))
    summary = upsert_balances(db, parsed)
    db.commit()
    return summary
