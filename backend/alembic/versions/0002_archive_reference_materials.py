"""archive flags, reference_values, multi-material, per-tier cost basis

Revision ID: 0002_archive_ref
Revises: 0001_initial
Create Date: 2026-06-08
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002_archive_ref"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_json = sa.JSON().with_variant(JSONB(), "postgresql")
_ts = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("items", sa.Column("materials", _json))
    op.add_column("items", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("bom_links", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("decided_costs", sa.Column("make_or_buy", sa.String(16)))
    op.add_column("decided_costs", sa.Column("source_type", sa.String(24)))

    # copy existing single material into the new materials list
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT item_id, material FROM items WHERE material IS NOT NULL AND material <> ''")).fetchall()
    for item_id, material in rows:
        bind.execute(
            sa.text("UPDATE items SET materials = :m WHERE item_id = :id"),
            {"m": json.dumps([material]), "id": item_id},
        )

    op.create_table(
        "reference_values",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("label", sa.Text()),
        sa.Column("meta", _json),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", _ts, server_default=sa.func.now()),
        sa.UniqueConstraint("category", "value", name="uq_reference_category_value"),
    )
    op.create_index("ix_reference_values_category", "reference_values", ["category"])


def downgrade() -> None:
    op.drop_table("reference_values")
    op.drop_column("decided_costs", "source_type")
    op.drop_column("decided_costs", "make_or_buy")
    op.drop_column("bom_links", "archived")
    op.drop_column("items", "archived")
    op.drop_column("items", "materials")
