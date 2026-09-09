"""Undoing one change from the history feed.

The rule that makes this safe: **an undo is a new forward change, never a deletion of
history.** `models.py` states the data model's rule — "History is a single append-only
`change_history` log" — so undoing writes a fresh row restoring the old value, with a
`change_reason` naming the row it reverses. Nothing is rewritten, the log only grows, and an
undo can itself be undone for free.

Two things bound what is offered:

*Addressability.* Undoing needs `(entity_type, entity_id, field_changed)` to identify one
scalar. Most kinds do: an item's row names the column, a decided cost carries its tier in
`field_changed` (`decided_cost@100`), a field value names the key. `cost_evidence` does not —
its `entity_id` is the item and nothing says WHICH evidence row — so it is not offered at all,
rather than offered and guessing.

*Supersession.* Only the LATEST change to a given field can be undone. Undoing an older one
would silently discard every edit made since, which is a worse outcome than not offering it.

`old_value` is `Text`, so restoring needs the column's real type back — `backup._coerce`
already does exactly that conversion for a backup cell, and is reused here rather than written
a second time.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .backup import _coerce
from .history import record_change
from .models import AssemblyLabor, BomLink, ChangeHistory, DecidedCost, FieldValue, Item

# The columns an item PATCH is allowed to change — the same set a merge copies. A field outside
# it is not editable in the first place, so a history row naming one is not undoable either.
ITEM_COLS = {
    "item_name", "item_type", "materials", "material", "weight_grams", "unit_of_measure",
    "supplier", "supplier_country", "supplier_part_number", "lead_time_weeks",
    "cost_type_id", "drawing_url", "thumbnail_file_id", "comment", "external_reference",
    "module_code",
}


def _tier_of(field: str) -> int | None:
    """`decided_cost@10000` -> 10000. None when the field carries no tier."""
    _, _, tail = field.partition("@")
    return int(tail) if tail.isdigit() else None


def undoable(db: Session, h: ChangeHistory) -> tuple[bool, str]:
    """Can this row be undone, and if not, why not — in words a person can act on."""
    if h.entity_type == "item" and h.change_type == "update" and h.field_changed in ITEM_COLS:
        pass
    elif h.entity_type == "bom_link" and h.change_type in ("create", "remove", "update"):
        if ">" not in h.entity_id:
            return False, "this row does not say which link it changed"
    elif h.entity_type == "decided_cost" and _tier_of(h.field_changed or "") is not None:
        pass
    elif h.entity_type == "assembly_labor" and (h.field_changed or "").startswith("assembly_time@"):
        pass
    elif h.entity_type == "field_value" and h.change_type == "update" and h.field_changed:
        pass
    elif h.entity_type == "cost_evidence":
        # entity_id is the item, and an item can have several evidence rows. Nothing here says
        # which one, so there is no way to undo it without guessing.
        return False, "the history does not record which evidence row this was"
    else:
        return False, f"undo is not supported for {h.field_changed or h.entity_type}"

    # An item since deleted, or re-coded, cannot be written back to.
    subject = h.entity_id.split(">")[0]
    if db.get(Item, subject) is None:
        return False, f"{subject} no longer exists"

    later = db.execute(
        select(ChangeHistory.id)
        .where(
            ChangeHistory.entity_type == h.entity_type,
            ChangeHistory.entity_id == h.entity_id,
            ChangeHistory.field_changed == h.field_changed,
            ChangeHistory.id > h.id,
        )
        .limit(1)
    ).first()
    if later:
        return False, "superseded by a later change"
    return True, ""


def describe(h: ChangeHistory) -> str:
    """What a confirmation has to spell out: the subject, the field, and current -> restored."""
    what = h.field_changed or h.entity_type
    if h.change_type == "create":
        return f"remove {what} on {h.entity_id} (added as {h.new_value or '—'})"
    if h.change_type == "remove":
        return f"put {what} back on {h.entity_id} (was {h.old_value or '—'})"
    return f"set {what} on {h.entity_id} back to {h.old_value or '—'} (currently {h.new_value or '—'})"


def apply_undo(db: Session, h: ChangeHistory, user: str) -> dict:
    """Restore the previous value. The caller has already checked `undoable`. Does not commit."""
    et, field = h.entity_type, (h.field_changed or "")
    restored: object = None

    if et == "item":
        item = db.get(Item, h.entity_id)
        restored = _coerce(h.old_value, Item.__table__.columns[field])
        setattr(item, field, restored)
        item.updated_by = user

    elif et == "bom_link":
        parent, _, child = h.entity_id.partition(">")
        link = db.execute(
            select(BomLink).where(BomLink.parent_item_id == parent, BomLink.child_item_id == child)
        ).scalar_one_or_none()
        if h.change_type == "create":
            # Archived, not deleted — the same soft delete the rest of the app uses, so the
            # link's own history stays attached to a row that still exists.
            if link is not None:
                link.archived = True
            restored = "removed"
        elif h.change_type == "remove":
            if link is None:
                link = BomLink(parent_item_id=parent, child_item_id=child, quantity=1)
                db.add(link)
            link.archived = False
            if h.old_value:
                try:
                    link.quantity = float(h.old_value)
                except ValueError:
                    pass
            restored = link.quantity
        else:
            link.quantity = float(h.old_value)
            restored = link.quantity

    elif et == "decided_cost":
        tier = _tier_of(field)
        row = db.execute(
            select(DecidedCost).where(DecidedCost.item_id == h.entity_id, DecidedCost.volume_tier == tier)
        ).scalar_one_or_none()
        if h.old_value is None:
            # There was no cost before this change, so undoing it means there is none again.
            if row is not None:
                db.delete(row)
            restored = None
        else:
            if row is None:
                row = DecidedCost(item_id=h.entity_id, volume_tier=tier, unit_cost_eur=0)
                db.add(row)
            row.unit_cost_eur = float(h.old_value)
            row.decided_by = user
            restored = row.unit_cost_eur

    elif et == "assembly_labor":
        tier = _tier_of(field)
        row = db.execute(
            select(AssemblyLabor).where(AssemblyLabor.item_id == h.entity_id, AssemblyLabor.volume_tier == tier)
        ).scalar_one_or_none()
        if row is None:
            row = AssemblyLabor(item_id=h.entity_id, volume_tier=tier)
            db.add(row)
        row.time_likely = float(h.old_value) if h.old_value else None
        row.updated_by = user
        restored = row.time_likely

    elif et == "field_value":
        row = db.execute(
            select(FieldValue).where(FieldValue.item_id == h.entity_id, FieldValue.field_key == field)
        ).scalar_one_or_none()
        if row is None:
            row = FieldValue(item_id=h.entity_id, field_key=field, value=h.old_value)
            db.add(row)
        else:
            row.value = h.old_value
        restored = row.value

    # The undo IS the new change, recorded the way any edit would be — so the next reader sees a
    # history that explains itself rather than a value that appears to have moved on its own.
    record_change(
        db, entity_type=et, entity_id=h.entity_id,
        change_type="update", field_changed=h.field_changed,
        old_value=h.new_value, new_value=None if restored is None else str(restored),
        changed_by=user, change_reason=f"undo of change #{h.id}",
    )
    return {"undone": h.id, "restored": None if restored is None else str(restored)}
