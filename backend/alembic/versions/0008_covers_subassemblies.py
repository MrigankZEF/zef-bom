"""assembly_labor.covers_subassemblies

Per-tier flag: this assembly's cost already includes the work on everything beneath it
(an outsourced or bought-in assembly). Per tier because sourcing genuinely differs by
volume — hand-built at @1, outsourced at @10k.

Revision ID: 0008_covers_subassemblies
Revises: 0007_cost_type_ref
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_covers_subassemblies"
down_revision = "0007_cost_type_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_labor",
        sa.Column("covers_subassemblies", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("assembly_labor", "covers_subassemblies")
