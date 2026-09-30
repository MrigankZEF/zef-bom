"""The API's public surface, and the curated schema people point AI connectors at."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import main as app_main

SCHEMA = Path(__file__).resolve().parents[2] / "docs" / "api" / "zef-bom-readonly-openapi.json"


@pytest.fixture
def schema():
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def test_the_curated_schema_is_read_only(schema):
    """It is handed to third-party AI tools. A write verb slipping in would invite one to try
    -- the server would still refuse, but the schema should not describe a door that is not
    there."""
    methods = {m for v in schema["paths"].values() for m in v}
    assert methods == {"get"}, methods


def test_every_curated_path_still_exists(schema):
    """The committed schema is a copy, and copies drift. If an endpoint is renamed, this fails
    here rather than silently in somebody's GPT six weeks later."""
    live = {p for p, v in app_main.app.openapi()["paths"].items() if "get" in v}
    missing = sorted(set(schema["paths"]) - live)
    assert not missing, f"curated schema names endpoints the app no longer serves: {missing}"


def test_the_curated_schema_points_at_production(schema):
    assert schema["servers"][0]["url"] == "https://zef-bom.up.railway.app"


def test_docs_are_public_in_dev_and_closed_in_production(monkeypatch):
    """`/docs` and `/openapi.json` sit outside /api, so the login guard never covered them.
    In dev that is handy; in production it published the whole API map to anyone."""
    import importlib

    from app import config

    monkeypatch.setattr(config.settings, "google_oauth_client_id", "")
    dev = importlib.reload(app_main)
    assert dev.app.openapi_url == "/openapi.json" and dev.app.docs_url == "/docs"

    monkeypatch.setattr(config.settings, "google_oauth_client_id", "a-real-client-id")
    prod = importlib.reload(app_main)
    assert prod.app.openapi_url is None
    assert prod.app.docs_url is None
    assert prod.app.redoc_url is None

    monkeypatch.setattr(config.settings, "google_oauth_client_id", "")
    importlib.reload(app_main)     # leave the module as the rest of the suite expects it
