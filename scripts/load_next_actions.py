"""Load each open quote's Siguiente paso (next step, owner, due date) from a JSON file.

Input: a JSON array, one row per quote (other keys are ignored):
    {"quote_number": "COT-IMPAG-400926DGO", "next_action": "Llamar para cerrar",
     "owner": "Hernán", "due": "2026-10-07"}

Usage (from impag-quot/):
  venv/bin/python scripts/load_next_actions.py --file next.json            # dry run
  venv/bin/python scripts/load_next_actions.py --file next.json --commit   # write

Same fields and audit line as PUT /quotes/{id}/next-action
(services/quote_next_action.py), signed LOADED_BY. A null next_action marks
the step done. Unknown quote_numbers are skipped with a warning; a quote that
already has exactly that step is left alone, so a re-run adds no notes.
next_action is cut to 200 characters ("…").

Safety: ALEMBIC_RUNNING=1 is set before `models` is imported so its
module-level create_all can never create tables. The quote.next_action*
columns must exist (`alembic upgrade head`) before running.
"""

import os

os.environ.setdefault("ALEMBIC_RUNNING", "1")

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOADED_BY = "revisión de chats 2026-10-05"


class RowError(ValueError):
    pass


def parse_row(row) -> dict:
    """{"quote_number", "next_action", "owner", "due"} cleaned for
    set_next_action; RowError when the row is unusable."""
    from services.quote_next_action import MAX_ACTION, MAX_OWNER, clean

    if not isinstance(row, dict):
        raise RowError(f"no es un objeto: {row!r}")
    quote_number = clean(str(row.get("quote_number") or ""))
    if not quote_number:
        raise RowError("sin quote_number")
    next_action = clean(row.get("next_action"))
    if next_action and len(next_action) > MAX_ACTION:
        next_action = next_action[: MAX_ACTION - 1].rstrip() + "…"
    owner = clean(row.get("owner"))
    if owner:
        owner = owner[:MAX_OWNER]
    due = row.get("due")
    if isinstance(due, str):
        due = due.strip() or None
    if due is not None:
        try:
            due = date.fromisoformat(due)
        except (TypeError, ValueError) as exc:
            raise RowError(
                f"{quote_number}: fecha inválida {row.get('due')!r}"
            ) from exc
    if next_action is None:
        owner = due = None
    return {
        "quote_number": quote_number,
        "next_action": next_action,
        "owner": owner,
        "due": due,
    }


def _fmt(next_action, owner, due) -> str:
    if next_action is None:
        return "—"
    return " · ".join(
        p for p in (next_action, owner, due.isoformat() if due else None) if p
    )


def apply_rows(db, rows, *, commit: bool = False, who: str = LOADED_BY) -> dict:
    """Match each row to its quote by quote_number and set its next step.
    Prints one line per row. Writes (one commit at the end) only with
    commit=True; a dry run changes nothing. Returns the counts."""
    from models import Quote
    from services.quote_next_action import set_next_action

    counts = {"updated": 0, "unchanged": 0, "missing": 0, "invalid": 0}
    for row in rows:
        try:
            item = parse_row(row)
        except RowError as exc:
            print(f"WARN  fila omitida: {exc}")
            counts["invalid"] += 1
            continue
        quote = (
            db.query(Quote).filter(Quote.quote_number == item["quote_number"]).first()
        )
        if quote is None:
            print(f"WARN  {item['quote_number']}: no existe, se omite")
            counts["missing"] += 1
            continue
        current = (quote.next_action, quote.next_action_owner, quote.next_action_due)
        wanted = (item["next_action"], item["owner"], item["due"])
        old, new = _fmt(*current), _fmt(*wanted)
        if current == wanted:
            counts["unchanged"] += 1
            print(f"=     {quote.quote_number:22} {quote.status:12} {old}")
            continue
        counts["updated"] += 1
        print(f"SET   {quote.quote_number:22} {quote.status:12} {old} → {new}")
        if commit:
            set_next_action(
                quote, item["next_action"], item["owner"], item["due"], who=who
            )
    if commit:
        db.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", required=True, help="JSON array of rows")
    parser.add_argument("--commit", action="store_true", help="write to the database")
    args = parser.parse_args()

    rows = json.loads(Path(args.file).read_text())
    if not isinstance(rows, list):
        sys.exit("El archivo debe ser un arreglo JSON.")

    from models import SessionLocal

    db = SessionLocal()
    try:
        counts = apply_rows(db, rows, commit=args.commit)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(
        f"\n{'Escrito' if args.commit else 'Dry run'}: "
        + ", ".join(f"{k} {v}" for k, v in counts.items())
    )
    if not args.commit:
        print("Nada se escribió. Usa --commit para guardar.")


if __name__ == "__main__":
    main()
