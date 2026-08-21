"""BOM structure + rollup endpoints (M3, read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from sqlalchemy import select

from ..db import get_db
from ..models import AssemblyLabor, BomLink, DecidedCost, Item
from ..rollups import BomGraph, top_level_reachable

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


@router.get("/flat")
def get_flat(
    db: Session = Depends(get_db),
    root: str | None = Query(default=None, description="BOM root to flatten; omitted = all top-level BOMs"),
    volume: int = Query(default=100),
):
    """A BOM flattened to one level: every descendant once, with the count the plant needs.

    The nested tree answers "what is this made of"; this answers "how many of that part do
    we buy". Rows come from the same graph as the tree and the treemaps, so the numbers
    cannot drift apart.

    Rows cover what is *inside* the root, so the root's own assembly labour is not one of
    them — it comes back as `own_assembly_cost` instead. Leaf rows + assembly rows +
    own_assembly_cost = `cost`.
    """
    g = BomGraph(db, volume_tier=volume)
    if root and root not in g.items:
        raise HTTPException(404, f"Item {root} not found")
    roots = [g.items[root]] if root else g.roots()
    out = []
    for r in roots:
        rl = g.rollup(r.item_id)
        out.append({
            "item_id": r.item_id,
            "item_name": r.item_name,
            "item_type": r.item_type,
            "module_code": r.module_code,
            "volume_tier": volume,
            "cost": round(rl.cost, 2),
            "own_assembly_cost": round(g.assembly_cost(r)[1], 2),
            "coverage": round(rl.coverage, 4),
            "rows": g.flat_rows(r.item_id),
        })
    return out if root is None else out[0]


@router.get("/items/{item_id}/usage")
def item_usage(item_id: str, db: Session = Depends(get_db), volume: int = Query(default=100)):
    """Which top-level BOMs need this item, and how many of it.

    `shared` means more than one BOM reaches it — then no single extended total is
    meaningful, and the drawer says nothing rather than something wrong.
    """
    g = BomGraph(db, volume_tier=volume)
    if item_id not in g.items:
        raise HTTPException(404, f"Item {item_id} not found")
    roots = g.roots_reaching(item_id)
    return {
        "item_id": item_id,
        "roots": roots,
        "total_count": round(sum(r["count"] for r in roots), 3),
        "shared": len(roots) > 1,
    }


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
        "cost_min": round(r.cost_min, 2),
        "cost_max": round(r.cost_max, 2),
        "assembly_cost": round(r.assembly_cost, 2),
        "parts_cost": round(r.cost - r.assembly_cost, 2),
        "covered": r.covered,
        "total": r.total,
        "coverage": round(r.coverage, 4),
        "missing": sorted(set(r.missing)),
        "missing_assembly": sorted(set(r.missing_assembly)),
        "covered_conflict": sorted(set(r.covered_conflict)),
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
            "cost_min": round(rl.cost_min, 2),
            "cost_max": round(rl.cost_max, 2),
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
    grand_min = sum(p["cost_min"] for p in per_root)
    grand_max = sum(p["cost_max"] for p in per_root)
    return {
        "volume_tier": volume, "grand_total": round(grand, 2),
        "grand_min": round(grand_min, 2), "grand_max": round(grand_max, 2),
        "per_root": per_root, "tiers": tiers,
    }


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
    parts, cost_total, cost_min_total, cost_max_total, weight_total, covered = [], 0.0, 0.0, 0.0, 0.0, 0
    for leaf_id, eff_qty in leaves.items():
        it = g.items[leaf_id]
        _est = g.decided.get((leaf_id, volume))  # (min, likely, max) or None
        unit_cost = _est[1] if _est is not None else None
        weight = it.weight_grams
        cost = (unit_cost or 0.0) * eff_qty
        wt = (weight or 0.0) * eff_qty
        cost_total += cost
        cost_min_total += (_est[0] if _est is not None else 0.0) * eff_qty
        cost_max_total += (_est[2] if _est is not None else 0.0) * eff_qty
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

    # Per-assembly cost (its own time × rate × effective qty) — one entry per assembly.
    asm_costs = []
    for aid, eff in g.flatten_assemblies(root).items():
        ac = g.assembly_cost(g.items[aid])[1] * eff
        if ac > 0:
            asm_costs.append({"item_id": aid, "item_name": g.items[aid].item_name, "cost": round(ac, 2)})
    asm_costs.sort(key=lambda x: -x["cost"])

    rl = g.rollup(root)  # full cost incl. assembly labour, with min/max
    tiers = []
    for v in VOLUME_TIERS:
        rv = BomGraph(db, volume_tier=v).rollup(root)
        tiers.append({"volume": v, "total": round(rv.cost, 2), "total_min": round(rv.cost_min, 2), "total_max": round(rv.cost_max, 2)})

    return {
        "root": root, "root_name": g.items[root].item_name, "volume_tier": volume,
        "parts": parts, "assemblies": asm_costs, "tiers": tiers,
        "totals": {
            "cost": round(rl.cost, 2),                       # full: parts + assembly labour
            "parts_cost": round(cost_total, 2),
            "assembly_cost": round(rl.cost - cost_total, 2),
            "cost_min": round(rl.cost_min, 2), "cost_max": round(rl.cost_max, 2),
            "weight_grams": round(weight_total, 1),
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
    links = list(db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars())
    children_map: dict[str, list[str]] = {}
    for bl in links:
        children_map.setdefault(bl.parent_item_id, []).append(bl.child_item_id)
    parents = set(children_map)
    linked = top_level_reachable(db)
    costed_tiers: dict[str, set] = {}
    for dc in db.execute(select(DecidedCost)).scalars():
        costed_tiers.setdefault(dc.item_id, set()).add(dc.volume_tier)
    labored_tiers: dict[str, set] = {}
    for al in db.execute(select(AssemblyLabor)).scalars():
        labored_tiers.setdefault(al.item_id, set()).add(al.volume_tier)

    out = []
    for it in items.values():
        if it.item_id not in linked:
            continue  # catalog-only item (not in any BOM) — not a pending gap
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
            have = costed_tiers.get(it.item_id, set())
            for tier, lbl in ((1, "cost@1"), (100, "cost@100"), (10000, "cost@10k")):
                if tier not in have:
                    missing.append(lbl)
        else:  # assembly
            have_t = labored_tiers.get(it.item_id, set())
            for tier, lbl in ((1, "asm_time@1"), (100, "asm_time@100"), (10000, "asm_time@10k")):
                if tier not in have_t:
                    missing.append(lbl)
            if it.cost_type_id is None:
                missing.append("cost_type")
        if missing:
            out.append({
                "item_id": it.item_id, "item_name": it.item_name,
                "module_code": it.module_code, "item_type": it.item_type,
                "weight_grams": it.weight_grams, "material": it.material,
                "missing": missing,
            })
    out.sort(key=lambda x: (-len(x["missing"]), x["item_id"]))
    return out
