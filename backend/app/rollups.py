"""In-memory BOM graph: tree expansion, cost/weight rollups, where-used.

The BOM is small (hundreds of items), so we load items + links + decided costs
once per request and compute over plain dicts — portable across SQLite/Postgres and
a direct port of the prototype's `rollup()`. Rollups never silently fill a missing
cost with zero: uncovered leaves are reported so coverage % is honest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AssemblyLabor, BomLink, DecidedCost, Item, ReferenceValue


def top_level_reachable(db: Session) -> set[str]:
    """Every item reachable by walking down from a live top-level root.

    "In the BOM tree" is stricter than "appears in some link": an orphaned sub-tree — links
    left over after its root was archived — is NOT in the tree any more, even though its
    rows still exist. Deliberately not built on `BomGraph`, which also loads all decided
    costs; callers here only need structure.
    """
    live = {
        it.item_id: it
        for it in db.execute(select(Item).where(Item.archived.is_(False))).scalars()
    }
    kids: dict[str, list[str]] = {}
    for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars():
        kids.setdefault(bl.parent_item_id, []).append(bl.child_item_id)

    seen: set[str] = set()
    stack = [iid for iid, it in live.items() if it.is_top_level]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in live:
            continue
        seen.add(cur)
        stack.extend(kids.get(cur, []))
    return seen


@dataclass
class Rollup:
    cost: float = 0.0         # most-likely: children parts + this assembly's process cost
    cost_min: float = 0.0     # 3-point estimate, summed independently up the tree
    cost_max: float = 0.0
    assembly_cost: float = 0.0  # just the process cost added at this node (for breakdown)
    assembly_cost_min: float = 0.0
    assembly_cost_max: float = 0.0
    covered: int = 0          # priced inputs: leaves with a decided cost + priced assemblies
    total: int = 0            # all inputs that need a cost
    missing: list[str] = field(default_factory=list)          # leaf ids with no decided cost
    missing_assembly: list[str] = field(default_factory=list)  # assembly ids with no process cost
    # a descendant carrying its own assembly cost under an ancestor marked as covering it
    covered_conflict: list[str] = field(default_factory=list)
    weight_grams: float | None = 0.0
    weight_missing: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return 1.0 if self.total == 0 else self.covered / self.total


class BomGraph:
    def __init__(self, db: Session, volume_tier: int = 100):
        self.volume = volume_tier
        # Archived items/links are excluded from all views (soft-delete).
        self.items: dict[str, Item] = {
            it.item_id: it
            for it in db.execute(select(Item).where(Item.archived.is_(False))).scalars()
        }
        self.children: dict[str, list[tuple[str, float]]] = {}
        self.parents: dict[str, list[tuple[str, float]]] = {}
        for link in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars():
            if link.parent_item_id not in self.items or link.child_item_id not in self.items:
                continue
            self.children.setdefault(link.parent_item_id, []).append(
                (link.child_item_id, link.quantity)
            )
            self.parents.setdefault(link.child_item_id, []).append(
                (link.parent_item_id, link.quantity)
            )
        # (cost_min, most_likely, cost_max) — min/max default to the likely value when blank.
        self.decided: dict[tuple[str, int], tuple[float, float, float]] = {
            (dc.item_id, dc.volume_tier): (
                float(dc.cost_min) if dc.cost_min is not None else float(dc.unit_cost_eur),
                float(dc.unit_cost_eur),
                float(dc.cost_max) if dc.cost_max is not None else float(dc.unit_cost_eur),
            )
            for dc in db.execute(
                select(DecidedCost).where(DecidedCost.volume_tier == volume_tier)
            ).scalars()
        }
        # Assembly labour: minutes (min, likely, max) per item at this tier, + the €/h rates.
        _labor_rows = list(db.execute(
            select(AssemblyLabor).where(AssemblyLabor.volume_tier == volume_tier)
        ).scalars())
        self.labor: dict[str, tuple[float | None, float, float | None]] = {
            al.item_id: (al.time_min, al.time_likely, al.time_max) for al in _labor_rows
        }
        # "this assembly's cost already covers everything beneath it" (outsourced/bought-in)
        self.covers_subs: set[str] = {
            al.item_id for al in _labor_rows if al.covers_subassemblies
        }
        # Assembly cost types are reference values (category 'assembly_cost_type') with a
        # €/hour rate in meta; keyed by the reference value's id (= item.cost_type_id).
        self.rates: dict[int, float] = {
            rv.id: float((rv.meta or {}).get("rate_eur_h") or 0.0)
            for rv in db.execute(
                select(ReferenceValue).where(ReferenceValue.category == "assembly_cost_type")
            ).scalars()
        }
        self._rollup_cache: dict[tuple[str, bool], Rollup] = {}

    def assembly_cost(self, item) -> tuple[float, float, float]:
        """(min, likely, max) € to assemble this item at the current tier = minutes × €/min."""
        if item is None or item.cost_type_id is None:
            return (0.0, 0.0, 0.0)
        rate = self.rates.get(item.cost_type_id)
        t = self.labor.get(item.item_id)
        if not rate or t is None:
            return (0.0, 0.0, 0.0)
        tmin, tlikely, tmax = t
        per_min = rate / 60.0
        return (
            (tmin if tmin is not None else tlikely) * per_min,
            tlikely * per_min,
            (tmax if tmax is not None else tlikely) * per_min,
        )

    def assembly_priced(self, item) -> bool:
        """True when this assembly's process cost is actually derived from data, rather than
        silently defaulting to 0 because nobody set a cost type or a time at this tier."""
        return (
            item is not None
            and item.cost_type_id is not None
            and bool(self.rates.get(item.cost_type_id))
            and item.item_id in self.labor
        )

    # ── structure ────────────────────────────────────────────────────────────
    def roots(self) -> list[Item]:
        return [it for it in self.items.values() if it.is_top_level]

    def where_used(self, item_id: str) -> list[dict]:
        return [
            {"parent": p, "quantity": q, "name": self.items[p].item_name}
            for p, q in self.parents.get(item_id, [])
            if p in self.items
        ]

    def rollup(
        self, item_id: str, _seen: frozenset[str] = frozenset(), _asm_covered: bool = False
    ) -> Rollup:
        """`_asm_covered` = an ancestor is marked as covering the assembly work beneath it, so
        nothing down here counts as missing an assembly cost."""
        key = (item_id, _asm_covered)
        if key in self._rollup_cache:
            return self._rollup_cache[key]
        if item_id in _seen:  # circular reference guard
            return Rollup(total=0)
        kids = self.children.get(item_id, [])
        item = self.items.get(item_id)
        if not kids:  # leaf — its own decided unit cost (min, likely, max)
            est = self.decided.get((item_id, self.volume))
            w = item.weight_grams if item else None
            cmin, likely, cmax = est if est is not None else (0.0, 0.0, 0.0)
            r = Rollup(
                cost=likely, cost_min=cmin, cost_max=cmax,
                covered=1 if est is not None else 0,
                total=1,
                missing=[] if est is not None else [item_id],
                weight_grams=w if w is not None else 0.0,
                weight_missing=[] if w is not None else [item_id],
            )
            self._rollup_cache[key] = r
            return r
        seen = _seen | {item_id}
        r = Rollup(weight_grams=0.0)
        # Everything below a covering assembly is already paid for by this one quoted cost.
        covers = item_id in self.covers_subs
        child_covered = _asm_covered or covers
        for child, qty in kids:
            cr = self.rollup(child, seen, child_covered)
            r.cost += cr.cost * qty
            r.cost_min += cr.cost_min * qty
            r.cost_max += cr.cost_max * qty
            r.covered += cr.covered
            r.total += cr.total
            r.missing.extend(cr.missing)
            r.missing_assembly.extend(cr.missing_assembly)
            r.covered_conflict.extend(cr.covered_conflict)
            r.weight_grams = (r.weight_grams or 0.0) + (cr.weight_grams or 0.0) * qty
            r.weight_missing.extend(cr.weight_missing)
        # An assembly's own process cost (time × rate) is added ON TOP of the children.
        amin, alikely, amax = self.assembly_cost(item)
        r.assembly_cost = alikely
        r.assembly_cost_min = amin
        r.assembly_cost_max = amax
        r.cost += alikely
        r.cost_min += amin
        r.cost_max += amax
        # An assembly is a cost input in its own right. Counting only leaves meant an unpriced
        # assembly was invisible and the row still read 100%.
        priced = self.assembly_priced(item)
        if _asm_covered:
            # An ancestor's quoted cost covers this one, so it is not a gap. If someone has
            # nevertheless entered a cost here, that is a contradiction worth surfacing.
            if priced:
                r.covered_conflict.append(item_id)
        else:
            r.total += 1
            if priced:
                r.covered += 1
            else:
                r.missing_assembly.append(item_id)
        self._rollup_cache[key] = r
        return r

    def flatten_leaves(self, root: str) -> dict[str, float]:
        """Effective quantity of each leaf part within `root` (qty multiplied along
        every path, summed across shared usages). Powers the cost/weight treemaps."""
        acc: dict[str, float] = {}

        def walk(item_id: str, qty: float, seen: frozenset[str]) -> None:
            kids = self.children.get(item_id, [])
            if not kids:
                acc[item_id] = acc.get(item_id, 0.0) + qty
                return
            inner = seen | {item_id}
            for child, q in kids:
                if child in inner:  # cycle guard
                    continue
                walk(child, qty * q, inner)

        if root in self.items:
            walk(root, 1.0, frozenset())
        return acc

    def flatten_assemblies(self, root: str) -> dict[str, float]:
        """Effective quantity of each assembly (non-leaf) within `root` — including the
        root itself. Powers per-assembly cost contributions in the treemap."""
        acc: dict[str, float] = {}

        def walk(item_id: str, qty: float, seen: frozenset[str]) -> None:
            kids = self.children.get(item_id, [])
            if not kids:
                return
            acc[item_id] = acc.get(item_id, 0.0) + qty
            inner = seen | {item_id}
            for child, q in kids:
                if child in inner:
                    continue
                walk(child, qty * q, inner)

        if root in self.items:
            walk(root, 1.0, frozenset())
        return acc

    def assembly_time_total(self, item_id: str, _seen: frozenset[str] = frozenset()) -> float:
        """Recursive most-likely assembly minutes at the current tier."""
        if item_id in _seen:
            return 0.0
        t = self.labor.get(item_id)
        total = float(t[1]) if t else 0.0  # time_likely at this tier
        seen = _seen | {item_id}
        for child, qty in self.children.get(item_id, []):
            total += self.assembly_time_total(child, seen) * qty
        return total

    def node(self, item_id: str, qty: float = 1, _seen: frozenset[str] = frozenset()) -> dict:
        """Nested tree node with embedded rollup. Cycle-safe."""
        item = self.items[item_id]
        r = self.rollup(item_id)
        children = []
        if item_id not in _seen:
            seen = _seen | {item_id}
            children = [
                self.node(c, q, seen) for c, q in self.children.get(item_id, []) if c in self.items
            ]
        return {
            "item_id": item.item_id,
            "item_name": item.item_name,
            "item_type": item.item_type,
            "module_code": item.module_code,
            "is_top_level": item.is_top_level,
            "quantity": qty,
            "has_children": bool(self.children.get(item_id)),
            "rollup_cost": round(r.cost, 2),
            "rollup_cost_min": round(r.cost_min, 2),
            "rollup_cost_max": round(r.cost_max, 2),
            # Sent explicitly: the client used to derive it as parent - sum(children x qty), which
            # drifts by cents once every term has been rounded to 2dp (worst case seen: 2.66 vs 2.50).
            "assembly_cost": round(r.assembly_cost, 2),
            "assembly_cost_min": round(r.assembly_cost_min, 2),
            "assembly_cost_max": round(r.assembly_cost_max, 2),
            "assembly_priced": self.assembly_priced(item) if self.children.get(item_id) else None,
            "coverage": round(r.coverage, 4),
            "rollup_weight_grams": round(r.weight_grams, 2) if r.weight_grams is not None else None,
            "children": children,
        }
