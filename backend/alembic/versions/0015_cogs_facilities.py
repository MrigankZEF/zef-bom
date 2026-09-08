"""The COGS ladder's four tables: facilities, their sub-items, the cost matrix, the locks.

Four decisions are baked into these tables, each for a reason that bites if reversed.

`cogs_value.item_id` is NOT NULL DEFAULT ''. The empty string means "the facility's own
value, for a locked row". A nullable column would be the obvious shape and is the wrong one:
NULLs compare distinct in a unique constraint on both SQLite and Postgres, so the constraint
would silently permit unlimited duplicate facility-own rows — and `backup._natural_key_cols`
drives restore's dedup off exactly that first unique constraint, so a restore would multiply
them.

`cogs_value.value` is Numeric(16,4), matching `decided_costs.unit_cost_eur` rather than
Float. The overhead pool reaches EUR 218,170,000 at @10k on the fixtures, and every value
round-trips through an Excel backup.

`cogs_lock` carries no boolean. Presence is locked. A `locked=false` row is state you then
have to keep consistent with deletion, for no gain.

Nothing is pre-seeded. An absent row means "not entered" — it displays as an em dash and
contributes 0 — whereas a stored 0 means "known to be zero". Seeding zeros would claim
knowledge nobody entered.

Revision ID: 0015_cogs_facilities
Revises: 0014_assembly_covers
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_cogs_facilities"
down_revision = "0014_assembly_covers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cogs_facility",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        # assembly | logistics | field. Immutable after creation — enforced in the router,
        # not here, because a CHECK would also have to change every time a kind is added.
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(255)),
        sa.UniqueConstraint("code", name="uq_cogs_facility_code"),
    )

    op.create_table(
        "cogs_facility_item",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("facility_id", sa.Integer(), sa.ForeignKey("cogs_facility.id"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("facility_id", "code", name="uq_cogs_item_facility_code"),
    )
    op.create_index("ix_cogs_facility_item_facility_id", "cogs_facility_item", ["facility_id"])

    op.create_table(
        "cogs_value",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("facility_id", sa.Integer(), sa.ForeignKey("cogs_facility.id"), nullable=False),
        # str(cogs_facility_item.id), or '' for the facility's own value. No FK: a column
        # that also holds the sentinel cannot carry one.
        sa.Column("item_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("row_key", sa.String(24), nullable=False),
        sa.Column("volume_tier", sa.Integer(), nullable=False),
        sa.Column("value", sa.Numeric(16, 4), nullable=False),
        sa.UniqueConstraint(
            "facility_id", "item_id", "row_key", "volume_tier", name="uq_cogs_value_cell"
        ),
    )
    op.create_index("ix_cogs_value_facility_id", "cogs_value", ["facility_id"])

    op.create_table(
        "cogs_lock",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("facility_id", sa.Integer(), sa.ForeignKey("cogs_facility.id"), nullable=False),
        sa.Column("row_key", sa.String(24), nullable=False),
        sa.UniqueConstraint("facility_id", "row_key", name="uq_cogs_lock_facility_row"),
    )
    op.create_index("ix_cogs_lock_facility_id", "cogs_lock", ["facility_id"])


def downgrade() -> None:
    # Children before parents, or the foreign keys refuse.
    op.drop_index("ix_cogs_lock_facility_id", table_name="cogs_lock")
    op.drop_table("cogs_lock")
    op.drop_index("ix_cogs_value_facility_id", table_name="cogs_value")
    op.drop_table("cogs_value")
    op.drop_index("ix_cogs_facility_item_facility_id", table_name="cogs_facility_item")
    op.drop_table("cogs_facility_item")
    op.drop_table("cogs_facility")
