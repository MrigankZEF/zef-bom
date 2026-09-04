# Data schema & API

## Shape

A per-tier value is always a triple. Store it as an object keyed by tier, not an array —
tier keys are meaningful and a fourth tier is plausible.

```ts
type Tier = 1 | 100 | 10000;
type TierValues = Record<Tier, number>;      // persisted as number, edited as string
type FacilityKind = "assembly" | "logistics" | "field";

interface FacilityItem {
  id: string;
  code: string;                 // "A-LINE" — short, uppercase, unique within facility
  name: string;
  values: Record<string, TierValues>;   // row key -> per-tier value
}

interface Facility {
  id: string;
  code: string;                 // "FAC-ASM"
  kind: FacilityKind;           // immutable after creation
  name: string;
  locks: Record<string, boolean>;        // row key -> locked
  ownValues: Record<string, TierValues>; // row key -> value, locked rows only
  items: FacilityItem[];
}
```

In the prototype these are `d`, `lock` and `p` respectively — rename on the way in.

**Row keys are schema, not data.** The `KINDS` map (see `COST_MODEL.md`) belongs in code, not
in the database: it defines which keys are valid for a kind, each key's label, unit and basis.
Store values against string keys and validate against the map on write. A value whose key is not
in its facility's kind is dead weight — the prototype preserves such orphans when a kind changes,
but since kind is now immutable, reject them instead.

## Suggested tables

```
facility          id, code, kind, name, created_at, created_by
facility_item     id, facility_id, code, name, sort_order
facility_value    facility_id, item_id NULL, row_key, tier, value
                    -- item_id NULL = the facility's own value for a locked row
facility_lock     facility_id, row_key, locked
```

One long `facility_value` table keyed `(facility_id, item_id, row_key, tier)` beats a wide
column-per-row table: the row set differs per kind and will grow.

## Endpoints

```
GET    /api/facilities                      -> Facility[] (with items and values)
POST   /api/facilities                      { code, name, kind }        kind required
PATCH  /api/facilities/:id                  { code?, name? }            kind NOT accepted
DELETE /api/facilities/:id                  cascades to items
POST   /api/facilities/:id/items            { code, name }
PATCH  /api/facilities/:id/items/:itemId    { code?, name?, values? }
DELETE /api/facilities/:id/items/:itemId
PUT    /api/facilities/:id/locks/:rowKey    { locked, ownValues? }
GET    /api/cogs?tier=100                   -> computed result + breakdown lines
```

`PATCH .../items/:itemId` with `values` should accept a sparse patch — one row key, one tier —
because that is how the matrix is edited: cell by cell. Debounce on the client, don't
round-trip per keystroke.

The kind rejection on PATCH is deliberate: the type is fixed at creation, and the UI offers no
way to change it. Enforce it server-side too.

## BOM integration — the open decision

The prototype seeds module costs as constants (`seed-modules.json`). In the real app they must
come from the BOM tool's existing rollup, at the tier in force. Two things to settle before
building:

1. **Which BOM.** The calculator needs a designated top-level BOM ("the microplant"), or a BOM
   selector on Costing. The prototype assumes one.
2. **Does the BOM cost already include labour?** The `labourSource` segmented control exists
   because the answer differs by team: if the BOM's part costs are bought-in prices, labour is
   this calculator's job; if they include assembly labour, adding hours × rate double-counts.
   The control currently defaults to "Hours × rate, here". Confirm with the cost engineers and
   consider making it a stored setting rather than a per-session toggle.

Coverage is worth surfacing: the BOM tool already knows what share of a subtree has a decided
cost. A COGS number built on 60%-covered BOM is a floor, and Costing should say so in the same
voice the BOM tool uses — *"90 parts in this BOM have no decided cost yet — the total is a floor
until they're filled in"*.
