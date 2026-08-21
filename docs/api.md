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
- `GET /pending` — items missing required fields
- `POST /items/{id}/attachments` — create/locate Drive folder, return URL
- `PUT /items/{id}/thumbnail` — `{file_id}`; pin a Drive file as the item's picture
  (`null` clears it)
- `GET /items/{id}/thumbnail` — the pinned image's bytes, proxied from Drive with a short
  server-side cache. 404 when nothing is pinned, 503 when Drive isn't configured
