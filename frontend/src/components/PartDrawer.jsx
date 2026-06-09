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
  const [d, setD] = useState(null);
  const [form, setForm] = useState({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
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
      safe(api.rollup(itemId), emptyRollup),
      safe(api.whereUsed(itemId), []),
      safe(api.tree(itemId), emptyNode),
      safe(api.decidedCost(itemId), []),
      safe(api.costEvidence(itemId), []),
      safe(api.itemHistory(itemId), []),
    ])
      .then(([item, rollup, parents, node, decided, evidence, history]) => {
        setD({ item, rollup, parents, node, decided, evidence, history });
        setForm({ ...item, materials: item.materials || (item.material ? [item.material] : []) });
      })
      .catch((e) => setError(e.message));
  };
  useEffect(() => { setD(null); setTab("details"); load(); /* eslint-disable-next-line */ }, [itemId]);

  if (error) return <Shell><p className="err" style={{ padding: 24 }}>{error}</p></Shell>;
  if (!d) return <Shell><p className="muted" style={{ padding: 24 }}>Loading…</p></Shell>;

  const { item, rollup, parents, node, decided, evidence, history } = d;
  const isAssembly = item.item_type === "assembly";
  const isLeaf = !node.has_children;
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const totalQty = parents.reduce((s, p) => s + (p.quantity || 0), 0) || 1;

  const saveDetails = async () => {
    const patch = { change_reason: reason || undefined };
    const fields = ["item_name", "weight_grams", "supplier", "supplier_country", "make_or_buy",
      "lead_time_weeks", "drawing_url", "comment", "assembly_time_min_1pc", "assembly_time_min_10k"];
    let dirty = false;
    for (const k of fields) {
      let v = form[k];
      if (["weight_grams", "lead_time_weeks", "assembly_time_min_1pc", "assembly_time_min_10k"].includes(k))
        v = v === "" || v == null ? null : Number(v);
      if (v !== item[k]) { patch[k] = v; dirty = true; }
    }
    const curMats = item.materials || (item.material ? [item.material] : []);
    if (JSON.stringify(form.materials || []) !== JSON.stringify(curMats)) { patch.materials = form.materials || []; dirty = true; }
    if (!dirty) return;
    setBusy(true);
    try { await api.patchItem(itemId, patch); setReason(""); load(); onChanged?.(); }
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
    try { await api.archiveLink(itemId, childId); load(); onChanged?.(); } catch (e) { setError(e.message); }
  };
  const restore = async () => {
    setBusy(true);
    try { await api.restoreItem(itemId); load(); onChanged?.(); }
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
        <div className="tabs">
          {tabs.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? "on" : ""}`} onClick={() => setTab(t.id)}>
              {t.label}{t.count ? <span style={{ marginLeft: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>{t.count}</span> : null}
            </button>
          ))}
        </div>

        {tab === "details" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <Readouts isAssembly={isAssembly} rollup={rollup} item={item} parents={parents} />

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
                  <>
                    <Field label="Assembly time @1pc (min)"><input className="input mono" type="number" value={form.assembly_time_min_1pc ?? ""} onChange={(e) => set("assembly_time_min_1pc", e.target.value)} /></Field>
                    <Field label="Assembly time @10k (min)"><input className="input mono" type="number" value={form.assembly_time_min_10k ?? ""} onChange={(e) => set("assembly_time_min_10k", e.target.value)} /></Field>
                  </>
                )}
                <div style={{ gridColumn: "1 / -1" }}><Field label="Drawing / CAD URL"><input className="input mono" value={form.drawing_url ?? ""} placeholder="https://…" onChange={(e) => set("drawing_url", e.target.value)} /></Field></div>
                <div style={{ gridColumn: "1 / -1" }}><Field label="Notes"><textarea className="input" style={{ height: 56, padding: 8 }} value={form.comment ?? ""} onChange={(e) => set("comment", e.target.value)} /></Field></div>
                <div style={{ gridColumn: "1 / -1" }}><Field label="Change comment (audit)"><input className="input" value={reason} placeholder="why this change…" onChange={(e) => setReason(e.target.value)} /></Field></div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
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
            <WhereUsed parents={parents} onOpenPart={onOpenPart} />
          </div>
        )}

        {tab === "cost" && (
          <CostTab itemId={itemId} isLeaf={isLeaf} rollup={rollup} decided={decided} evidence={evidence}
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

function Readouts({ isAssembly, rollup, item, parents }) {
  const mats = item.materials && item.materials.length ? item.materials.join(", ") : (item.material || "—");
  return (
    <div className="card">
      <div className="card-head"><span className="card-title">Key figures</span><span className="card-meta">@ 100/yr</span></div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {isAssembly ? (
          <>
            <Readout label="Rollup cost" value={rollup.cost > 0 ? fmtEURcompact(rollup.cost) : "—"} sub={`${fmtPct(rollup.coverage)} costed · ${rollup.total} leaves`} />
            <Readout label="Rolled-up weight" value={fmtWeight(rollup.weight_grams)} sub="sum over tree" />
            <Readout label="Assembly time" value={rollup.assembly_time_min ? rollup.assembly_time_min + " min" : "—"} sub="recursive" />
            <Readout label="Used in" value={parents.length} sub={parents.length ? "assemblies" : "top-level"} />
          </>
        ) : (
          <>
            <Readout label="Weight" value={fmtWeight(item.weight_grams)} />
            <Readout label="Material" value={mats} />
            <Readout label="Rollup cost" value={rollup.cost > 0 ? fmtEURcompact(rollup.cost) : "—"} sub="decided @ 100" />
            <Readout label="Used in" value={parents.length} sub={parents.length ? "assemblies" : "top-level"} />
          </>
        )}
      </div>
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

function CostTab({ itemId, isLeaf, rollup, decided, evidence, totalQty, reload, setError }) {
  const byTier = Object.fromEntries(decided.map((x) => [x.volume_tier, x]));
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const [ev, setEv] = useState({ source_type: "quote", unit_cost: "", volume_tier: 100, supplier_name: "", confidence: "high", note: "" });

  if (!isLeaf) {
    return (
      <div className="card">
        <div className="card-head"><span className="card-title">Derived cost</span><span className="card-meta">from children</span></div>
        <p className="muted" style={{ fontSize: 13 }}>Assembly cost is rolled up from children, not entered directly. Current: <strong>{rollup.cost > 0 ? fmtEURcompact(rollup.cost) : "—"}</strong> at {fmtPct(rollup.coverage)} coverage.</p>
      </div>
    );
  }

  const dval = (tier, key, fallback) => (draft[tier]?.[key] ?? (byTier[tier]?.[key] ?? fallback));
  const setD = (tier, key, v) => setDraft((d) => ({ ...d, [tier]: { ...d[tier], [key]: v } }));
  const saveTier = async (tier) => {
    const cost = dval(tier, "unit_cost_eur", byTier[tier]?.unit_cost_eur);
    if (cost === "" || cost == null) return;
    setBusy(true);
    try {
      await api.setDecidedCost(itemId, {
        volume_tier: tier, unit_cost_eur: Number(cost),
        make_or_buy: dval(tier, "make_or_buy", "") || null,
        basis_note: dval(tier, "basis_note", "") || null,
        confidence: dval(tier, "confidence", "medium"),
      });
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
      <div className="card">
        <div className="card-head"><span className="card-title">Decided unit cost — € per piece</span></div>
        <p style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 12 }}>
          Price of <strong>one piece</strong>. The BOM quantity is multiplied automatically in rollups.
          The tier is the annual <strong>microplant</strong> production scenario (economies of scale) —
          this part is used <strong>{totalQty}×</strong> total, so ~<strong>{(totalQty * 100).toLocaleString()}</strong> pieces/yr at 100 microplants.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {COST_TIERS.map((tier) => (
            <div key={tier} style={{ display: "grid", gridTemplateColumns: "70px 1fr 90px 1fr 56px", gap: 8, alignItems: "end" }}>
              <Field label={`@ ${tierLabel(tier)}/yr`}><span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>~{(totalQty * tier).toLocaleString()} pcs</span></Field>
              <Field label="€/piece"><input className="input mono" type="number" value={dval(tier, "unit_cost_eur", "")} onChange={(e) => setD(tier, "unit_cost_eur", e.target.value)} /></Field>
              <Field label="make/buy">
                <select className="select" value={dval(tier, "make_or_buy", "")} onChange={(e) => setD(tier, "make_or_buy", e.target.value)}>
                  <option value="">—</option><option>make</option><option>buy</option>
                </select>
              </Field>
              <Field label="basis / why"><input className="input" value={dval(tier, "basis_note", "")} placeholder="reasoning…" onChange={(e) => setD(tier, "basis_note", e.target.value)} /></Field>
              <button className="btn sm" style={{ marginBottom: 1 }} onClick={() => saveTier(tier)} disabled={busy}>set</button>
            </div>
          ))}
        </div>
      </div>

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
