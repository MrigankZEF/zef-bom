"""Ingestion orchestration: OPML file → resolved cells → diff summary → DB.

M2 covers the first-import / seed path (apply a new OPML into the DB and record an
upload_batch). The richer incremental diff review (renamed / removed / qty-change,
opt-in per-row approval) layers on top in M5.
"""
from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..history import record_change
from ..models import BomLink, Item, UploadBatch
from .authority import build_authority_from_db
from .miro_csv_fix import (
    BLOCKER_STATUSES,
    ITEM_NUMBER_RE,
    InventoryAuthority,
    InventoryItem,
    ParsedCell,
    build_parsed_cells,
    load_bom_input,
    normalize_item_name,
    repair_mindmap_tree,
)
from .writer import _item_type_of, _module_of, _top_level_numbers, apply_cells


def _augment_with_opml_numbers(
    authority: InventoryAuthority, cells: list[ParsedCell]
) -> InventoryAuthority:
    """Trust explicit numbers the OPML already carries that the DB doesn't know yet.

    Without this, a from-scratch import would flag every pre-numbered cell as
    needs-review. Seeding them into the authority makes numbered cells resolve as
    use-existing (the writer then creates them) and pushes new allocations above the
    OPML's max sequence per module so they never collide.
    """
    extra: dict[str, InventoryItem] = {}
    for c in cells:
        num = c.explicit_item_number
        if not num or authority.lookup_by_number(num) or num in extra:
            continue
        m = ITEM_NUMBER_RE.match(num)
        if not m:
            continue
        extra[num] = InventoryItem(
            partnumber=num,
            partname=c.normalized_item_name,
            module_code=m.group("module"),
            suffix=m.group("suffix"),
            normalized_name=c.normalized_item_name,
        )
    if not extra:
        return authority
    return InventoryAuthority(authority.items + list(extra.values()), pd.DataFrame())


def parse_opml(db: Session, path: Path) -> tuple[list[ParsedCell], InventoryAuthority]:
    """Load + repair an OPML/CSV and resolve every cell against the live items table.

    Two-pass: resolve once to surface the explicit numbers the OPML carries, seed
    those into the authority, then re-resolve so they're treated as known.
    """
    df_raw, input_format = load_bom_input(path)
    df = df_raw if input_format == "opml" else repair_mindmap_tree(df_raw)
    authority = build_authority_from_db(db)
    cells = build_parsed_cells(df, authority)
    augmented = _augment_with_opml_numbers(authority, cells)
    if augmented is not authority:
        authority = augmented
        cells = build_parsed_cells(df, authority)
    return cells, authority


def _confidence(cell: ParsedCell) -> str:
    explicit = bool(cell.explicit_item_number or (cell.explicit_module and cell.explicit_suffix))
    return "high" if explicit else "medium"


def build_diff_summary(cells: list[ParsedCell]) -> dict:
    """Bucket resolved cells for the upload_batch summary + Uploads UI.

    Deduped by item number, since OPML rows repeat every ancestor (a shared part
    appears in many cells). Buckets:
      * allocated      — unnumbered cells we auto-numbered (new to everyone)
      * pre_numbered    — cells the OPML already carried a number for
      * conflicts / needs_review — blockers awaiting human resolution
    Structural diffing vs prior uploads (renamed/removed/qty_change) lands in M5.
    """
    allocated: dict[str, dict] = {}
    pre_numbered: dict[str, dict] = {}
    conflicts, needs_review = [], []
    for c in cells:
        if c.resolution_status == "conflict" or c.blocker_reason == "conflict":
            conflicts.append(
                {"number": c.resolved_item_number, "name": c.normalized_item_name,
                 "issue": c.blocker_reason, "raw": c.cleaned_text}
            )
        elif c.resolution_status == "needs_review":
            needs_review.append(
                {"cell": c.cleaned_text, "issue": c.blocker_reason,
                 "module_guess": c.explicit_module or c.inferred_module}
            )
        elif c.resolution_status == "new_number_assigned" and c.resolved_item_number:
            allocated.setdefault(c.resolved_item_number, {
                "number": c.resolved_item_number, "name": c.resolved_item_name,
                "module": c.explicit_module or c.inferred_module,
                "confidence": _confidence(c)})
        elif c.resolved_item_number:  # matched_by_number / matched_by_name
            pre_numbered.setdefault(c.resolved_item_number, {
                "number": c.resolved_item_number, "name": c.resolved_item_name})
    return {
        "allocated": list(allocated.values()),
        "pre_numbered": list(pre_numbered.values()),
        "conflicts": conflicts,
        "needs_review": needs_review,
        "counts": {
            "allocated": len(allocated),
            "pre_numbered": len(pre_numbered),
            "unique_items": len(allocated) + len(pre_numbered),
            "conflicts": len(conflicts),
            "needs_review": len(needs_review),
        },
    }


def ingest_opml_file(
    db: Session,
    path: Path,
    *,
    uploaded_by: str | None = None,
    notes: str | None = None,
    is_top_level: bool = False,
    batch_id: str | None = None,
    auto_apply: bool = True,
) -> dict:
    """Parse an OPML, record an upload_batch, and (if clean) apply it. Commits."""
    path = Path(path)
    batch_id = batch_id or f"ub-{uuid.uuid4().hex[:10]}"
    cells, _authority = parse_opml(db, path)
    diff = build_diff_summary(cells)
    blockers = [c for c in cells if c.resolution_status in BLOCKER_STATUSES]

    batch = UploadBatch(
        id=batch_id,
        source_filename=path.name,
        uploaded_by=uploaded_by,
        notes=notes,
        is_top_level_bom=is_top_level,
        status="pending_review",
        summary_json=diff,
    )
    db.add(batch)

    applied = None
    if auto_apply and not blockers:
        applied = apply_cells(
            db, cells, batch_id=batch_id, user=uploaded_by, mark_top_level=is_top_level
        )
        batch.status = "approved"

    db.commit()
    return {
        "batch_id": batch_id,
        "status": batch.status,
        "diff": diff,
        "blockers": len(blockers),
        "applied": asdict(applied) if applied else None,
    }


# ── M5: incremental diff (re-import vs current DB) ────────────────────────────

def _opml_links(cells: list[ParsedCell]) -> dict[tuple[str, str], dict]:
    """Parent→child edges the OPML asserts, keyed (parent, child)."""
    links: dict[tuple[str, str], dict] = {}
    for c in cells:
        parent, child = c.parent_resolved_item_number, c.resolved_item_number
        if parent and child and parent != child:
            links.setdefault((parent, child), {"qty": c.quantity, "comment": c.comment})
    return links


def _merge_key(text: str) -> str:
    """Light-normalize an OPML node's text for merge comparison (drop qty/comment)."""
    t = text.split("//")[0]
    t = re.sub(r"#\s*\d+\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def detect_merges_from_opml(path: Path) -> list[dict]:
    """Flag assemblies whose SAME node text appears with genuinely different children.

    Works on the raw OPML tree (not the flattened cell model, which spreads a node's
    children across many rows and over-flags). Catches the clearest merges — e.g. two
    'A: Fan' nodes with different child sets that resolve to one item_id. (It can miss a
    merge where one occurrence is explicitly numbered and another isn't — see backlog.)
    """
    body = ET.parse(path).getroot().find("body")
    if body is None:
        return []
    occ: dict[str, set[frozenset]] = defaultdict(set)
    label: dict[str, str] = {}

    def visit(node):
        for child in node:
            if child.tag != "outline":
                continue
            key = _merge_key(child.attrib.get("text", ""))
            kids = frozenset(
                _merge_key(g.attrib.get("text", ""))
                for g in child if g.tag == "outline" and _merge_key(g.attrib.get("text", ""))
            )
            if key and kids:
                occ[key].add(kids)
                label[key] = re.sub(r"\s+", " ", child.attrib.get("text", "")).strip()
            visit(child)

    visit(body)
    return [
        {"name": label[key], "child_variants": [sorted(v) for v in variants]}
        for key, variants in occ.items()
        if len(variants) > 1
    ]


def build_incremental_diff(
    db: Session, cells: list[ParsedCell], opml_path: Path | None = None
) -> dict:
    """Compare resolved cells against the live DB: new / renamed / structural / blockers."""
    items: dict[str, ParsedCell] = {}
    for c in cells:
        if c.resolved_item_number and c.resolved_item_name:
            items.setdefault(c.resolved_item_number, c)

    new_parts, renamed, unchanged_items = [], [], 0
    for num, c in items.items():
        existing = db.get(Item, num)
        if existing is None:
            new_parts.append({
                "number": num, "name": c.resolved_item_name,
                "module": c.explicit_module or c.inferred_module,
                "allocated": c.resolution_status == "new_number_assigned",
                "confidence": _confidence(c),
            })
        elif normalize_item_name(existing.item_name) != normalize_item_name(c.resolved_item_name):
            renamed.append({"number": num, "from": existing.item_name, "to": c.resolved_item_name})
        else:
            unchanged_items += 1

    opml_links = _opml_links(cells)
    opml_parents = {p for (p, _ch) in opml_links}
    db_links = {
        (bl.parent_item_id, bl.child_item_id): bl.quantity
        for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars()
    }
    added, removed, qty_changed = [], [], []
    for (p, ch), info in opml_links.items():
        if (p, ch) not in db_links:
            added.append({"parent": p, "child": ch, "qty": info["qty"]})
        elif db_links[(p, ch)] != info["qty"]:
            qty_changed.append({"parent": p, "child": ch, "from": db_links[(p, ch)], "to": info["qty"]})
    for (p, ch), q in db_links.items():
        if p in opml_parents and (p, ch) not in opml_links:
            removed.append({"parent": p, "child": ch, "qty": q})

    conflicts = [
        {"number": c.resolved_item_number, "name": c.normalized_item_name,
         "issue": c.blocker_reason, "raw": c.cleaned_text}
        for c in cells if c.resolution_status == "conflict" or c.blocker_reason == "conflict"
    ]
    needs_review = [
        {"cell": c.cleaned_text, "issue": c.blocker_reason,
         "module_guess": c.explicit_module or c.inferred_module}
        for c in cells if c.resolution_status == "needs_review"
    ]
    merges = detect_merges_from_opml(opml_path) if opml_path else []

    return {
        "new_parts": new_parts,
        "renamed": renamed,
        "structural": {"added": added, "removed": removed, "qty_changed": qty_changed},
        "conflicts": conflicts,
        "needs_review": needs_review,
        "merges": merges,
        "counts": {
            "new_parts": len(new_parts), "renamed": len(renamed), "unchanged": unchanged_items,
            "added": len(added), "removed": len(removed), "qty_changed": len(qty_changed),
            "conflicts": len(conflicts), "needs_review": len(needs_review), "merges": len(merges),
        },
    }


def apply_incremental(
    db: Session, cells: list[ParsedCell], *, batch_id: str, user: str | None, mark_top_level: bool
) -> dict:
    """Apply a re-import: create new items, rename changed ones, reconcile links."""
    roots = _top_level_numbers(cells) if mark_top_level else set()
    result = {"items_created": 0, "items_renamed": 0,
              "links_added": 0, "links_removed": 0, "qty_changed": 0}

    items: dict[str, ParsedCell] = {}
    for c in cells:
        if c.resolved_item_number and c.resolved_item_name:
            items.setdefault(c.resolved_item_number, c)

    for num, c in items.items():
        existing = db.get(Item, num)
        if existing is None:
            db.add(Item(
                item_id=num, item_name=c.resolved_item_name, item_type=_item_type_of(c),
                module_code=_module_of(c), is_top_level=num in roots,
                created_by=user, updated_by=user,
            ))
            record_change(db, entity_type="item", entity_id=num, change_type="create",
                          new_value=c.resolved_item_name, changed_by=user, change_reason=f"import {batch_id}")
            result["items_created"] += 1
        elif normalize_item_name(existing.item_name) != normalize_item_name(c.resolved_item_name):
            old = existing.item_name
            existing.item_name = c.resolved_item_name
            existing.updated_by = user
            record_change(db, entity_type="item", entity_id=num, change_type="update",
                          field_changed="item_name", old_value=old, new_value=c.resolved_item_name,
                          changed_by=user, change_reason=f"import {batch_id}")
            result["items_renamed"] += 1
    db.flush()

    opml_links = _opml_links(cells)
    opml_parents = {p for (p, _ch) in opml_links}
    all_links = {
        (bl.parent_item_id, bl.child_item_id): bl
        for bl in db.execute(select(BomLink)).scalars()
    }
    for (p, ch), info in opml_links.items():
        bl = all_links.get((p, ch))
        if bl is None:
            db.add(BomLink(parent_item_id=p, child_item_id=ch, quantity=info["qty"], comment=info["comment"]))
            record_change(db, entity_type="bom_link", entity_id=f"{p}>{ch}", change_type="create",
                          new_value=f"qty={info['qty']}", changed_by=user, change_reason=f"import {batch_id}")
            result["links_added"] += 1
        else:
            if bl.archived:  # a previously-removed link returns → restore
                bl.archived = False
                result["links_added"] += 1
            if bl.quantity != info["qty"]:
                old = bl.quantity
                bl.quantity = info["qty"]
                record_change(db, entity_type="bom_link", entity_id=f"{p}>{ch}", change_type="update",
                              field_changed="quantity", old_value=old, new_value=info["qty"],
                              changed_by=user, change_reason=f"import {batch_id}")
                result["qty_changed"] += 1
    for (p, ch), bl in all_links.items():
        if not bl.archived and p in opml_parents and (p, ch) not in opml_links:
            bl.archived = True  # soft-delete, not hard-delete
            record_change(db, entity_type="bom_link", entity_id=f"{p}>{ch}", change_type="remove",
                          field_changed="archived", old_value=f"qty={bl.quantity}", new_value=True,
                          changed_by=user, change_reason=f"removed by import {batch_id}")
            result["links_removed"] += 1

    return result
