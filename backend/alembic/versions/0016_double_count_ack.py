"""assembly_labor.double_count_ack — the descendants whose double count is accepted

An assembly marked `covers='labor'` says its time covers the work below it, and the rollup
nevertheless adds every descendant's own assembly cost on top. That is deliberate: it keeps a
sub-assembly's own labour figure usable the day it is built under a parent that does not cover
it. So the drawer's old "one of the two is wrong" was simply untrue — both are counted, on
purpose — and the honest message is a question about double counting.

A question needs an answer that sticks, and this column holds it: the list of descendant item
ids somebody has looked at and accepted. A list rather than a boolean, because a boolean would
stay suppressed when a NEW item appears below the cover — exactly the case worth surfacing.

Revision ID: 0016_double_count_ack
Revises: 0015_cogs_facilities
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_double_count_ack"
down_revision = "0015_cogs_facilities"
branch_labels = None
depends_on = None

# JSONB on Postgres, plain JSON on SQLite — the same portability the model uses, so one
# migration is valid on the dev database and on Railway.
JSON_T = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("assembly_labor", sa.Column("double_count_ack", JSON_T, nullable=True))


def downgrade() -> None:
    op.drop_column("assembly_labor", "double_count_ack")
