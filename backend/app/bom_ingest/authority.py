"""Build the ingestion engine's `InventoryAuthority` from the live items table.

The legacy engine resolved Miro cells against an Excel inventory. Here the
authority is the database: existing `items` rows become the known part numbers /
names the resolver matches against. On an empty DB, every cell resolves to a
freshly allocated number — which is exactly the "start fresh from a new OPML" flow.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Item
from .miro_csv_fix import (
    ITEM_NUMBER_RE,
    InventoryAuthority,
    InventoryItem,
    normalize_item_name,
)


def build_authority_from_db(db: Session) -> InventoryAuthority:
    """Construct an InventoryAuthority from current `items` rows."""
    items: list[InventoryItem] = []
    for row in db.execute(select(Item)).scalars():
        match = ITEM_NUMBER_RE.match(row.item_id)
        if not match or not row.item_name:
            continue
        normalized = normalize_item_name(row.item_name)
        items.append(
            InventoryItem(
                partnumber=row.item_id,
                partname=normalized,
                module_code=match.group("module"),
                suffix=match.group("suffix"),
                normalized_name=normalized,
            )
        )
    # source_df is only used by the engine's Excel write-back path, which we don't use.
    authority = InventoryAuthority(items, pd.DataFrame())
    # Teach the resolver every module actually in use (e.g. MDAC), not just the seed set — so a
    # top assembly whose name *starts with a system word* ("Mdac Inimini system") is anchored to
    # that system and its bare-named children inherit it, instead of the whole tree asking for a
    # module. New/admin modules are added on top of this in parse_opml.
    authority.module_codes |= {it.module_code for it in items if it.module_code}
    return authority
