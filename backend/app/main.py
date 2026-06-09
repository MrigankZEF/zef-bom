"""FastAPI application entrypoint for the ZEF BOM backend."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.include_router(meta.router)
app.include_router(items.router)
app.include_router(tree.router)
app.include_router(edit.router)
app.include_router(uploads.router)
app.include_router(admin.router)
app.include_router(attachments.router)
app.include_router(auth.router)


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


@app.get("/")
def root() -> dict:
    return {"service": "zef-bom", "docs": "/docs"}
