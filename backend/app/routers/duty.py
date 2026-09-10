"""Duty rates, and the duty a BOM would attract.

The arithmetic and every modelling decision live in `app/duty.py` — above all that import
VAT never enters a COGS figure, because it is recoverable in Portugal. Read that docstring
before changing anything here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, require_admin
from ..db import get_db
from ..duty import duty_for_bom, facility_destination, normalize_hs
from ..history import record_change
from ..models import DutyRate
from ..rollups import BomGraph
from ..schemas import DutyRateIn

router = APIRouter(tags=["duty"])


def _out(r: DutyRate) -> dict:
    return {
        "id": r.id,
        "hs_code": r.hs_code,
        "origin_country": r.origin_country or "",
        "destination_country": r.destination_country,
        "rate_pct": float(r.rate_pct),
        "valid_from": r.valid_from.isoformat() if r.valid_from else None,
        "source": r.source,
        "note": r.note,
    }


@router.get("/duty/rates")
def list_rates(
    db: Session = Depends(get_db),
    destination: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
):
    q = select(DutyRate).order_by(DutyRate.hs_code, DutyRate.origin_country)
    if destination:
        q = q.where(DutyRate.destination_country == destination.upper()[:2])
    if not include_archived:
        q = q.where(DutyRate.archived.is_(False))
    return {
        "destination_default": facility_destination(db),
        "rates": [_out(r) for r in db.execute(q).scalars()],
    }


@router.post("/duty/rates", status_code=201)
def add_rate(body: DutyRateIn, db: Session = Depends(get_db),
             user: str = Depends(current_user), _: str = Depends(require_admin)):
    """Admin only. A duty rate is a published fact about the world, not a working estimate."""
    code = normalize_hs(body.hs_code)
    if not code:
        raise HTTPException(422, "hs_code must contain digits")
    r = DutyRate(
        hs_code=code,
        origin_country=(body.origin_country or "").upper()[:2],
        destination_country=body.destination_country.upper()[:2],
        rate_pct=body.rate_pct,
        valid_from=body.valid_from,
        source=body.source,
        note=body.note,
        created_by=user, updated_by=user,
    )
    db.add(r)
    db.flush()
    record_change(
        db, entity_type="duty_rate", entity_id=str(r.id), change_type="create",
        field_changed="rate_pct", new_value=str(body.rate_pct), changed_by=user,
        change_reason=f"{code} {r.origin_country or '*'}→{r.destination_country}",
    )
    db.commit()
    return _out(r)


@router.delete("/duty/rates/{rate_id}", status_code=204)
def archive_rate(rate_id: int, db: Session = Depends(get_db),
                 user: str = Depends(current_user), _: str = Depends(require_admin)):
    """Archived, not deleted — a landed cost computed last quarter used this row, and the
    reason it was that number should still be findable."""
    r = db.get(DutyRate, rate_id)
    if r is None:
        raise HTTPException(404, "Rate not found")
    r.archived = True
    r.updated_by = user
    record_change(
        db, entity_type="duty_rate", entity_id=str(rate_id), change_type="remove",
        field_changed="archived", old_value=str(float(r.rate_pct)), new_value="archived",
        changed_by=user,
    )
    db.commit()


@router.get("/duty/bom")
def duty_bom(
    db: Session = Depends(get_db),
    root: str = Query(...),
    volume: int = Query(default=100),
    destination: str | None = Query(default=None),
):
    """What this BOM would attract in import duty, line by line.

    Also returns the **cross-check** against the ladder's typed `duty` rung: the two are
    independent statements about the same money, and the useful thing is the difference. The
    rung is not replaced by this — deciding whether it becomes derived, or is redefined as
    "customs cost not attributable to a part", is a later decision and the sort that should
    be made with a real classified BOM in front of you, not now.
    """
    g = BomGraph(db, volume_tier=volume)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")
    result = duty_for_bom(db, g, root, destination=destination)

    # Read through the ladder, never by summing `cogs_value` directly. A locked row's
    # facility-own cell and its sub-item cells both exist, so a raw SUM double-counts exactly
    # the rows that were locked — and `cogs.breakdown` already computes each line through the
    # same getters as the total it belongs to.
    from ..cogs import breakdown as cogs_breakdown
    from .cogs import _facilities_at

    typed_total = sum(
        float(ln.get("value") or 0.0)
        for ln in cogs_breakdown(_facilities_at(db, volume))
        if ln.get("row_key") == "duty"
    )
    result["cross_check"] = {
        "typed_rung": round(typed_total, 2),
        "derived": result["totals"]["duty"],
        "difference": round(result["totals"]["duty"] - typed_total, 2),
        "note": (
            "Two independent statements about the same money. The derived figure is ex-works "
            "and excludes recoverable import VAT; the typed rung is whatever was entered on "
            "the Facilities screen."
        ),
    }
    return result
