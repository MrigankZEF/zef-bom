"""Exercise the public file route through ASGI without starting the database scheduler."""
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main


def _get(path):
    async def request():
        messages = []
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": unquote(path), "raw_path": path.encode(), "query_string": b"",
            "root_path": "", "headers": [], "server": ("test", 80),
            "client": ("127.0.0.1", 1),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await main.app(scope, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in messages)
        return status, body

    return asyncio.run(request())


@pytest.fixture
def webapp(tmp_path, monkeypatch):
    public = tmp_path / "webapp"
    public.mkdir()
    (public / "index.html").write_text("public SPA", encoding="utf-8")
    (public / "logo.txt").write_text("public logo", encoding="utf-8")
    (tmp_path / "private.txt").write_text("private marker", encoding="utf-8")
    sibling = tmp_path / "webapp-private"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("private marker", encoding="utf-8")
    monkeypatch.setattr(main, "_WEBAPP", public)
    monkeypatch.setattr(main.settings, "google_oauth_client_id", "test-client")
    return public


@pytest.mark.parametrize("path", [
    "/%2e%2e/private.txt",
    "/%2e%2e/webapp-private/secret.txt",
    "/nested/%2e%2e/%2e%2e/private.txt",
])
def test_public_route_rejects_traversal(webapp, path):
    status, body = _get(path)
    assert status == 404
    assert b"private marker" not in body


@pytest.mark.skipif(os.name != "nt", reason="Backslashes are path separators on Windows")
def test_public_route_rejects_windows_traversal(webapp):
    assert _get("/%2e%2e%5cprivate.txt")[0] == 404


def test_public_route_rejects_absolute_paths(webapp):
    assert _get("/" + (webapp.parent / "private.txt").as_posix())[0] == 404


def test_public_files_and_spa_routes_still_work_without_login(webapp):
    assert _get("/logo.txt") == (200, b"public logo")
    assert _get("/browse/some-bom") == (200, b"public SPA")
    assert _get("/") == (200, b"public SPA")
    assert _get("/api/health")[0] == 401
