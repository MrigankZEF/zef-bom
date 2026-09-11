"""Independent audit of the COGS ladder, against live data.

`tests/test_invariants.py` proves the arithmetic on fixtures. This proves it on the real
database, which is where a wrong assumption actually shows up — a facility nobody finished
filling in, a lock left on a row whose cells were then edited, a kind whose rows changed
under stored values.

Deliberately does NOT reuse `app.cogs`. It re-derives the four rungs from the raw tables the
long way — every facility, every sub-item, every row, resolving locks by hand — and then
checks that `cogs.compute` agrees. The point is to check the module, not to trust it.

    python scripts/audit_cogs.py            # every top-level BOM, all three tiers
    python scripts/audit_cogs.py AEC066A    # one root
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app import cogs as ladder  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import CogsFacility, CogsFacilityItem, CogsLock, CogsValue  # noqa: E402
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

facs = list(db.execute(select(CogsFacility).where(CogsFacility.archived.is_(False))).scalars())
items_by_fac: dict[int, list[CogsFacilityItem]] = {}
for it in db.execute(select(CogsFacilityItem)).scalars():
    items_by_fac.setdefault(it.facility_id, []).append(it)
locks_by_fac: dict[int, set[str]] = {}
for lk in db.execute(select(CogsLock)).scalars():
    locks_by_fac.setdefault(lk.facility_id, set()).add(lk.row_key)
# (facility, item_id, row_key, tier) -> value, straight off the table.
cells: dict[tuple[int, str, str, int], float] = {
    (v.facility_id, v.item_id, v.row_key, v.volume_tier): float(v.value)
    for v in db.execute(select(CogsValue)).scalars()
}

print(f"{len(facs)} live facilities, {sum(len(v) for v in items_by_fac.values())} sub-items, "
      f"{len(cells)} stored cells")

# ── data hygiene: the things that make a number quietly wrong ─────────────────────────────
kind_of = {f.id: f.kind for f in facs}
kind_of_code = {f.code: f.kind for f in facs}
all_fac_ids = {f.id for f in db.execute(select(CogsFacility)).scalars()}

orphan_cells = [k for k in cells if k[0] not in all_fac_ids]
print(f"cells on a facility that no longer exists: {len(orphan_cells)}")
check(not orphan_cells, f"{len(orphan_cells)} orphaned cogs_value rows")

# A cell whose row_key is not in its facility's kind contributes to nothing. The router
# rejects these on write, so any here predate that or came in through a restore.
foreign = [
    k for k in cells
    if k[0] in kind_of and ladder.row_of(kind_of[k[0]], k[2]) is None
]
print(f"cells whose row_key is not a row on their kind: {len(foreign)}")
for k in foreign[:10]:
    print("   ", f"facility {k[0]} ({kind_of.get(k[0])}) row '{k[2]}' @{k[3]}")
check(not foreign, f"{len(foreign)} cogs_value rows under an unknown row_key")

bad_tier = [k for k in cells if k[3] not in TIERS]
print(f"cells at a tier that is not a real tier: {len(bad_tier)}")
check(not bad_tier, f"{len(bad_tier)} cogs_value rows at a foreign tier")

item_ids = {str(it.id) for v in items_by_fac.values() for it in v}
dangling = [k for k in cells if k[1] != ladder.OWN and k[1] not in item_ids]
print(f"cells pointing at a deleted sub-item: {len(dangling)}")
check(not dangling, f"{len(dangling)} cogs_value rows under a missing sub-item")

# A lock on a row the kind no longer declares. Harmless to the arithmetic — `own_roll` only
# ever walks `rows_for(kind)`, so it is never visited — but it is invisible in the UI too,
# which makes it the kind of thing that survives a schema change and confuses the next reader.
stray_locks = [
    (f.code, rk) for f in facs
    for rk in locks_by_fac.get(f.id, set())
    if ladder.row_of(f.kind, rk) is None
]
print(f"locks on a row that no longer exists: {len(stray_locks)}")
for code, rk in stray_locks[:10]:
    print("   ", f"{code}: '{rk}'")
for code, rk in stray_locks:
    warns.append(f"{code}: lock on '{rk}', which is not a row on a '{kind_of_code[code]}' "
                 f"facility any more — it does nothing and cannot be seen")

# A locked row with no facility-own value at a tier contributes 0 for the whole facility,
# which is a silent hole rather than an error — the em dash is invisible once it is locked.
for f in facs:
    for row_key in locks_by_fac.get(f.id, set()):
        row = ladder.row_of(f.kind, row_key)
        if row is None:
            continue
        for tier in TIERS:
            if (f.id, ladder.OWN, row_key, tier) not in cells:
                warns.append(
                    f"{f.code}: '{row_key}' is locked but has no value @{tier} — the whole "
                    f"facility contributes 0 for that row"
                )

# ── the ladder, re-derived the long way ───────────────────────────────────────────────────
def long_way(tier: int) -> dict:
    """Re-accumulate every rung from the raw tables, resolving locks by hand."""
    oh = oh_unit = other = post = warr = area = fte = 0.0
    yf = 1.0
    for f in facs:
        locks = locks_by_fac.get(f.id, set())
        rows = ladder.rows_for(f.kind)
        subs = items_by_fac.get(f.id, [])
        # No sub-items: the facility IS the record, so every row is costed from its own
        # values whether locked or not. Recomputed here rather than asked of `app.cogs` —
        # the point of this script is to check that module, not to trust it.
        solo = not subs

        def cell(item_key: str, k: str) -> float:
            return cells.get((f.id, item_key, k, tier), 0.0)

        # A rate needed by a locked quantity row: the facility's own if that rate is locked
        # too, otherwise the unweighted mean of the non-zero sub-item values.
        def rate_for(k: str) -> float:
            if solo or k in locks:
                return cell(ladder.OWN, k)
            vals = [cell(str(s.id), k) for s in subs]
            nz = [v for v in vals if v]
            return (sum(nz) / len(nz)) if nz else 0.0

        for r in rows:
            k, b = r["k"], r["basis"]
            if solo or k in locks:
                v = cell(ladder.OWN, k)
                if b == "oh":
                    oh += v
                elif b == "fte":
                    oh += v * rate_for("salary")
                    fte += v
                elif b == "area":
                    oh += v * rate_for("rent")
                    area += v
                elif b == "direct":
                    other += v
                elif b == "toolTotal":
                    u = rate_for("toolUnits")
                    oh_unit += (v / u) if u else 0.0
                elif b == "scrap":
                    yf *= 1 - min(max(v, 0.0), ladder.SCRAP_MAX) / 100
                elif b == "post":
                    post += v
                elif b == "warranty":
                    warr += v
                elif b == "info":
                    area += v
                continue
            if solo:
                continue        # handled above; there is nothing below to walk
            for s in subs:
                key = str(s.id)

                def sub_rate(rk: str, key=key) -> float:
                    rr = ladder.row_of(f.kind, rk)
                    if rr is not None and rk in locks:
                        return cell(ladder.OWN, rk) if ladder.inherits(rr["basis"]) else 0.0
                    return cell(key, rk)

                v = cell(key, k)
                if b == "oh":
                    oh += v
                elif b == "fte":
                    oh += v * sub_rate("salary")
                    fte += v
                elif b == "area":
                    oh += v * sub_rate("rent")
                    area += v
                elif b == "direct":
                    other += v
                elif b == "toolTotal":
                    u = sub_rate("toolUnits")
                    oh_unit += (v / u) if u else 0.0
                elif b == "scrap":
                    yf *= 1 - min(max(v, 0.0), ladder.SCRAP_MAX) / 100
                elif b == "post":
                    post += v
                elif b == "warranty":
                    warr += v
                elif b == "info":
                    area += v
    return {"oh": oh, "oh_unit": oh_unit, "other": other, "post": post, "warr": warr,
            "yf": yf, "area": area, "fte": fte}


roots_wanted = sys.argv[1:]
for tier in TIERS:
    g = BomGraph(db, volume_tier=tier)
    roots = [r.item_id for r in g.roots()]
    if roots_wanted:
        roots = [r for r in roots if r in roots_wanted]

    mine = long_way(tier)
    theirs = ladder.all_roll(_facs_at := [
        {
            "kind": f.kind, "code": f.code,
            "locks": locks_by_fac.get(f.id, set()),
            "own": {
                k[2]: v for k, v in cells.items()
                if k[0] == f.id and k[1] == ladder.OWN and k[3] == tier
            },
            "items": [
                {"values": {
                    k[2]: v for k, v in cells.items()
                    if k[0] == f.id and k[1] == str(s.id) and k[3] == tier
                }}
                for s in items_by_fac.get(f.id, [])
            ],
        }
        for f in facs
    ])

    # The accumulators must agree before any rung can be trusted.
    for name, a, b in (
        ("overhead pool", mine["oh"], theirs.oh),
        ("tooling per plant", mine["oh_unit"], theirs.oh_unit),
        ("direct other", mine["other"], theirs.other),
        ("post", mine["post"], theirs.post),
        ("warranty pct", mine["warr"], theirs.warr_pct),
        ("yield factor", mine["yf"], theirs.yield_f),
        ("area", mine["area"], theirs.area),
        ("fte", mine["fte"], theirs.fte),
    ):
        check(close(a, b, 1e-6), f"tier {tier}: {name} disagrees — audit {a:,.4f} vs cogs {b:,.4f}")

    print(f"\ntier {tier}: pool={theirs.oh:,.2f}  other={theirs.other:,.2f}  "
          f"post={theirs.post:,.2f}  scrap={(1 - theirs.yield_f) * 100:.4f}%  "
          f"warranty={theirs.warr_pct:.2f}%")

    for root in roots:
        r = g.rollup(root)
        L = ladder.compute(units_per_year=tier, rollup_cost=r.cost,
                           rollup_assembly_cost=r.assembly_cost, facilities=_facs_at,
                           assembly_minutes=g.assembly_time_total(root))

        # The identities the plan commits to, checked against the rungs as served.
        check(close(L.bom + L.labour, r.cost),
              f"{root}@{tier}: the L1 partition does not reconstruct the rollup")
        check(close(L.bom_adj, r.cost / (theirs.yield_f or 1)),
              f"{root}@{tier}: scrap is not grossing the whole BOM cost")
        check(close(L.direct, L.bom_adj + L.other),
              f"{root}@{tier}: direct != bom_adj + other")
        check(close(L.overhead, L.pool_total / tier + theirs.oh_unit),
              f"{root}@{tier}: overhead is not the pool over the tier plus amortised tooling")
        check(close(L.burdened, L.direct + L.overhead),
              f"{root}@{tier}: COGM != direct + overhead")
        check(close(L.warranty, L.burdened * L.warranty_pct / 100),
              f"{root}@{tier}: warranty is not a percentage of burdened")
        check(close(L.cogs_unit, L.burdened + L.post + L.warranty),
              f"{root}@{tier}: COGS != burdened + post + warranty")
        check(L.consumables <= L.other + EPS,
              f"{root}@{tier}: consumables exceed the direct total they are part of")

        gaps = len(set(r.missing)) + len(set(r.missing_assembly)) + len(set(r.missing_quote))
        flag = f"  FLOOR ({gaps} uncosted)" if gaps else ""
        print(f"   {root:<10} BOM {r.cost:>12,.2f}  direct {L.direct:>12,.2f}  "
              f"COGM {L.burdened:>13,.2f}  COGS {L.cogs_unit:>13,.2f}{flag}")

# ── the one thing that cannot be checked, only reported ──────────────────────────────────
# Full allocation means every root absorbs 100% of every pool. Two roots' COGS figures are
# therefore not addable, and no total across them is printed here — by design.
if len(facs) and not roots_wanted:
    print("\nEvery facility is fully allocated to EACH root above. Those COGS figures cannot"
          "\nbe added together — doing so counts the company once per BOM.")

for w in warns:
    print(f"\nWARN  {w}")
for f_ in fails:
    print(f"\nFAIL  {f_}")

db.close()
print("\nDONE" if not fails else f"\nDONE WITH {len(fails)} FAILURES")
sys.exit(1 if fails else 0)
