"""Seed the catalog from the ZEF BOM Inventory Excel.

Upserts an item per valid part number (code), and writes its 3-point cost at the
10k tier (Future_10k_min / avg / Future_10k_max) plus the @1 prototype cost where
present. Idempotent — safe to re-run. The 100 tier is left empty (not in the sheet).

Usage:
    python scripts/seed_catalog.py "G:/Shared drives/BOM/Bom Exploration 4/database/ZEF BOM inventory.xlsx"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make `app` importable

import pandas as pd
from sqlalchemy import select

from app.bom_ingest.miro_csv_fix import ITEM_NUMBER_RE, normalize_item_name
from app.db import SessionLocal
from app.models import DecidedCost, Item

DEFAULT_XLSX = r"G:\Shared drives\BOM\Bom Exploration 4\database\ZEF BOM inventory.xlsx"


def _num(v):
    return float(v) if pd.notna(v) else None


def _set_cost(db, code, tier, likely, cmin=None, cmax=None):
    dc = db.execute(
        select(DecidedCost).where(DecidedCost.item_id == code, DecidedCost.volume_tier == tier)
    ).scalar_one_or_none()
    if dc is None:
        dc = DecidedCost(item_id=code, volume_tier=tier)
        db.add(dc)
    dc.unit_cost_eur = likely
    dc.cost_min = cmin
    dc.cost_max = cmax
    dc.decided_by = "catalog-import"


def run(xlsx_path: str) -> None:
    df = pd.read_excel(xlsx_path, "Sheet1")
    db = SessionLocal()
    created = updated = costed = 0
    try:
        for _, row in df.iterrows():
            code = str(row.get("partnumber") or "").strip()
            m = ITEM_NUMBER_RE.match(code)
            name = str(row.get("partname") or "").strip()
            if not m or not name:
                continue
            item = db.get(Item, code)
            if item is None:
                db.add(Item(
                    item_id=code, item_name=normalize_item_name(name),
                    item_type="assembly" if m.group("suffix") == "A" else "part",
                    module_code=m.group("module"),
                    created_by="catalog-import", updated_by="catalog-import",
                ))
                created += 1
            else:
                updated += 1
            likely_10k = _num(row.get("avg"))
            if likely_10k is not None:
                _set_cost(db, code, 10000, likely_10k, _num(row.get("Future_10k_min")), _num(row.get("Future_10k_max")))
                costed += 1
            proto = _num(row.get("current_cost_proto"))
            if proto is not None:
                _set_cost(db, code, 1, proto)
        db.commit()
        print(f"catalog import: created {created}, updated {updated}, items with 10k cost {costed}")
    finally:
        db.close()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XLSX)
