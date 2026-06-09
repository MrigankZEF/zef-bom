"""BOM structure + rollup endpoints (M3, read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from sqlalchemy import select

from ..db import get_db
from ..models import BomLink, DecidedCost, Item
from ..rollups import BomGraph

router = APIRouter(tags=["bom"])

# The volume scenarios actually used everywhere (matches the decided-cost tiers).
VOLUME_TIERS = [1, 100, 10000]


@router.get("/tree")
def get_tree(
    db: Session = Depends(get_db),
    root: str | None = Query(default=None, description="Item id to expand; omitted = all top-level BOMs"),
    volume: int = Query(default=100),
):
    g = BomGraph(db, volume_tier=volume)
    if root:
        if root not in g.items:
            raise HTTPException(404, f"Item {root} not found")
        return g.node(root)
    return [g.node(r.item_id) for r in g.roots()]


@router.get("/items/{item_id}/where-used")
def where_used(item_id: str, db: Session = Depends(get_db)):
    g = BomGraph(db)
    if item_id not in g.items:
        raise HTTPException(404, f"Item {item_id} not found")
    return g.where_used(item_id)


@router.get("/rollup")
def get_rollup(
    db: Session = Depends(get_db),
    root: str = Query(...),
    volume: int = Query(default=100),
):
    g = BomGraph(db, volume_tier=volume)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")
    r = g.rollup(root)
    return {
        "root": root,
        "volume_tier": volume,
        "cost": round(r.cost, 2),
        "covered": r.covered,
        "total": r.total,
        "coverage": round(r.coverage, 4),
        "missing": sorted(set(r.missing)),
        "weight_grams": round(r.weight_grams, 2) if r.weight_grams is not None else None,
        "assembly_time_min": round(g.assembly_time_total(root), 2),
    }


@router.get("/costing/summary")
def costing_summary(db: Session = Depends(get_db), volume: int = Query(default=100)):
    """Per-top-level rollup at the chosen volume + the cost curve across volume tiers."""
    g = BomGraph(db, volume_tier=volume)
    roots = g.roots()
    per_root = []
    for r in roots:
        rl = g.rollup(r.item_id)
        per_root.append({
            "item_id": r.item_id,
            "item_name": r.item_name,
            "cost": round(rl.cost, 2),
            "coverage": round(rl.coverage, 4),
            "covered": rl.covered,
            "total": rl.total,
            "missing": len(set(rl.missing)),
        })
    # Cost curve: total across all roots at each volume tier.
    tiers = []
    for v in VOLUME_TIERS:
        gv = BomGraph(db, volume_tier=v)
        total = sum(gv.rollup(r.item_id).cost for r in gv.roots())
        tiers.append({"volume": v, "total": round(total, 2)})
    grand = sum(p["cost"] for p in per_root)
    return {"volume_tier": volume, "grand_total": round(grand, 2), "per_root": per_root, "tiers": tiers}


@router.get("/costing/breakdown")
def costing_breakdown(
    db: Session = Depends(get_db),
    root: str = Query(...),
    volume: int = Query(default=100),
):
    """Per-part cost & weight contributions within ONE top-level BOM — for the treemaps.

    Each leaf's effective quantity (qty multiplied along every path) × its decided unit
    cost / weight. Returns the parts sorted by cost, plus the cost-vs-volume curve for
    this BOM only (not the whole plant).
    """
    g = BomGraph(db, volume_tier=volume)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")
    leaves = g.flatten_leaves(root)
    parts, cost_total, weight_total, covered = [], 0.0, 0.0, 0
    for leaf_id, eff_qty in leaves.items():
        it = g.items[leaf_id]
        unit_cost = g.decided.get((leaf_id, volume))
        weight = it.weight_grams
        cost = (unit_cost or 0.0) * eff_qty
        wt = (weight or 0.0) * eff_qty
        cost_total += cost
        weight_total += wt
        if unit_cost is not None:
            covered += 1
        parts.append({
            "item_id": leaf_id, "item_name": it.item_name, "module_code": it.module_code,
            "eff_qty": round(eff_qty, 3),
            "unit_cost": float(unit_cost) if unit_cost is not None else None,
            "cost": round(cost, 2), "weight_grams": round(wt, 1),
            "has_cost": unit_cost is not None, "has_weight": weight is not None,
        })
    parts.sort(key=lambda x: -x["cost"])

    tiers = []
    for v in VOLUME_TIERS:
        gv = BomGraph(db, volume_tier=v)
        lv = gv.flatten_leaves(root)
        tv = sum((gv.decided.get((lid, v)) or 0.0) * q for lid, q in lv.items())
        tiers.append({"volume": v, "total": round(tv, 2)})

    return {
        "root": root, "root_name": g.items[root].item_name, "volume_tier": volume,
        "parts": parts, "tiers": tiers,
        "totals": {
            "cost": round(cost_total, 2), "weight_grams": round(weight_total, 1),
            "covered": covered, "total": len(leaves),
            "coverage": round(covered / len(leaves), 4) if leaves else 0,
        },
    }


@router.get("/pending")
def pending(db: Session = Depends(get_db), module: str | None = Query(default=None)):
    """Items missing required data — the 'sit down and fill these in' queue.

    Leaf parts need weight + material + supplier_country + a decided cost;
    assemblies need an assembly time. (Stage is intentionally omitted for the MVP.)
    """
    items = {
        it.item_id: it
        for it in db.execute(select(Item).where(Item.archived.is_(False))).scalars()
    }
    parents = {
        bl.parent_item_id
        for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars()
    }
    costed = {dc.item_id for dc in db.execute(select(DecidedCost)).scalars()}

    out = []
    for it in items.values():
        if module and it.module_code != module:
            continue
        is_leaf = it.item_id not in parents
        missing = []
        if is_leaf:
            if it.weight_grams is None:
                missing.append("weight")
            if not it.material and not it.materials:
                missing.append("material")
            if not it.supplier_country:
                missing.append("supplier_country")
            if it.item_id not in costed:
                missing.append("cost")
        elif it.assembly_time_min_1pc is None:
            missing.append("assembly_time")
        if missing:
            out.append({
                "item_id": it.item_id, "item_name": it.item_name,
                "module_code": it.module_code, "item_type": it.item_type,
                "weight_grams": it.weight_grams, "material": it.material,
                "missing": missing,
            })
    out.sort(key=lambda x: (-len(x["missing"]), x["item_id"]))
    return out
