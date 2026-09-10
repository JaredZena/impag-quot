"""App sessions: exchange one Google sign-in for a long-lived session token.

- POST /auth/session   Authorization: Bearer <Google ID token>
                       -> {token, expires_at, email}

See auth.py for the token format and why it exists (Google ID tokens expire
hourly and the browser never renewed them). With APP_SESSION_SECRET unset the
endpoint answers 503 and the frontend keeps using the Google token.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from auth import (
    DEV_USER,
    DISABLE_AUTH,
    is_session_token,
    issue_session_token,
    security,
    session_secret_configured,
    verify_google_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/session")
def create_session(token: HTTPAuthorizationCredentials | None = Depends(security)):
    if not session_secret_configured():
        raise HTTPException(503, "Las sesiones largas no están configuradas")
    if DISABLE_AUTH:
        user = DEV_USER
    else:
        if token is None:
            raise HTTPException(401, "Not authenticated")
        if is_session_token(token.credentials):
            # Only a fresh Google sign-in opens a session: a session token
            # can't be used to extend itself forever.
            raise HTTPException(400, "Abre la sesión con tu cuenta de Google")
        user = verify_google_token(token)
    session_token, expires_at = issue_session_token(user)
    return {
        "success": True,
        "data": {
            "token": session_token,
            "expires_at": expires_at,
            "email": user["email"],
        },
        "error": None,
        "message": None,
    }
