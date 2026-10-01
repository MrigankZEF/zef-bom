"""Accepting passes issued by an outside authorization server (Auth0).

Why this exists: ChatGPT, and any connector that cannot hold a static credential, can only
reach a server through OAuth -- it sends the person to a login desk and arrives carrying what
the desk gave them. We are the *resource* in that arrangement, never the desk: we issue
nothing, store no password, and keep no session. We check a signature and read an email.

Three things are verified on every request, and all three matter:

  * the signature, against the keys the issuer publishes -- so the pass is genuinely theirs
  * `aud`, so a pass minted for some *other* API cannot be replayed at this one
  * `iss` and expiry, so it is current and from the desk we expect

Then the email inside is matched against the same `users` allowlist the browser uses. Being
able to sign in at the desk is not access to the BOM; being on the allowlist is. Someone
removed there loses OAuth and browser access together, which is the point of not keeping a
second list.

Off unless configured. With no domain and audience set this module does nothing, so dev and
anyone running without Auth0 are unaffected.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from .config import settings

# The claim an Auth0 Action writes the email into. An access token minted for a custom API
# carries the internal user id and no email, so without this there is nothing to match the
# allowlist against. Named as a URL because Auth0 requires custom claims to be namespaced.
EMAIL_CLAIM = "https://zef-bom/email"

_ALGOS = ["RS256"]          # asymmetric only: we hold a public key, never a shared secret
_jwks: PyJWKClient | None = None
_jwks_for: str | None = None


def configured() -> bool:
    return bool(settings.auth0_domain and settings.auth0_audience)


def issuer() -> str:
    return f"https://{settings.auth0_domain}/"


def metadata_url() -> str:
    return f"https://{settings.auth0_domain}/.well-known/openid-configuration"


def _client() -> PyJWKClient:
    """One JWKS client, kept across requests.

    It caches the issuer's signing keys and re-fetches when it meets a key id it has not seen,
    which is what makes key rotation a non-event rather than an outage.
    """
    global _jwks, _jwks_for
    url = f"https://{settings.auth0_domain}/.well-known/jwks.json"
    if _jwks is None or _jwks_for != url:
        _jwks = PyJWKClient(url, cache_keys=True, lifespan=3600)
        _jwks_for = url
    return _jwks


@dataclass
class Pass:
    """A verified pass: who it is for, and what it was allowed to ask for."""

    email: str
    subject: str
    scopes: frozenset[str]
    expires_at: int


class InvalidPass(Exception):
    """Refused. The message is safe to show a caller -- it never quotes the token."""


def looks_like_ours(token: str) -> bool:
    """Is this an outside pass rather than one of our own session tokens?

    Our sessions are signed HS256 with a shared secret; these are RS256 against a published
    key. Reading the unverified header only to route between the two is safe: nothing is
    trusted on the strength of it, and both paths verify in full afterwards.
    """
    try:
        return jwt.get_unverified_header(token).get("alg") in _ALGOS
    except Exception:
        return False


def verify(token: str) -> Pass:
    if not configured():
        raise InvalidPass("This server is not set up to accept sign-ins from an outside provider")
    try:
        key = _client().get_signing_key_from_jwt(token).key
    except Exception as exc:
        raise InvalidPass("Could not check the signature against the provider's keys") from exc

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=_ALGOS,
            audience=settings.auth0_audience,   # minted for THIS api, not merely valid
            issuer=issuer(),
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidPass("That sign-in has expired — sign in again") from exc
    except jwt.InvalidAudienceError as exc:
        raise InvalidPass("That pass was issued for a different service") from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidPass("That pass came from an unexpected provider") from exc
    except Exception as exc:
        raise InvalidPass("That pass could not be verified") from exc

    email = (claims.get(EMAIL_CLAIM) or "").strip().lower()
    if not email:
        # Everything verified, but there is no one to be. Said plainly, because the fix is a
        # dashboard setting and not something the reader can guess from "unauthorized".
        raise InvalidPass(
            "The sign-in carried no email address. The provider needs a login action adding "
            f"the claim {EMAIL_CLAIM!r} to the access token."
        )

    raw = claims.get("scope") or ""
    return Pass(
        email=email,
        subject=claims["sub"],
        scopes=frozenset(raw.split()) if isinstance(raw, str) else frozenset(),
        expires_at=int(claims.get("exp") or time.time()),
    )
