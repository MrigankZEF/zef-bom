"""Independent audit of the BOM rollup maths.

Deliberately does NOT reuse BomGraph's recursion. It rebuilds the cost two other ways:

  A) path expansion — walk every root-to-node path, multiply quantities along it, and sum
     (leaf unit cost x effective qty) + (assembly process cost x effective qty).
  B) the exact arithmetic the treemap does — parent = sum(child rollup x qty) + own assembly.

If A, B and BomGraph all agree for every item, the number on screen is the number in the DB.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import AssemblyLabor, BomLink, DecidedCost, Item, ReferenceValue  # noqa: E402
from app.rollups import BomGraph  # noqa: E402

TIERS = [1, 100, 10000]
EPS = 0.005
fails: list[str] = []
warns: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def close(a: float, b: float, eps: float = EPS) -> bool:
    return abs(a - b) <= eps


db = SessionLocal()
items = {i.item_id: i for i in db.execute(select(Item).where(Item.archived.is_(False))).scalars()}
links = [l for l in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars()
         if l.parent_item_id in items and l.child_item_id in items]
kids: dict[str, list[tuple[str, float]]] = defaultdict(list)
for l in links:
    kids[l.parent_item_id].append((l.child_item_id, l.quantity))
roots = [i for i in items.values() if i.is_top_level]

print(f"items={len(items)} links={len(links)} roots={len(roots)}")

rates = {rv.id: float((rv.meta or {}).get("rate_eur_h") or 0.0)
         for rv in db.execute(select(ReferenceValue).where(
             ReferenceValue.category == "assembly_cost_type")).scalars()}

for tier in TIERS:
    g = BomGraph(db, volume_tier=tier)
    decided = {dc.item_id: dc for dc in db.execute(
        select(DecidedCost).where(DecidedCost.volume_tier == tier)).scalars()}
    labor = {al.item_id: al for al in db.execute(
        select(AssemblyLabor).where(AssemblyLabor.volume_tier == tier)).scalars()}

    def unit_cost(iid: str, which: str) -> float | None:
        """Leaf unit cost, straight from the DecidedCost row (no graph involved)."""
        dc = decided.get(iid)
        if dc is None:
            return None
        likely = float(dc.unit_cost_eur)
        if which == "likely":
            return likely
        v = dc.cost_min if which == "min" else dc.cost_max
        return float(v) if v is not None else likely

    def asm_cost(iid: str, which: str) -> float:
        """Process cost = minutes x EUR/min, straight from AssemblyLabor + the rate."""
        it = items[iid]
        if it.cost_type_id is None:
            return 0.0
        rate = rates.get(it.cost_type_id)
        al = labor.get(iid)
        if not rate or al is None:
            return 0.0
        t = {"likely": al.time_likely,
             "min": al.time_min if al.time_min is not None else al.time_likely,
             "max": al.time_max if al.time_max is not None else al.time_likely}[which]
        return float(t) * (rate / 60.0)

    # ---- A) path expansion: effective quantity of every node under a root -------------
    def expand(root: str) -> tuple[dict[str, float], dict[str, float]]:
        leaf_qty: dict[str, float] = defaultdict(float)
        asm_qty: dict[str, float] = defaultdict(float)
        stack = [(root, 1.0, frozenset())]
        while stack:
            iid, q, seen = stack.pop()
            ch = kids.get(iid, [])
            if not ch:
                leaf_qty[iid] += q
                continue
            asm_qty[iid] += q
            inner = seen | {iid}
            for c, cq in ch:
                if c in inner:
                    continue
                stack.append((c, q * cq, inner))
        return leaf_qty, asm_qty

    # ---- B) treemap arithmetic: parent = sum(child rollup x qty) + own assembly -------
    memo: dict[tuple[str, str], float] = {}

    def treemap_cost(iid: str, which: str, seen: frozenset[str] = frozenset()) -> float:
        key = (iid, which)
        if key in memo:
            return memo[key]
        if iid in seen:
            return 0.0
        ch = kids.get(iid, [])
        if not ch:
            v = unit_cost(iid, which) or 0.0
        else:
            inner = seen | {iid}
            v = sum(treemap_cost(c, which, inner) * q for c, q in ch) + asm_cost(iid, which)
        memo[key] = v
        return v

    for which, field in (("min", "cost_min"), ("likely", "cost"), ("max", "cost_max")):
        for it in items.values():
            iid = it.item_id
            graph_v = getattr(g.rollup(iid), field)
            tm_v = treemap_cost(iid, which)
            check(close(graph_v, tm_v),
                  f"[t{tier}/{which}] {iid}: BomGraph={graph_v:.4f} vs treemap-arithmetic={tm_v:.4f}")

        for r in roots:
            leaf_qty, asm_qty = expand(r.item_id)
            expanded = sum((unit_cost(i, which) or 0.0) * q for i, q in leaf_qty.items())
            expanded += sum(asm_cost(i, which) * q for i, q in asm_qty.items())
            graph_v = getattr(g.rollup(r.item_id), field)
            check(close(graph_v, expanded, 0.02),
                  f"[t{tier}/{which}] ROOT {r.item_id}: BomGraph={graph_v:.4f} vs path-expansion={expanded:.4f}")

    # ---- invariants ------------------------------------------------------------------
    for it in items.values():
        rl = g.rollup(it.item_id)
        check(rl.cost_min <= rl.cost + EPS, f"[t{tier}] {it.item_id}: min {rl.cost_min} > likely {rl.cost}")
        check(rl.cost <= rl.cost_max + EPS, f"[t{tier}] {it.item_id}: likely {rl.cost} > max {rl.cost_max}")
        check(rl.covered <= rl.total, f"[t{tier}] {it.item_id}: covered>{rl.total}")
        if rl.total and rl.coverage < 1 and it.is_top_level:
            warns.append(f"[t{tier}] root {it.item_id} coverage {rl.coverage:.0%} "
                         f"({rl.total - rl.covered} uncosted leaves) — total is a floor")

    # ---- what node() ships to the browser --------------------------------------------
    for r in roots:
        def walk(n: dict, depth: int = 0) -> None:
            iid = n["item_id"]
            rl = g.rollup(iid)
            check(close(n["rollup_cost"], round(rl.cost, 2), 0.005),
                  f"[t{tier}] node {iid} rollup_cost {n['rollup_cost']} != {round(rl.cost, 2)}")
            if n["children"]:
                # what the client now uses: the explicit field, which must be exact
                check(close(n["assembly_cost"], round(rl.assembly_cost, 2), 0.005),
                      f"[t{tier}] node {iid}: assembly_cost {n['assembly_cost']} != {round(rl.assembly_cost, 2)}")
                check(close(n["assembly_cost_min"], round(rl.assembly_cost_min, 2), 0.005),
                      f"[t{tier}] node {iid}: assembly_cost_min mismatch")
                check(close(n["assembly_cost_max"], round(rl.assembly_cost_max, 2), 0.005),
                      f"[t{tier}] node {iid}: assembly_cost_max mismatch")
                # and how bad the OLD derived-by-subtraction approach was, for the record
                derived = n["rollup_cost"] - sum(c["rollup_cost"] * c["quantity"] for c in n["children"])
                d = abs(derived - rl.assembly_cost)
                if d > 0.005:
                    warns.append(f"[t{tier}] node {iid}: old subtraction drift {derived - rl.assembly_cost:+.4f} EUR "
                                 f"(derived {derived:.2f} vs true {rl.assembly_cost:.2f})")
            for c in n["children"]:
                walk(c, depth + 1)
        walk(g.node(r.item_id))

    # ---- /costing/breakdown totals vs the tree ----------------------------------------
    for r in roots:
        leaves = g.flatten_leaves(r.item_id)
        parts_cost = sum((unit_cost(i, "likely") or 0.0) * q for i, q in leaves.items())
        rl = g.rollup(r.item_id)
        asm_total = sum(g.assembly_cost(items[a])[1] * q
                        for a, q in g.flatten_assemblies(r.item_id).items())
        check(close(rl.cost, parts_cost + asm_total, 0.02),
              f"[t{tier}] breakdown {r.item_id}: parts {parts_cost:.2f} + asm {asm_total:.2f} "
              f"!= rollup {rl.cost:.2f}")

print(f"\nchecks failed: {len(fails)}")
for f in fails[:40]:
    print("  FAIL", f)
print(f"\nwarnings: {len(warns)}")
for w in warns[:25]:
    print("  warn", w)

# ---- data-quality sweep (not code bugs, but they'd show wrong numbers) ----------------
print("\n-- data quality --")
for tier in TIERS:
    bad = []
    for dc in db.execute(select(DecidedCost).where(DecidedCost.volume_tier == tier)).scalars():
        lo = dc.cost_min if dc.cost_min is not None else dc.unit_cost_eur
        hi = dc.cost_max if dc.cost_max is not None else dc.unit_cost_eur
        if not (Decimal(str(lo)) <= Decimal(str(dc.unit_cost_eur)) <= Decimal(str(hi))):
            bad.append(f"{dc.item_id}: {lo}/{dc.unit_cost_eur}/{hi}")
        if dc.item_id not in items:
            bad.append(f"{dc.item_id}: ORPHAN decided_cost (item missing/archived)")
    print(f"tier {tier}: {len(bad)} bad decided_cost rows")
    for b in bad[:10]:
        print("   ", b)
    lab_bad = []
    for al in db.execute(select(AssemblyLabor).where(AssemblyLabor.volume_tier == tier)).scalars():
        if al.item_id not in items:
            lab_bad.append(f"{al.item_id}: ORPHAN assembly_labor")
        elif not kids.get(al.item_id):
            lab_bad.append(f"{al.item_id}: labour on a LEAF (never rolled up)")
    print(f"tier {tier}: {len(lab_bad)} suspicious assembly_labor rows")
    for b in lab_bad[:10]:
        print("   ", b)

# assemblies with children but no cost_type/labour => silently 0 process cost
missing_rate = [i for i in items if kids.get(i) and items[i].cost_type_id is None]
print(f"\nassemblies with NO cost type (process cost silently 0): {len(missing_rate)}")
for i in missing_rate[:10]:
    print("   ", i, items[i].item_name)

db.close()
print("\nDONE" if not fails else "\nDONE WITH FAILURES")
