# The cost model

The whole calculator is one function: `compute(tier) -> COGS per plant`. Everything else is
data entry and presentation. Implement this as a pure module with no React in it
(`src/lib/cogs.js`) and unit-test it directly.

## Three layers on top of the BOM

```
                BOM cost per plant           <- from the BOM tool, at this tier
  ÷ yield       grossed up for scrap loss
  + layer 1     direct labour + metered utilities + tooling amortisation
  ─────────────────────────────────────────
  = direct manufacturing cost
  + layer 2     overhead pool (full year) ÷ units produced this tier
  ─────────────────────────────────────────
  = fully burdened manufacturing cost
  + layer 3     outbound shipping + crating + travel + install + commissioning
              + warranty accrual (% of burdened)
  ─────────────────────────────────────────
  = COGS per plant
```

Layer 2 is the only layer expressed as an annual pool. Layers 1 and 3 are per-plant.
Layer 3's warranty row is a *percentage of the burdened cost*, so it must be computed after
layer 2 — order matters.

## Facilities are typed

A facility has a **type**, chosen when it is created and **immutable thereafter**. The type
determines which rows the facility's cost matrix has — nothing else. There are three:

### `assembly` — Assembly & manufacturing
Floor, indirect staff and equipment sit in the overhead pool; hours, scrap, metered utilities
and tooling land in layer 1 as direct cost per plant.

| Row key | Label | Unit | Basis |
| --- | --- | --- | --- |
| `area` | Floor area | m² | `info` |
| `fte` | Indirect headcount | FTE | `fte` |
| `salary` | Average salary | € / yr | `salary` |
| `rent` | Rent & facilities | € / yr | `oh` |
| `dep` | Equipment depreciation | € / yr | `oh` |
| `maint` | Maintenance & calibration | € / yr | `oh` |
| `util` | Indirect utilities | € / yr | `oh` |
| `hours` | Direct labour hours | h / plant | `hours` |
| `rate` | Blended labour rate | € / h | `rate` |
| `scrap` | Scrap / yield loss | % of BOM | `scrap` |
| `meter` | Metered utilities | € / plant | `direct` |
| `toolTotal` | Tooling & fixtures | € total | `toolTotal` |
| `toolUnits` | Amortised over | plants | `toolUnits` |

### `logistics` — Supply chain & logistics
Inbound transport and customs are direct cost on the plant; the warehouse itself is overhead;
outgoing shipping and crating sit below the manufacturing line in layer 3.

| Row key | Label | Unit | Basis |
| --- | --- | --- | --- |
| `area` | Floor area | m² | `info` |
| `fte` | Headcount | FTE | `fte` |
| `salary` | Average salary | € / yr | `salary` |
| `rent` | Rent & yard | € / yr | `oh` |
| `systems` | Systems & customs admin | € / yr | `oh` |
| `inbound` | Inbound transport | € / plant | `direct` |
| `duty` | Customs & duties | € / plant | `direct` |
| `outbound` | Outgoing shipping | € / plant | `post` |
| `crating` | Packing & crating | € / plant | `post` |

### `field` — Field works
Crew payroll and field equipment are overhead; installation, commissioning, travel and the
warranty accrual are layer 3, added after the plant leaves the hall.

| Row key | Label | Unit | Basis |
| --- | --- | --- | --- |
| `fte` | Field crew | FTE | `fte` |
| `salary` | Average salary | € / yr | `salary` |
| `equip` | Field equipment & tools | € / yr | `oh` |
| `travel` | Travel & per diem | € / plant | `post` |
| `install` | Installation on site | € / plant | `post` |
| `commission` | Commissioning & handover | € / plant | `post` |
| `warranty` | Warranty accrual | % of burdened | `warranty` |

Adding a fourth type is adding one entry to the `KINDS` map. Nothing else in the model changes
— this is the point of the basis indirection, keep it.

## Basis — how a row enters the stack

`basis` is the only thing that decides what a number *means*. Given an accumulator and a row
value `v`, with `g(k)` reading a sibling row on the same record:

| Basis | Effect |
| --- | --- |
| `oh` | `acc.oh += v` — annual overhead, layer 2 |
| `fte` | `acc.oh += v * g("salary")`; `acc.fte += v` — payroll into layer 2 |
| `salary` | rate only; consumed by `fte`, contributes nothing itself |
| `direct` | `acc.other += v` — € per plant, layer 1 |
| `hours` | `acc.labour += v * g("rate")`; `acc.hours += v` — layer 1 |
| `rate` | rate only; consumed by `hours` |
| `toolTotal` | `acc.other += g("toolUnits") ? v / g("toolUnits") : 0` — layer 1 |
| `toolUnits` | divisor only; consumed by `toolTotal` |
| `scrap` | `acc.yieldF *= 1 - clamp(v, 0, 95) / 100` — multiplicative, see below |
| `post` | `acc.post += v` — € per plant, layer 3 |
| `warranty` | `acc.warrPct += v` — % of burdened, layer 3 |
| `info` | `acc.area += v` — carried for display, never costed |

Rows whose basis is `salary`, `rate` or `toolUnits` are **rates**: they are never summed into
a total, and when aggregated for display they are averaged over the non-zero values, not added.
Everything else sums.

### Scrap compounds
Yield losses at different stations multiply, they do not add: two stations at 2% each give
`0.98 × 0.98 = 0.9604`, a 3.96% loss, not 4%. Carry a yield *factor* through the whole rollup
and gross the BOM by `bomRaw / yieldFactor` once at the end. Do not sum scrap percentages.

## Row locking — the inheritance rule

Every row in a facility's matrix carries a **lock checkbox**, settable only at facility level.
This is the mechanism that keeps a facility from being a pile of unrelated numbers.

**Unlocked (default).** The row is entered on each sub-item and rolls up to the facility. At
facility level the row is read-only, showing the aggregate (a sum, or an average for rate rows).

**Locked.** The row is entered **once, on the facility**, and is read-only on every sub-item.
What "inherited" then means depends on the basis:

- **Rate rows** (`salary`, `rate`, `toolUnits`) — `inherits(basis) === true`. The facility's
  value flows *down* into every sub-item's arithmetic. Lock `salary` on the assembly hall and
  every cell's headcount is costed at that one salary. Sub-items display the inherited value,
  greyed.
- **Quantity rows** (everything else) — the value is counted **once at facility level** and the
  sub-items contribute nothing for that row. Lock `rent` and the hall pays one rent, not one per
  cell. Sub-items display an em dash.

Implementation shape (from the prototype):

```js
inherits(basis)  // salary | rate | toolUnits
isLocked(f, k)   // !!(f.lock && f.lock[k])
parentVal(f, k, tier)  // f.p[k][tier] — the facility's own value for a locked row

// per sub-item: skip locked rows entirely, but read inherited rates through the getter
getter(f, it, tier) => (k) => {
  const r = rowOf(f, k);
  if (r && isLocked(f, k)) return inherits(r.basis) ? parentVal(f, k, tier) : 0;
  return num(it.d[k][tier]);
}
itemRoll(f, it, tier)  // for each unlocked row: contrib(acc, basis, g(k), g)
ownRoll(f, tier)       // for each locked row:   contrib(acc, basis, parentVal(...), g)
facRoll(f, tier)       // merge(ownRoll, ...items.map(itemRoll))
allRoll(tier)          // merge over all facilities
```

Note `ownRoll`'s getter: when costing a locked `fte` row it needs a salary, which may itself
be locked (use the facility value) or not (average the sub-items'). Get this case right — it is
the one place the two directions of the rule meet.

**On locking a row**, seed the facility's value from the current aggregate so the number does not
jump. On unlocking, leave the sub-item values as they are — do not distribute the parent value
down.

## A facility with sub-items has no editable fields of its own

Other than locked rows, a facility drawer is a pure rollup: every cell read-only, showing the
aggregate of its children. This was an explicit decision — two places to type the same number is
a reconciliation bug waiting to happen.

## COGS assembly

```js
function compute(tier, facilities, bomByModule, labourSource) {
  const R = allRoll(tier, facilities);

  const bomRaw = sum(bomByModule, m => m.cost[tier]);
  const bomAdj = bomRaw / (R.yieldF || 1);

  // "Inside the BOM cost" means the BOM tool's number already carries labour;
  // adding hours x rate on top would double-count it.
  const labour = labourSource === "bom" ? 0 : R.labour;

  const direct    = bomAdj + labour + R.other;
  const poolTotal = R.oh;                    // full year
  const oh        = poolTotal / tier;        // per plant
  const burdened  = direct + oh;
  const warranty  = burdened * R.warrPct / 100;
  const addons    = R.post + warranty;

  return { bomRaw, bomAdj, direct, poolTotal, oh, burdened, warranty, addons,
           cogsUnit: burdened + addons,
           cogsYear: (burdened + addons) * tier,
           hours: R.hours, area: R.area, fte: R.fte,
           scrapPct: (1 - R.yieldF) * 100, warrPct: R.warrPct };
}
```

`tier` is doing double duty as both the tier key and the annual unit count — that is
intentional and correct here (the tier *is* "plants produced per year"), but name the parameter
`unitsPerYear` in the real implementation and pass the tier separately if you ever decouple them.

## Breakdown lines

Costing shows each layer as a list of named lines, sourced from the facility that carries them.
`breakdown(tier, layer)` walks every facility × every row, computes just that row's
contribution (respecting locks), and emits `{ label, src: facility.code, value }` for any
non-zero result. Two labels are rewritten for the reader: basis `fte` shows as
"Payroll, indirect", basis `hours` as "Direct labour".
