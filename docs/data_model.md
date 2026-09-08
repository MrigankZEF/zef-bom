# Data model

Source of truth: `backend/app/models.py` + Alembic migrations. This doc explains
the *intent*. Design rule throughout: **derive, don't store** — parentage,
where-used, and rollups are queries, never denormalised onto the item.

## Core

### `items`
The master list of physical things. PK is the **part number** (`item_id`, e.g.
`AEC001A`). `item_type` is `part` or `assembly`. `is_top_level` marks an assembly
as the root of a top-level BOM (there can be several). Cost is **not** stored here
(see below). `stage`/lifecycle is intentionally omitted for the MVP.

### `item_links`
Outward links on an item — supplier page, alternative supplier, shop, datasheet, info.
A table rather than columns because the count varies per item (three alternative
suppliers on one part, none on the next). `link_type` holds a `reference_values` value
(category `link_type`), so the kinds are admin-managed like suppliers and materials.
One-off `supplier_part_number` lives on `items` instead, since there is exactly one.

### `bom_links`
Parent → child edges with `quantity`. The **only** place structure lives.
"Where-used" = rows where `child_item_id = X`. The BOM tree = recursive walk over
`parent_item_id`. Unique on (parent, child).

### `change_history`
Append-only, field-level audit log: `(entity_type, entity_id, field_changed,
old_value, new_value, change_type, changed_at, changed_by)`. This is the **single**
historization mechanism (no `item_revisions`). It powers per-item history, the
global History feed, "BOM as of date X", and doubles as a human-readable backup
trail. Soft-delete = a `remove` entry, not a hard delete.

## Cost — two layers

### `cost_evidence` (0..n per part)
`unit_cost` and `source_type` are nullable — a row may be a plain costing note ("asked
them, waiting on a price") with a note and/or a link and no price at all. Nothing derives
cost from this table, so a priceless row is harmless; rollups read `decided_costs` only.
Supporting evidence: `quote | invoice | estimate_math | estimate_web | estimate_ai`,
each with supplier, country, currency, `unit_cost`, `volume_tier`, date,
`confidence`, optional `cost_min`/`cost_max`, note, and `attachment_url`. Optional —
some parts have several, some none.

### `decided_costs` (one per part per volume tier)
The number the user **commits to** as an educated call: `unit_cost_eur` at a
`volume_tier` (1 / 100 / 10000…), with `confidence`, `make_or_buy`, `basis_note`, and an optional
pointer to the evidence row that informed it. **Rollups sum these decided numbers**
— they never auto-pick a quote. A decided cost is only ever read for an item with
**no live children**: once something has contents it is costed from those contents plus
its assembly labour, and any decided cost stored on it is ignored (the drawer flags this). Unique on (item, volume_tier).

### `assembly_labor`
Minutes to assemble an item from its direct children (3-point, per volume tier);
cost = time × the item's cost-type rate. `covers_subassemblies` marks an outsourced or
bought-in assembly whose single quoted cost already includes the work on everything
beneath it — descendants then stop counting as missing an assembly cost. It is **per
tier** because sourcing differs by volume (built in house at @1, outsourced at @10k).
It affects coverage reporting only; the rollup arithmetic is unchanged. A descendant
that carries its own assembly cost under a covering ancestor is reported in
`covered_conflict[]`.

### Sourcing (`decided_costs.make_or_buy`)
**One source of truth, per volume tier.** `buy` (off the shelf) · `made-to-order` (our
specs) · `make` (in house). It lives on `decided_costs`, not on `items`, because sourcing
genuinely differs by volume — e.g. `UN023P` is make@1, buy@100, buy@10k. The old
`items.make_or_buy` column was dropped in migration 0009 (its values were pushed down
onto the tiers first); the retired value `modified-buy` maps to `made-to-order`, and the
restore path applies the same mapping so an older backup can't reintroduce it.
Consequence: sourcing can only be recorded once a part has a price at that tier.

### `code_registry`
Append-only ledger of every `(module, number)` ever issued — **never** deleted. Backs
the allocation rule in `naming_rules.md` §4c so a retired number is never reissued. Keyed
on module + number rather than the full code, because `allocate_code` has always matched
on module alone and a part promoted P→A keeps its number: the number is the identity,
the suffix is the type. Backfilled from `items` (live and archived) plus every code
recoverable from `change_history`.

## Custom fields — addable without migration

### `field_definitions`
`key`, `label`, `type` (`enum|number|boolean|url|text`), `applies_to`
(`part|assembly|both`), `required`, `options` (JSONB), `unit`, `group`. Adding a new
field is a row here — **no schema migration**.

### `field_values`
`(item_id, field_key) → value`. EAV store for user-added fields. Core fields stay
real columns on `items`.

## Ingestion

### `upload_batches`
One Miro OPML import: `source_filename`, `uploaded_by`, `notes`, `is_top_level_bom`,
`status` (`pending_review | approved | rejected`), and `summary_json` (the full diff
payload, for audit and replay).

## Derived views / queries (M3)
- `where_used(item_id)` — parents from `bom_links`.
- `bom_tree(root)` — recursive CTE with qty multipliers.
- `rollup_cost(root, volume)` / `rollup_weight(root)` — recursive sums returning
  `{cost, covered, total, missing[], missing_assembly[], covered_conflict[]}`.
  **Coverage counts assemblies as well as leaves**: a leaf needs a decided cost, an
  assembly needs a cost type + a labour time at that tier. Counting only leaves meant an
  unpriced assembly was invisible and the row still read 100%.
- `assembly_time_total(root)` — recursive sum of assembly minutes.

## The COGS ladder — facilities (M7)

Four rungs on top of the rolled-up BOM, at each of the three volume tiers. Four tables,
and **not one column in the schema is per-root**: facilities are global and fully allocated
to whichever BOM is on screen, and the volume tier *is* plants produced per year — so there
is no `units_per_year` field and no scenario table.

- `cogs_facility` — `id`, `code` UQ, `kind`, `name`, `archived`, `created_at/by`,
  `updated_at/by`. `kind` is `assembly | logistics | field`, chosen at creation and
  **immutable thereafter** (rejected on PATCH server-side, not merely hidden in the UI):
  it decides which rows the facility's matrix has, so changing it would strand every value
  already entered under a row key the new kind does not have.
- `cogs_facility_item` — `id`, `facility_id` FK, `code`, `name`, `sort_order`,
  UQ(`facility_id`, `code`). The sub-item the matrix is actually entered on.
- `cogs_value` — `id`, `facility_id` FK, `item_id`, `row_key`, `volume_tier`, `value`,
  UQ(`facility_id`, `item_id`, `row_key`, `volume_tier`). One cell of the matrix.
- `cogs_lock` — `id`, `facility_id` FK, `row_key`, UQ(`facility_id`, `row_key`).

### `cogs_value.item_id` is NOT NULL, and `''` means the facility itself

The empty string is the sentinel for "the facility's own value, for a locked row"
(`cogs.OWN`). A nullable column is the obvious shape and the wrong one: **NULLs compare
distinct in a unique constraint on both SQLite and Postgres**, so the constraint would
silently permit unlimited duplicate facility-own rows — and `backup._natural_key_cols`
drives restore's dedup off exactly that first unique constraint, so a restore would
multiply them.

It holds `str(cogs_facility_item.id)`, **not** the sub-item's code. The code would read
better in an Excel backup, but renaming one would orphan every value under it — which is
the `assembly_labor`-orphaned-by-a-re-code bug invited back in for legibility. There is no
FK on it, because a column that also holds `''` cannot carry one; that is the cost of the
sentinel.

Consequence for backups: Excel cannot tell `''` from an empty cell, so `backup._coerce`
restores a blank in any NOT NULL text column as `''` rather than NULL.

### `value` is Numeric(16,4)

Matching `decided_costs.unit_cost_eur`, not Float. The overhead pool reaches
€218,170,000 at @10k on the fixtures, and every value round-trips through an Excel backup.

### Row absence is the em dash

An **absent** row means "not entered": it displays `—` and contributes 0. A **stored 0**
means "known to be zero" and displays `0`. That is why nothing is ever pre-seeded with
zeros, and why clearing a cell deletes it rather than writing 0.

### `cogs_lock` carries no boolean

Presence is locked. A `locked=false` row is state you then have to keep consistent with
deletion, for no gain.

### Where `KINDS` lives, and the actor argument

The row schema — which rows each kind has, and each row's `basis` — lives in
`backend/app/cogs.py`, **in code**, against the house rule that new fields should be data
(`field_definitions` + `field_values`). The reason is the actor. A row's `basis` is
*behaviour*: a branch in `cogs.contrib`. In the database an admin could type a basis no
handler implements and get a silently uncosted row. `field_definitions` exists because
**users** add fields; facility rows get added by **us**, when we add a basis handler.
Different actor, different home. A row key not in its facility's kind is rejected on write.

### Two rows that price a third

Not every row is a standalone figure. Two pairs are a quantity and the rate that prices it,
and neither half means anything alone:

- `area` (m²) × `rent` (€/m²/yr) → the overhead pool
- `fte` (FTE) × `salary` (€/yr) → the overhead pool

A third pair is a total and its divisor: `toolTotal` (€) ÷ `toolUnits` (plants) → overhead,
**per plant**. That one is accumulated separately from the annual pool (`Acc.oh_unit`),
because the pool is divided by the tier and this figure must not be — amortising over a fixed
plant count means a fixed cost per plant at every volume.

The rate halves (`salary`, `rent`, `toolUnits`) are the rows that *inherit downward* when
locked; every other row is counted once at facility level instead. See `cogs.RATE_BASES`.

### A facility with no sub-items owns every row

Normally a facility owns only its locked rows — the rest are entered on the sub-items and
roll up. A facility with **no** sub-items is the record itself: every row is read from its
own values, locked or not. Without that rule such a facility could be filled in completely
and contribute nothing, its values sitting under the `item_id = ''` sentinel unread.

### Derived

- `cogs.compute(units_per_year, rollup_cost, rollup_assembly_cost, facilities)` — the
  ladder. L1 is a *partition* of `rollup_cost` (material = cost − assembly_cost), not a
  second sum, so the BOM view and the COGS view cannot drift.
- `cogs.breakdown(facilities, layer)` — each rung as named lines, computed through the same
  `contrib` and lock getters as the total.
