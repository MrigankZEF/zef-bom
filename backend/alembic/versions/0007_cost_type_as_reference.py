"""cost types become reference values (category 'assembly_cost_type')

Revision ID: 0007_cost_type_ref
Revises: 0006_cost_types_labor
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007_cost_type_ref"
down_revision: Union[str, None] = "0006_cost_types_labor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Cost types now live in reference_values (category 'assembly_cost_type', rate in meta);
    # items.cost_type_id holds that reference value's id. Drop the dedicated table.
    op.drop_table("cost_types")


def downgrade() -> None:
    import sqlalchemy as sa

    op.create_table(
        "cost_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("rate_eur_h", sa.Numeric(12, 4), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
