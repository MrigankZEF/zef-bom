"""Milestones: freeze a top-level BOM, list what has been frozen, compare against today.

The endpoints are thin. Everything that decides what a milestone *is* lives in
`app/milestones.py`, and everything that decides what a difference *is* lives in
`app/bomdiff.py` — both of which cost a snapshot through the same `BomGraph` as the live
BOM, which is the only reason the two sides of a diff can be compared at all.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, require_admin
from ..bomdiff import diff
from ..db import get_db
from ..history import record_change
from ..milestones import capture, graph_of
from ..models import BomMilestone, Item
from ..rollups import BomGraph
from ..schemas import MilestoneIn

router = APIRouter(tags=["milestones"])


def _counts(m: BomMilestone) -> dict:
    p = m.payload or {}
    return {k: len(p.get(k) or []) for k in ("items", "links", "decided", "labor", "rates")}


def _summary(m: BomMilestone, root_name: str | None = None) -> dict:
    return {
        "id": m.id,
        "root_item_id": m.root_item_id,
        "root_name": root_name,
        "name": m.name,
        "note": m.note,
        "taken_at": m.taken_at.isoformat() if m.taken_at else None,
        "taken_by": m.taken_by,
        "counts": _counts(m),
    }


@router.get("/milestones")
def list_milestones(db: Session = Depends(get_db), root: str | None = Query(default=None)):
    """Newest first. Payloads are deliberately not sent — one is a few hundred kilobytes and
    nothing on screen reads it directly; the diff endpoint does that server-side."""
    q = select(BomMilestone).order_by(BomMilestone.taken_at.desc(), BomMilestone.id.desc())
    if root:
        q = q.where(BomMilestone.root_item_id == root)
    rows = list(db.execute(q).scalars())
    names = {
        it.item_id: it.item_name
        for it in db.execute(
            select(Item).where(Item.item_id.in_({m.root_item_id for m in rows}))
        ).scalars()
    } if rows else {}
    return [_summary(m, names.get(m.root_item_id)) for m in rows]


@router.post("/milestones", status_code=201)
def take_milestone(
    body: MilestoneIn,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
):
    item = db.get(Item, body.root_item_id)
    if item is None or item.archived:
        raise HTTPException(404, f"Item {body.root_item_id} not found")
    if not item.is_top_level:
        # A milestone of a sub-assembly would compare against a subtree whose quantities
        # depend on which parent you came through, so the numbers would not be the ones
        # anybody saw. Top-level only, which is also what Browse selects.
        raise HTTPException(409, f"{body.root_item_id} is not a top-level BOM")

    m = capture(db, body.root_item_id, body.name.strip(), (body.note or "").strip() or None, user)
    db.flush()
    record_change(
        db, entity_type="bom_milestone", entity_id=str(m.id), change_type="create",
        field_changed="name", new_value=m.name, changed_by=user,
        change_reason=f"milestone of {m.root_item_id}",
    )
    db.commit()
    return _summary(m, item.item_name)


@router.delete("/milestones/{milestone_id}", status_code=204)
def delete_milestone(
    milestone_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(current_user),
    _: str = Depends(require_admin),
):
    """Admin only, and it really is gone — a payload is derivable from nothing."""
    m = db.get(BomMilestone, milestone_id)
    if m is None:
        raise HTTPException(404, "Milestone not found")
    record_change(
        db, entity_type="bom_milestone", entity_id=str(milestone_id), change_type="remove",
        field_changed="name", old_value=m.name, changed_by=user,
        change_reason=f"milestone of {m.root_item_id} deleted",
    )
    db.delete(m)
    db.commit()


@router.get("/milestones/{milestone_id}/tree")
def milestone_tree(
    milestone_id: int,
    db: Session = Depends(get_db),
    volume: int = Query(default=100),
):
    """The frozen BOM as a tree, costed at `volume` — the same shape `/tree` returns.

    Costed now rather than then: the payload holds inputs, and the rollup that reads them is
    today's. A fix to the rollup therefore improves both sides of a comparison at once,
    which is what makes them comparable.
    """
    m = db.get(BomMilestone, milestone_id)
    if m is None:
        raise HTTPException(404, "Milestone not found")
    g = graph_of(m, volume)
    if m.root_item_id not in g.items:
        raise HTTPException(409, "This milestone's payload does not contain its own root")
    return g.node(m.root_item_id)


@router.get("/milestones/{milestone_id}/diff")
def milestone_diff(
    milestone_id: int,
    db: Session = Depends(get_db),
    volume: int = Query(default=100),
):
    """What changed between the milestone and the BOM as it stands now, at one tier."""
    m = db.get(BomMilestone, milestone_id)
    if m is None:
        raise HTTPException(404, "Milestone not found")
    return diff(
        before=graph_of(m, volume),
        after=BomGraph(db, volume_tier=volume),
        root=m.root_item_id,
        db=db,
        since=m.taken_at,
        milestone=_summary(m),
    )
