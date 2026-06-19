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
    augmented = InventoryAuthority(authority.items + list(extra.values()), pd.DataFrame())
    # Carry the known-module set forward (a fresh InventoryAuthority resets it to the seed set),
    # plus any module the OPML's own numbers introduced, so name-anchoring stays consistent.
    augmented.module_codes = set(authority.module_codes) | {
        it.module_code for it in extra.values() if it.module_code
    }
    return augmented


# Module markers people write into Miro names: "<CODE>: Pow: Name" or "<CODE>: UN: Name".
# POW is deprecated to UNP. A "Pow:" assembly is a power sub-tree: it AND its descendants
# become UNP — EXCEPT (a) universals (UN/UNP) stay, and (b) a descendant whose name *starts
# with a system word* (AEC/DAC/…) keeps its system, and stops the UNP from flowing deeper.
# We rewrite each affected cell to the module-typed form "<MODULE> <SUFFIX>: Name" so the
# normal parser assigns the module and drops any marker from the name.
_NAME_MARKER_RE = re.compile(r"^(?:([A-Z]{2,5}\d{3,}[PA])\s*:\s*)?(POW|UNP|UN)\s*:\s*(.+)$", re.IGNORECASE)
_NAME_MARKER_MAP = {"POW": "UNP", "UNP": "UNP", "UN": "UN"}
_CODE_RE = re.compile(r"^([A-Z]{2,5}\d{3,}[PA])\s*:\s*(.+)$")
_UNIVERSAL_CODES = {"UN", "UNP"}


def _add_admin_modules(db: Session, authority: InventoryAuthority) -> None:
    """Fold admin-registered module codes (Admin → Reference data → Modules) into the known
    set, so a brand-new system word (added before any part uses it) still anchors a BOM."""
    from ..models import ReferenceValue
    rows = db.execute(
        select(ReferenceValue.value).where(
            ReferenceValue.category == "module", ReferenceValue.archived.is_(False)
        )
    ).scalars()
    for v in rows:
        code = (v or "").strip().upper()
        if re.fullmatch(r"[A-Z]{2,5}", code):
            authority.module_codes.add(code)


def _name_starts_with_system(name: str) -> bool:
    from .miro_csv_fix import DEFAULT_MODULE_CODES
    systems = sorted((m for m in DEFAULT_MODULE_CODES if m not in _UNIVERSAL_CODES and m != "POW"),
                     key=len, reverse=True)
    return bool(re.match(r"^(?:" + "|".join(systems) + r")\b", name.strip(), re.IGNORECASE))


def _cell_module(text: str):
    """(module, suffix, name) for a 'CODE: name' cell, else (None, None, name)."""
    m = _CODE_RE.match(text.strip())
    if not m:
        return None, None, text.strip()
    code = m.group(1)
    from .miro_csv_fix import ITEM_NUMBER_RE
    return ITEM_NUMBER_RE.match(code).group("module"), code[-1], m.group(2).strip()


def _apply_name_markers(df):
    cols = list(df.columns)
    for ridx in df.index:
        forced = None  # "UNP" while inside a Pow sub-tree (until a system-named node)
        for cidx, col in enumerate(cols):
            v = df.at[ridx, col]
            if not isinstance(v, str) or not v.strip():
                continue
            has_child = (cidx + 1 < len(cols)) and isinstance(df.at[ridx, cols[cidx + 1]], str) and bool(str(df.at[ridx, cols[cidx + 1]]).strip())

            mk = _NAME_MARKER_RE.match(v.strip())
            if mk:  # explicit Pow:/UN: marker on this node
                module = _NAME_MARKER_MAP[mk.group(2).upper()]
                suffix = (mk.group(1)[-1].upper() if mk.group(1) else ("A" if has_child else "P"))
                df.at[ridx, col] = f"{module} {suffix}: {mk.group(3).strip()}"
                forced = "UNP" if module == "UNP" else None  # POW flows down; UN: does not
                continue

            # Bare space-prefix power marker, e.g. "POW wire" (no colon). POW is deprecated to
            # UNP and, like the colon form, opens a power sub-tree that flows UNP downward.
            pw = re.match(r"^POW\s+(.+)$", v.strip(), re.IGNORECASE)
            if pw:
                sfx = "A" if has_child else "P"
                df.at[ridx, col] = f"UNP {sfx}: {pw.group(1).strip()}"
                forced = "UNP"
                continue

            mod, suffix, name = _cell_module(v)
            if mod in _UNIVERSAL_CODES:
                continue  # universal part stays; UNP keeps flowing past it
            if forced == "UNP":
                if _name_starts_with_system(name):
                    forced = None  # system-named node keeps its system and halts the flow
                    continue
                sfx = suffix or ("A" if has_child else "P")
                df.at[ridx, col] = f"UNP {sfx}: {name}"
    return df


def _name_match_overrides(
    cells: list[ParsedCell], catalog: InventoryAuthority,
    name_match_decisions: dict[str, str] | None = None,
) -> tuple[dict[str, ReviewOverride], list[dict]]:
    """Re-point numbered nodes whose number is wrong but whose NAME uniquely identifies
    an existing catalog item — untangling a catalog↔Miro numbering drift.

    Run against the *pure catalog* authority (before augmenting with the OPML's own
    numbers). A numbered node that the catalog can't cleanly match by number is either:
      * a conflict — its number exists under a different name, or
      * unknown    — its number isn't in the catalog at all.
    In both cases, if the node's name uniquely matches one catalog item, the DEFAULT is to
    adopt that item's number (merge — the name is the stable identity, no duplicate created).
    Nodes whose name matches nothing (or matches ambiguously) are left alone — they flow
    through as genuinely new / needs-review.

    Per-row override: `name_match_decisions` is {miro_number: "merge"|"new"}. "new" means the
    user has decided this is genuinely a *different* part that merely shares a name, so it is
    created fresh (its Miro module's next free code) instead of merging. Default is "merge".

    Returns (overrides keyed by cell_key, deduped list of {from, to, name} matches) — the
    matches list always carries every name-match (regardless of decision) so the review UI
    can show each with a merge/create-new toggle.
    """
    name_match_decisions = name_match_decisions or {}
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
        decision = str(name_match_decisions.get(c.explicit_item_number, "merge")).strip().lower()
        if decision == "new":
            # Create a brand-new part: keep the Miro name, allocate a fresh code in the Miro
            # module (the catalog item that shares the name is left untouched).
            overrides[c.cell_key] = ReviewOverride(review_decision="create_new")
        else:
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


def _defer_undetermined_subtrees(cells: list[ParsedCell]) -> None:
    """When a tree's ROOT can't be anchored to a system (no code, no recognised system word in
    its name), ask about the root alone — not every node beneath it.

    The naming rule is "anchor on the top-most assembly, then flow its system down". So if the
    root is undetermined, its bare-named descendants have nothing to inherit *yet* and the
    engine would otherwise flag each one. Instead we keep the root as the single
    needs-review item ("which system is this?") and mark its bare descendants `deferred`
    (non-blocking) — once the user picks the root's system, a re-parse flows it down and they
    all resolve. Determined-root trees are untouched; a descendant blocked for a *real* reason
    (its own number collides, ambiguous type, …) still surfaces.
    """
    by_row: dict[int, list[ParsedCell]] = defaultdict(list)
    for c in cells:
        by_row[c.row_index].append(c)
    for row_cells in by_row.values():
        root = next((c for c in row_cells if c.parent_key is None), None)
        if root is None or root.blocker_reason != "ambiguous_module":
            continue
        for c in row_cells:
            if c is root:
                continue  # the root stays the one thing we ask about
            # Defer everything that is blocked *only because the root is undetermined*: bare
            # nodes with no module (ambiguous_module) and anything orphaned by a deferred parent
            # (missing_parent cascades down). A genuine catalog discrepancy (conflict) is left
            # visible so it can still be resolved alongside the root question. Null the resolved
            # fields so deferred cells are ignored by the diff/apply until the system is chosen.
            if c.blocker_reason in ("ambiguous_module", "missing_parent"):
                c.resolution_status = "deferred"
                c.blocker_reason = None
                c.action = "defer"
                c.resolved_item_number = None
                c.resolved_item_name = None


def _strip_numbers_for_variant(df):
    """For a *variant* import, drop the explicit part numbers from every coded cell, keeping the
    module + type — `AEC066A: Pump` → `AEC A: Pump`. A variant is a separate BOM that must get
    its *own* fresh codes (it must not match the original by number), but the system (module) is
    still needed to anchor and code the variant, so we keep that and the part/assembly type."""
    for ridx in df.index:
        for col in df.columns:
            v = df.at[ridx, col]
            if not isinstance(v, str) or not v.strip():
                continue
            mod, sfx, name = _cell_module(v)
            if mod and sfx:
                df.at[ridx, col] = f"{mod} {sfx}: {name}"
    return df


def _variant_overrides(
    cells: list[ParsedCell], already: dict[str, ReviewOverride]
) -> dict[str, ReviewOverride]:
    """Force every *system* node of a variant to be created fresh (a new code, same name), so the
    variant coexists as a distinct BOM instead of merging into the original. Universals (UN/UNP)
    are deliberately left to match by name — a universal screw is the *same* screw across BOMs and
    variants, so it's shared, never duplicated. Cells the user already decided (a review/decision)
    keep that decision."""
    extra = dict(already)
    for c in cells:
        if c.cell_key in extra:
            continue
        module = (c.explicit_module or c.inferred_module or "").upper()
        if module in _UNIVERSAL_CODES:
            continue  # shared commodity — match/reuse by name
        extra[c.cell_key] = ReviewOverride(review_decision="create_new")
    return extra


def parse_opml(
    db: Session, path: Path,
    decisions: dict[str, str] | None = None,
    reviews: dict[str, dict] | None = None,
    name_match_decisions: dict[str, str] | None = None,
    variant: bool = False,
) -> tuple[list[ParsedCell], InventoryAuthority, list[dict]]:
    """Load + repair an OPML/CSV and resolve every cell against the live items table.

    (1) Resolve against the pure catalog. (2) Reconcile numbering drift by name — re-point
    numbered nodes whose number is wrong but whose name matches a catalog item (per-row the
    user may flip a match from merge to create-new via `name_match_decisions`). (3) Apply any
    per-conflict user `decisions` (new / rename / skip). (4) Seed genuinely-new OPML numbers
    so they resolve as new rather than blocking.

    `variant=True` imports the OPML as a *new, separate BOM* (e.g. prototype v2): system parts
    get fresh codes (keeping names), universals are shared, and the root becomes a new top-level
    tree — the original BOM is left completely untouched.

    Returns (cells, authority, name_matches) where name_matches lists {from, to, name} for
    nodes re-pointed to an existing catalog item by name.
    """
    decisions = decisions or {}
    reviews = reviews or {}
    df_raw, input_format = load_bom_input(path)
    df = df_raw if input_format == "opml" else repair_mindmap_tree(df_raw)
    df = _apply_name_markers(df)  # Pow:/UN: name markers → universal module, marker stripped
    if variant:
        df = _strip_numbers_for_variant(df)  # a variant must take its own fresh codes
    catalog = build_authority_from_db(db)
    _add_admin_modules(db, catalog)  # admin-registered modules also count as known system words
    cells = build_parsed_cells(df, catalog)

    overrides, name_matches = _name_match_overrides(cells, catalog, name_match_decisions)
    if decisions:
        overrides = _decision_overrides(cells, overrides, decisions)
    if reviews:
        overrides = _review_overrides(cells, overrides, reviews)
    if variant:
        # Fork system parts last, so any explicit user review/decision still wins.
        overrides = _variant_overrides(cells, overrides)
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

    # Anchor-on-the-top-assembly: if a tree root has no determinable system, ask about the root
    # alone and let its bare children inherit the choice (instead of flagging every node).
    _defer_undetermined_subtrees(cells)
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
    # Deferred = bare nodes waiting on an undetermined top-level system (see
    # _defer_undetermined_subtrees). They aren't asked about individually; they inherit the
    # system the user picks for their root. Count distinct ones to hint in the UI.
    deferred_names = {c.cleaned_text for c in cells if c.resolution_status == "deferred"}
    is_root_text = {c.cleaned_text for c in cells if c.parent_key is None}

    # Items the engine couldn't resolve — surfaced with a stable key (the node text) so the
    # user can resolve each inline (create / match / skip). Deduped by that key. A top-level
    # root with an undetermined system becomes the single "which system?" question whose answer
    # flows down to its deferred children.
    review_map: dict[str, dict] = {}
    for c in cells:
        if c.resolution_status != "needs_review":
            continue
        root_undetermined = c.cleaned_text in is_root_text and c.blocker_reason == "ambiguous_module"
        review_map.setdefault(c.cleaned_text, {
            "key": c.cleaned_text,
            "cell": c.cleaned_text,
            "name": c.normalized_item_name,
            "issue": "which_system" if root_undetermined else c.blocker_reason,
            "is_root": root_undetermined,
            "inherits": len(deferred_names) if root_undetermined else 0,
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
        "deferred": sorted(deferred_names),
        "counts": {
            "new_parts": len(new_parts), "renamed": len(renamed), "unchanged": unchanged_items,
            "name_matched": len(name_matched_list),
            "added": len(added), "removed": len(removed), "qty_changed": len(qty_changed),
            "conflicts": len(conflicts), "needs_review": len(needs_review), "merges": len(merges),
            "deferred": len(deferred_names),
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
