"""A read-only credential has to be read-only in every direction anyone might push on it."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, db as database
from app.models import ApiToken, User


def _req(method: str, path: str = "/api/tree"):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    User.__table__.create(engine)
    ApiToken.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(auth.settings, "google_oauth_client_id", "test-client")
    monkeypatch.setattr(auth.settings, "secret_key", "isolated-test-secret-with-at-least-32-bytes")
    with sessions() as db:
        db.add(User(email="admin@example.com", role="admin"))
        db.add(User(email="viewer@example.com", role="viewer"))
        db.commit()
    yield sessions
    engine.dispose()


def _mint(sessions, email="admin@example.com", days=30, revoked=False):
    secret, digest, prefix = auth.new_api_token()
    with sessions() as db:
        db.add(ApiToken(
            token_hash=digest, prefix=prefix, label="test", user_email=email,
            expires_at=datetime.now(timezone.utc) + timedelta(days=days),
            revoked_at=datetime.now(timezone.utc) if revoked else None,
        ))
        db.commit()
    return secret


def test_a_token_reads(env):
    auth.enforce_access(_req("GET"), "Bearer " + _mint(env))


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_token_never_writes_even_for_an_admin(env, method):
    """The owner here is an admin. The point of the rule is that it does not matter."""
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req(method), "Bearer " + _mint(env))
    assert e.value.status_code == 403
    assert "read-only" in e.value.detail


def test_the_token_secret_is_never_stored(env):
    secret = _mint(env)
    with env() as db:
        row = db.query(ApiToken).one()
    assert row.token_hash != secret
    assert secret not in (row.token_hash + row.prefix + row.label)
    # The prefix is short enough to identify a token and far too short to be one.
    assert secret.startswith(row.prefix) and len(row.prefix) < len(secret) / 2


@pytest.mark.parametrize("kind", ["revoked", "expired", "unknown", "tampered"])
def test_a_token_that_should_not_work_does_not(env, kind):
    if kind == "revoked":
        secret = _mint(env, revoked=True)
    elif kind == "expired":
        secret = _mint(env, days=-1)
    elif kind == "unknown":
        secret, _, _ = auth.new_api_token()
    else:
        secret = _mint(env) + "x"
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req("GET"), "Bearer " + secret)
    assert e.value.status_code == 401


def test_revoking_takes_effect_on_the_very_next_request(env):
    secret = _mint(env)
    auth.enforce_access(_req("GET"), "Bearer " + secret)     # works
    with env() as db:
        db.query(ApiToken).one().revoked_at = datetime.now(timezone.utc)
        db.commit()
    with pytest.raises(HTTPException):                        # and immediately does not
        auth.enforce_access(_req("GET"), "Bearer " + secret)


def test_a_token_cannot_reach_the_admin_endpoints(env):
    """`require_admin` guards the user list and `/admin/export`, which is a GET that hands
    back the WHOLE database. A read-only token must not open that door, or "read-only"
    would quietly mean "can download everything"."""
    with pytest.raises(HTTPException) as e:
        auth.require_admin("Bearer " + _mint(env))
    assert e.value.status_code == 403


def test_a_token_borrows_its_owners_account_and_dies_with_it(env):
    secret = _mint(env, email="viewer@example.com")
    auth.enforce_access(_req("GET"), "Bearer " + secret)
    with env() as db:
        db.delete(db.get(User, "viewer@example.com"))
        db.commit()
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req("GET"), "Bearer " + secret)
    assert e.value.status_code == 401


def test_api_tokens_are_not_in_the_backup(env):
    """Deliberate, and the reasoning is in backup.py: a restore must never resurrect a
    revoked credential. If someone adds the table to the backup, this test should make them
    argue for it rather than let it happen quietly."""
    from app.backup import BACKUP_SHEETS, RESTORE_ORDER

    assert ApiToken not in [m for _, m in BACKUP_SHEETS]
    assert ApiToken not in [m for _, m in RESTORE_ORDER]


def test_a_browser_session_still_works_exactly_as_before(env):
    """The new branch must be invisible to the existing path."""
    jwt_token = auth.create_token("admin@example.com", "Admin", "admin")
    auth.enforce_access(_req("GET"), "Bearer " + jwt_token)
    auth.enforce_access(_req("POST"), "Bearer " + jwt_token)
    assert auth.require_admin("Bearer " + jwt_token) == "admin@example.com"
