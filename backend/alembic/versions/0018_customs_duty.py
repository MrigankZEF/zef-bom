"""Customs: items.hs_code + country_of_origin, duty_rates, cogs_facility.country

Real columns rather than `field_definitions` rows: the custom-field mechanism is right for
what only humans read, and these are cost inputs the roll-up reaches — an EAV lookup inside
a rollup is the wrong shape.

`country_of_origin` is deliberately separate from `supplier_country`. Customs charges on
where a thing was MADE: a German distributor shipping a Chinese-made part is a CN origin at
a DE supplier, and conflating the two would be wrong on exactly the parts where duty is
largest.

`duty_rates` matches on the LONGEST HS prefix, so a 4-digit heading covers everything
beneath it until a 6- or 8-digit line is entered. `origin_country = ''` is the third-country
wildcard — `''` and not NULL, so the unique constraint still catches a duplicate.

This migration adds structure only. Filling in HS codes and TARIC rates is a separate job,
by decision: the acceptance test is that an item with a code and a matching rate produces a
duty figure that rolls up, not that the BOM is classified.

Revision ID: 0018_customs_duty
Revises: 0017_bom_milestones
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_customs_duty"
down_revision = "0017_bom_milestones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("hs_code", sa.String(length=16), nullable=True))
    op.add_column("items", sa.Column("country_of_origin", sa.String(length=64), nullable=True))
    op.create_index("ix_items_hs_code", "items", ["hs_code"])

    op.add_column("cogs_facility", sa.Column("country", sa.String(length=2), nullable=True))

    op.create_table(
        "duty_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hs_code", sa.String(length=16), nullable=False),
        sa.Column("origin_country", sa.String(length=2), nullable=False, server_default=""),
        sa.Column("destination_country", sa.String(length=2), nullable=False),
        sa.Column("rate_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hs_code", "origin_country", "destination_country", "valid_from",
                            name="uq_duty_rate_code_origin_dest_from"),
    )
    op.create_index("ix_duty_rates_hs_code", "duty_rates", ["hs_code"])


def downgrade() -> None:
    op.drop_index("ix_duty_rates_hs_code", table_name="duty_rates")
    op.drop_table("duty_rates")
    op.drop_column("cogs_facility", "country")
    op.drop_index("ix_items_hs_code", table_name="items")
    op.drop_column("items", "country_of_origin")
    op.drop_column("items", "hs_code")
