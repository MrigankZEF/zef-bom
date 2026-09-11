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

from .models import AssemblyLabor, BomLink, Item
from .rows import BomRows, load_rows


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


# How far each item is covered from ABOVE, per volume tier.
#
# `AssemblyLabor.covers` says how far one assembly's cost reaches DOWN. The question the review
# queue needs is the mirror of that: for this item, is there any way down to it from a live
# top-level root that is NOT already paid for? Because if every path to it is covered, then
# filling in its numbers changes nothing anybody will ever read — and the queue was asking for
# them anyway, which is how it stayed permanently red on rows nobody could usefully close.
#
# "Every path" is the whole point. An item used once under a covering assembly and once under
# one that does not is a REAL gap: the second usage needs the number. So the weakest claim
# along any path wins, and that is what the relaxation below computes.
_OPEN, _LABOR, _BOUNDARY = 0, 1, 2
COVER_STATE = {_OPEN: "open", _LABOR: "labor", _BOUNDARY: "boundary"}


def cover_reach(db: Session, tiers: tuple[int, ...] = (1, 100, 10000)) -> dict[str, dict[int, tuple[str, str | None]]]:
    """`{item_id: {tier: (state, covered_by)}}` for every item in a live BOM.

    `state` is one of:
      * `open`     — at least one path down to this item is not covered. A missing number here
                     is a real gap.
      * `labor`    — every path is under an ancestor whose `covers='labor'`. Nothing needs
                     filling, but note that the rollup still ADDS an assembly cost entered
                     here, on top of the covering assembly's own — see the drawer's note.
      * `boundary` — every path is under a `covers='all'` assembly, whose one quoted price
                     replaced the whole subtree. Anything entered below it is discarded.

    `covered_by` names the nearest ancestor that established the cover, so the UI can say
    *which* assembly is paying for this one rather than just that something is.
    """
    live = {
        it.item_id: it
        for it in db.execute(select(Item).where(Item.archived.is_(False))).scalars()
    }
    kids: dict[str, list[str]] = {}
    for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars():
        if bl.parent_item_id in live and bl.child_item_id in live:
            kids.setdefault(bl.parent_item_id, []).append(bl.child_item_id)
    covers: dict[tuple[str, int], str] = {
        (al.item_id, al.volume_tier): al.covers
        for al in db.execute(select(AssemblyLabor)).scalars()
        if al.covers and al.covers != "none"
    }

    out: dict[str, dict[int, tuple[str, str | None]]] = {}
    roots = [iid for iid, it in live.items() if it.is_top_level]
    for tier in tiers:
        # Relaxation rather than a plain walk: an item can be reached by many paths and the
        # weakest one decides. Only re-expanding when a WEAKER state arrives makes this
        # terminate on a shared sub-assembly and on a cycle alike.
        best: dict[str, tuple[int, str | None]] = {}
        queue: list[str] = []
        for r in roots:
            if best.get(r, (99, None))[0] > _OPEN:
                best[r] = (_OPEN, None)
                queue.append(r)
        while queue:
            cur = queue.pop()
            rank, by = best[cur]
            own = covers.get((cur, tier), "none")
            # The cover applies to what is BELOW `cur`, never to `cur` itself.
            if rank == _BOUNDARY:
                child = (_BOUNDARY, by)
            elif own == "all":
                child = (_BOUNDARY, cur)
            elif rank == _LABOR:
                child = (_LABOR, by)
            elif own == "labor":
                child = (_LABOR, cur)
            else:
                child = (_OPEN, None)
            for k in kids.get(cur, ()):
                if best.get(k, (99, None))[0] > child[0]:
                    best[k] = child
                    queue.append(k)
        for iid, (rank, by) in best.items():
            out.setdefault(iid, {})[tier] = (COVER_STATE[rank], by)
    return out


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
    # covers='all' assemblies with no decided cost at this tier. A quoted assembly whose
    # quote was never entered is a real gap, not a silent €0 — it gets its own list rather
    # than joining `missing_assembly`, because the fix is a price, not a time.
    missing_quote: list[str] = field(default_factory=list)
    # Everything documented under a covers='all' assembly: real items, deliberately not
    # costed, so the UI can say "14 items under 2 quoted assemblies" instead of hiding them.
    below_boundary: list[str] = field(default_factory=list)
    weight_grams: float | None = 0.0
    weight_missing: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return 1.0 if self.total == 0 else self.covered / self.total


class BomGraph:
    """The BOM as dicts, plus every number derived from it.

    Built from a `Session` in the request path, or from `rows` — which is how a stored
    milestone gets costed by exactly this code rather than by a second implementation of it.
    See `rows.py` for why that seam is where it is.
    """

    def __init__(self, db: Session | None = None, volume_tier: int = 100, *, rows: BomRows | None = None):
        if rows is None:
            if db is None:
                raise ValueError("BomGraph needs a Session or a BomRows")
            rows = load_rows(db, volume_tier)
        self.volume = volume_tier
        # Archived items/links are excluded by the loader (soft-delete).
        self.items: dict[str, Item] = {it.item_id: it for it in rows.items}
        self.children: dict[str, list[tuple[str, float]]] = {}
        self.parents: dict[str, list[tuple[str, float]]] = {}
        for link in rows.links:
            if link.parent_item_id not in self.items or link.child_item_id not in self.items:
                continue
            self.children.setdefault(link.parent_item_id, []).append(
                (link.child_item_id, link.quantity)
            )
            self.parents.setdefault(link.child_item_id, []).append(
                (link.parent_item_id, link.quantity)
            )
        # (cost_min, most_likely, cost_max) — min/max default to the likely value when blank.
        # Filtered by tier here as well as in the query: `rows` may legitimately carry all
        # three tiers, because a snapshot stores all of them.
        self.decided: dict[tuple[str, int], tuple[float, float, float]] = {
            (dc.item_id, dc.volume_tier): (
                float(dc.cost_min) if dc.cost_min is not None else float(dc.unit_cost_eur),
                float(dc.unit_cost_eur),
                float(dc.cost_max) if dc.cost_max is not None else float(dc.unit_cost_eur),
            )
            for dc in rows.decided
            if dc.volume_tier == volume_tier
        }
        # Assembly labour: minutes (min, likely, max) per item at this tier, + the €/h rates.
        _labor_rows = [al for al in rows.labor if al.volume_tier == volume_tier]
        # A row with no most-likely time carries no labour — that is the normal state of a
        # covers='all' assembly, which is bought as a finished unit — so it is left out
        # entirely rather than stored as a None that every caller has to re-check.
        self.labor: dict[str, tuple[float | None, float, float | None]] = {
            al.item_id: (al.time_min, al.time_likely, al.time_max)
            for al in _labor_rows
            if al.time_likely is not None
        }
        # How far each assembly's cost reaches down: 'none' | 'labor' | 'all'. See
        # AssemblyLabor.covers — 'labor' excuses the work below, 'all' replaces the whole
        # subtree with one quoted price.
        self.covers: dict[str, str] = {
            al.item_id: al.covers for al in _labor_rows if al.covers != "none"
        }
        # Assembly cost types are reference values (category 'assembly_cost_type') with a
        # €/hour rate in meta; keyed by the reference value's id (= item.cost_type_id).
        self.rates: dict[int, float] = {
            rv.id: float((rv.meta or {}).get("rate_eur_h") or 0.0)
            for rv in rows.rates
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

    # ── the cost boundary ────────────────────────────────────────────────────
    def is_boundary(self, item_id: str) -> bool:
        """True for an assembly bought as one quoted unit at this tier.

        Its own decided cost is the whole cost; the subtree below it is documentation.
        Deliberately independent of whether the quote has actually been entered — an
        unpriced boundary is a gap to report, not a licence to start summing the contents
        again behind the user's back.
        """
        return bool(self.children.get(item_id)) and self.covers.get(item_id) == "all"

    def descendants(self, item_id: str) -> set[str]:
        """Every item below `item_id`, boundaries included. Cycle-safe."""
        out: set[str] = set()
        stack = [c for c, _ in self.children.get(item_id, [])]
        while stack:
            cur = stack.pop()
            if cur in out or cur not in self.items:
                continue
            out.add(cur)
            stack.extend(c for c, _ in self.children.get(cur, []))
        return out

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
        covers = self.covers.get(item_id, "none")

        if covers == "all":
            # A bought-in assembly: one supplier price for the finished unit, covering the
            # parts and the labour below it. The subtree is still walked — for weight, which
            # is physical and owes nothing to how the thing was procured — but every cost
            # and coverage number it produces is discarded, because this one price replaced
            # all of them.
            est = self.decided.get((item_id, self.volume))
            cmin, likely, cmax = est if est is not None else (0.0, 0.0, 0.0)
            w, wmissing = 0.0, []
            for child, qty in kids:
                cr = self.rollup(child, seen, True)
                w += (cr.weight_grams or 0.0) * qty
                wmissing.extend(cr.weight_missing)
            if w == 0.0 and item is not None and item.weight_grams is not None:
                # Nothing below was ever weighed, but the finished unit was — which is the
                # normal case for something that arrives from a supplier in one box.
                w, wmissing = float(item.weight_grams), []
            r = Rollup(
                cost=likely, cost_min=cmin, cost_max=cmax,
                covered=1 if est is not None else 0,
                total=1,
                missing_quote=[] if est is not None else [item_id],
                below_boundary=sorted(self.descendants(item_id)),
                weight_grams=w, weight_missing=wmissing,
            )
            self._rollup_cache[key] = r
            return r

        r = Rollup(weight_grams=0.0)
        # Everything below a covering assembly is already paid for by this one quoted cost.
        child_covered = _asm_covered or covers == "labor"
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
            r.missing_quote.extend(cr.missing_quote)
            r.below_boundary.extend(cr.below_boundary)
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

    def flatten_leaves(self, root: str, explode_boundaries: bool = False) -> dict[str, float]:
        """Effective quantity of each priced leaf within `root` (qty multiplied along
        every path, summed across shared usages). Powers the cost/weight treemaps.

        A bought-in assembly counts as a leaf: it carries the one price, and descending
        past it would emit rows that cannot add up to that price. `explode_boundaries`
        walks through anyway, for the views that want the physical explosion — where-used,
        engineering, materials — rather than the costing one.
        """
        acc: dict[str, float] = {}

        def walk(item_id: str, qty: float, seen: frozenset[str]) -> None:
            kids = self.children.get(item_id, [])
            stop = not explode_boundaries and item_id != root and self.is_boundary(item_id)
            if not kids or stop:
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

    def flatten_assemblies(self, root: str, explode_boundaries: bool = False) -> dict[str, float]:
        """Effective quantity of each assembly (non-leaf) within `root` — including the
        root itself. Powers per-assembly cost contributions in the treemap.

        A bought-in assembly is skipped: `flatten_leaves` already emitted it as the priced
        row, and counting it here too would bill its quote twice.
        """
        acc: dict[str, float] = {}

        def walk(item_id: str, qty: float, seen: frozenset[str]) -> None:
            kids = self.children.get(item_id, [])
            if not kids:
                return
            if not explode_boundaries and item_id != root and self.is_boundary(item_id):
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

    def flat_rows(self, root: str) -> list[dict]:
        """`root` flattened to a single level: one row per distinct descendant.

        `count` is the effective quantity — the quantity multiplied along every path from
        the root and summed over paths, which is the number the plant actually needs. A
        part four deep at ×4 inside a ×6 assembly counts 24, not 4.

        Leaf rows carry a price and add up to the root's parts cost; assembly rows carry
        their own process cost (time × rate × count) and would double-count if summed with
        the leaves, hence the `is_leaf` flag for the caller to filter on.

        A bought-in assembly appears once, as a priced row with its quote, and its contents
        do not appear at all — the supplier is the one buying them. `is_boundary` marks it
        so the row can be badged rather than read as an ordinary part.
        """
        rollup = self.rollup(root)
        parts_total = sum(
            (self.decided.get((leaf, self.volume)) or (0.0, 0.0, 0.0))[1] * qty
            for leaf, qty in self.flatten_leaves(root).items()
        )
        # Share is of the whole BOM (parts + assembly labour) so the column sums to ~100%
        # across leaves and assemblies together, rather than to something over 100.
        denom = rollup.cost or parts_total or 0.0

        rows: list[dict] = []
        for leaf, qty in self.flatten_leaves(root).items():
            it = self.items[leaf]
            est = self.decided.get((leaf, self.volume))
            unit = est[1] if est is not None else None
            cost = (unit or 0.0) * qty
            rows.append({
                "item_id": leaf, "item_name": it.item_name, "item_type": it.item_type,
                "module_code": it.module_code, "is_leaf": True,
                "is_boundary": self.is_boundary(leaf),
                "count": round(qty, 3),
                "unit_cost": float(unit) if unit is not None else None,
                "cost": round(cost, 2),
                "share": round(cost / denom, 4) if denom else 0.0,
                # A leaf is either priced or it is not — coverage is that, per row.
                "coverage": 1.0 if unit is not None else 0.0,
            })
        for aid, qty in self.flatten_assemblies(root).items():
            if aid == root:
                continue   # the root is the thing being flattened, not a row inside it
            it = self.items[aid]
            unit = self.assembly_cost(it)[1]     # its own process cost, per build
            cost = unit * qty
            rows.append({
                "item_id": aid, "item_name": it.item_name, "item_type": it.item_type,
                "module_code": it.module_code, "is_leaf": False, "is_boundary": False,
                "count": round(qty, 3),
                "unit_cost": round(unit, 4) if unit else None,
                "cost": round(cost, 2),
                "share": round(cost / denom, 4) if denom else 0.0,
                "coverage": round(self.rollup(aid).coverage, 4),
            })
        # Dearest first, and anything without a price last — the order costing work wants.
        rows.sort(key=lambda r: (r["unit_cost"] is None, -r["cost"], r["item_id"]))
        return rows

    def roots_reaching(self, item_id: str) -> list[dict]:
        """Which top-level BOMs contain `item_id`, and how many it needs of it.

        The inverse of `flat_rows`, for the drawer: an extended total only means something
        when exactly one BOM is asking for the part.

        Boundaries are exploded here. "Which boats need this connector" is a question about
        the physical tree, and the answer does not change because a harness shop is the one
        placing the order.
        """
        out = []
        for root in self.roots():
            qty = self.flatten_leaves(root.item_id, explode_boundaries=True).get(item_id)
            if qty is None:
                qty = self.flatten_assemblies(root.item_id, explode_boundaries=True).get(item_id)
            if qty and root.item_id != item_id:
                out.append({"root": root.item_id, "root_name": root.item_name, "count": round(qty, 3)})
        return out

    def assembly_time_total(self, item_id: str, _seen: frozenset[str] = frozenset()) -> float:
        """Recursive most-likely assembly minutes at the current tier.

        A bought-in assembly contributes nothing: neither its own minutes nor its contents'
        are hours our plant ever spends.
        """
        if item_id in _seen or self.is_boundary(item_id):
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
            # 'none' | 'labor' | 'all' at the graph's tier — the tree badges a bought-in
            # assembly rather than drawing it as one more station we build.
            "covers": self.covers.get(item_id, "none") if self.children.get(item_id) else None,
            "quote_missing": item_id in r.missing_quote,
            "coverage": round(r.coverage, 4),
            "rollup_weight_grams": round(r.weight_grams, 2) if r.weight_grams is not None else None,
            "children": children,
        }
