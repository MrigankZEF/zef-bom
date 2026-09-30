"""The MCP connector: the protocol clients expect, and the read-only guarantee held in place
by something other than the HTTP verb."""
import json
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

from app import auth, db as database, mcp as engine
from app.db import Base
from app.models import ApiToken, AssemblyLabor, BomLink, DecidedCost, Item, ReferenceValue, User
from app.routers import mcp as transport


# ── the read-only guarantee ──────────────────────────────────────────────────────────────
def test_the_post_exemption_is_exactly_one_path():
    """MCP forced a hole in "a token may not POST". It must stay a single exact path: a
    prefix, or a second entry added casually, would reopen the whole API to tokens."""
    assert auth._TOKEN_POST_OK == frozenset({"/api/mcp"})


def test_every_exposed_tool_is_a_read():
    """The real guarantee now lives here rather than in the HTTP verb. Each tool must map to
    a handler registered as a GET, so a write cannot be exposed by wiring one in."""
    from app.main import app

    get_handlers = {
        r.endpoint for r in app.routes
        if "GET" in getattr(r, "methods", set()) and getattr(r, "endpoint", None)
    }
    wrapped = {
        "costing_summary": "costing_summary", "costing_breakdown": "costing_breakdown",
        "bom_tree": "get_tree", "flat_bom": "get_flat", "pending": "pending",
        "search_items": "list_items", "get_item": "get_item", "where_used": "where_used",
    }
    assert set(wrapped) == set(engine.TOOLS), "a tool was added without deciding it is a read"
    names = {h.__name__ for h in get_handlers}
    for tool, handler in wrapped.items():
        assert handler in names, f"{tool} wraps {handler}, which is not a GET endpoint"


def test_no_tool_name_suggests_a_write():
    forbidden = ("create", "update", "delete", "set_", "add_", "patch", "archive", "restore",
                 "import", "wipe", "revoke")
    for name in engine.TOOLS:
        assert not any(f in name for f in forbidden), name


# ── the guard, end to end ────────────────────────────────────────────────────────────────
def _req(method: str, path: str):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


@pytest.fixture
def guarded(monkeypatch):
    engine_ = create_engine("sqlite://", poolclass=StaticPool)
    User.__table__.create(engine_)
    ApiToken.__table__.create(engine_)
    sessions = sessionmaker(bind=engine_)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(auth.settings, "google_oauth_client_id", "test-client")
    monkeypatch.setattr(auth.settings, "secret_key", "isolated-test-secret-with-at-least-32-bytes")
    with sessions() as db:
        db.add(User(email="a@example.com", role="admin"))
        db.commit()
    secret, digest, prefix = auth.new_api_token()
    with sessions() as db:
        db.add(ApiToken(token_hash=digest, prefix=prefix, label="t", user_email="a@example.com",
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        db.commit()
    yield secret
    engine_.dispose()


def test_a_token_may_post_to_mcp(guarded):
    auth.enforce_access(_req("POST", "/api/mcp"), "Bearer " + guarded)


@pytest.mark.parametrize("path", ["/api/items/AEC066A", "/api/mcp/x", "/api/mcp2", "/api/bom"])
def test_a_token_still_cannot_post_anywhere_else(guarded, path):
    """Including paths that merely start with the exempt one."""
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req("POST", path), "Bearer " + guarded)
    assert e.value.status_code == 403


def test_mcp_still_needs_a_token(guarded):
    with pytest.raises(HTTPException) as e:
        auth.enforce_access(_req("POST", "/api/mcp"), None)
    assert e.value.status_code == 401


# ── the protocol ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def bom():
    """PLANT x1 -> PART x4 @ EUR 250, plus 90 minutes of assembly."""
    eng = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        db.add(Item(item_id="AEC900A", item_name="Plant", item_type="assembly",
                    module_code="AEC", is_top_level=True, cost_type_id=1))
        db.add(Item(item_id="AEC901P", item_name="Widget", item_type="part", module_code="AEC"))
        db.add(ReferenceValue(id=1, category="assembly_cost_type", value="Bench",
                              meta={"rate_eur_h": 60}))
        db.commit()
        db.add(BomLink(parent_item_id="AEC900A", child_item_id="AEC901P", quantity=4))
        db.add(DecidedCost(item_id="AEC901P", volume_tier=100, unit_cost_eur=250))
        db.add(AssemblyLabor(item_id="AEC900A", volume_tier=100, time_likely=90))
        db.commit()
        yield db
    eng.dispose()


def test_initialize_answers_what_a_client_needs(bom):
    r = transport._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, bom)
    assert r["result"]["protocolVersion"] == engine.PROTOCOL_VERSION
    assert "tools" in r["result"]["capabilities"]
    assert r["result"]["serverInfo"]["name"] == "zef-bom"


def test_a_notification_gets_no_reply(bom):
    """No `id` means no response. Answering makes strict clients drop the connection."""
    assert transport._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, bom) is None


def test_tools_list_describes_every_tool(bom):
    tools = transport._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bom)["result"]["tools"]
    assert {t["name"] for t in tools} == set(engine.TOOLS)
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_calling_a_tool_returns_the_real_number(bom):
    r = transport._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "costing_summary", "arguments": {"volume": 100}}}, bom)
    assert r["result"]["isError"] is False
    data = json.loads(r["result"]["content"][0]["text"])
    # 4 x EUR 250 of parts + 90 minutes at EUR 60/h = 1090.
    assert data["per_root"][0]["cost"] == pytest.approx(4 * 250 + 90 * 60 / 60)


def test_an_unknown_tool_is_refused(bom):
    r = transport._handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "delete_everything", "arguments": {}}}, bom)
    assert r["error"]["code"] == -32602


def test_a_missing_argument_says_which_one(bom):
    r = transport._handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                           "params": {"name": "costing_breakdown", "arguments": {}}}, bom)
    assert "root" in r["error"]["message"]


def test_an_unexpected_argument_is_refused(bom):
    """Arguments reach a real endpoint, so the accepted set is closed rather than ignored."""
    r = transport._handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                           "params": {"name": "pending", "arguments": {"drop_table": "items"}}}, bom)
    assert r["error"]["code"] == -32602
    assert "drop_table" in r["error"]["message"]


def test_a_failing_tool_reports_inside_a_successful_response(bom):
    """The model should see the failure and try something else, which it cannot do if the
    transport turns it into a protocol error."""
    r = transport._handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                           "params": {"name": "get_item", "arguments": {"item_id": "NOPE"}}}, bom)
    assert "error" not in r
    assert r["result"]["isError"] is True


def test_an_unknown_method_is_a_protocol_error(bom):
    r = transport._handle({"jsonrpc": "2.0", "id": 7, "method": "resources/list"}, bom)
    assert r["error"]["code"] == -32601
