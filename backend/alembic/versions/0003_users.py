"""users table (Sign in with Google)

Revision ID: 0003_users
Revises: 0002_archive_ref
Create Date: 2026-06-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_users"
down_revision: Union[str, None] = "0002_archive_ref"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ts = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(255), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("role", sa.String(16), nullable=False, server_default="editor"),
        sa.Column("created_at", _ts, server_default=sa.func.now()),
        sa.Column("last_login", _ts),
    )


def downgrade() -> None:
    op.drop_table("users")
