import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests
from google.oauth2 import id_token

# Your Google OAuth Client ID
GOOGLE_CLIENT_ID = (
    "223254458497-5tllach8urthlqtcau15sr35kaeicaqc.apps.googleusercontent.com"
)

# Local dev mode: skip auth entirely when DISABLE_AUTH=true
DISABLE_AUTH = os.getenv("DISABLE_AUTH", "").lower() == "true"


# Allowed emails from environment variable (no fallback)
def get_allowed_emails():
    emails_str = os.getenv("ALLOWED_EMAILS")
    if not emails_str:
        raise Exception("ALLOWED_EMAILS environment variable must be set")
    # Split by comma and clean whitespace
    emails = [email.strip().lower() for email in emails_str.split(",") if email.strip()]
    if not emails:
        raise Exception("ALLOWED_EMAILS must contain at least one valid email")
    return set(emails)


ALLOWED_EMAILS = get_allowed_emails()

security = HTTPBearer(auto_error=not DISABLE_AUTH)

DEV_USER = {
    "email": "dev@local.test",
    "name": "Dev User",
    "picture": None,
    "user_id": "dev-local",
}


# ==================== App sessions ====================
# Google ID tokens expire after one hour and nothing in the browser renewed
# them, so the team was sent back to the login screen every hour. After one
# Google sign-in the browser now exchanges its ID token for an app session
# token (POST /auth/session), HMAC-SHA256-signed with APP_SESSION_SECRET and
# valid APP_SESSION_TTL_DAYS (default 30). Every request still re-checks
# ALLOWED_EMAILS, so removing an email revokes access immediately; rotating
# the secret logs everyone out. No secret configured = feature off (Google ID
# tokens only, exactly as before).

SESSION_PREFIX = "impag1"
MIN_SECRET_LENGTH = 32


def _session_secret() -> bytes | None:
    secret = os.getenv("APP_SESSION_SECRET", "")
    return secret.encode() if len(secret) >= MIN_SECRET_LENGTH else None


def session_secret_configured() -> bool:
    return _session_secret() is not None


def _session_ttl_seconds() -> int:
    try:
        days = int(os.getenv("APP_SESSION_TTL_DAYS", "30"))
    except ValueError:
        days = 30
    return max(1, days) * 86400


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret: bytes, signing_input: str) -> str:
    digest = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    return _b64encode(digest)


def is_session_token(raw: str) -> bool:
    return raw.startswith(SESSION_PREFIX + ".")


def issue_session_token(user: dict, now: int | None = None) -> tuple[str, int]:
    """Sign an app session for an already-verified user. Returns (token, exp)."""
    secret = _session_secret()
    if secret is None:
        raise RuntimeError("APP_SESSION_SECRET is not configured")
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + _session_ttl_seconds()
    payload = {
        "email": user["email"],
        "name": user.get("name"),
        "picture": user.get("picture"),
        "sub": user.get("user_id"),
        "iat": issued_at,
        "exp": expires_at,
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{SESSION_PREFIX}.{body}"
    return f"{signing_input}.{_signature(secret, signing_input)}", expires_at


def verify_session_token(raw: str, now: int | None = None) -> dict:
    """User dict for a valid app session token; raises HTTPException otherwise."""
    invalid = HTTPException(status_code=401, detail="Sesión no válida")
    secret = _session_secret()
    parts = raw.split(".")
    if secret is None or len(parts) != 3 or parts[0] != SESSION_PREFIX:
        raise invalid
    expected = _signature(secret, f"{parts[0]}.{parts[1]}")
    if not hmac.compare_digest(parts[2].encode(), expected.encode()):
        raise invalid
    try:
        payload = json.loads(_b64decode(parts[1]))
    except ValueError:
        raise invalid from None
    if not isinstance(payload, dict):
        raise invalid
    current = int(time.time()) if now is None else now
    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or expires_at < current:
        raise HTTPException(status_code=401, detail="Sesión expirada")
    email = str(payload.get("email") or "").lower()
    if email not in ALLOWED_EMAILS:
        raise HTTPException(
            status_code=403, detail=f"Email {email} not authorized to access this API"
        )
    return {
        "email": email,
        "name": payload.get("name"),
        "picture": payload.get("picture"),
        "user_id": payload.get("sub"),
    }


def verify_google_token(
    token: HTTPAuthorizationCredentials | None = Depends(security),
):
    """
    Verify the bearer token — a Google ID token or an app session token — and
    check the user is allowlisted. When DISABLE_AUTH=true, returns a dev user.
    """
    if DISABLE_AUTH:
        return DEV_USER

    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if is_session_token(token.credentials):
        return verify_session_token(token.credentials)

    try:
        # Verify the token with Google's servers
        idinfo = id_token.verify_oauth2_token(
            token.credentials, requests.Request(), GOOGLE_CLIENT_ID
        )

        # Extract user email (case-insensitive)
        email = idinfo.get("email").lower() if idinfo.get("email") else None
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")

        # Check if email is in allowed list (case-insensitive comparison)
        if email not in ALLOWED_EMAILS:
            raise HTTPException(
                status_code=403,
                detail=f"Email {email} not authorized to access this API",
            )

        # Return user info for use in endpoints
        return {
            "email": email,
            "name": idinfo.get("name"),
            "picture": idinfo.get("picture"),
            "user_id": idinfo.get("sub"),
        }

    except HTTPException:
        # Keep the specific status (403 = not allowlisted) instead of
        # masking it as a generic 401 below.
        raise
    except ValueError:
        # Token verification failed
        raise HTTPException(status_code=401, detail="Invalid token") from None
    except Exception:
        # Other errors
        raise HTTPException(
            status_code=401, detail="Token verification failed"
        ) from None
