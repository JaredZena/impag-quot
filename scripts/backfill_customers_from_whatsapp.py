#!/usr/bin/env python3
"""
Pull customers/contacts out of the imported WhatsApp 1:1 chats into the customer
master (roadmap P2 — Customer 360, WhatsApp source).

The historical WhatsApp corpus lives in file_metadata (category='whatsapp-chat'),
ONE row per chat — not in wa_conversation (that table only holds the live agent).
Each 1:1 chat's counterpart is a real contact: client, supplier or staff. This
folds those counterparts into the phone-keyed spine:

  * unsaved contact  -> sender is a phone  -> phone-keyed merge (dedups against the
                        Directorio import, enriches last_activity_at)
  * saved contact    -> sender is a name   -> name-keyed create/merge (phone NULL)

Only 1:1 chats yield a customer. Group chats (>1 non-self sender, e.g. the
Operaciones / Contabilidad / Pagos exports) are skipped — not a single customer.
Self ("Impag Tech") and the operator ("Jared Zena") are never treated as contacts.

display_name is set to the contact identity exactly as it appears in the chat
description, so the Customer-360 `description ILIKE` match surfaces the chat doc
for free. Idempotent; net-new rows are tagged source='whatsapp'.

Usage:
    python scripts/backfill_customers_from_whatsapp.py           # dry-run (no writes)
    python scripts/backfill_customers_from_whatsapp.py --commit  # persist
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import SessionLocal, Customer, FileMetadata
from services.whatsapp_parser import parse_whatsapp_chat
from scripts.backfill_customers import normalize_phone, _touch

# Identities that are us, not a customer (lowercased, whitespace-collapsed).
SELF_NAMES = {"impag tech", "impag", "impagtech", "jared zena", "jared"}
# Generic WhatsApp export placeholders — never a real contact name.
BLOCK_NAMES = {"chat", "whatsapp chat", "chat de whatsapp"}
DESC_RE = re.compile(r"^WhatsApp\s+(.+?):", re.IGNORECASE)


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").lstrip("~").strip())


def _is_phone(s):
    return len(re.sub(r"\D", "", s or "")) >= 10


def _is_self(s):
    return _clean(s).lower() in SELF_NAMES


def main(commit):
    db = SessionLocal()
    try:
        # phone -> Customer, and name_normalized -> Customer (first wins on dupes)
        by_phone, by_name = {}, {}
        for c in db.query(Customer).all():
            if c.phone_e164:
                by_phone[c.phone_e164] = c
            if c.name_normalized:
                by_name.setdefault(c.name_normalized, c)

        def get_by_phone(phone, display):
            c = by_phone.get(phone)
            if c is None:
                c = Customer(phone_e164=phone, source="whatsapp")
                db.add(c); db.flush()
                by_phone[phone] = c
                if c.name_normalized:
                    by_name.setdefault(c.name_normalized, c)
            return c

        def get_by_name(name):
            key = _clean(name).lower()[:200]
            c = by_name.get(key)
            if c is None:
                c = Customer(display_name=_clean(name)[:200], name_normalized=key,
                             source="whatsapp")
                db.add(c); db.flush()
                by_name[key] = c
            return c

        docs = (db.query(FileMetadata)
                .filter(FileMetadata.category == "whatsapp-chat",
                        FileMetadata.extracted_text.isnot(None))
                .all())

        stats = dict(docs=len(docs), groups=0, empty=0, self_only=0,
                     phone_new=0, phone_hit=0, name_new=0, name_hit=0, skipped_self=0)

        for d in docs:
            msgs = parse_whatsapp_chat(d.extracted_text)
            if not msgs:
                stats["empty"] += 1
                continue

            non_self = sorted({_clean(m["sender"]) for m in msgs
                               if m.get("sender") and not _is_self(m["sender"])})
            if not non_self:
                stats["self_only"] += 1
                continue
            if len(non_self) > 1:
                stats["groups"] += 1
                continue

            sender = non_self[0]
            desc_name = _clean((DESC_RE.match(d.description or "") or [None, None])[1]) or None

            # Prefer a real (non-phone) name for display; fall back to the phone string.
            name = next((x for x in (desc_name, sender)
                         if x and not _is_phone(x) and _clean(x).lower() not in BLOCK_NAMES), None)
            phone_raw = next((x for x in (sender, desc_name) if x and _is_phone(x)), None)
            if name and _is_self(name):
                stats["skipped_self"] += 1
                continue

            tss = [m["timestamp"] for m in msgs if m.get("timestamp")]
            first_ts, last_ts = (min(tss), max(tss)) if tss else (None, None)

            phone = normalize_phone(phone_raw) if phone_raw else None
            if phone:
                existed = phone in by_phone
                display = name or _clean(phone_raw)
                c = get_by_phone(phone, display)
                _touch(c, name=display, at=first_ts, source="whatsapp")
                _touch(c, at=last_ts)
                stats["phone_hit" if existed else "phone_new"] += 1
            elif name and len(name) >= 3:
                key = _clean(name).lower()[:200]
                existed = key in by_name
                c = get_by_name(name)
                _touch(c, name=name, at=first_ts, source="whatsapp")
                _touch(c, at=last_ts)
                stats["name_hit" if existed else "name_new"] += 1
            else:
                stats["self_only"] += 1  # nothing usable

        total = db.query(Customer).count()
        if commit:
            db.commit()
        else:
            db.rollback()

        print(f"{'COMMITTED' if commit else 'DRY-RUN (no writes)'} — "
              f"{stats['docs']} chat docs scanned")
        print(f"  1:1 contacts: +{stats['phone_new']} new by phone, "
              f"{stats['phone_hit']} matched existing by phone; "
              f"+{stats['name_new']} new by name, {stats['name_hit']} matched by name")
        print(f"  skipped: {stats['groups']} group chats, {stats['self_only']} self/empty, "
              f"{stats['empty']} unparseable, {stats['skipped_self']} self-as-contact")
        print(f"  customer total now: {total}"
              f"{' (rolled back)' if not commit else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    main(commit="--commit" in sys.argv)
