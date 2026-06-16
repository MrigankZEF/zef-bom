"""Cross-cutting item operations: atomic item-id rename + the part→assembly rule.

The naming rule: anything with children is an assembly (item_type ``assembly`` and a
code ending in ``A``). A leaf is a part (``P``). When a part gains children we rename
its code ``…P → …A`` and retype it — atomically repointing every table that references
the old id, so nothing breaks.
"""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .bom_ingest.miro_csv_fix import ITEM_NUMBER_RE
from .history import record_change
from .models import BomLink, ChangeHistory, CostEvidence, DecidedCost, FieldValue, Item

UNIVERSAL = "UN"            # the module a shared (multi-system) part collapses to
UNIVERSALS = {"UN", "UNP"}  # "universal" modules — pinned; never auto-re-coded to a system


def assembly_code(item_id: str) -> str:
    """``AEC065P`` → ``AEC065A``. Leaves a non-P code unchanged."""
    if item_id and item_id[-1:].upper() == "P":
        return item_id[:-1] + "A"
    return item_id


def rename_item(
    db: Session, old_id: str, new_id: str, *, user: str | None, reason: str, new_type: str | None = None
) -> str:
    """Rename an item's id (and optionally its type), repointing all references in one
    transaction. Caller commits. Returns the resulting id."""
    old = db.get(Item, old_id)
    if old is None:
        raise ValueError(f"{old_id} not found")

    if old_id == new_id:
        if new_type and old.item_type != new_type:
            old.item_type = new_type
            old.updated_by = user
        return old_id

    if db.get(Item, new_id) is not None:
        raise ValueError(f"{new_id} already exists — cannot rename {old_id}")

    # 1) clone the row under the new id (copy every column), then flush so FKs resolve
    data = {c.name: getattr(old, c.name) for c in Item.__table__.columns}
    data["item_id"] = new_id
    data["updated_by"] = user
    if new_type:
        data["item_type"] = new_type
    _m = ITEM_NUMBER_RE.match(new_id)
    if _m:  # keep module_code in sync with the new code (e.g. AEC065P → UN042P sets module UN)
        data["module_code"] = _m.group("module")
    db.add(Item(**data))
    db.flush()

    # 2) repoint every reference old_id → new_id
    db.execute(update(BomLink).where(BomLink.parent_item_id == old_id).values(parent_item_id=new_id))
    db.execute(update(BomLink).where(BomLink.child_item_id == old_id).values(child_item_id=new_id))
    db.execute(update(CostEvidence).where(CostEvidence.item_id == old_id).values(item_id=new_id))
    db.execute(update(DecidedCost).where(DecidedCost.item_id == old_id).values(item_id=new_id))
    db.execute(update(FieldValue).where(FieldValue.item_id == old_id).values(item_id=new_id))
    db.execute(
        update(ChangeHistory)
        .where(ChangeHistory.entity_type == "item", ChangeHistory.entity_id == old_id)
        .values(entity_id=new_id)
    )

    # 3) drop the old row and log the rename
    db.delete(old)
    db.flush()
    record_change(
        db, entity_type="item", entity_id=new_id, change_type="update",
        field_changed="item_id", old_value=old_id, new_value=new_id,
        changed_by=user, change_reason=reason,
    )
    return new_id


def promote_to_assembly(db: Session, item_id: str, *, user: str | None) -> str:
    """A part has gained children → make it an assembly: rename ``…P → …A`` (unless that
    code is taken, in which case keep the id) and set type ``assembly``. Returns new id."""
    target = assembly_code(item_id)
    if target != item_id and db.get(Item, target) is not None:
        target = item_id  # A-code already used → keep the id, just retype
    return rename_item(
        db, item_id, target, user=user,
        reason="part → assembly (has children)", new_type="assembly",
    )


def part_code(item_id: str) -> str:
    """``AEC065A`` → ``AEC065P``. Leaves a non-A code unchanged."""
    if item_id and item_id[-1:].upper() == "A":
        return item_id[:-1] + "P"
    return item_id


def demote_to_part(db: Session, item_id: str, *, user: str | None) -> str:
    """An assembly lost all its children → make it a part: rename ``…A → …P`` and set type."""
    target = part_code(item_id)
    if target != item_id and db.get(Item, target) is not None:
        target = item_id  # P-code taken → keep id, just retype
    return rename_item(
        db, item_id, target, user=user,
        reason="assembly → part (no children)", new_type="part",
    )


def _has_children(db: Session, item_id: str) -> bool:
    """True if the item has at least one live child (link AND child item both un-archived)."""
    return db.execute(
        select(BomLink.id)
        .join(Item, Item.item_id == BomLink.child_item_id)
        .where(BomLink.parent_item_id == item_id, BomLink.archived.is_(False), Item.archived.is_(False))
    ).first() is not None


def normalize_type(db: Session, item_id: str, *, user: str | None) -> str:
    """Keep an item's type/suffix in step with whether it has children: with children →
    assembly (…A); without → part (…P). No-op for roots/archived."""
    it = db.get(Item, item_id)
    if it is None or it.is_top_level or it.archived:
        return item_id
    suffix = item_id[-1:].upper()
    if _has_children(db, item_id):
        if it.item_type != "assembly" or suffix == "P":
            return promote_to_assembly(db, item_id, user=user)
    else:
        if it.item_type != "part" or suffix == "A":
            return demote_to_part(db, item_id, user=user)
    return item_id


def allocate_code(db: Session, module: str, suffix: str) -> str:
    """Next free code in a module, e.g. ('UN','P') → 'UN042P' (max existing + 1)."""
    mx = 0
    for iid in db.execute(select(Item.item_id)).scalars():
        m = ITEM_NUMBER_RE.match(iid)
        if m and m.group("module") == module:
            mx = max(mx, int(m.group("number")))
    return f"{module}{mx + 1:03d}{suffix}"


def allowed_modules(db: Session, item_id: str) -> list[str]:
    """Modules a user may manually assign to this item.

    * A top-level BOM (root) is a *system* — it may be any system code (so you can switch
      AEC ↔ DAC and never get locked in), plus its current code. Not UN/UNP.
    * A non-root part/assembly may be UN, UNP, or its parent assembly's system — which is
      what stops e.g. a DAC part landing inside an AEC assembly.
    """
    from .bom_ingest.miro_csv_fix import DEFAULT_MODULE_CODES

    it = db.get(Item, item_id)
    cur = it.module_code if it else None
    if it and it.is_top_level:
        skip = UNIVERSALS | {"POW"}  # POW is deprecated to UNP — don't offer it for a root
        systems = {m for m in DEFAULT_MODULE_CODES if m not in skip}
        for x in db.execute(select(Item.module_code)).scalars():
            if x and x not in skip:
                systems.add(x)
        if cur:
            systems.add(cur)
        return sorted(systems)

    mods = set(UNIVERSALS)
    if cur:
        mods.add(cur)
    # Immediate parent assembly's module(s)…
    for bl in db.execute(
        select(BomLink).where(BomLink.child_item_id == item_id, BomLink.archived.is_(False))
    ).scalars():
        p = db.get(Item, bl.parent_item_id)
        if p and not p.archived and p.module_code:
            mods.add(p.module_code)
    # …plus the system(s) of the top-level BOM(s) the part lives under, so a part inside a
    # UNP sub-assembly can still be set back to its own system (e.g. AEC), not just UN/UNP.
    mods |= containing_root_modules(db, item_id)
    return sorted(mods, key=lambda m: (m not in UNIVERSALS, m))


def set_module(db: Session, item_id: str, module: str, *, user: str | None) -> str:
    """Manually change an item's module (the code's letter part), atomically re-coding it.
    Keeps the same number when free, else allocates a fresh one in the new module so there's
    no overlap. Validates against `allowed_modules`. Returns the new id."""
    it = db.get(Item, item_id)
    if it is None:
        raise ValueError(f"Item {item_id} not found")
    module = (module or "").strip().upper()
    m = ITEM_NUMBER_RE.match(item_id)
    if not m:
        raise ValueError(f"{item_id} isn't a standard code, so its module can't be changed here")
    if module == m.group("module"):
        return item_id  # no change
    if module not in allowed_modules(db, item_id):
        raise ValueError(
            f"Module '{module}' isn't allowed here — choose from {allowed_modules(db, item_id)} "
            "(a part may only take a universal code or its parent assembly's system)."
        )
    was_top_level = bool(it.is_top_level)
    suffix = m.group("suffix")
    same_number = f"{module}{m.group('number')}{suffix}"
    new_id = same_number if db.get(Item, same_number) is None else allocate_code(db, module, suffix)
    new_id = rename_item(
        db, item_id, new_id, user=user,
        reason=f"module {m.group('module')}→{module} (manual edit)",
    )
    # Changing a top-level BOM's system cascades to everything inside it: every part that
    # belongs only to this system re-codes to the new one (UN/UNP stay universal).
    if was_top_level:
        recode_all(db, user=user)
    return new_id


def containing_root_modules(db: Session, item_id: str) -> set[str]:
    """The set of distinct top-level-BOM modules an item sits under (its 'systems').
    Walks up the bom_links to every reachable top-level root."""
    items = {it.item_id: it for it in db.execute(select(Item).where(Item.archived.is_(False))).scalars()}
    parents: dict[str, list[str]] = {}
    for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars():
        parents.setdefault(bl.child_item_id, []).append(bl.parent_item_id)
    mods: set[str] = set()
    seen: set[str] = set()
    stack = [item_id]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        it = items.get(cur)
        if it is None:
            continue
        if it.is_top_level and it.module_code:
            mods.add(it.module_code)
        stack.extend(parents.get(cur, []))
    return mods


def would_cycle(db: Session, parent_id: str, child_id: str) -> bool:
    """True if linking parent→child would create a loop — i.e. child is the parent itself
    or an ancestor of it (parent already sits somewhere inside child's sub-tree)."""
    if parent_id == child_id:
        return True
    children: dict[str, list[str]] = {}
    for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars():
        children.setdefault(bl.parent_item_id, []).append(bl.child_item_id)
    seen: set[str] = set()
    stack = [child_id]
    while stack:
        cur = stack.pop()
        if cur == parent_id:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(children.get(cur, []))
    return False


def recode_item(db: Session, item_id: str, *, user: str | None) -> str:
    """Re-code an item to match its usage: a single system → that module's code; used in
    two or more systems → UN. Renames (atomically) only if the module should change.
    No-op for roots, archived, catalog-only (unused), or non-standard codes."""
    it = db.get(Item, item_id)
    if it is None or it.is_top_level or it.archived:
        return item_id
    m = ITEM_NUMBER_RE.match(item_id)
    if not m:
        return item_id
    # UN / UNP are "universal" — deliberately shared parts. Once universal, the code stays
    # (a multi-system part that became UN never reverts; a manual/marker UNP is never moved).
    if m.group("module") in UNIVERSALS:
        return item_id
    mods = containing_root_modules(db, item_id)
    if not mods:
        return item_id  # not used in any BOM → leave its catalog code alone
    desired = next(iter(mods)) if len(mods) == 1 else UNIVERSAL
    if m.group("module") == desired:
        return item_id
    new_id = allocate_code(db, desired, m.group("suffix"))
    return rename_item(
        db, item_id, new_id, user=user,
        reason=f"re-code {m.group('module')}→{desired} (used in {sorted(mods)})",
    )


def recode_all(db: Session, *, user: str | None) -> list[dict]:
    """Re-code every non-root item to match its usage. Only items whose module is wrong
    actually change (others are no-ops), so it both fixes new imports and cleans up
    pre-existing cross-system mistakes."""
    linked: set[str] = set()
    for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars():
        linked.add(bl.parent_item_id)
        linked.add(bl.child_item_id)
    ids = [
        it.item_id
        for it in db.execute(
            select(Item).where(Item.archived.is_(False), Item.is_top_level.is_(False))
        ).scalars()
        if it.item_id in linked  # catalog-only items have no usage → nothing to re-code
    ]
    changes: list[dict] = []
    for iid in ids:
        new = recode_item(db, iid, user=user)
        if new != iid:
            changes.append({"from": iid, "to": new})
    return changes


def normalize_structure(db: Session, *, user: str | None) -> list[dict]:
    """Bring the whole BOM into naming consistency after a structural change:
    pass 1 — every item's TYPE matches whether it has children (assembly ↔ part);
    pass 2 — every item's MODULE matches its usage (single system, or UN).
    Returns the ordered list of {from, to} renames."""
    changes: list[dict] = []
    snapshot = [
        it.item_id for it in db.execute(
            select(Item).where(Item.archived.is_(False), Item.is_top_level.is_(False))
        ).scalars()
    ]
    for iid in snapshot:
        new = normalize_type(db, iid, user=user)
        if new != iid:
            changes.append({"from": iid, "to": new})
    changes.extend(recode_all(db, user=user))
    return changes


def resolve_rename(changes: list[dict], old_id: str) -> str:
    """Follow a chain of {from,to} renames to the final id (A→B then B→C ⇒ A resolves to C)."""
    nxt = {c["from"]: c["to"] for c in changes}
    cur, seen = old_id, set()
    while cur in nxt and cur not in seen:
        seen.add(cur)
        cur = nxt[cur]
    return cur


def enforce_assembly_rule(db: Session, *, user: str | None) -> list[dict]:
    """Promote every item that has (non-archived) children but isn't a proper assembly.
    Runs after an import so violations never persist. Returns the conversions made."""
    parent_ids = {
        bl.parent_item_id
        for bl in db.execute(select(BomLink).where(BomLink.archived.is_(False))).scalars()
    }
    changes: list[dict] = []
    for pid in parent_ids:
        it = db.get(Item, pid)
        if it is None or it.archived:
            continue
        if it.item_type != "assembly" or it.item_id[-1:].upper() == "P":
            new_id = promote_to_assembly(db, it.item_id, user=user)
            changes.append({"from": pid, "to": new_id})
    return changes
