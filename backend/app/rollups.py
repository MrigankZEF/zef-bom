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

from .models import BomLink, DecidedCost, Item


@dataclass
class Rollup:
    cost: float = 0.0
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
        self.decided: dict[tuple[str, int], float] = {
            (dc.item_id, dc.volume_tier): float(dc.unit_cost_eur)
            for dc in db.execute(
                select(DecidedCost).where(DecidedCost.volume_tier == volume_tier)
            ).scalars()
        }
        self._rollup_cache: dict[str, Rollup] = {}

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
        if not kids:  # leaf
            cost = self.decided.get((item_id, self.volume))
            item = self.items.get(item_id)
            w = item.weight_grams if item else None
            r = Rollup(
                cost=cost or 0.0,
                covered=1 if cost is not None else 0,
                total=1,
                missing=[] if cost is not None else [item_id],
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
            r.covered += cr.covered
            r.total += cr.total
            r.missing.extend(cr.missing)
            r.weight_grams = (r.weight_grams or 0.0) + (cr.weight_grams or 0.0) * qty
            r.weight_missing.extend(cr.weight_missing)
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

    def assembly_time_total(self, item_id: str, _seen: frozenset[str] = frozenset()) -> float:
        if item_id in _seen:
            return 0.0
        item = self.items.get(item_id)
        total = float(item.assembly_time_min_1pc or 0.0) if item else 0.0
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
            "coverage": round(r.coverage, 4),
            "rollup_weight_grams": round(r.weight_grams, 2) if r.weight_grams is not None else None,
            "children": children,
        }
