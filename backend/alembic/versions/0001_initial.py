"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ts = sa.DateTime(timezone=True)
# Portable JSON: JSONB on Postgres, plain JSON on SQLite (local dev).
_json = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("item_id", sa.String(32), primary_key=True),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("item_type", sa.String(16), nullable=False),
        sa.Column("is_top_level", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("module_code", sa.String(8)),
        sa.Column("make_or_buy", sa.String(16)),
        sa.Column("material", sa.Text()),
        sa.Column("weight_grams", sa.Float()),
        sa.Column("unit_of_measure", sa.String(16), nullable=False, server_default="pcs"),
        sa.Column("supplier", sa.Text()),
        sa.Column("supplier_country", sa.String(64)),
        sa.Column("lead_time_weeks", sa.Float()),
        sa.Column("assembly_time_min_1pc", sa.Float()),
        sa.Column("assembly_time_min_10k", sa.Float()),
        sa.Column("drawing_url", sa.Text()),
        sa.Column("drive_folder_url", sa.Text()),
        sa.Column("comment", sa.Text()),
        sa.Column("external_reference", sa.Text()),
        sa.Column("created_at", _ts, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255)),
        sa.Column("updated_at", _ts, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(255)),
    )

    op.create_table(
        "field_definitions",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("applies_to", sa.String(16), nullable=False, server_default="both"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("options", _json),
        sa.Column("unit", sa.String(16)),
        sa.Column("group", sa.String(32)),
        sa.Column("created_at", _ts, server_default=sa.func.now()),
    )

    op.create_table(
        "bom_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("parent_item_id", sa.String(32), sa.ForeignKey("items.item_id"), nullable=False),
        sa.Column("child_item_id", sa.String(32), sa.ForeignKey("items.item_id"), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="1"),
        sa.Column("unit_of_measure", sa.String(16), nullable=False, server_default="pcs"),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", _ts, server_default=sa.func.now()),
        sa.Column("updated_at", _ts, server_default=sa.func.now()),
        sa.UniqueConstraint("parent_item_id", "child_item_id", name="uq_bom_links_parent_child"),
    )
    op.create_index("ix_bom_links_parent_item_id", "bom_links", ["parent_item_id"])
    op.create_index("ix_bom_links_child_item_id", "bom_links", ["child_item_id"])

    op.create_table(
        "change_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("changed_at", _ts, server_default=sa.func.now()),
        sa.Column("changed_by", sa.String(255)),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("field_changed", sa.String(64)),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("change_type", sa.String(16), nullable=False),
        sa.Column("change_reason", sa.Text()),
    )
    op.create_index("ix_change_history_changed_at", "change_history", ["changed_at"])
    op.create_index("ix_change_history_entity_type", "change_history", ["entity_type"])
    op.create_index("ix_change_history_entity_id", "change_history", ["entity_id"])

    op.create_table(
        "cost_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.String(32), sa.ForeignKey("items.item_id"), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("supplier_name", sa.Text()),
        sa.Column("supplier_country", sa.String(64)),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("unit_cost", sa.Numeric(14, 4), nullable=False),
        sa.Column("volume_tier", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.Date()),
        sa.Column("confidence", sa.String(8)),
        sa.Column("cost_min", sa.Numeric(14, 4)),
        sa.Column("cost_max", sa.Numeric(14, 4)),
        sa.Column("note", sa.Text()),
        sa.Column("attachment_url", sa.Text()),
        sa.Column("created_at", _ts, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255)),
    )
    op.create_index("ix_cost_evidence_item_id", "cost_evidence", ["item_id"])

    op.create_table(
        "decided_costs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.String(32), sa.ForeignKey("items.item_id"), nullable=False),
        sa.Column("volume_tier", sa.Integer(), nullable=False),
        sa.Column("unit_cost_eur", sa.Numeric(14, 4), nullable=False),
        sa.Column("confidence", sa.String(8)),
        sa.Column("basis_note", sa.Text()),
        sa.Column("based_on_evidence_id", sa.Integer(), sa.ForeignKey("cost_evidence.id")),
        sa.Column("decided_at", _ts, server_default=sa.func.now()),
        sa.Column("decided_by", sa.String(255)),
        sa.UniqueConstraint("item_id", "volume_tier", name="uq_decided_cost_item_volume"),
    )
    op.create_index("ix_decided_costs_item_id", "decided_costs", ["item_id"])

    op.create_table(
        "field_values",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.String(32), sa.ForeignKey("items.item_id"), nullable=False),
        sa.Column("field_key", sa.String(64), sa.ForeignKey("field_definitions.key"), nullable=False),
        sa.Column("value", sa.Text()),
        sa.Column("updated_at", _ts, server_default=sa.func.now()),
        sa.UniqueConstraint("item_id", "field_key", name="uq_field_value_item_field"),
    )
    op.create_index("ix_field_values_item_id", "field_values", ["item_id"])

    op.create_table(
        "upload_batches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_filename", sa.Text(), nullable=False),
        sa.Column("uploaded_at", _ts, server_default=sa.func.now()),
        sa.Column("uploaded_by", sa.String(255)),
        sa.Column("notes", sa.Text()),
        sa.Column("is_top_level_bom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending_review"),
        sa.Column("summary_json", _json),
    )


def downgrade() -> None:
    op.drop_table("upload_batches")
    op.drop_table("field_values")
    op.drop_table("decided_costs")
    op.drop_table("cost_evidence")
    op.drop_table("change_history")
    op.drop_table("bom_links")
    op.drop_table("field_definitions")
    op.drop_table("items")
