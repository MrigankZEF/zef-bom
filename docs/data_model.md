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
Supporting evidence: `quote | invoice | estimate_math | estimate_web | estimate_ai`,
each with supplier, country, currency, `unit_cost`, `volume_tier`, date,
`confidence`, optional `cost_min`/`cost_max`, note, and `attachment_url`. Optional —
some parts have several, some none.

### `decided_costs` (one per part per volume tier)
The number the user **commits to** as an educated call: `unit_cost_eur` at a
`volume_tier` (1 / 100 / 10000…), with `confidence`, `basis_note`, and an optional
pointer to the evidence row that informed it. **Rollups sum these decided numbers**
— they never auto-pick a quote. Unique on (item, volume_tier).

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
  `{cost, covered, total, missing[]}` so coverage % and uncosted leaves surface.
- `assembly_time_total(root)` — recursive sum of assembly minutes.
