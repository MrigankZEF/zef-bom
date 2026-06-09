"""Sign in with Google: verify the Google ID token, upsert the user, return a session JWT."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import create_token, current_claims
from ..config import settings
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleLoginIn(BaseModel):
    credential: str  # the Google ID token from the frontend


@router.get("/config")
def auth_config() -> dict:
    """Lets the frontend know whether login is configured (and the client id)."""
    return {"enabled": bool(settings.google_oauth_client_id), "client_id": settings.google_oauth_client_id}


@router.post("/google")
def google_login(body: GoogleLoginIn, db: Session = Depends(get_db)) -> dict:
    if not settings.google_oauth_client_id:
        raise HTTPException(503, "Google login isn't configured (set GOOGLE_OAUTH_CLIENT_ID)")
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token

    try:
        info = id_token.verify_oauth2_token(body.credential, grequests.Request(), settings.google_oauth_client_id)
    except Exception as exc:
        raise HTTPException(401, "Invalid Google sign-in") from exc

    email = info.get("email")
    if not email or not info.get("email_verified", True):
        raise HTTPException(401, "Google account has no verified email")
    if settings.allowed_email_domain and not email.endswith("@" + settings.allowed_email_domain):
        raise HTTPException(403, f"Only @{settings.allowed_email_domain} accounts may sign in")

    name = info.get("name")
    user = db.get(User, email)
    if user is None:
        # Allowlist: only people an admin has added may sign in.
        raise HTTPException(
            403,
            "Your account isn't authorized for the BOM tool yet. Ask a BOM admin to add you.",
        )
    user.name = name or user.name
    user.last_login = datetime.now(timezone.utc)
    db.commit()

    return {"token": create_token(email, name, user.role), "email": email, "name": name, "role": user.role}


@router.get("/me")
def me(authorization: str | None = Header(default=None)) -> dict | None:
    return current_claims(authorization)
