import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Icon, LayerTag, LockToggle, NumInput, Pill, fmtEURcompact, toNum } from "./ui";

// Every cost the COGS ladder adds on top of the BOM is entered here. Facilities are typed,
// and the type decides which rows the matrix asks for — so the screen is one tree, one
// drawer, and one matrix that changes shape with the record it is showing.
//
// Two rules this file exists to honour:
//   * Nothing is written until Save. The matrix stages edits locally and diffs on save,
//     matching the part drawer, so a stray click cannot change a costing.
//   * The frontend does zero arithmetic. Every figure rendered here — aggregates, per-rung
//     roll-ups, totals — is served by /cogs/facilities. Anything summed on this side would
//     be a second implementation of the ladder, in a language the tests cannot reach.

import { TIERS, DEFAULT_TIER, tierLabel } from "../tiers";

const cellKey = (itemId, rowKey, tier) => `${itemId}|${rowKey}|${tier}`;

// A stored value may legitimately be 0, so "is there a value" is an existence test, never
// a truthiness test. This is the em-dash rule in one function.
const has = (v) => v !== undefined && v !== null && v !== "";
const fmtCell = (v, dp = 2) =>
  has(v) ? Number(v).toLocaleString("en-US", { maximumFractionDigits: dp }) : "—";

export default function Facilities({ version, onChanged }) {
  const [schema, setSchema] = useState(null);      // { kinds, tiers, own_sentinel }
  const [data, setData] = useState(null);          // { facilities, totals }
  const [tier, setTier] = useState(DEFAULT_TIER);
  const [sel, setSel] = useState(null);            // { facilityId, itemId | null }
  const [error, setError] = useState(null);
  const [menu, setMenu] = useState(false);

  const load = () =>
    api.cogsFacilities().then(setData).catch((e) => setError(e.message));
  useEffect(() => { api.cogsKinds().then(setSchema).catch((e) => setError(e.message)); }, []);
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [version]);

  const OWN = schema?.own_sentinel ?? "";
  const kinds = useMemo(
    () => Object.fromEntries((schema?.kinds || []).map((k) => [k.kind, k])),
    [schema],
  );
  const facilities = data?.facilities || [];
  const totals = data?.totals?.[tier];

  const selected = sel && facilities.find((f) => f.id === sel.facilityId);
  const selectedItem = selected && sel.itemId
    ? selected.items.find((i) => i.id === sel.itemId)
    : null;

  const reload = () => load().then(() => onChanged && onChanged());

  const createFacility = async (kind) => {
    setMenu(false);
    const label = kinds[kind]?.label || kind;
    const code = window.prompt(
      `New ${label}.\n\nFacility code — letters and one hyphen, e.g. FAC-ASM.\n` +
      `The type is fixed once created.`,
      "FAC-",
    );
    if (!code) return;
    const name = window.prompt(`Name for ${code}?`, label);
    if (!name) return;
    try {
      const f = await api.createCogsFacility({ code: code.trim().toUpperCase(), kind, name: name.trim() });
      await reload();
      setSel({ facilityId: f.id, itemId: null });
    } catch (e) { setError(e.message); }
  };

  if (error) return <div className="page"><p className="err">Facilities failed to load: {error}</p></div>;
  if (!schema || !data) return <div className="page"><p className="muted">Loading facilities…</p></div>;

  return (
    <div className="page" style={selected ? { marginRight: "min(680px, 96vw)" } : undefined}>
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Cost inputs · per volume tier</div>
          <h1 className="page-title">Facilities</h1>
          <p className="page-sub">
            Every cost Costing adds on top of the BOM is entered here. Facilities are typed —
            an <strong>assembly hall</strong> carries floor, scrap and tooling,{" "}
            <strong>supply chain</strong> carries inbound transport and outgoing shipping,{" "}
            <strong>field works</strong> carries installation and commissioning — so each type
            asks for its own numbers. Click any sub-item and fill its matrix against all three
            tiers; the rows are tagged with the COGS layer they land in.
          </p>
        </div>
        <div className="page-actions">
          <div className="seg" role="group" aria-label="Volume tier">
            {TIERS.map((t) => (
              <button key={t} aria-pressed={tier === t} onClick={() => setTier(t)}
                      title={`${t.toLocaleString()} plants per year`}>
                {tierLabel(t)}
              </button>
            ))}
          </div>
          <NewFacilityMenu open={menu} setOpen={setMenu} kinds={schema.kinds} onPick={createFacility} />
        </div>
      </div>

      <div className="kpi-grid">
        <div className="kpi">
          <span className="kpi-label">Facility cost / yr</span>
          <span className="kpi-val">{fmtEURcompact(totals?.year_total)}</span>
          <span className="kpi-sub">pool + per-plant × {tier.toLocaleString()}</span>
        </div>
        <div className="kpi accent">
          <span className="kpi-label">Per plant</span>
          <span className="kpi-val">{fmtEURcompact(totals?.per_plant_total)}</span>
          <span className="kpi-sub">on top of the BOM</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Overhead pool · L2</span>
          <span className="kpi-val">{fmtEURcompact(totals?.pool_year)}</span>
          <span className="kpi-sub">full year ÷ {tier.toLocaleString()} plants</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Headcount</span>
          <span className="kpi-val">{fmtCell(totals?.fte, 1)}<span className="u">FTE</span></span>
          <span className="kpi-sub">{fmtCell(totals?.area, 0)} m² of floor</span>
        </div>
      </div>

      <div className="banner" style={{ marginBottom: 16 }}>
        <Icon name="box" size={15} className="ico" />
        <div>
          <h4>Everything Costing adds on top of the BOM is entered here</h4>
          <p>
            At {tier.toLocaleString()} plants/yr: direct cost{" "}
            <strong>{fmtEURcompact(totals?.direct_per_plant)}</strong> per plant (L1), an overhead
            pool of <strong>{fmtEURcompact(totals?.pool_year)}</strong> for the year —{" "}
            <strong>{fmtEURcompact(totals?.overhead_per_plant)}</strong> per plant (L2) — and{" "}
            <strong>{fmtEURcompact(totals?.post_per_plant)}</strong> per plant after it leaves the
            hall (L3), plus a {fmtCell(totals?.warranty_pct, 2)}% warranty accrual.
            {totals?.scrap_pct > 0 && ` Scrap loses ${fmtCell(totals.scrap_pct, 3)}% of the BOM.`}
          </p>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div className="tree-head tree-row" style={{ cursor: "default" }}>
          <div className="tree-name" style={{ cursor: "default" }}>
            <span className="micro" style={{ color: "var(--ink-3)" }}>Facility · sub-item</span>
          </div>
          <div className="fac-data">
            <span title="Headcount">FTE</span>
            <span title="Layer 2 — the whole-year overhead pool">L2 pool / yr</span>
            <span title="Layers 1 and 3 — direct and post-manufacturing cost per plant">L1+L3 / plant</span>
            <span title={`Everything this facility adds per plant at ${tier.toLocaleString()}/yr`}>
              @{tierLabel(tier)} / plant
            </span>
          </div>
        </div>

        {facilities.length === 0 && (
          <div className="empty" style={{ padding: 24, textAlign: "center", color: "var(--ink-3)" }}>
            No facilities yet. Add one — its type decides which costs it asks for.
          </div>
        )}

        {facilities.map((f) => {
          const r = f.rollup?.[tier];
          const isSel = sel?.facilityId === f.id && !sel?.itemId;
          return (
            <div key={f.id}>
              <div className={`tree-row ${isSel ? "on" : ""}`}
                   style={{ "--indent": "12px", "--tint": "transparent" }}>
                <div className="tree-name opens" title="Open this facility"
                     onClick={() => setSel({ facilityId: f.id, itemId: null })}>
                  <span className="num">{f.code}</span>
                  <span className="lbl assembly">{f.name}</span>
                  <span className="micro" style={{ color: "var(--ink-3)" }}>
                    {kinds[f.kind]?.label || f.kind}
                  </span>
                  {f.archived && <Pill kind="warm" title="Archived — still costed? No: archived facilities are excluded from the ladder.">archived</Pill>}
                  {!!f.locks.length && (
                    <Pill kind="info" title={`Entered once on the facility: ${f.locks.join(", ")}`}>
                      {f.locks.length} locked
                    </Pill>
                  )}
                </div>
                <div className="fac-data" title="Open this facility"
                     onClick={() => setSel({ facilityId: f.id, itemId: null })}>
                  <span className={has(r?.fte) && r.fte ? "" : "muted-cell"}>{fmtCell(r?.fte, 1)}</span>
                  <span className={r?.pool_year ? "" : "muted-cell"}>{fmtEURcompact(r?.pool_year)}</span>
                  <span className={r?.direct_per_plant + r?.post_per_plant ? "" : "muted-cell"}>
                    {fmtEURcompact((r?.direct_per_plant || 0) + (r?.post_per_plant || 0))}
                  </span>
                  <span className={r?.per_plant_total ? "" : "muted-cell"}>
                    {fmtEURcompact(r?.per_plant_total)}
                  </span>
                </div>
              </div>

              {f.items.map((it) => {
                const itSel = sel?.facilityId === f.id && sel?.itemId === it.id;
                return (
                  <div key={it.id} className={`tree-row ${itSel ? "on" : ""}`}
                       style={{ "--indent": "34px", "--tint": "rgba(28,27,26,0.014)" }}>
                    <div className="tree-name opens" title="Open this sub-item"
                         onClick={() => setSel({ facilityId: f.id, itemId: it.id })}>
                      <span className="num">{it.code}</span>
                      <span className="lbl">{it.name}</span>
                    </div>
                    {/* A sub-item's numbers only mean something inside its facility's
                        roll-up — showing a per-plant total per cell would invite adding
                        them up, which double-counts every locked row. */}
                    <div className="fac-data" title="Open this sub-item"
                         onClick={() => setSel({ facilityId: f.id, itemId: it.id })}>
                      <span className="muted-cell">{fmtCell(it.values?.fte?.[tier], 1)}</span>
                      <span className="muted-cell">—</span>
                      <span className="muted-cell">—</span>
                      <span className="muted-cell">—</span>
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      <p className="micro" style={{ color: "var(--ink-3)", marginTop: 12, maxWidth: 860 }}>
        A locked rate — salary, amortisation base — is inherited by every sub-item; a locked
        quantity is counted once for the whole facility. Every number here feeds Costing
        directly: L1 rows are direct cost per plant, L2 rows are the whole-year overhead pool,
        L3 rows are added after the plant leaves the hall.
      </p>

      {selected && (
        <FacilityDrawer
          key={`${selected.id}:${sel.itemId || "own"}`}
          facility={selected}
          item={selectedItem}
          kind={kinds[selected.kind]}
          own={OWN}
          tier={tier}
          onClose={() => setSel(null)}
          onOpenItem={(itemId) => setSel({ facilityId: selected.id, itemId })}
          reload={reload}
          setError={setError}
        />
      )}
    </div>
  );
}

// ── new facility ─────────────────────────────────────────────────────────────
// A dropdown rather than a button, because the type is the decision — it is chosen here and
// never again, and each entry says what that type carries so the choice is informed.
function NewFacilityMenu({ open, setOpen, kinds, onPick }) {
  return (
    <div className="fac-menu">
      <button className="btn sm" onClick={() => setOpen(!open)} title="Pick a type — it is fixed once created.">
        + Facility
      </button>
      {open && (
        <>
          {/* Click-away, so the menu does not need a document listener. */}
          <div style={{ position: "fixed", inset: 0, zIndex: 55 }} onClick={() => setOpen(false)} />
          <div className="fac-menu-pop">
            {kinds.map((k) => (
              <button key={k.kind} onClick={() => onPick(k.kind)}>
                <span className="k">{k.label}</span>
                <span className="d">{k.blurb}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── the drawer ───────────────────────────────────────────────────────────────
function FacilityDrawer({ facility, item, kind, own, tier, onClose, onOpenItem, reload, setError }) {
  const isFacility = !item;
  const record = item || facility;
  const recordId = item ? String(item.id) : own;

  // Staged edits, keyed by cell. Nothing is written until Save — the same contract the part
  // drawer keeps, so a stray click cannot change a costing.
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState(facility.name);

  const stored = (rowKey, t) =>
    (item ? item.values?.[rowKey]?.[t] : facility.own?.[rowKey]?.[t]);

  const staged = (rowKey, t) => {
    const k = cellKey(recordId, rowKey, t);
    return k in draft ? draft[k] : undefined;
  };
  const shown = (rowKey, t) => {
    const s = staged(rowKey, t);
    return s !== undefined ? s : (has(stored(rowKey, t)) ? String(stored(rowKey, t)) : "");
  };
  const setCell = (rowKey, t, v) =>
    setDraft((d) => ({ ...d, [cellKey(recordId, rowKey, t)]: v }));

  // A cell counts as changed when its parsed value differs from what is stored — so
  // retyping the same number, or tidying "1.50" to "1.5", is not a change.
  const changes = Object.entries(draft).reduce((acc, [k, raw]) => {
    const [itemId, rowKey, t] = k.split("|");
    const tierNum = Number(t);
    const before = itemId === own ? facility.own?.[rowKey]?.[tierNum] : item?.values?.[rowKey]?.[tierNum];
    const after = raw === "" ? null : toNum(raw);
    const same = (after == null && !has(before)) || (after != null && has(before) && Number(before) === after);
    if (!same) acc.push({ item_id: itemId, row_key: rowKey, volume_tier: tierNum, value: after });
    return acc;
  }, []);

  const save = async () => {
    if (!changes.length) return;
    setBusy(true);
    try {
      await api.saveCogsValues(facility.id, changes);
      setDraft({});
      await reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const toggleLock = async (rowKey, next) => {
    if (changes.length && !window.confirm(
      "Locking changes what every cell in this row means, and you have unsaved edits.\n\n" +
      "Discard them and continue?")) return;
    setBusy(true);
    try {
      setDraft({});
      if (next) await api.lockCogsRow(facility.id, rowKey);
      else await api.unlockCogsRow(facility.id, rowKey);
      await reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const renameFacility = async () => {
    if (name === facility.name) return;
    try { await api.patchCogsFacility(facility.id, { name }); await reload(); }
    catch (e) { setError(e.message); }
  };

  const addItem = async () => {
    const code = window.prompt("Sub-item code — e.g. A-LINE.", "");
    if (!code) return;
    const nm = window.prompt(`Name for ${code}?`, "");
    if (!nm) return;
    try {
      const it = await api.addCogsFacilityItem(facility.id, {
        code: code.trim().toUpperCase(), name: nm.trim(), sort_order: facility.items.length,
      });
      await reload();
      onOpenItem(it.id);
    } catch (e) { setError(e.message); }
  };

  const removeItem = async () => {
    if (!window.confirm(`Delete ${item.code} — ${item.name}?\n\nIts figures at all three tiers go with it.`)) return;
    try { await api.deleteCogsFacilityItem(facility.id, item.id); await reload(); onClose(); }
    catch (e) { setError(e.message); }
  };

  const removeFacility = async () => {
    if (!window.confirm(
      `Delete ${facility.code} — ${facility.name}?\n\n` +
      `Its ${facility.items.length} sub-item(s) and every figure they carry go with it. ` +
      `Archive instead if you want to keep the numbers.`)) return;
    try { await api.deleteCogsFacility(facility.id); await reload(); onClose(); }
    catch (e) { setError(e.message); }
  };

  const r = facility.rollup?.[tier];

  return (
    <>
      {/* No scrim. The page beside this drawer is shifted, not covered — same as the part
          drawer in Browse — and dimming a page that has already moved out of the way reads
          as a modal the drawer is not. Closing is the button in the header. */}
      <div className="drawer">
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>
            <div className="micro" style={{ color: "var(--ink-3)" }}>
              {facility.code}{item ? ` · ${item.code}` : ""}
            </div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 700 }}>
              {record.name}
            </div>
          </div>
          <button className="btn ghost sm" onClick={onClose} title="Close">
            <Icon name="close" size={11} />
          </button>
        </div>

        <div className="drawer-body">
          {isFacility ? (
            <div className="field-grid" style={{ marginBottom: 14 }}>
              <label className="full">
                <span className="micro" style={{ color: "var(--ink-3)" }}>Name</span>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)}
                       onBlur={renameFacility} />
              </label>
              <div>
                <span className="micro" style={{ color: "var(--ink-3)" }}>Code</span>
                <div className="mono" style={{ fontSize: 12.5, paddingTop: 4 }}>{facility.code}</div>
              </div>
              <div>
                <span className="micro" style={{ color: "var(--ink-3)" }}>Type</span>
                <div style={{ fontSize: 12.5, paddingTop: 4 }}>
                  {kind?.label || facility.kind}{" "}
                  <span className="micro" style={{ color: "var(--ink-4)" }}
                        title="The type decides which rows the matrix has, so changing it would strand every figure already entered.">
                    fixed at creation
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <p className="micro" style={{ color: "var(--ink-3)", marginTop: 8 }}>
              A sub-item of {facility.code} — {kind?.label || facility.kind}. Locked rows are
              entered on the facility, not here.
            </p>
          )}

          <TierMatrix
            rows={kind?.rows || []}
            locks={facility.locks}
            isFacility={isFacility}
            solo={!facility.items.length}
            aggregates={facility.aggregates}
            ownValues={facility.own}
            shown={shown}
            setCell={setCell}
            toggleLock={toggleLock}
            busy={busy}
          />

          <div style={{ marginTop: 16 }}>
            <div className="micro" style={{ color: "var(--ink-3)", marginBottom: 4 }}>
              {facility.code} contributes
            </div>
            <div className="derived" style={{ color: "var(--ink-3)" }}>
              <span />
              {TIERS.map((t) => <span key={t} style={{ fontSize: 10 }}>@{tierLabel(t)}</span>)}
            </div>
            <DerivedRow cls="oh" label="Overhead pool · full year" pick={(x) => x.pool_year} rollup={facility.rollup} />
            <DerivedRow cls="dir" label="Direct · per plant" pick={(x) => x.direct_per_plant} rollup={facility.rollup} />
            <DerivedRow cls="post" label="Post-manufacturing · per plant" pick={(x) => x.post_per_plant} rollup={facility.rollup} />
            <DerivedRow cls="total" label="Total · per plant at this tier" pick={(x) => x.per_plant_total} rollup={facility.rollup} />
            <p className="micro" style={{ color: "var(--ink-4)", marginTop: 6 }}>
              The overhead row is a whole year. The others are per plant — at @{tierLabel(tier)} the
              pool works out to {fmtEURcompact(r?.overhead_per_plant)} a plant.
            </p>
          </div>

          {isFacility && (
            <div style={{ marginTop: 18 }}>
              <div className="micro" style={{ color: "var(--ink-3)", marginBottom: 6 }}>
                Sub-items — where the numbers are entered
              </div>
              {facility.items.map((it) => (
                <div key={it.id} className="breakdown-row" style={{ cursor: "pointer" }}
                     onClick={() => onOpenItem(it.id)} title="Open">
                  <span className="breakdown-label">{it.name}</span>
                  <span className="breakdown-src mono">{it.code}</span>
                  <span className="breakdown-val mono"><Icon name="chevR" size={11} /></span>
                </div>
              ))}
              {!facility.items.length && (
                <p className="muted" style={{ fontSize: 12.5, margin: "4px 0" }}>
                  None yet. A facility with no sub-items has nothing to enter against.
                </p>
              )}
              <button className="btn ghost sm" style={{ marginTop: 8 }} onClick={addItem}>+ Sub-item</button>
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 22, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn" disabled={!changes.length || busy} onClick={save}>
              {busy ? "Saving…" : changes.length ? `Save ${changes.length} change${changes.length === 1 ? "" : "s"}` : "Saved"}
            </button>
            <button className="btn ghost" disabled={!changes.length || busy} onClick={() => setDraft({})}>
              Cancel
            </button>
            <span style={{ flex: 1 }} />
            {item
              ? <button className="btn danger sm" onClick={removeItem}>Delete sub-item</button>
              : <button className="btn danger sm" onClick={removeFacility} title="Admin only. Archive instead to keep the numbers.">Delete facility</button>}
          </div>

          <p className="micro" style={{ color: "var(--ink-4)", marginTop: 10 }}>
            An empty cell is “not entered” — it shows an em dash and costs nothing. Type 0 when
            you mean a known zero.
          </p>
        </div>
      </div>
    </>
  );
}

// ── the matrix ───────────────────────────────────────────────────────────────
// The most intricate thing on the screen: one row per row-key for the record's kind, three
// tier columns, and a cell whose rendering depends on where it is and what is locked.
//
// Note the inversion at the heart of it: locking a row moves the input UP to the facility.
// So the lock column exists only on a facility drawer, and a locked row is the one place a
// facility has an editable cell at all.
function TierMatrix({ rows, locks, isFacility, solo, aggregates, ownValues, shown, setCell, toggleLock, busy }) {
  const lockSet = new Set(locks || []);
  return (
    <div className={`matrix ${isFacility ? "" : "no-lock"}`}>
      <div className="matrix-head">
        <span className="lock-cell" title="Lock a row to enter it once, on the facility" />
        <span className="micro" style={{ color: "var(--ink-3)" }}>Row</span>
        <span />
        {TIERS.map((t) => <span key={t}>@{tierLabel(t)}</span>)}
      </div>

      {rows.map((row) => {
        const locked = lockSet.has(row.k);
        return (
          <div className={`matrix-row ${locked ? "locked" : ""}`} key={row.k}>
            <span className="lock-cell">
              {/* Locking moves a row's input up to the facility and stops the sub-items
                  contributing it. With no sub-items there is nothing to move it away from,
                  so a NEW lock has nothing to do — but an existing one must still be
                  clearable, or a facility locked before sub-items existed would be stuck
                  with locks nobody can reach. */}
              <LockToggle
                locked={locked}
                inherits={row.inherits_when_locked}
                disabled={busy || !isFacility || (solo && !locked)}
                soloNote={solo}
                onChange={(next) => toggleLock(row.k, next)}
              />
            </span>
            <span className="matrix-label">
              {row.label}<span className="u">{row.unit}</span>
            </span>
            <LayerTag basis={row.basis} />

            {TIERS.map((t) => (
              <Cell
                key={t}
                row={row} tier={t} locked={locked} isFacility={isFacility} solo={solo}
                aggregate={aggregates?.[row.k]?.[t]}
                ownValue={ownValues?.[row.k]?.[t]}
                shown={shown} setCell={setCell} busy={busy}
              />
            ))}
          </div>
        );
      })}
    </div>
  );
}

function Cell({ row, tier, locked, isFacility, solo, aggregate, ownValue, shown, setCell, busy }) {
  // A facility with no sub-items IS the record — there is nothing below to roll up, so
  // every cell is entered here. Otherwise a facility only owns its locked rows.
  const editable = isFacility ? (solo || locked) : !locked;

  if (editable) {
    return (
      <NumInput
        className="input mono cell"
        value={shown(row.k, tier)}
        onChange={(v) => setCell(row.k, tier, v)}
        disabled={busy}
        style={{ height: 25, textAlign: "right", fontSize: 11.5, padding: "0 7px" }}
      />
    );
  }

  // A facility's unlocked row is a pure roll-up of its sub-items — read-only, because two
  // places to type the same number is a reconciliation bug waiting to happen.
  if (isFacility) {
    const isRate = row.inherits_when_locked;
    return (
      <span className="cell-ro agg"
            title={isRate
              ? "The average of the sub-items' values — a rate, so averaged rather than summed. Lock the row to set it here instead."
              : "The sum of the sub-items' values. Lock the row to enter it once here instead."}>
        {fmtCell(aggregate)}
      </span>
    );
  }

  // A sub-item under a locked row. Which of the two things it shows is the whole lock rule.
  if (row.inherits_when_locked) {
    return (
      <span className="cell-ro inherited" title={`Inherited from the facility — every sub-item is costed at this ${row.label.toLowerCase()}.`}>
        {fmtCell(ownValue)}
      </span>
    );
  }
  return (
    <span className="cell-ro at-facility" title="Counted once at facility level — this sub-item contributes nothing for this row.">
      —
    </span>
  );
}

function DerivedRow({ cls, label, pick, rollup }) {
  return (
    <div className={`derived ${cls}`}>
      <span>{label}</span>
      {TIERS.map((t) => (
        <span key={t}>{rollup?.[t] ? fmtEURcompact(pick(rollup[t])) : "—"}</span>
      ))}
    </div>
  );
}
