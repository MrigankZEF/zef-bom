"""Auth: our own session JWTs + the shared `current_user` dependency.

Login (routers/auth.py) verifies a Google ID token and mints one of these JWTs.
`current_user` reads it from the Authorization header. In dev (no token), it falls
back to the X-User header / 'anonymous' so the app keeps working before login is set up.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Header, HTTPException, Request

from . import oauth
from .config import settings

_ALGO = "HS256"

# API tokens announce themselves. A bearer value starting with this is looked up in the
# database rather than decoded as a JWT -- so the two kinds of credential can never be
# confused for one another, and a malformed token fails as a token, not as a broken JWT.
TOKEN_PREFIX = "zbt_"

# The single exception to "a token may not POST". MCP puts every request in a POST body,
# reads included, so a connector cannot work without it. Narrow on purpose -- one exact path,
# not a prefix -- and the read-only guarantee moves to where it can actually be enforced:
# `app.mcp.TOOLS` is that endpoint's entire surface and holds only GET handlers. A test
# asserts both halves: that this set stays a single path, and that no write reaches the tools.
_TOKEN_POST_OK = frozenset({"/api/mcp"})
_PREFIX_SHOWN = 12          # characters kept in the clear, for "which token is this?"


def new_api_token() -> tuple[str, str, str]:
    """`(secret, sha256, prefix)` -- the only place a token exists in the clear."""
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return secret, hash_api_token(secret), secret[:_PREFIX_SHOWN]


def hash_api_token(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _bearer(authorization: str | None) -> str | None:
    """The credential out of an Authorization header, read generously.

    Generous about the wrapping, never about the secret. Every form below still has to be a
    token this server issued and has not revoked; nothing here admits anyone who lacks one.

    The strictness was costing more than it bought. A connector's header is typed into a form
    by hand, and the ways to get it subtly wrong are invisible on screen: a non-breaking space
    pasted out of a web page, a tab, no space at all. Each produced a 401, which a client reads
    not as "that credential is malformed" but as "this server wants an OAuth sign-in" -- so it
    goes hunting for metadata that does not exist and reports the server as invalid. Three days
    of that traced to one character that cannot be seen.

    A bare token with no scheme is accepted too: `zbt_` is our own prefix, so a value starting
    with it is unambiguous, and refusing it only punishes someone for a formatting rule they
    cannot see they have broken.
    """
    if not authorization:
        return None
    head = authorization.strip()
    if head.lower().startswith("bearer"):
        rest = head[len("bearer"):].strip()     # any whitespace, or none at all
        if rest:
            return rest
    if head.startswith(TOKEN_PREFIX):           # the scheme omitted entirely
        return head
    return None


def api_token_user(secret: str):
    """The `User` an unexpired, unrevoked token belongs to, or None.

    Also stamps `last_used_at`, at most once a minute: knowing a token is live matters when
    deciding whether to revoke one, and a write on every read would cost more than it tells.
    """
    from .db import SessionLocal
    from .models import ApiToken, User

    digest = hash_api_token(secret)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.query(ApiToken).filter(ApiToken.token_hash == digest).one_or_none()
        if row is None or row.revoked_at is not None:
            return None
        expires = row.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)   # SQLite hands back naive datetimes
        if expires is not None and expires <= now:
            return None
        last = row.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or (now - last) > timedelta(minutes=1):
            row.last_used_at = now
            db.commit()
        return db.get(User, row.user_email)


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

    bearer = _bearer(authorization)
    if bearer and bearer.startswith(TOKEN_PREFIX):
        # An API token is read-only, whoever owns it. Checked BEFORE the token is even
        # looked up, so the rule holds for expired and revoked ones too and cannot be
        # widened by promoting the owner.
        if method not in ("GET", "HEAD") and path not in _TOKEN_POST_OK:
            raise HTTPException(403, "API tokens are read-only — editing needs a browser sign-in.")
        user = api_token_user(bearer)
        if user is None:
            raise HTTPException(401, "Invalid, expired or revoked API token")
        return

    # A pass from the outside authorization server, for connectors that cannot hold a static
    # token. Checked before our own sessions because the two are told apart by signature
    # algorithm, and an RS256 token is never one of ours.
    if bearer and oauth.configured() and oauth.looks_like_ours(bearer):
        try:
            issued = oauth.verify(bearer)
        except oauth.InvalidPass as exc:
            raise HTTPException(401, str(exc)) from exc
        db = SessionLocal()
        try:
            user = db.get(User, issued.email)
        finally:
            db.close()
        if user is None:
            raise HTTPException(
                403, f"{issued.email} isn't authorized for the BOM tool. Ask an admin to add you.")
        # Same rule as a static token, for the same reason: a connector reads, it does not
        # edit. Signing in through a provider is a way of proving who you are, not a wider
        # grant than the token route gives.
        if method not in ("GET", "HEAD") and path not in _TOKEN_POST_OK:
            raise HTTPException(403, "Connector access is read-only — editing needs a browser sign-in.")
        return

    claims = current_claims(authorization)
    if not claims:
        # On the connector path, a bare 401 is a dead end: the client cannot tell "show me a
        # token" from "sign in somewhere", so it guesses, hunts for metadata, and reports the
        # server as unreachable. RFC 9728 says where to look, and this header is how. Only on
        # /api/mcp, and only when a provider is configured -- everywhere else a 401 still
        # means what it has always meant.
        if path in _TOKEN_POST_OK and oauth.configured():
            base = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
            raise HTTPException(
                401, "Sign-in required",
                headers={"WWW-Authenticate":
                         f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'},
            )
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
    from .db import SessionLocal
    from .models import User

    claims = current_claims(authorization)
    if not claims or not claims.get("sub"):
        raise HTTPException(403, "Admin only")
    # Tokens identify the user; the current allowlist decides their permissions.
    # A role change or removal must take effect before a seven-day token expires.
    with SessionLocal() as db:
        user = db.get(User, claims["sub"])
        if user is None or user.role != "admin":
            raise HTTPException(403, "Admin only")
        return user.email
