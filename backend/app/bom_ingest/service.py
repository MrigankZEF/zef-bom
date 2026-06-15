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
    ReviewOverride,
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


def _name_match_overrides(
    cells: list[ParsedCell], catalog: InventoryAuthority
) -> tuple[dict[str, ReviewOverride], list[dict]]:
    """Re-point numbered nodes whose number is wrong but whose NAME uniquely identifies
    an existing catalog item — untangling a catalog↔Miro numbering drift.

    Run against the *pure catalog* authority (before augmenting with the OPML's own
    numbers). A numbered node that the catalog can't cleanly match by number is either:
      * a conflict — its number exists under a different name, or
      * unknown    — its number isn't in the catalog at all.
    In both cases, if the node's name uniquely matches one catalog item, we adopt that
    item's number (the name is the stable identity). Nodes whose name matches nothing (or
    matches ambiguously) are left alone — they flow through as genuinely new / needs-review.

    Returns (overrides keyed by cell_key, deduped list of {from, to, name} matches).
    """
    overrides: dict[str, ReviewOverride] = {}
    matches: dict[tuple[str, str], dict] = {}
    for c in cells:
        if not c.explicit_item_number or not c.normalized_item_name:
            continue
        # Skip nodes the catalog already matched cleanly by their own number.
        if c.resolution_status == "matched_by_number":
            continue
        is_blocked = c.resolution_status in ("conflict", "needs_review") or c.blocker_reason == "conflict"
        if not is_blocked:
            continue
        module = c.explicit_module or c.inferred_module
        suffix = c.explicit_suffix or c.inferred_suffix
        cand = catalog.lookup_exact(c.normalized_item_name, module, suffix) if (module and suffix) else []
        if len(cand) != 1:
            by_name = catalog.lookup_by_name(c.normalized_item_name)
            cand = by_name if len(by_name) == 1 else []
        if len(cand) != 1 or cand[0].partnumber == c.explicit_item_number:
            continue
        target = cand[0]
        overrides[c.cell_key] = ReviewOverride(
            review_decision="match_existing", approved_item_number=target.partnumber
        )
        matches.setdefault(
            (c.explicit_item_number, target.partnumber),
            {"from": c.explicit_item_number, "to": target.partnumber, "name": target.partname},
        )
    return overrides, list(matches.values())


def _decision_overrides(
    cells: list[ParsedCell], already: dict[str, ReviewOverride], decisions: dict[str, str]
) -> dict[str, ReviewOverride]:
    """Turn the user's per-conflict choices into per-cell overrides.

    `decisions` is {explicit_number: "new" | "rename" | "skip"}, applied to the cells that
    are STILL a conflict after auto name-matching (i.e. a number reused for a different part
    whose name the catalog doesn't know):
      * new    — create a brand-new part (a fresh number is allocated; the catalog item that
                 owns the reused number is left untouched).
      * rename — keep the reused number and update that catalog item's name to Miro's.
      * skip   — ignore this node entirely.
    """
    extra = dict(already)
    for c in cells:
        num = c.explicit_item_number
        if not num or num not in decisions or c.cell_key in extra:
            continue
        if not (c.resolution_status in ("conflict", "needs_review") or c.blocker_reason == "conflict"):
            continue
        action = decisions[num]
        if action == "new":
            extra[c.cell_key] = ReviewOverride(review_decision="create_new")
        elif action == "skip":
            extra[c.cell_key] = ReviewOverride(review_decision="skip")
        elif action == "rename":
            extra[c.cell_key] = ReviewOverride(review_decision="match_existing")
    return extra


def _review_overrides(
    cells: list[ParsedCell], already: dict[str, ReviewOverride], reviews: dict[str, dict]
) -> dict[str, ReviewOverride]:
    """Turn the user's per-needs-review choices into per-cell overrides.

    `reviews` is keyed by the node's text {cell_text: {action, module, type, match}}:
      * create — make a new part in the chosen module + type (a number is allocated).
      * match  — point this node at an existing catalog code.
      * skip   — ignore this node.
    Applied to cells the engine couldn't resolve on its own (needs_review).
    """
    extra = dict(already)
    for c in cells:
        key = c.cleaned_text
        if key not in reviews or c.cell_key in extra or c.resolution_status != "needs_review":
            continue
        r = reviews[key] or {}
        action = r.get("action")
        if action == "skip":
            extra[c.cell_key] = ReviewOverride(review_decision="skip")
        elif action == "match" and r.get("match"):
            extra[c.cell_key] = ReviewOverride(
                review_decision="match_existing", approved_item_number=str(r["match"]).strip().upper()
            )
        elif action == "create":
            mod = (r.get("module") or "").strip().upper() or None
            suffix = "A" if r.get("type") == "assembly" else "P"
            extra[c.cell_key] = ReviewOverride(
                review_decision="create_new", approved_module=mod, approved_suffix=suffix
            )
    return extra


def parse_opml(
    db: Session, path: Path,
    decisions: dict[str, str] | None = None,
    reviews: dict[str, dict] | None = None,
) -> tuple[list[ParsedCell], InventoryAuthority, list[dict]]:
    """Load + repair an OPML/CSV and resolve every cell against the live items table.

    (1) Resolve against the pure catalog. (2) Reconcile numbering drift by name — re-point
    numbered nodes whose number is wrong but whose name matches a catalog item. (3) Apply any
    per-conflict user `decisions` (new / rename / skip). (4) Seed genuinely-new OPML numbers
    so they resolve as new rather than blocking.

    Returns (cells, authority, name_matches) where name_matches lists {from, to, name} for
    nodes re-pointed to an existing catalog item by name.
    """
    decisions = decisions or {}
    reviews = reviews or {}
    df_raw, input_format = load_bom_input(path)
    df = df_raw if input_format == "opml" else repair_mindmap_tree(df_raw)
    catalog = build_authority_from_db(db)
    cells = build_parsed_cells(df, catalog)

    overrides, name_matches = _name_match_overrides(cells, catalog)
    if decisions:
        overrides = _decision_overrides(cells, overrides, decisions)
    if reviews:
        overrides = _review_overrides(cells, overrides, reviews)
    if overrides:
        cells = build_parsed_cells(df, catalog, overrides)

    authority = catalog
    augmented = _augment_with_opml_numbers(authority, cells)
    if augmented is not authority:
        authority = augmented
        cells = build_parsed_cells(df, authority, overrides)

    # A "rename" choice keeps the reused number but adopts Miro's name → make the rename visible.
    for c in cells:
        num = c.explicit_item_number
        if num and decisions.get(num) == "rename" and c.resolved_item_number == num:
            c.resolved_item_name = c.normalized_item_name
    return cells, authority, name_matches


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
    cells, _authority, _name_matches = parse_opml(db, path)
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
    db: Session, cells: list[ParsedCell], opml_path: Path | None = None,
    name_matches: list[dict] | None = None,
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

    # A conflict = a Miro node whose number belongs to a DIFFERENT catalog part and whose
    # name the catalog doesn't recognise. Keyed by the reused number; show both names so the
    # user can choose rename-vs-new. (Deduped — a number can appear in many tree branches.)
    conflict_map: dict[str, dict] = {}
    for c in cells:
        if c.resolution_status != "conflict" and c.blocker_reason != "conflict":
            continue
        num = c.explicit_item_number
        existing = db.get(Item, num) if num else None
        conflict_map.setdefault(num or c.cleaned_text, {
            "number": num,
            "name": c.normalized_item_name,
            "catalog_name": existing.item_name if existing else None,
            "issue": c.blocker_reason,
            "raw": c.cleaned_text,
        })
    conflicts = list(conflict_map.values())
    # Items the engine couldn't resolve — surfaced with a stable key (the node text) so the
    # user can resolve each inline (create / match / skip). Deduped by that key.
    review_map: dict[str, dict] = {}
    for c in cells:
        if c.resolution_status != "needs_review":
            continue
        review_map.setdefault(c.cleaned_text, {
            "key": c.cleaned_text,
            "cell": c.cleaned_text,
            "name": c.normalized_item_name,
            "issue": c.blocker_reason,
            "module_guess": c.explicit_module or c.inferred_module,
            "type_guess": "assembly" if (c.explicit_suffix or c.inferred_suffix) == "A" else "part",
        })
    needs_review = list(review_map.values())
    merges = detect_merges_from_opml(opml_path) if opml_path else []

    name_matched_list = name_matches or []

    return {
        "new_parts": new_parts,
        "renamed": renamed,
        "name_matched": name_matched_list,
        "structural": {"added": added, "removed": removed, "qty_changed": qty_changed},
        "conflicts": conflicts,
        "needs_review": needs_review,
        "merges": merges,
        "counts": {
            "new_parts": len(new_parts), "renamed": len(renamed), "unchanged": unchanged_items,
            "name_matched": len(name_matched_list),
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
            continue
        if normalize_item_name(existing.item_name) != normalize_item_name(c.resolved_item_name):
            old = existing.item_name
            existing.item_name = c.resolved_item_name
            existing.updated_by = user
            record_change(db, entity_type="item", entity_id=num, change_type="update",
                          field_changed="item_name", old_value=old, new_value=c.resolved_item_name,
                          changed_by=user, change_reason=f"import {batch_id}")
            result["items_renamed"] += 1
        # A top-level import must flag its root(s) even when the item already exists (e.g.
        # seeded from the catalog) — otherwise it never appears as a tree root.
        if num in roots and not existing.is_top_level:
            existing.is_top_level = True
            existing.updated_by = user
            record_change(db, entity_type="item", entity_id=num, change_type="update",
                          field_changed="is_top_level", old_value="False", new_value="True",
                          changed_by=user, change_reason=f"import {batch_id}")
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
