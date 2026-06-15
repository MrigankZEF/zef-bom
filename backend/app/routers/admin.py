"""Archive/restore (soft-delete) + reference-data admin (suppliers/materials/countries/modules).

Nothing is ever hard-deleted: items and links carry an `archived` flag; reads exclude
archived rows; the Archive view lists them for restore. Reference values back the
dropdowns and are managed here via '+ add'.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import drive
from ..auth import current_user, require_admin
from ..backup import XLSX_MIME, build_backup_workbook, run_drive_backup
from ..db import get_db
from ..history import record_change
from ..models import (
    AssemblyLabor, BomLink, ChangeHistory, CostEvidence, DecidedCost, FieldValue, Item,
    ReferenceValue, UploadBatch, User,
)
from ..schemas import ReferenceIn, UserIn, UserRoleIn

router = APIRouter(tags=["admin"])

ROLES = {"admin", "editor", "viewer"}


# ── user management (admin only) ─────────────────────────────────────────────
@router.get("/users")
def list_users(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> list[dict]:
    rows = db.execute(select(User).order_by(User.role, User.email)).scalars()
    return [
        {"email": u.email, "name": u.name, "role": u.role,
         "last_login": u.last_login.isoformat() if u.last_login else None}
        for u in rows
    ]


@router.post("/users", status_code=201)
def add_user(body: UserIn, db: Session = Depends(get_db), admin: str = Depends(require_admin)) -> dict:
    email = body.email.strip().lower()
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {sorted(ROLES)}")
    u = db.get(User, email)
    if u is None:
        u = User(email=email, name=body.name, role=body.role)
        db.add(u)
    else:
        u.role = body.role
        if body.name:
            u.name = body.name
    db.commit()
    return {"email": u.email, "name": u.name, "role": u.role}


@router.patch("/users/{email}")
def set_user_role(email: str, body: UserRoleIn, db: Session = Depends(get_db), admin: str = Depends(require_admin)) -> dict:
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {sorted(ROLES)}")
    u = db.get(User, email)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.email == admin and body.role != "admin":
        raise HTTPException(409, "You can't demote yourself")
    u.role = body.role
    db.commit()
    return {"email": u.email, "role": u.role}


@router.delete("/users/{email}", status_code=204)
def remove_user(email: str, db: Session = Depends(get_db), admin: str = Depends(require_admin)) -> None:
    if email == admin:
        raise HTTPException(409, "You can't remove yourself")
    u = db.get(User, email)
    if u is not None:
        db.delete(u)
        db.commit()


# ── archive / restore items ──────────────────────────────────────────────────
@router.delete("/items/{item_id}")
def archive_item(item_id: str, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    item.archived = True
    record_change(db, entity_type="item", entity_id=item_id, change_type="remove",
                  field_changed="archived", old_value=False, new_value=True, changed_by=user,
                  change_reason="archived (soft-delete)")
    db.flush()
    from ..operations import normalize_structure  # parents lose this child → may demote A→P
    normalize_structure(db, user=user)
    db.commit()
    return {"item_id": item_id, "archived": True}


@router.post("/items/{item_id}/restore")
def restore_item(item_id: str, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    item.archived = False
    record_change(db, entity_type="item", entity_id=item_id, change_type="update",
                  field_changed="archived", old_value=True, new_value=False, changed_by=user,
                  change_reason="restored")
    db.flush()
    from ..operations import normalize_structure  # child is back → parent may re-promote P→A
    normalize_structure(db, user=user)
    db.commit()
    return {"item_id": item_id, "archived": False}


# ── archive / restore a single link (remove a child from one assembly) ───────
def _find_link(db: Session, parent: str, child: str) -> BomLink:
    link = db.execute(
        select(BomLink).where(BomLink.parent_item_id == parent, BomLink.child_item_id == child)
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(404, "Link not found")
    return link


@router.delete("/items/{parent_id}/links/{child_id}")
def archive_link(parent_id: str, child_id: str, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    from ..operations import normalize_structure, resolve_rename

    link = _find_link(db, parent_id, child_id)
    link.archived = True
    record_change(db, entity_type="bom_link", entity_id=f"{parent_id}>{child_id}", change_type="remove",
                  field_changed="archived", new_value=True, changed_by=user, change_reason="removed from assembly")
    db.flush()
    # parent may demote A→P (no children left); the removed subtree re-codes to its smaller usage
    changes = normalize_structure(db, user=user)
    db.commit()
    return {"parent": resolve_rename(changes, parent_id), "child": resolve_rename(changes, child_id), "archived": True}


@router.post("/items/{parent_id}/links/{child_id}/restore")
def restore_link(parent_id: str, child_id: str, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    link = _find_link(db, parent_id, child_id)
    link.archived = False
    record_change(db, entity_type="bom_link", entity_id=f"{parent_id}>{child_id}", change_type="update",
                  field_changed="archived", new_value=False, changed_by=user, change_reason="restored")
    db.commit()
    return {"parent": parent_id, "child": child_id, "archived": False}


# ── permanent delete (purge) — only allowed on already-archived rows ─────────
@router.delete("/items/{item_id}/purge")
def purge_item(item_id: str, db: Session = Depends(get_db), user: str = Depends(current_user),
               _: str = Depends(require_admin)) -> dict:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    if not item.archived:
        raise HTTPException(409, "Archive the item before permanently deleting it")
    record_change(db, entity_type="item", entity_id=item_id, change_type="remove",
                  field_changed="purged", old_value=item.item_name, new_value="permanently deleted",
                  changed_by=user, change_reason="purged from archive")
    # remove dependent rows first (FKs), then the item. change_history is kept as the trail.
    db.execute(delete(BomLink).where((BomLink.parent_item_id == item_id) | (BomLink.child_item_id == item_id)))
    db.execute(delete(CostEvidence).where(CostEvidence.item_id == item_id))
    db.execute(delete(DecidedCost).where(DecidedCost.item_id == item_id))
    db.execute(delete(FieldValue).where(FieldValue.item_id == item_id))
    db.execute(delete(AssemblyLabor).where(AssemblyLabor.item_id == item_id))
    db.delete(item)
    db.flush()
    from ..operations import normalize_structure
    normalize_structure(db, user=user)
    db.commit()
    return {"item_id": item_id, "deleted": True}


@router.delete("/items/{parent_id}/links/{child_id}/purge")
def purge_link(parent_id: str, child_id: str, db: Session = Depends(get_db), user: str = Depends(current_user),
               _: str = Depends(require_admin)) -> dict:
    link = _find_link(db, parent_id, child_id)
    if not link.archived:
        raise HTTPException(409, "Archive the link before permanently deleting it")
    record_change(db, entity_type="bom_link", entity_id=f"{parent_id}>{child_id}", change_type="remove",
                  field_changed="purged", new_value="permanently deleted", changed_by=user,
                  change_reason="purged from archive")
    db.delete(link)
    db.commit()
    return {"parent": parent_id, "child": child_id, "deleted": True}


# ── archive listing ──────────────────────────────────────────────────────────
@router.get("/archive")
def list_archive(db: Session = Depends(get_db)) -> dict:
    items = [
        {"item_id": it.item_id, "item_name": it.item_name, "item_type": it.item_type, "module_code": it.module_code}
        for it in db.execute(select(Item).where(Item.archived.is_(True)).order_by(Item.item_id)).scalars()
    ]
    names = {it.item_id: it.item_name for it in db.execute(select(Item)).scalars()}
    links = [
        {"parent": bl.parent_item_id, "child": bl.child_item_id,
         "parent_name": names.get(bl.parent_item_id), "child_name": names.get(bl.child_item_id),
         "quantity": bl.quantity}
        for bl in db.execute(select(BomLink).where(BomLink.archived.is_(True))).scalars()
    ]
    return {"items": items, "links": links}


# ── reference data (dropdown values) ─────────────────────────────────────────
@router.get("/reference")
def list_reference(db: Session = Depends(get_db), category: str | None = None) -> list[dict]:
    stmt = select(ReferenceValue).where(ReferenceValue.archived.is_(False))
    if category:
        stmt = stmt.where(ReferenceValue.category == category)
    stmt = stmt.order_by(ReferenceValue.category, ReferenceValue.sort_order, ReferenceValue.value)
    return [
        {"id": r.id, "category": r.category, "value": r.value, "label": r.label, "meta": r.meta}
        for r in db.execute(stmt).scalars()
    ]


@router.post("/reference", status_code=201)
def add_reference(body: ReferenceIn, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    existing = db.execute(
        select(ReferenceValue).where(ReferenceValue.category == body.category, ReferenceValue.value == body.value)
    ).scalar_one_or_none()
    if existing:
        if existing.archived:  # un-archive instead of duplicating
            existing.archived = False
            db.commit()
        return {"id": existing.id, "category": existing.category, "value": existing.value}
    ref = ReferenceValue(category=body.category, value=body.value, label=body.label, meta=body.meta)
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return {"id": ref.id, "category": ref.category, "value": ref.value}


@router.delete("/reference/{ref_id}")
def archive_reference(ref_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> dict:
    ref = db.get(ReferenceValue, ref_id)
    if ref is None:
        raise HTTPException(404, "Reference value not found")
    ref.archived = True
    db.commit()
    return {"id": ref_id, "archived": True}


# ── catalog import (ZEF inventory Excel → items + decided costs) ──────────────
# Required columns the importer reads from a sheet literally named "Sheet1".
CATALOG_REQUIRED_COLS = ("partnumber", "partname")


def _read_catalog_sheet(raw: bytes):
    """Open the uploaded workbook's 'Sheet1' as a DataFrame, or 400 with a clear reason."""
    import io

    import pandas as pd

    try:
        return pd.read_excel(io.BytesIO(raw), "Sheet1")
    except Exception as exc:  # noqa: BLE001 — surface any openpyxl/pandas failure verbatim
        raise HTTPException(400, f"Could not read the Excel (it must have a sheet named 'Sheet1'): {exc}") from exc


def _plan_catalog(df) -> dict:
    """Validate columns and parse rows WITHOUT touching the DB. Returns a plan with the
    valid rows to create, per-reason skip counts, and a small sample — used by both the
    dry-run preview and the real import (single source of truth, so they can't diverge)."""
    import pandas as pd

    from ..bom_ingest.miro_csv_fix import ITEM_NUMBER_RE, normalize_item_name

    def _num(v):
        return float(v) if pd.notna(v) else None

    columns = [str(c) for c in df.columns]
    missing = [c for c in CATALOG_REQUIRED_COLS if c not in df.columns]
    plan = {
        "columns_found": columns,
        "missing_required": missing,
        "rows": [],
        "will_create": 0,
        "with_10k_cost": 0,
        "skipped": 0,
        "skipped_reasons": {"bad_or_missing_code": 0, "missing_name": 0, "duplicate_in_file": 0},
        "sample": [],
    }
    if missing:
        return plan  # can't parse rows reliably without the key columns

    seen: set[str] = set()
    for _, row in df.iterrows():
        code = str(row.get("partnumber") or "").strip()
        m = ITEM_NUMBER_RE.match(code)
        name = str(row.get("partname") or "").strip()
        if not m:
            plan["skipped"] += 1
            plan["skipped_reasons"]["bad_or_missing_code"] += 1
            continue
        if not name:
            plan["skipped"] += 1
            plan["skipped_reasons"]["missing_name"] += 1
            continue
        if code in seen:
            plan["skipped"] += 1
            plan["skipped_reasons"]["duplicate_in_file"] += 1
            continue
        seen.add(code)
        rec = {
            "code": code,
            "name": normalize_item_name(name),
            "type": "assembly" if m.group("suffix") == "A" else "part",
            "module": m.group("module"),
            "avg": _num(row.get("avg")),
            "fmin": _num(row.get("Future_10k_min")),
            "fmax": _num(row.get("Future_10k_max")),
        }
        plan["rows"].append(rec)
        plan["will_create"] += 1
        if rec["avg"] is not None:
            plan["with_10k_cost"] += 1
        if len(plan["sample"]) < 8:
            plan["sample"].append({"code": code, "name": rec["name"], "type": rec["type"]})
    return plan


@router.post("/admin/import-catalog/preview")
async def import_catalog_preview(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
) -> dict:
    """Dry-run (admin only): validate the Excel and report exactly what a real import would
    do — how many items it would create, how many it would skip and why, and how many
    existing items would be wiped. Touches nothing in the database."""
    plan = _plan_catalog(_read_catalog_sheet(await file.read()))
    current = db.scalar(select(func.count()).select_from(Item)) or 0
    return {
        "ok": not plan["missing_required"] and plan["will_create"] > 0,
        "missing_required": plan["missing_required"],
        "columns_found": plan["columns_found"],
        "will_create": plan["will_create"],
        "with_10k_cost": plan["with_10k_cost"],
        "skipped": plan["skipped"],
        "skipped_reasons": plan["skipped_reasons"],
        "sample": plan["sample"],
        "current_item_count": current,
    }


@router.post("/admin/import-catalog")
async def import_catalog(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
) -> dict:
    """DESTRUCTIVE (admin only): wipe all BOM data and seed the catalog fresh from a ZEF
    inventory Excel. Keeps users + reference lists (suppliers/materials/cost types).
    Validates the sheet first and REFUSES to wipe if the format is wrong or nothing parses."""
    plan = _plan_catalog(_read_catalog_sheet(await file.read()))

    # Guard: never wipe on a malformed/unrecognised sheet (e.g. renamed columns).
    if plan["missing_required"]:
        raise HTTPException(
            400,
            f"Excel is missing required column(s): {', '.join(plan['missing_required'])}. "
            f"Columns found: {', '.join(plan['columns_found']) or '(none)'}. "
            "Nothing was changed.",
        )
    if not plan["rows"]:
        raise HTTPException(
            400,
            f"No valid rows found to import ({plan['skipped']} skipped) — refusing to wipe the "
            "database. Check the sheet format and column names. Nothing was changed.",
        )

    # Auto-backup BEFORE wiping, so this destructive action is reversible. Never let a
    # backup failure block the import — but always report whether one was saved.
    pre_backup = run_drive_backup(db, reason="prewipe")

    # wipe BOM data — keep users and reference values
    for model in (ChangeHistory, BomLink, DecidedCost, CostEvidence, AssemblyLabor, FieldValue, UploadBatch):
        db.execute(delete(model))
    db.execute(delete(Item))
    db.flush()

    created = costed = 0
    for rec in plan["rows"]:
        db.add(Item(
            item_id=rec["code"], item_name=rec["name"], item_type=rec["type"],
            module_code=rec["module"], is_top_level=False,
            created_by=admin, updated_by=admin,
        ))
        created += 1
        if rec["avg"] is not None:
            db.add(DecidedCost(
                item_id=rec["code"], volume_tier=10000, unit_cost_eur=rec["avg"],
                cost_min=rec["fmin"], cost_max=rec["fmax"], decided_by=admin,
            ))
            costed += 1
    db.commit()
    return {"wiped": True, "items_created": created, "with_10k_cost": costed,
            "skipped": plan["skipped"], "backup": pre_backup}


# ── full database backup (every table → one .xlsx workbook) ───────────────────
@router.get("/admin/export")
def export_backup(db: Session = Depends(get_db), admin: str = Depends(require_admin)) -> Response:
    """Download a full database backup: one .xlsx workbook with a sheet per table
    (items, BOM links, costs, labor, fields, reference, uploads, history, users)."""
    import datetime as _dt

    data = build_backup_workbook(db)
    stamp = _dt.date.today().isoformat()
    return Response(
        content=data,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="zef-bom-backup-{stamp}.xlsx"'},
    )


@router.post("/admin/backup-to-drive")
def backup_to_drive(db: Session = Depends(get_db), admin: str = Depends(require_admin)) -> dict:
    """Build a snapshot now and upload it to the Drive Backups folder."""
    if not drive.enabled():
        raise HTTPException(503, "Google Drive isn't configured, so server-side backups are unavailable. "
                                 "Use 'Backup now' to download a copy instead.")
    result = run_drive_backup(db, reason="manual")
    if not result.get("saved"):
        raise HTTPException(502, f"Backup to Drive failed: {result.get('error') or result.get('reason')}")
    return result


@router.get("/admin/backups")
def list_drive_backups(admin: str = Depends(require_admin)) -> dict:
    """List the snapshots in the Drive Backups folder (newest first)."""
    if not drive.enabled():
        return {"enabled": False, "backups": []}
    try:
        files = drive.list_backups()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not list Drive backups: {exc}") from exc
    return {"enabled": True, "backups": [
        {"name": f.get("name"), "url": f.get("webViewLink"), "created": f.get("createdTime"),
         "size": f.get("size")} for f in files
    ]}
