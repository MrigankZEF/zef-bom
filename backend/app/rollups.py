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


@dataclass
class Rollup:
    cost: float = 0.0         # most-likely: children parts + this assembly's process cost
    cost_min: float = 0.0     # 3-point estimate, summed independently up the tree
    cost_max: float = 0.0
    assembly_cost: float = 0.0  # just the process cost added at this node (for breakdown)
    assembly_cost_min: float = 0.0
    assembly_cost_max: float = 0.0
    covered: int = 0          # leaf instances with a decided cost
    total: int = 0            # leaf instances in total
    missing: list[str] = field(default_factory=list)
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
        self.labor: dict[str, tuple[float | None, float, float | None]] = {
            al.item_id: (al.time_min, al.time_likely, al.time_max)
            for al in db.execute(
                select(AssemblyLabor).where(AssemblyLabor.volume_tier == volume_tier)
            ).scalars()
        }
        # Assembly cost types are reference values (category 'assembly_cost_type') with a
        # €/hour rate in meta; keyed by the reference value's id (= item.cost_type_id).
        self.rates: dict[int, float] = {
            rv.id: float((rv.meta or {}).get("rate_eur_h") or 0.0)
            for rv in db.execute(
                select(ReferenceValue).where(ReferenceValue.category == "assembly_cost_type")
            ).scalars()
        }
        self._rollup_cache: dict[str, Rollup] = {}

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

    def rollup(self, item_id: str, _seen: frozenset[str] = frozenset()) -> Rollup:
        if item_id in self._rollup_cache:
            return self._rollup_cache[item_id]
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
            self._rollup_cache[item_id] = r
            return r
        seen = _seen | {item_id}
        r = Rollup(weight_grams=0.0)
        for child, qty in kids:
            cr = self.rollup(child, seen)
            r.cost += cr.cost * qty
            r.cost_min += cr.cost_min * qty
            r.cost_max += cr.cost_max * qty
            r.covered += cr.covered
            r.total += cr.total
            r.missing.extend(cr.missing)
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
        self._rollup_cache[item_id] = r
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
