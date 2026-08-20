"""Mutation endpoints (M4): edit items, manage cost evidence + decided cost,
custom field values. Every write appends to change_history.

Auth is deferred to M6; for now the editor identity comes from an optional
`X-User` header (falls back to 'anonymous').
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..bom_ingest.miro_csv_fix import ITEM_NUMBER_RE
from ..operations import allowed_modules, set_module

from ..auth import current_user
from ..db import get_db
from ..history import record_change
from ..models import (
    AssemblyLabor, BomLink, ChangeHistory, CostEvidence, DecidedCost, FieldDefinition, FieldValue, Item,
)
from ..schemas import (
    AddChildIn,
    AssemblyLaborIn,
    AssemblyLaborOut,
    ChangeHistoryOut,
    CostEvidenceIn,
    CostEvidenceOut,
    CreateBomIn,
    DecidedCostIn,
    DecidedCostOut,
    FieldValueIn,
    ItemOut,
    ItemPatch,
    MoveLinkIn,
    UpdateLinkIn,
)

router = APIRouter(tags=["edit"])


def _get_item(db: Session, item_id: str) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, f"Item {item_id} not found")
    return item


# ── items ────────────────────────────────────────────────────────────────────
@router.patch("/items/{item_id}", response_model=ItemOut)
def patch_item(
    item_id: str,
    body: ItemPatch,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
) -> Item:
    item = _get_item(db, item_id)
    fields = body.model_dump(exclude_unset=True)
    reason = fields.pop("change_reason", None)
    for field, new_value in fields.items():
        old_value = getattr(item, field)
        if old_value == new_value:
            continue
        setattr(item, field, new_value)
        record_change(
            db, entity_type="item", entity_id=item_id, change_type="update",
            field_changed=field, old_value=old_value, new_value=new_value,
            changed_by=user, change_reason=reason,
        )
    item.updated_by = user
    db.commit()
    db.refresh(item)
    return item


@router.get("/items/{item_id}/module-options")
def module_options(item_id: str, db: Session = Depends(get_db)) -> dict:
    """Modules this item may be set to in the add/edit dropdown (UN, UNP, its current
    module, and its parent assemblies' modules)."""
    _get_item(db, item_id)
    m = ITEM_NUMBER_RE.match(item_id)
    return {"current": m.group("module") if m else None, "options": allowed_modules(db, item_id)}


@router.post("/items/{item_id}/module")
def change_module(
    item_id: str,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
) -> dict:
    """Manually re-code an item to a new module (UN/UNP or a parent system). Atomic: every
    reference is repointed and the catalog updates. A clashing number is auto-reallocated."""
    _get_item(db, item_id)
    try:
        new_id = set_module(db, item_id, (body or {}).get("module", ""), user=user)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"item_id": new_id}


@router.post("/items/{item_id}/promote")
def promote_item(item_id: str, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    """Convert a part into an assembly (…P → …A, retype), repointing all references.
    Used when a part gains children (the P→A naming rule)."""
    from ..operations import promote_to_assembly

    _get_item(db, item_id)
    new_id = promote_to_assembly(db, item_id, user=user)
    db.commit()
    return {"old_id": item_id, "new_id": new_id, "item_type": "assembly"}


@router.post("/items/{parent_id}/children")
def add_child(
    parent_id: str, body: AddChildIn, db: Session = Depends(get_db), user: str = Depends(current_user)
) -> dict:
    """Add an existing catalog item as a child of `parent_id`. The parent becomes an
    assembly (P→A) and the child is re-coded to match its new usage (system code, or UN
    if now used across systems)."""
    from ..operations import normalize_structure, resolve_rename, would_cycle

    _get_item(db, parent_id)
    _get_item(db, body.child_id)
    if would_cycle(db, parent_id, body.child_id):
        raise HTTPException(409, f"Can't add {body.child_id} under {parent_id} — it would create a loop")
    link = db.execute(
        select(BomLink).where(BomLink.parent_item_id == parent_id, BomLink.child_item_id == body.child_id)
    ).scalar_one_or_none()
    if link is not None and not link.archived:
        raise HTTPException(409, f"{body.child_id} is already under {parent_id}")
    if link is not None:
        link.archived = False
        link.quantity = body.quantity
    else:
        db.add(BomLink(parent_item_id=parent_id, child_item_id=body.child_id, quantity=body.quantity))
    record_change(
        db, entity_type="bom_link", entity_id=f"{parent_id}>{body.child_id}", change_type="create",
        new_value=f"qty={body.quantity}", changed_by=user, change_reason="added from catalog",
    )
    db.flush()
    # parent → assembly, added subtree re-coded to its new usage (and any cascade)
    changes = normalize_structure(db, user=user)
    db.commit()
    return {
        "parent_id": resolve_rename(changes, parent_id),
        "child_id": resolve_rename(changes, body.child_id),
        "quantity": body.quantity,
    }


@router.patch("/items/{parent_id}/children/{child_id}")
def set_child_quantity(
    parent_id: str, child_id: str, body: UpdateLinkIn,
    db: Session = Depends(get_db), user: str = Depends(current_user),
) -> dict:
    """Change how many of `child_id` sit in `parent_id`.

    Deliberately does NOT run `normalize_structure`: quantity changes no structure, so no
    re-code or P<->A promotion can be triggered. That is the whole point of this endpoint —
    the old workaround (remove the child, re-add it with a new quantity) went through the
    naming engine twice and could hand the part a different code on the way back."""
    link = db.execute(
        select(BomLink).where(BomLink.parent_item_id == parent_id, BomLink.child_item_id == child_id)
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(404, f"{child_id} is not under {parent_id}")
    if link.archived:
        raise HTTPException(409, f"{child_id} was removed from {parent_id} — restore it before setting a quantity")
    old = link.quantity
    if old != body.quantity:
        link.quantity = body.quantity
        record_change(
            db, entity_type="bom_link", entity_id=f"{parent_id}>{child_id}", change_type="update",
            field_changed="quantity", old_value=old, new_value=body.quantity,
            changed_by=user, change_reason="quantity edited",
        )
        db.commit()
    return {"parent_id": parent_id, "child_id": child_id, "quantity": body.quantity}


@router.post("/bom")
def create_bom(body: CreateBomIn, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    """Start a new top-level BOM from scratch (no Miro import): a fresh assembly root in the
    chosen system. Children are then added with the normal add-child flow, so all the naming
    rules apply. A BOM root is a *system*, so a universal (UN/UNP) isn't allowed here."""
    import re
    from ..operations import allocate_code, clear_item_refs

    name = (body.item_name or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    module = (body.module or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}", module):
        raise HTTPException(400, f"Invalid system code '{module}' — use 2–5 letters (e.g. AEC, DAC, MDAC).")
    if module in ("UN", "UNP"):
        raise HTTPException(400, "A BOM root must be a system (e.g. AEC, DAC), not a universal (UN/UNP).")

    code = allocate_code(db, module, "A")  # a BOM root is an assembly
    clear_item_refs(db, code)  # a fresh code must not inherit stale cost/link rows
    db.add(Item(
        item_id=code, item_name=name, item_type="assembly", module_code=module,
        is_top_level=True, created_by=user, updated_by=user,
    ))
    record_change(db, entity_type="item", entity_id=code, change_type="create",
                  new_value=name, changed_by=user, change_reason=f"new top-level BOM ({module})")
    db.commit()
    return {"item_id": code, "item_name": name, "item_type": "assembly", "module_code": module, "is_top_level": True}


@router.post("/items/{child_id}/move")
def move_item(
    child_id: str, body: MoveLinkIn, db: Session = Depends(get_db), user: str = Depends(current_user)
) -> dict:
    """Move ONE placement of a part: detach it from `from_parent` and attach it under
    `to_parent`, within the same BOM. Other usages of the part are untouched. Cycle-checked;
    the naming engine then re-codes everything to its new usage."""
    from ..operations import containing_roots, normalize_structure, resolve_rename, would_cycle

    _get_item(db, child_id)
    from_parent = (body.from_parent or "").strip()
    to_parent = (body.to_parent or "").strip()
    _get_item(db, from_parent)
    _get_item(db, to_parent)
    if to_parent == from_parent:
        raise HTTPException(400, "Pick a different assembly to move it to.")
    if to_parent == child_id:
        raise HTTPException(409, "An item can't be moved into itself.")

    # The live link we're moving must exist.
    link = db.execute(
        select(BomLink).where(BomLink.parent_item_id == from_parent,
                              BomLink.child_item_id == child_id, BomLink.archived.is_(False))
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(404, f"{child_id} isn't currently under {from_parent}.")

    # Same BOM only — a cross-BOM move is just an "add it there" (keeps this simple/safe).
    if not (containing_roots(db, from_parent) & containing_roots(db, to_parent)):
        raise HTTPException(409, f"{to_parent} is in a different BOM — add {child_id} there instead of moving it.")
    if would_cycle(db, to_parent, child_id):
        raise HTTPException(409, f"Can't move {child_id} under {to_parent} — it would create a loop.")

    qty = body.quantity if body.quantity is not None else link.quantity

    # Detach from the old parent (soft, recoverable), attach to the new one.
    link.archived = True
    record_change(db, entity_type="bom_link", entity_id=f"{from_parent}>{child_id}", change_type="remove",
                  field_changed="archived", old_value=f"qty={link.quantity}", new_value=True,
                  changed_by=user, change_reason=f"moved to {to_parent}")
    existing = db.execute(
        select(BomLink).where(BomLink.parent_item_id == to_parent, BomLink.child_item_id == child_id)
    ).scalar_one_or_none()
    if existing is not None:
        existing.archived = False
        existing.quantity = qty
    else:
        db.add(BomLink(parent_item_id=to_parent, child_item_id=child_id, quantity=qty))
    record_change(db, entity_type="bom_link", entity_id=f"{to_parent}>{child_id}", change_type="create",
                  new_value=f"qty={qty}", changed_by=user, change_reason=f"moved from {from_parent}")
    db.flush()

    # Types + codes follow the new structure: to_parent gains a child (→ assembly), from_parent
    # may lose its last child (→ part), and the moved subtree re-codes to its new system/UN.
    changes = normalize_structure(db, user=user)
    db.commit()
    return {
        "child_id": resolve_rename(changes, child_id),
        "from_parent": resolve_rename(changes, from_parent),
        "to_parent": resolve_rename(changes, to_parent),
        "quantity": qty,
        "recodes": changes,
    }


# ── cost evidence ────────────────────────────────────────────────────────────
@router.get("/items/{item_id}/cost-evidence", response_model=list[CostEvidenceOut])
def list_cost_evidence(item_id: str, db: Session = Depends(get_db)) -> list[CostEvidence]:
    _get_item(db, item_id)
    return list(
        db.execute(
            select(CostEvidence).where(CostEvidence.item_id == item_id).order_by(CostEvidence.volume_tier)
        ).scalars()
    )


@router.post("/items/{item_id}/cost-evidence", response_model=CostEvidenceOut, status_code=201)
def add_cost_evidence(
    item_id: str, body: CostEvidenceIn, db: Session = Depends(get_db), user: str = Depends(current_user)
) -> CostEvidence:
    _get_item(db, item_id)
    data = body.model_dump(exclude={"change_reason"})
    ev = CostEvidence(item_id=item_id, created_by=user, **data)
    db.add(ev)
    db.flush()
    record_change(
        db, entity_type="cost_evidence", entity_id=item_id, change_type="create",
        field_changed="cost_evidence",
        new_value=f"{ev.source_type} {ev.unit_cost} {ev.currency} @ {ev.volume_tier}",
        changed_by=user, change_reason=body.change_reason,
    )
    db.commit()
    db.refresh(ev)
    return ev


@router.delete("/items/{item_id}/cost-evidence/{evidence_id}", status_code=204)
def delete_cost_evidence(
    item_id: str, evidence_id: int, db: Session = Depends(get_db), user: str = Depends(current_user)
) -> None:
    ev = db.get(CostEvidence, evidence_id)
    if ev is None or ev.item_id != item_id:
        raise HTTPException(404, "Cost evidence not found")
    record_change(
        db, entity_type="cost_evidence", entity_id=item_id, change_type="remove",
        field_changed="cost_evidence", old_value=f"{ev.source_type} {ev.unit_cost} @ {ev.volume_tier}",
        changed_by=user,
    )
    db.delete(ev)
    db.commit()


# ── decided cost ─────────────────────────────────────────────────────────────
@router.get("/items/{item_id}/decided-cost", response_model=list[DecidedCostOut])
def list_decided_cost(item_id: str, db: Session = Depends(get_db)) -> list[DecidedCost]:
    _get_item(db, item_id)
    return list(
        db.execute(
            select(DecidedCost).where(DecidedCost.item_id == item_id).order_by(DecidedCost.volume_tier)
        ).scalars()
    )


@router.put("/items/{item_id}/decided-cost", response_model=DecidedCostOut)
def set_decided_cost(
    item_id: str, body: DecidedCostIn, db: Session = Depends(get_db), user: str = Depends(current_user)
) -> DecidedCost:
    _get_item(db, item_id)
    existing = db.execute(
        select(DecidedCost).where(
            DecidedCost.item_id == item_id, DecidedCost.volume_tier == body.volume_tier
        )
    ).scalar_one_or_none()
    old = existing.unit_cost_eur if existing else None
    if existing is None:
        existing = DecidedCost(item_id=item_id, volume_tier=body.volume_tier)
        db.add(existing)
    existing.unit_cost_eur = body.unit_cost_eur
    existing.cost_min = body.cost_min
    existing.cost_max = body.cost_max
    existing.confidence = body.confidence
    existing.make_or_buy = body.make_or_buy
    existing.source_type = body.source_type
    existing.basis_note = body.basis_note
    existing.based_on_evidence_id = body.based_on_evidence_id
    existing.decided_by = user
    record_change(
        db, entity_type="decided_cost", entity_id=item_id,
        change_type="update" if old is not None else "create",
        field_changed=f"decided_cost@{body.volume_tier}", old_value=old, new_value=body.unit_cost_eur,
        changed_by=user, change_reason=body.change_reason,
    )
    db.commit()
    db.refresh(existing)
    return existing


# ── assembly labour (minutes → cost via the item's cost type) ────────────────
@router.get("/items/{item_id}/assembly-labor", response_model=list[AssemblyLaborOut])
def list_assembly_labor(item_id: str, db: Session = Depends(get_db)) -> list[AssemblyLabor]:
    _get_item(db, item_id)
    return list(
        db.execute(
            select(AssemblyLabor).where(AssemblyLabor.item_id == item_id).order_by(AssemblyLabor.volume_tier)
        ).scalars()
    )


@router.put("/items/{item_id}/assembly-labor", response_model=AssemblyLaborOut)
def set_assembly_labor(
    item_id: str, body: AssemblyLaborIn, db: Session = Depends(get_db), user: str = Depends(current_user)
) -> AssemblyLabor:
    _get_item(db, item_id)
    existing = db.execute(
        select(AssemblyLabor).where(
            AssemblyLabor.item_id == item_id, AssemblyLabor.volume_tier == body.volume_tier
        )
    ).scalar_one_or_none()
    old = existing.time_likely if existing else None
    if existing is None:
        existing = AssemblyLabor(item_id=item_id, volume_tier=body.volume_tier)
        db.add(existing)
    existing.time_likely = body.time_likely
    existing.time_min = body.time_min
    existing.time_max = body.time_max
    existing.updated_by = user
    record_change(
        db, entity_type="assembly_labor", entity_id=item_id,
        change_type="update" if old is not None else "create",
        field_changed=f"assembly_time@{body.volume_tier}", old_value=old, new_value=body.time_likely,
        changed_by=user,
    )
    db.commit()
    db.refresh(existing)
    return existing


# ── custom field values ──────────────────────────────────────────────────────
@router.put("/items/{item_id}/field-values")
def set_field_value(
    item_id: str, body: FieldValueIn, db: Session = Depends(get_db), user: str = Depends(current_user)
) -> dict:
    _get_item(db, item_id)
    if db.get(FieldDefinition, body.field_key) is None:
        raise HTTPException(400, f"Unknown field '{body.field_key}' — define it first")
    existing = db.execute(
        select(FieldValue).where(FieldValue.item_id == item_id, FieldValue.field_key == body.field_key)
    ).scalar_one_or_none()
    old = existing.value if existing else None
    if existing is None:
        existing = FieldValue(item_id=item_id, field_key=body.field_key)
        db.add(existing)
    existing.value = body.value
    record_change(
        db, entity_type="field_value", entity_id=item_id, change_type="update",
        field_changed=body.field_key, old_value=old, new_value=body.value, changed_by=user,
    )
    db.commit()
    return {"item_id": item_id, "field_key": body.field_key, "value": body.value}


# ── per-item history (for the drawer) ────────────────────────────────────────
@router.get("/items/{item_id}/history", response_model=list[ChangeHistoryOut])
def item_history(item_id: str, db: Session = Depends(get_db), limit: int = 50):
    _get_item(db, item_id)
    return list(
        db.execute(
            select(ChangeHistory)
            .where(ChangeHistory.entity_id == item_id)
            .order_by(ChangeHistory.changed_at.desc(), ChangeHistory.id.desc())
            .limit(limit)
        ).scalars()
    )


# ── global change feed (History tab) ─────────────────────────────────────────
@router.get("/history", response_model=list[ChangeHistoryOut])
def global_history(
    db: Session = Depends(get_db),
    limit: int = 150,
    entity_type: str | None = None,
):
    stmt = select(ChangeHistory).order_by(
        ChangeHistory.changed_at.desc(), ChangeHistory.id.desc()
    ).limit(limit)
    if entity_type:
        stmt = stmt.where(ChangeHistory.entity_type == entity_type)
    return list(db.execute(stmt).scalars())
