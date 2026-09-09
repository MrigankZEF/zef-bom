# UI fixes — todo

Collected first, planned second. Nothing here is implemented until the plan for it lands.

Branch cut from `origin/main` at `6eb9db2`, which already carried fixes for three of these —
noted per item below.

## 1. Cost as a distribution, not a hard number

A unit cost is a three-point estimate (`cost_min` / `unit_cost_eur` / `cost_max`) but the KPI
tile and the rows print a single figure, so the reader takes € 2.9k for a fact. Draw the spread
instead: a small distribution curve inline, most-likely at the peak, min–max as the width.

- Data already exists: `Part.cost_min` / `cost_max` in `backend/app/models.py`, the three-point
  row on the cost record, and the rolled-up `ct.cost_min` / `ct.cost_max` the `Costing` view
  already reads.
- Today the range only shows as text (`range €2.7k–€3.1k`), as the shaded band on the volume
  chart, and — since `6eb9db2` — on the KPI sub-line permanently. Still a number, not a shape.
- Open: which rows get the sparkline (KPI tile only, or every tree row), and what shape to
  draw when min == max (no spread) or when only part of a subtree is three-point costed.

## 2. The treemap's areas are not proportional to cost — PARTLY FIXED upstream

`6eb9db2` (Mrigank, 2026-09-08) fixed a *different* cause than the one measured below:
`gridCells` stretched a partly-filled last row when a part used ×N is split into N tiles, worth
up to 3× on equal-value cells. Verified fixed — each row now takes `h * count / n`, so every
cell is exactly `w*h/n`.

The nesting-depth distortion is untouched. Two leaves of the same cost still come out different
sizes, and the driver is depth, not value. Measured on a synthetic stack (4 identical cells +
2 electrodes, `depth = 4`, 900 × 558): a €1.50 Cell Membrane at level 1 gets 24 167 px², an
identical €1.50 Bipolar Plate one level deeper gets 19 776 px² — **1.22× for the same money**,
1.31× across the whole leaf set. Only 83% of the canvas ends up carrying value at all.

Three compounding causes, all still present in `frontend/src/components/CostTreemap.jsx`:

- **Chrome is subtracted but never accounted for.** `emit` gives a card a rect proportional to
  its value, then recurses into `r.h - hh - pad - 1` (`:217`) and lets the children fill that
  *whole* remainder. So the children of a card are drawn at `value / (area − chrome)` px² per €.
  Chrome is a fixed 15–22 px header plus 2–3 px padding, so a small card loses a large fraction
  of itself and a big card loses almost none — and each extra level subtracts a header again.
- **`INSET` is a constant.** Every tile is drawn 1.5 px narrower and shorter than its rect
  (`:367`). Negligible on a big tile, a large slice off a small one, and it applies to the card
  *and* again to each child inside it.
- **The 3% assembly-work floor drops value silently.** `childItems` hides an assembly's own cost
  when it is under 3% of the card's total (`:194`), but the card was sized on the total
  *including* it. The children then re-normalise over the smaller sum and inflate to fill.

`squarify` itself is area-exact — verified. The fix belongs in the nesting, not the algorithm.

- Open: what to trade. Exact areas mean either reserving chrome *before* sizing (children get
  `value × (area − chrome) / area`, so a card's interior is honest but a card is bigger than
  its value) or dropping the header into the tile itself. Needs a decision on which invariant
  the reader is owed.

## 3. The focus assembly has no flyover — DONE upstream (`6eb9db2`)

The current breadcrumb entry now carries the same tooltip, built from a synthetic `focusTile`
(`CostTreemap.jsx:307`). Written first as a disabled `<button>`, which fires no mouse events —
it is a `<span onMouseMove>` now (`:324`). Verified present.

Original complaint, for the record: every tile inside the treemap had a tooltip, but the
assembly you were *looking at* had none, because `layout` starts from the focus node's children
and the focus itself is never a tile.

- Still open, if it matters: a container card's body remains mostly unhoverable, since the child
  `<g>`s paint after it and take the pointer. Only the title bar and the 2–3 px border are live.

## 4. Min/max scenario never reaches the key numbers — DONE upstream (`6eb9db2`)

The KPI now follows the Min/Likely/Max switch and names the scenario (`Costing.jsx:51-53`
and `:85`), with the min–max range on the sub-line permanently. Verified present.

Original complaint: the toggle re-scaled the treemap but left the headline on the most-likely
figure, so the total on screen contradicted the picture beside it and the max total could not be
read anywhere.

- Item 1 still stands on top of this: the range is text, not a drawn distribution.

## 5. Reframe the "conflicting assembly costs" warning

The roll-up arithmetic stays as it is: an ancestor marked `covers = 'labor'` still has every
descendant's own assembly cost added on top (`rollups.py:271` adds it unconditionally; the
`_asm_covered` check at `:281` only records the item in `covered_conflict[]`). That is
deliberate — it keeps a sub-assembly's own labour figure usable when the same sub-assembly is
built under a parent that does not cover it.

What changes is only the drawer's copy. Today it declares an error and demands the user pick a
side:

> **Conflicting assembly costs** — AEC083A, UNP174A carry an assembly cost, but an assembly
> above them are marked as already covering the work below. One of the two is wrong — either
> untick the cover, or clear the assembly cost below it.

Neither entry is necessarily wrong, and the roll-up does add both — so the honest message is a
question about double counting, not a verdict:

> There are assembly costs on items below this assembly, and this assembly is marked as
> covering the work beneath it. Both are being counted. Check that is what you meant.

The item list stays, and the codes become links — `PartDrawer` already takes `onOpenPart`
(`:66`) and the pattern is used for children (`:536`) and where-used (`:910`), so it is a
one-liner per code.

- `frontend/src/components/PartDrawer.jsx:1153` — title, the accent border that makes it read
  as an error, the body copy, and the item list becomes `onOpenPart` links.
- The roll-up itself does not move. `covered_conflict[]` keeps its name and its meaning.
- Open: does the note belong on the covering assembly's drawer, on each descendant's, or both?
  Today it fires wherever the rolled-up `covered_conflict[]` is non-empty, so a covering
  assembly and every assembly above it all show it.

### Accepting the risk

A note that cannot be answered is a note people learn to scroll past, so it needs an "I
checked this, it is intentional" that sticks. There is no acknowledge/dismiss mechanism
anywhere in the app yet — this would be the first, so it is worth picking a shape that a
second one could reuse.

**Recommended: store the accepted set on the `assembly_labor` row.** That row already *is* the
decision — it carries `covers` and it is already per (item, volume tier), which is the right
grain, because a cover ticked at @10k and not at @1 should not silently accept @1 as well. It
also already has `updated_at` / `updated_by` (`models.py:335-338`), so who accepted and when
come free.

Store the descendant list that was accepted, not a bare boolean. A boolean goes stale the
moment someone adds assembly time to a *new* item below the cover — the note would stay
suppressed on a double count nobody has ever looked at. With the set stored, the rule is:
show the note when the current `covered_conflict[]` differs from the accepted set, and say what
changed — *"2 items have been added below this assembly since this was accepted"*. Re-accepting
is one click.

Rejected alternatives:

- **`localStorage`** — cheapest, and wrong. Whether a double count is intentional is a costing
  judgement the next person needs to see, not a per-browser preference. It would also
  disappear on a new machine and never reach the backup.
- **A free-text note on the item** — the app has notes already, but nothing reads them, so the
  warning could not know it had been answered.

Touch points if we take the recommendation:

- `backend/app/models.py:334` — a nullable column on `AssemblyLabor` beside `covers`, holding
  the accepted item ids. A new alembic migration (`0015_...`) after `0014_assembly_covers.py`.
- `backend/app/routers/edit.py:799` — the assembly-labor endpoints read and write it, and
  `AssemblyLaborIn` / `AssemblyLaborOut` in `schemas.py` carry it.
- `backend/app/routers/edit.py:320` — the copy-an-item path lists `AssemblyLabor`'s fields
  explicitly, so a new column has to be added there deliberately.
- `backend/app/backup.py` — the column has to travel with the backup, or an acceptance is lost
  on restore.
- Open: on copy, does an acceptance carry over? A copy is arguably a fresh decision that should
  be re-checked, which argues for dropping it.
- Open: does accepting belong in `change_history`? It is a costing decision with a name against
  it, and the History tab is where those are read.

## 6. The review queue asks for data that nothing will ever use

The Pending queue demands `asm_time@1 / @100 / @10k` on assemblies whose labour cannot enter
any total. Filling them changes no number, so the queue stays permanently red on rows nobody
can usefully close.

`backend/app/routers/tree.py:285-303` builds `missing` for an assembly from that item's **own**
`AssemblyLabor.covers` and nothing else. It never walks ancestry. So it handles the one case it
can see locally — `covers == 'all'` on the item itself asks for a quote instead of minutes
(`:298`) — and misses the two that depend on what sits above:

- **An ancestor with `covers = 'labor'`.** The roll-up already treats these as not-a-gap:
  `_asm_covered` propagates down and the covered assembly is skipped for both
  `missing_assembly[]` and `total` (`rollups.py:277-283`). Pending demands the minutes anyway.
- **Anything under a `covers = 'all'` boundary.** The roll-up discards every cost the subtree
  produces and lists the items in `below_boundary[]` (`rollups.py:243`) — the Costing tab even
  says so in as many words. Pending still asks for times on the assemblies and prices on the
  leaves below it.

The only filter Pending applies is `it.item_id not in linked` (`:269`), which drops catalog-only
items — not covered ones.

So two places compute "what is missing" by different rules, and the queue contradicts the
coverage percentage shown next to it. That is the actual defect; the red chips are the symptom.

### The rule

An item is only free of a gap if **every** place it is used already covers it. Used once under
a covering assembly and once under one that does not, and it is a real gap — the second usage
needs the number. So the test is per (item, volume tier): *is there any path down from a live
top-level root that reaches this item without a cover above it?* If yes, flag. If no, the row
stays but reads as covered.

The rows do not disappear. An item has to make sense on its own, outside whichever BOM it
happens to sit in today, and a vanished row cannot say why it vanished. A greyed
"covered above — nothing to fill here" row that links to the assembly doing the covering keeps
both: no false work, and the reason is on screen.

Per-tier matters: `covers` is per volume tier, so the same assembly can be a genuine gap at @1
(built in house) and covered at @10k (outsourced above). That is exactly the grain of the chips
already shown, so nothing in the UI needs to change shape.

### One wrinkle: `labor` and `all` are not equally inert

- **Below a `covers = 'all'` boundary** — the roll-up discards every cost the subtree produces
  (`rollups.py:221-246`). Filling anything down there changes no total, ever. Genuinely inert,
  and "nothing to fill here" is the whole truth.
- **Below an ancestor with `covers = 'labor'`** — the roll-up still *adds* the descendant's own
  assembly cost (`rollups.py:271`, unconditional; see item 5). So filling it is not inert at
  all: it changes the total, by double counting. The queue should say that, not "nothing to
  fill" — something closer to *"covered by AEC002A — a time entered here is added on top."*

This is the same hole item 5's note covers, seen from the other side. Together they are
consistent: leave a covered assembly's time empty and it contributes nothing; fill it and the
drawer warns you that both are counted. **Decided (2026-09-09): that is the reading.** The
roll-up arithmetic stays as it is, the queue stops asking for what no uncovered usage needs,
and item 5's note is what stands between a filled covered assembly and a silent double count.
So items 5 and 6 ship together — the note is load-bearing for the queue change, not cosmetic.

### Fix direction

- Have `/pending` ask `BomGraph` rather than re-deriving from raw queries. `missing_assembly[]`,
  `missing[]`, `missing_quote[]` and `below_boundary[]` are already this answer, already
  tier-aware, and already the numbers the Costing tab reports.
- `rollups.py:18` `top_level_reachable` already does the top-down walk over live roots and is
  deliberately kept off `BomGraph`. Carrying a per-tier "covered from above" flag down that
  same walk, and recording whether *any* path arrived uncovered, is the whole rule — and it
  answers it BOM-wide in one pass, which is what `/pending` needs and what a single-rooted
  `BomGraph` cannot give.
- Decided: the queue and the drawer get different treatment. The queue is a work list, so a
  covered item is not work and its chip goes grey. The drawer is the item standing alone —
  its labour being empty is worth showing there regardless of who covers it in today's BOM,
  because the whole point is that the sub-assembly can be lifted into an assembly that does
  not cover it.
- Open: verify against live data which case the screenshot rows (`AEC002A`, `AEC052A`,
  `AEC063A`, `AEC082A`, `AEC084A`, `AEC111A`, `AEC015A`, `AEC054A`) fall into — a `labor` cover
  above, a boundary above, or genuinely unfilled. Same fix either way, but it says how much of
  the queue this clears and which of the two messages most rows get.
- `UN027A` also shows `cost_type`, which `tree.py:303` already guards with
  `any(cov.get(t) != "all")` — that guard needs the same ancestry awareness.

## 7. A file named after the item auto-pins as its thumbnail

If an item's Drive folder holds `AEC001A.png`, that is somebody saying "this is the picture of
this part" as plainly as it can be said. It should become the thumbnail without anyone opening
the drawer and clicking *set as thumbnail*.

This does not break the rule the convention doc states — *"Pinned rather than 'newest image in
the folder', so a new upload never silently swaps the picture"*
(`docs/attachments_convention.md`). An exact `<item_id>` filename is an explicit naming
decision, not the newest-file guess that rule exists to prevent. Two guards keep it honest:
auto-pin **only when nothing is pinned yet**, and only on an exact stem match.

Where it can fire, and the trade:

- **On upload** — `attachments.py:59` `upload_attachment` already has the filename, the item
  and a `user` for the history row. Cheapest, and immediate.
- **On listing** — `attachments.py:36` `list_attachments`. This is the case that matters most,
  because the convention doc says the team drops files straight into Drive via the Drive UI,
  which never touches the upload endpoint. A GET that writes is not lovely, but it is a lazy
  backfill guarded by `thumbnail_file_id is None`, so it fires once per item at most.
- **A backfill script** — `backend/scripts/` already holds this shape
  (`migrate_note_urls.py`, `audit_rollups.py`). Wanted anyway, to catch the files already
  sitting in Drive today.

Probably all three: upload for the live path, script for the backlog, and listing decided on
its own merits.

Mechanics that already exist:

- `drive.list_files` returns `name` and `has_thumbnail` per file (`drive.py:197`), the latter
  being exactly "Drive can render this", so no format sniffing is needed.
- `set_thumbnail` (`attachments.py:115`) already validates that a file id sits in *this* item's
  folder before pinning, and writes the `thumbnail_file_id` history row. The auto-pin should go
  through the same path rather than assigning the column directly.
- `list_files` queries `'{folder_id}' in parents` (`drive.py:187`), so it sees the item's own
  folder only, never a subfolder. That settles the nested question by itself.

- Open: which extensions. `.png` was the ask; `.jpg` / `.jpeg` / `.webp` are the same intent.
  `AEC001A.pdf` is almost certainly a drawing or datasheet, not a picture — and Drive *will*
  render a thumbnail for it, so `has_thumbnail` will not filter it out. Recommend an explicit
  image-extension allowlist rather than "anything Drive can render".
- Open: case. Item ids are uppercase (`AEC001A`); a file may well arrive as `aec001a.png`.
  Match the stem case-insensitively.
- Open: what counts as the stem. `AEC001A.png` clearly. `AEC001A_front.png` or
  `AEC001A rev B.png`? Exact-stem-only is the safe start, and can widen later if the folders
  turn out to be named that way.
- Open: `changed_by` on an automatic pin. The upload path has a real user; the backfill script
  does not. Needs a convention — the history log is read by people, so `auto` or similar wants
  to be legible there.
- Open: if the pinned file is later deleted from Drive, `get_thumbnail` 404s
  (`attachments.py:149`) and the item keeps a dead id. Worth deciding whether the auto-pin
  re-resolves in that case or leaves it alone.

## 8. One volume-tier control, not two

Opening the drawer from Browse puts two tier switches on screen at once — the toolbar's
`cost @ 1 · 100 · 10k` and the drawer's own, inside Key figures. They are two independent
`useState(DEFAULT_TIER)` calls (`Tree.jsx:20` and `PartDrawer.jsx:70`), so they agree only
until one is touched, and then the tree reads @10k while the drawer beside it reads @100 with
nothing on screen saying why.

Keep the left one. The drawer follows it.

- `frontend/src/components/PartDrawer.jsx:734` — drop `<TierToggle>` from `Readouts`, and the
  `TierToggle` component at `:609` with it. The drawer takes the tier as a prop instead of
  owning `useState` at `:70`.
- `frontend/src/App.jsx:29` — the tier lifts to here, next to `openPart`. It has to: the
  drawer is a sibling of the view, not a child, and it is opened from Catalog, Pending, History
  and Admin too, none of which have a tier control of their own. So this becomes the app's
  tier, and Browse's toolbar is where it is set.
- `frontend/src/components/Tree.jsx:187-196` — the remaining control gets bigger and says what
  it is. Today it is three unlabelled `1 / 100 / 10k` chips behind a small `cost @`; it should
  read as the app-wide switch it now is.

Decided (2026-09-09):

- **Costing keeps its own control, but not its own value.** `Costing.jsx:14`'s `volume` state
  goes; its buttons at `:84-86` bind to the same app-wide tier. Costing and Browse are never
  on screen together, so two *controls* is not the confusion item 8 is about — two *values*
  is. Sharing the value means switching Browse → Costing lands on the tier you were already
  looking at.
- **Does not survive a reload.** No `localStorage`. Every load starts at `DEFAULT_TIER` (10k),
  which is the volume the business case is written at.
- Costing's `scenario` (Min / Likely / Max) stays local to Costing. It is a different axis and
  nothing outside that tab reads it.

- Open: `TIERS`, `DEFAULT_TIER` and `tierLabel` are declared three times over
  (`Tree.jsx:5-9`, `PartDrawer.jsx:6-10`, `Costing.jsx:6-9`). Lifting the state is the moment
  to put them in one place.

## 9. Strip the treemap's card chrome

Three pieces of decoration on the nested overview cost more than they give, and all three
also eat area — which is item 2's defect, so this is a legibility change that pays for itself
twice.

- **Rounded corners.** `cardR` (`CostTreemap.jsx:52`) rounds every card by 3–5 px and leaves
  come out with a flat `rx="2"` (`:408`). At the nesting depths this view actually reaches, a
  rounded corner on a 20 px-tall tile just softens the one edge that says where a box ends.
  Square everything.
- **The nested indent.** `framePad` (`:53`) insets a card's contents by 2–3 px on both sides
  and again at the bottom (`:213`). Read across seven levels — AEC → Core → Triple Stack →
  Stack → Half Stack Cells → Cell → Cell Frame in the screenshot — it stacks into a visible
  stair of gutters that carry nothing and that no tile can use. The header rule already says
  where a card's contents begin; the indent restates it and charges area for it.
- **The bar in front of assembly names.** `:376-377` draws a 2.5 px (4 px in Module mode)
  coloured stub at the head of every card title. In Module mode it duplicates the card's own
  wash; in Heat mode it is the only module colour on screen, which contradicts the comment at
  the top of the file — *"Heat mode is strictly one red ramp"*.

What still carries nesting once they are gone: `cardWash` shifts each level further toward
bone (`:49`), `headFont` shrinks with depth (`:45`), and `cardLine` keeps a hairline border
(`:51`). That is three signals for depth, which is enough.

- Also to look at: `INSET` (`:54`) is a constant 1.5 px gutter around every tile, so it reads
  as part of the same family. Item 2 wants it gone or made proportional regardless.
- Open: does removing `framePad` entirely leave child tiles touching the parent's border, and
  does that read as one box or two? A single hairline may be enough separation, but it wants
  looking at on a real BOM rather than being decided here.
- Open: the legend swatches at `:445` also use `rx="2"`. Cosmetic and out of the way, but they
  should match whatever the tiles end up doing.

## 10. BOM milestones — snapshot a top-level BOM, then compare against live

Wanted: mark the current state of a top-level BOM, keep editing, and flip between the marked
state and live to see what moved. The switch lives in Browse, beside the BOM picker.

### What exists to build on

- `docs/api.md:62` already advertises `GET /history?as_of=` and `models.py:150` calls
  `change_history` the thing that powers *"BOM as of date X"*. **Neither is implemented** —
  `as_of` appears in no router. So the timeline story is aspiration, not foundation.
- `BomGraph.__init__` (`rollups.py:75`) runs five queries and then works entirely off plain
  dicts: `items`, `children`, `parents`, `decided`, `labor`, `covers`, `rates`. Nothing after
  the constructor touches the `Session`. **That is the seam.** Split the loading out and a
  milestone view reuses every rollup, every coverage number and every treemap unchanged.
- The five inputs are `Item` (unarchived), `BomLink` (unarchived), `DecidedCost`,
  `AssemblyLabor` and `ReferenceValue` where `category = 'assembly_cost_type'`. Decided costs
  and labour are read per tier, so a snapshot has to keep all three tiers or you cannot change
  tier while looking at a milestone.
- `backup.py:155` `RESTORE_ORDER` plus `_backup_cell` (`:61`) and `_coerce` (`:198`) already
  serialise and revive exactly these models. Subtree-scoping that is reuse, not a rewrite.

### Recommendation: store the rows, not the timestamp

A milestone is a self-contained copy of the subtree's rows at that instant, in one new table:

```
bom_milestones
  id, root_item_id, name, note, taken_at, taken_by, payload   -- JSONB_OR_JSON
```

`payload` holds the five tables above, filtered to the subtree reachable from
`root_item_id`, in `backup.py`'s existing cell format. `JSONB_OR_JSON` already exists at
`models.py:38`. Read-only, always.

Why this one:

- **Correct by construction.** It cannot disagree with what was on screen, because it *is*
  what was on screen.
- **Small.** The BOM in the screenshots is 93 parts; five tables of that is tens of KB.
- **No new query paths.** With the `BomGraph` seam, tree, flatten, rollup, coverage, treemap
  and export all work against a milestone with no forked logic.
- **Does not block the timeline story.** A milestone's `taken_at` is precisely the timestamp
  `as_of` would need, so building reconstruction later is additive.

### Rejected

- **A named timestamp, reconstructed from `change_history`.** This is the better design and
  should be the end state: a milestone becomes a bookmark, storage goes to nothing, and *any*
  moment is comparable rather than only the ones somebody remembered to mark. It is rejected
  **for now, on measured evidence**, not on principle. The log cannot currently reconstruct a
  past BOM, because three of `BomGraph`'s five inputs have unlogged write paths:

  1. **Reference values, and with them the assembly rates, are never logged.**
     `BomGraph.rates` reads `rate_eur_h` out of `ReferenceValue.meta` (`rollups.py:125`).
     `add_reference` (`admin.py:248`) and `archive_reference` (`admin.py:265`) write no
     `change_history` row — `record_change` is called 7 times in that file and never for a
     reference value. Narrower than it first looks: there is no PATCH/PUT for a reference, so
     no API path edits a rate *in place*. Changing one means creating a new row and repointing
     `item.cost_type_id`, and that repoint **is** logged on the item. What is unlogged is the
     reference row's own creation and archival, and any `meta` edited through a restored
     backup workbook — which is silent and would reprice every assembly using that row.
  2. **The catalog import wipes with bulk deletes.** `import_catalog` (`admin.py:397`) clears
     BOM data with `db.execute(delete(model))` (`:428`) — no per-row history. Anything before a
     wipe is unreconstructable.
  3. **Restore writes no history.** `record_change` appears **zero** times in `backup.py`;
     `restore_from_workbook` (`:344`) replaces tables wholesale. After any restore the log
     cannot account for what is in the database.

  Plus a mechanical cost: `old_value` / `new_value` are `Text` (`models.py:165-166`), so
  replay needs per-column coercion.

  A wrong past cost that looks right is worse than no feature. Fixing all three is worth doing
  on its own merits — they are audit holes regardless of milestones — but they are a separate
  piece of work, and until they are closed reconstruction cannot be trusted.
- **Cloning the subtree under new part numbers.** Directly against the grain of the data model:
  part numbers are never reused, `CodeRegistry` is the append-only ledger of every number ever
  handed out, and a snapshot would burn ~93 of them per milestone while polluting the catalog
  and every where-used answer.
- **Effective-dating every table (`valid_from` / `valid_to`).** The textbook answer and far too
  large — it rewrites every query in `rollups.py`, `tree.py` and `edit.py`, for a feature whose
  ask is "let me compare two states".

### Open questions

- **Scope of a snapshot.** One top-level root, or the whole database? Per-root is what was
  asked for and keeps payloads small, but a shared sub-assembly edited under another root will
  show as a change here — correctly, though it may read as surprising.
- **How the switch presents.** A `Live · <milestone name>` selector beside the BOM picker is
  the smaller change. Item 8 is already lifting tier into `App.jsx`, and this wants the same
  treatment, since the drawer has to follow it too.
- **The drawer while a milestone is selected.** It is full of write paths. Read-only, or
  refuse to open? Read-only is more useful and more work.
### Diff, and what `change_history` is actually for (decided 2026-09-09)

**Diff, not just flip.** And the important part: a diff needs no history at all. It is computed
from two *states* — the snapshot payload and live — each loaded into a `BomGraph` at the same
tier, then walked together:

- row set = every item reachable from the root in either state
- per row: `added` / `removed` / `changed` / `unchanged`, plus deltas on quantity, unit cost,
  rolled-up cost and weight
- at the top: the total delta, and which rows account for it

That is arithmetic over two known-good states. Nothing can be silently wrong, because neither
side is reconstructed.

**So `change_history` annotates the diff, it does not produce it.** Filtered to
`taken_at .. now` and to the items in the diff, it answers *who changed this, when, and why*
(`change_reason` is already on the row) next to each changed line. Split that way:

- **state** comes from the snapshot — trustworthy by construction
- **explanation** comes from the log — and if the log has a gap, you lose a sentence of
  provenance, not a number

That is the whole reason this is safe to build now. The reason `as_of` was rejected above is
that it makes correctness depend on the log; using the log for narration puts it exactly where
its append-only, best-effort nature is fine.

**It also earns `as_of` later.** A milestone is a known-good state at a known timestamp — which
is precisely the fixture a reconstruction needs to be tested against. Build `as_of`, replay to
a milestone's `taken_at`, and assert it reproduces the payload. Until that passes on real data,
reconstruction is not trustworthy; once it does, `docs/api.md:62`'s promise can be kept, and
every past state becomes addressable rather than only the marked ones. Milestones are the step
that makes the aspirational feature provable instead of hopeful.

### Why not just a date, then

Both designs answer the same question, so build them behind the same seam and the choice stops
being permanent. The diff engine takes **two `BomGraph`s** and does not care where they came
from, so there are two state providers behind one interface:

```
state(root, milestone_id)  -> from the stored payload      (works today)
state(root, timestamp)     -> reconstructed from history   (once the log is complete)
```

Order: payload provider first, because it works now and is provable. Then close the three
logging holes. Then build the timestamp provider and validate it *against the payloads* — a
milestone is a known-good state at a known instant, which is exactly the fixture reconstruction
needs. Once it reproduces every stored payload on real data, milestones can degrade to
bookmarks and the payload becomes optional.

One thing a payload keeps that a reconstruction never has: it is **evidence**. If a number from
a BOM went to a customer, the stored rows are what was actually on screen. A reconstruction is
a derivation, and it changes the day you fix a replay bug. For a tool whose output is quoted
externally, that is worth keeping even after `as_of` works — which argues for payloads on
milestones and reconstruction for everything in between, rather than one replacing the other.

### Where the diff shows (decided 2026-09-09)

- **Flattened Browse is the home.** It is already a row per item with cost columns, so it takes
  delta columns directly. Pick a milestone, compare against live.
- **The treemap gets two, side by side.** Not one treemap coloured by delta. Two treemaps of
  the same BOM at the same tier, milestone left and live right, is honest — a treemap's whole
  claim is that area is the number, and a delta has no area. Reading "this block grew" off two
  pictures is what the eye is good at.
- Consequence for the layout: two treemaps at once halves the width each gets, and item 2 is
  already about area being scarce and item 9 about chrome eating it. Worth building 9 before
  the side-by-side, so the second treemap is not paying for decoration.

### Not breaking the existing rules

`models.py:4-11` states the data model's rules. Two of them bear on this:

- *"Derive, don't store: parentage, where-used, and rollups are all queries — never
  denormalised onto the item."* So the payload holds **inputs only** — items, links, decided
  costs, assembly labour, rates. No rolled-up costs, no coverage percentages, no totals. A
  milestone is re-derived through the same `BomGraph` as live, which is also what keeps the two
  sides of a diff comparable: same code, different inputs.
- *"History is a single append-only `change_history` log (no item_revisions)."* A milestone
  table sits close to this, so be deliberate: it is **not** a revision chain. There are no
  version numbers on items, no per-item history rows, no new part numbers, and nothing in the
  app derives anything from a milestone except the milestone view itself. The live tables stay
  the single source of truth; a milestone is an inert, user-named copy that is only ever read.
  `change_history` remains the one log, and this feature never writes to it or replays it.

- **Schema drift.** A snapshot taken today is read by tomorrow's code, after a migration adds
  a column. Reads have to tolerate missing keys rather than assume the current schema —
  `_coerce` is already defensive, which helps.
- **Backup.** `bom_milestones` needs adding to `RESTORE_ORDER`, or milestones die on restore.

## 11. Close the change_history holes

Three write paths change data the rollup reads and leave no trace in `change_history`. They are
audit holes on their own terms — "who changed this number" has no answer for any of them — and
they are also what blocks item 10's reconstruction story, so closing them has two payoffs.

- **Reference values.** `add_reference` (`admin.py:248`) and `archive_reference` (`:265`) write
  nothing. For `category = 'assembly_cost_type'` the row carries `meta.rate_eur_h`, which is
  the €/hour behind every assembly cost in the system. Log create and archive; if a PATCH for
  a reference is ever added, log the `meta` change too.
- **The catalog import.** `import_catalog` (`admin.py:397`) wipes BOM data with
  `db.execute(delete(model))` (`:428`). It already takes a pre-wipe Drive backup, which is the
  right instinct, but the log itself goes silent across the event. One row recording the wipe —
  what was removed, by whom, and which backup file holds the previous state — is enough to keep
  the log honest without a row per deleted item.
- **Restore.** `record_change` appears **zero** times in `backup.py`; `restore_from_workbook`
  (`:344`) replaces tables wholesale. Same treatment: one row saying the database was restored,
  from which file, by whom.

Not a hole: **backups already carry the history.** `ChangeHistory` is in `BACKUP_SHEETS`
(`backup.py:42`) and in `RESTORE_ORDER` (`:166`), so every snapshot contains the full log and a
restore brings it back. Nothing to build there.

- Open: for the wipe and the restore, one summary row or a row per affected item? A row per
  item is faithful but writes tens of thousands of rows on an import. A summary row plus the
  backup filename is probably the honest trade — the detail lives in the workbook, and the log
  says where to look.
- Open: `entity_type` currently takes `item`, `bom_link`, `decided_cost`, `assembly_labor`,
  `field_value`, `cost_evidence`, `item_link` and the four `cogs_*` kinds. This adds
  `reference_value` and something for database-level events — `database` or similar. The
  History tab reads these, so it needs to render the new kinds.

## 12. HS code on purchased items, and expected EU import duty

Wanted: an HS code on parts and assemblies that are bought in, and from it an expected import
duty for goods landing at our assembly facility in Portugal.

**Scope (decided 2026-09-09): build the structure now, fill the data later.** Columns, the
duty-rate table, the facility country, the lookup and the roll-up all land in this run. Actual
HS codes on parts and actual TARIC rates in the table are a separate pass. So the acceptance
test for this item is that an item with an HS code and a matching rate produces a duty figure
and rolls it up — not that the BOM is classified.

### What already exists

- **The COGS ladder already has the lines.** `cogs.py:86-87` defines `inbound`
  ("Inbound transport") and `duty` ("Customs & duties"), both `€ / plant`, basis `direct`. So
  today duty is a plant-level lump somebody types in. This item does not add a concept — it
  makes an existing line derivable bottom-up. **The two then have to reconcile**, which is the
  real design question below, not the HS lookup.
- **Custom fields need no migration.** `models.py:10` — *"Custom fields are data
  (`field_definitions` + `field_values`) so new fields need no migration"* — and
  `FieldDefinition` (`:235`) already has `type`, `applies_to`, `unit` and `group`. An HS code
  as a `text` field applying to `both` is a single row, no schema change.
- **Origin is already on the item**, sort of: `Item.supplier_country` (`models.py:60`).
- **Purchased-ness is per tier.** `DecidedCost.make_or_buy` (`models.py:222`) is `buy` /
  `made-to-order` / `make`, and it lives on the tier row, not the item. So *whether* duty
  applies is a per-tier question even though the HS code is a property of the physical thing.
- `CogsFacility` (`models.py:398`) has `code`, `kind`, `name` — **no country**. "Our facility
  in Portugal" is not modelled anywhere yet.

### The three real problems

1. **`supplier_country` is not country of origin.** Customs charges on where a thing was
   *made*, not where it was bought. A German distributor shipping a Chinese-made part is a CN
   origin at a DE supplier, and the duty is completely different. Reusing `supplier_country`
   for duty would be quietly wrong on exactly the parts that matter most. This wants its own
   field — `country_of_origin` — defaulting to `supplier_country` but separately editable.
2. **A duty rate is a function of three things, not one.** `(HS code, country of origin,
   destination)` → rate. The EU has free-trade agreements with a long list of origins where
   the rate is 0%, plus anti-dumping duties on specific HS/origin pairs that dwarf the base
   rate. "The duty for HS 8504.40" is not a well-formed question without an origin.
3. **Where the rates come from.** The authority is the EU TARIC database. It has no
   dependable free API, it changes, and the app currently makes no external calls except to
   Drive. So: an admin-maintained table, seeded from a TARIC export, with the date it was
   valid on. That keeps the number auditable — which matters more here than freshness, because
   a landed cost that quietly changed under a quote is worse than one that is three months old
   and dated.

### Shape

- `Item` gets `hs_code` and `country_of_origin`, or both arrive as `field_definitions` rows.
  Recommend **real columns**: a custom field is right for something only humans read, and
  these two are cost inputs that `BomGraph`-adjacent code has to reach. A `field_values` EAV
  lookup inside a rollup is the wrong shape.
- A small `duty_rates` table: `hs_code`, `origin_country`, `destination_country`, `rate_pct`,
  `valid_from`, `note`, `source`. Longest-prefix match on the HS code, so a 4-digit heading
  covers everything under it until someone enters the 6- or 8-digit line.
- `CogsFacility` gets a `country`, so "landing in Portugal" is data rather than an assumption
  baked into a formula.
- Duty per item at a tier = decided unit cost × rate, and only where `make_or_buy` is `buy` or
  `made-to-order`. Rolls up like any other cost.

### Open

- **How the per-part figure meets the plant-level `duty` rung.** Three options: the rung
  becomes read-only and derived; the rung stays typed and the derived figure is shown beside
  it as a cross-check; or the rung becomes "everything not attributable to a part". The
  cross-check is the cheapest and the most honest first move — it says nothing is wrong yet,
  only that two numbers disagree by X.
- **Duty base.** Customs value is normally CIF — goods plus freight plus insurance to the EU
  border — not the ex-works price. Using the decided unit cost understates it. That drags
  `inbound` into the same calculation, which may be the reason to do both together.
- **VAT is not a cost.** Portuguese import VAT is recoverable, so it must never enter COGS.
  Worth writing down before someone adds 23%.
- **Missing data has to read as missing, not as zero.** An item with no HS code must show as
  uncosted for duty — item 6's coverage machinery is the pattern, and this is a new kind of
  gap for the Pending queue.
- **Assemblies.** A bought-in assembly (`covers = 'all'`) is one customs line with one HS
  code. An assembly built here from imported parts has duty on the parts and none on itself.
  The `covers` flag already distinguishes these, which is convenient.

## 13. Costing KPIs: drop "Most expensive", add € / kg

The fourth KPI tile shows the single dearest part (`Costing.jsx:133-137`, fed by
`topPart = data.parts[0]` at `:56`). It is already the first row of the breakdown below it and
the biggest block in the treemap beside it, so the tile spends a quarter of the KPI row
restating what two other things on the same screen already say.

Replace it with **€ / kg** — cost per kilogram of the whole BOM. Both inputs are already in the
same object and already on screen two tiles to the left: `shownCost` (`:53`) over
`data.totals.weight_grams / 1000` (`:124`). In the screenshot that is €6.9k over 136.83 kg,
about €50/kg.

- `frontend/src/components/Costing.jsx:133-137` — swap the tile's label, value and sub-line.
- `:56` — `topPart` becomes unused; check nothing else reads it before deleting.
- The value follows the scenario like the unit-cost tile now does, since it is derived from
  `shownCost`. At Min and Max it should move.

- Open: what the sub-line says. `fmtWeight` (`ui.jsx:90`) switches between g and kg at 1 kg, so
  a small BOM would want €/kg regardless rather than €/g — the tile is a density figure, and
  changing its unit under the reader defeats comparing two BOMs. Recommend fixing the unit at
  €/kg always, and putting the weight it divided by on the sub-line.
- Open: weight coverage is not cost coverage. A BOM where half the parts have no weight gives a
  €/kg that is silently too high. `Rollup` already tracks `weight_missing[]`, so the sub-line
  can say "over 136.83 kg · 4 parts unweighed" and stay honest.
- Open: divide by zero. A BOM with no weights at all must show "—", not infinity.

## 14. Coverage tile says nothing about weight coverage

The Coverage KPI reports cost coverage only — `153/157 inputs priced · 93/93 parts`
(`Costing.jsx:127-131`). Weight has exactly the same failure mode and no readout at all: the
Total weight tile says `136.83 kg · rolled up` whether every part was weighed or half of them
were never entered.

`Rollup` already tracks it — `weight_missing[]` is maintained all the way up
(`rollups.py`), it is simply not exposed. `tree.py:222` builds `totals` with `weight_grams` and
the cost coverage fields beside it, and stops there.

- `backend/app/routers/tree.py:222-235` — add `weight_covered` / `weight_total` /
  `weight_missing` to `totals`, mirroring how `covered` / `total` / `missing_assembly` are
  already served.
- `frontend/src/components/Costing.jsx:127-131` — a second line under the cost coverage line.
- This is also what makes item 13's € / kg honest, since that figure divides by a weight whose
  completeness is currently invisible. Ship them together.

- Open: one Coverage tile with two lines, or the weight figure on the Total weight tile where
  the number it qualifies actually lives? The second reads better; the first keeps all
  coverage in one place.

## 15. Pending's filter chips are a fixed list, and one of them never matches

The chips are hardcoded (`Pending.jsx:5-15`): `Any missing`, `Weight`, `Material`, `Country`,
`Cost`, `Facilities`. The backend emits a different vocabulary — `weight`, `material`,
`supplier_country`, `cost@1` / `cost@100` / `cost@10k`, `asm_time@1` / `@100` / `@10k`,
`quote@1` / `@100` / `@10k`, `cost_type` (`tree.py:277-304`). Three consequences:

- **The `Cost` chip can never match anything.** The counter is
  `items.filter((i) => i.missing.includes(c.key))` (`:28`) — exact array membership — and no
  item ever carries a bare `"cost"`, only `cost@1` and friends. It reads `0` on every database
  there has ever been. The screenshot shows `Cost 0` beside 103 pending items.
- **The dominant gap has no chip at all.** Every row in the screenshot is missing
  `asm_time@*`, and there is no way to filter to it.
- **Chips show at zero.** `Weight 0` and `Material 0` take up the bar saying nothing.

Fix: derive the chips from the data. Collect the distinct labels across every pending item,
count them, and render only the non-empty ones — which fixes the fixed list, the broken chip
and the zero-count chips in one move.

- `frontend/src/components/Pending.jsx:5-15` and `:28-33` — `FIELD_CHIPS` becomes a label map
  for display names (`supplier_country` → "Country") rather than the source of truth for which
  chips exist.
- Open: `cost@1` / `cost@100` / `cost@10k` as three chips, or one `Cost` chip matching any of
  them? Three is truthful and crowds the bar; one needs prefix matching rather than membership.
  Recommend grouping by prefix — `Cost`, `Assembly time`, `Quote` — with the tier in the chip's
  title attribute, since the row's own chips already say which tier.
- Depends on item 6: once covered items stop being flagged, several of these counts drop, and
  a chip that only existed because of covered assemblies should disappear on its own.

## 16. Undo a change from the History tab

The History tab lists every change with `old_value → new_value` (`History.jsx:71-72`) and no
way to act on it. Add an Undo button per row, behind a confirmation that names exactly what
will change.

### The rule that keeps this safe

**An undo is a new forward change, never a deletion of history.** `models.py:9` — *"History is
a single append-only `change_history` log"* — so undoing writes a fresh row restoring the old
value, with a `change_reason` pointing at the row it reverses. The log grows; nothing is
rewritten. That also means an undo can itself be undone, for free.

### What is addressable, and what is not

An undo needs `(entity_type, entity_id, field_changed)` to identify one scalar. Checked against
the actual call sites:

- `item` — `entity_id = item_id`, `field_changed` = the column (`edit.py:71`). Addressable.
- `bom_link` — `entity_id = "parent>child"` (`:481`, `:517`, `:591`). Addressable.
- `decided_cost` — `entity_id = item_id` but the tier rides in `field_changed` as
  `decided_cost@100` (`:710`, `:738`). Addressable.
- `field_value` — `entity_id = item_id`, `field_changed` = the field key (`:866`). Addressable.
- `cost_evidence` — `entity_id = item_id` and nothing identifies *which* evidence row
  (`:644`, `:664`). **Not addressable.** An item can have several.
- The four `cogs_*` kinds need the same audit before they get a button.

Where a row is not addressable, there is no button and the reason says why — not a button that
guesses.

### Scope for the first pass

- **`update` only.** Undoing a `create` means deleting, and undoing a `remove` on an item means
  recreating a part number — straight into `CodeRegistry`'s never-reuse rule. Both want their
  own thinking.
- **Exception: `bom_link` create and remove.** Adding or removing a component is the most
  common mistake and re-adding a link touches no part numbers. Safe, and the highest value.
- **Only the latest change to that `(entity, field)` is undoable.** Undoing an older row would
  silently discard every edit made since. An older row shows "superseded by a later change"
  instead.

### Mechanics

- `backup.py:198` `_coerce(value, column)` already turns a backup's text cell into the column's
  real type. `old_value` is `Text` (`models.py:165`), so this is exactly the coercion undo
  needs — reuse it rather than writing a second one.
- New endpoint, e.g. `POST /history/{id}/undo`. It re-reads the row, re-checks it is the latest
  for that field, applies the old value through the same path a normal edit would, and records
  the new history row.
- `History.jsx` — a button per eligible row, and a confirm dialog that spells out the item, the
  field, and `current → restored`. The repo already uses `window.confirm` for this kind of
  thing (`PartDrawer.jsx:1104`).

- Open: permission. Should undo be admin-only? It is a normal edit in effect, so probably not —
  but undoing someone else's change is socially different from undoing your own.
- Open: a bulk "undo everything since this point" is the obvious next ask, and is really item
  10's diff read backwards. Out of scope here; worth not designing against it.
