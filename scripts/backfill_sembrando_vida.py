#!/usr/bin/env python3
"""Backfill Sembrando Vida customers from the "Contactos_Sembrando Vida" sheet.

Phone-joins the SV contact list (191 rows, phone-keyed) against the customer
table: matches get the `sembrando-vida` tag (existing tags preserved) and
has_purchased upgraded when the sheet says they bought; unmatched contacts
with a valid phone are created as new customers with source='sembrando-vida'.

Reads the sheet live via the Sheets API (same OAuth env vars as sales_sync)
and snapshots it to scripts/snapshots/contactos_sembrando_vida.json first.

Usage:
  DATABASE_URL=... GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \
  GOOGLE_SHEETS_REFRESH_TOKEN=... python3 scripts/backfill_sembrando_vida.py            # dry-run
  ... python3 scripts/backfill_sembrando_vida.py --commit                               # write
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from models import Customer  # noqa: E402
from services.sales_sync import get_sheets_access_token  # noqa: E402

import requests  # noqa: E402

SV_SHEET_ID = "19Zpcwmwwshm9W2_ANX6cigpr5HRmKdJ1fwuwgkZ6VUs"
SV_TAB = "1. Sembrando Vida"
SV_TAG = "sembrando-vida"
SNAPSHOT = os.path.join(
    os.path.dirname(__file__), "snapshots", "contactos_sembrando_vida.json"
)


def normalize_phone(raw):
    """Same canonicalization as backfill_customers.py."""
    d = re.sub(r"\D", "", raw or "")
    d = re.sub(r"^0+", "", d)
    if not d:
        return None
    if d.startswith("521") and len(d) == 13:
        return "+52" + d[3:]
    if d.startswith("52") and len(d) == 12:
        return "+52" + d[2:]
    if d.startswith("1") and len(d) == 11:
        return "+1" + d[1:]
    if len(d) == 10:
        return "+52" + d
    return None  # unusable fragment — do NOT invent international numbers


def normalize_name(name):
    return re.sub(r"\s+", " ", name or "").strip().lower()[:200]


def fetch_contacts():
    token = get_sheets_access_token()
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SV_SHEET_ID}/values/"
        + requests.utils.quote(f"{SV_TAB}!A1:K400", safe="")
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    values = resp.json().get("values", [])
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False)

    # Header row contains 'Cliente' + 'Contacto'
    hdr_idx = next(
        i
        for i, r in enumerate(values)
        if any("Cliente" in str(c) for c in r) and any("Contacto" in str(c) for c in r)
    )
    hdr = [str(c).strip().lower() for c in values[hdr_idx]]

    def col(*needles):
        for i, h in enumerate(hdr):
            if all(n in h for n in needles):
                return i
        return None

    c_name, c_phone = col("cliente"), col("contacto")
    c_loc, c_bought = col("ubicaci"), col("adquirio")
    c_group, c_req = col("cac"), col("requerimiento")

    contacts = []
    for row in values[hdr_idx + 1 :]:

        def cell(i):
            return str(row[i]).strip() if i is not None and i < len(row) else ""

        name = cell(c_name)
        if not name:
            continue
        contacts.append(
            {
                "name": name,
                "phone": normalize_phone(cell(c_phone)),
                "raw_phone": cell(c_phone),
                "location": cell(c_loc),
                "bought": cell(c_bought).strip().lower().startswith("si"),
                "group": cell(c_group),
                "requirement": cell(c_req),
            }
        )
    return contacts


def main():
    commit = "--commit" in sys.argv
    engine = create_engine(os.environ["DATABASE_URL"])
    contacts = fetch_contacts()
    print(
        f"Contactos SV en el sheet: {len(contacts)} "
        f"({sum(1 for c in contacts if c['phone'])} con teléfono válido)"
    )

    with Session(engine) as db:
        by_phone = {c.phone_e164: c for c in db.query(Customer).all() if c.phone_e164}
        by_name = {
            c.name_normalized: c for c in db.query(Customer).all() if c.name_normalized
        }

        tagged = upgraded = created = skipped_no_phone = already = 0
        now = datetime.now(timezone.utc)
        for ct in contacts:
            cust = by_phone.get(ct["phone"]) if ct["phone"] else None
            if cust is None:
                cust = by_name.get(normalize_name(ct["name"]))

            if cust is not None:
                tags = list(cust.tags or [])
                if SV_TAG in tags:
                    already += 1
                else:
                    tags.append(SV_TAG)
                    cust.tags = tags
                    flag_modified(cust, "tags")
                    tagged += 1
                if ct["bought"] and not cust.has_purchased:
                    cust.has_purchased = True
                    upgraded += 1
            elif ct["phone"]:
                new_cust = Customer(
                    display_name=ct["name"][:200],
                    name_normalized=normalize_name(ct["name"]),
                    phone_e164=ct["phone"],
                    location=(f"{ct['location']}"[:300] or None),
                    source="sembrando-vida",
                    tags=[SV_TAG],
                    has_purchased=ct["bought"],
                    first_seen_at=now,
                )
                db.add(new_cust)
                # Register in the lookup maps so a duplicate sheet row with the
                # same phone updates this record instead of violating the
                # unique phone constraint with a second INSERT.
                by_phone[ct["phone"]] = new_cust
                if new_cust.name_normalized:
                    by_name.setdefault(new_cust.name_normalized, new_cust)
                created += 1
            else:
                skipped_no_phone += 1

        print(f"Etiquetados (existentes): {tagged}  |  ya tenían tag: {already}")
        print(f"has_purchased actualizado: {upgraded}")
        print(f"Clientes nuevos (source=sembrando-vida): {created}")
        print(
            f"Omitidos sin teléfono válido y sin match por nombre: {skipped_no_phone}"
        )

        if commit:
            db.commit()
            print("COMMIT hecho.")
        else:
            db.rollback()
            print("Dry-run — nada escrito. Re-ejecuta con --commit.")


if __name__ == "__main__":
    main()
