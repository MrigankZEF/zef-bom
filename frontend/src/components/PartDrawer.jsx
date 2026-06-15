import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Icon, ModulePill, Pill, fmtEURcompact, fmtPct, fmtWeight } from "./ui";
import { RefSelect, MultiRef } from "./RefInputs.jsx";

const COST_TIERS = [1, 100, 10000];
const SOURCES = ["quote", "invoice", "estimate_math", "estimate_web", "estimate_ai", "other"];
const tierLabel = (v) => (v >= 1000 ? `${v / 1000}k` : `${v}`);

function Accordion({ title, meta, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <button onClick={() => setOpen((o) => !o)} style={{ width: "100%", border: 0, background: "transparent", cursor: "pointer", display: "flex", alignItems: "center", gap: 8, padding: "12px 16px" }}>
        <Icon name={open ? "chevD" : "chevR"} size={13} />
        <span className="card-title" style={{ flex: 1, textAlign: "left" }}>{title}</span>
        {meta && <span className="card-meta">{meta}</span>}
      </button>
      {open && <div style={{ padding: "0 16px 16px", borderTop: "1px solid var(--hair)" }}>{children}</div>}
    </div>
  );
}

const Field = ({ label, children }) => (
  <div><span className="input-label">{label}</span>{children}</div>
);

export default function PartDrawer({ itemId, onClose, onOpenPart, onChanged }) {
  const [tab, setTab] = useState("details");
  const [tier, setTier] = useState(100);
  const [addingChild, setAddingChild] = useState(false);
  const [d, setD] = useState(null);
  const [form, setForm] = useState({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    // Archived items aren't in the live tree/rollup graph, so those calls 404 —
    // fail-soft to empty so an archived item still opens with its saved data.
    const safe = (p, fallback) => p.catch(() => fallback);
    const emptyRollup = { cost: 0, covered: 0, total: 0, coverage: 0, missing: [], weight_grams: null, assembly_time_min: 0 };
    const emptyNode = { item_id: itemId, has_children: false, children: [] };
    Promise.all([
      api.getItem(itemId),
      safe(api.rollup(itemId, 1), emptyRollup),
      safe(api.rollup(itemId, 100), emptyRollup),
      safe(api.rollup(itemId, 10000), emptyRollup),
      safe(api.whereUsed(itemId), []),
      safe(api.tree(itemId), emptyNode),
      safe(api.decidedCost(itemId), []),
      safe(api.costEvidence(itemId), []),
      safe(api.itemHistory(itemId), []),
      safe(api.assemblyLabor(itemId), []),
      safe(api.costTypes(), []),
    ])
      .then(([item, r1, r100, r10k, parents, node, decided, evidence, history, labor, costTypes]) => {
        const rollups = { 1: r1, 100: r100, 10000: r10k };
        setD({ item, rollups, rollup: r100, parents, node, decided, evidence, history, labor, costTypes });
        setForm({ ...item, materials: item.materials || (item.material ? [item.material] : []) });
      })
      .catch((e) => setError(e.message));
  };
  useEffect(() => { setD(null); setTab("details"); setAddingChild(false); load(); /* eslint-disable-next-line */ }, [itemId]);

  if (error) return <Shell><p className="err" style={{ padding: 24 }}>{error}</p></Shell>;
  if (!d) return <Shell><p className="muted" style={{ padding: 24 }}>Loading…</p></Shell>;

  const { item, rollups, rollup, parents, node, decided, evidence, history, labor, costTypes } = d;
  const isAssembly = item.item_type === "assembly";
  const isLeaf = !node.has_children;
  const set = (k, v) => { setSaved(false); setForm((f) => ({ ...f, [k]: v })); };
  const totalQty = parents.reduce((s, p) => s + (p.quantity || 0), 0) || 1;

  const saveDetails = async () => {
    const patch = { change_reason: reason || undefined };
    const fields = ["item_name", "weight_grams", "supplier", "supplier_country", "make_or_buy",
      "lead_time_weeks", "drawing_url", "comment"];
    let dirty = false;
    for (const k of fields) {
      let v = form[k];
      if (["weight_grams", "lead_time_weeks"].includes(k))
        v = v === "" || v == null ? null : Number(v);
      if (v !== item[k]) { patch[k] = v; dirty = true; }
    }
    const curMats = item.materials || (item.material ? [item.material] : []);
    if (JSON.stringify(form.materials || []) !== JSON.stringify(curMats)) { patch.materials = form.materials || []; dirty = true; }
    if (!dirty) return;
    setBusy(true);
    try { await api.patchItem(itemId, patch); setReason(""); setSaved(true); load(); onChanged?.(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const archive = async () => {
    if (!window.confirm(`Archive ${itemId}? It's removed from the BOM but recoverable from the Archive.`)) return;
    setBusy(true);
    try { await api.archiveItem(itemId); onChanged?.(); onClose(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const removeChild = async (childId) => {
    if (!window.confirm(`Remove ${childId} from ${itemId}? (archived, recoverable)`)) return;
    try {
      const r = await api.archiveLink(itemId, childId);
      onChanged?.();
      if (r?.parent && r.parent !== itemId) onOpenPart(r.parent); else load();  // parent may have demoted A→P
    } catch (e) { setError(e.message); }
  };
  const restore = async () => {
    setBusy(true);
    try { await api.restoreItem(itemId); load(); onChanged?.(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const exportBom = async (fmt) => {
    setBusy(true);
    try { await api.exportBom(itemId, fmt); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const convertToAssembly = async () => {
    const newId = /P$/i.test(itemId) ? itemId.slice(0, -1) + "A" : itemId;
    const msg = newId === itemId
      ? `Convert ${itemId} to an assembly?`
      : `Convert ${itemId} → ${newId} and make it an assembly? All references are updated automatically.`;
    if (!window.confirm(msg)) return;
    setBusy(true);
    try { const res = await api.promoteItem(itemId); onChanged?.(); onOpenPart(res.new_id); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const tabs = [
    { id: "details", label: "Details" },
    { id: "cost", label: "Cost", count: evidence.length || undefined },
    { id: "files", label: "Files" },
    { id: "history", label: "History", count: history.length || undefined },
  ];

  return (
    <Shell>
      <div className="drawer-head">
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
            <ModulePill code={item.module_code} />
            <Pill kind="warm">{item.item_type}</Pill>
            {item.is_top_level && <Pill kind="accent">top-level</Pill>}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 16, color: "var(--ink-3)" }}>{itemId}</span>
            <h2 style={{ margin: 0 }}>{item.item_name}</h2>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn ghost sm danger" title="Archive (soft-delete)" onClick={archive} disabled={busy}><Icon name="alert" size={13} /></button>
          <button className="btn ghost sm" onClick={onClose}><Icon name="close" /></button>
        </div>
      </div>

      <div className="drawer-body">
        {item.archived && (
          <div className="card" style={{ marginBottom: 12, borderColor: "var(--accent)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 13, color: "var(--accent)" }}><Icon name="alert" size={13} /> Archived — not in the active BOM. Its saved data is shown below.</span>
            <button className="btn sm" onClick={restore} disabled={busy}>Restore</button>
          </div>
        )}
        {!isAssembly && !isLeaf && !item.archived && (
          <div className="card" style={{ marginBottom: 12, borderColor: "var(--accent)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 13, color: "var(--accent)" }}><Icon name="alert" size={13} /> This part has children — by the naming rule it should be an assembly.</span>
            <button className="btn sm" onClick={convertToAssembly} disabled={busy}>Convert to assembly</button>
          </div>
        )}
        <div className="tabs">
          {tabs.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? "on" : ""}`} onClick={() => setTab(t.id)}>
              {t.label}{t.count ? <span style={{ marginLeft: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>{t.count}</span> : null}
            </button>
          ))}
        </div>

        {tab === "details" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {isAssembly && (
              <div className="card" style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 16px" }}>
                <span className="card-title" style={{ flex: 1 }}>Export this {item.is_top_level ? "BOM" : "assembly"}</span>
                <button className="btn ghost sm" onClick={() => exportBom("opml")} disabled={busy}><Icon name="box" size={12} /> OPML</button>
                <button className="btn ghost sm" onClick={() => exportBom("csv")} disabled={busy}><Icon name="box" size={12} /> CSV</button>
              </div>
            )}
            <Readouts isAssembly={isAssembly} rollups={rollups} tier={tier} setTier={setTier} item={item} parents={parents} />

            <Accordion title="Add / edit" meta="fill in item data">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, paddingTop: 14 }}>
                <div style={{ gridColumn: "1 / -1" }}><Field label="Name"><input className="input" value={form.item_name ?? ""} onChange={(e) => set("item_name", e.target.value)} /></Field></div>
                {!isAssembly && (
                  <>
                    <Field label="Weight (g)"><input className="input mono" type="number" value={form.weight_grams ?? ""} onChange={(e) => set("weight_grams", e.target.value)} /></Field>
                    <Field label="Make / buy">
                      <select className="select" value={form.make_or_buy ?? ""} onChange={(e) => set("make_or_buy", e.target.value)}>
                        <option value="">—</option><option>make</option><option>buy</option><option>modified-buy</option>
                      </select>
                    </Field>
                    <div style={{ gridColumn: "1 / -1" }}><Field label="Materials (one or more)"><MultiRef category="material" values={form.materials} onChange={(v) => set("materials", v)} /></Field></div>
                    <Field label="Supplier"><RefSelect category="supplier" value={form.supplier} onChange={(v) => set("supplier", v)} placeholder="— supplier —" /></Field>
                    <Field label="Supplier country"><RefSelect category="country" value={form.supplier_country} onChange={(v) => set("supplier_country", v)} placeholder="— country —" /></Field>
                    <Field label="Lead time (wk)"><input className="input mono" type="number" value={form.lead_time_weeks ?? ""} onChange={(e) => set("lead_time_weeks", e.target.value)} /></Field>
                  </>
                )}
                {isAssembly && (
                  <div style={{ gridColumn: "1 / -1", fontSize: 12, color: "var(--ink-3)" }}>
                    Assembly time &amp; cost are in the <strong>Cost</strong> tab.
                  </div>
                )}
                <div style={{ gridColumn: "1 / -1" }}><Field label="Drawing / CAD URL"><input className="input mono" value={form.drawing_url ?? ""} placeholder="https://…" onChange={(e) => set("drawing_url", e.target.value)} /></Field></div>
                <div style={{ gridColumn: "1 / -1" }}><Field label="Notes"><textarea className="input" style={{ height: 56, padding: 8 }} value={form.comment ?? ""} onChange={(e) => set("comment", e.target.value)} /></Field></div>
                <div style={{ gridColumn: "1 / -1" }}><Field label="Change comment (audit)"><input className="input" value={reason} placeholder="why this change…" onChange={(e) => setReason(e.target.value)} /></Field></div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12, marginTop: 14 }}>
                {saved && <span style={{ fontSize: 12.5, color: "var(--ok)" }}>✓ Saved — shown under Key figures above.</span>}
                <button className="btn" onClick={saveDetails} disabled={busy}><Icon name="check" /> {busy ? "Saving…" : "Save changes"}</button>
              </div>
            </Accordion>

            {isAssembly && node.children.length > 0 && (
              <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                <div className="card-head" style={{ padding: "14px 18px 10px" }}><span className="card-title">Children · {node.children.length}</span></div>
                {node.children.map((c) => (
                  <div key={c.item_id} style={{ display: "grid", gridTemplateColumns: "90px 1fr 60px 80px 28px", gap: 10, padding: "8px 18px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13 }}>
                    <span className="mono" style={{ fontSize: 12, cursor: "pointer" }} onClick={() => onOpenPart(c.item_id)}>{c.item_id}</span>
                    <span style={{ cursor: "pointer" }} onClick={() => onOpenPart(c.item_id)}>{c.item_name}</span>
                    <span style={{ fontFamily: "var(--font-mono)", textAlign: "right", color: "var(--ink-3)" }}>× {c.quantity}</span>
                    <span style={{ textAlign: "right" }}><ModulePill code={c.module_code} /></span>
                    <button className="btn ghost sm danger" title="Remove from this assembly" onClick={() => removeChild(c.item_id)}><Icon name="close" size={11} /></button>
                  </div>
                ))}
              </div>
            )}
            {!item.archived && (addingChild ? (
              <AddChildPanel
                parentId={itemId}
                onAdded={(r) => { setAddingChild(false); onChanged?.(); if (r.parent_id !== itemId) onOpenPart(r.parent_id); else load(); }}
                onCancel={() => setAddingChild(false)}
                setError={setError}
              />
            ) : (
              <button className="btn ghost sm" onClick={() => setAddingChild(true)} style={{ alignSelf: "flex-start" }}>+ Add child from catalog</button>
            ))}
            <WhereUsed parents={parents} onOpenPart={onOpenPart} />
          </div>
        )}

        {tab === "cost" && (
          <CostTab itemId={itemId} isLeaf={isLeaf} item={item} rollups={rollups}
            decided={decided} evidence={evidence} labor={labor} costTypes={costTypes}
            totalQty={totalQty} reload={() => { load(); onChanged?.(); }} setError={setError} />
        )}

        {tab === "files" && <FilesTab itemId={itemId} />}

        {tab === "history" && (
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            <div className="card-head" style={{ padding: "14px 18px 10px" }}><span className="card-title">Change history · {history.length}</span></div>
            {history.length === 0 && <div style={{ padding: 18, color: "var(--ink-3)" }}>No changes yet.</div>}
            {history.map((h) => (
              <div key={h.id} style={{ display: "grid", gridTemplateColumns: "130px 1fr", gap: 10, padding: "8px 18px", borderTop: "1px solid var(--hair-faint)", fontSize: 12.5 }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>{(h.changed_at || "").slice(0, 16).replace("T", " ")}</span>
                <span><strong>{h.field_changed || h.change_type}</strong>{h.old_value != null && <span style={{ color: "var(--ink-3)" }}> {h.old_value} →</span>} <span>{h.new_value ?? ""}</span><span style={{ color: "var(--ink-3)" }}> · {h.changed_by}</span></span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Shell>
  );
}

function Shell({ children }) {
  return <aside className="drawer">{children}</aside>;
}

function TierToggle({ tier, setTier }) {
  return (
    <span style={{ display: "inline-flex", border: "1px solid var(--hair)", borderRadius: 7, overflow: "hidden" }}>
      {COST_TIERS.map((t) => (
        <button key={t} onClick={() => setTier(t)} title={`${t.toLocaleString()} pieces`}
          style={{
            border: 0, cursor: "pointer", padding: "3px 10px", fontFamily: "var(--font-mono)", fontSize: 11,
            background: tier === t ? "var(--accent)" : "transparent",
            color: tier === t ? "#fff" : "var(--ink-3)",
          }}>
          {tierLabel(t)}
        </button>
      ))}
    </span>
  );
}

// Make a user-entered URL absolute, so a value like "drive.google.com/x" or "www.foo.com"
// opens externally instead of being treated as a localhost-relative path.
function extUrl(u) {
  const s = String(u || "").trim();
  if (!s) return s;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s) || s.startsWith("//")) return s; // already has a scheme
  return `https://${s}`;
}

function Readouts({ isAssembly, rollups, tier, setTier, item, parents }) {
  const rollup = rollups[tier] || {};
  const hasRange = rollup.cost_min != null && (rollup.cost_min < rollup.cost || rollup.cost_max > rollup.cost);
  const rng = hasRange ? `${fmtEURcompact(rollup.cost_min)}–${fmtEURcompact(rollup.cost_max)}` : null;
  const mats = item.materials && item.materials.length ? item.materials.join(", ") : (item.material || "—");
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Key figures</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
          <span className="card-meta">cost @</span>
          <TierToggle tier={tier} setTier={setTier} />
          <span className="card-meta">pcs</span>
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {isAssembly ? (
          <>
            <Readout label={`Cost @ ${tierLabel(tier)} pcs`} value={rollup.cost > 0 ? fmtEURcompact(rollup.cost) : "—"} sub={rng ? `${rng} · ${fmtPct(rollup.coverage)} costed` : `${fmtPct(rollup.coverage)} costed · ${rollup.total} leaves`} />
            <Readout label="Rolled-up weight" value={fmtWeight(rollup.weight_grams)} sub="sum over tree" />
            <Readout label="Assembly time" value={rollup.assembly_time_min ? rollup.assembly_time_min + " min" : "—"} sub="recursive" />
            <Readout label="Used in" value={parents.length} sub={parents.length ? "assemblies" : "top-level"} />
          </>
        ) : (
          <>
            <Readout label="Weight" value={fmtWeight(item.weight_grams)} />
            <Readout label="Material" value={mats} />
            <Readout label={`Cost @ ${tierLabel(tier)} pcs`} value={rollup.cost > 0 ? fmtEURcompact(rollup.cost) : "—"} sub={rng ? `range ${rng}` : "decided unit cost"} />
            <Readout label="Used in" value={parents.length} sub={parents.length ? "assemblies" : "top-level"} />
          </>
        )}
      </div>

      {(item.drawing_url || item.drive_folder_url || item.supplier || item.comment) && (
        <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--hair-faint)", display: "grid", gap: 10 }}>
          {item.drawing_url && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 13 }}>
              <span className="label" style={{ minWidth: 96 }}>Drawing / CAD</span>
              <a href={extUrl(item.drawing_url)} target="_blank" rel="noreferrer" className="mono"
                style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--accent)", fontSize: 12 }}>
                {item.drawing_url}
              </a>
              <a href={extUrl(item.drawing_url)} target="_blank" rel="noreferrer" className="btn ghost sm">Open ↗</a>
            </div>
          )}
          {item.drive_folder_url && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 13 }}>
              <span className="label" style={{ minWidth: 96 }}>Drive folder</span>
              <span style={{ flex: 1, color: "var(--ink-3)", fontSize: 12 }}>attachments</span>
              <a href={extUrl(item.drive_folder_url)} target="_blank" rel="noreferrer" className="btn ghost sm">Open ↗</a>
            </div>
          )}
          {item.supplier && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 13 }}>
              <span className="label" style={{ minWidth: 96 }}>Supplier</span>
              <span style={{ flex: 1, color: "var(--ink-2)" }}>{item.supplier}{item.supplier_country ? ` · ${item.supplier_country}` : ""}</span>
            </div>
          )}
          {item.comment && (
            <div style={{ display: "flex", gap: 12, fontSize: 13 }}>
              <span className="label" style={{ minWidth: 96, paddingTop: 2 }}>Notes</span>
              <span style={{ flex: 1, color: "var(--ink-2)", whiteSpace: "pre-wrap", lineHeight: 1.45 }}>{item.comment}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Readout({ label, value, sub }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="label">{label}</span>
      <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 20, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>{value}</span>
      {sub && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>{sub}</span>}
    </div>
  );
}

function AddChildPanel({ parentId, onAdded, onCancel, setError }) {
  const [cat, setCat] = useState(null);
  const [q, setQ] = useState("");
  const [qty, setQty] = useState(1);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.catalog().then(setCat).catch((e) => setError(e.message)); }, []);
  const s = q.trim().toLowerCase();
  const results = !s ? [] : (cat || [])
    .filter((r) => r.item_id !== parentId && (r.item_id.toLowerCase().includes(s) || (r.item_name || "").toLowerCase().includes(s)))
    .slice(0, 40);
  const add = async (childId) => {
    setBusy(true);
    try { const r = await api.addChild(parentId, { child_id: childId, quantity: Number(qty) || 1 }); onAdded(r); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ padding: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <span className="card-title" style={{ flex: 1 }}>Add child from catalog</span>
        <button className="btn ghost sm" onClick={onCancel}>cancel</button>
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        <input className="input" placeholder="Search catalog by code or name…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus style={{ flex: 1 }} />
        <input className="input mono" type="number" value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: 64 }} title="quantity" />
      </div>
      <div style={{ maxHeight: 240, overflowY: "auto" }}>
        {!cat && <div style={{ padding: 10, color: "var(--ink-3)", fontSize: 12 }}>Loading catalog…</div>}
        {cat && !s && <div style={{ padding: 10, color: "var(--ink-3)", fontSize: 12 }}>Type to search the {cat.length} catalog items…</div>}
        {results.map((r) => (
          <div key={r.item_id} style={{ display: "grid", gridTemplateColumns: "100px 1fr 50px", gap: 8, padding: "6px 4px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13 }}>
            <span className="mono" style={{ fontSize: 12 }}>{r.item_id}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.item_name}</span>
            <button className="btn sm" onClick={() => add(r.item_id)} disabled={busy}>add</button>
          </div>
        ))}
        {cat && s && results.length === 0 && <div style={{ padding: 10, color: "var(--ink-3)", fontSize: 12 }}>No matches.</div>}
      </div>
      <p style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}>
        Adding makes this an assembly; the part's code may change to match its usage (system code, or UN if used across systems).
      </p>
    </div>
  );
}

function WhereUsed({ parents, onOpenPart }) {
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div className="card-head" style={{ padding: "14px 18px 10px" }}><span className="card-title">Where used · {parents.length}</span></div>
      {parents.length === 0 ? (
        <div style={{ padding: 18, color: "var(--ink-3)", fontSize: 13 }}>Top-level — not used inside another assembly.</div>
      ) : parents.map((p) => (
        <div key={p.parent} onClick={() => onOpenPart(p.parent)} style={{ display: "grid", gridTemplateColumns: "90px 1fr 60px", gap: 10, padding: "8px 18px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", cursor: "pointer", fontSize: 13 }}>
          <span className="mono" style={{ fontSize: 12 }}>{p.parent}</span><span>{p.name}</span>
          <span style={{ fontFamily: "var(--font-mono)", textAlign: "right", color: "var(--ink-3)" }}>× {p.quantity}</span>
        </div>
      ))}
    </div>
  );
}

function FilesTab({ itemId }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);
  const load = () => { setError(null); api.attachments(itemId).then(setData).catch((e) => setError(e.message)); };
  useEffect(() => { setData(null); load(); /* eslint-disable-next-line */ }, [itemId]);

  const upload = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) return;
    setBusy(true);
    try { await api.uploadAttachment(itemId, f); if (fileRef.current) fileRef.current.value = ""; load(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  if (error) return <p className="err">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  if (!data.configured) {
    return (
      <div className="card">
        <div className="card-head"><span className="card-title">Attachments</span></div>
        <p className="muted" style={{ fontSize: 13 }}>
          Google Drive isn't connected yet. Once the service account + <em>ZEF BOM Attachments</em> folder are configured,
          this part gets its own Drive folder here for invoices, quotes, datasheets and drawings.
        </p>
      </div>
    );
  }
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div className="card-head" style={{ padding: "14px 18px 10px" }}>
        <span className="card-title">Attachments · {data.files.length}</span>
        {data.folder_url && <a className="card-meta" href={data.folder_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>Open folder ↗</a>}
      </div>
      {data.files.map((f) => (
        <a key={f.id} href={f.url} target="_blank" rel="noreferrer" style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 10, padding: "9px 18px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13, textDecoration: "none", color: "var(--ink)" }}>
          <span><Icon name="box" size={12} /> {f.name}</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>{(f.modified || "").slice(0, 10)}</span>
        </a>
      ))}
      {data.files.length === 0 && <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 13 }}>No files yet.</div>}
      <div style={{ display: "flex", gap: 8, padding: 14, borderTop: "1px solid var(--hair)", alignItems: "center" }}>
        <input ref={fileRef} type="file" className="input" style={{ paddingTop: 6, flex: 1 }} />
        <button className="btn sm" onClick={upload} disabled={busy}><Icon name="check" /> {busy ? "Uploading…" : "Upload"}</button>
      </div>
    </div>
  );
}

function CostTab({ itemId, isLeaf, item, rollups, decided, evidence, labor, costTypes, totalQty, reload, setError }) {
  const byTier = Object.fromEntries(decided.map((x) => [x.volume_tier, x]));
  const laborByTier = Object.fromEntries((labor || []).map((l) => [l.volume_tier, l]));
  const [draft, setDraft] = useState({});
  const [tdraft, setTdraft] = useState({});
  const [adding, setAdding] = useState(false);
  const [ctName, setCtName] = useState("");
  const [ctRate, setCtRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [ev, setEv] = useState({ source_type: "quote", unit_cost: "", volume_tier: 100, supplier_name: "", confidence: "high", note: "" });

  const dval = (vt, key, fallback) => (draft[vt]?.[key] ?? (byTier[vt]?.[key] ?? fallback));
  const setD = (vt, key, v) => setDraft((d) => ({ ...d, [vt]: { ...d[vt], [key]: v } }));
  const saveTier = async (vt) => {
    const cost = dval(vt, "unit_cost_eur", byTier[vt]?.unit_cost_eur);
    if (cost === "" || cost == null) return;
    const num = (k) => { const v = dval(vt, k, ""); return v === "" || v == null ? null : Number(v); };
    setBusy(true);
    try {
      await api.setDecidedCost(itemId, {
        volume_tier: vt, unit_cost_eur: Number(cost),
        cost_min: num("cost_min"), cost_max: num("cost_max"),
        make_or_buy: dval(vt, "make_or_buy", "") || null,
        basis_note: dval(vt, "basis_note", "") || null,
        confidence: dval(vt, "confidence", "medium"),
      });
      reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const setCostType = async (id) => {
    setBusy(true);
    try { await api.patchItem(itemId, { cost_type_id: id ? Number(id) : null, change_reason: "cost type" }); reload(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const addCostTypeInline = async () => {
    if (!ctName.trim() || ctRate === "") return;
    setBusy(true);
    try {
      const r = await api.addCostType(ctName.trim(), Number(ctRate));
      await api.patchItem(itemId, { cost_type_id: r.id });
      setAdding(false); setCtName(""); setCtRate(""); reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const tval = (vt, key) => (tdraft[vt]?.[key] ?? (laborByTier[vt]?.[key] ?? ""));
  const setT = (vt, key, v) => setTdraft((d) => ({ ...d, [vt]: { ...d[vt], [key]: v } }));
  const saveLabor = async (vt) => {
    const likely = tval(vt, "time_likely");
    if (likely === "" || likely == null) return;
    const num = (k) => { const v = tval(vt, k); return v === "" || v == null ? null : Number(v); };
    setBusy(true);
    try {
      await api.setAssemblyLabor(itemId, { volume_tier: vt, time_likely: Number(likely), time_min: num("time_min"), time_max: num("time_max") });
      reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const addEvidence = async () => {
    if (!ev.unit_cost) return;
    setBusy(true);
    try {
      await api.addCostEvidence(itemId, { ...ev, unit_cost: Number(ev.unit_cost), volume_tier: Number(ev.volume_tier) });
      setEv({ ...ev, unit_cost: "", supplier_name: "", note: "" }); reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {!isLeaf ? (
        <>
          <div className="card">
            <div className="card-head"><span className="card-title">Assembly cost</span><span className="card-meta">parts + assembly</span></div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
              {COST_TIERS.map((t) => {
                const r = rollups[t] || {};
                return (
                  <div key={t} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <span className="label">@ {tierLabel(t)} pcs</span>
                    <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 18, fontVariantNumeric: "tabular-nums" }}>{r.cost > 0 ? fmtEURcompact(r.cost) : "—"}</span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>parts {fmtEURcompact(r.parts_cost || 0)} + assembly {fmtEURcompact(r.assembly_cost || 0)}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="card">
            <div className="card-head"><span className="card-title">Assembly cost type</span></div>
            <div style={{ display: "flex", gap: 8 }}>
              <select className="select" value={item.cost_type_id ?? ""} onChange={(e) => setCostType(e.target.value)} disabled={busy} style={{ flex: 1 }}>
                <option value="">— pick a cost type —</option>
                {(costTypes || []).map((c) => <option key={c.id} value={c.id}>{c.value} (€{c.meta?.rate_eur_h}/h)</option>)}
              </select>
              <button className="btn ghost sm" onClick={() => setAdding((a) => !a)} disabled={busy}>+ add</button>
            </div>
            {adding && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 100px 48px", gap: 8, marginTop: 8, alignItems: "end" }}>
                <Field label="name (e.g. machine-A)"><input className="input" value={ctName} onChange={(e) => setCtName(e.target.value)} /></Field>
                <Field label="€/hour"><input className="input mono" type="number" value={ctRate} onChange={(e) => setCtRate(e.target.value)} /></Field>
                <button className="btn sm" style={{ marginBottom: 1 }} onClick={addCostTypeInline} disabled={busy}>add</button>
              </div>
            )}
            <p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 8 }}>
              Assembly cost = time × this rate (labour, machine, …). Also editable in <strong>Admin → Reference data → Assembly cost types</strong>.
            </p>
          </div>

          <div className="card">
            <div className="card-head"><span className="card-title">Assembly time — minutes</span><span className="card-meta">min · likely · max</span></div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {COST_TIERS.map((t) => (
                <div key={t} style={{ display: "grid", gridTemplateColumns: "60px 1fr 44px", gap: 8, alignItems: "end" }}>
                  <Field label={`@ ${tierLabel(t)} pcs`}><span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>{t.toLocaleString()}</span></Field>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 5 }}>
                    <Field label="min"><input className="input mono" type="number" value={tval(t, "time_min")} onChange={(e) => setT(t, "time_min", e.target.value)} /></Field>
                    <Field label="likely*"><input className="input mono" type="number" value={tval(t, "time_likely")} onChange={(e) => setT(t, "time_likely", e.target.value)} /></Field>
                    <Field label="max"><input className="input mono" type="number" value={tval(t, "time_max")} onChange={(e) => setT(t, "time_max", e.target.value)} /></Field>
                  </div>
                  <button className="btn sm" style={{ marginBottom: 1 }} onClick={() => saveLabor(t)} disabled={busy}>set</button>
                </div>
              ))}
            </div>
          </div>
        </>
      ) : (
        <div className="card">
          <div className="card-head"><span className="card-title">Decided unit cost — € per piece</span><span className="card-meta">min · most-likely · max</span></div>
          <p style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 12 }}>
            Price of <strong>one piece</strong> (qty is multiplied automatically). <strong>Most-likely</strong> is
            required; <strong>min/max</strong> are optional and give a cost range. The tier is the production-volume scenario.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {COST_TIERS.map((t) => (
              <div key={t} style={{ display: "grid", gridTemplateColumns: "60px 1fr 70px 44px", gap: 8, alignItems: "end" }}>
                <Field label={`@ ${tierLabel(t)} pcs`}><span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>~{(totalQty * t).toLocaleString()}</span></Field>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 5 }}>
                  <Field label="min €"><input className="input mono" type="number" value={dval(t, "cost_min", "")} onChange={(e) => setD(t, "cost_min", e.target.value)} /></Field>
                  <Field label="likely €*"><input className="input mono" type="number" value={dval(t, "unit_cost_eur", "")} onChange={(e) => setD(t, "unit_cost_eur", e.target.value)} /></Field>
                  <Field label="max €"><input className="input mono" type="number" value={dval(t, "cost_max", "")} onChange={(e) => setD(t, "cost_max", e.target.value)} /></Field>
                </div>
                <Field label="make/buy">
                  <select className="select" value={dval(t, "make_or_buy", "")} onChange={(e) => setD(t, "make_or_buy", e.target.value)}>
                    <option value="">—</option><option>make</option><option>buy</option>
                  </select>
                </Field>
                <button className="btn sm" style={{ marginBottom: 1 }} onClick={() => saveTier(t)} disabled={busy}>set</button>
              </div>
            ))}
          </div>
        </div>
      )}

      <Accordion title="Cost evidence" meta={`${evidence.length} on file`} defaultOpen={evidence.length > 0}>
        <div style={{ paddingTop: 12 }}>
          {evidence.map((q) => (
            <div key={q.id} style={{ borderTop: "1px solid var(--hair-faint)", padding: "8px 0", fontSize: 12.5 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <Pill kind={q.source_type === "invoice" ? "ok" : q.source_type?.startsWith("estimate") ? "warm" : "info"}>{(q.source_type || "").replace("estimate_", "est·")}</Pill>
                <span style={{ flex: 1 }}>{q.supplier_name || <em style={{ color: "var(--ink-3)" }}>—</em>}</span>
                <span className="mono">{q.currency} {q.unit_cost} @{q.volume_tier}</span>
                <button className="btn ghost sm danger" onClick={async () => { try { await api.deleteCostEvidence(itemId, q.id); reload(); } catch (e) { setError(e.message); } }}><Icon name="close" size={11} /></button>
              </div>
              {q.note && <div style={{ color: "var(--ink-3)", fontSize: 11.5, marginTop: 3 }}>{q.note}</div>}
            </div>
          ))}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 80px 80px", gap: 8, marginTop: 12, alignItems: "end" }}>
            <Field label="Source"><select className="select" value={ev.source_type} onChange={(e) => setEv({ ...ev, source_type: e.target.value })}>{SOURCES.map((s) => <option key={s}>{s}</option>)}</select></Field>
            <Field label="Supplier"><RefSelect category="supplier" value={ev.supplier_name} onChange={(v) => setEv({ ...ev, supplier_name: v })} placeholder="—" /></Field>
            <Field label="€/unit"><input className="input mono" type="number" value={ev.unit_cost} onChange={(e) => setEv({ ...ev, unit_cost: e.target.value })} /></Field>
            <Field label="Volume"><input className="input mono" type="number" value={ev.volume_tier} onChange={(e) => setEv({ ...ev, volume_tier: e.target.value })} /></Field>
          </div>
          <div style={{ marginTop: 8 }}><Field label="Note (reasoning / math)"><input className="input" value={ev.note} placeholder="e.g. derived from 1.2 kg × €4.5/kg + machining" onChange={(e) => setEv({ ...ev, note: e.target.value })} /></Field></div>
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}><button className="btn sm" onClick={addEvidence} disabled={busy}><Icon name="check" /> add evidence</button></div>
        </div>
      </Accordion>
    </div>
  );
}
