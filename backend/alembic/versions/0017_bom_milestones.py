"""bom_milestones — a top-level BOM frozen as inputs, so today can be compared against it

`payload` holds the rows a `BomGraph` is built from: items, links, decided costs, assembly
labour and the assembly cost-type rates, scoped to the subtree, all three tiers, in the same
cell format the .xlsx backup uses. No rolled-up cost, no coverage, no totals — the data model
says derive rather than store, and a stored total would disagree with the rollup the first
time the rollup was fixed.

A payload rather than a timestamp because `change_history` is append-only and aspirational,
not foundational: three write paths logged nothing until `71045fd`, and a state reconstructed
from an incomplete log is a BOM that never existed. Once `as_of` replay exists it gets checked
against these payloads, not the other way round.

Revision ID: 0017_bom_milestones
Revises: 0016_double_count_ack
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017_bom_milestones"
down_revision = "0016_double_count_ack"
branch_labels = None
depends_on = None

JSON_T = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "bom_milestones",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("root_item_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("taken_by", sa.String(length=255), nullable=True),
        sa.Column("payload", JSON_T, nullable=True),
        sa.ForeignKeyConstraint(["root_item_id"], ["items.item_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bom_milestones_root_item_id", "bom_milestones", ["root_item_id"])


def downgrade() -> None:
    op.drop_index("ix_bom_milestones_root_item_id", table_name="bom_milestones")
    op.drop_table("bom_milestones")
