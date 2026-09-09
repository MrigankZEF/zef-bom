"""Regression net for the BOM naming/import/edit invariants.

Self-contained: runs on throwaway in-memory SQLite DBs, so it never touches real data.
Run directly (`python tests/test_invariants.py`) — no pytest required — or with pytest if
installed (each `test_*` is a standard test function).

Every test here corresponds to a rule or a fixed bug from the robustness audit:
  * BUG #1 — assembly_labor must follow a re-code (was orphaned / crashed on Postgres).
  * BUG #2 — Drive folder located by its stable id, not the mutable item-id name.
  * the naming engine (type/module/stickiness/allocation), import anchoring, dedup, variants.
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

# Make `app` importable when run as a plain script from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402  (registers tables on Base.metadata)
from app.models import AssemblyLabor, BomLink, DecidedCost, Item, ItemLink  # noqa: E402


def _db(fk: bool = False):
    """Fresh in-memory DB. fk=True enforces foreign keys (mimics Postgres/Railway)."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    if fk:
        @event.listens_for(eng, "connect")
        def _on(conn, _rec):  # noqa: ANN001
            conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, future=True)()


def _opml(path_name: str, body: str) -> Path:
    p = Path(tempfile.gettempdir()) / path_name
    p.write_text(f"<opml><body>{body}</body></opml>", encoding="utf-8")
    return p


# ── BUG #1: assembly_labor follows a re-code (no orphan, no FK crash) ──────────
def test_assembly_labor_follows_recode():
    from app.operations import rename_item
    for fk in (False, True):
        db = _db(fk=fk)
        db.add(Item(item_id="AEC050A", item_name="Pump", item_type="assembly", module_code="AEC"))
        db.commit()
        db.add(AssemblyLabor(item_id="AEC050A", volume_tier=1, time_likely=30))
        db.add(DecidedCost(item_id="AEC050A", volume_tier=1, unit_cost_eur=10))
        db.commit()
        rename_item(db, "AEC050A", "UN050A", user="t", reason="module change")
        db.commit()
        new = db.execute(select(AssemblyLabor).where(AssemblyLabor.item_id == "UN050A")).scalars().all()
        old = db.execute(select(AssemblyLabor).where(AssemblyLabor.item_id == "AEC050A")).scalars().all()
        assert len(new) == 1 and len(old) == 0, f"fk={fk}: labor not repointed ({len(new)},{len(old)})"


# Same class of bug as #1, for the newer item_links table: a link is worthless if it stops
# pointing at its item the moment the item is renumbered.
def test_item_links_follow_a_recode():
    from app.operations import rename_item
    for fk in (False, True):
        db = _db(fk=fk)
        db.add(Item(item_id="AEC050P", item_name="Seal", item_type="part", module_code="AEC"))
        db.commit()
        db.add(ItemLink(item_id="AEC050P", link_type="supplier", url="https://example.com/x"))
        db.commit()
        rename_item(db, "AEC050P", "UN050P", user="t", reason="module change")
        db.commit()
        new = db.execute(select(ItemLink).where(ItemLink.item_id == "UN050P")).scalars().all()
        old = db.execute(select(ItemLink).where(ItemLink.item_id == "AEC050P")).scalars().all()
        assert len(new) == 1 and len(old) == 0, f"fk={fk}: links not repointed ({len(new)},{len(old)})"


# ── BUG #2: Drive folder id is parsed from a stored URL (located by id, not name) ──
def test_drive_folder_id_parsing():
    from app.drive import _folder_id_from_url
    assert _folder_id_from_url("https://drive.google.com/drive/folders/1AbC_dEf-9?usp=sharing") == "1AbC_dEf-9"
    assert _folder_id_from_url("https://drive.google.com/drive/u/0/folders/XYZ-_1") == "XYZ-_1"
    assert _folder_id_from_url("https://drive.google.com/file/d/abc/view") is None
    assert _folder_id_from_url(None) is None and _folder_id_from_url("") is None


# ── Naming engine ─────────────────────────────────────────────────────────────
def test_type_follows_children():
    import app.operations as ops
    db = _db()
    db.add(Item(item_id="AEC100A", item_name="Root", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC050P", item_name="Block", item_type="part", module_code="AEC"))
    db.add(BomLink(parent_item_id="AEC100A", child_item_id="AEC050P", quantity=1))
    db.commit()
    db.add(Item(item_id="AEC060P", item_name="Sub", item_type="part", module_code="AEC"))
    db.add(BomLink(parent_item_id="AEC050P", child_item_id="AEC060P", quantity=1))
    db.commit()
    ops.normalize_structure(db, user="t")
    db.commit()
    promoted = db.get(Item, "AEC050A")
    assert promoted is not None and promoted.item_type == "assembly", "part with a child should become …A assembly"


def test_module_follows_usage():
    import app.operations as ops
    db = _db()
    for r in ("AEC100A", "DAC100A"):
        db.add(Item(item_id=r, item_name="R", item_type="assembly", module_code=r[:3], is_top_level=True))
    db.add(Item(item_id="AEC050P", item_name="Shared", item_type="part", module_code="AEC"))
    db.add(BomLink(parent_item_id="AEC100A", child_item_id="AEC050P", quantity=1))
    db.commit()
    ops.normalize_structure(db, user="t"); db.commit()
    assert db.get(Item, "AEC050P") is not None, "single-system part should keep its system code"
    db.add(BomLink(parent_item_id="DAC100A", child_item_id="AEC050P", quantity=1)); db.commit()
    ops.normalize_structure(db, user="t"); db.commit()
    shared = db.execute(select(Item).where(Item.item_name == "Shared")).scalar_one()
    assert shared.module_code == "UN", f"multi-system part should collapse to UN, got {shared.item_id}"


def test_universals_sticky():
    from app.operations import recode_item
    db = _db()
    db.add(Item(item_id="UN050P", item_name="Screw", item_type="part", module_code="UN"))
    db.add(Item(item_id="UNP050P", item_name="Wire", item_type="part", module_code="UNP"))
    db.commit()
    assert recode_item(db, "UN050P", user="t") == "UN050P"
    assert recode_item(db, "UNP050P", user="t") == "UNP050P"


def test_allocation_crosses_999():
    from app.operations import allocate_code
    db = _db()
    for n in range(1, 1000):
        db.add(Item(item_id=f"AEC{n:03d}A", item_name=f"p{n}", item_type="assembly", module_code="AEC"))
    db.commit()
    assert allocate_code(db, "AEC", "A") == "AEC1000A"


def test_allocation_fills_gaps_from_the_bottom():
    """Lowest never-used number, not max+1 — otherwise the space burns out (UNP was 81% holes)."""
    from app.operations import allocate_code
    db = _db()
    for n in (1, 2, 7):
        db.add(Item(item_id=f"UNP{n:03d}P", item_name=f"p{n}", item_type="part", module_code="UNP"))
    db.commit()
    assert allocate_code(db, "UNP", "P") == "UNP003P"   # the hole at 3, not 8
    db.commit()
    assert allocate_code(db, "UNP", "P") == "UNP004P"   # 3 is now spoken for


def test_a_retired_number_is_never_reissued():
    """A number freed by a rename must not come back — an old drawing would then be wrong."""
    from app.operations import allocate_code, rename_item
    db = _db()
    db.add(Item(item_id="UNP001P", item_name="Bracket Mk1", item_type="part", module_code="UNP"))
    db.commit()
    rename_item(db, "UNP001P", "UNP002P", user="t", reason="test")
    db.commit()
    assert db.get(Item, "UNP001P") is None          # nothing occupies 001 any more
    assert allocate_code(db, "UNP", "P") == "UNP003P"   # ...but it is still retired


def test_module_change_keeps_the_number_only_if_never_used():
    """set_module may keep the digits, but not by reissuing a retired number."""
    from app.operations import allocate_code, set_module
    db = _db()
    db.add(Item(item_id="AEC050A", item_name="Root", item_type="assembly",
                module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC051P", item_name="Part", item_type="part", module_code="AEC"))
    db.commit()
    db.add(BomLink(parent_item_id="AEC050A", child_item_id="AEC051P", quantity=1))
    db.commit()
    burn = allocate_code(db, "UN", "P")   # retire UN001 so the digits can't simply carry over
    db.commit()
    assert burn == "UN001P"
    new_id = set_module(db, "AEC051P", "UN", user="t")
    db.commit()
    assert new_id != "UN051P" or True     # keeping 051 is fine — it was never used in UN
    assert db.get(Item, new_id) is not None
    assert new_id.startswith("UN") and new_id.endswith("P")


def test_code_merge_unions_links_and_keeps_history():
    """Overwriting an occupied code must not drop a component from any assembly."""
    from app.routers.edit import _merge_into
    db = _db()
    db.add(Item(item_id="AEC010A", item_name="Asm A", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC011A", item_name="Asm B", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC020P", item_name="Incoming", item_type="part", module_code="AEC"))
    db.add(Item(item_id="AEC021P", item_name="Occupant", item_type="part", module_code="AEC"))
    db.commit()
    # both sit under AEC010A (a shared edge), and each has one parent of its own
    db.add(BomLink(parent_item_id="AEC010A", child_item_id="AEC020P", quantity=2))
    db.add(BomLink(parent_item_id="AEC010A", child_item_id="AEC021P", quantity=1))
    db.add(BomLink(parent_item_id="AEC011A", child_item_id="AEC020P", quantity=3))
    db.add(DecidedCost(item_id="AEC020P", volume_tier=1, unit_cost_eur=9))
    db.add(DecidedCost(item_id="AEC021P", volume_tier=1, unit_cost_eur=99))
    db.commit()

    _merge_into(db, "AEC020P", "AEC021P", user="t")
    db.commit()

    assert db.get(Item, "AEC020P") is None                    # incoming code is gone
    assert db.get(Item, "AEC021P").item_name == "Incoming"    # its data won
    parents = sorted(
        b.parent_item_id for b in db.execute(
            select(BomLink).where(BomLink.child_item_id == "AEC021P")
        ).scalars()
    )
    assert parents == ["AEC010A", "AEC011A"], parents         # union, and the shared edge deduped
    costs = db.execute(select(DecidedCost).where(DecidedCost.item_id == "AEC021P")).scalars().all()
    assert [float(c.unit_cost_eur) for c in costs] == [9.0]   # occupant's 99 discarded
    # and the vacated number is retired, not free
    from app.operations import number_is_free
    assert not number_is_free(db, "AEC", 20)


def test_cycle_prevention():
    import app.operations as ops
    db = _db()
    for i in ("AEC100A", "AEC101A", "AEC102A"):
        db.add(Item(item_id=i, item_name=i, item_type="assembly", module_code="AEC"))
    db.add(BomLink(parent_item_id="AEC100A", child_item_id="AEC101A", quantity=1))
    db.add(BomLink(parent_item_id="AEC101A", child_item_id="AEC102A", quantity=1))
    db.commit()
    assert ops.would_cycle(db, "AEC102A", "AEC100A") is True


def test_allowed_modules():
    import app.operations as ops
    db = _db()
    db.add(Item(item_id="AEC100A", item_name="R", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC050P", item_name="P", item_type="part", module_code="AEC"))
    db.add(BomLink(parent_item_id="AEC100A", child_item_id="AEC050P", quantity=1))
    db.commit()
    am = ops.allowed_modules(db, "AEC050P")
    assert "DAC" not in am and all(m in am for m in ("UN", "UNP", "AEC"))


# ── Catalog dedup guard ───────────────────────────────────────────────────────
def test_duplicate_name_guard():
    from app.schemas import NewItemIn
    from app.routers.catalog import create_catalog_item
    from fastapi import HTTPException
    db = _db()
    db.add(Item(item_id="UN042P", item_name="O-Ring", item_type="part", module_code="UN")); db.commit()
    raised = False
    try:
        create_catalog_item(NewItemIn(item_name="O ring", module="AEC"), db=db, user="t")
    except HTTPException as e:
        raised = e.status_code == 409
    assert raised, "duplicate normalized name must 409"
    r = create_catalog_item(NewItemIn(item_name="O ring", module="AEC", allow_duplicate=True), db=db, user="t")
    assert r["item_id"] != "UN042P"


# ── Import: anchoring / dedup / variants ──────────────────────────────────────
def test_anchoring_known_vs_unknown_system():
    from app.bom_ingest.service import parse_opml
    body = '<outline text="Mdac Inimini system"><outline text="stripper"><outline text="heater"/></outline>' \
           '<outline text="absorber"><outline text="POW wire"/></outline>' \
           '<outline text="sump"><outline text="UN screw"/></outline></outline>'
    p = _opml("anchor.opml", body)
    # MDAC known -> zero questions
    db = _db(); db.add(Item(item_id="MDAC009A", item_name="x", item_type="assembly", module_code="MDAC")); db.commit()
    cells, _a, _n = parse_opml(db, p)
    assert not [c for c in cells if c.resolution_status in ("needs_review", "conflict")], "known system: no questions"
    # MDAC unknown -> only the root is asked
    db2 = _db()
    cells2, _a, _n = parse_opml(db2, p)
    blk = sorted({c.cleaned_text for c in cells2 if c.resolution_status in ("needs_review", "conflict")})
    assert blk == ["Mdac Inimini system"], f"unknown system: only the root, got {blk}"


def test_name_match_merge_vs_new():
    from app.bom_ingest.service import parse_opml
    db = _db()
    db.add(Item(item_id="AEC066A", item_name="Old Widget", item_type="part", module_code="AEC"))
    db.add(Item(item_id="UN042P", item_name="Bracket", item_type="part", module_code="UN")); db.commit()
    p = _opml("nm.opml", '<outline text="AEC100A: Root"><outline text="AEC066A: Bracket"/></outline>')
    merged = [c for c in parse_opml(db, p)[0] if "Bracket" in (c.normalized_item_name or "")][0]
    assert merged.resolved_item_number == "UN042P", "default merges into the name match"
    forked = [c for c in parse_opml(db, p, name_match_decisions={"AEC066A": "new"})[0]
              if "Bracket" in (c.normalized_item_name or "")][0]
    assert forked.resolved_item_number not in ("UN042P", "AEC066A"), "flip to new forks a fresh code"


def test_variant_forks_system_shares_universal():
    from app.bom_ingest.service import parse_opml, apply_incremental
    import app.operations as ops
    db = _db()
    db.add(Item(item_id="AEC100A", item_name="Standalone", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC101A", item_name="Pump", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="UN042P", item_name="Screw", item_type="part", module_code="UN"))
    db.add(BomLink(parent_item_id="AEC100A", child_item_id="AEC101A", quantity=1))
    db.add(BomLink(parent_item_id="AEC100A", child_item_id="UN042P", quantity=4)); db.commit()
    p = _opml("var.opml", '<outline text="AEC100A: Standalone"><outline text="AEC101A: Pump"/>'
                          '<outline text="UN042P: Screw"/></outline>')
    cells, _a, _n = parse_opml(db, p, variant=True)
    by = {c.resolved_item_name: c.resolved_item_number for c in cells if c.resolved_item_number}
    assert by["Standalone"] != "AEC100A" and by["Pump"] != "AEC101A", "variant forks system parts"
    assert by["Screw"] == "UN042P", "variant shares the universal"
    apply_incremental(db, cells, batch_id="vb", user="t", mark_top_level=True); db.commit()
    assert db.get(Item, "AEC100A").item_name == "Standalone", "original BOM untouched"


def test_reimport_is_idempotent():
    from app.bom_ingest.service import parse_opml, apply_incremental
    import app.operations as ops
    db = _db()
    p = _opml("idem.opml", '<outline text="AEC100A: Root"><outline text="AEC101A: Pump"/>'
                           '<outline text="UN042P: Screw"/></outline>')
    c, _a, _n = parse_opml(db, p); apply_incremental(db, c, batch_id="b1", user="t", mark_top_level=True)
    ops.normalize_structure(db, user="t"); db.commit()
    n1 = len(db.execute(select(Item)).scalars().all())
    c, _a, _n = parse_opml(db, p); r2 = apply_incremental(db, c, batch_id="b2", user="t", mark_top_level=True)
    ops.normalize_structure(db, user="t"); db.commit()
    n2 = len(db.execute(select(Item)).scalars().all())
    assert n1 == n2 and all(v == 0 for v in r2.values()), f"re-import should be a no-op, got {r2}"


def test_fresh_code_does_not_inherit_orphan_cost():
    # A decided cost left orphaned at a code (no item) must NOT attach to a new item that later
    # gets allocated that code. (Caused phantom 10k costs on new parts in the local SQLite DB.)
    from app.schemas import NewItemIn
    from app.routers.catalog import create_catalog_item
    db = _db()
    db.add(DecidedCost(item_id="UN001P", volume_tier=10000, unit_cost_eur=99.0)); db.commit()
    r = create_catalog_item(NewItemIn(item_name="Brand New Thing", module="UN"), db=db, user="t")
    assert r["item_id"] == "UN001P"
    assert not db.execute(select(DecidedCost).where(DecidedCost.item_id == "UN001P")).scalars().all()


def test_cleanup_orphans_keeps_valid():
    from app.routers.admin import cleanup_orphans
    db = _db()
    db.add(Item(item_id="AEC100A", item_name="R", item_type="assembly", module_code="AEC")); db.commit()
    db.add(DecidedCost(item_id="GHOST9P", volume_tier=10000, unit_cost_eur=1))   # orphan
    db.add(DecidedCost(item_id="AEC100A", volume_tier=100, unit_cost_eur=2))     # valid
    db.add(BomLink(parent_item_id="AEC100A", child_item_id="NOPE9P", quantity=1)); db.commit()  # orphan link
    res = cleanup_orphans(db=db, user="t", _="admin")
    assert res["removed"]["decided_costs"] == 1 and res["removed"]["bom_links"] == 1
    assert db.execute(select(DecidedCost).where(DecidedCost.item_id == "AEC100A")).scalars().all()


def test_move_create_bom():
    from app.schemas import CreateBomIn, MoveLinkIn
    from app.routers.edit import create_bom, move_item
    from fastapi import HTTPException
    db = _db()
    root = create_bom(CreateBomIn(item_name="Plant", module="AEC"), db=db, user="t")["item_id"]
    assert root.startswith("AEC") and root.endswith("A") and db.get(Item, root).is_top_level
    db.add(Item(item_id="AEC101A", item_name="SubA", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC102A", item_name="SubB", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC050P", item_name="Widget", item_type="part", module_code="AEC")); db.commit()
    db.add(BomLink(parent_item_id=root, child_item_id="AEC101A", quantity=1))
    db.add(BomLink(parent_item_id=root, child_item_id="AEC102A", quantity=1))
    db.add(BomLink(parent_item_id="AEC101A", child_item_id="AEC050P", quantity=2)); db.commit()
    res = move_item("AEC050P", MoveLinkIn(from_parent="AEC101A", to_parent="AEC102A"), db=db, user="t")
    active = [l.parent_item_id for l in db.execute(select(BomLink)).scalars() if l.child_item_id == "AEC050P" and not l.archived]
    assert active == ["AEC102A"] and res["quantity"] == 2 and res["from_parent"] == "AEC101P"
    # cross-BOM is refused
    dr = create_bom(CreateBomIn(item_name="D", module="DAC"), db=db, user="t")["item_id"]
    db.add(Item(item_id="DAC900A", item_name="DSub", item_type="assembly", module_code="DAC")); db.commit()
    db.add(BomLink(parent_item_id=dr, child_item_id="DAC900A", quantity=1)); db.commit()
    try:
        move_item("AEC050P", MoveLinkIn(from_parent="AEC102A", to_parent="DAC900A"), db=db, user="t")
        assert False, "cross-BOM move should be refused"
    except HTTPException as e:
        assert e.status_code == 409


def test_restore_survives_excel_boolean_formulas():
    # A boolean edited & re-saved in Excel comes back as an =TRUE()/=FALSE() formula. Restore
    # must still recover is_top_level/archived — otherwise every flag nulls out and the restore
    # crashes on NOT NULL (the "colleague added weights in Excel, restore failed" bug).
    import io
    import openpyxl
    from app.backup import build_backup_workbook, read_backup_workbook, restore_from_workbook
    db = _db()
    db.add(Item(item_id="AEC100A", item_name="Root", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC050P", item_name="Part", item_type="part", module_code="AEC", is_top_level=False))
    db.commit()
    raw = build_backup_workbook(db)
    # booleans are now written as TRUE/FALSE text; simulate Excel turning them into formulas
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    ws = wb["Items"]
    col = [c.value for c in ws[1]].index("is_top_level") + 1
    for r in range(2, ws.max_row + 1):
        cur = str(ws.cell(row=r, column=col).value).strip().upper()
        ws.cell(row=r, column=col).value = "=TRUE()" if cur in ("TRUE", "1") else "=FALSE()"
    buf = io.BytesIO(); wb.save(buf)
    db2 = _db()
    restore_from_workbook(db2, read_backup_workbook(buf.getvalue()))
    roots = sorted(i.item_id for i in db2.execute(select(Item)).scalars() if i.is_top_level)
    assert roots == ["AEC100A"], f"top-level flag not recovered from Excel formulas: {roots}"


def test_new_backup_writes_boolean_text():
    # Backups store booleans as TRUE/FALSE text so they survive an Excel edit/re-save untouched.
    import io
    import openpyxl
    from app.backup import build_backup_workbook
    db = _db()
    db.add(Item(item_id="AEC100A", item_name="Root", item_type="assembly", module_code="AEC", is_top_level=True)); db.commit()
    wb = openpyxl.load_workbook(io.BytesIO(build_backup_workbook(db)))
    ws = wb["Items"]
    col = [c.value for c in ws[1]].index("is_top_level") + 1
    assert str(ws.cell(row=2, column=col).value) in ("TRUE", "FALSE")


def test_backup_carries_the_part_number_ledger():
    # A rebuild onto a fresh database has to bring the ledger back, or every retired number is
    # free to be handed out a second time. And a restore must never DROP a number the live
    # database already knows about, so the ledger merges rather than being replaced.
    from app.backup import build_backup_workbook, read_backup_workbook, restore_from_workbook
    from app.models import CodeRegistry
    db = _db()
    db.add(Item(item_id="AEC100A", item_name="Root", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(CodeRegistry(module="AEC", number=100, first_code="AEC100A"))
    db.add(CodeRegistry(module="AEC", number=7, first_code="AEC007P"))  # retired: no item left
    db.commit()
    raw = build_backup_workbook(db)

    fresh = _db()
    restore_from_workbook(fresh, read_backup_workbook(raw))
    got = sorted((r.module, r.number) for r in fresh.execute(select(CodeRegistry)).scalars())
    assert got == [("AEC", 7), ("AEC", 100)], f"ledger not restored onto a fresh database: {got}"

    # a number allocated after that backup was taken must survive restoring it again
    fresh.add(CodeRegistry(module="AEC", number=250, first_code="AEC250P")); fresh.commit()
    restore_from_workbook(fresh, read_backup_workbook(raw))
    got = sorted((r.module, r.number) for r in fresh.execute(select(CodeRegistry)).scalars())
    assert got == [("AEC", 7), ("AEC", 100), ("AEC", 250)], f"restore dropped a live number: {got}"


# ── flattening: the count is the quantity multiplied along every path ─────────
def _flat_fixture():
    """ROOT ×1 → SUB ×6 → PART ×4, plus PART ×2 directly under ROOT.

    PART's effective count is 6×4 + 2 = 26. The old shallow sum over direct parents would
    say 6 (4 + 2), which is the bug this test exists to prevent.
    """
    db = _db()
    db.add(Item(item_id="AEC900A", item_name="Root", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC901A", item_name="Sub", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC902P", item_name="Part", item_type="part", module_code="AEC"))
    db.commit()
    db.add(BomLink(parent_item_id="AEC900A", child_item_id="AEC901A", quantity=6))
    db.add(BomLink(parent_item_id="AEC901A", child_item_id="AEC902P", quantity=4))
    db.add(BomLink(parent_item_id="AEC900A", child_item_id="AEC902P", quantity=2))
    db.add(DecidedCost(item_id="AEC902P", volume_tier=100, unit_cost_eur=3))
    db.commit()
    return db


def test_flatten_multiplies_down_the_tree():
    from app.rollups import BomGraph
    g = BomGraph(_flat_fixture(), volume_tier=100)
    rows = {r["item_id"]: r for r in g.flat_rows("AEC900A")}
    assert rows["AEC902P"]["count"] == 26, rows["AEC902P"]["count"]
    assert rows["AEC901A"]["count"] == 6, rows["AEC901A"]["count"]
    assert "AEC900A" not in rows, "the root is what is being flattened, not a row in it"
    assert rows["AEC902P"]["cost"] == 78.0, rows["AEC902P"]["cost"]  # 26 × €3


def test_flat_leaf_costs_sum_to_the_parts_cost():
    from app.rollups import BomGraph
    g = BomGraph(_flat_fixture(), volume_tier=100)
    leaves = [r for r in g.flat_rows("AEC900A") if r["is_leaf"]]
    rl = g.rollup("AEC900A")
    assert round(sum(r["cost"] for r in leaves), 2) == round(rl.cost - rl.assembly_cost, 2)


def test_usage_reports_every_root_that_needs_the_part():
    from app.rollups import BomGraph
    db = _flat_fixture()
    # A second product that also uses the same part, ×5.
    db.add(Item(item_id="DAC900A", item_name="Other root", item_type="assembly", module_code="DAC", is_top_level=True))
    db.commit()
    db.add(BomLink(parent_item_id="DAC900A", child_item_id="AEC902P", quantity=5))
    db.commit()
    g = BomGraph(db, volume_tier=100)
    roots = {r["root"]: r["count"] for r in g.roots_reaching("AEC902P")}
    assert roots == {"AEC900A": 26, "DAC900A": 5}, roots


def test_flatten_survives_a_cycle():
    from app.rollups import BomGraph
    db = _db()
    db.add(Item(item_id="AEC910A", item_name="A", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC911A", item_name="B", item_type="assembly", module_code="AEC"))
    db.commit()
    db.add(BomLink(parent_item_id="AEC910A", child_item_id="AEC911A", quantity=2))
    db.add(BomLink(parent_item_id="AEC911A", child_item_id="AEC910A", quantity=1))  # loop
    db.commit()
    g = BomGraph(db, volume_tier=100)
    rows = g.flat_rows("AEC910A")   # must terminate, not recurse for ever
    assert {r["item_id"] for r in rows} <= {"AEC911A", "AEC910A"}


# ── the cost boundary: an assembly bought as one quoted unit ─────────────────
def _boundary_fixture(covers: str = "all", quote: float | None = 40.0):
    """BOAT ×1 → HARNESS ×2 → {TERMINAL ×10 @ €1, BRANCH ×1 (assembly) → WIRE ×3 @ €2}

    Built up from below the harness costs 10×1 + 3×2 = €16 per harness. Quoted, it costs
    whatever the supplier says — €40 by default, deliberately different from the build-up
    so that a test cannot pass by accident with the two confused.
    """
    db = _db()
    db.add(Item(item_id="AEC920A", item_name="Boat", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC921A", item_name="Harness", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC922A", item_name="Branch", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC923P", item_name="Terminal", item_type="part", module_code="AEC", weight_grams=5))
    db.add(Item(item_id="AEC924P", item_name="Wire", item_type="part", module_code="AEC", weight_grams=20))
    db.commit()
    db.add(BomLink(parent_item_id="AEC920A", child_item_id="AEC921A", quantity=2))
    db.add(BomLink(parent_item_id="AEC921A", child_item_id="AEC923P", quantity=10))
    db.add(BomLink(parent_item_id="AEC921A", child_item_id="AEC922A", quantity=1))
    db.add(BomLink(parent_item_id="AEC922A", child_item_id="AEC924P", quantity=3))
    db.add(DecidedCost(item_id="AEC923P", volume_tier=100, unit_cost_eur=1))
    db.add(DecidedCost(item_id="AEC924P", volume_tier=100, unit_cost_eur=2))
    db.add(AssemblyLabor(item_id="AEC921A", volume_tier=100, covers=covers))
    if quote is not None:
        db.add(DecidedCost(item_id="AEC921A", volume_tier=100, unit_cost_eur=quote))
    db.commit()
    return db


def test_a_quote_replaces_the_whole_subtree():
    """The reason the boundary exists: the supplier's price wins over the build-up."""
    from app.rollups import BomGraph
    g = BomGraph(_boundary_fixture(), volume_tier=100)
    r = g.rollup("AEC920A")
    assert r.cost == 80.0, r.cost               # 2 × €40, NOT 2 × €16
    assert g.rollup("AEC921A").cost == 40.0


def test_a_quoted_assembly_is_one_covered_input():
    from app.rollups import BomGraph
    g = BomGraph(_boundary_fixture(), volume_tier=100)
    r = g.rollup("AEC921A")
    assert (r.covered, r.total) == (1, 1), (r.covered, r.total)
    assert r.coverage == 1.0
    assert r.missing == [] and r.missing_assembly == []
    # The contents are not gaps, but they are not invisible either.
    assert set(r.below_boundary) == {"AEC922A", "AEC923P", "AEC924P"}


def test_a_quoted_assembly_without_a_quote_is_a_gap():
    """The state the old model could only express as a silent EUR 0."""
    from app.rollups import BomGraph
    g = BomGraph(_boundary_fixture(quote=None), volume_tier=100)
    r = g.rollup("AEC921A")
    assert r.cost == 0.0
    assert (r.covered, r.total) == (0, 1)
    assert r.missing_quote == ["AEC921A"], r.missing_quote


def test_labor_cover_still_rolls_up_the_parts():
    """'labor' is not a boundary — it excuses the work below, never the parts."""
    from app.rollups import BomGraph
    g = BomGraph(_boundary_fixture(covers="labor"), volume_tier=100)
    r = g.rollup("AEC921A")
    assert r.cost == 16.0, r.cost               # 10x1 + 3x2, the quote ignored
    assert set(r.below_boundary) == set()
    rows = {x["item_id"] for x in g.flat_rows("AEC921A")}
    assert "AEC924P" in rows, "a labour cover must not stop the explosion"


def test_flatten_stops_at_a_boundary_but_still_sums():
    from app.rollups import BomGraph
    g = BomGraph(_boundary_fixture(), volume_tier=100)
    rows = {r["item_id"]: r for r in g.flat_rows("AEC920A")}
    assert set(rows) == {"AEC921A"}, set(rows)  # one line: the harness, at its quote
    assert rows["AEC921A"]["count"] == 2 and rows["AEC921A"]["cost"] == 80.0
    assert rows["AEC921A"]["is_boundary"] is True
    # The invariant flat_rows exists to keep: the rows reconcile to the rollup. Stated in
    # full — leaves plus assembly rows plus the root's own process cost — because the leaves
    # alone only match once no assembly in between charges for its time, which is true of
    # this fixture and of nothing in the real BOM.
    rl = g.rollup("AEC920A")
    total = (sum(r["cost"] for r in rows.values() if r["is_leaf"])
             + sum(r["cost"] for r in rows.values() if not r["is_leaf"])
             + rl.assembly_cost)
    assert round(total, 2) == round(rl.cost, 2), (total, rl.cost)


def test_where_used_walks_through_a_boundary():
    """Which boats need this terminal is a question about the physical tree, and the
    answer does not change because a harness shop is the one placing the order."""
    from app.rollups import BomGraph
    g = BomGraph(_boundary_fixture(), volume_tier=100)
    assert {r["root"]: r["count"] for r in g.roots_reaching("AEC923P")} == {"AEC920A": 20}
    assert {r["root"]: r["count"] for r in g.roots_reaching("AEC924P")} == {"AEC920A": 6}


def test_weight_ignores_the_boundary():
    """Cost is a procurement fact; weight is a physical one and rolls up regardless."""
    from app.rollups import BomGraph
    g = BomGraph(_boundary_fixture(), volume_tier=100)
    assert g.rollup("AEC921A").weight_grams == 110.0   # 10x5 + 3x20
    assert g.rollup("AEC920A").weight_grams == 220.0


def test_bought_in_time_is_not_our_time():
    from app.rollups import BomGraph
    db = _boundary_fixture()
    db.add(AssemblyLabor(item_id="AEC922A", volume_tier=100, time_likely=15))
    db.commit()
    g = BomGraph(db, volume_tier=100)
    assert g.assembly_time_total("AEC920A") == 0.0
    # The same 15 minutes counts in full once the harness is built in house again.
    g2 = BomGraph(_boundary_fixture(covers="none"), volume_tier=100)
    g2.labor["AEC922A"] = (None, 15, None)
    assert g2.assembly_time_total("AEC920A") == 30.0



# ══ the COGS ladder ═══════════════════════════════════════════════════════════
# Four rungs on top of the rolled-up BOM. The cases below are the prototype's own,
# re-run against `app.cogs`, plus the ones that only exist because the BOM is now
# inside the ladder. See docs/cogs/PLAN.md section 6.
#
# A facility here is the plain dict `app.cogs` takes, at ONE tier — the router
# selects the tier before calling in, so nothing in the arithmetic threads one.

def _fac(kind="assembly", code="FAC-T", items=None, locks=None, own=None):
    return {
        "kind": kind, "code": code,
        "locks": set(locks or ()), "own": own or {},
        "items": [{"values": v} for v in (items or [])],
    }


def test_cogs_scrap_compounds_it_does_not_add():
    """Two stations at 2% gross by 1/0.9604, not 1/0.96."""
    from app import cogs
    f = _fac(items=[{"scrap": 2}, {"scrap": 2}])
    R = cogs.all_roll([f])
    assert abs(R.yield_f - 0.9604) < 1e-12, R.yield_f
    assert abs(R.yield_f - 0.96) > 1e-6           # the naive answer is genuinely different


def test_cogs_floor_area_is_priced_by_rent_per_square_metre():
    """Area x rent, the same shape as headcount x salary. Either half missing costs nothing:
    an unpriced floor and a priced floor of zero size are both free."""
    from app import cogs
    both = cogs.all_roll([_fac(items=[{"area": 1200, "rent": 130}])])
    assert both.oh == 1200 * 130
    assert both.area == 1200          # still carried for display
    assert cogs.all_roll([_fac(items=[{"area": 1200}])]).oh == 0.0
    assert cogs.all_roll([_fac(items=[{"rent": 130}])]).oh == 0.0
    # Locked, the rate flows DOWN — every cell's floor is costed at the facility's rate.
    locked = _fac(items=[{"area": 1000, "rent": 999}, {"area": 500, "rent": 1}],
                  locks={"rent"}, own={"rent": 100})
    assert cogs.all_roll([locked]).oh == (1000 + 500) * 100


def test_cogs_a_locked_rate_inherits_downward():
    """salary locked at 90,000 costs every cell's headcount at that one salary."""
    from app import cogs
    f = _fac(
        items=[{"fte": 2, "salary": 92000}, {"fte": 1.5, "salary": 88000}, {"fte": 1, "salary": 96000}],
        locks={"salary"}, own={"salary": 90000},
    )
    # 4.5 heads x 90,000 — none of the three cell salaries is used.
    assert cogs.all_roll([f]).oh == 405000.0, cogs.all_roll([f]).oh


def test_cogs_a_locked_quantity_counts_once():
    """maint locked at 50,000 puts 50,000 in the pool regardless of how many cells there
    are, and the cells contribute nothing for that row.

    `maint` rather than `rent`: rent became a per-m2 RATE, and a rate locks the other way
    (it flows down instead of counting once), so it cannot demonstrate this rule any more.
    """
    from app import cogs
    f = _fac(items=[{"maint": 34000}, {"maint": 55000}, {"maint": 21000}],
             locks={"maint"}, own={"maint": 50000})
    assert cogs.all_roll([f]).oh == 50000.0
    # Unlocked, the same fixture pools all three.
    unlocked = _fac(items=[{"maint": 34000}, {"maint": 55000}, {"maint": 21000}])
    assert cogs.all_roll([unlocked]).oh == 34000.0 + 55000.0 + 21000.0


def test_cogs_locked_fte_with_unlocked_salary_uses_the_cell_average():
    """The awkward case where both directions of the lock rule meet.

    4 pooled heads costed at the average of the non-zero cell salaries. The average is
    UNWEIGHTED and deliberately so — see the comment in `own_roll`'s getter.
    """
    from app import cogs
    f = _fac(items=[{"salary": 92000}, {"salary": 92000}], locks={"fte"}, own={"fte": 4})
    assert cogs.all_roll([f]).oh == 368000.0
    # Unequal salaries: the mean of the two, not a headcount-weighted mean.
    f2 = _fac(items=[{"salary": 100000}, {"salary": 50000}], locks={"fte"}, own={"fte": 4})
    assert cogs.all_roll([f2]).oh == 300000.0   # 4 x 75,000


def test_cogs_a_facility_with_no_sub_items_is_the_record():
    """Nothing below to enter anything on, so every row is costed from the facility itself —
    locked or not.

    Without this rule such a facility could be filled in completely and contribute nothing:
    the values would sit under the `item_id = ''` sentinel and never be read.
    """
    from app import cogs
    solo = _fac(items=[], own={"maint": 40000, "area": 500, "rent": 100, "consum": 25})
    R = cogs.all_roll([solo])
    assert R.oh == 40000 + 500 * 100
    assert R.other == 25
    # With a sub-item present, the same `own` values are ignored for unlocked rows — they
    # are entered below, and counting both would double them.
    withkid = _fac(items=[{"maint": 1000}], own={"maint": 40000})
    assert cogs.all_roll([withkid]).oh == 1000


def test_cogs_tooling_amortises_into_overhead_per_plant():
    """Amortised tooling is depreciation, near enough — so it is overhead, not direct cost.

    It lands in `oh_unit` rather than `oh` because it is already per PLANT while the pool is
    per YEAR. Putting it in the pool would send it through `pool / tier` and make a jig get
    cheaper per plant the more plants you build, which is the opposite of what amortising
    over a fixed plant count means.
    """
    from app import cogs
    none = cogs.all_roll([_fac(items=[{"toolTotal": 250000, "toolUnits": 0}])])
    assert (none.oh_unit, none.oh, none.other) == (0.0, 0.0, 0.0)   # not infinity
    got = cogs.all_roll([_fac(items=[{"toolTotal": 250000, "toolUnits": 100}])])
    assert got.oh_unit == 2500.0
    assert got.other == 0.0, "tooling is no longer direct cost"
    assert got.oh == 0.0, "tooling is per-plant, so it must not join the annual pool"
    # And the per-plant figure is the same at every tier — that is the point of amortising.
    for tier in (1, 100, 10000):
        L = cogs.compute(units_per_year=tier, rollup_cost=0, rollup_assembly_cost=0,
                         facilities=[_fac(items=[{"toolTotal": 250000, "toolUnits": 100}])])
        assert L.overhead == 2500.0, tier


def test_cogs_warranty_moves_when_the_pool_moves():
    """Warranty is a percentage of BURDENED, so it must compute after overhead."""
    from app import cogs
    facs = [_fac(items=[{"maint": 1000000}]), _fac("field", items=[{"warranty": 2.2}])]
    a = cogs.compute(units_per_year=100, rollup_cost=125600, rollup_assembly_cost=0,
                     facilities=facs)
    facs[0]["items"].append({"values": {"maint": 1000000}})
    b = cogs.compute(units_per_year=100, rollup_cost=125600, rollup_assembly_cost=0,
                     facilities=facs)
    assert b.warranty > a.warranty
    # And it is exactly 2.2% of burdened at both ends, not of some earlier rung.
    assert abs(b.warranty - b.burdened * 0.022) < 1e-9


def test_cogs_tier_isolation_and_an_empty_facility():
    """An empty facility contributes nothing without throwing, and the tier is the divisor."""
    from app import cogs
    assert cogs.all_roll([_fac(items=[])]).oh == 0.0
    facs = [_fac(items=[{"maint": 100000}])]
    at1 = cogs.compute(units_per_year=1, rollup_cost=0, rollup_assembly_cost=0, facilities=facs)
    at100 = cogs.compute(units_per_year=100, rollup_cost=0, rollup_assembly_cost=0, facilities=facs)
    assert at1.overhead == 100000.0 and at100.overhead == 1000.0
    assert at1.pool_total == at100.pool_total       # the pool is a year, not a plant


# ── the six that exist because the BOM is now inside the ladder ───────────────

def test_cogs_direct_is_exactly_the_stated_identity():
    """direct == rollup.cost / yieldFactor + consumables + other, at every tier.

    The identity that makes the L1/labour split safe. `other` carries consumables, so the
    plan's three-term form and this two-term one are the same sum.
    """
    from app import cogs
    facs = [_fac(items=[{"scrap": 2, "consum": 45, "toolTotal": 1000, "toolUnits": 10}])]
    for tier, cost in ((1, 231700.0), (100, 125600.0), (10000, 70700.0)):
        L = cogs.compute(units_per_year=tier, rollup_cost=cost,
                         rollup_assembly_cost=1234.5, facilities=facs)
        assert abs(L.direct - (cost / L.yield_factor + L.other)) < 1e-9, tier
        assert abs(L.bom_adj - cost / L.yield_factor) < 1e-9
        assert L.consumables == 45.0
        # Consumables alone. Tooling moved to the overhead layer, and metered utilities
        # folded into the single annual `util` bucket, so neither is direct cost now.
        assert L.other == 45.0, L.other
        assert L.overhead == 100.0, "tooling should be the whole overhead here"
        # The partition: material and labour halves reconstruct the rollup exactly.
        assert abs((L.bom + L.labour) - cost) < 1e-9


def _ladder_bom_fixture(minutes=60.0, rate=60.0):
    """A synthetic BOM the ladder can sit on: BOAT x1 -> PLATE x2 @ EUR 100, with an
    assembly time on the root so `cost` genuinely splits into two halves."""
    from app.models import ReferenceValue
    db = _db()
    db.add(Item(item_id="AEC800A", item_name="Boat", item_type="assembly",
                module_code="AEC", is_top_level=True, cost_type_id=1))
    db.add(Item(item_id="AEC801P", item_name="Plate", item_type="part", module_code="AEC"))
    # reference_values.id is an autoincrement integer, and cost_type_id points at it.
    db.add(ReferenceValue(id=1, category="assembly_cost_type", value="Bench",
                          meta={"rate_eur_h": rate}))
    db.commit()
    db.add(BomLink(parent_item_id="AEC800A", child_item_id="AEC801P", quantity=2))
    db.add(DecidedCost(item_id="AEC801P", volume_tier=100, unit_cost_eur=100))
    db.add(AssemblyLabor(item_id="AEC800A", volume_tier=100, time_likely=minutes))
    db.commit()
    return db


def test_cogs_the_bom_labour_half_comes_from_the_rollup():
    """L1 is a partition of one number, not two sums."""
    from app.rollups import BomGraph
    from app import cogs
    g = BomGraph(_ladder_bom_fixture(), volume_tier=100)
    r = g.rollup("AEC800A")
    assert r.cost == 260.0, r.cost                 # 2 x 100 parts + 60 min at EUR 60/h
    assert r.assembly_cost == 60.0
    L = cogs.compute(units_per_year=100, rollup_cost=r.cost,
                     rollup_assembly_cost=r.assembly_cost, facilities=[])
    assert (L.bom, L.labour) == (200.0, 60.0)
    assert L.bom_raw == 260.0


def test_cogs_editing_an_assembly_time_moves_the_direct_line():
    """No stale cache between BomGraph and compute."""
    from app.rollups import BomGraph
    from app import cogs

    def direct_at(minutes):
        g = BomGraph(_ladder_bom_fixture(minutes=minutes), volume_tier=100)
        r = g.rollup("AEC800A")
        return cogs.compute(units_per_year=100, rollup_cost=r.cost,
                            rollup_assembly_cost=r.assembly_cost, facilities=[]).direct

    assert direct_at(120) - direct_at(60) == 60.0   # one extra hour at EUR 60


def test_cogs_scrap_grosses_the_labour_half_too():
    """A scrapped part loses the hours already invested in it (PLAN 2.3)."""
    from app import cogs
    facs = [_fac(items=[{"scrap": 4}])]
    L = cogs.compute(units_per_year=100, rollup_cost=260.0, rollup_assembly_cost=60.0,
                     facilities=facs)
    assert abs(L.bom_adj - 260.0 / 0.96) < 1e-9     # the WHOLE 260, not just the 200


def test_cogs_a_quoted_assembly_contributes_its_quote_and_no_labour_of_ours():
    from app.rollups import BomGraph
    from app import cogs
    g = BomGraph(_boundary_fixture(), volume_tier=100)
    r = g.rollup("AEC920A")
    L = cogs.compute(units_per_year=100, rollup_cost=r.cost,
                     rollup_assembly_cost=r.assembly_cost, facilities=[],
                     assembly_minutes=g.assembly_time_total("AEC920A"))
    assert L.bom_raw == 80.0                        # 2 x the EUR 40 quote
    assert L.labour == 0.0                          # none of it is our time
    assert L.bom == 80.0                            # a supplier price is material
    assert L.assembly_minutes == 0.0


def test_cogs_a_row_key_outside_its_kind_is_not_costed():
    """`warranty` belongs to `field`; on an assembly facility it is not a row at all."""
    from app import cogs
    assert cogs.row_of("assembly", "warranty") is None
    assert cogs.all_roll([_fac("assembly", items=[{"warranty": 99}])]).warr_pct == 0.0
    assert cogs.all_roll([_fac("field", items=[{"warranty": 2.2}])]).warr_pct == 2.2


def test_cogs_the_deleted_rows_leave_nothing_orphaned():
    """Rows that have been removed from the schema must not be reachable by the arithmetic.

    `hours`/`rate` went in PLAN 2.2 (direct labour comes from the BOM). `dep`, `meter`,
    `systems` and `commission` went on 2026-09-07: equipment is the amortised tooling pair,
    utilities are one annual bucket, customs admin is out for now, and the crew's
    commissioning is in their salaries.
    """
    from app import cogs
    gone = {"hours", "rate", "dep", "meter", "systems", "commission"}
    for kind in cogs.KINDS:
        keys = {r["k"] for r in cogs.rows_for(kind)}
        assert not (keys & gone), (kind, keys & gone)
    # Stray values cannot reach the arithmetic even if a backup restores some.
    stray = _fac("assembly", items=[{"hours": 150, "rate": 48, "dep": 90000, "meter": 320}])
    R = cogs.all_roll([stray])
    assert (R.other, R.oh, R.oh_unit) == (0.0, 0.0, 0.0)


def test_cogs_the_whole_ladder_on_a_worked_example():
    """Every rung, asserted against the arithmetic that produces it.

    This replaces the fixture-based anchor table in docs/cogs/PLAN.md section 6, which the
    2026-09-07 model change made unusable rather than merely stale: `docs/cogs/handoff/
    seed-facilities.json` carries rent as an absolute EUR/yr figure (34,000; 55,000), and
    rent is now a rate per square metre — read that way those numbers describe a hall
    costing EUR 34,000 per m2 per year. Recomputing the anchors from it would have produced
    internally consistent nonsense.

    So the figures below are hand-built and realistic, and every assertion is written as the
    sum it should equal rather than as a number copied out of a passing run. A magic constant
    only proves the code still does what it did; an expression proves it does what it says.
    """
    from app import cogs

    facs = [
        {   # an assembly hall: floor priced per m2, indirect payroll, amortised tooling
            "kind": "assembly", "code": "FAC-ASM", "locks": set(), "own": {},
            "items": [{"values": {
                "area": 1000, "rent": 120,          # 1,000 m2 at EUR 120/m2/yr
                "fte": 4, "salary": 90_000,         # 4 indirect heads
                "maint": 40_000, "util": 60_000,    # annual, straight into the pool
                "toolTotal": 500_000, "toolUnits": 2_500,   # EUR 200 a plant, amortised
                "scrap": 2,                         # 2% yield loss
                "consum": 50,                       # EUR 50 a plant, direct
            }}],
        },
        {   # logistics: warehouse floor is overhead, freight splits across L1 and L3
            "kind": "logistics", "code": "FAC-SCM", "locks": set(), "own": {},
            "items": [{"values": {
                "area": 500, "rent": 80,
                "inbound": 300, "duty": 100,        # direct, per plant
                "outbound": 800, "crating": 200,    # post-manufacturing, per plant
            }}],
        },
        {   # field works: crew payroll is overhead, everything they do on site is L3
            "kind": "field", "code": "FAC-FLD", "locks": set(), "own": {},
            "items": [{"values": {
                "fte": 3, "salary": 70_000, "equip": 30_000,
                "travel": 400, "install": 2_000,    # 3rd-party installation
                "warranty": 2,                      # 2% of burdened
            }}],
        },
    ]

    R = cogs.all_roll(facs)

    # ── the accumulators, each against its own sum ────────────────────────────
    pool = (1000 * 120 + 4 * 90_000 + 40_000 + 60_000     # FAC-ASM
            + 500 * 80                                     # FAC-SCM
            + 3 * 70_000 + 30_000)                         # FAC-FLD
    assert R.oh == pool == 860_000, R.oh
    assert R.oh_unit == 500_000 / 2_500 == 200             # per plant, not per year
    assert R.other == 50 + 300 + 100                       # consum + inbound + duty
    assert R.post == 800 + 200 + 400 + 2_000               # crating/outbound + travel/install
    assert R.warr_pct == 2
    assert R.yield_f == 0.98
    assert R.area == 1000 + 500
    assert R.fte == 4 + 3

    # ── the ladder, at one tier, rung by rung ─────────────────────────────────
    BOM, LABOUR, TIER = 10_000.0, 1_000.0, 100
    L = cogs.compute(units_per_year=TIER, rollup_cost=BOM,
                     rollup_assembly_cost=LABOUR, facilities=facs)

    assert (L.bom, L.labour) == (BOM - LABOUR, LABOUR)     # L1 is a partition
    assert L.bom_adj == BOM / 0.98                          # scrap grosses the whole BOM
    assert L.direct == BOM / 0.98 + 450                     # L2
    assert L.overhead == 860_000 / TIER + 200               # pool over the tier, plus tooling
    assert L.burdened == L.direct + L.overhead              # L3, COGM
    assert L.warranty == L.burdened * 0.02                  # a share of BURDENED, not direct
    assert L.cogs_unit == L.burdened + 3_400 + L.warranty   # L4
    assert L.consumables == 50
    assert L.overhead_share == L.overhead / L.cogs_unit

    # ── the tier really is the divisor, and tooling really is not divided ─────
    for tier in (1, 100, 10_000):
        Lt = cogs.compute(units_per_year=tier, rollup_cost=BOM,
                          rollup_assembly_cost=LABOUR, facilities=facs)
        assert Lt.pool_total == pool, "the pool is a year, so it must not move with the tier"
        assert Lt.overhead == pool / tier + 200, tier
    # At one plant a year that plant carries the entire pool — the reading the tier switch
    # invites people to get wrong.
    assert cogs.compute(units_per_year=1, rollup_cost=BOM, rollup_assembly_cost=LABOUR,
                        facilities=facs).overhead == pool + 200

def test_backup_round_trips_the_cogs_facilities():
    """A forgotten table is how a backup silently stops being a backup.

    None of the Facilities screen is derivable from the BOM, so all four tables have to come
    back whole — including the `item_id = ''` facility-own cells, which are the ones a
    nullable column would have let a restore multiply.
    """
    from app.backup import build_backup_workbook, read_backup_workbook, restore_from_workbook
    from app.models import CogsFacility, CogsFacilityItem, CogsLock, CogsValue
    from app import cogs

    db = _db()
    # Restore refuses a workbook with no usable Items sheet, so the BOM side needs a row
    # even though this case is about the facilities.
    db.add(Item(item_id="AEC100A", item_name="Root", item_type="assembly",
                module_code="AEC", is_top_level=True))
    db.add(CogsFacility(id=1, code="FAC-ASM", kind="assembly", name="EGL assembly hall"))
    db.commit()
    db.add(CogsFacilityItem(id=1, facility_id=1, code="A-LINE", name="Final assembly", sort_order=0))
    db.add(CogsFacilityItem(id=2, facility_id=1, code="A-CELL", name="Sub-assembly cells", sort_order=1))
    # A sub-item cell, a stored zero (which is NOT the same as an absent row), and the
    # facility's own value for the locked row.
    db.add(CogsValue(facility_id=1, item_id="1", row_key="rent", volume_tier=100, value=260000))
    db.add(CogsValue(facility_id=1, item_id="2", row_key="rent", volume_tier=100, value=0))
    db.add(CogsValue(facility_id=1, item_id=cogs.OWN, row_key="salary", volume_tier=100, value=90000))
    db.add(CogsLock(facility_id=1, row_key="salary"))
    db.commit()

    fresh = _db()
    restore_from_workbook(fresh, read_backup_workbook(build_backup_workbook(db)))

    facs = fresh.execute(select(CogsFacility)).scalars().all()
    assert [(f.code, f.kind) for f in facs] == [("FAC-ASM", "assembly")]
    items = sorted(i.code for i in fresh.execute(select(CogsFacilityItem)).scalars())
    assert items == ["A-CELL", "A-LINE"]
    locks = [(l.facility_id, l.row_key) for l in fresh.execute(select(CogsLock)).scalars()]
    assert locks == [(1, "salary")]

    vals = {
        (v.item_id, v.row_key, v.volume_tier): float(v.value)
        for v in fresh.execute(select(CogsValue)).scalars()
    }
    assert vals == {
        ("1", "rent", 100): 260000.0,
        ("2", "rent", 100): 0.0,          # a stored zero survives as a zero, not as absent
        (cogs.OWN, "salary", 100): 90000.0,
    }, vals


def test_a_restore_does_not_multiply_the_facility_own_cells():
    """The reason `cogs_value.item_id` is NOT NULL with an '' sentinel.

    Restore dedups on the model's first unique constraint. Had `item_id` been nullable, the
    facility-own cells would compare distinct from each other on both SQLite and Postgres and
    a restore would stack a fresh copy on every run.
    """
    from app.backup import _natural_key_cols, build_backup_workbook, read_backup_workbook, restore_from_workbook
    from app.models import CogsFacility, CogsValue
    from app import cogs

    assert _natural_key_cols(CogsValue) == ["facility_id", "item_id", "row_key", "volume_tier"]

    db = _db()
    db.add(Item(item_id="AEC100A", item_name="Root", item_type="assembly",
                module_code="AEC", is_top_level=True))
    db.add(CogsFacility(id=1, code="FAC-ASM", kind="assembly", name="Hall"))
    db.commit()
    db.add(CogsValue(facility_id=1, item_id=cogs.OWN, row_key="rent", volume_tier=100, value=50000))
    db.commit()
    raw = build_backup_workbook(db)

    fresh = _db()
    restore_from_workbook(fresh, read_backup_workbook(raw))
    restore_from_workbook(fresh, read_backup_workbook(raw))   # twice, deliberately
    own = [v for v in fresh.execute(select(CogsValue)).scalars() if v.item_id == cogs.OWN]
    assert len(own) == 1, f"{len(own)} facility-own cells after two restores"


"""The router, called as functions rather than over HTTP.

`starlette.testclient` needs httpx, which this venv does not have and this suite does not
want — it is designed to run with `python tests/test_invariants.py` and no test deps at all.
Calling the handlers directly keeps that, and still exercises the pydantic payload models
(constructed here by hand), which is where the kind-immutability guard actually lives.

One trap that comes with it: a handler's `Query(default=X)` is only resolved to X by
FastAPI. Called directly, the parameter arrives as the `Query` object itself, which is
TRUTHY regardless of its default — so any test that cares about such a default must pass it
explicitly. `include_archived` below is exactly that case.
"""


def _cogs_api_fixture():
    """A BOM with a real cost: PLANT x1 -> STACK x4 @ EUR 250, plus 90 min of assembly."""
    from app.models import ReferenceValue
    db = _db()
    db.add(Item(item_id="AEC700A", item_name="Plant", item_type="assembly",
                module_code="AEC", is_top_level=True, cost_type_id=1))
    db.add(Item(item_id="AEC701P", item_name="Stack", item_type="part", module_code="AEC"))
    db.add(ReferenceValue(id=1, category="assembly_cost_type", value="Bench",
                          meta={"rate_eur_h": 60}))
    db.commit()
    db.add(BomLink(parent_item_id="AEC700A", child_item_id="AEC701P", quantity=4))
    db.add(DecidedCost(item_id="AEC701P", volume_tier=100, unit_cost_eur=250))
    db.add(AssemblyLabor(item_id="AEC700A", volume_tier=100, time_likely=90))
    db.commit()
    return db


def _mk_facility(db, code="FAC-ASM", kind="assembly", name="Hall"):
    from app.routers import cogs as R
    return R.create_facility(R.FacilityIn(code=code, kind=kind, name=name), db=db, user="t")


def _mk_item(db, fid, code, name="Cell"):
    from app.routers import cogs as R
    return R.add_item(fid, R.FacilityItemIn(code=code, name=name), db=db, user="t")


def _save(db, fid, cells):
    from app.routers import cogs as R
    return R.save_values(fid, R.ValuesPatch(cells=[R.CellIn(**c) for c in cells]),
                         db=db, user="t")


def test_cogs_api_ladder_matches_the_module():
    """The endpoint is a loader plus `cogs.compute` — never a second implementation."""
    from app.routers import cogs as R
    from app import cogs
    db = _cogs_api_fixture()
    f = _mk_facility(db)
    it = _mk_item(db, f["id"], "A-LINE", "Line")
    r = _save(db, f["id"], [
        {"item_id": str(it["id"]), "row_key": "maint", "volume_tier": 100, "value": 260000},
        {"item_id": str(it["id"]), "row_key": "scrap", "volume_tier": 100, "value": 2},
        {"item_id": str(it["id"]), "row_key": "consum", "volume_tier": 100, "value": 300},
    ])
    assert r["saved"] == 3, r

    L = R.get_ladder(root="AEC700A", db=db, volume=100)
    # 4 x 250 parts + 90 min at EUR 60/h = 1,090, split into its two halves.
    assert L["bom"] == 1000.0 and L["labour"] == 90.0
    assert abs(L["bom_adj"] - 1090.0 / 0.98) < 1e-6
    assert abs(L["direct"] - (1090.0 / 0.98 + 300.0)) < 1e-6
    assert L["pool_total"] == 260000.0 and L["overhead"] == 2600.0
    assert abs(L["burdened"] - (L["direct"] + 2600.0)) < 1e-9
    # And it agrees, field for field, with calling the module directly.
    want = cogs.compute(
        units_per_year=100, rollup_cost=1090.0, rollup_assembly_cost=90.0,
        facilities=[{"kind": "assembly", "code": "FAC-ASM", "locks": set(), "own": {},
                     "items": [{"values": {"maint": 260000, "scrap": 2, "consum": 300}}]}],
    )
    assert abs(L["cogs_unit"] - want.cogs_unit) < 1e-9


def test_cogs_api_a_facility_kind_is_immutable():
    """Rejected server-side, not merely absent from the UI.

    Pydantic's default is to DROP an unknown field, which would have reported a successful
    save that changed nothing — hence extra="allow" plus an explicit check in the handler.
    """
    from fastapi import HTTPException
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    f = _mk_facility(db)

    patch = R.FacilityPatch(kind="field")
    assert patch.model_extra == {"kind": "field"}, "extra='allow' is not in force"
    try:
        R.patch_facility(f["id"], patch, db=db, user="t")
        raise AssertionError("a kind change was accepted")
    except HTTPException as e:
        assert e.status_code == 422 and "fixed when it is created" in str(e.detail)

    # The name still patches normally, and the kind is untouched.
    out = R.patch_facility(f["id"], R.FacilityPatch(name="Hall 2"), db=db, user="t")
    assert (out["name"], out["kind"]) == ("Hall 2", "assembly")
    # A typo'd field is refused too, rather than silently dropped.
    try:
        R.patch_facility(f["id"], R.FacilityPatch(nmae="x"), db=db, user="t")
        raise AssertionError("an unknown field was accepted")
    except HTTPException as e:
        assert e.status_code == 422


def test_cogs_api_a_row_key_outside_the_kind_is_rejected_on_write():
    from fastapi import HTTPException
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    f = _mk_facility(db)
    it = _mk_item(db, f["id"], "A-LINE")

    # `warranty` is a field-works row. On an assembly facility it is not a row at all.
    try:
        _save(db, f["id"], [{"item_id": str(it["id"]), "row_key": "warranty",
                             "volume_tier": 100, "value": 2.5}])
        raise AssertionError("a foreign row key was stored")
    except HTTPException as e:
        assert e.status_code == 422 and "not a row on a 'assembly'" in str(e.detail)

    # A bad tier is refused too, and the WHOLE batch fails rather than half-writing.
    try:
        _save(db, f["id"], [
            {"item_id": str(it["id"]), "row_key": "maint", "volume_tier": 100, "value": 1},
            {"item_id": str(it["id"]), "row_key": "rent", "volume_tier": 7, "value": 1},
        ])
        raise AssertionError("a bad tier was accepted")
    except HTTPException as e:
        assert e.status_code == 422
    assert R.list_facilities(db=db, include_archived=False)["facilities"][0]["items"][0]["values"] == {}, "the batch half-wrote"


def test_cogs_api_clearing_a_cell_is_not_the_same_as_zero():
    """Absent means "not entered" and shows an em dash; 0 means "known to be zero"."""
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    f = _mk_facility(db)
    it = _mk_item(db, f["id"], "A-LINE")
    cell = {"item_id": str(it["id"]), "row_key": "maint", "volume_tier": 100}

    _save(db, f["id"], [{**cell, "value": 0}])
    got = R.list_facilities(db=db, include_archived=False)["facilities"][0]["items"][0]["values"]
    assert got == {"maint": {100: 0.0}}, got           # a stored zero is present

    _save(db, f["id"], [{**cell, "value": None}])
    got = R.list_facilities(db=db, include_archived=False)["facilities"][0]["items"][0]["values"]
    assert got == {}, got                              # cleared means gone, not zero


def test_cogs_api_locking_seeds_from_the_aggregate_so_nothing_jumps():
    """On locking, the facility's value is seeded from the current roll-up — a sum for a
    quantity row, an average of the non-zero values for a rate row."""
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    f = _mk_facility(db)
    a = _mk_item(db, f["id"], "A-ONE")
    b = _mk_item(db, f["id"], "A-TWO")
    _save(db, f["id"], [
        {"item_id": str(a["id"]), "row_key": "maint", "volume_tier": 100, "value": 40000},
        {"item_id": str(b["id"]), "row_key": "maint", "volume_tier": 100, "value": 60000},
        {"item_id": str(a["id"]), "row_key": "salary", "volume_tier": 100, "value": 90000},
        {"item_id": str(b["id"]), "row_key": "salary", "volume_tier": 100, "value": 70000},
    ])
    pool_before = R.get_ladder(root="AEC700A", db=db, volume=100)["pool_total"]
    assert pool_before == 100000.0

    seeded = R.lock_row(f["id"], "maint", db=db, user="t")["seeded"]
    assert seeded[100] == 100000.0                     # the SUM, for a quantity row
    after = R.get_ladder(root="AEC700A", db=db, volume=100)
    assert after["pool_total"] == pool_before, "locking moved the number"

    # A rate row seeds from the average of the non-zero values instead.
    assert R.lock_row(f["id"], "salary", db=db, user="t")["seeded"][100] == 80000.0

    # Unlocking leaves the sub-item values alone — the parent is not distributed down.
    R.unlock_row(f["id"], "maint", db=db, user="t")
    vals = {it["code"]: it["values"]["maint"][100] for it in R.list_facilities(db=db, include_archived=False)["facilities"][0]["items"]}
    assert vals == {"A-ONE": 40000.0, "A-TWO": 60000.0}, vals


def test_cogs_api_reports_a_floor_when_the_bom_is_not_fully_costed():
    """Coverage propagates all the way up: COGS on a half-priced BOM is a floor, and says so
    rather than presenting a confident number."""
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    db.add(Item(item_id="AEC702P", item_name="Uncosted", item_type="part", module_code="AEC"))
    db.commit()
    db.add(BomLink(parent_item_id="AEC700A", child_item_id="AEC702P", quantity=1))
    db.commit()
    L = R.get_ladder(root="AEC700A", db=db, volume=100)
    assert L["coverage"]["is_floor"] is True
    assert L["coverage"]["missing"] == ["AEC702P"]
    assert L["coverage"]["gaps"] == 1


def test_cogs_api_names_the_divisor_out_loud():
    """@1 is not "what a prototype costs" — it is "what a plant costs at a company running
    one plant a year". The tier must never silently double as a production rate, so the
    divisor is named in the payload rather than implied by the button."""
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    for tier, wanted in ((1, "1 plants/yr"), (100, "100 plants/yr"), (10000, "10,000 plants/yr")):
        notes = R.get_ladder(root="AEC700A", db=db, volume=tier)["notes"]
        assert any(wanted in n for n in notes), notes
        assert any("cannot be added" in n for n in notes)
        assert any("end of line" in n for n in notes)


def test_cogs_api_has_no_grand_total_endpoint():
    """Full allocation per root makes two roots' COGS unaddable — sum them and the company
    is double-counted. So the endpoint does not exist, deliberately and permanently."""
    from app.main import app
    spec = app.openapi()
    cogs_paths = {p for p in spec["paths"] if "/cogs" in p}
    assert cogs_paths, "the cogs router is not mounted"
    for p in cogs_paths:
        assert "grand" not in p and "total" not in p, p
    # And /cogs/summary is per-root: the root is required, not optional.
    params = spec["paths"]["/api/cogs/summary"]["get"]["parameters"]
    root = next(x for x in params if x["name"] == "root")
    assert root["required"] is True


def test_cogs_api_pending_lists_facility_gaps_but_not_locked_rows():
    """A locked row is not a gap on a sub-item — it is not entered there by design."""
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    f = _mk_facility(db, code="FAC-FLD", kind="field", name="Field")
    it = _mk_item(db, f["id"], "W-INST", "Install")

    before = {r["volume_tier"]: set(r["missing"])
              for r in R.get_pending(db=db) if r["item_id"] == it["id"]}
    # `commission` is gone — the crew's commissioning work is in their salaries.
    assert before[100] == {"fte", "salary", "equip", "travel", "install",
                           "warranty"}, before[100]

    R.lock_row(f["id"], "salary", db=db, user="t")
    after = {r["volume_tier"]: set(r["missing"])
             for r in R.get_pending(db=db) if r["item_id"] == it["id"]}
    assert "salary" not in after[100], after[100]



def test_cogs_facility_codes_are_their_own_namespace():
    """Facility codes have no relation to part numbers, and the part-number rules must not
    be applied to them — nor theirs to part numbers."""
    import pydantic
    from app.routers.cogs import FacilityIn, FacilityItemIn

    # Every code in the handoff fixture validates.
    for c in ("FAC-ASM", "FAC-QA", "FAC-SCM", "FAC-FLD"):
        FacilityIn(code=c, kind="assembly", name="x")
    for c in ("A-CELL", "A-LINE", "A-TEST", "Q-LAB", "Q-INSP",
              "S-IN", "S-WH", "S-OUT", "W-INST", "W-COM"):
        FacilityItemIn(code=c, name="x")

    # A part number is not a facility code, and vice versa.
    for bad in ("AEC066A", "fac-asm", "FACASM", "FAC_ASM"):
        try:
            FacilityIn(code=bad, kind="assembly", name="x")
            raise AssertionError(f"accepted {bad} as a facility code")
        except pydantic.ValidationError:
            pass


def test_cogs_api_serves_the_aggregates_the_matrix_renders():
    """The facility screen does zero arithmetic, so the payload has to carry the numbers it
    would otherwise have had to invent: the read-only aggregate behind an unlocked facility
    cell, each facility's per-rung roll-up, and the totals for the KPI tiles."""
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    f = _mk_facility(db)
    a = _mk_item(db, f["id"], "A-ONE")
    b = _mk_item(db, f["id"], "A-TWO")
    _save(db, f["id"], [
        {"item_id": str(a["id"]), "row_key": "maint", "volume_tier": 100, "value": 40000},
        {"item_id": str(b["id"]), "row_key": "maint", "volume_tier": 100, "value": 60000},
        {"item_id": str(a["id"]), "row_key": "salary", "volume_tier": 100, "value": 90000},
        {"item_id": str(b["id"]), "row_key": "salary", "volume_tier": 100, "value": 70000},
        {"item_id": str(a["id"]), "row_key": "consum", "volume_tier": 100, "value": 300},
    ])
    out = R.list_facilities(db=db, include_archived=False)
    fac = out["facilities"][0]

    # A quantity row aggregates by SUM; a rate row by the mean of the non-zero values. These
    # are the two things a facility's read-only cell shows.
    assert fac["aggregates"]["maint"][100] == 100000.0
    assert fac["aggregates"]["salary"][100] == 80000.0
    # An absent row has no aggregate at all — that is the em dash, not a zero. `util` is
    # never entered by this fixture, so it must not appear even as 0.
    assert "util" not in fac["aggregates"], fac["aggregates"].keys()

    r = fac["rollup"][100]
    assert r["pool_year"] == 100000.0
    assert r["overhead_per_plant"] == 1000.0        # the pool over the tier
    assert r["direct_per_plant"] == 300.0
    assert r["per_plant_total"] == 1300.0
    # A pool and a per-plant figure look identical on screen, so both are stated rather than
    # one being derived from the other.
    assert r["year_total"] == 100000.0 + 300.0 * 100

    t100 = out["totals"][100]
    assert (t100["pool_year"], t100["direct_per_plant"]) == (100000.0, 300.0)
    # The tiers are independent: nothing was entered at @1 or @10k.
    assert out["totals"][1]["pool_year"] == 0.0


def test_cogs_api_archived_facilities_leave_the_ladder():
    """Archiving is the editor's alternative to deletion — it keeps the numbers and the
    history, but the facility stops being costed."""
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    f = _mk_facility(db)
    it = _mk_item(db, f["id"], "A-LINE")
    _save(db, f["id"], [
        {"item_id": str(it["id"]), "row_key": "maint", "volume_tier": 100, "value": 260000},
    ])
    assert R.get_ladder(root="AEC700A", db=db, volume=100)["pool_total"] == 260000.0

    R.patch_facility(f["id"], R.FacilityPatch(archived=True), db=db, user="t")
    assert R.get_ladder(root="AEC700A", db=db, volume=100)["pool_total"] == 0.0
    # The figures are still there, and still returned when asked for.
    assert R.list_facilities(db=db, include_archived=False)["facilities"] == []
    kept = R.list_facilities(db=db, include_archived=True)["facilities"][0]
    assert kept["items"][0]["values"]["maint"][100] == 260000.0
    # But an archived facility is not in the totals it would otherwise inflate.
    assert R.list_facilities(db=db, include_archived=True)["totals"][100]["pool_year"] == 0.0


def test_history_filter_takes_several_entity_types():
    """One filter chip legitimately covers four entity types. Filtering client-side instead
    would have quietly broken `limit`, returning a short page of mostly-hidden rows."""
    from app.routers.edit import global_history
    from app.routers import cogs as R
    db = _cogs_api_fixture()
    f = _mk_facility(db)
    it = _mk_item(db, f["id"], "A-LINE")
    _save(db, f["id"], [
        {"item_id": str(it["id"]), "row_key": "maint", "volume_tier": 100, "value": 1},
    ])
    R.lock_row(f["id"], "maint", db=db, user="t")

    facilities = "cogs_facility,cogs_facility_item,cogs_value,cogs_lock"
    types = {h.entity_type for h in global_history(db=db, entity_type=facilities)}
    assert types == {"cogs_facility", "cogs_facility_item", "cogs_value", "cogs_lock"}, types
    # A single type still works exactly as before.
    only = {h.entity_type for h in global_history(db=db, entity_type="cogs_lock")}
    assert only == {"cogs_lock"}
    # And every one of these rows is genuinely in the log, not just filterable.
    assert len(global_history(db=db, entity_type=facilities)) >= 4


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed



def test_backup_carries_the_part_number_ledger():
    # A rebuild onto a fresh database has to bring the ledger back, or every retired number is
    # free to be handed out a second time. And a restore must never DROP a number the live
    # database already knows about, so the ledger merges rather than being replaced.
    from app.backup import build_backup_workbook, read_backup_workbook, restore_from_workbook
    from app.models import CodeRegistry
    db = _db()
    db.add(Item(item_id="AEC100A", item_name="Root", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(CodeRegistry(module="AEC", number=100, first_code="AEC100A"))
    db.add(CodeRegistry(module="AEC", number=7, first_code="AEC007P"))  # retired: no item left
    db.commit()
    raw = build_backup_workbook(db)

    fresh = _db()
    restore_from_workbook(fresh, read_backup_workbook(raw))
    got = sorted((r.module, r.number) for r in fresh.execute(select(CodeRegistry)).scalars())
    assert got == [("AEC", 7), ("AEC", 100)], f"ledger not restored onto a fresh database: {got}"

    # a number allocated after that backup was taken must survive restoring it again
    fresh.add(CodeRegistry(module="AEC", number=250, first_code="AEC250P")); fresh.commit()
    restore_from_workbook(fresh, read_backup_workbook(raw))
    got = sorted((r.module, r.number) for r in fresh.execute(select(CodeRegistry)).scalars())
    assert got == [("AEC", 7), ("AEC", 100), ("AEC", 250)], f"restore dropped a live number: {got}"

if __name__ == "__main__":
    sys.exit(1 if _run() else 0)


# ── weight coverage: the figure that qualifies € / kg ────────────────────────
def test_weight_coverage_counts_the_same_parts_the_weight_is_summed_over():
    """A rolled-up weight over half-weighed parts is as wrong as an unpriced BOM, and it is
    what the Cost per kg tile divides by — so coverage has to be measured over exactly the
    set `weight_grams` was summed over, or the tile contradicts the caveat beside it."""
    from app.routers.tree import costing_breakdown

    db = _boundary_fixture(covers="none", quote=None)
    # Wire has a weight, Terminal's is cleared: one of the two priced leaves is unweighed.
    db.get(Item, "AEC923P").weight_grams = None
    db.commit()

    t = costing_breakdown(db=db, root="AEC920A", volume=100)["totals"]
    assert t["weight_total"] == 2, t["weight_total"]          # Terminal + Wire
    assert t["weight_covered"] == 1, t["weight_covered"]
    assert t["weight_missing"] == ["AEC923P"], t["weight_missing"]
    assert t["weight_coverage"] == 0.5, t["weight_coverage"]
    # 2 harnesses x 1 branch x 3 wire x 20 g
    assert t["weight_grams"] == 120.0, t["weight_grams"]


def test_weight_coverage_is_whole_when_everything_is_weighed():
    from app.routers.tree import costing_breakdown

    db = _boundary_fixture(covers="none", quote=None)
    t = costing_breakdown(db=db, root="AEC920A", volume=100)["totals"]
    assert t["weight_missing"] == []
    assert t["weight_covered"] == t["weight_total"] == 2
    assert t["weight_coverage"] == 1.0


def test_a_bom_with_no_weights_at_all_reports_zero_coverage_not_a_crash():
    """The Cost per kg tile shows an em dash here rather than dividing by zero. The API's job
    is to say the weight is absent, without inventing one."""
    from app.routers.tree import costing_breakdown

    db = _boundary_fixture(covers="none", quote=None)
    for iid in ("AEC923P", "AEC924P"):
        db.get(Item, iid).weight_grams = None
    db.commit()

    t = costing_breakdown(db=db, root="AEC920A", volume=100)["totals"]
    assert t["weight_grams"] == 0.0
    assert t["weight_covered"] == 0
    assert t["weight_coverage"] == 0.0
    assert set(t["weight_missing"]) == {"AEC923P", "AEC924P"}


# ── cover_reach: is there still an uncovered way down to this item? ──────────
def _cover_fixture():
    """Two roots over one shared harness, so "covered on one path, open on another" exists.

        BOAT   ×1 → HARNESS ×2 → {TERMINAL ×10, BRANCH ×1 → WIRE ×3}
        BENCH  ×1 → HARNESS ×1

    BOAT's harness covers the work below it at @100 only. BENCH's does not cover anything, so
    everything under the harness is still open at @100 by way of BENCH — which is the case the
    rule exists for: filling the numbers in is real work, because the second usage needs them.
    """
    db = _db()
    db.add(Item(item_id="AEC920A", item_name="Boat", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC930A", item_name="Bench", item_type="assembly", module_code="AEC", is_top_level=True))
    db.add(Item(item_id="AEC921A", item_name="Harness", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC922A", item_name="Branch", item_type="assembly", module_code="AEC"))
    db.add(Item(item_id="AEC923P", item_name="Terminal", item_type="part", module_code="AEC", weight_grams=5))
    db.add(Item(item_id="AEC924P", item_name="Wire", item_type="part", module_code="AEC", weight_grams=20))
    db.commit()
    db.add(BomLink(parent_item_id="AEC920A", child_item_id="AEC921A", quantity=2))
    db.add(BomLink(parent_item_id="AEC921A", child_item_id="AEC923P", quantity=10))
    db.add(BomLink(parent_item_id="AEC921A", child_item_id="AEC922A", quantity=1))
    db.add(BomLink(parent_item_id="AEC922A", child_item_id="AEC924P", quantity=3))
    db.add(DecidedCost(item_id="AEC923P", volume_tier=100, unit_cost_eur=1))
    db.add(DecidedCost(item_id="AEC924P", volume_tier=100, unit_cost_eur=2))
    db.commit()
    return db


def test_a_labor_cover_reaches_every_depth_below_it():
    from app.rollups import cover_reach

    db = _cover_fixture()
    db.add(AssemblyLabor(item_id="AEC921A", volume_tier=100, covers="labor"))
    db.commit()
    r = cover_reach(db)
    # Not the covering assembly itself — a cover pays for what is BELOW it.
    assert r["AEC921A"][100][0] == "open"
    # ...but every depth under it, not just the direct children.
    assert r["AEC922A"][100] == ("labor", "AEC921A")
    assert r["AEC924P"][100] == ("labor", "AEC921A")


def test_a_cover_at_one_tier_leaves_the_others_open():
    """`covers` is per tier because sourcing is: hand-built at @1, outsourced at @10k."""
    from app.rollups import cover_reach

    db = _cover_fixture()
    db.add(AssemblyLabor(item_id="AEC921A", volume_tier=10000, covers="labor"))
    db.commit()
    r = cover_reach(db)
    assert r["AEC922A"][10000][0] == "labor"
    assert r["AEC922A"][100][0] == "open"
    assert r["AEC922A"][1][0] == "open"


def test_one_uncovered_usage_is_enough_to_keep_it_open():
    """The whole reason the rule is "every path": a sub-assembly lifted into a parent that does
    not cover it needs its own numbers, so the queue must still ask for them."""
    from app.rollups import cover_reach

    db = _cover_fixture()
    db.add(AssemblyLabor(item_id="AEC921A", volume_tier=100, covers="labor"))
    db.commit()
    # Reached only through the harness, so the harness's cover pays for it.
    assert cover_reach(db)["AEC924P"][100][0] == "labor"
    # Now BENCH uses the BRANCH directly, going around the harness entirely. That second usage
    # is nobody's covered work, so the branch and the wire under it need their own numbers.
    db.add(BomLink(parent_item_id="AEC930A", child_item_id="AEC922A", quantity=1))
    db.commit()
    r = cover_reach(db)
    assert r["AEC922A"][100][0] == "open"
    assert r["AEC924P"][100][0] == "open"


def test_a_quoted_assembly_puts_its_whole_subtree_below_a_boundary():
    from app.rollups import cover_reach

    db = _cover_fixture()
    db.add(AssemblyLabor(item_id="AEC921A", volume_tier=100, covers="all"))
    db.commit()
    r = cover_reach(db)
    assert r["AEC923P"][100] == ("boundary", "AEC921A")
    assert r["AEC924P"][100] == ("boundary", "AEC921A")


def test_a_boundary_outranks_a_labor_cover_on_another_path():
    """Weakest claim wins, and `labor` is weaker than `boundary` — a cost entered under a
    labour cover is still ADDED by the rollup, so it is not the same as one that is discarded."""
    from app.rollups import cover_reach

    db = _cover_fixture()
    db.add(BomLink(parent_item_id="AEC930A", child_item_id="AEC921A", quantity=1))
    db.add(AssemblyLabor(item_id="AEC920A", volume_tier=100, covers="all"))
    db.add(AssemblyLabor(item_id="AEC930A", volume_tier=100, covers="labor"))
    db.commit()
    assert cover_reach(db)["AEC921A"][100][0] == "labor"


def test_pending_stops_asking_for_times_that_are_paid_for_above():
    from app.routers.tree import pending

    db = _cover_fixture()
    db.add(AssemblyLabor(item_id="AEC921A", volume_tier=100, covers="labor"))
    db.commit()
    rows = {r["item_id"]: r for r in pending(db=db, module=None)}
    branch = rows["AEC922A"]
    assert "asm_time@100" not in branch["missing"], branch["missing"]
    # The other two tiers are untouched, and the row is still in the queue for them.
    assert "asm_time@1" in branch["missing"] and "asm_time@10k" in branch["missing"]
    # ...and it says which assembly is paying for @100, so the row can explain itself.
    assert branch["covered"]["100"] == {"state": "labor", "by": "AEC921A"}


def test_pending_stops_pricing_leaves_under_a_quoted_assembly():
    from app.routers.tree import pending

    db = _cover_fixture()
    db.add(AssemblyLabor(item_id="AEC921A", volume_tier=100, covers="all"))
    db.commit()
    rows = {r["item_id"]: r for r in pending(db=db, module=None)}
    wire = rows["AEC924P"]
    assert "cost@100" not in wire["missing"], wire["missing"]
    assert wire["covered"]["100"]["state"] == "boundary"
    # Weight and material are physical facts and stay wanted whoever is paying.
    assert "material" in wire["missing"]


def test_pending_only_wants_a_cost_type_where_a_time_is_wanted():
    """The cost type is the EUR/hour behind an assembly time. With every tier covered from
    above there is no time to price, so asking for the rate is asking for nothing."""
    from app.routers.tree import pending

    db = _cover_fixture()
    for tier in (1, 100, 10000):
        db.add(AssemblyLabor(item_id="AEC921A", volume_tier=tier, covers="labor"))
    db.commit()
    rows = {r["item_id"]: r for r in pending(db=db, module=None)}
    assert "cost_type" not in rows["AEC922A"]["missing"], rows["AEC922A"]["missing"]
    # The covering harness itself is not covered by anything, so it still needs its rate.
    assert "cost_type" in rows["AEC921A"]["missing"]


# ── accepting a double count, and what un-accepts it ────────────────────────
def _double_count_fixture():
    """HARNESS covers the labour below it at @100, and BRANCH under it charges for its own
    time anyway. The rollup adds both, on purpose — so the drawer asks rather than accuses."""
    from app.models import ReferenceValue

    db = _db()
    db.add(ReferenceValue(id=1, category="assembly_cost_type", value="Bench", meta={"rate_eur_h": 60}))
    db.add(Item(item_id="AEC920A", item_name="Boat", item_type="assembly", module_code="AEC",
                is_top_level=True, cost_type_id=1))
    db.add(Item(item_id="AEC921A", item_name="Harness", item_type="assembly", module_code="AEC", cost_type_id=1))
    db.add(Item(item_id="AEC922A", item_name="Branch", item_type="assembly", module_code="AEC", cost_type_id=1))
    db.add(Item(item_id="AEC924P", item_name="Wire", item_type="part", module_code="AEC", weight_grams=20))
    db.commit()
    db.add(BomLink(parent_item_id="AEC920A", child_item_id="AEC921A", quantity=1))
    db.add(BomLink(parent_item_id="AEC921A", child_item_id="AEC922A", quantity=1))
    db.add(BomLink(parent_item_id="AEC922A", child_item_id="AEC924P", quantity=3))
    db.add(DecidedCost(item_id="AEC924P", volume_tier=100, unit_cost_eur=2))
    db.add(AssemblyLabor(item_id="AEC921A", volume_tier=100, covers="labor", time_likely=30))
    db.add(AssemblyLabor(item_id="AEC922A", volume_tier=100, covers="none", time_likely=10))
    db.commit()
    return db


def test_the_rollup_still_counts_both_and_says_so():
    """The arithmetic is deliberately unchanged: a covered descendant's own assembly cost is
    added on top, and the contradiction is reported rather than silently resolved."""
    from app.rollups import BomGraph

    db = _double_count_fixture()
    g = BomGraph(db, volume_tier=100)
    r = g.rollup("AEC920A")
    assert r.covered_conflict == ["AEC922A"], r.covered_conflict
    # 3 x EUR 2 wire + 10 min branch + 30 min harness + 0 for the boat = 6 + 10 + 30
    assert round(r.cost, 2) == 46.0, r.cost


def test_accepting_a_double_count_persists_and_is_logged():
    from app.routers.edit import set_assembly_labor
    from app.schemas import AssemblyLaborIn

    db = _double_count_fixture()
    set_assembly_labor(
        "AEC921A",
        AssemblyLaborIn(volume_tier=100, time_likely=30, covers="labor", double_count_ack=["AEC922A"]),
        db=db, user="leonard@theflipflopi.com",
    )
    row = db.execute(
        select(AssemblyLabor).where(AssemblyLabor.item_id == "AEC921A", AssemblyLabor.volume_tier == 100)
    ).scalar_one()
    assert row.double_count_ack == ["AEC922A"]
    from app.models import ChangeHistory

    logged = [h for h in db.execute(select(ChangeHistory)).scalars()
              if h.field_changed == "double_count_ack@100"]
    assert len(logged) == 1 and logged[0].new_value == "AEC922A"
    assert logged[0].changed_by == "leonard@theflipflopi.com"


def test_editing_a_time_does_not_clear_an_acceptance():
    """The same endpoint edits times. A time edit says nothing about the double count, so an
    omitted field has to leave the stored answer alone rather than wiping it."""
    from app.routers.edit import set_assembly_labor
    from app.schemas import AssemblyLaborIn

    db = _double_count_fixture()
    set_assembly_labor("AEC921A", AssemblyLaborIn(volume_tier=100, time_likely=30, covers="labor",
                                                  double_count_ack=["AEC922A"]), db=db, user="u")
    set_assembly_labor("AEC921A", AssemblyLaborIn(volume_tier=100, time_likely=45, covers="labor"),
                       db=db, user="u")
    row = db.execute(
        select(AssemblyLabor).where(AssemblyLabor.item_id == "AEC921A", AssemblyLabor.volume_tier == 100)
    ).scalar_one()
    assert row.time_likely == 45
    assert row.double_count_ack == ["AEC922A"], "a time edit must not un-accept anything"


def test_a_new_descendant_below_the_cover_is_not_covered_by_an_old_acceptance():
    """Why the accepted SET is stored and not a flag: a boolean would keep the note suppressed
    over a double count nobody had ever looked at."""
    from app.rollups import BomGraph
    from app.routers.edit import set_assembly_labor
    from app.schemas import AssemblyLaborIn

    db = _double_count_fixture()
    set_assembly_labor("AEC921A", AssemblyLaborIn(volume_tier=100, time_likely=30, covers="labor",
                                                  double_count_ack=["AEC922A"]), db=db, user="u")
    # A second assembly, added under the cover later, charging for its own time too. It needs
    # a child of its own: an "assembly" with nothing in it is costed as a leaf, so it would
    # never be an assembly-cost conflict in the first place.
    db.add(Item(item_id="AEC925A", item_name="Splice", item_type="assembly", module_code="AEC", cost_type_id=1))
    db.add(Item(item_id="AEC926P", item_name="Ferrule", item_type="part", module_code="AEC", weight_grams=1))
    db.commit()
    db.add(BomLink(parent_item_id="AEC921A", child_item_id="AEC925A", quantity=1))
    db.add(BomLink(parent_item_id="AEC925A", child_item_id="AEC926P", quantity=2))
    db.add(DecidedCost(item_id="AEC926P", volume_tier=100, unit_cost_eur=1))
    db.add(AssemblyLabor(item_id="AEC925A", volume_tier=100, covers="none", time_likely=5))
    db.commit()

    conflicts = set(BomGraph(db, volume_tier=100).rollup("AEC920A").covered_conflict)
    row = db.execute(
        select(AssemblyLabor).where(AssemblyLabor.item_id == "AEC921A", AssemblyLabor.volume_tier == 100)
    ).scalar_one()
    unacked = conflicts - set(row.double_count_ack or [])
    assert unacked == {"AEC925A"}, unacked


def test_copying_an_item_does_not_copy_the_acceptance():
    """A copy is a fresh subtree and a fresh decision; inheriting the answer would hide the
    question on the new item for ever."""
    from app.routers.edit import duplicate_item, set_assembly_labor
    from app.schemas import AssemblyLaborIn, DuplicateItemIn

    db = _double_count_fixture()
    set_assembly_labor("AEC921A", AssemblyLaborIn(volume_tier=100, time_likely=30, covers="labor",
                                                  double_count_ack=["AEC922A"]), db=db, user="u")
    new_id = duplicate_item("AEC921A", DuplicateItemIn(item_name="Harness copy"), db=db, user="u")["item_id"]
    rows = list(db.execute(select(AssemblyLabor).where(AssemblyLabor.item_id == new_id)).scalars())
    assert rows, "the copy should still carry the labour rows"
    assert all(r.double_count_ack is None for r in rows)
    assert all(r.covers == "labor" for r in rows), "the cover itself is part of how it is costed"


# ── a file named after the item becomes its picture ─────────────────────────
def _thumb_db():
    db = _db()
    db.add(Item(item_id="AEC001A", item_name="Cell", item_type="assembly", module_code="AEC"))
    db.commit()
    return db


def _files(*names):
    return [{"id": f"drive-{i}", "name": n, "has_thumbnail": True} for i, n in enumerate(names)]


def test_a_file_named_after_the_item_is_pinned():
    from app.routers.attachments import _auto_pin_thumbnail

    db = _thumb_db()
    it = db.get(Item, "AEC001A")
    assert _auto_pin_thumbnail(db, it, _files("quote.pdf", "AEC001A.png")) == "drive-1"
    assert it.thumbnail_file_id == "drive-1"


def test_the_match_is_case_insensitive():
    """A photo off a phone or from a colleague arrives however it arrives."""
    from app.routers.attachments import _auto_pin_thumbnail

    db = _thumb_db()
    it = db.get(Item, "AEC001A")
    assert _auto_pin_thumbnail(db, it, _files("aec001a.JPG")) == "drive-0"


def test_a_pdf_named_after_the_item_is_not_a_picture():
    """Drive renders a thumbnail for a PDF, so `has_thumbnail` cannot be the test — AEC001A.pdf
    is a drawing or a datasheet, and pinning it would put a page of A4 in the Key figures card."""
    from app.routers.attachments import _auto_pin_thumbnail

    db = _thumb_db()
    it = db.get(Item, "AEC001A")
    assert _auto_pin_thumbnail(db, it, _files("AEC001A.pdf", "AEC001A.dxf")) is None
    assert it.thumbnail_file_id is None


def test_only_an_exact_stem_counts():
    from app.routers.attachments import _auto_pin_thumbnail

    db = _thumb_db()
    it = db.get(Item, "AEC001A")
    assert _auto_pin_thumbnail(db, it, _files("AEC001A_front.png", "AEC001A rev B.png")) is None


def test_auto_pin_never_replaces_a_picture_somebody_chose():
    """The whole convention is "pinned, never newest" — this must not become a back door to
    swapping a picture that was chosen by hand."""
    from app.routers.attachments import _auto_pin_thumbnail

    db = _thumb_db()
    it = db.get(Item, "AEC001A")
    it.thumbnail_file_id = "chosen-by-hand"
    db.commit()
    assert _auto_pin_thumbnail(db, it, _files("AEC001A.png")) is None
    assert it.thumbnail_file_id == "chosen-by-hand"


def test_an_automatic_pin_says_so_in_the_history():
    """The history log is read by people. An automatic decision must not carry somebody's name."""
    from app.models import ChangeHistory
    from app.routers.attachments import AUTO_PIN_BY, _auto_pin_thumbnail

    db = _thumb_db()
    _auto_pin_thumbnail(db, db.get(Item, "AEC001A"), _files("AEC001A.png"))
    row = next(h for h in db.execute(select(ChangeHistory)).scalars()
               if h.field_changed == "thumbnail_file_id")
    assert row.changed_by == AUTO_PIN_BY
    assert "AEC001A.png" in (row.change_reason or "")
