"""Helper for appending to the change_history audit log.

Every mutating endpoint should funnel field changes through `record_change`
so history stays the single source of truth for 'who changed what, when'.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import ChangeHistory


def record_change(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    change_type: str,
    field_changed: str | None = None,
    old_value: object = None,
    new_value: object = None,
    changed_by: str | None = None,
    change_reason: str | None = None,
) -> ChangeHistory:
    """Append one change_history row. Does not commit — caller owns the transaction."""
    entry = ChangeHistory(
        entity_type=entity_type,
        entity_id=str(entity_id),
        change_type=change_type,
        field_changed=field_changed,
        old_value=None if old_value is None else str(old_value),
        new_value=None if new_value is None else str(new_value),
        changed_by=changed_by,
        change_reason=change_reason,
    )
    db.add(entry)
    return entry


def diff_fields(before: dict, after: dict, skip: set[str] | None = None) -> dict[str, tuple]:
    """Return {field: (old, new)} for keys whose value changed. Helper for PATCH endpoints."""
    skip = skip or set()
    changes: dict[str, tuple] = {}
    for key, new_val in after.items():
        if key in skip:
            continue
        old_val = before.get(key)
        if old_val != new_val:
            changes[key] = (old_val, new_val)
    return changes
