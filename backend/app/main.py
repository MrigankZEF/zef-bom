"""FastAPI application entrypoint for the ZEF BOM backend."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import enforce_access
from .config import settings
from .routers import admin, attachments, auth, catalog, edit, export, items, meta, tree, uploads

app = FastAPI(
    title="ZEF BOM API",
    version="0.1.0",
    summary="BOM / inventory / costing backend for the ZEF microplant.",
    dependencies=[Depends(enforce_access)],  # login + role guard (no-op in dev)
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All API routes live under /api so the guard protects exactly the API and the
# frontend is served from everything else.
for _r in (meta, items, tree, edit, uploads, admin, attachments, auth, export, catalog):
    app.include_router(_r.router, prefix="/api")


@app.on_event("startup")
def _startup() -> None:
    """Run DB migrations, then seed the first admin — so a fresh deploy is self-setup,
    regardless of the platform's start command."""
    # 1) migrate (idempotent): creates/updates all tables to head
    try:
        from alembic import command
        from alembic.config import Config

        backend_dir = Path(__file__).resolve().parent.parent
        cfg = Config(str(backend_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(backend_dir / "alembic"))
        command.upgrade(cfg, "head")
    except Exception as exc:  # don't take the whole app down; surface in logs
        print(f"[startup] migration error: {exc}")

    # 2) seed the first admin if the users table is empty
    if not settings.admin_email:
        return
    from .db import SessionLocal
    from .models import User

    db = SessionLocal()
    try:
        if db.query(User).first() is None:
            db.add(User(email=settings.admin_email.strip().lower(), role="admin"))
            db.commit()
    except Exception as exc:
        print(f"[startup] admin seed error: {exc}")
    finally:
        db.close()


def _monthly_backup_check() -> None:
    """Create this month's Drive snapshot if it's due (no-op when Drive isn't configured
    or one already exists for the month). Runs in a worker thread — blocking Drive I/O."""
    from .backup import monthly_backup_if_due
    from .db import SessionLocal

    db = SessionLocal()
    try:
        res = monthly_backup_if_due(db)
        if res.get("saved"):
            print(f"[backup] monthly snapshot saved: {res.get('name')} (trimmed {res.get('trimmed', 0)})")
    finally:
        db.close()


@app.on_event("startup")
async def _start_scheduler() -> None:
    """Daily heartbeat that takes the monthly Drive backup when due. In-process, so no
    extra infra; idempotent and restart-safe (it checks Drive for the month's snapshot)."""
    async def _loop() -> None:
        await asyncio.sleep(30)  # let startup/migrations settle first
        while True:
            try:
                await asyncio.to_thread(_monthly_backup_check)
            except Exception as exc:  # noqa: BLE001 — never crash the heartbeat
                print(f"[backup] scheduler error: {exc}")
            await asyncio.sleep(24 * 3600)  # check daily

    asyncio.create_task(_loop())


# ── serve the built React app (single-service deploy) ────────────────────────
# In production we drop the built frontend into backend/webapp/. FastAPI serves it
# from the same origin as the API, so there's no CORS or second service to wire.
_WEBAPP = Path(__file__).resolve().parent.parent / "webapp"
if (_WEBAPP / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(_WEBAPP / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        # API routes are matched before this catch-all; everything else is the SPA.
        candidate = _WEBAPP / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_WEBAPP / "index.html"))
else:
    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"service": "zef-bom", "docs": "/docs", "note": "frontend not built into backend/webapp"}
