# API surface

Indicative; built out per milestone. Interactive docs at `/docs` (Swagger) when the
server runs. Frontend reaches these under `VITE_API_BASE` (default `/api`, proxied).

## Live now (M0)
- `GET /health` — `{status, items}`
- `GET /` — service banner
- `GET /items` — list; filters `module`, `item_type`, `top_level_only`, `q`
- `GET /items/{item_id}` — one item
- `GET /field-definitions` — custom field defs

## M3 — read / tree / rollup
- `GET /tree?root=&top_level_only=` — hierarchy (recursive)
- `GET /items/{id}/where-used` — parent assemblies
- `GET /rollup?root=&volume=` — `{cost, covered, total, coverage, missing[], missing_assembly[], covered_conflict[]}` (coverage counts assemblies too)
- `GET /costing/summary?volume=` — per-subsystem rollups + tier totals
- `GET /catalog` — flat list; costs are the **rolled-up** figures per tier (same numbers the drawer shows), not raw `decided_costs`

- `GET /flat?root=&volume=` — a BOM flattened to one level: one row per distinct descendant
  with `count` (the quantity multiplied along every path and summed over paths), `unit_cost`,
  `cost`, `share` of the BOM, `coverage`, `module_code` and `is_leaf`. Rows are sorted
  dearest-first with the unpriced last. Leaf rows sum to the parts cost; assembly rows carry
  their own process cost, so summing both double-counts — filter on `is_leaf`. The root's own
  labour is not a row; it comes back as `own_assembly_cost`.
- `GET /items/{id}/usage?volume=` — which top-level BOMs need this item and how many of it
  (`roots[]`, `total_count`, `shared`). `shared` = more than one BOM reaches it, in which case
  no single extended total means anything.

## M4 — edit / cost
- `PATCH /items/{id}` — partial update; writes change_history
- `POST /items/{id}/code` — `{mode: auto|manual, code, on_conflict: abort|merge, preview}`;
  change an item's number. `preview` reports what would happen and writes nothing.
- `POST /items/{id}/top-level` — `{is_top_level}`; promote/demote a BOM root. Validated
  (assembly with contents, no parents, system-coded) and runs the naming engine, returning
  any `renamed[]`. `is_top_level` is **not** settable via `PATCH /items/{id}`.
- `POST /items/{id}/duplicate` — `{item_name, allow_duplicate}`; copies core fields,
  decided costs (all tiers), assembly labour and custom field values into a fresh code.
  Does **not** copy cost evidence or Drive files (they document the original part).
  An assembly copies shallow — links to the same children. Lands in the catalog only.
- `PATCH /items/{parent}/children/{child}` — set a link's quantity (no re-code; writes change_history)
- `GET /items/{id}/links` — outward links (supplier page, alternative supplier, shop, …),
  ordered by `sort_order`
- `POST /items/{id}/links` — `{link_type, url, label, sort_order}`; `link_type` is a
  `reference_values` value (category `link_type`), so the kinds are admin-managed
- `DELETE /items/{id}/links/{link_id}`
- `GET|POST|PATCH|DELETE /items/{id}/cost-evidence` — every field is optional, but a POST
  needs at least one of price / note / link (422 otherwise): a row may be a quote *or* just
  a costing note
- `GET|PUT /items/{id}/decided-cost?volume=`
- `DELETE /items/{id}/decided-cost?volume=` — drop a decided cost (e.g. one stranded on an assembly)
- `PUT /items/{id}/field-values`
- `POST /field-definitions`

## M5 — uploads
- `POST /uploads` — OPML → parse → diff (status `pending_review`)
- `GET /uploads`, `GET /uploads/{id}/diff`
- `POST /uploads/{id}/approve` — per-row selections; atomic write
- `POST /uploads/{id}/reject`

## M6 — history / attachments / auth
- `GET /history?entity=&since=&as_of=` — change feed + "BOM as of date X". `entity` takes a
  comma-separated list. Beyond the per-item kinds, `reference_value` covers the admin lists
  (including the EUR/hour on an assembly cost type) and `database` covers whole-database
  events — a catalog wipe or a restore — one row each, naming the safety backup that holds
  the previous state. Those rows are written AFTER the event, because `change_history` is
  itself one of the tables a wipe or a restore replaces.
- `GET /pending` — items missing required fields. `missing[]` holds the gaps; `covered{tier:
  {state, by}}` holds the tiers where there is nothing to fill because the work is paid for
  above — `labor` (an ancestor's cover, whose cost the rollup still adds on top) or `boundary`
  (a quoted assembly, whose one price replaced the subtree). A row can carry only `covered`,
  which means it is in the list to explain itself, not to be worked through.
- `POST /history/{id}/undo` — put one change back. Rows from `/history` carry `undoable`,
  `undo_blocked` (why not, in words) and `undo_summary` (what would change). An undo is a
  **new forward change**, never a deletion: the log stays append-only, the original row
  stays, and an undo can itself be undone. Only the LATEST change to a field is offered —
  undoing an older one would silently discard everything since, so it is refused with 409.
  `cost_evidence` is never offered: its `entity_id` is the item and nothing records which
  evidence row changed. See `app/undo.py`.
- `POST /items/{id}/attachments` — create/locate Drive folder, return URL
- `PUT /items/{id}/thumbnail` — `{file_id}`; pin a Drive file as the item's picture
  (`null` clears it)
- `GET /items/{id}/thumbnail` — the pinned image's bytes, proxied from Drive with a short
  server-side cache. 404 when nothing is pinned, 503 when Drive isn't configured

## M7 — the COGS ladder / facilities

- `GET /api/cogs/kinds` — the matrix schema: each kind's rows in order, with unit, basis
  and `inherits_when_locked`. Served rather than duplicated in JS, so the frontend cannot
  go stale against a new basis handler. Also returns `tiers` and `own_sentinel` (`""`).
- `GET /api/cogs/facilities?include_archived=` — the whole tree in one call: facilities,
  sub-items, locks, and every cell keyed `row_key → tier → value`. Absent cells are absent,
  never null — the em dash is the missing key.
- `POST /api/cogs/facilities` — `{code, kind, name}`. Kind must be one of `KINDS`.
- `PATCH /api/cogs/facilities/{id}` — `{name?, archived?}` only. **A payload carrying
  `kind` is rejected with 422**, not ignored: the model is declared `extra="allow"`
  precisely so the handler can see it and refuse, because pydantic's default is to drop
  unknown fields, which would report a successful save that changed nothing. Any other
  unknown field is refused the same way.
- `DELETE /api/cogs/facilities/{id}` — **admin only**; takes the facility's sub-items,
  values and locks with it. Editors archive instead (`PATCH {archived: true}`), which keeps
  the numbers and the history.
- `POST /api/cogs/facilities/{id}/items` — `{code, name, sort_order?}`.
- `DELETE /api/cogs/facilities/{id}/items/{item_id}` — deletes its cells too; they are keyed
  by `str(id)`, so leaving them would make them unreachable rather than merely unused, and
  they would still be in the backup.
- `PATCH /api/cogs/facilities/{id}/values` — **the batched save.** `{cells: [{item_id,
  row_key, volume_tier, value}]}`, where `item_id: ""` is the facility's own value and
  `value: null` **deletes** the cell (back to "not entered" — not the same as storing 0).
  One transaction, one `change_history` entry per changed cell, nothing written until Save.
  The whole batch is validated first and rejected entirely if any cell names a row outside
  the facility's kind or a tier that is not a tier — a partial save would leave the screen
  disagreeing with the database.
- `PUT /api/cogs/facilities/{id}/locks/{row_key}` — locks a row, **seeding the facility's
  own value from the current aggregate at each tier** (a sum for a quantity row, the mean of
  the non-zero values for a rate row) so the total does not jump the moment the box is
  ticked. Returns `{locked, seeded: {tier: value}}`.
- `DELETE /api/cogs/facilities/{id}/locks/{row_key}` — unlocks, leaving the sub-item values
  exactly as they are. The parent value is *not* distributed downward; it stays stored, so
  re-locking returns to it.
- `GET /api/cogs/ladder?root=&volume=` — the four rungs for one root at one tier, with every
  intermediate (`bom`, `labour`, `bom_raw`, `bom_adj`, `other`, `consumables`, `direct`,
  `pool_total`, `overhead`, `burdened`, `post`, `warranty`, `cogs_unit`, …), a `coverage`
  block (`is_floor` when the BOM has gaps) and the `notes` the screen must say out loud.
  The frontend does zero arithmetic — it renders these fields.
- `GET /api/cogs/summary?root=` — all three tiers for one root. `root` is **required**.
- `GET /api/cogs/breakdown?volume=&layer=` — each rung as named lines with the facility that
  carries them. `layer` is `direct | overhead | post`.
- `GET /api/cogs/pending` — facility gaps in the same voice as `/pending`'s BOM gaps. Lives
  here rather than in `tree.py` so that router stays untouched by the ladder; `Pending.jsx`
  merges the two lists. A locked row is not a gap on a sub-item — it is not entered there by
  design.

## G — milestones and the diff

- `GET /api/milestones?root=` — newest first. **Payloads are never sent**: one is a few
  hundred kilobytes and nothing on screen reads it directly. Each row carries `counts`
  (`items`/`links`/`decided`/`labor`/`rates`) so the UI can say how big a snapshot is.
- `POST /api/milestones` — `{root_item_id, name, note?}`. **Top-level only** (409 otherwise):
  a sub-assembly's effective quantities depend on which parent you came through, so a
  comparison would not be of numbers anybody saw. A client cannot supply a payload — one it
  composed would be a snapshot of what the client believed.
- `DELETE /api/milestones/{id}` — **admin only**, and it really is gone. A payload is
  derivable from nothing.
- `GET /api/milestones/{id}/tree?volume=` — the frozen BOM as a tree, the same shape `/tree`
  returns, costed **now** at the requested tier. Refetch per tier rather than convert: a
  snapshot stores all three.
- `GET /api/milestones/{id}/diff?volume=` — `{counts, totals, rows[], history{}}`.
  `totals.accounted_delta` is the sum of the per-row contribution deltas and must equal
  `totals.cost_delta`; the UI shows a warning when it does not, because then the roll-up and
  the per-item breakdown disagree and one of them is wrong.

### A milestone stores inputs, and the diff needs no history

`bom_milestones.payload` holds only what a `BomGraph` is built from — items, links, decided
costs, assembly labour, and the assembly cost-type rates — scoped to the subtree, at all
three tiers, in `backup.py`'s cell format. No rolled-up cost, no coverage, no totals.

Both sides of a comparison are therefore costed by the same `BomGraph` from the same kind of
rows (see `app/rows.py`), which is what makes them comparable at all: give the frozen side its
own arithmetic and a difference in the output is a difference between two implementations as
much as between two states, with no way to tell which.

The diff itself is arithmetic over two complete states and uses `change_history` for **one**
thing: naming who touched a row. A hole in the log costs a sentence of provenance, never a
figure. This is deliberate and load-bearing — three write paths were logging nothing until
`71045fd`, so numbers reconstructed from the log would have been silently wrong.

### Archived rows are excluded at capture, not on read

A soft delete is a delete as far as the BOM is concerned, so an archived link was not in the
BOM when the snapshot was taken. Ten archived links survive inside the real AEC subtree, and
keeping them made a fresh milestone roll up to € 3,058 against the live € 2,872. Filtering on
the way back out instead would be wrong in the other direction: an item archived *after* a
snapshot must stay in the payload, or the diff loses the deletion it exists to show.

### There is no grand-total COGS endpoint, and there never will be

Facilities are global and fully allocated to whichever root is on screen. That is correct
for comparative simulation, and it makes two roots' COGS figures **unaddable**: sum them and
you have double-counted the company. `/costing/summary` does return a `grand_total` across
roots — legitimate for BOM cost, wrong by construction for COGS. That endpoint stays as it
is and COGS never joins it. Recorded here so the absence does not read as an oversight.

### COGS stays out of the CSV export, deliberately

`/export/csv` emits per-tier costs per row. COGS is a per-plant figure with no per-row
meaning, so there is nothing coherent to put in a column.

## H — customs and import duty

- `GET /api/duty/rates?destination=&include_archived=` — the rate table, plus
  `destination_default` (from `cogs_facility.country`, falling back to `PT`).
- `POST /api/duty/rates` — **admin only**. `{hs_code, destination_country, origin_country?,
  rate_pct, valid_from?, source?, note?}`. `rate_pct` is a percentage of the customs value
  (2.7 for 2.7%), because that is how every schedule and every broker states it and a units
  mix-up is a factor of 100 on a real invoice. `origin_country` empty = the third-country
  wildcard.
- `DELETE /api/duty/rates/{id}` — **admin only**, and it *archives*: a landed cost computed
  last quarter used that row, and why it was that number should stay findable.
- `GET /api/duty/bom?root=&volume=&destination=` — duty per line plus totals, gaps, and the
  `cross_check` against the ladder's typed `duty` rung.

### The four rules, and where they are enforced

`app/duty.py`'s docstring is the authority; each has a test in `test_invariants.py`.

1. **Import VAT is never in the figure.** Recoverable in Portugal — a cash-flow event, not a
   cost. Adding 23% would overstate cost of goods by roughly a quarter. `vat_excluded: true`
   is asserted so anyone adding a `vat_pct` has to come and read the rule.
2. **Only bought lines are dutiable**, from `DecidedCost.make_or_buy`. Something we make in
   the hall is not a customs line; the material under it is. That makes dutiability
   **per tier** even though an HS code belongs to the physical thing.
3. **A bought-in assembly is one customs line.** `flatten_leaves` already stops at
   `covers='all'`, which is exactly the right set — descending would invent lines no customs
   declaration ever had.
4. **A missing HS code is missing, not zero** — a floor with its own `coverage`, exactly as
   with cost.

### What it deliberately does not do

The customs value is **ex-works**, not CIF, so duty is understated by the inbound freight
and insurance to the border — the same money the ladder's `inbound` rung holds. `basis` names
what was used so the number never passes as a CIF value. Fixing it means deciding how
inbound freight is apportioned across lines, which is its own decision with its own inputs.

The typed `duty` rung is **not** replaced. The two are independent statements about the same
money and the difference is the useful part; whether the rung becomes derived, or is
redefined as "customs cost not attributable to a part", is a decision to make with a
classified BOM in front of you. The cross-check reads the rung through `cogs.breakdown`, never
by summing `cogs_value` — a locked row has both a facility-own cell and sub-item cells, so a
raw SUM double-counts exactly the rows that were locked.

Duty runs off a `BomGraph`, so a **milestone's** duty is computed by the same code. Items and
sourcing come from the graph; rates stay live, because a published tariff is a fact about the
world now rather than part of a snapshot. So a duty comparison across a milestone shows how
classification and sourcing moved, priced at today's schedule.
