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
