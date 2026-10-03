#!/usr/bin/env python3
"""Load the quotes announced in WhatsApp (Sep–Oct 2026) into the quote table.

Input: a JSON list scraped from WhatsApp Web search (one row per folio):
    {"f": "400926", "d": "30/9/2026", "c": ["<customer chat name>", ...],
     "doc": "400926DGO-MIGUEL CORDERO-PELETIZADORAS", "t": "<Cotización Enviada text>"}

Every row goes through the same path as the admin's "Registrar desde WhatsApp"
(services/quote_capture.parse_single + apply_capture), so a folio already in
the table is registered as a re-send instead of duplicated. Rows without a
template message are rebuilt from the PDF name. "A QUIEN CORRESPONDA" price
lists are skipped (no customer, not a deal).

STATUS holds what the chats showed this week (sales, "ya no le interesó",
"se sale de nuestro presupuesto"); everything else stays "sent".

Usage:
    python scripts/load_wa_quotes.py quotes.json                 # dry-run
    python scripts/load_wa_quotes.py quotes.json --commit        # write
    python scripts/load_wa_quotes.py quotes.json --commit --no-followup
        (--no-followup: last_followup_at = now, so tomorrow's follow-up sweep
         does not turn the whole backlog into tasks at once; it resumes after
         FOLLOWUP_INTERVAL_DAYS)
    --only-new: leave folios that are already registered alone (a history
        load is not a re-send).

A quote still "sent" but older than the follow-up window (DEAD_AFTER_DAYS) is
loaded as "expired", like scripts/backfill_quote_pdfs.py.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import SessionLocal  # noqa: E402
from services import quote_capture  # noqa: E402
from services.quote_followup import DEAD_AFTER_DAYS  # noqa: E402

LOADED_BY = "carga-whatsapp-2026-10"

# digits -> (status, reason, total). Evidence from the chats read 2026-10-03.
STATUS = {
    "350926": (
        "accepted",
        "Venta 02/10: 1 paq bolsa 17x17 $3,550 (transferencia)",
        Decimal("3550"),
    ),
    "370926": (
        "accepted",
        "Venta 02/10: kit de motobomba (nota NOT-IMPAG-011026DGO)",
        None,
    ),
    "100926": ("accepted", "28/09: «ya llegó su bolsa, ya puede pasar por ella»", None),
    "090926": (
        "needs_work",
        "Busca algo más económico: «se sale de nuestro poder adquisitivo» (01/10)",
        None,
    ),
    "230926": ("rejected", "Ya no le interesa (01/10)", None),
    # Jul–Aug, read 2026-10-03.
    "050726": ("accepted", "Nota de venta 050726DGO al mismo cliente (13/07)", None),
    "220826": (
        "accepted",
        "Nota de venta 120826DGO a Enrique, Pipila Coneto (25/08) + abonos",
        None,
    ),
}

# Folios that are not real quotes.
SKIP = {"130826": "«Cliente: lol» (prueba)"}


def parse_day(text: str) -> date:
    d, m, y = (int(x) for x in text.split("/"))
    return date(y, m, d)


def phone_from_chats(chats):
    """A chat named like '+52 1 677 885 7208' is the customer's number."""
    for name in chats or []:
        if re.fullmatch(r"\+?[\d ]{10,20}", name.strip()):
            return name
    return None


# Typos in the group message that the customer chat / PDF name gets right.
NAME_FIX = {
    "010926": ("Maticruz", "Maricruz"),
    "080826": ("Afredo", "Alfredo"),
    "090826": ("Alredo", "Alfredo"),
}


def message_for(row) -> str:
    doc_folio = (row.get("doc") or "").split("-")[0].strip()
    if row.get("t"):
        text = row["t"]
        # "Cotización Enviada 160926" while the PDF says 160926DGO: use the
        # PDF's folio so every quote_number carries its state.
        if re.fullmatch(r"\d{6}[A-Z]{2,4}", doc_folio):
            text = re.sub(
                rf"(enviada\s+){row['f']}(?![A-Za-z])",
                rf"\g<1>{doc_folio}",
                text,
                flags=re.I,
            )
        if row["f"] in NAME_FIX:
            text = text.replace(*NAME_FIX[row["f"]])
        return text
    # No template in the group: rebuild one from "FOLIO-CLIENTE-DESCRIPCION".
    folio, cliente, *rest = [p.strip() for p in row["doc"].split("-")]
    return f"Cotización Enviada {folio} Cliente: {cliente.title()} Material/Proyecto: {' '.join(rest)}"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    commit = "--commit" in sys.argv
    no_followup = "--no-followup" in sys.argv
    only_new = "--only-new" in sys.argv
    rows = json.load(open(sys.argv[1]))

    db = SessionLocal()
    created = updated = skipped = 0
    try:
        for row in sorted(rows, key=lambda r: parse_day(r["d"])):
            if "QUIEN CORRESPONDA" in (row.get("doc") or "").upper() or re.search(
                r"cliente:\s*a quien corresponda", row.get("t") or "", re.I
            ):
                print(f"SKIP  {row['f']}  lista de precios «a quien corresponda»")
                skipped += 1
                continue
            if row["f"] in SKIP:
                print(f"SKIP  {row['f']}  {SKIP[row['f']]}")
                skipped += 1
                continue
            try:
                parsed = quote_capture.parse_single(message_for(row))
            except quote_capture.CaptureError as exc:
                print(f"ERROR {row['f']}  {exc}")
                skipped += 1
                continue

            if only_new and quote_capture.find_existing(db, parsed) is not None:
                print(f"EXISTE   {parsed.quote_number:22} {row['d']:>10}  (no se toca)")
                skipped += 1
                continue
            status, reason, total = STATUS.get(row["f"], ("sent", None, None))
            sent_day = parse_day(row["d"])
            if status == "sent" and (date.today() - sent_day).days > DEAD_AFTER_DAYS:
                status = "expired"
            phone = phone_from_chats(row.get("c"))
            result = quote_capture.apply_capture(
                db,
                parsed,
                user_email=LOADED_BY,
                total=total,
                phone=phone,
                sent_date=sent_day,
                dry_run=not commit,
            )
            action = result["action"]
            created += action == "created"
            updated += action == "updated"
            print(
                f"{action.upper():8} {parsed.quote_number:22} {row['d']:>10}  "
                f"{(parsed.cliente or '')[:24]:24} {status:10} "
                f"{(phone or 'S/N'):>18}  {(parsed.material or '')[:40]}"
            )

            quote = result["quote"]
            if commit and quote is not None:
                now = datetime.now(timezone.utc)
                if status == "expired":
                    quote.status = status
                    quote.expired_at = quote.sent_at + timedelta(
                        days=quote.validity_days
                    )
                elif status != "sent":
                    quote.status = status
                    if status == "accepted":
                        quote.accepted_at = quote.accepted_at or now
                    label = {
                        "accepted": "Aceptada",
                        "needs_work": "Por ajustar",
                        "rejected": "Perdida",
                    }[status]
                    line = f"[Estado] {quote_capture._stamp(now)} {label} — {reason} ({LOADED_BY})"
                    quote.notes = f"{quote.notes}\n{line}"
                if no_followup and status == "sent":
                    quote.last_followup_at = now
                db.commit()
    finally:
        db.close()

    print(f"\n{created} nuevas, {updated} reenvíos, {skipped} omitidas.")
    if not commit:
        print("Dry-run — nada escrito. Re-ejecuta con --commit.")


if __name__ == "__main__":
    main()
