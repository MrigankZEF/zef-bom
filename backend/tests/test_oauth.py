"""Passes from an outside authorization server.

Real RSA keys and real signatures throughout: a stubbed verifier would prove only that the
test agrees with itself, and the whole value of this module is that it refuses things.
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, db as database, oauth
from app.models import ApiToken, User

DOMAIN = "zef-test.eu.auth0.com"
AUDIENCE = "https://zef-bom.test/api"
ISSUER = f"https://{DOMAIN}/"


@pytest.fixture(scope="module")
def keys():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return k, pem


@pytest.fixture
def env(monkeypatch, keys):
    key, pem = keys
    eng = create_engine("sqlite://", poolclass=StaticPool)
    User.__table__.create(eng)
    ApiToken.__table__.create(eng)
    sessions = sessionmaker(bind=eng)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(auth.settings, "google_oauth_client_id", "test-client")
    monkeypatch.setattr(auth.settings, "secret_key", "isolated-test-secret-with-at-least-32-bytes")
    for mod in (oauth.settings, auth.settings):
        monkeypatch.setattr(mod, "auth0_domain", DOMAIN, raising=False)
        monkeypatch.setattr(mod, "auth0_audience", AUDIENCE, raising=False)
    # Hand the verifier our throwaway public key instead of fetching the real JWKS.
    monkeypatch.setattr(oauth, "_client",
                        lambda: SimpleNamespace(
                            get_signing_key_from_jwt=lambda t: SimpleNamespace(key=key.public_key())))
    with sessions() as db:
        db.add(User(email="m.sinha@zeroemissionfuels.com", role="admin"))
        db.commit()
    yield pem
    eng.dispose()


def mint(pem, *, email="m.sinha@zeroemissionfuels.com", aud=AUDIENCE, iss=ISSUER,
         exp=None, claim=oauth.EMAIL_CLAIM, alg="RS256"):
    body = {"sub": "auth0|abc123", "aud": aud, "iss": iss,
            "exp": exp if exp is not None else int(time.time()) + 3600,
            "iat": int(time.time()), "scope": "openid profile email"}
    if email is not None:
        body[claim] = email
    return jwt.encode(body, pem, algorithm=alg)


def _req(method="GET", path="/api/mcp"):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path),
                           base_url="https://zef-bom.test/")


# ── what a good pass does ────────────────────────────────────────────────────────────────
def test_a_valid_pass_is_accepted(env):
    auth.enforce_access(_req(), "Bearer " + mint(env))


def test_the_email_decides_who_you_are(env):
    p = oauth.verify(mint(env))
    assert p.email == "m.sinha@zeroemissionfuels.com"
    assert p.subject == "auth0|abc123"


# ── what it refuses, and why each one matters ────────────────────────────────────────────
def test_a_pass_for_another_api_is_refused(env):
    """The one that stops a token minted for some other service being replayed here."""
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req(), "Bearer " + mint(env, aud="https://something-else/api"))
    assert e.value.status_code == 401


def test_a_pass_from_another_provider_is_refused(env):
    with pytest.raises(HTTPException):
        auth.enforce_access(_req(), "Bearer " + mint(env, iss="https://attacker.example/"))


def test_an_expired_pass_is_refused(env):
    with pytest.raises(HTTPException):
        auth.enforce_access(_req(), "Bearer " + mint(env, exp=int(time.time()) - 60))


def test_a_pass_signed_by_the_wrong_key_is_refused(env):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
    with pytest.raises(HTTPException):
        auth.enforce_access(_req(), "Bearer " + mint(pem))


def test_an_unsigned_pass_is_refused(env):
    """`alg: none` is the oldest trick there is; only RS256 is ever accepted."""
    body = {"sub": "x", "aud": AUDIENCE, "iss": ISSUER, "exp": int(time.time()) + 60,
            oauth.EMAIL_CLAIM: "m.sinha@zeroemissionfuels.com"}
    with pytest.raises(HTTPException):
        auth.enforce_access(_req(), "Bearer " + jwt.encode(body, None, algorithm="none"))


def test_signing_in_is_not_the_same_as_being_allowed_in(env):
    """A perfectly valid pass for somebody not on the allowlist. The provider says who you
    are; this list says whether you may read the BOM."""
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req(), "Bearer " + mint(env, email="stranger@example.com"))
    assert e.value.status_code == 403


def test_a_pass_without_the_email_claim_says_how_to_fix_it(env):
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req(), "Bearer " + mint(env, email=None))
    assert oauth.EMAIL_CLAIM in e.value.detail


# ── the read-only rule applies here too ──────────────────────────────────────────────────
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_a_pass_cannot_write(env, method):
    """Its owner is an admin. Signing in through a provider is proof of identity, not a
    wider grant than the token route gives."""
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req(method, "/api/items/AEC066A"), "Bearer " + mint(env))
    assert e.value.status_code == 403
    assert "read-only" in e.value.detail


# ── nothing that worked before changes ───────────────────────────────────────────────────
def test_the_browser_session_is_untouched(env):
    tok = auth.create_token("m.sinha@zeroemissionfuels.com", "M", "admin")
    auth.enforce_access(_req(), "Bearer " + tok)
    auth.enforce_access(_req("POST", "/api/items/X"), "Bearer " + tok)


def test_a_static_api_token_still_works(env):
    secret, digest, prefix = auth.new_api_token()
    from datetime import datetime, timedelta, timezone
    with database.SessionLocal() as db:
        db.add(ApiToken(token_hash=digest, prefix=prefix, label="t",
                        user_email="m.sinha@zeroemissionfuels.com",
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        db.commit()
    auth.enforce_access(_req(), "Bearer " + secret)


def test_with_no_provider_configured_nothing_changes(env, monkeypatch):
    """Dev, and anyone not using Auth0, must be unaffected."""
    monkeypatch.setattr(oauth.settings, "auth0_domain", "")
    assert oauth.configured() is False
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req(), "Bearer " + mint(env))
    assert e.value.status_code == 401
