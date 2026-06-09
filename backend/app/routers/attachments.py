"""Per-part Drive attachments (M6): list, ensure folder, upload files."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
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
    _get_item(db, item_id)
    if not drive.enabled():
        return {"configured": False, "folder_url": None, "files": []}
    data = drive.list_files(item_id)
    return {"configured": True, **data}


@router.post("/items/{item_id}/attachments/folder")
def ensure_folder(item_id: str, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    _require_drive()
    item = _get_item(db, item_id)
    folder = drive.ensure_part_folder(item_id)
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
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
) -> dict:
    _require_drive()
    item = _get_item(db, item_id)
    content = await file.read()
    result = drive.upload_file(item_id, file.filename or "file", content, file.content_type)
    if item.drive_folder_url != result.get("folder_url"):
        item.drive_folder_url = result.get("folder_url")
    record_change(db, entity_type="item", entity_id=item_id, change_type="update",
                  field_changed="attachment", new_value=result["name"], changed_by=user,
                  change_reason="uploaded to Drive")
    db.commit()
    return {"configured": True, **result}
