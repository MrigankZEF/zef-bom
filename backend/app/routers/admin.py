"""Archive/restore (soft-delete) + reference-data admin (suppliers/materials/countries/modules).

Nothing is ever hard-deleted: items and links carry an `archived` flag; reads exclude
archived rows; the Archive view lists them for restore. Reference values back the
dropdowns and are managed here via '+ add'.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth import current_user, require_admin
from ..db import get_db
from ..history import record_change
from ..models import (
    AssemblyLabor, BomLink, CostEvidence, DecidedCost, FieldValue, Item, ReferenceValue, User,
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
