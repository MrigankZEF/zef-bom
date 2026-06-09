"""Seed reference-data lists (countries, modules, materials, suppliers).

Idempotent — safe to re-run. Countries ship with a `region` so cost analysis can
group EU vs Asia etc. without mixing "EU" into the country dropdown. Modules,
materials and suppliers are derived from whatever's already in the DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import CostEvidence, Item, ReferenceValue  # noqa: E402

COUNTRIES = [
    ("Netherlands", "EU"), ("Germany", "EU"), ("Czechia", "EU"), ("France", "EU"),
    ("Italy", "EU"), ("Spain", "EU"), ("Poland", "EU"), ("Belgium", "EU"),
    ("Austria", "EU"), ("Sweden", "EU"), ("Denmark", "EU"), ("Ireland", "EU"),
    ("Switzerland", "Europe"), ("United Kingdom", "Europe"), ("Norway", "Europe"),
    ("Turkey", "Europe"), ("India", "Asia"), ("China", "Asia"), ("Japan", "Asia"),
    ("South Korea", "Asia"), ("Taiwan", "Asia"), ("Vietnam", "Asia"),
    ("Thailand", "Asia"), ("Malaysia", "Asia"), ("Singapore", "Asia"),
    ("United States", "Americas"), ("Canada", "Americas"), ("Mexico", "Americas"),
    ("Brazil", "Americas"), ("Other", None),
]

MODULE_LABELS = {
    "AEC": "Anion-exchange electrolyzer", "DAC": "Direct air capture",
    "DRY": "Dryer & compression", "MEU": "Methanol synthesis", "CTL": "Control & power",
    "ENC": "Enclosure & frame", "UN": "Universal / shared", "POW": "Power",
    "DS": "Distillation", "FM": "Fluid management", "MS": "Methanol storage", "PL": "Plant",
}


def _ensure(db, category, value, label=None, meta=None, order=0):
    existing = db.execute(
        select(ReferenceValue).where(ReferenceValue.category == category, ReferenceValue.value == value)
    ).scalar_one_or_none()
    if existing:
        return False
    db.add(ReferenceValue(category=category, value=value, label=label, meta=meta, sort_order=order))
    return True


def main() -> int:
    db = SessionLocal()
    added = {"country": 0, "module": 0, "material": 0, "supplier": 0}
    try:
        for i, (name, region) in enumerate(COUNTRIES):
            if _ensure(db, "country", name, meta={"region": region} if region else None, order=i):
                added["country"] += 1

        modules = {it.module_code for it in db.execute(select(Item)).scalars() if it.module_code}
        for code in sorted(modules):
            if _ensure(db, "module", code, label=MODULE_LABELS.get(code)):
                added["module"] += 1

        materials: set[str] = set()
        for it in db.execute(select(Item)).scalars():
            if it.material:
                materials.add(it.material)
            for m in (it.materials or []):
                materials.add(m)
        for m in sorted(materials):
            if _ensure(db, "material", m):
                added["material"] += 1

        suppliers = {
            ev.supplier_name for ev in db.execute(select(CostEvidence)).scalars() if ev.supplier_name
        }
        for s in sorted(suppliers):
            if _ensure(db, "supplier", s):
                added["supplier"] += 1

        db.commit()
    finally:
        db.close()
    print("Seeded reference values:", added)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
