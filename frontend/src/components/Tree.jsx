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
  // "tree" = the nested hierarchy; "flat" = each BOM one level deep with full counts.
  const [view, setView] = useState("tree");
  const [flat, setFlat] = useState(null);
  // On by default: leaf rows sum to the BOM's parts cost, so the Total column is safe to
  // read as a sum. Assembly rows carry their own process cost and would double-count.
  const [leafOnly, setLeafOnly] = useState(true);
  const [newBom, setNewBom] = useState(false);
  const [drag, setDrag] = useState(null);      // { child, fromParent, root, key, name }
  const [dropKey, setDropKey] = useState(null);
  const [undo, setUndo] = useState(null);      // { child, from, to, name }
  const [moving, setMoving] = useState(false);
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

  const loadFlat = () =>
    api.flat(null, tier).then(setFlat).catch((e) => setError(e.message));
  useEffect(() => { if (view === "flat") loadFlat(); /* eslint-disable-next-line */ }, [view, version, tier]);

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

  // ── drag & drop: move a placement to another assembly in the SAME BOM ──────
  // A row's pathKey ("root/.../parent/child") gives us the part, its current parent and its BOM.
  const rowInfo = (key, node) => {
    const segs = String(key).split("/");
    return { child: node.item_id, fromParent: segs[segs.length - 2] || null, root: segs[0], key, name: node.item_name };
  };
  const validTarget = (key, node) => {
    if (!drag) return false;
    if (node.item_type !== "assembly") return false;        // drop only onto assemblies
    const segs = String(key).split("/");
    if (segs[0] !== drag.root) return false;                // same BOM only
    if (node.item_id === drag.child || node.item_id === drag.fromParent) return false;  // itself / already there
    if (segs.includes(drag.child)) return false;            // can't drop into its own subtree (loop)
    return true;
  };
  const doMove = async (toParent) => {
    if (!drag) return;
    const { child, fromParent } = drag;
    const name = drag.name;
    setMoving(true); setError(null);
    try {
      const r = await api.moveItem(child, { from_parent: fromParent, to_parent: toParent });
      setExpanded((p) => new Set(p).add(toParent));         // reveal the result
      await loadTree();
      setUndo({ child: r.child_id, from: r.to_parent, to: r.from_parent, name });
    } catch (e) { setError(e.message); }
    finally { setMoving(false); setDrag(null); setDropKey(null); }
  };
  const doUndo = async () => {
    if (!undo) return;
    const u = undo; setUndo(null); setError(null);
    try { await api.moveItem(u.child, { from_parent: u.from, to_parent: u.to }); await loadTree(); }
    catch (e) { setError(e.message); }
  };

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

  // `anc[i]` = the ancestor occupying rail column i still has siblings below this row, so
  // its guide line runs straight through. The last column is always the elbow into this
  // row, shaped by `isLast`. Both are computed over the *visible* children, so the rails
  // stay honest when a filter hides siblings.
  // The left zone is the structure zone: it moves the tree and dismisses the details panel,
  // which would otherwise sit there describing a part you have navigated away from.
  const expandZone = (n) => {
    if (focus) onOpenPart(null);
    // A leaf has nothing below it, so dismissing the panel is the whole action.
    if (n.has_children && (n.children?.length ?? 0) > 0) toggle(n.item_id);
  };

  const rows = [];
  const walk = (n, depth, pathKey, anc, isLast) => {
    const open = expanded.has(n.item_id) || (filtering && forceOpen.has(n.item_id));
    rows.push({ n, depth, open, key: pathKey, anc, isLast });
    if (!open) return;
    const kids = filtering ? n.children.filter(subtreeMatches) : n.children;
    // A root sits flush left with no rail column of its own, so its children start on a
    // bare elbow rather than inheriting a line.
    const childAnc = depth === 0 ? [] : [...anc, !isLast];
    kids.forEach((c, i) =>
      walk(c, depth + 1, `${pathKey}/${c.item_id}`, childAnc, i === kids.length - 1));
  };
  const visibleRoots = filtering ? (roots || []).filter(subtreeMatches) : (roots || []);
  visibleRoots.forEach((r, i) => walk(r, 0, r.item_id, [], i === visibleRoots.length - 1));

  const partCount = rows.filter((r) => r.n.item_type === "part").length;
  const asmCount = rows.filter((r) => r.n.item_type === "assembly").length;

  if (error) return <div className="page"><p className="err">Failed to load tree: {error}</p></div>;
  if (!roots) return <div className="page"><p className="muted">Loading BOM…</p></div>;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Browse</div>
          <h1 className="page-title">{view === "tree" ? "BOM tree" : "Flattened BOM"}</h1>
          <p className="page-sub">
            {view === "tree" ? (
              <>
                The microplant hierarchy. Expand assemblies, <strong>drag a part onto another assembly</strong> to
                move it (same BOM), and click any item to inspect.
              </>
            ) : (
              <>
                Each BOM one level deep: every item once, with the <strong>count the whole BOM needs</strong> —
                quantities multiplied down the tree. Structure editing lives in the tree view.
              </>
            )}
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="segmented-mini">
            <button className={view === "tree" ? "on" : ""} onClick={() => setView("tree")}>BOM tree</button>
            <button className={view === "flat" ? "on" : ""} onClick={() => setView("flat")}>Flattened</button>
          </span>
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
          {view === "tree" && <button className="btn ghost sm" onClick={expandAll}><Icon name="chevD" size={12} /> Expand all</button>}
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
        {view === "flat" ? (
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-3)", cursor: "pointer", whiteSpace: "nowrap" }}
                 title="Leaf parts only — then the Total column adds up to the BOM's parts cost. Including sub-assemblies double-counts, since their cost is already inside their own parts.">
            <input type="checkbox" checked={leafOnly} onChange={(e) => setLeafOnly(e.target.checked)} />
            leaf parts only
          </label>
        ) : (
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>
            {partCount} parts · {asmCount} assemblies
          </div>
        )}
      </div>

      {view === "tree" && (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div className="tree-head">
            <div>Part / assembly</div>
            <div className="tree-data">
              <div className="right">Qty</div>
              <div className="right">Cost @ {tierLabel(tier)}</div>
              <div className="right">Total</div>
              <div className="right">Coverage</div>
              <div className="right">Module</div>
              <div></div>
            </div>
          </div>
          <div className="tree">
            {rows.map(({ n, depth, open, key, anc, isLast }) => {
              const expandable = n.has_children && (n.children?.length ?? 0) > 0;  // false when a loop cut its children
              const isDragging = drag?.key === key;
              const isValid = validTarget(key, n);
              const isDrop = dropKey === key && isValid;
              return (
              <div
                key={key}
                className={`tree-row ${focus === n.item_id ? "on" : ""}`}
                style={{
                  "--indent": `${12 + depth * 22}px`,
                  // Only the custom property is set — never `background` itself — so the
                  // :hover and .on rules still win in the cascade.
                  "--tint": depth ? `rgba(28,27,26,${(Math.min(depth, 5) * 0.014).toFixed(3)})` : "transparent",
                  cursor: depth > 0 ? "grab" : undefined,
                  opacity: isDragging ? 0.4 : 1,
                  ...(isDrop
                    ? { background: "rgba(228,0,43,0.10)", outline: "2px solid var(--accent)", outlineOffset: "-2px" }
                    : isValid ? { background: "rgba(228,0,43,0.035)" } : {}),
                }}
                draggable={depth > 0}
                onDragStart={(e) => { setDrag(rowInfo(key, n)); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", n.item_id); }}
                onDragEnd={() => { setDrag(null); setDropKey(null); }}
                onDragOver={(e) => { if (isValid) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (dropKey !== key) setDropKey(key); } }}
                onDragLeave={() => setDropKey((d) => (d === key ? null : d))}
                onDrop={(e) => { if (isValid) { e.preventDefault(); doMove(n.item_id); } }}
                title={drag ? (isValid ? `Move ${drag.name} into ${n.item_name}` : "")
                  : "Open details · drag to move"}
                onClick={() => onOpenPart(n.item_id)}
              >
                {depth > 0 && (
                  <span className="tree-rails" aria-hidden="true">
                    {Array.from({ length: depth }, (_, i) => (
                      <span key={i} className={i === depth - 1
                        ? `tree-rail elbow${isLast ? " last" : ""}`
                        : `tree-rail${anc[i] ? " line" : ""}`} />
                    ))}
                  </span>
                )}
                <div
                  className={`tree-name ${expandable ? "expands" : focus ? "dismisses" : "inert"}`}
                  title={expandable ? (open ? "Collapse" : "Expand one level") : focus ? "Close details" : ""}
                  onClick={(e) => { e.stopPropagation(); expandZone(n); }}
                >
                  <button
                    className={`tree-toggle ${expandable ? "" : "is-leaf"}`}
                    tabIndex={expandable ? 0 : -1}
                    onClick={(e) => { e.stopPropagation(); expandZone(n); }}
                  >
                    <Icon name={open ? "chevD" : "chevR"} size={12} />
                  </button>
                  <span className="num">{n.item_id}</span>
                  <span className={`lbl ${n.item_type === "assembly" ? "assembly" : ""}`}>{n.item_name}</span>
                  {n.item_type === "assembly" && <Pill kind="warm">asm</Pill>}
                </div>
                <div className="tree-data" title="Open details">
                <div className="qty">× {n.quantity}</div>
                <div className={`cost ${n.rollup_cost === 0 ? "missing" : ""}`}>
                  {n.rollup_cost > 0 ? fmtEURcompact(n.rollup_cost) : "—"}
                </div>
                {/* Line extended cost: unit rollup x the quantity on THIS row. Deliberately not
                    the effective quantity from the root, so it always agrees with the two
                    columns beside it — the trade-off is that leaf lines don't sum to the BOM. */}
                <div className={`cost ${n.rollup_cost === 0 ? "missing" : ""}`}
                     title={n.rollup_cost > 0 ? `${fmtEURcompact(n.rollup_cost)} x ${n.quantity}` : undefined}>
                  {n.rollup_cost > 0 ? fmtEURcompact(n.rollup_cost * n.quantity) : "—"}
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
      )}

      {view === "flat" && (flat === null
        ? <p className="muted">Flattening…</p>
        : <FlatView boms={flat} tier={tier} tierLabel={tierLabel} expanded={expanded} toggle={toggle}
            matches={matches} filtering={filtering} leafOnly={leafOnly}
            focus={focus} onOpenPart={onOpenPart} />)}

      {(undo || moving) && (
        <div style={{ position: "sticky", bottom: 16, marginTop: 12, display: "flex", justifyContent: "center", pointerEvents: "none" }}>
          <div style={{ display: "inline-flex", gap: 12, alignItems: "center", background: "var(--bg-raised)", border: "1px solid var(--hair-strong)", borderRadius: 8, padding: "8px 14px", boxShadow: "var(--shadow-2)", fontSize: 13, pointerEvents: "auto" }}>
            {moving ? <span style={{ color: "var(--ink-3)" }}>Moving…</span> : (
              <>
                <span>Moved <strong>{undo.name}</strong> → <span className="mono">{undo.from}</span></span>
                <button className="btn ghost sm" onClick={doUndo}>Undo</button>
                <button className="btn ghost sm" title="dismiss" onClick={() => setUndo(null)}><Icon name="close" size={11} /></button>
              </>
            )}
          </div>
        </div>
      )}
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
        Creates an empty top-level assembly in this system. Add components from the item drawer; all the naming rules apply automatically.
      </p>
    </div>
  );
}

// The same window as the tree, flattened: each top-level BOM is still a row you expand, but
// what opens is one level deep — every descendant once, with the count the plant actually
// needs (quantity multiplied along every path, summed over paths). Drag-to-move is absent by
// design: a flat row has no single parent, so moving it means nothing here.
function FlatView({ boms, tier, tierLabel, expanded, toggle, matches, filtering, leafOnly, focus, onOpenPart }) {
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div className="tree-head">
        <div>Part / assembly</div>
        <div className="tree-data flat">
          <div className="right">Count</div>
          <div className="right">Unit @ {tierLabel(tier)}</div>
          <div className="right">Total</div>
          <div className="right">Share</div>
          <div className="right">Coverage</div>
          <div className="right">Module</div>
          <div></div>
        </div>
      </div>
      <div className="tree">
        {boms.length === 0 && (
          <div className="empty" style={{ padding: 24, textAlign: "center", color: "var(--ink-3)" }}>
            No top-level BOMs yet.
          </div>
        )}
        {boms.map((bom) => {
          const open = expanded.has(bom.item_id);
          const rows = (bom.rows || [])
            .filter((r) => (leafOnly ? r.is_leaf : true))
            .filter((r) => !filtering || matches(r));
          return (
            <div key={bom.item_id}>
              <div className={`tree-row ${focus === bom.item_id ? "on" : ""}`}
                   style={{ "--indent": "12px", "--tint": "transparent" }}
                   onClick={() => onOpenPart(bom.item_id)}>
                <div className="tree-name expands" title={open ? "Collapse" : "Flatten this BOM"}
                     onClick={(e) => { e.stopPropagation(); toggle(bom.item_id); }}>
                  <button className="tree-toggle" onClick={(e) => { e.stopPropagation(); toggle(bom.item_id); }}>
                    <Icon name={open ? "chevD" : "chevR"} size={12} />
                  </button>
                  <span className="num">{bom.item_id}</span>
                  <span className="lbl assembly">{bom.item_name}</span>
                  <Pill kind="warm">asm</Pill>
                </div>
                <div className="tree-data flat" title="Open details">
                  <div className="qty">{rows.length} rows</div>
                  <div className="cost">—</div>
                  <div className={`cost ${bom.cost === 0 ? "missing" : ""}`}>
                    {bom.cost > 0 ? fmtEURcompact(bom.cost) : "—"}
                  </div>
                  <div className="qty">100%</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, justifyContent: "flex-end" }}>
                    <div className="cov-bar" style={{ width: 38 }}>
                      <div className="filled" style={{ width: `${bom.coverage * 100}%` }} />
                    </div>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>
                      {Math.round(bom.coverage * 100)}%
                    </span>
                  </div>
                  <div style={{ textAlign: "right" }}><ModulePill code={bom.module_code} /></div>
                  <button className="tree-toggle" title="Open details"
                          style={{ justifySelf: "end", width: 26, height: 26, color: "var(--ink-3)" }}
                          onClick={(e) => { e.stopPropagation(); onOpenPart(bom.item_id); }}>
                    <Icon name="chevR" size={13} />
                  </button>
                </div>
              </div>

              {open && rows.length === 0 && (
                <div style={{ padding: "10px 34px", fontSize: 12.5, color: "var(--ink-3)" }}>
                  Nothing here{filtering ? " matches the filters" : ""}.
                </div>
              )}

              {open && rows.map((r) => (
                <div key={r.item_id} className={`tree-row ${focus === r.item_id ? "on" : ""}`}
                     style={{ "--indent": "34px", "--tint": "rgba(28,27,26,0.014)" }}
                     title="Open details"
                     onClick={() => onOpenPart(r.item_id)}>
                  <div className="tree-name inert">
                    <span className="num">{r.item_id}</span>
                    <span className={`lbl ${r.is_leaf ? "" : "assembly"}`}>{r.item_name}</span>
                    {!r.is_leaf && <Pill kind="warm">asm</Pill>}
                  </div>
                  <div className="tree-data flat">
                    <div className="qty">× {r.count.toLocaleString()}</div>
                    <div className={`cost ${r.unit_cost == null ? "missing" : ""}`}
                         title={r.is_leaf ? "decided unit cost" : "this assembly's own process cost per build"}>
                      {r.unit_cost != null ? fmtEURcompact(r.unit_cost) : "—"}
                    </div>
                    <div className={`cost ${r.cost === 0 ? "missing" : ""}`}
                         title={r.unit_cost != null ? `${fmtEURcompact(r.unit_cost)} x ${r.count.toLocaleString()}` : undefined}>
                      {r.cost > 0 ? fmtEURcompact(r.cost) : "—"}
                    </div>
                    <div className="qty">{r.share > 0 ? `${(r.share * 100).toFixed(1)}%` : "—"}</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, justifyContent: "flex-end" }}>
                      <div className="cov-bar" style={{ width: 38 }}>
                        <div className="filled" style={{ width: `${r.coverage * 100}%` }} />
                      </div>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>
                        {Math.round(r.coverage * 100)}%
                      </span>
                    </div>
                    <div style={{ textAlign: "right" }}><ModulePill code={r.module_code} /></div>
                    <button className="tree-toggle" title="Open details"
                            style={{ justifySelf: "end", width: 26, height: 26, color: "var(--ink-3)" }}
                            onClick={(e) => { e.stopPropagation(); onOpenPart(r.item_id); }}>
                      <Icon name="chevR" size={13} />
                    </button>
                  </div>
                </div>
              ))}

              {/* The root's own assembly labour has no row — the root is the thing being
                  flattened — so it is stated here rather than quietly missing from the sum. */}
              {open && bom.own_assembly_cost > 0 && (
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10,
                              padding: "8px 34px", borderBottom: "1px solid var(--hair-faint)",
                              fontSize: 12, color: "var(--ink-3)" }}>
                  <span>plus the final assembly of {bom.item_id} itself</span>
                  <span style={{ fontFamily: "var(--font-mono)" }}>{fmtEURcompact(bom.own_assembly_cost)}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
