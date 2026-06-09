"""Health + reference-data endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import FieldDefinition, Item

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    item_count = db.execute(
        select(func.count()).select_from(Item).where(Item.archived.is_(False))
    ).scalar_one()
    return {"status": "ok", "items": item_count}


@router.get("/field-definitions")
def list_field_definitions(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(FieldDefinition).order_by(FieldDefinition.group, FieldDefinition.key))
    return [
        {
            "key": f.key,
            "label": f.label,
            "type": f.type,
            "applies_to": f.applies_to,
            "required": f.required,
            "options": f.options,
            "unit": f.unit,
            "group": f.group,
        }
        for f in rows.scalars()
    ]
