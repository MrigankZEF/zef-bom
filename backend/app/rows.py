"""The rows a BOM graph is built from, separated from where they came from.

`BomGraph` runs five queries in its constructor and then never touches the `Session` again —
every rollup, coverage count, flatten and treemap works off plain dicts. That means the graph
does not actually depend on a database; it depends on five lists of rows. Naming that
dependency is what lets a graph be built from a *milestone* — rows read back out of a stored
snapshot — and get the same arithmetic, through the same code, as the live BOM.

Which is the point. A comparison between a snapshot and today is only trustworthy if both
sides are derived the same way. If the milestone side had its own summing code, a diff would
be reporting the difference between two implementations as well as the difference between two
states, and there would be no way to tell those apart.

The rows are ORM instances, attached or not. SQLAlchemy is happy to construct an `Item` that
belongs to no session, so a snapshot's cells become ordinary `Item`/`BomLink`/`DecidedCost`
objects and the graph cannot tell the difference. No parallel row type, no `.get()` shims
inside the rollup.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AssemblyLabor, BomLink, DecidedCost, Item, ReferenceValue


@dataclass
class BomRows:
    """Everything a `BomGraph` needs, and nothing derived.

    `decided` and `labor` may hold **all** tiers — a snapshot stores all three, because which
    tier you want to look at is a question asked long after the snapshot was taken. The graph
    filters to its own tier when it builds, so passing a wider set is correct rather than
    merely tolerated.
    """
    items: list[Item]
    links: list[BomLink]
    decided: list[DecidedCost]
    labor: list[AssemblyLabor]
    rates: list[ReferenceValue]


def load_rows(db: Session, volume_tier: int | None = None) -> BomRows:
    """The five queries, in one place.

    `volume_tier=None` loads every tier — what a snapshot wants. A tier narrows the cost and
    labour queries, which is worth doing at the database rather than in Python for the request
    path, where this runs several times per page.

    Archived items and links are excluded here, as they are from every other view: the
    soft delete is a delete as far as the BOM is concerned.
    """
    items = list(db.execute(select(Item).where(Item.archived.is_(False))).scalars())
    links = list(db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars())

    dc_q = select(DecidedCost)
    al_q = select(AssemblyLabor)
    if volume_tier is not None:
        dc_q = dc_q.where(DecidedCost.volume_tier == volume_tier)
        al_q = al_q.where(AssemblyLabor.volume_tier == volume_tier)

    return BomRows(
        items=items,
        links=links,
        decided=list(db.execute(dc_q).scalars()),
        labor=list(db.execute(al_q).scalars()),
        # Assembly cost types carry the €/hour rate in `meta`. Loaded whole: there are a
        # handful, and the graph keys them by id.
        rates=list(db.execute(
            select(ReferenceValue).where(ReferenceValue.category == "assembly_cost_type")
        ).scalars()),
    )
