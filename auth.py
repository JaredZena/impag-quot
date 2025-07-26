from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer
from google.oauth2 import id_token
from google.auth.transport import requests
import os

# Your Google OAuth Client ID
GOOGLE_CLIENT_ID = "223254458497-5tllach8urthlqtcau15sr35kaeicaqc.apps.googleusercontent.com"

# Allowed emails (your 3 users)
ALLOWED_EMAILS = {
    "zena.hernandez.010195@gmail.com",
    "user2@company.com",  # Replace with actual emails
    "user3@company.com"   # Replace with actual emails
}

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

        # Extract user email
        email = idinfo.get('email')
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")

        # Check if email is in allowed list
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