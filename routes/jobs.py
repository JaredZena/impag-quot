"""
Machine-triggered background jobs (roadmap P2, Week 2).

These endpoints are for an EXTERNAL scheduler (AWS EventBridge Scheduler, GitHub
Actions cron, a cron pinger, …), NOT for humans — so they are guarded by a shared
secret header instead of the Google-OAuth dependency used everywhere else. The
guard is FAIL-CLOSED: if JOB_TRIGGER_SECRET is unset or the header doesn't match
exactly, the request is rejected. Nothing here sends WhatsApp messages; drafts
land in the human approval queue and stay behind WA_SENDING_ENABLED.
"""
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from models import get_db
from services.quote_followup import sweep_stale_quotes

router = APIRouter(prefix="/jobs", tags=["jobs"])


def verify_job_token(x_job_token: str = Header(default="")):
    """Constant-time shared-secret check. Fail-closed when unconfigured."""
    secret = os.getenv("JOB_TRIGGER_SECRET", "")
    if not secret or not hmac.compare_digest(x_job_token or "", secret):
        raise HTTPException(status_code=403, detail="Invalid or missing job token")
    return True


@router.post("/quote-followup", dependencies=[Depends(verify_job_token)])
def run_quote_followup(dry_run: bool = False, db: Session = Depends(get_db)):
    """Sweep stalled quotes → follow-up Tasks (+ approval-queue WA drafts)."""
    return sweep_stale_quotes(db, dry_run=dry_run)
