# Implementation notes

## Suggested file layout

```
src/
  lib/
    cogs.js            compute(), allRoll(), facRoll(), itemRoll(), ownRoll(), breakdown()
    facilityKinds.js   the KINDS map: rows, labels, units, bases, per-kind blurbs
    format.js          existing € / % / weight formatters — reuse, do not re-write
  api/
    facilities.js      fetch/patch wrappers
  screens/
    Facilities.jsx     tree + KPI + banner + drawer host
    Costing.jsx        extend the existing screen with the three layer cards
  components/
    FacilityTree.jsx
    FacilityDrawer.jsx
    TierMatrix.jsx
    LockToggle.jsx
    BreakdownList.jsx
```

`lib/cogs.js` must not import React. It takes plain data and returns plain data — that is what
makes the model testable and what will let a backend reuse it later.

## Build order

1. `facilityKinds.js` + `cogs.js` + tests. No UI. Get the numbers right against the fixtures
   first — the table below is your acceptance criteria.
2. Facilities tree reading the fixtures, no editing. Confirms the rollup columns.
3. Drawer with the matrix, sub-item editing only. No locking yet.
4. Locking, both directions, both bases. This is the subtle part.
5. Wire Costing's three layer cards to `breakdown()`.
6. Replace the module fixture with the real BOM rollup.
7. Persistence.

## Test cases worth writing first

- **Scrap compounds.** Two sub-items at 2% scrap → BOM grossed by `1/0.9604`, not `1/0.96`.
- **Locked rate inherits.** Lock `salary` at 90,000 on a facility whose sub-items carry
  70,000 and 100,000 → both cells cost at 90,000; the facility's payroll is
  `(fte₁ + fte₂) × 90,000`.
- **Locked quantity counts once.** Lock `rent` at 50,000 → facility overhead includes 50,000
  regardless of sub-item count; sub-item rolls show nothing for rent.
- **Locked `fte` with unlocked `salary`.** The facility's headcount is costed at the average
  of its sub-items' non-zero salaries. (The awkward case — assert it explicitly.)
- **Tooling with zero `toolUnits`** contributes 0, not `Infinity`.
- **Warranty is computed on burdened**, so it moves when overhead moves. Change the pool, assert
  the warranty figure changes.
- **Rate rows average, quantity rows sum** in facility-level aggregate display.
- **Empty facility** (no sub-items) with all rows unlocked → contributes nothing, does not throw.
- **Tier isolation.** Editing `@100` never changes `@1` or `@10k`.

## Reference figures from the fixtures

With the seed data, no locks set, labour source "Hours × rate, here". Use these as regression
anchors — recompute them from `seed-*.json` once your model runs, and commit the result.

| | @1 | @100 | @10k |
| --- | --- | --- | --- |
| BOM raw | € 231,700 | € 125,600 | € 70,700 |

The remaining figures depend on the full rollup; generate and commit them from your own first
green test run rather than transcribing from the prototype.

## Input handling

Values are edited as **strings** and parsed on read — this is what lets a half-typed `"1."` or
a decimal comma exist in the field without the model exploding. Keep the prototype's split:
strings in state, `toNum()` at the boundary. Reuse the BOM tool's existing `toNum`; it already
handles the decimal comma.

## What the prototype does NOT do

- No persistence — all state is in memory, reset on reload.
- No auth, no permissions. Facilities are cost data; consider who may edit them.
- No audit trail. The BOM tool has History; facility cost changes should land there too.
- No validation beyond numeric parsing. No warning on a facility with no sub-items, no check
  that a locked row has a value, no sanity bounds on scrap.
- No BOM integration — module costs are constants.
- Sub-item codes and facility codes are not checked for uniqueness.
