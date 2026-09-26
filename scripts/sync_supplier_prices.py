"""Sync the Concentrado de Precios sheet into supplier_product.

Usage (from impag-quot/):
  venv/bin/python scripts/sync_supplier_prices.py            # dry run: full diff, rolls back
  venv/bin/python scripts/sync_supplier_prices.py --apply    # write
  venv/bin/python scripts/sync_supplier_prices.py --apply --retire-legacy
      # one-time: also archive the 2025-11-17 import of an older copy of this
      # sheet (superseded by the gsheet: rows). Only unreferenced rows.

Same code path as POST /jobs/supplier-price-sync (the daily GitHub Action).
"""

import os

os.environ.setdefault("ALEMBIC_RUNNING", "1")

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

from sqlalchemy import text

from models import SessionLocal
from services.supplier_price_sync import sync_supplier_prices

# Suppliers created by the 2025-11-17 import of an older copy of the sheet.
LEGACY_SUPPLIER_IDS = (96, 97, 98, 99, 101, 104)

LEGACY_SQL = """
    SELECT sp.id FROM supplier_product sp
    WHERE sp.supplier_id = ANY(:sids)
      AND sp.archived_at IS NULL
      AND sp.created_at::date = DATE '2025-11-17'
      AND COALESCE(sp.supplier_sku, '') NOT LIKE 'gsheet:%'
      AND NOT EXISTS (SELECT 1 FROM quote_item q WHERE q.supplier_product_id = sp.id)
      AND NOT EXISTS (SELECT 1 FROM kit_item k WHERE k.supplier_product_id = sp.id)
      AND NOT EXISTS (SELECT 1 FROM pos_sale_item p WHERE p.supplier_product_id = sp.id)
      AND NOT EXISTS (SELECT 1 FROM balance_item b WHERE b.supplier_product_id = sp.id)
"""


def retire_legacy(db, apply: bool) -> int:
    ids = [
        r[0] for r in db.execute(text(LEGACY_SQL), {"sids": list(LEGACY_SUPPLIER_IDS)})
    ]
    if apply and ids:
        db.execute(
            text(
                "UPDATE supplier_product SET archived_at = :now, is_active = false WHERE id = ANY(:ids)"
            ),
            {"now": datetime.now(timezone.utc), "ids": ids},
        )
        db.commit()
    return len(ids)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--retire-legacy", action="store_true")
    ap.add_argument("--out", help="write the full JSON report here")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        report = sync_supplier_prices(db, dry_run=not args.apply)
        if args.retire_legacy:
            n = retire_legacy(db, apply=args.apply)
            print(
                f"  legacy 2025-11-17 rows {'archived' if args.apply else 'to archive'}: {n}"
            )
    finally:
        db.close()

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        f"{'APPLIED' if args.apply else 'DRY RUN'}: {report['rows_parsed']} filas, "
        f"{report['linked_to_cotizador']} con paquetería del Cotizador"
    )
    for k in ("created", "updated", "archived"):
        print(f"  {k}: {len(report[k])}")
    print(f"  unchanged: {report['unchanged']}")
    for g in report["archive_guarded"]:
        print(f"  GUARD: {g}")
    print(f"  skipped: {len(report['skipped'])}")


if __name__ == "__main__":
    main()
