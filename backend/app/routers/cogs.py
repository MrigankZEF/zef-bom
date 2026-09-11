"""The COGS ladder's endpoints — the only place the ladder meets the BOM rollup.

`app.cogs` is pure arithmetic over plain dicts. This module is what loads facility rows out
of the database, selects a tier, asks `BomGraph` for the root's rollup, and hands both to
`cogs.compute`. Keeping the join here and nowhere else is what stops a second, drifting
implementation of the ladder appearing beside the first.

There is deliberately **no grand-total COGS endpoint, and there never will be.** Facilities
are global and fully allocated to whichever root is on screen — correct for comparative
simulation, and it makes two roots' COGS figures unaddable: sum them and you have
double-counted the company. `/costing/summary` does return a `grand_total` across roots,
which is legitimate for BOM cost and wrong by construction for COGS. That endpoint stays as
it is and COGS never joins it.

The frontend does zero arithmetic. Every payload here carries every intermediate rung, so no
figure on screen is ever a client-side sum.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import cogs as ladder
from ..auth import current_user, require_admin
from ..db import get_db
from ..history import record_change
from ..models import CogsFacility, CogsFacilityItem, CogsLock, CogsValue
from ..rollups import BomGraph
from .tree import VOLUME_TIERS

router = APIRouter(tags=["cogs"])


# ── payload shapes ────────────────────────────────────────────────────────────────────────
# Facility codes are their own namespace, unrelated to part numbers: no module prefix, no
# P/A suffix, not allocated from `code_registry`, and a hyphen is part of the shape rather
# than a disallowed character. They name places and teams, not things in a BOM.
FACILITY_CODE = r"^[A-Z]{2,4}-[A-Z0-9]{2,8}$"       # FAC-ASM
FACILITY_ITEM_CODE = r"^[A-Z]{1,2}-[A-Z0-9]{2,8}$"  # A-LINE


class FacilityIn(BaseModel):
    code: str = Field(pattern=FACILITY_CODE)
    kind: str
    name: str = Field(min_length=1, max_length=255)


class FacilityPatch(BaseModel):
    """`kind` is absent on purpose — it is immutable after creation. A payload carrying one
    is rejected rather than ignored, so a client that tries learns that it failed.

    `extra="allow"` is what makes that rejection possible: pydantic's default is to DROP
    unknown fields, which would have silently accepted `{"kind": ...}` and reported success
    while changing nothing. Extras land in `model_extra` and are checked in the handler.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    archived: bool | None = None


class FacilityItemIn(BaseModel):
    code: str = Field(pattern=FACILITY_ITEM_CODE)
    name: str = Field(min_length=1, max_length=255)
    sort_order: int = 0


class CellIn(BaseModel):
    """One cell of the matrix. `value: None` deletes the cell — which is not the same as
    storing 0: an absent row is "not entered" and displays as an em dash, a stored 0 is
    "known to be zero"."""

    item_id: str = ladder.OWN     # '' = the facility's own value, for a locked row
    row_key: str
    volume_tier: int
    value: float | None = None


class ValuesPatch(BaseModel):
    """The staged save: every changed cell in one request, one transaction, one history
    entry per cell. Nothing is written until Save, matching the part drawer — so a stray
    click cannot change a costing."""

    cells: list[CellIn]


# ── loading facilities into the shape `app.cogs` takes ────────────────────────────────────
def _facilities_at(db: Session, tier: int, *, include_archived: bool = False) -> list[dict]:
    """Every facility as a plain dict at ONE tier, ready for `cogs.all_roll`/`compute`.

    Three flat queries and a regroup in Python rather than a relationship walk: the whole
    table is small, and this keeps the arithmetic's input a plain structure the tests can
    build by hand.
    """
    q = select(CogsFacility)
    if not include_archived:
        q = q.where(CogsFacility.archived.is_(False))
    facs = list(db.execute(q).scalars())
    if not facs:
        return []
    fac_ids = [f.id for f in facs]

    items: dict[int, list[CogsFacilityItem]] = {}
    for it in db.execute(
        select(CogsFacilityItem)
        .where(CogsFacilityItem.facility_id.in_(fac_ids))
        .order_by(CogsFacilityItem.sort_order, CogsFacilityItem.id)
    ).scalars():
        items.setdefault(it.facility_id, []).append(it)

    # (facility, item_id) -> {row_key: value}, this tier only.
    cells: dict[tuple[int, str], dict[str, float]] = {}
    for v in db.execute(
        select(CogsValue).where(
            CogsValue.facility_id.in_(fac_ids), CogsValue.volume_tier == tier
        )
    ).scalars():
        cells.setdefault((v.facility_id, v.item_id), {})[v.row_key] = float(v.value)

    locks: dict[int, set[str]] = {}
    for lk in db.execute(select(CogsLock).where(CogsLock.facility_id.in_(fac_ids))).scalars():
        locks.setdefault(lk.facility_id, set()).add(lk.row_key)

    return [
        {
            "id": f.id,
            "code": f.code,
            "kind": f.kind,
            "name": f.name,
            "locks": locks.get(f.id, set()),
            "own": cells.get((f.id, ladder.OWN), {}),
            "items": [
                {
                    "id": it.id,
                    "code": it.code,
                    "name": it.name,
                    "values": cells.get((f.id, str(it.id)), {}),
                }
                for it in items.get(f.id, [])
            ],
        }
        for f in facs
    ]


def _acc_dict(acc: "ladder.Acc", tier: int) -> dict:
    """One accumulator as the screen reads it: the annual pool, the per-plant halves, and
    the pool divided by the tier — because a pool and a per-plant figure look identical on
    screen and confusing the two is the whole trap this feature has to avoid."""
    return {
        "pool_year": acc.oh,
        # The pool over the tier PLUS the already-per-plant half (amortised tooling), which
        # is why this is not simply pool_year / tier.
        "overhead_per_plant": acc.oh / (tier or 1) + acc.oh_unit,
        "tooling_per_plant": acc.oh_unit,
        "direct_per_plant": acc.other,
        "post_per_plant": acc.post,
        "warranty_pct": acc.warr_pct,
        "scrap_pct": (1 - acc.yield_f) * 100,
        "yield_factor": acc.yield_f,
        "area": acc.area,
        "fte": acc.fte,
        # What this facility costs in a year at this tier, and per plant. Both stated, so
        # neither has to be derived from the other on screen.
        "year_total": acc.oh + (acc.oh_unit + acc.other + acc.post) * (tier or 1),
        "per_plant_total": acc.oh / (tier or 1) + acc.oh_unit + acc.other + acc.post,
    }


def _get_facility(db: Session, facility_id: int) -> CogsFacility:
    f = db.get(CogsFacility, facility_id)
    if f is None:
        raise HTTPException(404, f"Facility {facility_id} not found")
    return f


def _check_row_key(kind: str, row_key: str) -> dict:
    """A key not in its facility's kind is rejected on write, not stored.

    Storing it would leave a value no basis handler ever reads — invisible in every total,
    and impossible to distinguish from a typo later.
    """
    row = ladder.row_of(kind, row_key)
    if row is None:
        raise HTTPException(
            422, f"'{row_key}' is not a row on a '{kind}' facility"
        )
    return row


# ── the row schema ────────────────────────────────────────────────────────────────────────
@router.get("/cogs/kinds")
def get_kinds():
    """The matrix schema the UI renders: which rows each kind has, in order, with units.

    Served rather than duplicated in JS — a second copy is a second thing to update when a
    basis handler is added, and the frontend would have no way to know it had gone stale.
    """
    return {
        "kinds": [
            {
                "kind": k,
                "label": v["label"],
                "blurb": v["blurb"],
                "rows": [
                    {**r, "inherits_when_locked": ladder.inherits(r["basis"])}
                    for r in v["rows"]
                ],
            }
            for k, v in ladder.KINDS.items()
        ],
        "tiers": VOLUME_TIERS,
        # So the UI can render "the facility's own value" cells without hardcoding ''.
        "own_sentinel": ladder.OWN,
    }


# ── facilities ────────────────────────────────────────────────────────────────────────────
@router.get("/cogs/facilities")
def list_facilities(
    db: Session = Depends(get_db),
    include_archived: bool = Query(default=False),
):
    """The whole tree, with every tier's cells — the Facilities screen loads once.

    Cells come back keyed by tier so the matrix can switch tiers without a round trip, which
    is also what lets the three columns be compared side by side.

    Also carries what the screen would otherwise have to add up itself:

      * `aggregates[row_key][tier]` — the roll-up shown in a facility's read-only cell for an
        unlocked row (a sum, or the mean of the non-zero values for a rate row).
      * `rollup[tier]` — that facility's own contribution to each rung.
      * `totals[tier]` — the same across every live facility, for the KPI tiles.

    The frontend does zero arithmetic, and these are the numbers it would have had to invent
    to render the matrix. Computing them here keeps the one implementation.
    """
    facs = list(
        db.execute(
            select(CogsFacility).order_by(CogsFacility.code)
            if include_archived
            else select(CogsFacility)
            .where(CogsFacility.archived.is_(False))
            .order_by(CogsFacility.code)
        ).scalars()
    )
    if not facs:
        return {"facilities": [], "totals": {t: _acc_dict(ladder.Acc(), t) for t in VOLUME_TIERS}}
    fac_ids = [f.id for f in facs]

    items: dict[int, list[CogsFacilityItem]] = {}
    for it in db.execute(
        select(CogsFacilityItem)
        .where(CogsFacilityItem.facility_id.in_(fac_ids))
        .order_by(CogsFacilityItem.sort_order, CogsFacilityItem.id)
    ).scalars():
        items.setdefault(it.facility_id, []).append(it)

    # (facility, item_id) -> row_key -> tier -> value. Absent stays absent: the em dash is
    # the absence of a key here, never a null written into it.
    cells: dict[tuple[int, str], dict[str, dict[int, float]]] = {}
    for v in db.execute(select(CogsValue).where(CogsValue.facility_id.in_(fac_ids))).scalars():
        cells.setdefault((v.facility_id, v.item_id), {}).setdefault(v.row_key, {})[
            v.volume_tier
        ] = float(v.value)

    locks: dict[int, list[str]] = {}
    for lk in db.execute(select(CogsLock).where(CogsLock.facility_id.in_(fac_ids))).scalars():
        locks.setdefault(lk.facility_id, []).append(lk.row_key)

    # Per-tier facility dicts in the shape `app.cogs` takes, so the roll-ups below go
    # through exactly the same arithmetic the ladder uses. No parallel implementation.
    at_tier = {t: {x["id"]: x for x in _facilities_at(db, t, include_archived=True)}
               for t in VOLUME_TIERS}

    def aggregates_for(f) -> dict[str, dict[int, float]]:
        out: dict[str, dict[int, float]] = {}
        for row in ladder.rows_for(f.kind):
            for tier in VOLUME_TIERS:
                fac = at_tier[tier].get(f.id)
                if fac is None:
                    continue
                agg = _aggregate(fac, row, [i["values"] for i in fac["items"]])
                if agg is not None:
                    out.setdefault(row["k"], {})[tier] = agg
        return out

    def rollup_for(f) -> dict[int, dict]:
        out = {}
        for tier in VOLUME_TIERS:
            fac = at_tier[tier].get(f.id)
            acc = ladder.fac_roll(fac) if fac else ladder.Acc()
            out[tier] = _acc_dict(acc, tier)
        return out

    out_facs = [
        {
            "id": f.id,
            "code": f.code,
            "kind": f.kind,
            "name": f.name,
            "archived": f.archived,
            "locks": sorted(locks.get(f.id, [])),
            "own": cells.get((f.id, ladder.OWN), {}),
            "aggregates": aggregates_for(f),
            "rollup": rollup_for(f),
            "items": [
                {
                    "id": it.id,
                    "code": it.code,
                    "name": it.name,
                    "sort_order": it.sort_order,
                    "values": cells.get((f.id, str(it.id)), {}),
                }
                for it in items.get(f.id, [])
            ],
        }
        for f in facs
    ]
    live = {f.id for f in facs if not f.archived}
    totals = {
        t: _acc_dict(
            ladder.all_roll([x for i, x in at_tier[t].items() if i in live]), t
        )
        for t in VOLUME_TIERS
    }
    return {"facilities": out_facs, "totals": totals}


@router.post("/cogs/facilities")
def create_facility(
    body: FacilityIn,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
):
    if body.kind not in ladder.KINDS:
        raise HTTPException(422, f"Unknown facility kind '{body.kind}'")
    if db.execute(select(CogsFacility).where(CogsFacility.code == body.code)).scalar_one_or_none():
        raise HTTPException(409, f"Facility code {body.code} already exists")
    f = CogsFacility(
        code=body.code, kind=body.kind, name=body.name, created_by=user, updated_by=user
    )
    db.add(f)
    db.flush()
    record_change(
        db, entity_type="cogs_facility", entity_id=str(f.id), change_type="create",
        field_changed="kind", new_value=body.kind, changed_by=user,
    )
    db.commit()
    db.refresh(f)
    return {"id": f.id, "code": f.code, "kind": f.kind, "name": f.name, "archived": f.archived}


@router.patch("/cogs/facilities/{facility_id}")
def patch_facility(
    facility_id: int,
    body: FacilityPatch,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
):
    """Name and archived flag only.

    The kind is immutable and rejected here rather than merely left out of the UI: changing
    it would strand every stored value under a row key the new kind does not have, and the
    values would stay in the table contributing nothing.
    """
    f = _get_facility(db, facility_id)
    extra = getattr(body, "model_extra", None) or {}
    if "kind" in extra:
        raise HTTPException(
            422,
            "A facility's kind is fixed when it is created — it decides which rows the "
            "matrix has, and changing it would strand every value already entered.",
        )
    if extra:
        # Everything else unknown is a typo, and a silently dropped field reads as a
        # successful save that did nothing.
        raise HTTPException(422, f"Unknown field(s): {', '.join(sorted(extra))}")
    if body.name is not None and body.name != f.name:
        record_change(
            db, entity_type="cogs_facility", entity_id=str(f.id), change_type="update",
            field_changed="name", old_value=f.name, new_value=body.name, changed_by=user,
        )
        f.name = body.name
    if body.archived is not None and body.archived != f.archived:
        record_change(
            db, entity_type="cogs_facility", entity_id=str(f.id), change_type="update",
            field_changed="archived", old_value=f.archived, new_value=body.archived,
            changed_by=user,
        )
        f.archived = body.archived
    f.updated_by = user
    db.commit()
    return {"id": f.id, "code": f.code, "kind": f.kind, "name": f.name, "archived": f.archived}


@router.delete("/cogs/facilities/{facility_id}")
def delete_facility(
    facility_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    """Admin only, and it takes the facility's values and locks with it.

    Editors archive instead — `PATCH {archived: true}` keeps the history and the numbers.
    """
    f = _get_facility(db, facility_id)
    db.execute(delete(CogsValue).where(CogsValue.facility_id == facility_id))
    db.execute(delete(CogsLock).where(CogsLock.facility_id == facility_id))
    db.execute(delete(CogsFacilityItem).where(CogsFacilityItem.facility_id == facility_id))
    record_change(
        db, entity_type="cogs_facility", entity_id=str(facility_id), change_type="delete",
        field_changed="code", old_value=f.code, changed_by=user,
    )
    db.delete(f)
    db.commit()
    return {"deleted": facility_id}


# ── sub-items ─────────────────────────────────────────────────────────────────────────────
@router.post("/cogs/facilities/{facility_id}/items")
def add_item(
    facility_id: int,
    body: FacilityItemIn,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
):
    _get_facility(db, facility_id)
    dupe = db.execute(
        select(CogsFacilityItem).where(
            CogsFacilityItem.facility_id == facility_id, CogsFacilityItem.code == body.code
        )
    ).scalar_one_or_none()
    if dupe:
        raise HTTPException(409, f"{body.code} already exists on this facility")
    it = CogsFacilityItem(
        facility_id=facility_id, code=body.code, name=body.name, sort_order=body.sort_order
    )
    db.add(it)
    db.flush()
    record_change(
        db, entity_type="cogs_facility_item", entity_id=str(it.id), change_type="create",
        field_changed="code", new_value=body.code, changed_by=user,
    )
    db.commit()
    db.refresh(it)
    return {"id": it.id, "code": it.code, "name": it.name, "sort_order": it.sort_order}


@router.delete("/cogs/facilities/{facility_id}/items/{item_id}")
def delete_item(
    facility_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
):
    it = db.get(CogsFacilityItem, item_id)
    if it is None or it.facility_id != facility_id:
        raise HTTPException(404, f"Sub-item {item_id} not found on facility {facility_id}")
    # Its cells go with it. They are keyed by str(id), so leaving them would make them
    # unreachable rather than merely unused — and they would still be in the backup.
    db.execute(
        delete(CogsValue).where(
            CogsValue.facility_id == facility_id, CogsValue.item_id == str(item_id)
        )
    )
    record_change(
        db, entity_type="cogs_facility_item", entity_id=str(item_id), change_type="delete",
        field_changed="code", old_value=it.code, changed_by=user,
    )
    db.delete(it)
    db.commit()
    return {"deleted": item_id}


# ── the matrix ────────────────────────────────────────────────────────────────────────────
@router.patch("/cogs/facilities/{facility_id}/values")
def save_values(
    facility_id: int,
    body: ValuesPatch,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
):
    """One staged save: the changed cells as a batch, one history entry each, one commit.

    Rejects the whole batch if any cell names a row the facility's kind does not have or a
    tier that is not a real tier — a partial save would leave the screen disagreeing with
    the database about what was written.
    """
    f = _get_facility(db, facility_id)
    item_ids = {
        str(i) for i in db.execute(
            select(CogsFacilityItem.id).where(CogsFacilityItem.facility_id == facility_id)
        ).scalars()
    }

    for c in body.cells:
        _check_row_key(f.kind, c.row_key)
        if c.volume_tier not in VOLUME_TIERS:
            raise HTTPException(422, f"{c.volume_tier} is not a volume tier")
        if c.item_id != ladder.OWN and c.item_id not in item_ids:
            raise HTTPException(422, f"Sub-item {c.item_id} is not on this facility")

    written = 0
    for c in body.cells:
        existing = db.execute(
            select(CogsValue).where(
                CogsValue.facility_id == facility_id,
                CogsValue.item_id == c.item_id,
                CogsValue.row_key == c.row_key,
                CogsValue.volume_tier == c.volume_tier,
            )
        ).scalar_one_or_none()
        old = float(existing.value) if existing is not None else None

        if c.value is None:
            # Clearing a cell DELETES it, back to "not entered". Writing 0 instead would
            # claim we know the value is zero.
            if existing is None:
                continue
            db.delete(existing)
        elif existing is None:
            db.add(CogsValue(
                facility_id=facility_id, item_id=c.item_id, row_key=c.row_key,
                volume_tier=c.volume_tier, value=Decimal(str(c.value)),
            ))
        elif old == c.value:
            continue    # unchanged: no write, no history line
        else:
            existing.value = Decimal(str(c.value))

        record_change(
            db, entity_type="cogs_value", entity_id=f"{facility_id}:{c.item_id or 'own'}",
            change_type="update", field_changed=f"{c.row_key}@{c.volume_tier}",
            old_value=old, new_value=c.value, changed_by=user,
        )
        written += 1

    f.updated_by = user
    db.commit()
    return {"saved": written}


# ── locks ─────────────────────────────────────────────────────────────────────────────────
def _aggregate(fac: dict, row: dict, tier_cells: list[dict]) -> float | None:
    """The current roll-up of one row across sub-items, for seeding a lock.

    A rate row averages over its non-zero values; everything else sums. Returns None when
    nothing was entered at all — there is no aggregate to seed from, and seeding 0 would
    invent knowledge.
    """
    vals = [v.get(row["k"]) for v in tier_cells]
    live = [float(v) for v in vals if v not in (None, "")]
    if not live:
        return None
    if row["basis"] in ladder.RATE_BASES:
        nz = [v for v in live if v]
        return (sum(nz) / len(nz)) if nz else 0.0
    return sum(live)


@router.put("/cogs/facilities/{facility_id}/locks/{row_key}")
def lock_row(
    facility_id: int,
    row_key: str,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
):
    """Lock a row, seeding the facility's own value from the current aggregate at each tier.

    Seeding matters: without it, locking a row that already had values would drop the whole
    row to nothing and the total would jump the moment the box was ticked.
    """
    f = _get_facility(db, facility_id)
    row = _check_row_key(f.kind, row_key)
    if db.execute(
        select(CogsLock).where(CogsLock.facility_id == facility_id, CogsLock.row_key == row_key)
    ).scalar_one_or_none():
        return {"locked": row_key, "seeded": {}}

    seeded: dict[int, float] = {}
    for tier in VOLUME_TIERS:
        fac = next((x for x in _facilities_at(db, tier, include_archived=True)
                    if x["id"] == facility_id), None)
        if fac is None:
            continue
        agg = _aggregate(fac, row, [it["values"] for it in fac["items"]])
        if agg is None:
            continue
        existing = db.execute(
            select(CogsValue).where(
                CogsValue.facility_id == facility_id,
                CogsValue.item_id == ladder.OWN,
                CogsValue.row_key == row_key,
                CogsValue.volume_tier == tier,
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(CogsValue(
                facility_id=facility_id, item_id=ladder.OWN, row_key=row_key,
                volume_tier=tier, value=Decimal(str(agg)),
            ))
        else:
            existing.value = Decimal(str(agg))
        seeded[tier] = agg

    db.add(CogsLock(facility_id=facility_id, row_key=row_key))
    record_change(
        db, entity_type="cogs_lock", entity_id=str(facility_id), change_type="create",
        field_changed=row_key, new_value="locked", changed_by=user,
    )
    db.commit()
    return {"locked": row_key, "seeded": seeded}


@router.delete("/cogs/facilities/{facility_id}/locks/{row_key}")
def unlock_row(
    facility_id: int,
    row_key: str,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
):
    """Unlock a row, leaving the sub-item values exactly as they are.

    The parent value is NOT distributed downward: it was one number for the whole facility,
    and splitting it across cells would be inventing per-cell figures nobody entered. The
    facility's own value stays stored, so re-locking returns to it.
    """
    lk = db.execute(
        select(CogsLock).where(CogsLock.facility_id == facility_id, CogsLock.row_key == row_key)
    ).scalar_one_or_none()
    if lk is None:
        raise HTTPException(404, f"{row_key} is not locked on facility {facility_id}")
    db.delete(lk)
    record_change(
        db, entity_type="cogs_lock", entity_id=str(facility_id), change_type="delete",
        field_changed=row_key, old_value="locked", changed_by=user,
    )
    db.commit()
    return {"unlocked": row_key}


# ── the ladder ────────────────────────────────────────────────────────────────────────────
def _coverage_note(r, rungs: dict | None = None, facilities: list | None = None) -> dict:
    """What the screen has to say out loud about how solid the number is.

    Coverage propagates all the way up, so a COGS figure on a half-priced BOM is a floor and
    must say so — in the same voice the BOM tool already uses on the treemap.
    """
    gaps = len(set(r.missing)) + len(set(r.missing_assembly)) + len(set(r.missing_quote))
    # A COGS figure is a floor for a second reason the BOM knows nothing about: when nothing
    # has been entered ABOVE the BOM. An empty Facilities tab makes overhead, freight and the
    # warranty accrual read EUR 0.00 -- numbers this screen would otherwise present as fact.
    #
    # Counting only BOM gaps meant the warning switched itself OFF at exactly the wrong
    # moment: the day the last assembly time is filled in, coverage reaches 100%, the banner
    # disappears, and the screen starts calling the bare BOM cost a COGS. The two halves get
    # completed weeks apart, so they cannot stand in for one another.
    empty_rungs: list[str] = []
    if rungs is not None:
        if not facilities:
            empty_rungs.append("facilities")
        else:
            if not rungs.get("pool_total"):
                empty_rungs.append("overhead")
            if not rungs.get("post"):
                empty_rungs.append("post")
            if not rungs.get("warranty_pct"):
                empty_rungs.append("warranty")
    return {
        "coverage": round(r.coverage, 4),
        "gaps": gaps,
        "missing": sorted(set(r.missing)),
        "missing_assembly": sorted(set(r.missing_assembly)),
        "missing_quote": sorted(set(r.missing_quote)),
        "below_boundary": sorted(set(r.below_boundary)),
        "empty_rungs": empty_rungs,
        "is_floor": gaps > 0 or bool(empty_rungs),
    }


def _ladder_at(db: Session, root: str, tier: int) -> dict:
    g = BomGraph(db, volume_tier=tier)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")
    r = g.rollup(root)
    facs = _facilities_at(db, tier)
    L = ladder.compute(
        units_per_year=tier,
        rollup_cost=r.cost,
        rollup_assembly_cost=r.assembly_cost,
        facilities=facs,
        assembly_minutes=g.assembly_time_total(root),
    )
    return {
        **L.as_dict(),
        "root": root,
        "volume_tier": tier,
        "coverage": _coverage_note(r, L.as_dict(), facs),
        # The assumptions the reader cannot infer and the numbers depend on. Served rather
        # than written into the JSX so they cannot drift from the arithmetic that needs them.
        "notes": [
            f"Overhead pool ÷ {tier:,} plants/yr",
            "Every facility fully allocated to this BOM. Figures for two BOMs cannot be added.",
            "Scrap applied to the full direct cost — loss assumed at end of line.",
        ],
    }


@router.get("/cogs/ladder")
def get_ladder(
    root: str = Query(...),
    db: Session = Depends(get_db),
    volume: int = Query(default=100),
):
    """The four rungs for one root at one tier, every intermediate included."""
    if volume not in VOLUME_TIERS:
        raise HTTPException(422, f"{volume} is not a volume tier")
    return _ladder_at(db, root, volume)


@router.get("/cogs/summary")
def get_summary(root: str = Query(...), db: Session = Depends(get_db)):
    """All three tiers for one root — what the volume chart and the tier switch read.

    Per root, and only ever per root. See this module's docstring for why there is no
    across-roots total.
    """
    return {
        "root": root,
        "tiers": [_ladder_at(db, root, t) for t in VOLUME_TIERS],
    }


@router.get("/cogs/breakdown")
def get_breakdown(
    db: Session = Depends(get_db),
    volume: int = Query(default=100),
    layer: str | None = Query(default=None, description="direct | overhead | post"),
):
    """Each layer as named lines, with the facility that carries them.

    Every line is computed through the same `contrib` and the same lock getters as the total,
    so a line can never disagree with the rung it belongs to.
    """
    if volume not in VOLUME_TIERS:
        raise HTTPException(422, f"{volume} is not a volume tier")
    lines = ladder.breakdown(_facilities_at(db, volume), layer)
    return {"volume_tier": volume, "layer": layer, "lines": lines}


@router.get("/cogs/pending")
def get_pending(db: Session = Depends(get_db)):
    """Facility gaps, in the same voice as `/pending`'s BOM gaps.

    A sub-item with nothing entered at a tier is exactly the kind of "sit down and fill this
    in" gap that queue already lists, and it should be discoverable there rather than only by
    opening every drawer. This lives here rather than in `tree.py` so that router stays
    untouched by the ladder; `Pending.jsx` merges the two lists.

    A locked row is not a gap on a sub-item — it is not entered there by design.
    """
    out = []
    per_tier = {t: _facilities_at(db, t, include_archived=False) for t in VOLUME_TIERS}
    for tier, facs in per_tier.items():
        for f in facs:
            rows = [
                r for r in ladder.rows_for(f["kind"])
                if r["k"] not in f["locks"] and r["basis"] != "info"
            ]
            if not rows:
                continue
            for it in f["items"]:
                missing = [r["k"] for r in rows if it["values"].get(r["k"]) is None]
                if missing:
                    out.append({
                        "facility_id": f["id"],
                        "facility_code": f["code"],
                        "facility_name": f["name"],
                        "item_id": it["id"],
                        "item_code": it["code"],
                        "item_name": it["name"],
                        "volume_tier": tier,
                        "missing": missing,
                    })
    return out
