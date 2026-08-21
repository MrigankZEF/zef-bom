"""Catalog: a flat, searchable list of every item (parts + assemblies) with its
3-point cost at each volume tier. The items table IS the catalog — so edits flow
here automatically and it doubles as the naming authority for imports.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..history import record_change
from ..models import BomLink, DecidedCost, Item
from ..rollups import BomGraph, top_level_reachable
from ..schemas import NewItemIn

router = APIRouter(tags=["catalog"])

# The volume tiers the catalog renders a column for; mirrors TIERS in Catalog.jsx.
CATALOG_TIERS = (1, 100, 10000)


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)) -> list[dict]:
    """Every non-archived item, flat, with its ROLLED-UP cost (min/likely/max) per tier.

    Deliberately the rollup, not the raw `decided_costs` row: `rollups` only consults a
    decided cost for a childless item, so for an assembly the stored figure is ignored and the
    real cost is contents + assembly labour. Reading decided_costs here made the catalog
    disagree with the drawer for every assembly. A leaf is unaffected — its rollup IS its
    decided cost.
    """
    costs: dict[str, dict] = {}
    for tier in CATALOG_TIERS:
        g = BomGraph(db, volume_tier=tier)
        for iid in g.items:
            r = g.rollup(iid)
            if r.covered == 0 and not r.cost:
                continue  # nothing priced anywhere below — show an em dash, not € 0.00
            costs.setdefault(iid, {})[tier] = {
                "min": round(r.cost_min, 2),
                "likely": round(r.cost, 2),
                "max": round(r.cost_max, 2),
            }
    linked: set[str] = set()
    for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars():
        linked.add(bl.parent_item_id)
        linked.add(bl.child_item_id)
    # Stricter than `in_bom`: actually reachable from a live top-level root, so an orphaned
    # sub-tree left behind by an archived root doesn't count as "in use".
    in_use = top_level_reachable(db)
    return [
        {
            "item_id": it.item_id,
            "item_name": it.item_name,
            "item_type": it.item_type,
            "module_code": it.module_code,
            "in_bom": it.item_id in linked,
            "in_top_level_bom": it.item_id in in_use,
            "costs": costs.get(it.item_id, {}),
        }
        for it in db.execute(
            select(Item).where(Item.archived.is_(False)).order_by(Item.item_id)
        ).scalars()
    ]


@router.post("/catalog/items", status_code=201)
def create_catalog_item(body: NewItemIn, db: Session = Depends(get_db), user: str = Depends(current_user)) -> dict:
    """Create a new catalog item with a free name and a chosen module.

    UN (default) = a universal part: it keeps the UN code wherever it's used. A specific
    system code (AEC / DAC / MDAC / …) follows usage — it stays that code while used in only
    that system, and becomes UN if it ends up shared across two or more systems."""
    from ..operations import allocate_code, clear_item_refs
    from ..bom_ingest.miro_csv_fix import normalize_item_name

    name = (body.item_name or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    module = (body.module or "UN").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}", module):
        raise HTTPException(400, f"Invalid module code '{module}' — use 2–5 letters (e.g. UN, AEC, MDAC).")

    # No accidental duplicates: if a live item already carries this name, block unless the
    # user explicitly confirms (allow_duplicate) it's a genuinely different part. Compared on
    # the normalized name so "O ring" and "O-Ring" count as the same.
    if not body.allow_duplicate:
        target = normalize_item_name(name)
        dupes = [
            it.item_id for it in db.execute(
                select(Item).where(Item.archived.is_(False))
            ).scalars()
            if it.item_name and normalize_item_name(it.item_name) == target
        ]
        if dupes:
            raise HTTPException(
                409,
                f"A part named “{name}” already exists ({', '.join(sorted(dupes)[:5])}"
                f"{'…' if len(dupes) > 5 else ''}). Re-submit with allow_duplicate to add it "
                "as a separate part.",
            )

    suffix = "A" if body.item_type == "assembly" else "P"
    code = allocate_code(db, module, suffix)
    clear_item_refs(db, code)  # a fresh code must not inherit stale cost/link rows
    db.add(Item(
        item_id=code, item_name=name, item_type=body.item_type, module_code=module,
        is_top_level=False, created_by=user, updated_by=user,
    ))
    record_change(db, entity_type="item", entity_id=code, change_type="create",
                  new_value=name, changed_by=user, change_reason=f"created in catalog ({module})")
    db.commit()
    return {"item_id": code, "item_name": name, "item_type": body.item_type, "module_code": module}
