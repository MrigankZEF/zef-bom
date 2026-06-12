"""cost types + assembly labor (time × rate), replacing flat assembly cost

Revision ID: 0006_cost_types_labor
Revises: 0005_cost_min_max
Create Date: 2026-06-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_cost_types_labor"
down_revision: Union[str, None] = "0005_cost_min_max"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ts = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "cost_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("rate_eur_h", sa.Numeric(12, 4), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", _ts, server_default=sa.func.now()),
    )
    op.create_table(
        "assembly_labor",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.String(32), sa.ForeignKey("items.item_id"), nullable=False, index=True),
        sa.Column("volume_tier", sa.Integer(), nullable=False),
        sa.Column("time_min", sa.Float()),
        sa.Column("time_likely", sa.Float(), nullable=False),
        sa.Column("time_max", sa.Float()),
        sa.Column("updated_at", _ts, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(255)),
        sa.UniqueConstraint("item_id", "volume_tier", name="uq_assembly_labor_item_volume"),
    )
    with op.batch_alter_table("items") as b:
        b.add_column(sa.Column("cost_type_id", sa.Integer(), nullable=True))
        for col in ("assembly_cost_eur_1", "assembly_cost_eur_100", "assembly_cost_eur_10k",
                    "assembly_time_min_1pc", "assembly_time_min_100", "assembly_time_min_10k"):
            b.drop_column(col)


def downgrade() -> None:
    with op.batch_alter_table("items") as b:
        b.drop_column("cost_type_id")
        for col in ("assembly_time_min_1pc", "assembly_time_min_100", "assembly_time_min_10k",
                    "assembly_cost_eur_1", "assembly_cost_eur_100", "assembly_cost_eur_10k"):
            b.add_column(sa.Column(col, sa.Float()))
    op.drop_table("assembly_labor")
    op.drop_table("cost_types")
