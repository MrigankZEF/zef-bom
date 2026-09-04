# ZEF · COGS Calculator — developer handoff

A working prototype of the **COGS (cost of goods sold) calculator** for ZEF · BOM, ready to be
implemented as a real screen pair inside `zef-bom-frontend`. This folder is the specification;
`reference/COGS-Calculator.html` is the prototype itself — open it in a browser and click
through it before writing any code.

## What this is

The BOM tool answers *"what do the parts cost?"*. It does not answer *"what does a plant cost
to make and deliver?"*. This calculator closes that gap: it takes the rolled-up BOM cost from
the BOM tool and stacks three further layers on top of it, at each of the three volume tiers
(`@1`, `@100`, `@10k`), to produce a fully burdened COGS per plant.

Two screens:

| Screen | Role |
| --- | --- |
| **Costing** | Read-only. Shows the BOM by module, then the three cost layers rolled up, then the COGS waterfall and KPI tiles. No cost is entered here. |
| **Facilities** | The single input surface. A tree of typed facilities and their sub-items; a drawer on the right holds each record's cost matrix across all three tiers. |

The hard rule that shapes the whole thing: **every cost that is not the BOM is entered on
Facilities, exactly once, against all three tiers.** Costing is a reading of that data.

## Read these in order

1. `COST_MODEL.md` — the arithmetic. Read this first; it is the actual product.
2. `DATA_SCHEMA.md` — persistence shape, API surface, migration notes.
3. `SCREENS.md` — layout, interaction and copy for both screens.
4. `COMPONENTS.md` — which ZEF design-system primitives each region uses.
5. `IMPLEMENTATION.md` — suggested file layout, build order, test cases.
6. `seed-facilities.json` / `seed-modules.json` — the prototype's data, as fixtures.

## Non-negotiables

- **Three tiers, always.** Every cost value is a triple keyed `1 | 100 | 10000`. There is no
  single-value cost anywhere in this model. A cost that genuinely does not vary is entered three
  times with the same number — do not add a "same at all tiers" shortcut, it hides the question.
- **Overhead is a whole-year pool, divided by units produced.** It is *not* the same pool at
  every tier: a hundred plants a year needs more floor, supervision and depreciation than one.
- **Produced = sold.** No inventory split, no WIP. This is the accounting policy in force; do
  not add a capitalisation toggle.
- **COGS = burdened manufacturing cost + freight + install/commissioning + warranty accrual.**
  This is locked. An earlier version had a policy switch; it was removed deliberately.
- **Unknown is an em dash**, never `0`, never `N/A`. Follow the design system on this.
