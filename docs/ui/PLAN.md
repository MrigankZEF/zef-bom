# UI fixes — implementation plan

Source of truth for *what* and *why*: `docs/ui/TODO.md`, items 1–15. This file is only the
*order*, the *grouping*, and what each commit has to prove before the next one starts.

Branch: `bom-ui-fixes`, cut from `origin/main` at `6eb9db2`, with the COGS ladder merged in as
`df75e79`. Items 3 and 4 arrived upstream in `6eb9db2` and are closed.

Migrations `0001`–`0015` exist. This plan claims **`0016`–`0018`**, in the order the phases
below run — so nothing renumbers if a phase slips.

## Where this has got to (2026-09-09)

| Phase | State |
| --- | --- |
| D3 · Pending's chips from the data | done — `1a6f981` |
| B1 · weight coverage + € / kg | done — `f9fccf1` |
| C1 · one volume tier | done — `7f76a4b` |
| A · treemap areas and colour | done — `1680aee`, as ONE commit; see its message for why |
| D1 · queue stops asking for covered numbers | done — `7d0666c` |
| D2 · the double-count note, and accepting it | done — `8e5a05c`, migration `0016` |
| E · thumbnails pin themselves | done — `c11a9fb` |
| F1 · close the change_history holes | done — `71045fd` |
| I1 · undo from the History tab | done — `2a386e9` |
| B2 · cost as a distribution | done — `0ee6165`; Uploads moved to Admin in `8f74398` |
| G · milestones and diff | **next** — claims migration `0017` |
| H · import duty structure | not started — claims migration `0018` |

Phase A grew past its plan while being reviewed on the real BOM: colour by branch, the
hover ring, the title-collision handling and the flyout's item count all came out of looking
at it, and none of them were in A1 or A2. That is the working rule below doing its job, not
scope creep — but it is why A is one commit and not two.

## Working rule: run it before committing

Each phase is **built and run locally first**, checked against the real BOM in the browser, and
only then committed. The commit boundaries below are the intended shape of the history, not
permission to commit unverified work. Expect the review pass to change details in every phase —
several of these items are taste calls that only resolve when seen on real data (A1's borders,
B1's placement of the weight coverage line, D1's two cover messages).

---

## How the work is grouped

Five of the fifteen items are not independent, and the grouping is built around those:

| Coupling | Why |
| --- | --- |
| 9 → 2 | Stripping the treemap's chrome removes most of what distorts its areas. Fixing the area maths first means doing it twice. |
| 13 + 14 | € / kg divides by a weight whose completeness is currently invisible. Shipping the figure without the coverage line ships a number that can be silently wrong. |
| 5 + 6 | Decided in TODO §5–6: the roll-up keeps double-counting, the queue stops asking, and the drawer's note is the only thing standing between the two. The note is load-bearing, not cosmetic. |
| 6 → 15 | Deriving Pending's chips from the data is pointless while the data still contains gaps nothing can close. |
| 11 → 10 | Reconstruction from `change_history` is only trustworthy once the three unlogged write paths are closed. Phase G builds the seam that lets it arrive later. |
| A → G5 | Two treemaps side by side halve the width each gets. Doing that before the chrome and area work means the comparison view pays for decoration. |

Everything else is independent and is ordered small-and-visible first, so the branch is
useful early rather than only at the end.

---

## Phase A — the treemap tells the truth about area

**A1 · Strip the card chrome** (TODO §9)

Square corners, drop the nested indent, drop the coloured stub before assembly titles.

- `CostTreemap.jsx` — `cardR` (`:52`) and its use (`:373`); `framePad` (`:53`) and its use
  (`:213`); the title stub (`:376-377`); leaf `rx` (`:408`); legend swatch `rx` (`:445`).
- Depth still reads afterwards from `cardWash` (`:49`), `headFont` (`:45`) and `cardLine`
  (`:51`) — three signals, which is why the fourth and fifth can go.
- Proof: a seven-level BOM on screen, before and after. No unit test — this is taste, and a
  test would only pin the taste in place. Whether a child tile touching the parent's border
  reads as one box or two is decided here, looking at it.

**A2 · Areas become proportional to cost** (TODO §2)

Measured today: 1.22× between two €1.50 parts one nesting level apart, 1.31× across the leaf
set, 17% of the canvas carrying no value.

- Reserve chrome *before* sizing: a card's children are laid out at
  `value × (inner area / outer area)`, so the interior scale matches the parent's.
- `INSET` (`:54`) becomes proportional or goes.
- The 3% assembly-work floor (`:194`) must stop dropping value after the card was sized on it —
  either keep the sliver or re-normalise the parent.
- Proof: a real test. Port the measuring harness used to find this and assert max/min px² per €
  across every leaf stays within a few percent, over a spread of depths and box shapes.
  `6eb9db2` set the precedent — it checked 432 layouts — and the same standard applies here.

*A1 and A2 touch the same twenty lines. A1 first, because it deletes some of them.*

---

## Phase B — the Costing KPI row

**B1 · Weight coverage, and € / kg** (TODO §13 + §14)

- `tree.py:222-235` — expose `weight_covered` / `weight_total` / `weight_missing` in `totals`,
  mirroring the cost coverage fields already there. `Rollup.weight_missing` is already
  maintained; this is exposure, not new arithmetic.
- `Costing.jsx:133-137` — "Most expensive" becomes € / kg. Unit fixed at kg regardless of
  magnitude, so two BOMs stay comparable. `—` when weight is zero.
- `Costing.jsx:127-131` — the weight coverage line. Whether it sits on the Coverage tile or the
  Total weight tile is decided on screen; the second reads better on paper.
- `topPart` (`:56`) becomes dead — remove it.
- Proof: an API test that a BOM with an unweighed part reports `weight_missing`, and that
  € / kg is `—` at zero weight.

**B2 · Cost as a distribution** (TODO §1)

The three-point estimate is already rolled up and already on the KPI sub-line as text since
`6eb9db2`. This draws it.

- A small inline SVG curve: peak at most-likely, width from `cost_min`–`cost_max`.
- Degenerate cases first, not last: `min == max` (no spread), and a subtree only partly
  three-point costed.
- KPI tile only. Whether every tree row gets one is a separate decision and a separate commit.
- Proof: on screen, plus a unit test that the path is well-formed when min == max.

---

## Phase C — one volume tier (TODO §8)

**C1 · Lift the tier, delete the second control**

- `TIERS` / `DEFAULT_TIER` / `tierLabel` are declared three times (`Tree.jsx:5-9`,
  `PartDrawer.jsx:6-10`, `Costing.jsx:6-9`). One module.
- Tier state moves to `App.jsx:29`. `Tree.jsx:20`, `PartDrawer.jsx:70` and `Costing.jsx:14`
  stop owning it.
- `PartDrawer.jsx:734` drops `<TierToggle>`; the component at `:609` goes with it.
- `Costing.jsx:84-86` keeps its buttons, bound to the shared value.
- `Tree.jsx:187-196` — the surviving control gets bigger and says what it is.
- No `localStorage`. Every load starts at 10k.
- Proof: manual. Browse → Costing keeps the tier; the drawer cannot diverge from the tree
  because there is nothing left to diverge.

---

## Phase D — what a cover means

**D1 · The queue stops asking for what nothing will use** (TODO §6)

The rule: an item is free of a gap only if **every** path to it from a live top-level root is
covered. Any uncovered path and it is a real gap, per (item, tier).

- `rollups.py:18` `top_level_reachable` already walks live roots top-down. Extend that walk to
  carry a per-tier "covered from above" flag and record whether any path arrived uncovered. One
  pass, BOM-wide — which is what `/pending` needs and a single-rooted `BomGraph` cannot give.
- `tree.py:285-303` uses that instead of the item's own `covers`. `:269`'s `linked` filter and
  `:303`'s `cost_type` guard both become ancestry-aware.
- Rows do not disappear. A covered row greys out and links to the assembly covering it. Two
  messages, because the two cover kinds differ: below `covers='all'` nothing changes any total
  ("nothing to fill here"); below `covers='labor'` the roll-up still adds it, so the message is
  *"covered by AEC002A — a time entered here is added on top"*.
- The drawer keeps showing an empty labour figure regardless of today's cover. That is the whole
  point — the sub-assembly has to make sense lifted into a parent that does not cover it.
- Proof: a test per case — covered at one tier and not another; used twice, covered once; below
  a boundary. `test_labor_cover_still_rolls_up_the_parts` (`test_invariants.py:563`) asserts
  `r.cost == 16.0` and must stay green, because the arithmetic is deliberately unchanged.

**D2 · The warning becomes a question, and can be answered** (TODO §5) — migration `0016`

- `PartDrawer.jsx:1153` — "Conflicting assembly costs" stops declaring one entry wrong. New
  copy: *"There are assembly costs on items below this assembly, and this assembly is marked as
  covering the work beneath it. Both are being counted. Check that is what you meant."* Item
  list stays, codes become `onOpenPart` links (the pattern is at `:536` and `:910`). The accent
  border goes — it is a note, not an error.
- Acceptance, per TODO §5: a nullable column on `AssemblyLabor` beside `covers`
  (`models.py:334`) holding **the accepted item list**, not a boolean — so the note returns when
  a new item appears below the cover, saying what changed. `updated_at` / `updated_by`
  (`:335-338`) already give who and when.
- `edit.py:799` endpoints and `schemas.py` carry it. `edit.py:320`'s explicit copy field list
  must be considered deliberately — a copy is arguably a fresh decision, so probably *not*
  carried over. `backup.py`'s `BACKUP_SHEETS` reads `model.__table__.columns`, so the column
  travels with the backup on its own.
- `rollups.py:271` does **not** move. The roll-up keeps adding both.
- Proof: a test that accepting suppresses the note, and that adding a new assembly-costed
  descendant brings it back.

**D3 · Pending's chips come from the data** (TODO §15)

- `Pending.jsx:5-15` — `FIELD_CHIPS` becomes a display-name map. Chips are derived from the
  distinct labels present, counted, and rendered only when non-zero.
- This fixes the `Cost` chip, which can never match today: the counter at `:28` is exact array
  membership and the backend only ever emits `cost@1` / `cost@100` / `cost@10k`. It has read `0`
  on every database there has ever been.
- Group by prefix — `Cost`, `Assembly time`, `Quote` — with the tier in the title attribute.
- After D1 several counts drop and some chips vanish on their own. That is the test.

---

## Phase E — thumbnails (TODO §7)

**E1 · `<item_id>.png` pins itself**

- Match on exact stem, case-insensitive, against an explicit image-extension allowlist — *not*
  `has_thumbnail`, which is true for PDFs and would pin a drawing named after the part.
- Only when `thumbnail_file_id is None`. That is why it does not break the convention doc's
  "pinned, never newest" rule.
- Fires in `upload_attachment` (`attachments.py:59`) and in `list_attachments` (`:36`) — the
  second because the team drops files straight into Drive, which never touches the upload path.
  A GET that writes, guarded so it fires at most once per item.
- Goes through `set_thumbnail`'s existing validation (`:115`), which already proves the file is
  in *this* item's folder and writes the history row. `list_files` is top-level-only
  (`drive.py:187`), so subfolders cannot match — nothing to decide there.
- `changed_by` needs a legible convention for an automatic pin; the History tab is read by
  people.

**E2 · Backfill script**

`backend/scripts/` already holds this shape. One pass over items with a folder and no pinned
thumbnail. Needed for what is already sitting in Drive.

---

## Phase F — close the history holes (TODO §11)

**F1 · Log what currently changes silently**

- `admin.py:248` / `:265` — reference value create and archive. For
  `category='assembly_cost_type'` that row carries the €/hour behind every assembly cost.
- `admin.py:397` — the catalog import wipes with `db.execute(delete(model))` (`:428`). One
  summary row: what was removed, by whom, and which pre-wipe backup holds the previous state.
- `backup.py:344` — restore replaces tables wholesale, and `record_change` appears zero times in
  that file. Same treatment: one row naming the file and the user.
- Summary rows, not a row per item — an import would otherwise write tens of thousands, and the
  detail already lives in the workbook.
- `entity_type` gains `reference_value` and a database-level kind; the History tab has to render
  them.
- Not a hole: backups already carry the log. `ChangeHistory` is in `BACKUP_SHEETS`
  (`backup.py:42`) and `RESTORE_ORDER` (`:166`).

Independent of everything else, and the precondition for `as_of` ever being trustworthy.

---

## Phase I — undo from the History tab (TODO §16)

**I1 · Undo one change, with confirmation**

Sits after F1 deliberately: F1 closes the write paths that leave no history, and an undo button
is only as trustworthy as the log it acts on.

- **An undo is a new forward change.** `models.py:9` keeps the log append-only, so undoing
  writes a fresh row restoring the old value with a `change_reason` naming the row it reverses.
  Nothing is rewritten, and an undo can itself be undone.
- `POST /history/{id}/undo` — re-reads the row, re-checks it is still the latest change for that
  `(entity, field)`, applies the old value through the same path a normal edit takes, records the
  new row.
- `backup.py:198` `_coerce(value, column)` already converts a text cell to a column's real type,
  which is exactly what `old_value` (`models.py:165`, `Text`) needs. Reuse it.
- Scope: `update` only, plus `bom_link` create and remove — adding or removing a component is
  the common mistake and re-adding a link touches no part numbers. Undoing an item `remove`
  would recreate a part number, straight into `CodeRegistry`'s never-reuse rule; not in this
  pass.
- Only the latest change to a field is undoable. An older row reads "superseded by a later
  change" rather than silently discarding everything since.
- No button where the row is not addressable — `cost_evidence` carries only `entity_id =
  item_id` with nothing identifying which evidence row (`edit.py:644`, `:664`). The reason shows
  instead of a guess. The four `cogs_*` kinds need the same audit before they get one.
- `History.jsx` — button per eligible row, and a confirm that spells out item, field, and
  `current → restored`. `window.confirm` is the existing pattern (`PartDrawer.jsx:1104`).
- Proof: a test that undo restores the value *and* appends a row; that undoing twice returns to
  the original; and that a superseded row is refused.

---

## Phase G — milestones and diff (TODO §10) — migration `0017`

**G1 · The `BomGraph` data seam** — pure refactor, no behaviour change

`BomGraph.__init__` (`rollups.py:75`) runs five queries then works entirely off plain dicts;
nothing after the constructor touches the `Session`. Split loading from the graph so a graph can
be built from in-memory rows. Every rollup, coverage number, flatten and treemap then works
against a milestone unchanged.

Proof: the whole suite green with no test edits. If a test needs changing, the refactor is not
one.

**G2 · Capture** — `bom_milestones` (`id, root_item_id, name, note, taken_at, taken_by, payload`)

- `payload` holds `Item`, `BomLink`, `DecidedCost`, `AssemblyLabor` and the
  `assembly_cost_type` `ReferenceValue`s, filtered to the subtree, **all three tiers**, in
  `backup.py`'s existing cell format (`_backup_cell` `:61`, `_coerce` `:198`).
- **Inputs only.** No rolled-up costs, no coverage, no totals — `models.py:5` says derive, don't
  store, and re-deriving through the same `BomGraph` is also what makes the two sides of a diff
  comparable.
- `JSONB_OR_JSON` (`models.py:38`). Read-only for ever. Add to `BACKUP_SHEETS` and
  `RESTORE_ORDER` or milestones die on restore.
- Reads must tolerate missing keys — a snapshot taken today is read after tomorrow's migration.

**G3 · The diff engine**

Two `BomGraph`s at the same tier, walked together: row set is every item reachable from the root
in either; per row `added` / `removed` / `changed` / `unchanged` plus deltas on quantity, unit
cost, rolled-up cost and weight; at the top the total delta and what accounts for it.

No history involved — this is arithmetic over two known-good states.

**G4 · Browse shows it**

- A `Live · <milestone>` selector beside the BOM picker. It lifts to `App.jsx` for the same
  reason the tier did in C1 — the drawer has to follow it.
- Flattened view gains delta columns.
- The drawer while a milestone is selected: read-only. More useful than refusing to open, and
  more work.
- `change_history` filtered to `taken_at .. now` annotates each changed row — who, when, and
  `change_reason`. **Annotation only.** A gap in the log costs a sentence of provenance, never a
  number.

**G5 · Two treemaps, side by side** — after Phase A

Milestone left, live right, same BOM, same tier. Not one treemap coloured by delta: a treemap's
whole claim is that area is the number, and a delta has no area.

*Later, not now:* once F1 has landed and `as_of` is built, replay to a milestone's `taken_at`
and assert it reproduces the payload. Milestones are the fixture that makes reconstruction
provable. Until that passes on real data, payloads stay the source of truth — and arguably stay
anyway, because a payload is evidence of what was on screen and a reconstruction is a derivation
that changes when you fix a replay bug.

---

## Phase H — import duty structure (TODO §12) — migration `0018`

Scope decided: **structure now, data later.** Acceptance is that an item with an HS code and a
matching rate produces a duty figure that rolls up — not that the BOM is classified.

**H1 · Schema**

- `Item.hs_code` and `Item.country_of_origin` as real columns, not `field_definitions` rows. The
  custom-field mechanism is right for what only humans read; these are cost inputs the roll-up
  reaches, and an EAV lookup inside a rollup is the wrong shape.
- `country_of_origin` is separate from `supplier_country` (`models.py:60`) on purpose. Customs
  charges on where a thing was *made*. A German distributor shipping a Chinese-made part is a CN
  origin at a DE supplier, and conflating them would be wrong on exactly the parts that matter
  most. Default from supplier, edit independently.
- `duty_rates`: `hs_code`, `origin_country`, `destination_country`, `rate_pct`, `valid_from`,
  `source`, `note`. Longest-prefix match on HS, so a 4-digit heading covers its subtree until
  someone enters the 6- or 8-digit line.
- `CogsFacility` (`models.py:398`) gains `country`, so "landing in Portugal" is data rather than
  an assumption inside a formula.

**H2 · Lookup and roll-up**

- Duty per item at a tier = customs value × rate, only where `DecidedCost.make_or_buy`
  (`models.py:222`) is `buy` or `made-to-order` — which makes it a per-tier question even though
  the HS code belongs to the physical thing.
- A bought-in assembly (`covers='all'`) is one customs line with one HS code; an assembly built
  here from imported parts has duty on the parts and none on itself. `covers` already
  distinguishes them.
- Missing HS code reads as **missing, not zero** — D1's coverage machinery is the pattern, and
  this is a new gap kind for the Pending queue.
- **Import VAT never enters COGS.** It is recoverable in Portugal. Write that in the code comment
  before someone adds 23%.

**H3 · Where it surfaces**

- Drawer fields for HS code and origin, on purchased items.
- An admin screen for `duty_rates`, seeded from a TARIC export with the date it was valid on.
  Auditable beats fresh: a landed cost that quietly changed under a quote is worse than one that
  is three months old and dated.
- The COGS ladder already has the lines — `inbound` and `duty`, both `€ / plant`, basis `direct`
  (`cogs.py:86-87`). First move is the **cross-check**: show the derived per-part total beside
  the typed rung and say they disagree by X. Making the rung derived, or redefining it as
  "everything not attributable to a part", is a later decision.
- Customs value is normally CIF — goods plus freight and insurance to the border — not ex-works.
  Using the decided unit cost understates it, and that pulls `inbound` into the same
  calculation. Flagged, not solved here.

---

## Order, and why

1. **D3, B1, C1** — small, visible, no migration. The branch is useful after a day.
2. **A1 → A2** — the treemap stops contradicting its own numbers. A2 lands with a real test.
3. **D1 → D2** — cover semantics, together, as decided. First migration (`0016`).
4. **E1 → E2** — thumbnails. Independent, easy to review after the heavier work.
5. **F1 → I1** — history holes, then undo. In that order: an undo button is only as
   trustworthy as the log it acts on.
6. **B2** — the distribution curve. Sits on B1 and wants the KPI row settled first.
7. **G1 → G5** — milestones. G1 is a no-behaviour-change refactor and should be reviewed on its
   own. G5 waits for Phase A.
8. **H1 → H3** — duty structure. Last because it is the largest new surface and the one with the
   most open modelling questions above it.

Twenty commits across nine phases — A×2, B×2, C×1, D×3, E×2, F×1, I×1, G×5, H×3. Each phase is
run locally and reviewed before its commits are made.

## Deliberately not in scope

- `as_of` reconstruction — F1 first, then validated against G2's payloads.
- Filling HS codes and TARIC rates — H's acceptance test does not need them.
- Whether Costing's `scenario` becomes app-wide. It stays local; nothing outside that tab reads
  it.
- A distribution curve on every tree row. B2 does the KPI tile; the rest is its own decision.
