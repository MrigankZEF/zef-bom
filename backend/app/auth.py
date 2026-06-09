"""Auth: our own session JWTs + the shared `current_user` dependency.

Login (routers/auth.py) verifies a Google ID token and mints one of these JWTs.
`current_user` reads it from the Authorization header. In dev (no token), it falls
back to the X-User header / 'anonymous' so the app keeps working before login is set up.
"""
from __future__ import annotations

import time

import jwt
from fastapi import Header, HTTPException, Request

from .config import settings

_ALGO = "HS256"


def create_token(email: str, name: str | None, role: str) -> str:
    payload = {"sub": email, "name": name, "role": role, "exp": int(time.time()) + 7 * 24 * 3600}
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[_ALGO])


def current_user(
    authorization: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
) -> str:
    """Return the acting user's email (for attribution)."""
    if authorization and authorization.lower().startswith("bearer "):
        try:
            data = decode_token(authorization.split(" ", 1)[1])
        except Exception as exc:  # expired / tampered
            raise HTTPException(401, "Invalid or expired session") from exc
        return data.get("sub") or "anonymous"
    return x_user or "anonymous"


def current_claims(authorization: str | None = Header(default=None)) -> dict | None:
    """Full claims (email, name, role) when a valid token is present, else None."""
    if authorization and authorization.lower().startswith("bearer "):
        try:
            return decode_token(authorization.split(" ", 1)[1])
        except Exception:
            return None
    return None


def enforce_access(request: Request, authorization: str | None = Header(default=None)):
    """App-wide guard. Only the API (/api/*) is protected; the served frontend and
    the login endpoints (/api/auth/*) are public. When login is configured, every
    /api call must carry a valid session for an allowlisted user; viewers may read
    but not write. Dev (no client id) is unguarded."""
    from .db import SessionLocal
    from .models import User

    if not settings.google_oauth_client_id:
        return  # auth disabled → dev mode
    method = request.method
    if method == "OPTIONS":
        return  # CORS preflight
    path = request.url.path
    if not path.startswith("/api"):
        return  # the frontend (static assets + SPA) — public
    if path.startswith("/api/auth"):
        return  # login / auth-config — public

    claims = current_claims(authorization)
    if not claims:
        raise HTTPException(401, "Sign-in required")
    db = SessionLocal()
    try:
        user = db.get(User, claims.get("sub"))
    finally:
        db.close()
    if user is None:
        raise HTTPException(403, "Your account isn't authorized for the BOM tool. Ask an admin to add you.")
    if method in ("POST", "PUT", "PATCH", "DELETE") and user.role == "viewer":
        raise HTTPException(403, "You have view-only access — editing is disabled.")


def require_admin(authorization: str | None = Header(default=None)) -> str:
    """Dependency for admin-only endpoints (user management, reference lists, purge)."""
    if not settings.google_oauth_client_id:
        return "dev"  # dev mode
    from fastapi import HTTPException

    claims = current_claims(authorization)
    if not claims or claims.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    return claims.get("sub")
