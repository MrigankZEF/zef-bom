"""Sourcing: one source of truth (per tier) + the buy / made-to-order / make taxonomy

`make_or_buy` lived on BOTH `items` and `decided_costs`, giving two answers to one
question — and the two UIs didn't even offer the same options (Details had
"modified-buy", Cost did not). The per-tier copy wins: sourcing genuinely differs by
volume (measured: 6 items differ between tiers, e.g. UN023P = make@1, buy@100,
buy@10k) and it is where the data actually lives (80 rows vs 14).

Values become: buy (off the shelf) | made-to-order (our specs) | make (in house).
"modified-buy" maps to made-to-order.

Verified before writing this: all 14 items carrying an item-level value already have
decided_costs rows to migrate into, so nothing is lost.

Revision ID: 0009_sourcing_single_source
Revises: 0008_covers_subassemblies
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_sourcing_single_source"
down_revision = "0008_covers_subassemblies"
branch_labels = None
depends_on = None

_MAP = {"modified-buy": "made-to-order"}


def upgrade() -> None:
    conn = op.get_bind()

    # 1) normalise any legacy value already sitting on decided_costs
    for old, new in _MAP.items():
        conn.execute(
            sa.text("UPDATE decided_costs SET make_or_buy = :new WHERE make_or_buy = :old"),
            {"new": new, "old": old},
        )

    # 2) push each item-level value down onto that item's tiers that don't state one
    rows = conn.execute(
        sa.text("SELECT item_id, make_or_buy FROM items WHERE make_or_buy IS NOT NULL AND make_or_buy != ''")
    ).fetchall()
    for item_id, value in rows:
        conn.execute(
            sa.text(
                "UPDATE decided_costs SET make_or_buy = :v "
                "WHERE item_id = :i AND (make_or_buy IS NULL OR make_or_buy = '')"
            ),
            {"v": _MAP.get(value, value), "i": item_id},
        )

    # 3) the item-level column is now redundant
    with op.batch_alter_table("items") as batch:
        batch.drop_column("make_or_buy")


def downgrade() -> None:
    # The column comes back empty: which tier's value would be "the" item-level one is
    # exactly the ambiguity this migration removes.
    with op.batch_alter_table("items") as batch:
        batch.add_column(sa.Column("make_or_buy", sa.String(16), nullable=True))
