"""The MCP endpoint: JSON-RPC 2.0 over one POST, at /api/mcp.

Under /api on purpose, so the existing app-wide guard covers it and a connector needs the
same token as everything else -- rather than a second door with its own idea of who may knock.

MCP puts every request, including reads, in a POST body, which collides with the rule that a
token may not POST. `auth.enforce_access` therefore exempts this ONE path by name, and the
read-only guarantee is kept where it belongs instead: `app.mcp.TOOLS` is the entire surface,
and every entry in it is a GET handler.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from .. import mcp as engine
from ..db import get_db

router = APIRouter(tags=["mcp"])


def _result(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle(body: dict, db: Session) -> dict | None:
    """One JSON-RPC message in, one response out -- or None for a notification.

    A notification (no `id`) must get no response at all; answering one makes strict clients
    drop the connection, which is a confusing way to find out.
    """
    req_id = body.get("id")
    method = body.get("method")
    is_notification = "id" not in body

    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": engine.PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": engine.SERVER_INFO,
            "instructions": (
                "Read-only access to the ZEF BOM and costing tool. Costs exist at three "
                "volumes (1, 100, 10000 plants per year); always say which one a figure is "
                "from. Coverage below 100% means the total is a floor, not a price."
            ),
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _result(req_id, {})

    if method == "tools/list":
        return _result(req_id, {"tools": engine.tool_list()})

    if method == "tools/call":
        params = body.get("params") or {}
        try:
            data = engine.call_tool(db, params.get("name", ""), params.get("arguments"))
        except engine.McpError as exc:
            return _error(req_id, exc.code, exc.message)
        except Exception as exc:
            # A failed tool is reported INSIDE a successful response with isError, not as a
            # protocol error: the model is meant to see what went wrong and try something
            # else, which it cannot do if the transport swallows it.
            return _result(req_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
        return _result(req_id, {
            "content": [{"type": "text", "text": json.dumps(data, default=str)}],
            "isError": False,
        })

    if is_notification:
        return None
    return _error(req_id, -32601, f"Method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)) -> Response:
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        return Response(json.dumps(_error(None, -32700, "Parse error")),
                        media_type="application/json", status_code=400)

    # A client may batch several messages in one array.
    if isinstance(body, list):
        out = [r for r in (_handle(m, db) for m in body if isinstance(m, dict)) if r is not None]
        if not out:
            return Response(status_code=202)
        return Response(json.dumps(out), media_type="application/json")

    if not isinstance(body, dict):
        return Response(json.dumps(_error(None, -32600, "Invalid Request")),
                        media_type="application/json", status_code=400)

    result = _handle(body, db)
    if result is None:
        return Response(status_code=202)   # notification: accepted, nothing to say
    return Response(json.dumps(result), media_type="application/json")


@router.get("/mcp")
def mcp_stream() -> Response:
    """The optional server-to-client stream. This server never speaks first, so it declines
    rather than holding a connection open that would carry nothing."""
    return Response(status_code=405, headers={"Allow": "POST"})
