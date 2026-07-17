#!/usr/bin/env python3
"""
Deterministic, phone-keyed customer backfill (roadmap P2 — Customer 360).

Seeds the customer master from the CLEAN machine key that already exists:
WAConversation.customer_phone (unique E.164) + Quote.customer_phone. No fuzzy
name matching here — that's a later, separate pass. Idempotent: re-runnable;
matches on normalized phone.

Usage: python scripts/backfill_customers.py
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import SessionLocal, Customer, WAConversation, Quote

DIRECTORIO_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots", "directorio_clientes.csv")
_SOURCE_MAP = {"visita": "visita", "whatsapp": "whatsapp", "marketplace": "marketplace", "messenger": "messenger"}


def normalize_phone(raw):
    """Canonicalize to E.164 for dedup. MX numbers → +52 + 10 national digits
    (drops the WhatsApp mobile '1'); US → +1; else best-effort."""
    d = re.sub(r"\D", "", raw or "")
    d = re.sub(r"^0+", "", d)  # strip dialing prefix (0 / 01)
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
    return "+" + d


def _touch(cust, name=None, email=None, location=None, at=None, source=None, purchased=False):
    if name and not cust.display_name:
        cust.display_name = name.strip()[:200]
        cust.name_normalized = re.sub(r"\s+", " ", name).strip().lower()[:200]
    if email and not cust.email:
        cust.email = email.strip()[:255]
    if location and not cust.location:
        cust.location = location.strip()[:300]
    if source and not cust.source:
        cust.source = source
    if purchased:
        cust.has_purchased = True
    if at:
        if cust.first_seen_at is None or at < cust.first_seen_at:
            cust.first_seen_at = at
        if cust.last_activity_at is None or at > cust.last_activity_at:
            cust.last_activity_at = at


def main():
    db = SessionLocal()
    try:
        cache = {c.phone_e164: c for c in db.query(Customer).all()}

        def get_or_create(phone_e164):
            c = cache.get(phone_e164)
            if c is None:
                c = Customer(phone_e164=phone_e164)
                db.add(c)
                db.flush()
                cache[phone_e164] = c
            return c

        wa_linked = q_linked = dir_rows = 0

        # Directorio de Clientes snapshot (the real customer directory; phone-keyed)
        if os.path.exists(DIRECTORIO_CSV):
            with open(DIRECTORIO_CSV, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    phone = normalize_phone(row.get("telefono"))
                    if not phone:  # no-phone leads → deferred to the name-merge pass
                        continue
                    c = get_or_create(phone)
                    loc = " / ".join(x for x in [(row.get("lugar") or "").strip(),
                                                 (row.get("municipio") or "").strip()] if x)
                    _touch(c, name=row.get("nombre"), location=loc or None,
                           source=_SOURCE_MAP.get((row.get("tipo_contacto") or "").strip().lower()),
                           purchased=(row.get("compro", "").strip().lower() == "si"))
                    dir_rows += 1

        # WhatsApp conversations
        for conv in db.query(WAConversation).all():
            phone = normalize_phone(conv.customer_phone)
            if not phone:
                continue
            c = get_or_create(phone)
            _touch(c, name=conv.customer_name, at=conv.last_message_at or conv.created_at, source="whatsapp")
            conv.customer_id = c.id
            wa_linked += 1

        # Quotes
        for q in db.query(Quote).all():
            phone = normalize_phone(q.customer_phone)
            if not phone:
                continue
            c = get_or_create(phone)
            _touch(c, name=q.customer_name, email=q.customer_email, location=q.customer_location,
                   at=q.created_at, source="quote", purchased=(q.status == "accepted"))
            q.customer_id = c.id
            q_linked += 1

        db.commit()
        total = db.query(Customer).count()
        print(f"Customers: {total} total. Directorio rows {dir_rows}, "
              f"linked {wa_linked} WA conversations, {q_linked} quotes.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
