#!/usr/bin/env python3
"""
Local Apoyos discovery runner — seed/test the AI discovery job against a
throwaway sqlite file, without touching the production database and without
going through the storefront's apoyos.yml GitHub Action loop.

Refuses to run unless DATABASE_URL already points at a sqlite file (checked
BEFORE models/config are imported, since importing them builds the real
engine and, for a non-sqlite URL, would touch it).

Usage:
    DATABASE_URL=sqlite:////tmp/apoyos.db CLAUDE_API_KEY=... \\
        python scripts/apoyos_discover_local.py \\
        [--topics estado:durango,nacional:ganaderia] [--export apoyos-export.json]

Without --topics, every topic the current "apoyos" settings would build is
run (all configured states/sectors/extra_queries + any recheck batches).
"""

import argparse
import json
import os
import sys
import time

if not os.getenv("DATABASE_URL", "").startswith("sqlite"):
    print(
        f"Refusing to run: DATABASE_URL must point at a sqlite file "
        f"(got: {os.getenv('DATABASE_URL')!r}). This script seeds/tests against "
        f"a throwaway local database — never run it against Neon.",
        file=sys.stderr,
    )
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm.attributes import flag_modified

from models import ApoyoRun, Base, SessionLocal, engine
from services import app_settings
from services.apoyos import ApoyosSettings, serialize_public
from services.apoyos_discovery import (
    _finalize_run,
    _now,
    _process_topic_job,
    build_topics,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--topics",
        help="comma-separated topic keys to run (e.g. estado:durango,nacional:ganaderia); "
        "default: every topic the current settings would build",
    )
    parser.add_argument(
        "--export",
        help="path to write the published-feed shape of every apoyo found "
        "(drafts are treated as publishable for this export)",
    )
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        settings = app_settings.read(db, "apoyos", ApoyosSettings)
        all_topics = build_topics(db, settings)

        if args.topics:
            wanted = {t.strip() for t in args.topics.split(",") if t.strip()}
            topics = [t for t in all_topics if t["key"] in wanted]
            missing = wanted - {t["key"] for t in topics}
            if missing:
                print(
                    f"Unknown topic key(s), skipping: {sorted(missing)}",
                    file=sys.stderr,
                )
            if not topics:
                print("No matching topics to run.", file=sys.stderr)
                sys.exit(1)
        else:
            topics = all_topics

        print(f"Running {len(topics)} topic(s): {[t['key'] for t in topics]}")

        run = ApoyoRun(
            trigger="manual",
            status="running",
            topics=topics,
            triggered_by="local-script",
            started_at=_now(),
        )
        db.add(run)
        db.commit()
        run_id = run.id

        # Synchronous, not through step()/services.background: this script
        # is a one-shot CLI, not a polled HTTP endpoint, so it calls the same
        # per-topic job function step() would submit to the thread pool,
        # directly and in order, on this one session.
        for i, key in enumerate([t["key"] for t in topics]):
            db.refresh(run)
            live_topics = run.topics or []
            idx = next((j for j, t in enumerate(live_topics) if t["key"] == key), None)
            if idx is None:
                continue
            live_topics[idx]["status"] = "running"
            live_topics[idx]["started_at"] = _now().isoformat()
            run.topics = live_topics
            flag_modified(run, "topics")
            db.commit()

            started = time.monotonic()
            _process_topic_job(run_id, key, settings)(db)
            elapsed = time.monotonic() - started

            db.refresh(run)
            topic = next((t for t in (run.topics or []) if t["key"] == key), {})
            print(
                f"  [{i + 1}/{len(topics)}] "
                f"{topic.get('key', '?'):22s} status={topic.get('status', '?'):8s} "
                f"found={topic.get('found', 0)} new={topic.get('new', 0)} "
                f"updated={topic.get('updated', 0)} cost=${topic.get('cost_usd', 0):.4f} "
                f"({elapsed:.1f}s)"
                + (f" error={topic.get('error')}" if topic.get("error") else "")
            )

        _finalize_run(db, run)
        db.refresh(run)
        print()
        print(
            f"Run {run.id} finished: status={run.status} found={run.found_count} "
            f"new={run.new_count} updated={run.updated_count} closed={run.closed_count} "
            f"cost=${float(run.cost_usd):.4f} web_searches={run.web_searches}"
        )

        if args.export:
            from models import Apoyo

            rows = (
                db.query(Apoyo)
                .filter(Apoyo.status.in_(("draft", "published")))
                .order_by(Apoyo.id.asc())
                .all()
            )
            payload = [serialize_public(r) for r in rows]
            with open(args.export, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            print(f"Exported {len(payload)} apoyo(s) to {args.export}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
