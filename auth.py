from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer
from google.oauth2 import id_token
from google.auth.transport import requests
import os

# Your Google OAuth Client ID
GOOGLE_CLIENT_ID = "223254458497-5tllach8urthlqtcau15sr35kaeicaqc.apps.googleusercontent.com"

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

security = HTTPBearer()

def verify_google_token(token = Depends(security)):
    """
    Verify Google OAuth token and check if user is authorized
    """
    try:
        # Verify the token with Google's servers
        idinfo = id_token.verify_oauth2_token(
            token.credentials,
            requests.Request(),
            GOOGLE_CLIENT_ID
        )

        # Extract user email (case-insensitive)
        email = idinfo.get('email').lower() if idinfo.get('email') else None
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")

        # Check if email is in allowed list (case-insensitive comparison)
        if email not in ALLOWED_EMAILS:
            raise HTTPException(
                status_code=403,
                detail=f"Email {email} not authorized to access this API"
            )

        # Return user info for use in endpoints
        return {
            "email": email,
            "name": idinfo.get('name'),
            "picture": idinfo.get('picture'),
            "user_id": idinfo.get('sub')
        }

    except ValueError as e:
        # Token verification failed
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        # Other errors
        raise HTTPException(status_code=401, detail="Token verification failed") 