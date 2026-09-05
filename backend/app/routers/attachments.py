"""Per-part Drive attachments (M6): list, ensure folder, upload files, item thumbnail."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from .. import drive
from ..auth import current_user
from ..db import get_db
from ..history import record_change
from ..models import Item

router = APIRouter(tags=["attachments"])


def _require_drive() -> None:
    if not drive.enabled():
        raise HTTPException(
            503,
            "Drive isn't configured yet. Set GOOGLE_SERVICE_ACCOUNT_FILE and "
            "DRIVE_ATTACHMENTS_ROOT_ID in backend/.env (see the setup guide).",
        )


def _get_item(db: Session, item_id: str) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    return item


@router.get("/items/{item_id}/attachments")
def list_attachments(item_id: str, db: Session = Depends(get_db)) -> dict:
    item = _get_item(db, item_id)
    if not drive.enabled():
        return {"configured": False, "folder_url": None, "files": []}
    # Locate by the stored folder (stable id) so a re-coded item still finds its attachments.
    data = drive.list_files(item_id, item.drive_folder_url)
    return {"configured": True, **data}


@router.post("/items/{item_id}/attachments/folder")
def ensure_folder(item_id: str, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    _require_drive()
    item = _get_item(db, item_id)
    folder = drive.ensure_part_folder(item_id, item.drive_folder_url)
    if item.drive_folder_url != folder["url"]:
        item.drive_folder_url = folder["url"]
        record_change(db, entity_type="item", entity_id=item_id, change_type="update",
                      field_changed="drive_folder_url", new_value=folder["url"], changed_by=user)
        db.commit()
    return {"configured": True, **folder}


@router.post("/items/{item_id}/attachments", status_code=201)
async def upload_attachment(
    item_id: str,
    file: UploadFile = File(...),
    rel_path: str = Form(default=""),  # sub-folder path for folder uploads, e.g. "membranes/typeA"
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
) -> dict:
    _require_drive()
    item = _get_item(db, item_id)
    content = await file.read()
    name = file.filename or "file"
    # Run the (blocking) Drive call OFF the event loop so one upload can't freeze the whole
    # server — and turn any Drive failure into a clean 502 instead of risking the worker.
    try:
        if rel_path.strip():
            result = await asyncio.to_thread(
                drive.upload_file_nested, item_id, rel_path, name, content, file.content_type,
                item.drive_folder_url)
        else:
            result = await asyncio.to_thread(
                drive.upload_file, item_id, name, content, file.content_type, item.drive_folder_url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Drive upload failed for '{name}': {exc}") from exc
    if item.drive_folder_url != result.get("folder_url"):
        item.drive_folder_url = result.get("folder_url")
    record_change(db, entity_type="item", entity_id=item_id, change_type="update",
                  field_changed="attachment", new_value=result["name"], changed_by=user,
                  change_reason="uploaded to Drive")
    db.commit()
    return {"configured": True, **result}


# ── thumbnail (one pinned image per item) ────────────────────────────────────
# Drive's thumbnailLink expires within hours and only works with our credentials, so the
# bytes are proxied here. Fetching them on every drawer open would mean a Drive round trip
# per render, hence a small in-process cache keyed by file id.
_THUMB_TTL = 10 * 60  # seconds
_thumb_cache: dict[str, tuple[float, bytes, str]] = {}


@router.put("/items/{item_id}/thumbnail")
def set_thumbnail(
    item_id: str,
    file_id: str | None = Body(default=None, embed=True),
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
) -> dict:
    """Pin a Drive file as this item's picture, or clear it with a null file_id."""
    item = _get_item(db, item_id)
    old = item.thumbnail_file_id
    new = (file_id or "").strip() or None
    if new != old:
        item.thumbnail_file_id = new
        record_change(db, entity_type="item", entity_id=item_id, change_type="update",
                      field_changed="thumbnail_file_id", old_value=old, new_value=new,
                      changed_by=user)
        db.commit()
    return {"item_id": item_id, "thumbnail_file_id": new}


@router.get("/items/{item_id}/thumbnail")
async def get_thumbnail(item_id: str, db: Session = Depends(get_db)) -> Response:
    item = _get_item(db, item_id)
    if not item.thumbnail_file_id:
        raise HTTPException(404, "No thumbnail pinned on this item")
    _require_drive()
    fid = item.thumbnail_file_id
    hit = _thumb_cache.get(fid)
    now = time.monotonic()
    if hit and now - hit[0] < _THUMB_TTL:
        return Response(content=hit[1], media_type=hit[2])
    # Blocking Drive calls go off the event loop, like the uploads above.
    got = await asyncio.to_thread(drive.thumbnail, fid)
    if got is None:
        raise HTTPException(404, "Drive has no thumbnail for that file")
    content, mime = got
    _thumb_cache[fid] = (now, content, mime)
    return Response(content=content, media_type=mime)
