import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Icon, ModulePill, NumInput, Pill, fmtEURcompact, fmtPct, fmtWeight, toNum } from "./ui";
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
  const [tab, setTab] = useState("overview");
  const [tier, setTier] = useState(100);
  const [addingChild, setAddingChild] = useState(false);
  // Quantities are read-only until you explicitly enter edit mode, matching the
  // "Add / edit" + "Save changes" pattern the details section already uses. Nothing is
  // written until Save, so a stray click can't change a BOM.
  const [editQty, setEditQty] = useState(false);
  const [qtyDraft, setQtyDraft] = useState({});   // { child_id: "raw string" }
  const [d, setD] = useState(null);
  const [form, setForm] = useState({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  // Two separate channels. `loadError` means the drawer has no data to show, so it
  // legitimately replaces the body. `actionError` means an action failed while the drawer is
  // perfectly usable — replacing the body there would throw away unsaved input.
  const [loadError, setLoadError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const setError = setActionError;   // every action, and every sub-component, funnels here
  const [modOpts, setModOpts] = useState({ current: null, options: [] });
  const [pendingMod, setPendingMod] = useState(null);  // staged module; applied on Save
  const copyName = useRef(null);   // survives the duplicate-name confirm round trip
  useEffect(() => {
    api.moduleOptions(itemId)
      .then((o) => { setModOpts(o); setPendingMod(o.current); })
      .catch(() => { setModOpts({ current: null, options: [] }); setPendingMod(null); });
  }, [itemId]);

  const load = () => {
    setLoadError(null);
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
      safe(api.itemLinks(itemId), []),
    ])
      .then(([item, r1, r100, r10k, parents, node, decided, evidence, history, labor, costTypes, links]) => {
        const rollups = { 1: r1, 100: r100, 10000: r10k };
        setActionError(null);   // a successful reload means the last action went through
        setD({ item, rollups, rollup: r100, parents, node, decided, evidence, history, labor, costTypes, links });
        setForm({ ...item, materials: item.materials || (item.material ? [item.material] : []) });
      })
      .catch((e) => setLoadError(e.message));
  };
  useEffect(() => { setD(null); setTab("overview"); setAddingChild(false); setEditQty(false); setQtyDraft({}); setActionError(null); load(); /* eslint-disable-next-line */ }, [itemId]);

  if (loadError) return <Shell><p className="err" style={{ padding: 24 }}>{loadError}</p></Shell>;
  if (!d) return <Shell><p className="muted" style={{ padding: 24 }}>Loading…</p></Shell>;

  const { item, rollups, rollup, parents, node, decided, evidence, history, labor, costTypes, links } = d;
  const isAssembly = item.item_type === "assembly";
  const isLeaf = !node.has_children;
  const set = (k, v) => { setSaved(false); setForm((f) => ({ ...f, [k]: v })); };
  const totalQty = parents.reduce((s, p) => s + (p.quantity || 0), 0) || 1;

  const saveDetails = async () => {
    const patch = { change_reason: reason || undefined };
    const fields = ["item_name", "weight_grams", "supplier", "supplier_country",
      "supplier_part_number", "lead_time_weeks", "drawing_url", "comment"];
    let dirty = false;
    for (const k of fields) {
      let v = form[k];
      if (["weight_grams", "lead_time_weeks"].includes(k)) v = toNum(v);
      if (v !== item[k]) { patch[k] = v; dirty = true; }
    }
    const curMats = item.materials || (item.material ? [item.material] : []);
    if (JSON.stringify(form.materials || []) !== JSON.stringify(curMats)) { patch.materials = form.materials || []; dirty = true; }
    const modChanged = pendingMod && modOpts.current && pendingMod !== modOpts.current;
    if (!dirty && !modChanged) return;
    setBusy(true); setError(null);
    try {
      if (dirty) await api.patchItem(itemId, patch);   // field edits on the current code first
      if (modChanged) {
        const r = await api.setItemModule(itemId, pendingMod);  // then re-code (atomic)
        setReason(""); setSaved(true); onChanged?.();
        if (r.item_id && r.item_id !== itemId) { onOpenPart?.(r.item_id); return; }  // reopen under new code
      }
      setReason(""); setSaved(true); load(); onChanged?.();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
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
  // ── contents quantity editing ──────────────────────────────────────────────
  // A row is "changed" only when it parses to a valid, different, positive number.
  const qtyRaw = (c) => qtyDraft[c.item_id] ?? String(c.quantity);
  const qtyBad = (c) => { const n = toNum(qtyRaw(c)); return n === null || n <= 0; };
  const qtyChanged = (c) => !qtyBad(c) && toNum(qtyRaw(c)) !== c.quantity;
  const qtyEdits = () => (node.children || []).filter(qtyChanged);
  const qtyInvalid = () => (node.children || []).filter(qtyBad);

  const cancelQty = () => { setQtyDraft({}); setEditQty(false); setActionError(null); };
  const saveQty = async () => {
    const bad = qtyInvalid();
    if (bad.length) {
      setActionError(`Quantity must be a number greater than 0 — check ${bad.map((c) => c.item_id).join(", ")}.`);
      return;
    }
    const edits = qtyEdits();
    if (!edits.length) { cancelQty(); return; }
    setBusy(true); setActionError(null);
    try {
      for (const c of edits) await api.setChildQuantity(itemId, c.item_id, toNum(qtyRaw(c)));
      setQtyDraft({}); setEditQty(false);
      load(); onChanged?.();
    } catch (e) { setActionError(e.message); } finally { setBusy(false); }
  };

  const setTopLevel = async (want) => {
    const msg = want
      ? `Make ${itemId} a top-level BOM? It becomes a root in Browse, and everything inside it counts as in use.`
      : `${itemId} will stop being a top-level BOM. Its contents stay, but nothing will treat it as a root.`;
    if (!window.confirm(msg)) return;
    setBusy(true);
    try {
      const r = await api.setTopLevel(itemId, want);
      onChanged?.();
      // Codes below may have followed the change of owning system — say so rather than
      // letting ids change silently.
      if (r.renamed?.length) {
        setActionError(`${r.renamed.length} item${r.renamed.length > 1 ? "s were" : " was"} re-coded to match: `
          + r.renamed.slice(0, 6).map((c) => `${c.from} → ${c.to}`).join(", ")
          + (r.renamed.length > 6 ? " …" : ""));
      }
      load();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const copyItem = async (force = false) => {
    // Prefilled with "(copy)" and pre-selected, because a copy exists to become a variant and
    // a variant needs its own name — better to get it right at creation than leave litter.
    const suggested = force ? undefined : window.prompt(`Name for the copy of ${itemId}:`, `${item.item_name} (copy)`);
    const name = force ? copyName.current : (suggested || "").trim();
    if (!name) return;
    copyName.current = name;
    setBusy(true);
    try {
      const r = await api.duplicateItem(itemId, { item_name: name, allow_duplicate: force });
      onChanged?.();
      onOpenPart(r.item_id);
    } catch (e) {
      if (!force && /\b409\b/.test(e.message) && /allow_duplicate/.test(e.message)) {
        const detail = (e.message.match(/"detail"\s*:\s*"([^"]+)"/) || [])[1] || "";
        const codes = (detail.match(/\(([^)]+)\)/) || [])[1];
        if (window.confirm(`A part named “${name}” already exists${codes ? ` (${codes})` : ""}.

Create this copy anyway? It gets its own new code.`)) {
          return copyItem(true);
        }
        return;
      }
      setError(e.message);
    } finally { setBusy(false); }
  };
  const changeCode = async (mode) => {
    let payload = { mode };
    if (mode === "manual") {
      const typed = window.prompt(`New code for ${itemId} (e.g. ${itemId.slice(0, 3)}042${itemId.slice(-1)}):`, itemId);
      if (!typed || typed.trim().toUpperCase() === itemId) return;
      payload.code = typed.trim().toUpperCase();
    }
    setBusy(true);
    try {
      // Ask first: the merge branch discards the occupant's costs and is not reversible,
      // so the confirmation has to state the real numbers rather than a generic warning.
      const plan = await api.setItemCode(itemId, { ...payload, preview: true });
      if (plan.action === "noop") return;
      let ok;
      if (plan.action === "merge") {
        const c = plan.conflict || {};
        ok = window.confirm(
          `${plan.target} already exists — "${c.occupant_name}".

` +
          `Overwrite it with ${itemId}'s data?
` +
          `  · ${plan.target} keeps its code, but takes this item's name, fields and costs
` +
          `  · ${c.discards_costs || 0} decided cost row(s) on ${plan.target} are discarded
` +
          (c.gains_parents?.length ? `  · ${plan.target} is added to ${c.gains_parents.join(", ")}
` : "") +
          `  · ${itemId} stops existing, and its number is never reissued

` +
          `This cannot be undone automatically.`
        );
      } else {
        ok = window.confirm(`Renumber ${itemId} to ${plan.target}?`);
      }
      if (!ok) return;
      const r = await api.setItemCode(itemId, payload);
      onChanged?.();
      if (r.renamed?.length) {
        setActionError(`${r.renamed.length} other item${r.renamed.length > 1 ? "s were" : " was"} re-coded to match: `
          + r.renamed.slice(0, 6).map((c) => `${c.from} → ${c.to}`).join(", "));
      }
      onOpenPart(r.item_id);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
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

  // Two working tabs: Overview reads, Edit writes. History is the audit trail.
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "edit", label: "Edit" },
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
          <button className="btn ghost sm" title="Copy this item into the catalog" onClick={() => copyItem()} disabled={busy}><Icon name="box" size={13} /></button>
          <button className="btn ghost sm danger" title="Archive (soft-delete)" onClick={archive} disabled={busy}><Icon name="alert" size={13} /></button>
          <button className="btn ghost sm" onClick={onClose}><Icon name="close" /></button>
        </div>
      </div>

      {actionError && (
        <div role="alert" style={{ flex: "0 0 auto", margin: "0 24px 4px", padding: "10px 12px", border: "1px solid var(--accent)", borderRadius: 4, background: "var(--accent-soft)", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
          <span style={{ fontSize: 13, color: "var(--accent)" }}>
            <Icon name="alert" size={13} /> {actionError}
          </span>
          <button className="btn ghost sm" title="Dismiss" onClick={() => setActionError(null)}>
            <Icon name="close" size={11} />
          </button>
        </div>
      )}

      <div className="drawer-body">
        {item.archived && (
          <div className="card" style={{ marginBottom: 12, borderColor: "var(--accent)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 13, color: "var(--accent)" }}><Icon name="alert" size={13} /> Archived — not in the active BOM. Its saved data is shown below.</span>
            <button className="btn sm" onClick={restore} disabled={busy}>Restore</button>
          </div>
        )}
        {!isAssembly && !isLeaf && !item.archived && (
          <div className="card" style={{ marginBottom: 12, borderColor: "var(--accent)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 13, color: "var(--accent)" }}><Icon name="alert" size={13} /> This part has components — by the naming rule it should be an assembly.</span>
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

        {tab === "overview" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <Readouts isAssembly={isAssembly} rollups={rollups} tier={tier} setTier={setTier} item={item} parents={parents} links={links} />
            <WhereUsed itemId={itemId} parents={parents} onOpenPart={onOpenPart}
              onMoved={(r) => { onChanged?.(); onOpenPart(r.child_id); }} setError={setError} />
            {node.children.length > 0 && (
              <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                <div className="card-head" style={{ padding: "14px 18px 10px" }}>
                  <span className="card-title" style={{ flex: 1 }}>Contents · {node.children.length}</span>
                  <span className="card-meta">edit them in the Edit tab</span>
                </div>
                {node.children.map((c) => (
                  <div key={c.item_id} title={`Open ${c.item_id}`}
                       style={{ display: "grid", gridTemplateColumns: "90px minmax(0, 1fr) 78px 80px", gap: 10, padding: "8px 18px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13, cursor: "pointer" }}
                       onClick={() => onOpenPart(c.item_id)}>
                    <span className="mono" style={{ fontSize: 12 }}>{c.item_id}</span>
                    <span>{c.item_name}</span>
                    <span style={{ fontFamily: "var(--font-mono)", textAlign: "right", color: "var(--ink-3)" }}>× {c.quantity}</span>
                    <span style={{ textAlign: "right" }}><ModulePill code={c.module_code} /></span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "edit" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <CostOverviewCard isLeaf={isLeaf} rollups={rollups} decided={decided} />

            {!isLeaf && (
              <AssemblyCostCards itemId={itemId} item={item} rollups={rollups} decided={decided}
                labor={labor} costTypes={costTypes}
                reload={() => { load(); onChanged?.(); }} setError={setError} />
            )}

            <Accordion title="Item data" meta="fill in item data" defaultOpen>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 14, paddingTop: 14 }}>
                <div style={{ gridColumn: "1 / -1" }}><Field label="Name"><input className="input" value={form.item_name ?? ""} onChange={(e) => set("item_name", e.target.value)} /></Field></div>
                <div style={{ gridColumn: "1 / -1" }}>
                  <Field label={`Module / code  ·  currently ${item.item_id}`}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <select className="select" value={pendingMod ?? modOpts.current ?? ""} disabled={busy || !modOpts.options.length}
                        onChange={(e) => { setSaved(false); setPendingMod(e.target.value); }} style={{ width: 140 }}>
                        {modOpts.options.map((m) => <option key={m} value={m}>{m}</option>)}
                      </select>
                      {pendingMod && modOpts.current && pendingMod !== modOpts.current ? (
                        <span style={{ fontSize: 11.5, color: "var(--accent)" }}>
                          will re-code <strong>{item.item_id} → {pendingMod}…</strong> on Save (everywhere it is used)
                        </span>
                      ) : (
                        <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                          <strong>UN/UNP</strong> stay universal; a system code follows usage
                        </span>
                      )}
                    </div>
                  </Field>
                </div>
                {!isAssembly && (
                  <>
                    <Field label="Weight (g)"><NumInput value={form.weight_grams} onChange={(v) => set("weight_grams", v)} /></Field>
                    <div style={{ gridColumn: "1 / -1" }}><Field label="Materials (one or more)"><MultiRef category="material" values={form.materials} onChange={(v) => set("materials", v)} /></Field></div>
                    <Field label="Supplier"><RefSelect category="supplier" value={form.supplier} onChange={(v) => set("supplier", v)} placeholder="— supplier —" /></Field>
                    <Field label="Supplier country"><RefSelect category="country" value={form.supplier_country} onChange={(v) => set("supplier_country", v)} placeholder="— country —" /></Field>
                    <Field label="Supplier part number"><input className="input mono" value={form.supplier_part_number ?? ""} placeholder="e.g. DTM04-4P" onChange={(e) => set("supplier_part_number", e.target.value)} /></Field>
                    <Field label="Lead time (wk)"><NumInput value={form.lead_time_weeks} onChange={(v) => set("lead_time_weeks", v)} /></Field>
                  </>
                )}
                <div style={{ gridColumn: "1 / -1" }}><Field label="Drawing / CAD URL"><input className="input mono" value={form.drawing_url ?? ""} placeholder="https://…" onChange={(e) => set("drawing_url", e.target.value)} /></Field></div>
                <div style={{ gridColumn: "1 / -1" }}><Field label="Notes"><textarea className="input" style={{ height: 56, padding: 8 }} value={form.comment ?? ""} onChange={(e) => set("comment", e.target.value)} /></Field></div>
                <div style={{ gridColumn: "1 / -1" }}>
                  <LinksEditor itemId={itemId} links={links}
                    reload={() => { load(); onChanged?.(); }} setError={setError} />
                </div>
                <div style={{ gridColumn: "1 / -1" }}><Field label="Change comment (audit)"><input className="input" value={reason} placeholder="why this change…" onChange={(e) => setReason(e.target.value)} /></Field></div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12, marginTop: 14 }}>
                {saved && <span style={{ fontSize: 12.5, color: "var(--ok)" }}>✓ Saved — shown under Key figures in Overview.</span>}
                <button className="btn" onClick={saveDetails} disabled={busy}><Icon name="check" /> {busy ? "Saving…" : "Save changes"}</button>
              </div>
            </Accordion>

            {/* A leaf carries its own price; an assembly is costed from its contents, so what
                follows the item data differs between the two. */}
            {isLeaf && (
              <DecidedCostCard itemId={itemId} decided={decided} totalQty={totalQty}
                reload={() => { load(); onChanged?.(); }} setError={setError} />
            )}

            <CostEvidenceCard itemId={itemId} evidence={evidence}
              reload={() => { load(); onChanged?.(); }} setError={setError} />

            {node.children.length > 0 && (
              <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                <div className="card-head" style={{ padding: "14px 18px 10px", display: "flex", alignItems: "center", gap: 10 }}>
                  <span className="card-title" style={{ flex: 1 }}>Contents · {node.children.length}</span>
                  {!item.archived && (editQty ? (
                    <>
                      <button className="btn ghost sm" onClick={cancelQty} disabled={busy}>Cancel</button>
                      <button className="btn sm" onClick={saveQty} disabled={busy || qtyEdits().length === 0}>
                        <Icon name="check" size={11} />
                        {busy ? " Saving…" : qtyEdits().length ? ` Save ${qtyEdits().length} change${qtyEdits().length > 1 ? "s" : ""}` : " Save"}
                      </button>
                    </>
                  ) : (
                    <button className="btn ghost sm" onClick={() => setEditQty(true)} title="Change how many of each item this assembly holds">
                      Edit quantities
                    </button>
                  ))}
                </div>
                {editQty && (
                  <div style={{ padding: "0 18px 10px", fontSize: 11.5, color: "var(--ink-3)" }}>
                    Nothing is written until you press Save. Cancel discards every change.
                  </div>
                )}
                {node.children.map((c) => (
                  <div key={c.item_id} style={{ display: "grid", gridTemplateColumns: "90px minmax(0, 1fr) 78px 80px 28px", gap: 10, padding: "8px 18px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13, background: editQty && qtyChanged(c) ? "var(--accent-soft)" : undefined }}>
                    <span className="mono" style={{ fontSize: 12, cursor: "pointer" }} onClick={() => onOpenPart(c.item_id)}>{c.item_id}</span>
                    <span style={{ cursor: "pointer" }} onClick={() => onOpenPart(c.item_id)}>{c.item_name}</span>
                    {editQty ? (
                      <span style={{ display: "flex", alignItems: "center", gap: 4, justifyContent: "flex-end" }}>
                        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-4)" }}>×</span>
                        <NumInput
                          value={qtyRaw(c)}
                          onChange={(v) => setQtyDraft((q) => ({ ...q, [c.item_id]: v }))}
                          onKeyDown={(e) => { if (e.key === "Escape") cancelQty(); }}
                          title={`How many ${c.item_id} sit in ${itemId} (was ${c.quantity})`}
                          style={{ width: 54, textAlign: "right", padding: "3px 6px", fontSize: 12,
                                   borderColor: qtyBad(c) ? "var(--accent)" : undefined }}
                        />
                      </span>
                    ) : (
                      <span style={{ fontFamily: "var(--font-mono)", textAlign: "right", color: "var(--ink-3)" }}>× {c.quantity}</span>
                    )}
                    <span style={{ textAlign: "right" }}><ModulePill code={c.module_code} /></span>
                    {editQty
                      ? <span />
                      : <button className="btn ghost sm danger" title="Remove from this assembly" onClick={() => removeChild(c.item_id)}><Icon name="close" size={11} /></button>}
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
              <button className="btn ghost sm" onClick={() => setAddingChild(true)} style={{ alignSelf: "flex-start" }}>+ Add component</button>
            ))}

            <FilesTab itemId={itemId} />

            <AdvancedOptions
              itemId={itemId} item={item} isAssembly={isAssembly} parents={parents} busy={busy}
              changeCode={changeCode} setTopLevel={setTopLevel} exportBom={exportBom} />
          </div>
        )}

        {tab === "history" && (
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            <div className="card-head" style={{ padding: "14px 18px 10px" }}><span className="card-title">Change history · {history.length}</span></div>
            {history.length === 0 && <div style={{ padding: 18, color: "var(--ink-3)" }}>No changes yet.</div>}
            {history.map((h) => (
              <div key={h.id} style={{ display: "grid", gridTemplateColumns: "130px minmax(0, 1fr)", gap: 10, padding: "8px 18px", borderTop: "1px solid var(--hair-faint)", fontSize: 12.5 }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>{(h.changed_at || "").slice(0, 16).replace("T", " ")}</span>
                <span style={{ overflowWrap: "anywhere" }}><strong>{h.field_changed || h.change_type}</strong>{h.old_value != null && <span style={{ color: "var(--ink-3)" }}> {h.old_value} →</span>} <span>{h.new_value ?? ""}</span><span style={{ color: "var(--ink-3)" }}> · {h.changed_by}</span></span>
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

// One outward link. The URL is shown as far as it fits and cut with an ellipsis — a supplier
// URL is routinely 200 characters, and the whole thing in the tooltip is enough.
function LinkRow({ label, url }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 13 }}>
      <span className="label" style={{ minWidth: 96 }}>{label}</span>
      <a href={extUrl(url)} target="_blank" rel="noreferrer" className="mono" title={url}
        style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--accent)", fontSize: 12 }}>
        {url}
      </a>
      <a href={extUrl(url)} target="_blank" rel="noreferrer" className="btn ghost sm">Open ↗</a>
    </div>
  );
}

// Links are their own rows, so add/remove writes straight away rather than waiting for the
// item's Save — said plainly in the card, because every other field here is staged.
function LinksEditor({ itemId, links, reload, setError }) {
  const [type, setType] = useState("supplier");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const add = async () => {
    if (!url.trim()) return;
    setBusy(true);
    try { await api.addItemLink(itemId, { link_type: type, url: url.trim() }); setUrl(""); reload(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const remove = async (id) => {
    try { await api.deleteItemLink(itemId, id); reload(); }
    catch (e) { setError(e.message); }
  };
  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="input-label">Links</span>
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>saved as you add them, not on Save changes</span>
      </div>
      {(links || []).map((l) => (
        <div key={l.id} style={{ display: "grid", gridTemplateColumns: "110px minmax(0, 1fr) 28px", gap: 8, alignItems: "center", padding: "4px 0" }}>
          <Pill kind="warm">{l.link_type}</Pill>
          <a href={extUrl(l.url)} target="_blank" rel="noreferrer" className="mono" title={l.url}
            style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--accent)", fontSize: 12 }}>
            {l.url}
          </a>
          <button className="btn ghost sm danger" title="Remove this link" onClick={() => remove(l.id)}><Icon name="close" size={11} /></button>
        </div>
      ))}
      <div style={{ display: "grid", gridTemplateColumns: "140px minmax(0, 1fr) 64px", gap: 8, marginTop: 6, alignItems: "center" }}>
        <RefSelect category="link_type" value={type} onChange={setType} placeholder="— kind —" />
        <input className="input mono" value={url} placeholder="https://…"
               onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="btn sm" onClick={add} disabled={busy || !url.trim()}>+ add</button>
      </div>
    </div>
  );
}

function Readouts({ isAssembly, rollups, tier, setTier, item, parents, links }) {
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

      {(item.drawing_url || item.drive_folder_url || item.supplier || item.supplier_part_number
        || (links || []).length || item.comment) && (
        <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--hair-faint)", display: "grid", gap: 10 }}>
          {item.drawing_url && <LinkRow label="Drawing / CAD" url={item.drawing_url} />}
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
          {item.supplier_part_number && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 13 }}>
              <span className="label" style={{ minWidth: 96 }}>Supplier PN</span>
              <span className="mono" style={{ flex: 1, minWidth: 0, color: "var(--ink-2)", fontSize: 12, overflowWrap: "anywhere" }}>{item.supplier_part_number}</span>
            </div>
          )}
          {(links || []).map((l) => <LinkRow key={l.id} label={l.link_type} url={l.url} />)}
          {item.comment && (
            <div style={{ display: "flex", gap: 12, fontSize: 13 }}>
              <span className="label" style={{ minWidth: 96, paddingTop: 2 }}>Notes</span>
              {/* A pasted URL is one unbroken word with no break opportunity, so without
                  `anywhere` it runs straight past the card edge. */}
              <span style={{ flex: 1, minWidth: 0, color: "var(--ink-2)", whiteSpace: "pre-wrap",
                             overflowWrap: "anywhere", lineHeight: 1.45 }}>{item.comment}</span>
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
  const [mode, setMode] = useState("catalog");   // catalog | new
  const [cat, setCat] = useState(null);
  const [refMods, setRefMods] = useState([]);
  const [q, setQ] = useState("");
  const [qty, setQty] = useState(1);
  const [busy, setBusy] = useState(false);
  // new-item fields
  const [nm, setNm] = useState("");
  const [ntype, setNtype] = useState("part");
  const [nmod, setNmod] = useState("UN");
  useEffect(() => { api.catalog().then(setCat).catch((e) => setError(e.message)); }, []);
  useEffect(() => { api.reference("module").then((r) => setRefMods(r.map((x) => String(x.value).toUpperCase()))).catch(() => {}); }, []);

  const s = q.trim().toLowerCase();
  const results = !s ? [] : (cat || [])
    .filter((r) => r.item_id !== parentId && (r.item_id.toLowerCase().includes(s) || (r.item_name || "").toLowerCase().includes(s)))
    .slice(0, 40);
  const modOptions = [...new Set(["UN", "UNP", ...((cat || []).map((r) => r.module_code).filter(Boolean)), ...refMods])].sort(
    (a, b) => (a === "UN" ? -1 : b === "UN" ? 1 : a.localeCompare(b)));

  const addExisting = async (childId) => {
    setBusy(true);
    try { const r = await api.addChild(parentId, { child_id: childId, quantity: toNum(qty) || 1 }); onAdded(r); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const createAndAdd = async (force = false) => {
    if (!nm.trim()) { setError("Give the new item a name."); return; }
    setBusy(true); setError(null);
    try {
      const created = await api.createCatalogItem({ item_name: nm.trim(), item_type: ntype, module: nmod, allow_duplicate: force });
      const r = await api.addChild(parentId, { child_id: created.item_id, quantity: toNum(qty) || 1 });
      onAdded(r);
    } catch (e) {
      // Same duplicate-name guard as the catalog: confirm to add a genuinely separate part.
      if (!force && /\b409\b/.test(e.message) && /allow_duplicate/.test(e.message)) {
        const codes = (e.message.match(/\(([^)]+)\)/) || [])[1];
        setBusy(false);
        if (window.confirm(`A part named “${nm.trim()}” already exists${codes ? ` (${codes})` : ""}.\n\nCreate a separate new part anyway?`)) return createAndAdd(true);
        return;
      }
      setError(e.message);
    } finally { setBusy(false); }
  };

  return (
    <div className="card" style={{ padding: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
        <span className="card-title" style={{ flex: 1 }}>Add component</span>
        <div style={{ display: "inline-flex", gap: 4 }}>
          {[["catalog", "From catalog"], ["new", "Create new"]].map(([v, label]) => (
            <button key={v} className={`btn sm ${mode === v ? "" : "ghost"}`} onClick={() => setMode(v)}>{label}</button>
          ))}
        </div>
        <button className="btn ghost sm" onClick={onCancel}>cancel</button>
      </div>

      {mode === "catalog" ? (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <input className="input" placeholder="Search catalog by code or name…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus style={{ flex: 1 }} />
            <NumInput value={qty} onChange={setQty} style={{ width: 64 }} title="quantity" />
          </div>
          <div style={{ maxHeight: 240, overflowY: "auto" }}>
            {!cat && <div style={{ padding: 10, color: "var(--ink-3)", fontSize: 12 }}>Loading catalog…</div>}
            {cat && !s && <div style={{ padding: 10, color: "var(--ink-3)", fontSize: 12 }}>Type to search the {cat.length} catalog items…</div>}
            {results.map((r) => (
              <div key={r.item_id} style={{ display: "grid", gridTemplateColumns: "100px minmax(0, 1fr) 50px", gap: 8, padding: "6px 4px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13 }}>
                <span className="mono" style={{ fontSize: 12 }}>{r.item_id}</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.item_name}</span>
                <button className="btn sm" onClick={() => addExisting(r.item_id)} disabled={busy}>add</button>
              </div>
            ))}
            {cat && s && results.length === 0 && <div style={{ padding: 10, color: "var(--ink-3)", fontSize: 12 }}>No matches — switch to <strong>Create new</strong> to add it.</div>}
          </div>
        </>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 110px 110px 64px", gap: 8, alignItems: "end" }}>
          <Field label="Name"><input className="input" value={nm} autoFocus placeholder="e.g. Bracket" onChange={(e) => setNm(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createAndAdd()} /></Field>
          <Field label="Type"><select className="select" value={ntype} onChange={(e) => setNtype(e.target.value)}><option value="part">part</option><option value="assembly">assembly</option></select></Field>
          <Field label="Module"><select className="select" value={nmod} onChange={(e) => setNmod(e.target.value)}>{modOptions.map((m) => <option key={m} value={m}>{m}</option>)}</select></Field>
          <Field label="Qty"><NumInput value={qty} onChange={setQty} /></Field>
          <div style={{ gridColumn: "1 / -1", display: "flex", justifyContent: "flex-end" }}>
            <button className="btn sm" onClick={() => createAndAdd()} disabled={busy}><Icon name="check" size={12} /> {busy ? "Adding…" : "Create & add"}</button>
          </div>
        </div>
      )}
      <p style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 8 }}>
        Adding makes this an assembly; the component's code may change to match its usage (system code, or UN if used across systems).
      </p>
    </div>
  );
}

function WhereUsed({ itemId, parents, onOpenPart, onMoved, setError }) {
  const [moveFrom, setMoveFrom] = useState(null);  // parent id whose move-picker is open
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div className="card-head" style={{ padding: "14px 18px 10px" }}><span className="card-title">Where used · {parents.length}</span></div>
      {parents.length === 0 ? (
        <div style={{ padding: 18, color: "var(--ink-3)", fontSize: 13 }}>Top-level — not used inside another assembly.</div>
      ) : parents.map((p) => (
        <div key={p.parent} style={{ borderTop: "1px solid var(--hair-faint)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "90px minmax(0, 1fr) 54px 64px", gap: 10, padding: "8px 18px", alignItems: "center", fontSize: 13 }}>
            <span className="mono" style={{ fontSize: 12, cursor: "pointer" }} onClick={() => onOpenPart(p.parent)}>{p.parent}</span>
            <span style={{ cursor: "pointer" }} onClick={() => onOpenPart(p.parent)}>{p.name}</span>
            <span style={{ fontFamily: "var(--font-mono)", textAlign: "right", color: "var(--ink-3)" }}>× {p.quantity}</span>
            <button className="btn ghost sm" title="Move to another assembly in this BOM"
              onClick={() => setMoveFrom(moveFrom === p.parent ? null : p.parent)}>Move</button>
          </div>
          {moveFrom === p.parent && (
            <MovePicker itemId={itemId} fromParent={p.parent}
              onCancel={() => setMoveFrom(null)}
              onMoved={(r) => { setMoveFrom(null); onMoved(r); }} setError={setError} />
          )}
        </div>
      ))}
    </div>
  );
}

// Pick a new assembly to move THIS placement under. Searches the catalog for assemblies; the
// backend enforces same-BOM (a cross-BOM target returns a clear "add it there instead" message).
function MovePicker({ itemId, fromParent, onCancel, onMoved, setError }) {
  const [cat, setCat] = useState(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.catalog().then(setCat).catch((e) => setError(e.message)); }, []);
  const s = q.trim().toLowerCase();
  const results = !s ? [] : (cat || [])
    .filter((r) => r.item_type === "assembly" && r.item_id !== itemId && r.item_id !== fromParent
      && (r.item_id.toLowerCase().includes(s) || (r.item_name || "").toLowerCase().includes(s)))
    .slice(0, 30);
  const move = async (toParent) => {
    setBusy(true); setError(null);
    try { const r = await api.moveItem(itemId, { from_parent: fromParent, to_parent: toParent }); onMoved(r); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <div style={{ padding: "4px 18px 14px", background: "var(--bg-sunk, rgba(0,0,0,0.02))" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontSize: 12, color: "var(--ink-3)" }}>Move from <span className="mono">{fromParent}</span> to:</span>
        <input className="input" placeholder="search an assembly in this BOM…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus style={{ flex: 1 }} />
        <button className="btn ghost sm" onClick={onCancel}>cancel</button>
      </div>
      <div style={{ maxHeight: 200, overflowY: "auto" }}>
        {cat && !s && <div style={{ padding: 6, color: "var(--ink-3)", fontSize: 12 }}>Type to find the target assembly…</div>}
        {results.map((r) => (
          <div key={r.item_id} style={{ display: "grid", gridTemplateColumns: "100px minmax(0, 1fr) 56px", gap: 8, padding: "5px 4px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13 }}>
            <span className="mono" style={{ fontSize: 12 }}>{r.item_id}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.item_name}</span>
            <button className="btn sm" onClick={() => move(r.item_id)} disabled={busy}>move</button>
          </div>
        ))}
        {cat && s && results.length === 0 && <div style={{ padding: 6, color: "var(--ink-3)", fontSize: 12 }}>No assembly matches.</div>}
      </div>
    </div>
  );
}

function FilesTab({ itemId }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);
  const folderRef = useRef(null);
  const load = () => { setError(null); api.attachments(itemId).then(setData).catch((e) => setError(e.message)); };
  useEffect(() => { setData(null); load(); /* eslint-disable-next-line */ }, [itemId]);

  // Upload a list of files sequentially, each with an optional sub-folder path. Keeps going
  // past a failed file (one bad file shouldn't abort the whole batch) and reports failures.
  const uploadBatch = async (ref, withPaths) => {
    const files = Array.from(ref.current?.files || []);
    if (!files.length) return;
    setBusy(true); setError(null);
    const failed = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const rel = f.webkitRelativePath || f.name;
      const dir = withPaths ? rel.split("/").slice(0, -1).join("/") : "";
      setProgress(`${i + 1}/${files.length} · ${rel}`);
      try { await api.uploadAttachment(itemId, f, dir); }
      catch { failed.push(rel); }
    }
    if (ref.current) ref.current.value = "";
    setBusy(false); setProgress(null);
    if (failed.length) setError(`${failed.length}/${files.length} failed: ${failed.slice(0, 4).join(", ")}${failed.length > 4 ? "…" : ""}`);
    load();
  };
  const upload = () => uploadBatch(fileRef, false);
  const uploadFolder = () => uploadBatch(folderRef, true);

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
      <div style={{ padding: 14, borderTop: "1px solid var(--hair)", display: "grid", gap: 10 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input ref={fileRef} type="file" multiple className="input" style={{ paddingTop: 6, flex: 1 }} />
          <button className="btn sm" onClick={upload} disabled={busy} style={{ whiteSpace: "nowrap" }}><Icon name="check" /> Upload file(s)</button>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {/* webkitdirectory lets the user pick a whole folder; the structure is recreated in Drive */}
          <input ref={folderRef} type="file" webkitdirectory="" directory="" multiple className="input" style={{ paddingTop: 6, flex: 1 }} />
          <button className="btn sm" onClick={uploadFolder} disabled={busy} style={{ whiteSpace: "nowrap" }}><Icon name="box" size={12} /> Upload folder</button>
        </div>
        {busy && <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{progress || "Uploading…"}</span>}
      </div>
    </div>
  );
}

// The three volume tiers side by side, read-only — the figure the cards below it edit.
// A leaf shows its own decided unit cost; an assembly shows the rolled-up cost split into
// the parts beneath it plus its own assembly labour.
function CostOverviewCard({ isLeaf, rollups, decided }) {
  const byTier = Object.fromEntries(decided.map((x) => [x.volume_tier, x]));
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Cost overview</span>
        {!isLeaf && <span className="card-meta">parts + assembly</span>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}>
        {COST_TIERS.map((t) => {
          const r = rollups[t] || {};
          const dc = byTier[t];
          const unit = dc?.unit_cost_eur != null ? Number(dc.unit_cost_eur) : null;
          const value = isLeaf ? unit : (r.cost > 0 ? r.cost : null);
          return (
            <div key={t} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="label">@ {tierLabel(t)} pcs</span>
              <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 18, fontVariantNumeric: "tabular-nums" }}>
                {value != null && value > 0 ? fmtEURcompact(value) : "—"}
              </span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>
                {isLeaf
                  ? (unit == null ? "no decided cost" : "decided unit cost")
                  : `parts ${fmtEURcompact(r.parts_cost || 0)} + assembly ${fmtEURcompact(r.assembly_cost || 0)}`}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Everything that prices an assembly: the warnings, the cost type and the time per tier.
function AssemblyCostCards({ itemId, item, rollups, decided, labor, costTypes, reload, setError }) {
  const laborByTier = Object.fromEntries((labor || []).map((l) => [l.volume_tier, l]));
  const [tdraft, setTdraft] = useState({});
  const [adding, setAdding] = useState(false);
  const [ctName, setCtName] = useState("");
  const [ctRate, setCtRate] = useState("");
  const [busy, setBusy] = useState(false);

  const setCostType = async (id) => {
    setBusy(true);
    try { await api.patchItem(itemId, { cost_type_id: id ? Number(id) : null, change_reason: "cost type" }); reload(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const addCostTypeInline = async () => {
    if (!ctName.trim() || ctRate === "") return;
    setBusy(true);
    try {
      const r = await api.addCostType(ctName.trim(), toNum(ctRate));
      await api.patchItem(itemId, { cost_type_id: r.id });
      setAdding(false); setCtName(""); setCtRate(""); reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const tval = (vt, key) => (tdraft[vt]?.[key] ?? (laborByTier[vt]?.[key] ?? ""));
  const setT = (vt, key, v) => setTdraft((d) => ({ ...d, [vt]: { ...d[vt], [key]: v } }));
  const saveLabor = async (vt) => {
    const likely = tval(vt, "time_likely");
    if (likely === "" || likely == null) return;
    setBusy(true);
    try {
      await api.setAssemblyLabor(itemId, { volume_tier: vt, time_likely: toNum(likely), time_min: toNum(tval(vt, "time_min")), time_max: toNum(tval(vt, "time_max")), covers_subassemblies: !!tval(vt, "covers_subassemblies") });
      reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const dropDecided = async (vt) => {
    if (!window.confirm(`Remove the decided cost at @${tierLabel(vt)} from ${itemId}? The rollup already ignores it.`)) return;
    setBusy(true);
    try { await api.deleteDecidedCost(itemId, vt); reload(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  // A descendant carrying its own assembly cost under an ancestor marked as covering
  // it — the two statements contradict each other.
  const conflicts = [...new Set(COST_TIERS.flatMap((t) => rollups[t]?.covered_conflict || []))];

  return (
    <>
      {conflicts.length > 0 && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <div className="card-head"><span className="card-title">Conflicting assembly costs</span></div>
          <p style={{ fontSize: 12.5, color: "var(--ink-2)", margin: 0 }}>
            {conflicts.join(", ")} {conflicts.length === 1 ? "carries" : "carry"} an assembly
            cost, but an assembly above {conflicts.length === 1 ? "it is" : "them are"} marked
            as already covering the work below. One of the two is wrong — either untick the
            cover, or clear the assembly cost below it.
          </p>
        </div>
      )}

      {decided.length > 0 && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <div className="card-head"><span className="card-title">Unused decided cost</span></div>
          <p style={{ fontSize: 12.5, color: "var(--ink-2)", margin: "0 0 10px" }}>
            A decided cost is stored on this item but <strong>ignored</strong> — an assembly is
            costed from its contents plus assembly labour, so only a part with no contents
            uses one. It was probably set before this item gained contents.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {decided.map((dc) => (
              <div key={dc.volume_tier} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink-3)" }}>@ {tierLabel(dc.volume_tier)}</span>
                <span style={{ fontFamily: "var(--font-mono)", flex: 1 }}>{fmtEURcompact(dc.unit_cost_eur)}</span>
                <button className="btn ghost sm danger" disabled={busy}
                        onClick={() => dropDecided(dc.volume_tier)}>remove it</button>
              </div>
            ))}
          </div>
        </div>
      )}

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
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 100px 48px", gap: 8, marginTop: 8, alignItems: "end" }}>
            <Field label="name (e.g. machine-A)"><input className="input" value={ctName} onChange={(e) => setCtName(e.target.value)} /></Field>
            <Field label="€/hour"><NumInput value={ctRate} onChange={setCtRate} /></Field>
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
            <div key={t} style={{ display: "grid", gridTemplateColumns: "60px minmax(0, 1fr) 44px", gap: 8, alignItems: "end" }}>
              <Field label={`@ ${tierLabel(t)} pcs`}><span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>{t.toLocaleString()}</span></Field>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 5 }}>
                <Field label="min"><NumInput value={tval(t, "time_min")} onChange={(v) => setT(t, "time_min", v)} /></Field>
                <Field label="likely*"><NumInput value={tval(t, "time_likely")} onChange={(v) => setT(t, "time_likely", v)} /></Field>
                <Field label="max"><NumInput value={tval(t, "time_max")} onChange={(v) => setT(t, "time_max", v)} /></Field>
              </div>
              <button className="btn sm" style={{ marginBottom: 1 }} onClick={() => saveLabor(t)} disabled={busy}>set</button>
              <label style={{ gridColumn: "2 / 4", display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--ink-3)", cursor: "pointer", marginTop: -4 }}
                     title="For an outsourced or bought-in assembly, where one quoted cost already includes the work on everything below. Sub-assemblies below stop counting as missing an assembly cost.">
                <input type="checkbox" checked={!!tval(t, "covers_subassemblies")}
                       onChange={(e) => { setT(t, "covers_subassemblies", e.target.checked); }} />
                Assembly cost covers all sub-assemblies
              </label>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 11, color: "var(--ink-3)", margin: "10px 0 0" }}>
          Tick per tier, then press <strong>set</strong> — sourcing can differ by volume
          (built in house at @1, outsourced at @10k).
        </p>
      </div>
    </>
  );
}

// A leaf part's own price per volume tier.
function DecidedCostCard({ itemId, decided, totalQty, reload, setError }) {
  const byTier = Object.fromEntries(decided.map((x) => [x.volume_tier, x]));
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const dval = (vt, key, fallback) => (draft[vt]?.[key] ?? (byTier[vt]?.[key] ?? fallback));
  const setD = (vt, key, v) => setDraft((d) => ({ ...d, [vt]: { ...d[vt], [key]: v } }));
  const saveTier = async (vt) => {
    const cost = dval(vt, "unit_cost_eur", byTier[vt]?.unit_cost_eur);
    if (cost === "" || cost == null) return;
    setBusy(true);
    try {
      await api.setDecidedCost(itemId, {
        volume_tier: vt, unit_cost_eur: toNum(cost),
        cost_min: toNum(dval(vt, "cost_min", "")), cost_max: toNum(dval(vt, "cost_max", "")),
        make_or_buy: dval(vt, "make_or_buy", "") || null,
        basis_note: dval(vt, "basis_note", "") || null,
        confidence: dval(vt, "confidence", "medium"),
      });
      reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="card">
      <div className="card-head"><span className="card-title">Decided unit cost — € per piece</span><span className="card-meta">min · most-likely · max</span></div>
      <p style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 12 }}>
        Price of <strong>one piece</strong> (qty is multiplied automatically). <strong>Most-likely</strong> is
        required; <strong>min/max</strong> are optional and give a cost range. The tier is the production-volume scenario.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {COST_TIERS.map((t) => (
          <div key={t} style={{ display: "grid", gridTemplateColumns: "60px minmax(0, 1fr) 70px 44px", gap: 8, alignItems: "end" }}>
            <Field label={`@ ${tierLabel(t)} pcs`}><span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-3)" }}>~{(totalQty * t).toLocaleString()}</span></Field>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 5 }}>
              <Field label="min €"><NumInput value={dval(t, "cost_min", "")} onChange={(v) => setD(t, "cost_min", v)} /></Field>
              <Field label="likely €*"><NumInput value={dval(t, "unit_cost_eur", "")} onChange={(v) => setD(t, "unit_cost_eur", v)} /></Field>
              <Field label="max €"><NumInput value={dval(t, "cost_max", "")} onChange={(v) => setD(t, "cost_max", v)} /></Field>
            </div>
            <Field label="Sourcing">
              <select className="select" value={dval(t, "make_or_buy", "")} onChange={(e) => setD(t, "make_or_buy", e.target.value)}
                      title="How we get this part at this volume. It can differ by tier — a prototype made in house at @1 may be bought at @10k.">
                <option value="">—</option>
                <option value="buy">buy (off the shelf)</option>
                <option value="made-to-order">made to order (our specs)</option>
                <option value="make">make in house</option>
              </select>
            </Field>
            <button className="btn sm" style={{ marginBottom: 1 }} onClick={() => saveTier(t)} disabled={busy}>set</button>
          </div>
        ))}
      </div>
    </div>
  );
}

// Quotes, invoices and estimates behind the decided cost. Kept for assemblies too — a
// bought-in assembly has quotes like any part.
function CostEvidenceCard({ itemId, evidence, reload, setError }) {
  const [busy, setBusy] = useState(false);
  const [ev, setEv] = useState({ source_type: "quote", unit_cost: "", volume_tier: 100, supplier_name: "", confidence: "high", note: "" });
  const addEvidence = async () => {
    if (!ev.unit_cost) return;
    setBusy(true);
    try {
      await api.addCostEvidence(itemId, { ...ev, unit_cost: toNum(ev.unit_cost), volume_tier: toNum(ev.volume_tier) });
      setEv({ ...ev, unit_cost: "", supplier_name: "", note: "" }); reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
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
            {q.note && <div style={{ color: "var(--ink-3)", fontSize: 11.5, marginTop: 3, overflowWrap: "anywhere" }}>{q.note}</div>}
          </div>
        ))}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr) 80px 80px", gap: 8, marginTop: 12, alignItems: "end" }}>
          <Field label="Source"><select className="select" value={ev.source_type} onChange={(e) => setEv({ ...ev, source_type: e.target.value })}>{SOURCES.map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Supplier"><RefSelect category="supplier" value={ev.supplier_name} onChange={(v) => setEv({ ...ev, supplier_name: v })} placeholder="—" /></Field>
          <Field label="€/unit"><NumInput value={ev.unit_cost} onChange={(v) => setEv({ ...ev, unit_cost: v })} /></Field>
          <Field label="Volume"><NumInput value={ev.volume_tier} onChange={(v) => setEv({ ...ev, volume_tier: v })} /></Field>
        </div>
        <div style={{ marginTop: 8 }}><Field label="Note (reasoning / math)"><input className="input" value={ev.note} placeholder="e.g. derived from 1.2 kg × €4.5/kg + machining" onChange={(e) => setEv({ ...ev, note: e.target.value })} /></Field></div>
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}><button className="btn sm" onClick={addEvidence} disabled={busy}><Icon name="check" /> add evidence</button></div>
      </div>
    </Accordion>
  );
}

// Rare, structural actions — renumbering, BOM roots, export. Collapsed by default so the
// Edit tab opens on the things people actually change every day.
function AdvancedOptions({ itemId, item, isAssembly, parents, busy, changeCode, setTopLevel, exportBom }) {
  return (
    <Accordion title="Advanced options" meta="numbering · BOM root · export">
      <div style={{ display: "flex", flexDirection: "column", gap: 12, paddingTop: 14 }}>
        {!item.archived && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div className="card-title">Item number</div>
              <p style={{ fontSize: 11.5, color: "var(--ink-3)", margin: "4px 0 0" }}>
                Renumber automatically to the lowest never-used code, or type one. A number
                that was used before is never reissued.
              </p>
            </div>
            <button className="btn ghost sm" disabled={busy} onClick={() => changeCode("auto")}>Renumber automatically</button>
            <button className="btn ghost sm" disabled={busy} onClick={() => changeCode("manual")}>Type a code…</button>
          </div>
        )}

        {isAssembly && !item.archived && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, borderTop: "1px solid var(--hair-faint)", paddingTop: 12 }}>
            <div style={{ flex: 1 }}>
              <div className="card-title">Top-level BOM</div>
              <p style={{ fontSize: 11.5, color: "var(--ink-3)", margin: "4px 0 0" }}>
                {item.is_top_level
                  ? "This assembly is a BOM root — it appears at the top of Browse and everything inside it counts as in use."
                  : parents.length
                    ? `Not available: ${itemId} sits inside ${parents.map((p) => p.parent).join(", ")}. Remove it from there first, or it would appear twice in the tree.`
                    : "Make this assembly a root of its own BOM."}
              </p>
            </div>
            <button className="btn ghost sm"
                    disabled={busy || (!item.is_top_level && parents.length > 0)}
                    onClick={() => setTopLevel(!item.is_top_level)}>
              {item.is_top_level ? "Make it normal" : "Make top-level"}
            </button>
          </div>
        )}

        {isAssembly && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, borderTop: "1px solid var(--hair-faint)", paddingTop: 12 }}>
            <span className="card-title" style={{ flex: 1 }}>Export this {item.is_top_level ? "BOM" : "assembly"}</span>
            <button className="btn ghost sm" onClick={() => exportBom("opml")} disabled={busy}><Icon name="box" size={12} /> OPML</button>
            <button className="btn ghost sm" onClick={() => exportBom("csv")} disabled={busy}><Icon name="box" size={12} /> CSV</button>
          </div>
        )}
      </div>
    </Accordion>
  );
}
