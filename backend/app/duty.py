"""Import duty on a BOM: which lines are dutiable, at what rate, on what value.

Four decisions worth stating before any arithmetic, because each one is a place where a
plausible shortcut is wrong.

**Import VAT is never in here, and must never be added.** VAT on an import into Portugal is
recoverable — it is a cash-flow event, not a cost — so putting 23% into a COGS figure would
overstate the cost of goods by roughly a quarter. Duty is different: it is not recoverable
and it genuinely is part of what the part costs to have. If someone later adds a `vat_pct`
to `duty_rates`, this is the paragraph that says why the roll-up must still ignore it.

**Only bought lines are dutiable.** Duty is charged when goods cross a border, so an item
we make in the hall from imported material is not itself a customs line; the material under
it is. `DecidedCost.make_or_buy` is the switch — `buy` and `made-to-order` cross the border,
`make` does not. That makes dutiability a **per-tier** question even though the HS code
belongs to the physical thing, because a part can be `make@1` as a prototype and `buy@10k`
once a supplier will tool for it.

**A bought-in assembly is ONE customs line.** `covers='all'` means a supplier price replaced
the whole subtree; it arrives in a box under one HS code, and walking inside it to duty the
individual parts would invent lines that never appeared on a customs declaration.
`BomGraph.flatten_leaves` already stops at that boundary, which is exactly the right set.

**A missing HS code is missing, not zero.** The same rule the cost coverage machinery
follows: a dutiable line with no classification is a gap to report, never a silent € 0. A
duty total on a partly classified BOM is a FLOOR, and `coverage` here exists so the screen
can say so.

One thing this module does not solve, and says so rather than pretending: the customs value
is normally **CIF** — goods plus freight and insurance to the border — not ex-works. Using
the decided unit cost understates it, by however much the freight is, which is the same
number the `inbound` rung of the COGS ladder is trying to hold. Fixing it properly means
deciding how inbound freight is apportioned across lines, and that is a separate decision
with its own inputs. `basis` in the output names what was actually used, so nothing here
silently claims to be CIF.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DutyRate

# The sourcing values that mean "this crosses a border". `make` is in-house and is not a
# customs line — the material beneath it is.
DUTIABLE_SOURCING = ("buy", "made-to-order")

# Where the plant is when nothing says otherwise. Read from `cogs_facility.country` when a
# facility declares one; this is the fallback so a duty figure is possible before the
# facilities screen has been filled in.
DEFAULT_DESTINATION = "PT"

# The origin used when an item does not name one. Not a guess at a country — a marker that
# the third-country wildcard rate applies, which is also what `origin_country=''` means in
# the rate table.
WILDCARD = ""


def normalize_hs(code: str | None) -> str:
    """`8544.42.90` -> `85444290`. Digits only, so a prefix match is a string prefix."""
    if not code:
        return ""
    return "".join(ch for ch in str(code) if ch.isdigit())


def load_rates(db: Session, destination: str) -> list[DutyRate]:
    """Live rates for one destination, longest HS code first.

    Sorted here rather than at each lookup: the whole table is a few hundred rows at most,
    and `pick_rate` then simply takes the first row that matches.
    """
    rows = list(db.execute(
        select(DutyRate).where(
            DutyRate.destination_country == destination,
            DutyRate.archived.is_(False),
        )
    ).scalars())
    # Longest prefix wins; then a named origin beats the wildcard; then the most recently
    # valid rate. `valid_from` is nullable and an undated rate sorts oldest — a dated rate is
    # better evidence than an undated one, so it should win a tie.
    rows.sort(key=lambda r: (
        -len(normalize_hs(r.hs_code)),
        0 if r.origin_country else 1,
        -(r.valid_from or date.min).toordinal(),
    ))
    return rows


def pick_rate(rates: list[DutyRate], hs: str, origin: str, on: date | None = None) -> DutyRate | None:
    """The rate that applies to this code and origin — longest matching HS prefix.

    `rates` must be pre-sorted by `load_rates`, so the first match is the best one. A rate
    whose `valid_from` is in the future of `on` is skipped: a schedule published today for
    January does not price a shipment that already landed. Omitted dates mean today.
    """
    code = normalize_hs(hs)
    if not code:
        return None
    effective_date = on if on is not None else date.today()
    for r in rates:
        rc = normalize_hs(r.hs_code)
        if not code.startswith(rc):
            continue
        if r.origin_country and r.origin_country != origin:
            continue
        if r.valid_from is not None and r.valid_from > effective_date:
            continue
        return r
    return None


def facility_destination(db: Session) -> str:
    """The country the plant lands in, from the facilities that declare one.

    Assembly first: that is where the BOM arrives. A logistics facility in another country
    is a real thing to model one day, and modelling it would mean duty per facility rather
    than per BOM — which is a bigger change than this module, so the destination stays
    single here and the ladder's typed `duty` rung remains available for anything else.
    """
    from .models import CogsFacility

    facs = list(db.execute(
        select(CogsFacility).where(
            CogsFacility.archived.is_(False), CogsFacility.country.is_not(None)
        )
    ).scalars())
    for kind in ("assembly", "logistics", "field"):
        for f in facs:
            if f.kind == kind and f.country:
                return f.country
    return DEFAULT_DESTINATION


def duty_for_bom(db: Session, g, root: str, destination: str | None = None,
                 on: date | None = None) -> dict:
    """Duty per dutiable line in `root`, at `g`'s tier, plus the totals and the gaps.

    `g` is a `BomGraph` — which also means a MILESTONE's graph works here unchanged, so a
    duty figure can be compared across a snapshot the same way a cost can.
    """
    on = on if on is not None else date.today()
    dest = destination or facility_destination(db)
    rates = load_rates(db, dest)

    leaves = g.flatten_leaves(root)          # stops at bought-in assemblies, which is right
    if not leaves:
        return _empty(root, g.volume, dest)

    # Items and sourcing come from the GRAPH, never from a fresh query. That is what makes a
    # milestone's duty the milestone's: re-reading `items` here would classify a frozen BOM
    # with today's HS codes and report no change after a reclassification. Rates stay live —
    # a published tariff is a fact about the world now, not part of the snapshot — so a duty
    # comparison across a milestone shows how the CLASSIFICATION and sourcing moved, priced
    # at today's schedule. Stated because it is a choice, not an accident.
    lines, total, dutiable, classified = [], 0.0, 0, 0
    for iid, eff in leaves.items():
        it = g.items.get(iid)
        if it is None:
            continue
        how = g.sourcing.get(iid)
        if how not in DUTIABLE_SOURCING:
            # Made here, or sourcing never decided. Not a customs line, and not a gap in
            # THIS module — an undecided make_or_buy is already Pending's business.
            continue
        dutiable += 1
        est = g.decided.get((iid, g.volume))
        # Ex-works, not CIF. See the module docstring: the shortfall is the inbound freight,
        # and `basis` says so rather than letting the number pass as a customs value.
        value = (est[1] if est is not None else 0.0) * eff
        hs = normalize_hs(it.hs_code)
        origin = (it.country_of_origin or WILDCARD).strip().upper()[:2]
        rate = pick_rate(rates, hs, origin, on) if hs else None
        amount = value * float(rate.rate_pct) / 100.0 if rate is not None else 0.0
        if hs:
            classified += 1
        if rate is not None:
            total += amount
        lines.append({
            "item_id": iid, "item_name": it.item_name, "eff_qty": round(eff, 3),
            "hs_code": it.hs_code, "origin": origin or None,
            "customs_value": round(value, 2), "basis": "ex-works",
            "rate_pct": float(rate.rate_pct) if rate is not None else None,
            "rate_source": rate.source if rate is not None else None,
            "rate_valid_from": rate.valid_from.isoformat() if rate is not None and rate.valid_from else None,
            "duty": round(amount, 2),
            # Two different gaps with two different fixes: classify the part, or enter the
            # rate for a code that is already classified.
            "missing_hs": not hs,
            "missing_rate": bool(hs) and rate is None,
        })

    lines.sort(key=lambda x: -x["duty"])
    missing_hs = sorted(x["item_id"] for x in lines if x["missing_hs"])
    missing_rate = sorted(x["item_id"] for x in lines if x["missing_rate"])
    priced = sum(1 for x in lines if x["rate_pct"] is not None)
    return {
        "root": root,
        "volume_tier": g.volume,
        "destination": dest,
        "lines": lines,
        "totals": {
            "duty": round(total, 2),
            "customs_value": round(sum(x["customs_value"] for x in lines), 2),
            "dutiable_lines": dutiable,
            "priced_lines": priced,
            "coverage": round(priced / dutiable, 4) if dutiable else 1.0,
            "missing_hs": missing_hs,
            "missing_rate": missing_rate,
            # A duty total on a partly classified BOM is a floor, and has to say so.
            "is_floor": bool(missing_hs or missing_rate),
            "basis": "ex-works",
            "vat_excluded": True,
        },
    }


def _empty(root: str, tier: int, dest: str) -> dict:
    return {
        "root": root, "volume_tier": tier, "destination": dest, "lines": [],
        "totals": {
            "duty": 0.0, "customs_value": 0.0, "dutiable_lines": 0, "priced_lines": 0,
            "coverage": 1.0, "missing_hs": [], "missing_rate": [], "is_floor": False,
            "basis": "ex-works", "vat_excluded": True,
        },
    }
