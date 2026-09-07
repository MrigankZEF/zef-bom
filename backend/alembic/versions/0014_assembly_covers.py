"""assembly_labor.covers — a three-way costing basis, replacing covers_subassemblies

  none  — roll up the contents and add this assembly's own time × rate. The default,
          and what almost every assembly is.
  labor — one time estimate here covers the assembly work on everything below. Parts
          below are still priced individually and still roll up. This is exactly what
          `covers_subassemblies` did.
  all   — a supplier quote on this assembly covers the parts AND the labour below. The
          subtree becomes documentation: it contributes nothing to cost, and nothing in
          it counts as a costing gap.

`time_likely` becomes nullable. An assembly bought as a finished unit has a price, not
minutes, and being forced to invent a time in order to reach the flag was the reason
'all' could not be expressed at all — hence three different hand-rolled conventions in
the live data (a childless "top down" harness, a manifold priced from below, and a stack
carrying a prose comment).

The backfill is deliberately conservative: every existing `covers_subassemblies` row
becomes 'labor', which is precisely the arithmetic those rows produce today. Nothing
re-prices on migration. The genuinely bought-in assemblies get re-marked 'all' by hand,
one at a time, each with a quote to back it.

Revision ID: 0014_assembly_covers
Revises: 0013_item_thumbnail
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_assembly_covers"
down_revision = "0013_item_thumbnail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_labor",
        sa.Column("covers", sa.String(8), nullable=False, server_default="none"),
    )
    # Bare column in the predicate: true/false on Postgres, 1/0 on SQLite, both truthy.
    op.execute("UPDATE assembly_labor SET covers = 'labor' WHERE covers_subassemblies")
    with op.batch_alter_table("assembly_labor") as b:
        b.alter_column("time_likely", existing_type=sa.Float(), nullable=True)
        b.drop_column("covers_subassemblies")


def downgrade() -> None:
    op.add_column(
        "assembly_labor",
        sa.Column("covers_subassemblies", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE assembly_labor SET covers_subassemblies = true WHERE covers <> 'none'")
    # A quoted assembly has no minutes to go back to; 0 keeps the column non-null without
    # inventing a plausible time.
    op.execute("UPDATE assembly_labor SET time_likely = 0 WHERE time_likely IS NULL")
    with op.batch_alter_table("assembly_labor") as b:
        b.alter_column("time_likely", existing_type=sa.Float(), nullable=False)
        b.drop_column("covers")
