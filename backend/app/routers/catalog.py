"""Catalog: a flat, searchable list of every item (parts + assemblies) with its
3-point cost at each volume tier. The items table IS the catalog — so edits flow
here automatically and it doubles as the naming authority for imports.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..history import record_change
from ..models import BomLink, DecidedCost, Item
from ..schemas import NewItemIn

router = APIRouter(tags=["catalog"])


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)) -> list[dict]:
    """Every non-archived item, flat, with decided costs (min/likely/max) per tier."""
    costs: dict[str, dict] = {}
    for dc in db.execute(select(DecidedCost)).scalars():
        costs.setdefault(dc.item_id, {})[dc.volume_tier] = {
            "min": float(dc.cost_min) if dc.cost_min is not None else None,
            "likely": float(dc.unit_cost_eur),
            "max": float(dc.cost_max) if dc.cost_max is not None else None,
        }
    linked: set[str] = set()
    for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars():
        linked.add(bl.parent_item_id)
        linked.add(bl.child_item_id)
    return [
        {
            "item_id": it.item_id,
            "item_name": it.item_name,
            "item_type": it.item_type,
            "module_code": it.module_code,
            "in_bom": it.item_id in linked,
            "costs": costs.get(it.item_id, {}),
        }
        for it in db.execute(
            select(Item).where(Item.archived.is_(False)).order_by(Item.item_id)
        ).scalars()
    ]


@router.post("/catalog/items", status_code=201)
def create_catalog_item(body: NewItemIn, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    """Create a new catalog item with a free name. It gets a placeholder UN code; once you
    add it into a BOM, the naming engine re-codes it to its system (or keeps UN if shared)."""
    from ..operations import allocate_code

    name = (body.item_name or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    suffix = "A" if body.item_type == "assembly" else "P"
    code = allocate_code(db, "UN", suffix)
    db.add(Item(
        item_id=code, item_name=name, item_type=body.item_type, module_code="UN",
        is_top_level=False, created_by=user, updated_by=user,
    ))
    record_change(db, entity_type="item", entity_id=code, change_type="create",
                  new_value=name, changed_by=user, change_reason="created in catalog")
    db.commit()
    return {"item_id": code, "item_name": name, "item_type": body.item_type}
