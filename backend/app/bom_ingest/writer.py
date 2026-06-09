"""Write resolved ParsedCells into the new schema (items + bom_links + change_history).

Replaces the legacy `write_database_records`, which targeted item_revisions and
linked bom rows by revision id. Here links reference `item_id` directly and every
insert appends a change_history row (our single historization mechanism).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..history import record_change
from ..models import BomLink, Item
from .miro_csv_fix import ParsedCell


@dataclass
class ApplyResult:
    items_created: int = 0
    links_created: int = 0


def _item_type_of(cell: ParsedCell) -> str:
    suffix = cell.explicit_suffix or cell.inferred_suffix
    if suffix:
        return "assembly" if suffix.upper() == "A" else "part"
    return "assembly" if cell.has_children else "part"


def _module_of(cell: ParsedCell) -> str | None:
    return cell.explicit_module or cell.inferred_module


def _top_level_numbers(cells: list[ParsedCell]) -> set[str]:
    """Roots = items that are a parent of something but never a child in this tree."""
    parents: set[str] = set()
    children: set[str] = set()
    for c in cells:
        if c.parent_resolved_item_number and c.resolved_item_number:
            parents.add(c.parent_resolved_item_number)
            children.add(c.resolved_item_number)
    return parents - children


def apply_cells(
    db: Session,
    cells: list[ParsedCell],
    *,
    batch_id: str,
    user: str | None,
    mark_top_level: bool = False,
) -> ApplyResult:
    """Upsert items and bom_links from resolved cells. Caller commits the transaction."""
    result = ApplyResult()
    roots = _top_level_numbers(cells) if mark_top_level else set()

    # 1) Items — one upsert per unique resolved number.
    unique: dict[str, ParsedCell] = {}
    for cell in cells:
        if cell.resolved_item_number and cell.resolved_item_name:
            unique.setdefault(cell.resolved_item_number, cell)

    for number, cell in unique.items():
        if db.get(Item, number) is not None:
            continue
        item = Item(
            item_id=number,
            item_name=cell.resolved_item_name,
            item_type=_item_type_of(cell),
            module_code=_module_of(cell),
            is_top_level=number in roots,
            external_reference=cell.cleaned_text or None,
            created_by=user,
            updated_by=user,
        )
        db.add(item)
        record_change(
            db,
            entity_type="item",
            entity_id=number,
            change_type="create",
            new_value=cell.resolved_item_name,
            changed_by=user,
            change_reason=f"import {batch_id}",
        )
        result.items_created += 1

    db.flush()  # ensure item rows exist before linking

    # 2) BOM links — parent → child edges, deduped against this batch and the DB.
    seen: set[tuple[str, str]] = set()
    for cell in cells:
        parent = cell.parent_resolved_item_number
        child = cell.resolved_item_number
        if not parent or not child or parent == child:
            continue
        key = (parent, child)
        if key in seen:
            continue
        seen.add(key)
        exists = db.execute(
            select(BomLink.id).where(
                BomLink.parent_item_id == parent, BomLink.child_item_id == child
            )
        ).first()
        if exists:
            continue
        db.add(
            BomLink(
                parent_item_id=parent,
                child_item_id=child,
                quantity=cell.quantity,
                comment=cell.comment or None,
            )
        )
        record_change(
            db,
            entity_type="bom_link",
            entity_id=f"{parent}>{child}",
            change_type="create",
            new_value=f"qty={cell.quantity}",
            changed_by=user,
            change_reason=f"import {batch_id}",
        )
        result.links_created += 1

    return result
