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
- `GET /history?entity=&since=&as_of=` — change feed + "BOM as of date X"
- `GET /pending` — items missing required fields. `missing[]` holds the gaps; `covered{tier:
  {state, by}}` holds the tiers where there is nothing to fill because the work is paid for
  above — `labor` (an ancestor's cover, whose cost the rollup still adds on top) or `boundary`
  (a quoted assembly, whose one price replaced the subtree). A row can carry only `covered`,
  which means it is in the list to explain itself, not to be worked through.
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

### There is no grand-total COGS endpoint, and there never will be

Facilities are global and fully allocated to whichever root is on screen. That is correct
for comparative simulation, and it makes two roots' COGS figures **unaddable**: sum them and
you have double-counted the company. `/costing/summary` does return a `grand_total` across
roots — legitimate for BOM cost, wrong by construction for COGS. That endpoint stays as it
is and COGS never joins it. Recorded here so the absence does not read as an oversight.

### COGS stays out of the CSV export, deliberately

`/export/csv` emits per-tier costs per row. COGS is a per-plant figure with no per-row
meaning, so there is nothing coherent to put in a column.
