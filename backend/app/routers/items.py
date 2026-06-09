"""Item read endpoints. Edit endpoints land in M4; tree/rollup in M3."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Item
from ..schemas import ItemOut

router = APIRouter(prefix="/items", tags=["items"])


@router.get("", response_model=list[ItemOut])
def list_items(
    db: Session = Depends(get_db),
    module: str | None = Query(default=None, description="Filter by module_code"),
    item_type: str | None = Query(default=None, description="part | assembly"),
    top_level_only: bool = Query(default=False),
    q: str | None = Query(default=None, description="Search item_id or item_name"),
    include_archived: bool = Query(default=False),
) -> list[Item]:
    stmt = select(Item)
    if not include_archived:
        stmt = stmt.where(Item.archived.is_(False))
    if module:
        stmt = stmt.where(Item.module_code == module)
    if item_type:
        stmt = stmt.where(Item.item_type == item_type)
    if top_level_only:
        stmt = stmt.where(Item.is_top_level.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(Item.item_id).like(like) | func.lower(Item.item_name).like(like)
        )
    stmt = stmt.order_by(Item.item_id)
    return list(db.execute(stmt).scalars())


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str, db: Session = Depends(get_db)) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    return item
