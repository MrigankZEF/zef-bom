# Screens

Both screens live inside the existing app shell — 56px sticky top bar, nav tabs, page content
capped at 1480px. Add **Costing** (already exists — extend it) and **Facilities** (new) to the
tab set.

## Facilities

**Page head.** Title "Facilities", tier segmented control in the actions slot, then the
explainer:

> Every cost Costing adds on top of the BOM is entered here. Facilities are typed — an
> **assembly hall** carries floor, hours and tooling, **supply chain** carries inbound transport
> and outgoing shipping, **field works** carries installation and commissioning — so each type
> asks for its own numbers. Click any sub-item and fill its matrix against all three tiers; the
> rows are tagged with the COGS layer they land in.

**KPI tiles**, four-up: total cost at tier · per plant · overhead pool (layer 2) · headcount.

**Banner**, tone default, icon `box`: "Everything Costing adds on top of the BOM is entered
here" + a sentence naming the three layers' current values at the tier in force.

**Tree.** Uses the design system's `patterns/tree.css`. Facility rows carry a chevron toggle,
code, name and the type label in `.micro`; sub-item rows are indented 34px with no toggle.
Numeric columns are a shared `grid-template-columns: 44px 84px 84px 76px` on both header and
rows — FTE · L2 pool / yr · L1+L3 / plant · Cost @tier. Selection is the design system's red
8% fill + 2px red left edge.

**New facility** is a dropdown, not a button: three entries, one per type, each with a
one-line description of what that type carries. The type is chosen here and never again —
`"Pick a type — it is fixed once created."`

**Drawer**, `min(680px, 96vw)`, fixed right, main region gets a matching margin. Contents:
crumb (facility code) · code and name fields · facility type, displayed as text with
"fixed at creation" beside it · the cost matrix · derived totals · delete.

**The matrix** is the heart of the screen. One row per row-key for the record's kind, three
columns `@1 / @100 / @10k`. Each row: lock checkbox (facility level only, 14px, ink fill when
on) · label · unit in `.micro` · layer tag (`L1` / `L2` / `L3`) in mono at `--ink-3`.

Cell rendering by state:

| Record | Row | Cell |
| --- | --- | --- |
| facility | unlocked | read-only aggregate, `--ink-2` |
| facility | locked | editable input |
| sub-item | unlocked | editable input |
| sub-item | locked, rate basis | read-only inherited value, `--ink-3`, note "from facility" |
| sub-item | locked, quantity basis | em dash, `--ink-3`, note "at facility" |

Note the inversion: locking a row moves the input *up* to the facility. The checkbox column
therefore only appears on facility drawers.

**Derived rows** under the matrix, per tier: overhead full year (info slate) · direct per plant
(ink) · post-manufacturing per plant (warm) · total at this tier (red).

**Foot note.** "A locked rate — salary, labour rate, amortisation base — is inherited by every
sub-item; a locked quantity is counted once for the whole facility. Every number here feeds
Costing directly — L1 rows are direct cost per plant, L2 rows are the whole-year overhead pool,
L3 rows are added after the plant leaves the hall."

## Costing

Read-only. Banner: "The BOM comes from the BOM tool — every other cost is entered on
Facilities."

Left column: BOM by module (existing treatment). Right column: three cards, one per layer, each
a list of `{ label · source facility code · value }` lines with a hairline-ruled total and a
"Edit on Facilities" ghost button. Then the COGS waterfall and KPI tiles.

Card titles: "1 · Direct manufacturing cost" · "2 · Manufacturing overhead" (meta: "pool ÷
{batch}") · "3 · Post-manufacturing" (meta: "produced = sold").

The per-card notes are computed sentences, not static copy — they name the tier, the yield loss,
the labour hours, the per-plant overhead. Keep that; it is how this product explains itself.
