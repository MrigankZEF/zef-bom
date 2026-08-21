"""code_registry: never reuse a part number

Allocation was `max(existing) + 1` per module, and `set_module` parks an item's old digits
in the new module — together they burned the number space. Measured before this migration:
UNP 36 codes in use / max 188 (81% waste), UN 54 / 154 (65%), AEC 161 / 193 (17%).

Filling those holes safely needs a record of every number ever issued, not just the ones
currently occupied: 123 codes existed only in change_history, and reissuing one would make an
old drawing or PO refer to a different part.

Backfilled from items (live AND archived) plus every standard code recoverable from
change_history.

Revision ID: 0010_code_registry
Revises: 0009_sourcing_single_source
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "0010_code_registry"
down_revision = "0009_sourcing_single_source"
branch_labels = None
depends_on = None

_CODE = re.compile(r"^(?P<module>[A-Z]{2,5})(?P<number>\d{3,})(?P<suffix>[PA])$")


def upgrade() -> None:
    op.create_table(
        "code_registry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("module", sa.String(8), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("first_code", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("module", "number", name="uq_code_registry_module_number"),
    )
    op.create_index("ix_code_registry_module", "code_registry", ["module"])

    conn = op.get_bind()
    seen: dict[tuple[str, int], str] = {}

    def note(value) -> None:
        if not value:
            return
        m = _CODE.match(str(value).strip())
        if m:
            seen.setdefault((m.group("module"), int(m.group("number"))), str(value).strip())

    # every code an item holds today, archived ones included
    for (iid,) in conn.execute(sa.text("SELECT item_id FROM items")):
        note(iid)
    # every code that ever appeared in the audit log — these are the freed numbers
    for eid, old, new in conn.execute(
        sa.text("SELECT entity_id, old_value, new_value FROM change_history WHERE entity_type = 'item'")
    ):
        note(eid)
        note(old)
        note(new)

    if seen:
        conn.execute(
            sa.text(
                "INSERT INTO code_registry (module, number, first_code) "
                "VALUES (:module, :number, :first_code)"
            ),
            [
                {"module": mod, "number": num, "first_code": code}
                for (mod, num), code in sorted(seen.items())
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_code_registry_module", table_name="code_registry")
    op.drop_table("code_registry")
