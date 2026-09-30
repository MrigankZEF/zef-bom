"""api_tokens — a read-only credential for programs, so a script need not be a browser

Revision ID: 0019_api_tokens
Revises: 0018_customs_duty
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_api_tokens"
down_revision = "0018_customs_duty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # The hash, never the token. 64 hex characters of SHA-256.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("user_email", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_hash"),
    )
    # Every request carrying a token looks it up by hash, so this index is on the hot path.
    op.create_index("ix_api_tokens_hash", "api_tokens", ["token_hash"])
    op.create_index("ix_api_tokens_user", "api_tokens", ["user_email"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_user", table_name="api_tokens")
    op.drop_index("ix_api_tokens_hash", table_name="api_tokens")
    op.drop_table("api_tokens")
