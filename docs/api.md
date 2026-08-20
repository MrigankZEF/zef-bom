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
- `GET /rollup?root=&volume=` — `{cost, covered, total, coverage, missing[]}`
- `GET /costing/summary?volume=` — per-subsystem rollups + tier totals

## M4 — edit / cost
- `PATCH /items/{id}` — partial update; writes change_history
- `POST /items/{id}/duplicate`
- `PATCH /items/{parent}/children/{child}` — set a link's quantity (no re-code; writes change_history)
- `GET|POST|PATCH|DELETE /items/{id}/cost-evidence`
- `GET|PUT /items/{id}/decided-cost?volume=`
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
