# Component mapping

Everything below is a ZEF design-system primitive already present in `_ds_bundle.js` /
`zef.css`. Nothing new needs designing.

| Region | Component / class |
| --- | --- |
| Tier switch, labour source, facility-type menu entries | `Segmented` |
| KPI tiles (four-up) | `KpiTile` |
| Explanatory banners | `Banner` (`tone="default"`, `icon="box"`) |
| Facility tree | `patterns/tree.css` — `.tree-row`, `.tree-row.on`, chevron via `Icon` `chevR`/`chevD` |
| Drawer | `Drawer` — `min(680px,96vw)`, `--shadow-2`, scrim `rgba(28,27,26,.18)` + 2px blur |
| Code / name fields | `Input` inside `.field-grid` |
| Matrix cells | `NumInput` (accepts decimal comma, rewrites to dot) |
| Layer cards on Costing | `Card` with head band |
| Buttons | `Button` — ghost for "Edit on Facilities", danger for delete |
| Module rows | `ModulePill` for AEC/DAC/MEU/ENC/CTL/DRY |
| Comparison bars | `Bar` |
| Coverage, if BOM coverage is surfaced | `CoverageBar` |

**Built ad hoc in the prototype, worth extracting as real components:**

- **`TierMatrix`** — the three-column editable/read-only matrix with lock column. Used twice
  (facility, sub-item) and it is the most intricate thing here. One component, driven by the
  row schema plus a per-row cell state.
- **`LockToggle`** — 14px checkbox, ink fill when on, `title` carrying the full explanation of
  what locking does for that row's basis. Native `title`, per house style.
- **`LayerTag`** — `L1`/`L2`/`L3` in mono at `--ink-3`.
- **`BreakdownList`** — label · source · value line list with hairline total.

**Design-system rules that were easy to get wrong in the prototype** — watch for them:

- Dropdown surfaces use `--bg-raised`, not `--paper`. Pure white is never a surface.
- No shadows except things that physically float: the drawer and the new-facility dropdown.
- Every figure is mono with tabular numerals. Every one.
- Radii: 2px chips, 4px buttons and fields, 6px cards. Never pill-shaped.
- Hover changes colour only — nothing moves, lifts or glows.
