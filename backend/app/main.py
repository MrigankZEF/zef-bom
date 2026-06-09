"""FastAPI application entrypoint for the ZEF BOM backend."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import enforce_access
from .config import settings
from .routers import admin, attachments, auth, edit, items, meta, tree, uploads

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
for _r in (meta, items, tree, edit, uploads, admin, attachments, auth):
    app.include_router(_r.router, prefix="/api")


@app.on_event("startup")
def _seed_admin() -> None:
    """On first boot with an empty users table, seed ADMIN_EMAIL as the first admin."""
    if not settings.admin_email:
        return
    from .db import SessionLocal
    from .models import User

    db = SessionLocal()
    try:
        if db.query(User).first() is None:
            db.add(User(email=settings.admin_email.strip().lower(), role="admin"))
            db.commit()
    except Exception:
        pass  # table may not exist yet if migrations haven't run
    finally:
        db.close()


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
