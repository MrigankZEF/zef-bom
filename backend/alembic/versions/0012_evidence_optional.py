"""cost_evidence: a row can be a plain note, with no price and no source

Costing work produces two kinds of note. One is "here is a quote for €2.77" — the row this
table was built for. The other is "asked the supplier, waiting on a price", or the reasoning
behind a number nobody has committed to yet. That second kind had nowhere to live: both
`unit_cost` and `source_type` were NOT NULL, so the only way to file a thought was to invent
a price for it.

Safe to relax: nothing derives cost from evidence. Rollups read `decided_costs` only, and
the drawer renders a missing price as "—".

Revision ID: 0012_evidence_optional
Revises: 0011_item_links
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_evidence_optional"
down_revision = "0011_item_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cost_evidence") as batch:
        batch.alter_column("unit_cost", existing_type=sa.Numeric(14, 4), nullable=True)
        batch.alter_column("source_type", existing_type=sa.String(length=24), nullable=True)


def downgrade() -> None:
    # A note-only row has no price to put back, so it cannot satisfy the NOT NULL again.
    # Dropping those rows would destroy the very data this migration exists to allow, so
    # they are parked at 0 with the source spelled out instead.
    op.execute("UPDATE cost_evidence SET unit_cost = 0 WHERE unit_cost IS NULL")
    op.execute("UPDATE cost_evidence SET source_type = 'other' WHERE source_type IS NULL")
    with op.batch_alter_table("cost_evidence") as batch:
        batch.alter_column("unit_cost", existing_type=sa.Numeric(14, 4), nullable=False)
        batch.alter_column("source_type", existing_type=sa.String(length=24), nullable=False)
