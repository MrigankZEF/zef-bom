"""Role changes must apply to sessions that have already been issued."""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, db as database
from app.models import User


@pytest.fixture
def users(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    User.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(auth.settings, "google_oauth_client_id", "test-client")
    monkeypatch.setattr(auth.settings, "secret_key", "isolated-test-secret-with-at-least-32-bytes")
    with sessions() as db:
        db.add(User(email="admin@example.com", role="admin"))
        db.commit()
    yield sessions
    engine.dispose()


@pytest.mark.parametrize("new_role", ["editor", "viewer", None])
def test_existing_admin_token_loses_access_when_account_changes(users, new_role):
    token = auth.create_token("admin@example.com", "Admin", "admin")
    assert auth.require_admin("Bearer " + token) == "admin@example.com"
    with users() as db:
        user = db.get(User, "admin@example.com")
        if new_role is None:
            db.delete(user)
        else:
            user.role = new_role
        db.commit()
    with pytest.raises(HTTPException) as caught:
        auth.require_admin("Bearer " + token)
    assert caught.value.status_code == 403


def test_current_admin_role_wins_over_stale_token_role(users):
    token = auth.create_token("admin@example.com", "Admin", "editor")
    assert auth.require_admin("Bearer " + token) == "admin@example.com"


@pytest.mark.parametrize("authorization", [None, "Bearer invalid", "Basic invalid"])
def test_admin_requires_a_valid_session(users, authorization):
    with pytest.raises(HTTPException) as caught:
        auth.require_admin(authorization)
    assert caught.value.status_code == 403


def test_admin_guard_preserves_explicit_dev_mode(monkeypatch):
    monkeypatch.setattr(auth.settings, "google_oauth_client_id", "")
    assert auth.require_admin(None) == "dev"
