# The COGS ladder — build plan

Four rungs on top of the rolled-up BOM, at each of the three volume tiers. The prototype in
`handoff/` is where the model came from; this is the shape it takes once it has to live inside
the existing system. **Where this doc and `handoff/` disagree, this doc wins.**

Status: decisions closed, held for a fresh pull.

---

## 1. The arithmetic

Every figure is either read from the BOM or entered once on Facilities. Nothing is entered
twice, and nothing the BOM already knows is re-typed anywhere.

```
L1  BOM
      bom     = rollup.cost - rollup.assembly_cost
      labour  = rollup.assembly_cost

L2  Direct manufacturing cost
      direct  = rollup.cost / yieldFactor + consumables + other

L3  COGM — fully burdened manufacturing cost
      pool     = sum of every facility's annual overhead
      overhead = pool / tier            # tier IS plants per year
      burdened = direct + overhead

L4  COGS per plant
      warranty = burdened * warrantyPct / 100
      cogs     = burdened + freight + install + warranty
```

**L1** is one call — `BomGraph(db, volume_tier=v).rollup(root_id)` — split into its two halves,
not two separate sums. A bought-in assembly priced with `covers='all'` lands in the material
half, because that is what a supplier price is.

**L2** grosses the *whole* BOM cost, material and labour together, because scrap loses the hours
already invested. One yield factor over the full direct cost implies the loss happens at the end
of the line, so this is the upper bound — per-station scrap later comes in under it, never over.
Yield factors multiply across stations: two at 2% give 3.96%, not 4%.

**L3** is the only rung expressed as a whole-year pool. It is not the same pool at every tier —
a hundred plants a year needs more floor, supervision and depreciation than one.

**L4** is order-dependent: warranty is a percentage of the burdened figure, so it must compute
after overhead and it moves when the pool moves. Produced = sold; no inventory split, no WIP,
no capitalisation toggle.

### Why the split is the whole design

The BOM rollup already adds our assembly labour: `cost = parts + assembly_cost`, where
`assembly_cost` is `AssemblyLabor` minutes × the cost type's `rate_eur_h`. So L1 and the labour
term are a *partition* of a single number, and L2's first term is that same number whole. Part
cost and assembly cost are never cached, never re-derived, never copied — there is nothing for
the two views to drift apart on.

---

## 2. Design decisions

The handoff prototype ran in a browser with constant module costs and no database. It was the
source of the model, not a contract — so where it and the existing system disagree, the system
wins and the prototype bends. Each decision notes what the prototype did, as provenance rather
than as a standard we failed to meet.

### 2.1 Math moves to the backend

*Prototype:* a pure JS module in the frontend (`src/lib/cogs.js`), no backend involvement.

The ladder lives in `backend/app/cogs.py`, pure Python, no SQLAlchemy and no FastAPI. Three
reasons: the export router will want COGS and cannot import JSX; a ladder in JS beside a rollup
in Python is precisely the two-sources-that-drift problem being guarded against; and the
arithmetic net is `backend/tests/test_invariants.py`, which cannot reach a browser module.

### 2.2 Facility labour rows deleted (`hours`, `rate`)

*Prototype:* the assembly kind carries `hours` (h/plant) and `rate` (€/h), and a `labourSource`
segmented control decides whether to add them or trust the BOM.

Both rows are gone; direct labour comes only from the BOM. On the fixtures the two sources
differed by up to €16,520 per plant — adding both double-counts by that, picking wrong
understates by it. The `labourSource` toggle disappears with them, rather than shipping as a
per-session switch someone can leave set wrong.

Knock-ons: the `hours` and `rate` bases and the `labour`/`hours` accumulators are deleted; the
inherit-downward set shrinks from `{salary, rate, toolUnits}` to `{salary, toolUnits}`; assembly
hours are still displayed, sourced honestly from `assembly_time_total(root)`.

### 2.3 Scrap grosses everything, not material only

*Prototype:* `bomAdj = bomRaw / yieldF`, where `bomRaw` is material.

Same formula, but `bomRaw` is now `rollup.cost` — material *and* labour — because a scrapped
part loses the hours already spent on it. Scrap stays a generic facility-level assumption for
now; per-assembly scrap is a later refinement, and framing today's number as the upper bound
means that refinement reads as a tightening rather than a correction.

### 2.4 No scenario table

*Prototype:* a designated top-level BOM, plus an open question about binding a root and storing
units per year.

Tier *is* plants per year, so no `units_per_year` field. Facilities are global with full
allocation to whichever root is on screen, so nothing is per-root. The root comes from the
Costing tab's existing selector. Four tables, and not one column in the schema is per-root.

### 2.5 A consumables row is added

*Prototype:* no kind has a consumables row — `meter` is metered utilities, `toolTotal` is
tooling amortisation, `inbound`/`duty` are logistics.

Direct consumables are named in the ladder's second rung and had nowhere to live. One row on the
assembly kind: `consum`, "Direct consumables", €/plant, basis `direct`. The basis already
exists, so this costs nothing and the rung stops approximating itself with utilities.

### 2.6 Save button, not debounce

*Prototype:* sparse per-cell `PATCH`, debounced on the client.

The matrix follows the pattern already in the part drawer — *"nothing is written until Save, so a
stray click can't change a BOM"*: staged local state, diffed on save, a `Save N changes` button
carrying the count, Cancel discarding everything. One PATCH with the changed cells as a batch,
one history entry per changed cell, one transaction.

### 2.7 No grand-total COGS

*Prototype:* assumes a single microplant and never confronts multiple roots.

Full allocation per root means every root absorbs 100% of every facility pool — correct for
comparative simulation, and it makes two roots' COGS figures unaddable. Sum them and you have
double-counted the company. So there is no grand-total COGS endpoint, ever.

Note that `/costing/summary` already returns a `grand_total` across roots: legitimate for BOM
cost, wrong by construction for COGS. That endpoint stays as it is and COGS never joins it.

### 2.8 Three views, BOM+ first

*Prototype:* two screens, Costing read-only and rebuilt around layer cards.

A segmented switch in the existing control row:

```
[AEC066A ▾]  [1 unit] [100 units] [10k units]   [ BOM+ | COGM | COGS ]
```

**BOM+** is today's Costing tab entirely unchanged — treemap, volume chart, KPIs — plus the
direct-cost rungs. **COGM** and **COGS** are ladder, breakdown lists and waterfall, cumulative,
each showing every rung up to its own. The treemap belongs to BOM+ alone: it visualises BOM
structure, and there is no tree underneath overhead.

---

## 3. Schema

Migration `0015_cogs_facilities.py`.

```
cogs_facility        id, code UQ, kind, name, archived, created_at/by, updated_at/by
cogs_facility_item   id, facility_id FK, code, name, sort_order      UQ(facility_id, code)
cogs_value           id, facility_id FK, item_id, row_key, volume_tier, value
                                              UQ(facility_id, item_id, row_key, volume_tier)
cogs_lock            id, facility_id FK, row_key                     UQ(facility_id, row_key)
```

Four decisions are baked into those four lines, each for a reason that bites if reversed.

### `item_id` is NOT NULL DEFAULT `''`

Empty string means "the facility's own value, for a locked row". The prototype says NULL — but
NULLs compare distinct in a unique constraint on both SQLite and Postgres, so the constraint
would silently permit unlimited duplicate facility-own rows. Worse, `_natural_key_cols` drives
restore's dedup off exactly that first unique constraint, so a restore would multiply them.

### `value` is Numeric(16,4)

Matching `DecidedCost`, not Float. The overhead pool reaches €218,170,000 at @10k on the
fixtures, and every value round-trips through an Excel backup.

### `cogs_lock` carries no boolean

Presence is locked. A `locked=false` row is state you then have to keep consistent with
deletion, for no gain.

### Row absence is the em dash

Absent row = not entered: displays `—`, contributes 0. A stored `0` = known to be zero: displays
`0`. That is the prototype's "unknown is an em dash, never 0" made structural, and it is why
nothing is pre-seeded with zeros.

### Row keys stay in code, against the house rule

The house rule leans the other way: `models.py` says *"custom fields are data
(`field_definitions` + `field_values`) so new fields need no migration"*, and
`assembly_cost_type` already carries its `rate_eur_h` in `reference_values.meta`. The facility
matrix is the same shape.

It still stays in code, because a row's `basis` is *behaviour* — a branch in the arithmetic. In
the database an admin could type a basis no handler implements and get a silently uncosted row.
`field_definitions` exists because **users** add fields; facility rows get added by **us**, when
we add a basis handler. Different actor, different home. A key not in its facility's kind is
rejected on write, not stored.

### Backup, and the thing that caught us out

Four new sheets in `BACKUP_SHEETS`, and in `RESTORE_ORDER` as facility → facility_item → value →
lock, which satisfies the foreign keys on insert. Not merge-only. A forgotten table is how a
backup silently stops being a backup.

### Kind is immutable, roles are the existing ones

The type is chosen at creation and rejected on PATCH server-side, not merely absent from the UI.
Editor may edit, admin may delete a facility, viewer is read-only — the roles already in force
everywhere else.

---

## 4. Where the code lives

**New**

| File | What |
| --- | --- |
| `backend/app/cogs.py` | KINDS, contrib, item_roll, own_roll, fac_roll, all_roll, compute, breakdown. Plain data in, plain data out. |
| `backend/app/routers/cogs.py` | The only place the ladder meets `BomGraph(...).rollup(root)`. |
| `backend/alembic/versions/0015_cogs_facilities.py` | The four tables. |
| `frontend/src/components/Facilities.jsx` | Tree, drawer, tier matrix, lock toggles. The single input surface. |
| `backend/scripts/audit_cogs.py` | Beside `audit_rollups.py` — asserts the ladder identities against live data, so a bad edit surfaces without a test run. |

**Edited**

| File | What |
| --- | --- |
| `backend/app/models.py` | Four classes appended. One model registry, per the existing convention — a second one splits `Base.metadata` and leaves the test harness's `create_all` depending on an import that is easy to forget. |
| `backend/app/backup.py` | Three constants. |
| `backend/app/main.py` | One name in the router import list. |
| `frontend/src/components/Costing.jsx` | The view switch and the two new views. |
| `frontend/src/App.jsx` | One entry in TABS. |
| `frontend/src/components/Pending.jsx` | Merges facility gaps from `/cogs/pending` into the existing queue. |
| `frontend/src/components/History.jsx` | One "Facilities" filter chip. |

**Untouched:** `rollups.py`, `tree.py`, `export.py`, `operations.py`, `PartDrawer.jsx`,
`Tree.jsx`, `CostTreemap.jsx`.

The frontend does zero arithmetic — it renders `compute()`'s fields, every intermediate
included, so no figure on screen is ever a client-side sum.

`tree.py` staying untouched is why the facility gaps come from `/cogs/pending` in our own router
rather than from `/pending`, which lives there. `Pending.jsx` merges the two lists.

---

## 5. Fitting the existing system

A screen pair bolted on beside the BOM tool is not the same thing as a screen pair that belongs
to it. These are what belonging costs, and each is small. The prototype suggests none of them.

**Facilities join the Pending queue.** The "sit down and fill these in" tab already lists leaves
missing a decided cost and assemblies missing a time. A facility sub-item with no values at a
tier is exactly that kind of gap, and it should appear in exactly that voice rather than being
discoverable only by opening every drawer.

**History gains a filter chip.** `History.jsx` hardcodes its filter list, so the four `cogs_*`
entity types would land in "All" and be filterable by nothing. One chip, "Facilities". Its
`isItem` check correctly declines to link them to items, since they are not items.

**Reuse `VOLUME_TIERS`.** The router already declares the tier list. A second one is a second
thing to forget when a fourth tier arrives — and the prototype's own non-negotiable is that the
triple is never shortcut, which a drifting second list would quietly break.

**An audit script, not just tests.** `audit_cogs.py` beside `audit_rollups.py`. Tests prove the
arithmetic on fixtures; the audit proves it on the real BOM, which is where a wrong assumption
actually shows up.

**COGS stays out of the CSV export — deliberately.** The export emits per-tier costs per row.
COGS is a per-plant figure with no per-row meaning, so there is nothing coherent to put in a
column. Recorded as a decision so it does not read as an oversight later.

---

## 6. Proving it

The prototype's cases were implemented against the fixtures and run before any of this was
planned. All seven pass. The two that referenced `hours`/`rate` are dropped with those rows; six
new cases exist because the BOM is now inside the ladder.

**Green already**

- Scrap compounds — two sub-items at 2% gross by `1/0.9604`, not `1/0.96`. Measured 3.5621%
  across the fixtures, against 3.6000% for a naive sum.
- A locked rate inherits — `salary` locked at 90,000 over cells carrying 92k/88k/96k costs all
  headcount at 90,000; payroll is €405,000.
- A locked quantity counts once — `rent` locked at 50,000 puts 50,000 in the pool regardless of
  cell count, and the cells contribute nothing for that row.
- Locked `fte` with unlocked `salary` costs the pooled heads at the average of the non-zero cell
  salaries: 4 × €92,000 = €368,000. The awkward case where both directions of the rule meet.
- Tooling with zero `toolUnits` contributes 0, not infinity.
- Warranty moves when the pool moves — +€1M to overhead at @100 lifts the accrual, because it is
  a percentage of burdened.
- Tier isolation, and an empty facility contributes nothing without throwing.

**To write**

- `direct == rollup.cost / yieldFactor + consumables + other` exactly, at every tier. The
  identity that makes the L1/labour split safe.
- Editing an `AssemblyLabor` time moves the direct-cost line — no stale cache between `BomGraph`
  and `compute`.
- A `covers='all'` quoted assembly contributes its quote once and no labour of ours.
- COGS on a 0%-covered BOM is a flagged floor, never a silent number.
- Deleting `hours`/`rate` leaves no basis handler reachable and no accumulator orphaned.
- A row key absent from a facility's kind is rejected on write.

### One comment the code must carry

The salary average in `own_roll` is unweighted. Locking `fte` across cells with unequal headcount
therefore costs the pooled heads at a salary no single cell actually pays. That is correct per
spec and genuinely surprising, so it gets a comment at the site — otherwise the next reader
"fixes" it.

### Regression anchors

From the fixtures, no locks, with `hours`/`rate` deleted. The BOM column here is material only —
the labour half now comes from the BOM, so the golden test builds a small synthetic BOM in-test,
as `test_invariants.py` already does throughout, and commits figures from its first green run.

| Fixture rollup | @1 | @100 | @10k |
| --- | --- | --- | --- |
| BOM, material | 231,700.00 | 125,600.00 | 70,700.00 |
| after scrap × 1/yieldFactor | 240,258.29 | 129,055.93 | 72,061.25 |
| direct other | 6,720.00 | 5,470.00 | 4,240.00 |
| **L2 · direct mfg cost** | **246,978.29** | **134,525.93** | **76,301.25** |
| overhead pool, full year | 1,673,200.00 | 10,374,000.00 | 218,170,000.00 |
| overhead per plant | 1,673,200.00 | 103,740.00 | 21,817.00 |
| **L3 · COGM** | **1,920,178.29** | **238,265.93** | **98,118.25** |
| freight + install | 32,400.00 | 24,500.00 | 17,720.00 |
| warranty accrual | 48,004.46 | 5,241.85 | 1,766.13 |
| **L4 · COGS per plant** | **2,000,582.75** | **268,007.78** | **117,604.38** |
| overhead as share of COGS | 83.6% | 38.7% | 18.6% |

### Read the @1 column carefully

Overhead per plant at @1 is €1,673,200 against a €231,700 BOM — 84% of the COGS figure. The
model is behaving exactly as specified: the fixtures describe 10.7 FTE and 1,530 m² of floor, and
one plant a year absorbs all of it.

Which means **@1 is not "what does a prototype cost" — it is "what does a plant cost at a company
running one plant a year"**. Anyone reading the tier switch the first way gets a number eight
times too high and it looks authoritative. This is why the divisor is named on screen rather than
implied by the tier button.

---

## 7. What the screen says out loud

Each of these is an assumption the numbers depend on and the reader cannot infer. Stating them is
part of the feature, not documentation of it.

> **Overhead pool ÷ 100 plants/yr**

Named as a divisor on the overhead line, so the tier is never silently doing double duty as a
production rate.

> **Every facility fully allocated to this BOM. Figures for two BOMs cannot be added.**

Full allocation per root is right for comparative simulation and makes the totals non-additive.
Said where a reader might otherwise reach for a calculator.

> **Scrap applied to the full direct cost — loss assumed at end of line.**

The upper bound. Marks today's generic assumption as the conservative one, so per-assembly scrap
later reads as a tightening.

> **90 parts in this BOM have no decided cost yet — COGS is a floor until they're filled in.**

Coverage propagates all the way up. Same sentence pattern the BOM tool already uses on the
treemap, in the same voice, driven by `rollup.coverage` plus the quote and boundary gaps.

---

## 8. Build order

Numbers before pixels.

1. `cogs.py` and its tests. No UI, no endpoint. The thirteen cases green and the anchors
   committed before anything is rendered.
2. Migration 0015 and the backup wiring, with a round-trip test: back up, restore, assert the
   four tables come back whole.
3. `routers/cogs.py` against a real root, coverage and gaps included in the payload.
4. Facilities tree and drawer, reading only. Confirms the rollup columns against the fixtures.
5. Matrix editing with staged save and the change log. Locking last — it is the subtle part, and
   both directions of the inheritance rule need to be green before it ships.
6. The Costing view switch: BOM+ untouched, then COGM, then COGS.
7. Pending and History wiring, and the audit script.

Each step's documentation ships in that step's commit.

---

## 9. Documentation

Docs land with the code they describe, not as a sweep at the end. There is no `CLAUDE.md` in
this repo, which is worth knowing separately from this work.

| Doc | Gains |
| --- | --- |
| `docs/data_model.md` | The four tables, the `item_id = ''` sentinel and why, row-absence-is-em-dash, and where `KINDS` lives with the actor argument. |
| `docs/api.md` | Facility endpoints, the batched save PATCH, kind rejection on PATCH, and an explicit note that no grand-total COGS endpoint exists — with the reason. |
| `docs/validation_rules.md` | Row key must belong to the facility's kind; kind immutable after creation; scrap clamped 0–95%; facility and sub-item code patterns. |
| `docs/naming_rules.md` | Facility codes (`FAC-ASM`) and sub-item codes (`A-LINE`) are a new namespace with no relation to part numbers. That needs saying in the doc that otherwise governs every code in the system. |
| `README.md` | The costing section gains the ladder and the Facilities screen. |

---

## What survives in `handoff/`

`handoff/` is provenance. Four of its files were dropped once this plan superseded them — see
git history for `handoff/README.md`, `handoff/DATA_SCHEMA.md`, `handoff/IMPLEMENTATION.md` and
`handoff/seed-modules.json`. What remains is still load-bearing at build time:

| File | Why it stays |
| --- | --- |
| `COST_MODEL.md` | The full basis table and the lock inheritance rules, in more detail than this plan reproduces. |
| `SCREENS.md` | Layout, interaction and copy for the tree, drawer and matrix. Not reproduced anywhere else. |
| `COMPONENTS.md` | Which ZEF design-system primitive each region uses. |
| `facility-kinds.json` | The row schema per kind — source for `KINDS`, minus `hours`/`rate`. |
| `seed-facilities.json` | Fixture behind the regression anchors above. |
| `reference/COGS-Calculator.html` | The clickable prototype. |

`toNum` from `IMPLEMENTATION.md` survives as a note rather than a file: values are edited as
strings and parsed on read, so a half-typed `1.` or a decimal comma can exist in the field
without the model exploding. `NumInput`/`toNum` in `ui.jsx` already do this.

---

Fixture figures computed from `handoff/seed-facilities.json` and `handoff/facility-kinds.json`
by a throwaway implementation of the prototype's model, with the decisions above applied. Module
sums reconcile to the handoff's own anchors at all three tiers: 231,700 / 125,600 / 70,700.
