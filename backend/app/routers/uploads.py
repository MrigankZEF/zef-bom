"""Upload workflow (M5): drop a Miro OPML → review the diff → approve or reject.

Upload parses + diffs against the live DB and stores a pending batch (nothing is
written to items/links yet). Approve re-parses the saved file and applies it
atomically (new items, renames, link add/remove/qty), with full change_history.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..history import record_change
from ..models import BomLink, Item, UploadBatch
from ..bom_ingest.miro_csv_fix import BLOCKER_STATUSES
from ..bom_ingest.service import apply_incremental, build_incremental_diff, parse_opml
from ..bom_ingest.writer import _top_level_numbers
from ..operations import normalize_structure, promote_to_assembly, would_cycle

router = APIRouter(prefix="/uploads", tags=["uploads"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def _saved_path(batch_id: str) -> Path:
    return UPLOAD_DIR / f"{batch_id}.opml"


def _batch_out(b: UploadBatch) -> dict:
    return {
        "id": b.id,
        "source_filename": b.source_filename,
        "uploaded_at": b.uploaded_at.isoformat() if b.uploaded_at else None,
        "uploaded_by": b.uploaded_by,
        "notes": b.notes,
        "is_top_level_bom": b.is_top_level_bom,
        "status": b.status,
        "counts": (b.summary_json or {}).get("counts"),
    }


@router.get("")
def list_uploads(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(UploadBatch).order_by(UploadBatch.uploaded_at.desc())).scalars()
    return [_batch_out(b) for b in rows]


@router.get("/{batch_id}")
def get_upload(batch_id: str, db: Session = Depends(get_db)) -> dict:
    b = db.get(UploadBatch, batch_id)
    if b is None:
        raise HTTPException(404, "Upload batch not found")
    return {**_batch_out(b), "diff": b.summary_json}


@router.post("", status_code=201)
async def create_upload(
    file: UploadFile = File(...),
    notes: str | None = Form(default=None),
    is_top_level: bool = Form(default=False),
    attach_to: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
) -> dict:
    batch_id = f"ub-{uuid.uuid4().hex[:10]}"
    path = _saved_path(batch_id)
    path.write_bytes(await file.read())

    try:
        cells, _authority, name_matches = parse_opml(db, path)
    except Exception as exc:  # malformed OPML, etc.
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not parse OPML: {exc}") from exc

    diff = build_incremental_diff(db, cells, opml_path=path, name_matches=name_matches)
    if attach_to and not is_top_level:  # remember where this sub-BOM should hang
        diff["attach_to"] = attach_to.strip()
    blockers = sum(1 for c in cells if c.resolution_status in BLOCKER_STATUSES)

    batch = UploadBatch(
        id=batch_id,
        source_filename=file.filename or "upload.opml",
        uploaded_by=user,
        notes=notes,
        is_top_level_bom=is_top_level,
        status="pending_review",
        summary_json=diff,
    )
    db.add(batch)
    db.commit()
    return {**_batch_out(batch), "diff": diff, "blockers": blockers}


@router.post("/{batch_id}/approve")
def approve_upload(
    batch_id: str,
    body: dict | None = Body(default=None),
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
) -> dict:
    b = db.get(UploadBatch, batch_id)
    if b is None:
        raise HTTPException(404, "Upload batch not found")
    if b.status != "pending_review":
        raise HTTPException(409, f"Batch already {b.status}")
    path = _saved_path(batch_id)
    if not path.exists():
        raise HTTPException(410, "Uploaded file no longer available; re-upload to approve")

    # Per-row choices from the review screen:
    #   decisions = {reused_number: "new"|"rename"|"skip"}  (number collisions)
    #   reviews   = {node_text: {action, module, type, match}}  (needs-review items)
    decisions = (body or {}).get("decisions") or {}
    reviews = (body or {}).get("reviews") or {}
    cells, _authority, _name_matches = parse_opml(db, path, decisions, reviews)
    blockers = [c for c in cells if c.resolution_status in BLOCKER_STATUSES]
    if blockers:
        nums = ", ".join(sorted({c.explicit_item_number or c.cleaned_text for c in blockers})[:8])
        raise HTTPException(
            409,
            f"{len(blockers)} item(s) still need a choice before approval: {nums}",
        )

    applied = apply_incremental(
        db, cells, batch_id=batch_id, user=user, mark_top_level=b.is_top_level_bom
    )
    db.flush()

    # Attach a non-top-level import under the chosen parent (its sub-BOM roots hang there).
    attach_to = (b.summary_json or {}).get("attach_to")
    attached: list[str] = []
    if attach_to and not b.is_top_level_bom and db.get(Item, attach_to) is not None:
        for root_num in _top_level_numbers(cells):
            if root_num == attach_to or would_cycle(db, attach_to, root_num):
                continue  # skip self / loop-creating attachments
            existing = db.execute(
                select(BomLink).where(BomLink.parent_item_id == attach_to, BomLink.child_item_id == root_num)
            ).scalar_one_or_none()
            if existing is None:
                db.add(BomLink(parent_item_id=attach_to, child_item_id=root_num, quantity=1))
                record_change(db, entity_type="bom_link", entity_id=f"{attach_to}>{root_num}",
                              change_type="create", new_value="qty=1", changed_by=user,
                              change_reason=f"attached import {batch_id}")
                attached.append(root_num)
            elif existing.archived:
                existing.archived = False
                attached.append(root_num)
        db.flush()
        promote_to_assembly(db, attach_to, user=user)

    # Naming rules: types follow children (assembly ↔ part) and codes follow usage (system / UN).
    recodes = normalize_structure(db, user=user)
    b.status = "approved"
    db.commit()
    return {
        "batch_id": batch_id, "status": "approved", "applied": applied,
        "attached": attached, "recodes": recodes,
    }


@router.post("/{batch_id}/reject")
def reject_upload(batch_id: str, db: Session = Depends(get_db)) -> dict:
    b = db.get(UploadBatch, batch_id)
    if b is None:
        raise HTTPException(404, "Upload batch not found")
    b.status = "rejected"
    db.commit()
    return {"batch_id": batch_id, "status": "rejected"}
