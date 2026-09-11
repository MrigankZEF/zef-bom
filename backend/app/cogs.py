"""The COGS ladder — four rungs on top of the rolled-up BOM, at one volume tier.

Pure Python. No SQLAlchemy, no FastAPI, nothing importable only from a request. The router
loads the rows and hands them here as plain dicts; this module does arithmetic and nothing
else. Three reasons it lives here rather than in the frontend, where the prototype had it:
the export router may want COGS and cannot import JSX; a ladder in JS beside a rollup in
Python is exactly the two-sources-that-drift problem the BOM tool is built to avoid; and the
arithmetic's net is `tests/test_invariants.py`, which cannot reach a browser module.

    L1  BOM          bom     = rollup.cost - rollup.assembly_cost
                     labour  = rollup.assembly_cost
    L2  Direct       direct  = rollup.cost / yield_factor + consumables + other
    L3  COGM         overhead = pool / tier          # the tier IS plants per year
                     burdened = direct + overhead
    L4  COGS         warranty = burdened * warranty_pct / 100
                     cogs     = burdened + freight + install + warranty

L1 and the labour term are a *partition* of one number, not two sums. `BomGraph.rollup`
already returns `cost = parts + assembly_cost`, so part cost and assembly cost are never
cached, never re-derived and never copied — there is nothing here for the two views to drift
apart on. A bought-in assembly priced with `covers='all'` lands in the material half, because
that is what a supplier price is.

See `docs/cogs/PLAN.md` for the closed decisions, and `docs/cogs/handoff/COST_MODEL.md` for
the basis table and the lock inheritance rules in full.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── the row schema, per facility kind ─────────────────────────────────────────────────────
# Sourced from `docs/cogs/handoff/facility-kinds.json`, minus `hours` and `rate`: direct
# labour comes from the BOM alone (PLAN 2.2 — on the fixtures the two sources differed by up
# to €16,520/plant, so adding both double-counts and picking wrong understates), plus the
# `consum` row the ladder's second rung names and the prototype had nowhere to put (PLAN 2.5).
#
# This stays in code, against the house rule that new fields should be data. A row's `basis`
# is *behaviour* — a branch in `contrib` below — so a basis typed into a table by an admin
# would be a silently uncosted row. `field_definitions` exists because USERS add fields;
# facility rows get added by US, when we add a basis handler. Different actor, different home.
KINDS: dict[str, dict] = {
    "assembly": {
        "label": "Assembly & manufacturing",
        "blurb": (
            "Floor, indirect staff and equipment sit in the overhead pool; scrap and "
            "consumables land in layer 1 as direct cost per plant."
        ),
        "rows": [
            # Floor area is not free-standing information any more: it is the quantity that
            # `rent` prices. A square metre with no rent per square metre costs nothing, and
            # a rent per square metre with no area costs nothing — the pair is the figure.
            {"k": "area", "label": "Floor area", "unit": "m²", "basis": "area"},
            {"k": "fte", "label": "Indirect headcount", "unit": "FTE", "basis": "fte"},
            {"k": "salary", "label": "Average salary", "unit": "€ / yr", "basis": "salary"},
            {"k": "rent", "label": "Rent & facilities", "unit": "€ / m² / yr", "basis": "rent"},
            # Equipment is expressed as a one-off spend amortised over a plant count, which is
            # the way a jig or a fixture is actually bought. It lands in the overhead pool:
            # amortised tooling IS depreciation, near enough, and a separate annual
            # depreciation row would have been the same money entered twice.
            {"k": "toolTotal", "label": "Tooling & fixtures", "unit": "€ total", "basis": "toolTotal"},
            {"k": "toolUnits", "label": "Amortised over", "unit": "plants", "basis": "toolUnits"},
            {"k": "maint", "label": "Maintenance & calibration", "unit": "€ / yr", "basis": "oh"},
            # One utilities bucket. Metered-per-plant and indirect-per-year were two rows for
            # one bill, and splitting them asked whoever was filling this in to apportion a
            # meter reading they do not have.
            {"k": "util", "label": "Utilities", "unit": "€ / yr", "basis": "oh"},
            {"k": "scrap", "label": "Scrap / yield loss", "unit": "% of BOM", "basis": "scrap"},
            {"k": "consum", "label": "Direct consumables", "unit": "€ / plant", "basis": "direct"},
        ],
    },
    "logistics": {
        "label": "Supply chain & logistics",
        "blurb": (
            "Inbound transport and customs are direct cost on the plant; the warehouse itself "
            "is overhead; outgoing shipping and crating sit below the manufacturing line in "
            "layer 3."
        ),
        "rows": [
            {"k": "area", "label": "Floor area", "unit": "m²", "basis": "area"},
            {"k": "fte", "label": "Headcount", "unit": "FTE", "basis": "fte"},
            {"k": "salary", "label": "Average salary", "unit": "€ / yr", "basis": "salary"},
            # Same name and same arithmetic as the assembly hall's. A warehouse's floor is
            # priced the way a hall's floor is priced; calling it "Rent & yard" and metering
            # it differently made two things of one.
            {"k": "rent", "label": "Rent & facilities", "unit": "€ / m² / yr", "basis": "rent"},
            {"k": "inbound", "label": "Inbound transport", "unit": "€ / plant", "basis": "direct"},
            {"k": "duty", "label": "Customs & duties", "unit": "€ / plant", "basis": "direct"},
            {"k": "outbound", "label": "Outgoing shipping", "unit": "€ / plant", "basis": "post"},
            {"k": "crating", "label": "Packing & crating", "unit": "€ / plant", "basis": "post"},
        ],
    },
    "field": {
        "label": "Field works",
        "blurb": (
            "Crew payroll and field equipment are overhead; travel, third-party installation "
            "and the warranty accrual are layer 3, added after the plant leaves the hall."
        ),
        "rows": [
            # The crew's own installation and commissioning work is already in these salaries,
            # which is why neither has a row of its own.
            {"k": "fte", "label": "Field crew", "unit": "FTE", "basis": "fte"},
            {"k": "salary", "label": "Average salary", "unit": "€ / yr", "basis": "salary"},
            {"k": "equip", "label": "Field equipment & tools", "unit": "€ / yr", "basis": "oh"},
            {"k": "travel", "label": "Travel & per diem", "unit": "€ / plant", "basis": "post"},
            # Work bought in from outside, NOT our crew going to site — that is in the
            # salaries above. Same row key as the old "Installation on site", deliberately
            # repurposed rather than replaced, so nothing already entered is orphaned.
            {"k": "install", "label": "3rd party installation", "unit": "€ / plant", "basis": "post"},
            {"k": "warranty", "label": "Warranty accrual", "unit": "% of burdened", "basis": "warranty"},
        ],
    },
}

# Rows that are rates, not quantities: never summed, averaged over the non-zero values when
# aggregated for display, and the set that flows DOWNWARD when locked. `hours`/`rate` are
# gone, so `rate` itself no longer appears in any kind — it stays named here because `basis`
# is the vocabulary, and a future kind may want a rate row again.
RATE_BASES = frozenset({"salary", "rate", "toolUnits", "rent"})

# The sentinel meaning "the facility's own value, for a locked row". NOT NULL, because NULLs
# compare distinct in a unique constraint on both SQLite and Postgres — a nullable item_id
# would silently permit unlimited duplicate facility-own rows, and `_natural_key_cols` drives
# restore's dedup off exactly that constraint, so a restore would multiply them.
OWN = ""

SCRAP_MAX = 95.0  # a 100% scrap row would divide the BOM by zero


def rows_for(kind: str) -> list[dict]:
    """The row schema for a kind, or [] for a kind that does not exist."""
    return KINDS.get(kind, {}).get("rows", [])


def row_of(kind: str, row_key: str) -> dict | None:
    for r in rows_for(kind):
        if r["k"] == row_key:
            return r
    return None


def inherits(basis: str) -> bool:
    """True if locking this row flows the facility's value DOWN into each sub-item's
    arithmetic, rather than counting it once at facility level."""
    return basis in RATE_BASES


def num(v) -> float:
    """Values are edited as strings and parsed on read, so a half-typed `1.` or a decimal
    comma can sit in the field without the model exploding (the `toNum` note from the
    handoff's IMPLEMENTATION.md, which `NumInput`/`toNum` in ui.jsx already do)."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return 0.0


# ── the accumulator ───────────────────────────────────────────────────────────────────────
@dataclass
class Acc:
    """What one tier's worth of facility rows adds up to, before the BOM joins it.

    `yield_f` is a *factor*, carried multiplicatively through the whole rollup and applied to
    the BOM once at the end. Yield losses at different stations multiply, they do not add:
    two stations at 2% give 0.98 x 0.98 = 0.9604, a 3.96% loss, not 4%.
    """

    oh: float = 0.0         # annual overhead pool, layer 2
    # Layer-2 cost that is already per-plant rather than per-year: amortised tooling, whose
    # whole point is a fixed cost per plant over a fixed plant count. Added to the overhead
    # figure AFTER the pool is divided by the tier — see `contrib`'s toolTotal branch.
    oh_unit: float = 0.0
    other: float = 0.0      # € per plant, layer 1
    post: float = 0.0       # € per plant, layer 3
    warr_pct: float = 0.0   # % of burdened, layer 3
    yield_f: float = 1.0
    area: float = 0.0       # carried for display, never costed
    fte: float = 0.0

    def merge(self, o: "Acc") -> "Acc":
        return Acc(
            oh=self.oh + o.oh,
            oh_unit=self.oh_unit + o.oh_unit,
            other=self.other + o.other,
            post=self.post + o.post,
            warr_pct=self.warr_pct + o.warr_pct,
            yield_f=self.yield_f * o.yield_f,   # factors compose by product, not by sum
            area=self.area + o.area,
            fte=self.fte + o.fte,
        )


def contrib(acc: Acc, basis: str, v: float, g) -> None:
    """Add one row's value to the accumulator, according to what its basis MEANS.

    `basis` is the only thing that decides that, and `g(k)` reads a sibling row on the same
    record — a salary for a headcount, a divisor for a tooling total. Adding a fourth kind is
    one entry in KINDS; adding a fourth *basis* is one branch here. Keep that split.
    """
    if basis == "oh":
        acc.oh += v
    elif basis == "fte":
        acc.oh += v * g("salary")
        acc.fte += v
    elif basis == "area":
        # Floor costs area x rent-per-square-metre, the same shape as headcount x salary.
        # Either half missing means no cost, which is correct: an unpriced floor and a
        # priced floor of zero size both cost nothing.
        acc.oh += v * g("rent")
        acc.area += v
    elif basis == "direct":
        acc.other += v
    elif basis == "toolTotal":
        units = g("toolUnits")
        # Tooling with no amortisation count contributes 0, not infinity.
        #
        # Overhead, not direct cost: amortised tooling is depreciation in all but name.
        #
        # It goes in its OWN accumulator rather than the pool, because `v / units` is euros
        # per PLANT while the pool is euros per YEAR. Adding it to `oh` would send it through
        # `pool / tier` a second time and make a EUR 1.4M jig get cheaper per plant the more
        # plants you build — which is precisely what amortising over a fixed plant count says
        # it does not do. `compute` adds this on after dividing the pool.
        #
        # Keeping it separate is also what lets `contrib` stay tier-free: the tier belongs to
        # the ladder, not to what a row means.
        acc.oh_unit += (v / units) if units else 0.0
    elif basis == "scrap":
        acc.yield_f *= 1 - min(max(v, 0.0), SCRAP_MAX) / 100
    elif basis == "post":
        acc.post += v
    elif basis == "warranty":
        acc.warr_pct += v
    elif basis == "info":
        # Carried for display, never costed. No kind uses it since floor area became a
        # priced quantity (`area`); kept because it is the right handler for a row that is
        # genuinely reference-only, and deleting a basis deletes vocabulary.
        acc.area += v
    # `salary`, `rate` and `toolUnits` are rates: consumed through `g` by the row that needs
    # them, contributing nothing on their own account. Falling through is the whole handler.


# ── locks, and the two directions of the inheritance rule ─────────────────────────────────
# A facility is a dict shaped as the router builds it:
#   {"kind": str, "locks": {row_key}, "own": {row_key: value}, "items": [{"values": {k: v}}]}
# One tier's values only — the router selects the tier before calling in, so nothing here
# has to thread a tier through the arithmetic.

def _getter(fac: dict, values: dict):
    """Read row `k` as the arithmetic should see it, from one sub-item's values.

    A locked rate is read from the facility and flows down; a locked quantity reads 0,
    because it is counted once at facility level and the sub-items contribute nothing for
    that row. This is the rule that keeps a facility from being a pile of unrelated numbers.
    """
    kind = fac.get("kind", "")
    locks = fac.get("locks") or set()
    own = fac.get("own") or {}

    def g(k: str) -> float:
        r = row_of(kind, k)
        if r is not None and k in locks:
            return num(own.get(k)) if inherits(r["basis"]) else 0.0
        return num(values.get(k))

    return g


def item_roll(fac: dict, values: dict) -> Acc:
    """One sub-item's contribution: every UNLOCKED row, read through the getter so that
    inherited rates still reach the arithmetic."""
    acc = Acc()
    locks = fac.get("locks") or set()
    g = _getter(fac, values)
    for r in rows_for(fac.get("kind", "")):
        if r["k"] in locks:
            continue
        contrib(acc, r["basis"], g(r["k"]), g)
    return acc


def own_roll(fac: dict) -> Acc:
    """The facility's own contribution.

    Normally that is every LOCKED row, counted once, from `own` — the unlocked ones are
    entered on the sub-items and roll up through `item_roll`.

    A facility with NO sub-items is the exception, and it is `solo` below. There is nothing
    underneath to enter anything on, so the facility itself is the record: every row is
    costed from `own`, locked or not. Without this rule such a facility could be filled in
    completely and still contribute nothing to the ladder — the values would sit in
    `cogs_value` under the `item_id = ''` sentinel and never be read, which is the worst
    kind of wrong: silent.
    """
    acc = Acc()
    locks = fac.get("locks") or set()
    own = fac.get("own") or {}
    kind = fac.get("kind", "")
    items = fac.get("items") or []
    solo = not items

    def g(k: str) -> float:
        """The one place both directions of the lock rule meet.

        Costing a locked `fte` row needs a salary. If salary is locked too, it is the
        facility's own. If it is not, it is the average of the sub-items' non-zero salaries —
        a rate, so averaged rather than summed.

        That average is UNWEIGHTED. Locking `fte` across cells with unequal headcount
        therefore costs the pooled heads at a salary no single cell actually pays. That is
        correct per spec and genuinely surprising, so: do not "fix" it to a
        headcount-weighted mean without changing the spec first.
        """
        r = row_of(kind, k)
        # Solo: there is no layer below to average, so a sibling rate is the facility's own.
        if solo or (r is not None and k in locks):
            return num(own.get(k))
        vals = [num((it.get("values") or {}).get(k)) for it in items]
        live = [v for v in vals if v]
        if not live:
            return 0.0
        if r is not None and r["basis"] in RATE_BASES:
            return sum(live) / len(live)
        return sum(live)

    for r in rows_for(kind):
        if not solo and r["k"] not in locks:
            continue
        contrib(acc, r["basis"], num(own.get(r["k"])), g)
    return acc


def fac_roll(fac: dict) -> Acc:
    """One facility at one tier: its own locked rows, plus every sub-item's unlocked ones."""
    acc = own_roll(fac)
    for it in fac.get("items") or []:
        acc = acc.merge(item_roll(fac, it.get("values") or {}))
    return acc


def all_roll(facs: list[dict]) -> Acc:
    acc = Acc()
    for f in facs:
        acc = acc.merge(fac_roll(f))
    return acc


# ── the ladder ────────────────────────────────────────────────────────────────────────────
@dataclass
class Ladder:
    """Every rung and every intermediate, so the frontend renders fields and never sums.

    Nothing on screen should be a client-side total: that is a second implementation of the
    arithmetic, in a language where the tests cannot reach it.
    """

    units_per_year: int
    bom: float = 0.0            # L1 material — the BOM rollup minus our assembly labour
    labour: float = 0.0         # L1 labour — AssemblyLabor minutes x the cost type's rate
    bom_raw: float = 0.0        # material + labour, whole: what scrap grosses
    bom_adj: float = 0.0        # after scrap
    consumables: float = 0.0
    other: float = 0.0
    direct: float = 0.0         # L2
    pool_total: float = 0.0     # full-year overhead
    overhead: float = 0.0       # per plant
    burdened: float = 0.0       # L3 — COGM
    post: float = 0.0           # freight + install + crating + travel + commissioning
    warranty: float = 0.0
    cogs_unit: float = 0.0      # L4
    scrap_pct: float = 0.0
    warranty_pct: float = 0.0
    yield_factor: float = 1.0
    area: float = 0.0
    fte: float = 0.0
    assembly_minutes: float = 0.0
    overhead_share: float = 0.0  # of COGS, for the "read the @1 column carefully" line

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def compute(
    *,
    units_per_year: int,
    rollup_cost: float,
    rollup_assembly_cost: float,
    facilities: list[dict],
    assembly_minutes: float = 0.0,
) -> Ladder:
    """The whole ladder for one tier.

    `units_per_year` is the tier — the tier IS plants produced per year, which is why there is
    no `units_per_year` column anywhere in the schema. It is named for what it does rather
    than for the button that sets it, so that decoupling them later is a signature change and
    not an archaeology exercise.

    `rollup_cost` and `rollup_assembly_cost` come straight from `BomGraph(...).rollup(root)` —
    the partition described at the top of this module.
    """
    R = all_roll(facilities)
    tier = units_per_year or 1

    # Scrap grosses the WHOLE BOM cost, material and labour together, because a scrapped part
    # loses the hours already invested in it. One yield factor over the full direct cost
    # implies the loss happens at the end of the line, so this is the upper bound — a later
    # per-station scrap model comes in under it, never over.
    bom_raw = float(rollup_cost)
    labour = float(rollup_assembly_cost)
    yf = R.yield_f or 1.0
    bom_adj = bom_raw / yf

    # The plan's L2 reads `rollup.cost / yieldFactor + consumables + other`. Consumables are
    # basis `direct` like the rest of `other`, so they are one accumulator, not two — the
    # split below is for the reader, and `direct` is built from the whole. Deriving the split
    # by row key here rather than routing `consum` to its own accumulator in `contrib` is
    # deliberate: `contrib` dispatches on basis alone, and that indirection is what makes a
    # fourth facility kind one entry in KINDS and nothing else.
    consumables = sum(
        line["value"] for line in breakdown(facilities) if line["row_key"] == "consum"
    )
    direct = bom_adj + R.other
    pool_total = R.oh
    overhead = pool_total / tier + R.oh_unit
    burdened = direct + overhead
    # Order-dependent: warranty is a percentage of the BURDENED figure, so it computes after
    # overhead and it moves whenever the pool moves.
    warranty = burdened * R.warr_pct / 100
    cogs_unit = burdened + R.post + warranty

    return Ladder(
        units_per_year=tier,
        bom=bom_raw - labour,
        labour=labour,
        bom_raw=bom_raw,
        bom_adj=bom_adj,
        consumables=consumables,
        # The FULL basis-`direct` total, consumables included — so `direct` is reproducible
        # as `bom_adj + other`, and `consumables` reads as the named slice of it that it is.
        other=R.other,
        direct=direct,
        pool_total=pool_total,
        overhead=overhead,
        burdened=burdened,
        post=R.post,
        warranty=warranty,
        cogs_unit=cogs_unit,
        scrap_pct=(1 - yf) * 100,
        warranty_pct=R.warr_pct,
        yield_factor=yf,
        area=R.area,
        fte=R.fte,
        assembly_minutes=assembly_minutes,
        overhead_share=(overhead / cogs_unit) if cogs_unit else 0.0,
    )


# ── breakdown ─────────────────────────────────────────────────────────────────────────────
# Two labels are rewritten for the reader: a headcount row reads as payroll, because what
# enters the pool is the salary bill and not the heads.
_LABEL_OVERRIDE = {"fte": "Payroll, indirect"}

# Which rung each basis belongs to, so a breakdown can be asked for one layer at a time.
LAYER_OF_BASIS = {
    "direct": "direct",
    "scrap": "direct",
    "oh": "overhead",
    "fte": "overhead",
    "area": "overhead",
    "toolTotal": "overhead",   # amortised tooling is depreciation, near enough
    "post": "post",
    "warranty": "post",
}


def breakdown(facilities: list[dict], layer: str | None = None) -> list[dict]:
    """Every facility x every row, as named lines with the facility that carries them.

    Each line is that row's contribution computed on its own — through the same `contrib` and
    the same getters, so a line can never disagree with the total it belongs to. Rows that
    resolve to nothing are dropped: an absent row is not entered, and an unentered row has no
    business taking up a line.
    """
    out: list[dict] = []
    for fac in facilities:
        kind = fac.get("kind", "")
        code = fac.get("code") or ""
        locks = fac.get("locks") or set()
        for r in rows_for(kind):
            basis = r["basis"]
            if layer is not None and LAYER_OF_BASIS.get(basis) != layer:
                continue
            if basis in RATE_BASES or basis == "info":
                continue  # a rate is consumed by another row; it is not a line of its own
            one = Acc()
            if r["k"] in locks:
                # Isolate this locked row: own_roll over a facility whose only lock is this
                # one still reads sibling rates correctly, because the getter falls back to
                # the sub-item average exactly as it does in the real rollup.
                one = own_roll({**fac, "locks": {r["k"]}})
            else:
                for it in fac.get("items") or []:
                    vals = it.get("values") or {}
                    g = _getter(fac, vals)
                    sub = Acc()
                    contrib(sub, basis, g(r["k"]), g)
                    one = one.merge(sub)
            value = _line_value(one, basis)
            if value:
                out.append({
                    "label": _LABEL_OVERRIDE.get(r["k"], r["label"]),
                    "src": code,
                    "row_key": r["k"],
                    "basis": basis,
                    "layer": LAYER_OF_BASIS.get(basis),
                    "value": value,
                    # Not every line is money. Scrap and warranty contribute a RATE, and a
                    # reader shown "EUR 1.50" for a 1.5% yield loss would draw exactly the
                    # wrong conclusion — so the unit travels with the number.
                    "unit": "pct" if basis in ("scrap", "warranty") else "eur",
                })
    return out


def _line_value(acc: Acc, basis: str) -> float:
    """The one number a single row contributed, read off whichever accumulator it lands in."""
    if basis in ("oh", "fte", "area"):
        return acc.oh
    if basis == "toolTotal":
        return acc.oh_unit      # already per plant, unlike the pool beside it
    if basis == "direct":
        return acc.other
    if basis == "post":
        return acc.post
    if basis == "warranty":
        return acc.warr_pct
    if basis == "scrap":
        return (1 - acc.yield_f) * 100
    return 0.0
