"""Export a top-level BOM as OPML (Miro round-trip) or CSV (spreadsheets).

OPML node text matches the import grammar — ``CODE: name`` with ``#qty`` appended
when quantity ≠ 1 — so an exported file re-imports cleanly. CSV is a flat, indented
BOM explosion (one row per node occurrence in the tree).
"""
from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from xml.dom import minidom

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DecidedCost
from ..rollups import BomGraph

router = APIRouter(tags=["export"])

TIERS = [1, 100, 10000]


def _node_text(item, qty: float) -> str:
    text = f"{item.item_id}: {item.item_name}"
    if qty and float(qty) != 1:
        text += f" #{int(qty)}"
    return text


@router.get("/export/opml")
def export_opml(root: str = Query(...), db: Session = Depends(get_db)):
    g = BomGraph(db)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")

    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = g.items[root].item_name
    body = ET.SubElement(opml, "body")

    def build(item_id: str, qty: float, parent_el: ET.Element, seen: frozenset[str]) -> None:
        el = ET.SubElement(parent_el, "outline", text=_node_text(g.items[item_id], qty))
        if item_id in seen:  # cycle guard
            return
        for child, q in g.children.get(item_id, []):
            if child in g.items:
                build(child, q, el, seen | {item_id})

    build(root, 1, body, frozenset())
    xml = minidom.parseString(ET.tostring(opml, encoding="utf-8")).toprettyxml(indent="  ", encoding="utf-8")
    return Response(
        content=xml,
        media_type="text/x-opml",
        headers={"Content-Disposition": f'attachment; filename="{root}.opml"'},
    )


@router.get("/export/csv")
def export_csv(root: str = Query(...), db: Session = Depends(get_db)):
    g = BomGraph(db)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")

    decided: dict[tuple[str, int], float] = {
        (dc.item_id, dc.volume_tier): float(dc.unit_cost_eur)
        for dc in db.execute(select(DecidedCost)).scalars()
    }

    rows: list[dict] = []

    def cost_for(item, item_id: str, is_leaf: bool, tier: int):
        # leaf part → its decided unit cost; assemblies are derived, left blank in the flat CSV
        return decided.get((item_id, tier), "") if is_leaf else ""

    def walk(item_id: str, qty: float, parent_id: str | None, level: int, seen: frozenset[str]) -> None:
        it = g.items[item_id]
        is_leaf = not g.children.get(item_id)
        rows.append({
            "level": level,
            "item_id": item_id,
            "item_name": it.item_name,
            "type": it.item_type,
            "module": it.module_code or "",
            "qty_in_parent": int(qty) if qty else 1,
            "parent_id": parent_id or "",
            "weight_g": it.weight_grams if it.weight_grams is not None else "",
            "supplier": it.supplier or "",
            "country": it.supplier_country or "",
            "material": ", ".join(it.materials) if it.materials else (it.material or ""),
            "cost_eur@1": cost_for(it, item_id, is_leaf, 1),
            "cost_eur@100": cost_for(it, item_id, is_leaf, 100),
            "cost_eur@10k": cost_for(it, item_id, is_leaf, 10000),
        })
        if item_id in seen:
            return
        for child, q in g.children.get(item_id, []):
            if child in g.items:
                walk(child, q, item_id, level + 1, seen | {item_id})

    walk(root, 1, None, 0, frozenset())

    cols = ["level", "item_id", "item_name", "type", "module", "qty_in_parent", "parent_id",
            "weight_g", "supplier", "country", "material", "cost_eur@1", "cost_eur@100", "cost_eur@10k"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{root}.csv"'},
    )
