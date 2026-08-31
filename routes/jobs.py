"""
Machine-triggered background jobs (roadmap P2, Week 2).

These endpoints are for an EXTERNAL scheduler (AWS EventBridge Scheduler, GitHub
Actions cron, a cron pinger, …), NOT for humans — so they are guarded by shared
secret headers instead of the Google-OAuth dependency used everywhere else.
EITHER header is accepted: X-Job-Token == JOB_TRIGGER_SECRET (dedicated job
secret) OR X-API-Key == STOREFRONT_API_KEY — the daily storefront GitHub Action
already holds that key for /sales/sync, and these jobs sit in the same
machine-to-machine trust tier. The guard is FAIL-CLOSED: if neither secret is
configured or neither header matches exactly, the request is rejected. Nothing
here sends WhatsApp messages; drafts land in the human approval queue and stay
behind WA_SENDING_ENABLED.
"""

import hmac
import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from models import get_db
from services.quote_followup import sweep_stale_quotes

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _api_key_matches(x_api_key: str | None) -> bool:
    """Same constant-time check as routes/sales.py — one key, one trust tier."""
    expected_key = os.getenv("STOREFRONT_API_KEY")
    if not expected_key or not x_api_key:
        return False
    # Compare as bytes: str compare_digest raises TypeError on non-ASCII input,
    # which would turn a garbage header into a 500 instead of a 401.
    return secrets.compare_digest(
        x_api_key.encode("utf-8", "replace"), expected_key.encode("utf-8")
    )


def verify_job_token(
    x_job_token: str = Header(default=""),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """Constant-time shared-secret check (either header). Fail-closed when
    unconfigured."""
    secret = os.getenv("JOB_TRIGGER_SECRET", "")
    if secret and hmac.compare_digest(x_job_token or "", secret):
        return True
    if _api_key_matches(x_api_key):
        return True
    raise HTTPException(status_code=403, detail="Invalid or missing job token")


@router.post("/quote-followup", dependencies=[Depends(verify_job_token)])
def run_quote_followup(dry_run: bool = False, db: Session = Depends(get_db)):
    """Sweep stalled quotes → follow-up Tasks (+ approval-queue WA drafts)."""
    return sweep_stale_quotes(db, dry_run=dry_run)
