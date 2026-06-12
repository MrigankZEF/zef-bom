"""assembly cost + per-tier assembly time

Revision ID: 0004_assembly_cost
Revises: 0003_users
Create Date: 2026-06-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_assembly_cost"
down_revision: Union[str, None] = "0003_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # per-volume assembly time (add the missing @100) + assembly (process) cost per tier
    op.add_column("items", sa.Column("assembly_time_min_100", sa.Float(), nullable=True))
    op.add_column("items", sa.Column("assembly_cost_eur_1", sa.Float(), nullable=True))
    op.add_column("items", sa.Column("assembly_cost_eur_100", sa.Float(), nullable=True))
    op.add_column("items", sa.Column("assembly_cost_eur_10k", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "assembly_cost_eur_10k")
    op.drop_column("items", "assembly_cost_eur_100")
    op.drop_column("items", "assembly_cost_eur_1")
    op.drop_column("items", "assembly_time_min_100")
