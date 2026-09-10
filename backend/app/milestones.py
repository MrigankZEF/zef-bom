"""Freezing a BOM, and reading it back as rows.

Two functions and a rule. `capture` walks a top-level root, collects the rows a `BomGraph`
needs and stores them as cells. `rows_of` turns those cells back into ORM instances that no
session owns, which `BomGraph(rows=...)` then costs with exactly the code the live BOM uses.

The rule is that a milestone holds **inputs only**. No rolled-up cost, no coverage, no
totals — `models.py` says derive, don't store, and here it earns its keep twice over: a
stored total would disagree with the rollup the first time the rollup was fixed, and two
sides of a diff derived by different code cannot be compared at all.

Scope is the subtree, not the database. A milestone of the AEC stack should not carry the
UN parts that happen to sit in another BOM, or it stops being a snapshot of that BOM. But
the subtree is taken through `explode_boundaries` — a bought-in assembly's contents are
inside the milestone even though they are not costed, because "we replaced this quote with
a build" is exactly the kind of change a diff has to be able to show.

All three tiers, always. Which tier you want to look at is a question asked long after the
snapshot was taken, and a milestone that only kept 10k could not answer it.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .backup import _backup_cell, _coerce
from .models import AssemblyLabor, BomLink, BomMilestone, DecidedCost, Item, ReferenceValue
from .rollups import BomGraph
from .rows import BomRows

# payload key -> model. The order is the order `rows_of` rebuilds in; nothing here depends
# on it, since these instances are never added to a session, but it keeps the payload
# readable when someone opens one to see what a milestone actually kept.
PAYLOAD_TABLES: list[tuple[str, type]] = [
    ("items", Item),
    ("links", BomLink),
    ("decided", DecidedCost),
    ("labor", AssemblyLabor),
    ("rates", ReferenceValue),
]


def _json_safe(v):
    """`_backup_cell` for a JSON column rather than a spreadsheet cell.

    The two are almost the same problem and differ in one place: a `Numeric` column hands
    back a `Decimal`, which openpyxl writes happily and `json.dumps` refuses outright. Kept
    as its decimal STRING rather than cast to float, so the payload records the value that
    was stored rather than a binary approximation of it — `_coerce` turns either back into
    the float the rollup uses, so nothing downstream can tell, but a payload is meant to be
    evidence and evidence should not round.
    """
    v = _backup_cell(v)
    return str(v) if isinstance(v, Decimal) else v


def _cells(obj) -> dict:
    """One row as a dict of JSON-safe cells."""
    return {c.name: _json_safe(getattr(obj, c.name)) for c in obj.__table__.columns}


def subtree_ids(db: Session, root: str) -> set[str]:
    """`root` and everything below it, through bought-in assemblies as well as under them."""
    g = BomGraph(db, volume_tier=100)
    if root not in g.items:
        return set()
    ids = {root}
    stack = [root]
    while stack:
        cur = stack.pop()
        for child, _ in g.children.get(cur, ()):
            if child not in ids:
                ids.add(child)
                stack.append(child)
    return ids


def build_payload(db: Session, root: str) -> dict:
    """The rows to keep, as cells. Scoped to the subtree; all tiers."""
    ids = subtree_ids(db, root)
    if not ids:
        return {}

    # Archived is excluded, exactly as `load_rows` excludes it: a soft delete IS a delete
    # as far as the BOM is concerned, so an archived link was not in the BOM at the time and
    # a snapshot that kept it would not be a snapshot of what anybody saw.
    #
    # This is not a detail. Ten archived links survive inside the real AEC subtree, and
    # keeping them made a freshly taken milestone roll up to EUR 3,058 against the live
    # EUR 2,872 — a snapshot that disagreed with the screen it was taken from.
    items = list(db.execute(
        select(Item).where(Item.item_id.in_(ids), Item.archived.is_(False))
    ).scalars())
    links = [
        bl for bl in db.execute(
            select(BomLink).where(BomLink.parent_item_id.in_(ids), BomLink.archived.is_(False))
        ).scalars()
        if bl.child_item_id in ids
    ]
    decided = list(db.execute(select(DecidedCost).where(DecidedCost.item_id.in_(ids))).scalars())
    labor = list(db.execute(select(AssemblyLabor).where(AssemblyLabor.item_id.in_(ids))).scalars())
    # Rates are global and tiny, and an item's cost_type_id is meaningless without the €/h
    # it points at — so the whole category travels with the snapshot. A rate edited after
    # the milestone was taken is a real change in cost, and this is what lets the diff say so.
    rates = list(db.execute(
        select(ReferenceValue).where(ReferenceValue.category == "assembly_cost_type")
    ).scalars())

    rows = {"items": items, "links": links, "decided": decided, "labor": labor, "rates": rates}
    return {key: [_cells(o) for o in rows[key]] for key, _ in PAYLOAD_TABLES}


def capture(db: Session, root: str, name: str, note: str | None, user: str) -> BomMilestone:
    """Freeze `root` as it stands. Does not commit."""
    m = BomMilestone(
        root_item_id=root, name=name, note=note, taken_by=user,
        payload=build_payload(db, root),
    )
    db.add(m)
    return m


def rows_of(m: BomMilestone) -> BomRows:
    """A milestone's payload as ORM instances belonging to no session.

    Nothing is filtered here — `build_payload` already stored only what was live, which is
    the correct place for that decision: an item archived AFTER the snapshot is still in the
    payload, and the diff reports it as removed, which is exactly what happened.

    Missing keys are tolerated deliberately. A payload written before a table existed has
    no key for it, and that means an empty list, not a broken milestone.
    """
    payload = m.payload or {}
    built: dict[str, list] = {}
    for key, model in PAYLOAD_TABLES:
        cols = {c.name: c for c in model.__table__.columns}
        out = []
        for cell_row in payload.get(key) or []:
            # Unknown keys are dropped rather than passed to the constructor: a payload
            # written before a column was removed still has to load.
            kwargs = {k: _coerce(v, cols[k]) for k, v in cell_row.items() if k in cols}
            out.append(model(**kwargs))
        built[key] = out
    return BomRows(
        items=built["items"], links=built["links"], decided=built["decided"],
        labor=built["labor"], rates=built["rates"],
    )


def graph_of(m: BomMilestone, volume_tier: int) -> BomGraph:
    """The milestone, costed by the live code. The whole reason `rows.py` exists."""
    return BomGraph(rows=rows_of(m), volume_tier=volume_tier)
