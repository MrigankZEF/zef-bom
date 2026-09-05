"""items.thumbnail_file_id: pin one uploaded image as the item's picture

A part number and a name do not tell you what a thing looks like. The images were already
being uploaded into each item's Drive folder, so this only records *which* of them is the
picture — a pinned file id rather than "the first image in the folder", so uploading a new
photo never silently swaps the thumbnail.

No image processing: Drive already stores a thumbnail for every format it can render, and
the backend proxies those bytes (its `thumbnailLink` expires within hours, so the browser
never sees one).

Revision ID: 0013_item_thumbnail
Revises: 0012_evidence_optional
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_item_thumbnail"
down_revision = "0012_evidence_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch:
        batch.add_column(sa.Column("thumbnail_file_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("items") as batch:
        batch.drop_column("thumbnail_file_id")
