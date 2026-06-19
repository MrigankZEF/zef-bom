import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Icon, ModulePill, Pill, fmtEURcompact } from "./ui";

// Browse / BOM tree — ported from the prototype, wired to GET /tree (nested nodes
// with embedded rollups). Expand state + filtering live client-side.
export default function Tree({ onOpenPart, focus, version }) {
  const [roots, setRoots] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());
  const [query, setQuery] = useState("");
  const [moduleF, setModuleF] = useState("all");
  const [coverageF, setCoverageF] = useState("all");
  const [tier, setTier] = useState(100);
  const [newBom, setNewBom] = useState(false);
  const tierLabel = (v) => (v >= 1000 ? `${v / 1000}k` : `${v}`);

  const loadTree = () =>
    api
      .tree(null, tier)
      .then((data) => {
        setRoots(data);
        setExpanded((prev) => (prev.size ? prev : new Set(data[0] ? [data[0].item_id] : [])));
      })
      .catch((e) => setError(e.message));
  useEffect(() => { loadTree(); /* eslint-disable-next-line */ }, [version, tier]);

  const modules = useMemo(() => {
    const set = new Set();
    const walk = (n) => { if (n.module_code) set.add(n.module_code); n.children.forEach(walk); };
    (roots || []).forEach(walk);
    return [...set].sort();
  }, [roots]);

  const toggle = (id) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const expandAll = () => {
    const ids = new Set();
    const collect = (n) => { if (n.has_children) ids.add(n.item_id); n.children.forEach(collect); };
    (roots || []).forEach(collect);
    setExpanded(ids);
  };
  const collapseAll = () => setExpanded(new Set());

  const matches = (n) => {
    if (moduleF !== "all" && n.module_code !== moduleF) return false;
    if (coverageF === "covered" && n.coverage < 1) return false;
    if (coverageF === "uncovered" && n.coverage >= 1) return false;
    if (query) {
      const q = query.toLowerCase();
      if (!n.item_id.toLowerCase().includes(q) && !n.item_name.toLowerCase().includes(q)) return false;
    }
    return true;
  };
  const subtreeMatches = (n) => matches(n) || n.children.some(subtreeMatches);
  const filtering = query || moduleF !== "all" || coverageF !== "all";

  // While filtering, force-open the ancestors of matches (computed fresh, NOT stored).
  // This never touches `expanded`, so clearing the search snaps back to your manual state.
  const forceOpen = new Set();
  if (filtering && roots) {
    const visit = (n, path) => {
      if (matches(n)) path.forEach((id) => forceOpen.add(id));
      n.children.forEach((c) => visit(c, [...path, n.item_id]));
    };
    roots.forEach((r) => visit(r, []));
  }

  const rows = [];
  const walk = (n, depth, pathKey) => {
    if (filtering && !subtreeMatches(n)) return;
    const open = expanded.has(n.item_id) || (filtering && forceOpen.has(n.item_id));
    rows.push({ n, depth, open, key: pathKey });
    if (open) n.children.forEach((c) => walk(c, depth + 1, `${pathKey}/${c.item_id}`));
  };
  (roots || []).forEach((r) => walk(r, 0, r.item_id));

  const partCount = rows.filter((r) => r.n.item_type === "part").length;
  const asmCount = rows.filter((r) => r.n.item_type === "assembly").length;

  if (error) return <div className="page"><p className="err">Failed to load tree: {error}</p></div>;
  if (!roots) return <div className="page"><p className="muted">Loading BOM…</p></div>;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Browse</div>
          <h1 className="page-title">BOM tree</h1>
          <p className="page-sub">
            The microplant hierarchy. Expand assemblies, filter, and click any item to inspect.
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="card-meta" style={{ marginRight: 2 }}>cost @</span>
          <span style={{ display: "inline-flex", border: "1px solid var(--hair)", borderRadius: 7, overflow: "hidden" }}>
            {[1, 100, 10000].map((t) => (
              <button key={t} onClick={() => setTier(t)} title={`${t.toLocaleString()} pcs`}
                style={{ border: 0, cursor: "pointer", padding: "5px 11px", fontFamily: "var(--font-mono)", fontSize: 11,
                  background: tier === t ? "var(--accent)" : "transparent", color: tier === t ? "#fff" : "var(--ink-3)" }}>
                {tierLabel(t)}
              </button>
            ))}
          </span>
          <button className="btn ghost sm" onClick={expandAll}><Icon name="chevD" size={12} /> Expand all</button>
          <button className="btn ghost sm" onClick={collapseAll}><Icon name="chevR" size={12} /> Collapse all</button>
          <button className="btn sm" onClick={() => setNewBom((v) => !v)}><Icon name="box" size={12} /> New BOM</button>
        </div>
      </div>

      {newBom && (
        <NewBomPanel
          systems={modules}
          onCancel={() => setNewBom(false)}
          onCreated={(root) => { setNewBom(false); loadTree().then(() => onOpenPart(root.item_id)); }}
          setError={setError}
        />
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto auto", gap: 12, marginBottom: 16, alignItems: "center" }}>
        <div className="search">
          <Icon name="search" className="ico" />
          <input placeholder="Search by part number or name…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <select className="select" value={moduleF} onChange={(e) => setModuleF(e.target.value)}>
          <option value="all">All modules</option>
          {modules.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select className="select" value={coverageF} onChange={(e) => setCoverageF(e.target.value)}>
          <option value="all">All coverage</option>
          <option value="covered">Fully costed</option>
          <option value="uncovered">Has uncosted</option>
        </select>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>
          {partCount} parts · {asmCount} assemblies
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div className="tree-head">
          <div>Part / assembly</div>
          <div className="right">Qty</div>
          <div className="right">Cost @ {tierLabel(tier)}</div>
          <div className="right">Coverage</div>
          <div className="right">Module</div>
          <div></div>
        </div>
        <div className="tree">
          {rows.map(({ n, depth, open, key }) => {
            const expandable = n.has_children && (n.children?.length ?? 0) > 0;  // false when a loop cut its children
            return (
            <div
              key={key}
              className={`tree-row ${focus === n.item_id ? "on" : ""}`}
              style={{ "--indent": `${12 + depth * 22}px` }}
              title={expandable ? "Click to expand · arrow opens details" : (n.has_children ? "In a loop — click to open details" : "Click to open details")}
              onClick={() => (expandable ? toggle(n.item_id) : onOpenPart(n.item_id))}
            >
              <div className="tree-name">
                <button
                  className={`tree-toggle ${expandable ? "" : "is-leaf"}`}
                  onClick={(e) => { e.stopPropagation(); expandable ? toggle(n.item_id) : onOpenPart(n.item_id); }}
                >
                  <Icon name={open ? "chevD" : "chevR"} size={12} />
                </button>
                <span className="num">{n.item_id}</span>
                <span className={`lbl ${n.item_type === "assembly" ? "assembly" : ""}`}>{n.item_name}</span>
                {n.item_type === "assembly" && <Pill kind="warm">asm</Pill>}
              </div>
              <div className="qty">× {n.quantity}</div>
              <div className={`cost ${n.rollup_cost === 0 ? "missing" : ""}`}>
                {n.rollup_cost > 0 ? fmtEURcompact(n.rollup_cost) : "—"}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, justifyContent: "flex-end" }}>
                <div className="cov-bar" style={{ width: 38 }}>
                  <div className="filled" style={{ width: `${n.coverage * 100}%` }} />
                </div>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>
                  {Math.round(n.coverage * 100)}%
                </span>
              </div>
              <div style={{ textAlign: "right" }}><ModulePill code={n.module_code} /></div>
              <button
                className="tree-toggle"
                title="Open details"
                style={{ justifySelf: "end", width: 26, height: 26, color: "var(--ink-3)" }}
                onClick={(e) => { e.stopPropagation(); onOpenPart(n.item_id); }}
              >
                <Icon name="chevR" size={13} />
              </button>
            </div>
            );
          })}
          {rows.length === 0 && (
            <div className="empty" style={{ padding: 24, textAlign: "center", color: "var(--ink-3)" }}>
              No items match these filters.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Create a brand-new top-level BOM (no Miro import): a name + the system it belongs to.
// The root is an assembly; children are added afterwards from the part drawer.
function NewBomPanel({ systems, onCancel, onCreated, setError }) {
  const [name, setName] = useState("");
  const [mod, setMod] = useState("");
  const [refMods, setRefMods] = useState([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.reference("module").then((r) => setRefMods(r.map((x) => String(x.value).toUpperCase()))).catch(() => {}); }, []);
  const options = [...new Set([...(systems || []), ...refMods])]
    .filter((m) => m && m !== "UN" && m !== "UNP").sort();

  const create = async () => {
    const system = mod.trim().toUpperCase();
    if (!name.trim() || !system) { setError("Give the BOM a name and a system (e.g. AEC)."); return; }
    setBusy(true); setError(null);
    try { const r = await api.createBom({ item_name: name.trim(), module: system }); onCreated(r); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="card" style={{ marginBottom: 16, padding: 14, display: "flex", gap: 10, alignItems: "end", flexWrap: "wrap", borderColor: "var(--accent)" }}>
      <div style={{ flex: 1, minWidth: 220 }}>
        <span className="input-label">New BOM name</span>
        <input className="input" value={name} autoFocus placeholder="e.g. DAC 18 — Standalone"
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
      </div>
      <div style={{ width: 160 }}>
        <span className="input-label">System</span>
        <input className="input mono" list="bom-systems" value={mod} placeholder="AEC"
          onChange={(e) => setMod(e.target.value.toUpperCase())} onKeyDown={(e) => e.key === "Enter" && create()} />
        <datalist id="bom-systems">{options.map((m) => <option key={m} value={m} />)}</datalist>
      </div>
      <button className="btn" onClick={create} disabled={busy}><Icon name="check" size={12} /> {busy ? "Creating…" : "Create BOM"}</button>
      <button className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button>
      <p style={{ width: "100%", margin: "2px 0 0", fontSize: 11.5, color: "var(--ink-3)" }}>
        Creates an empty top-level assembly in this system. Add parts &amp; sub-assemblies from the item drawer; all the naming rules apply automatically.
      </p>
    </div>
  );
}
