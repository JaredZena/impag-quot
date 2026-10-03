"""Sync the Bombeo Solar Concentrado de Precios sheet: VDE parts into
supplier_product, kits into KIT products sold online at a 30% margin.

Usage (from impag-quot/):
  venv/bin/python scripts/sync_bombeo_sheet.py            # dry run: full diff, rolls back
  venv/bin/python scripts/sync_bombeo_sheet.py --apply    # write

Same code path as POST /jobs/bombeo-sheet-sync (the daily GitHub Action).
"""

import os

os.environ.setdefault("ALEMBIC_RUNNING", "1")

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import SessionLocal
from services.bombeo_sheet_sync import sync_bombeo_sheet


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", help="write the full JSON report here")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        report = sync_bombeo_sheet(db, dry_run=not args.apply)
    finally:
        db.close()

    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str)
        )
    print(
        f"{'APPLIED' if args.apply else 'DRY RUN'}: {report['parts_parsed']} parts, {report['kits_parsed']} kits"
    )
    for section in ("parts", "kits"):
        print(
            f"  {section}: "
            + ", ".join(
                f"{k} {len(v) if isinstance(v, list) else v}"
                for k, v in report[section].items()
            )
        )
    for k in report["kits"]["created"] + report["kits"]["updated"]:
        print(
            f"    {k.get('product_id')}: {k['kit']} → {k.get('price', k.get('new_price'))}"
        )
    for s in report["skipped"]:
        print(f"  skipped: {s}")


if __name__ == "__main__":
    main()
