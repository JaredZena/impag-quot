#!/usr/bin/env python3
"""
Stalled-quote follow-up sweep — CLI trigger (roadmap P2, Week 2).

Runs the same sweep as the /jobs/quote-followup endpoint, but locally against the
prod DB — the low-friction path that matches how the other IMPAG scripts run.
Wire it to a nightly launchd/cron entry when ready.

Usage:
    python scripts/followup_stale_quotes.py            # dry-run: list candidates
    python scripts/followup_stale_quotes.py --commit   # create tasks + WA drafts
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import SessionLocal
from services.quote_followup import sweep_stale_quotes


def main(commit: bool):
    db = SessionLocal()
    try:
        s = sweep_stale_quotes(db, dry_run=not commit)
        print(f"{'COMMITTED' if commit else 'DRY-RUN'} — {s['candidates']} stale quote(s)")
        for it in s["items"]:
            wa = " +WA-draft" if it["will_create_wa_draft"] else ""
            state = "expired" if it.get("expired") else ("viewed" if it["viewed"] else "sent")
            print(f"  {it['quote_number']:16s} {(it['customer'] or '')[:28]:28s} "
                  f"{it['days_since_sent']}d {state} (nudge #{it['followup_count'] + 1}){wa}")
        if commit:
            print(f"  -> tasks: {s['tasks_created']}, WA drafts: {s['wa_drafts_created']}, "
                  f"errors: {s['errors']}")
        elif s["candidates"]:
            print("  (dry-run: nothing written; re-run with --commit)")
    finally:
        db.close()


if __name__ == "__main__":
    main(commit="--commit" in sys.argv)
