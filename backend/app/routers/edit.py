"""Mutation endpoints (M4): edit items, manage cost evidence + decided cost,
custom field values. Every write appends to change_history.

Auth is deferred to M6; for now the editor identity comes from an optional
`X-User` header (falls back to 'anonymous').
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..history import record_change
from ..models import ChangeHistory, CostEvidence, DecidedCost, FieldDefinition, FieldValue, Item
from ..schemas import (
    ChangeHistoryOut,
    CostEvidenceIn,
    CostEvidenceOut,
    DecidedCostIn,
    DecidedCostOut,
    FieldValueIn,
    ItemOut,
    ItemPatch,
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
