"""3-point decided cost: add cost_min / cost_max

Revision ID: 0005_cost_min_max
Revises: 0004_assembly_cost
Create Date: 2026-06-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_cost_min_max"
down_revision: Union[str, None] = "0004_assembly_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("decided_costs", sa.Column("cost_min", sa.Numeric(14, 4), nullable=True))
    op.add_column("decided_costs", sa.Column("cost_max", sa.Numeric(14, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("decided_costs", "cost_max")
    op.drop_column("decided_costs", "cost_min")
