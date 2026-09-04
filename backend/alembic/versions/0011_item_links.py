"""item_links + supplier_part_number: outward links as data, not free-text notes

People were pasting supplier and shop URLs into the item's notes field, because that was the
only place they fitted. That loses the structure (which link is the supplier? which the
alternative?), makes the notes unreadable, and the long unbroken URLs overflowed the card.

A table rather than a set of columns, because the count varies per item — one part has three
alternative suppliers, the next has none. `link_type` holds a reference_values value
(category 'link_type') so the kinds stay admin-managed, exactly like suppliers and materials.

`supplier_part_number` is a plain column: there is exactly one per item.

Revision ID: 0011_item_links
Revises: 0010_code_registry
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_item_links"
down_revision = "0010_code_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.String(length=32), nullable=False),
        sa.Column("link_type", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.item_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_item_links_item_id", "item_links", ["item_id"])

    with op.batch_alter_table("items") as batch:
        batch.add_column(sa.Column("supplier_part_number", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("items") as batch:
        batch.drop_column("supplier_part_number")
    op.drop_index("ix_item_links_item_id", table_name="item_links")
    op.drop_table("item_links")
