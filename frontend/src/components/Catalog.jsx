import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { ModulePill, fmtEURcompact } from "./ui";

const TIERS = [1, 100, 10000];
const tierLabel = (v) => (v >= 1000 ? `${v / 1000}k` : `${v}`);
const COLS = "104px 1fr 64px 96px 96px 96px";

function CostCell({ c }) {
  if (!c || c.likely == null) return <span style={{ color: "var(--ink-4)" }}>—</span>;
  const range = (c.min != null && c.min < c.likely) || (c.max != null && c.max > c.likely);
  return (
    <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
      {fmtEURcompact(c.likely)}
      {range && (
        <span style={{ color: "var(--ink-3)", fontSize: 9.5, display: "block" }}>
          {c.min != null ? fmtEURcompact(c.min) : "?"}–{c.max != null ? fmtEURcompact(c.max) : "?"}
        </span>
      )}
    </span>
  );
}

export default function Catalog({ onOpenPart, version }) {
  const [rows, setRows] = useState(null);
  const [q, setQ] = useState("");
  const [fMod, setFMod] = useState("all");
  const [fType, setFType] = useState("all");
  const [fCost, setFCost] = useState("all");
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [nm, setNm] = useState("");
  const [ntype, setNtype] = useState("part");
  const [nmod, setNmod] = useState("UN");
  const [refMods, setRefMods] = useState([]);

  useEffect(() => { setRows(null); api.catalog().then(setRows).catch((e) => setError(e.message)); }, [version]);
  useEffect(() => { api.reference("module").then((r) => setRefMods(r.map((x) => x.value))).catch(() => {}); }, []);

  // Module choices: UN first, then every system code in use + any admin-added module.
  const modules = useMemo(() => {
    const s = new Set(["UN", "UNP"]);
    (rows || []).forEach((r) => r.module_code && s.add(r.module_code));
    refMods.forEach((m) => m && s.add(String(m).toUpperCase()));
    return [...s].sort((a, b) => (a === "UN" ? -1 : b === "UN" ? 1 : a.localeCompare(b)));
  }, [rows, refMods]);

  const create = async (force = false) => {
    if (!nm.trim()) return;
    try {
      const r = await api.createCatalogItem({ item_name: nm.trim(), item_type: ntype, module: nmod, allow_duplicate: force });
      setNm(""); setAdding(false); setError(null);
      api.catalog().then(setRows);
      onOpenPart?.(r.item_id);
    } catch (e) {
      // Duplicate-name guard: the backend rejects a name that already exists (409) unless we
      // confirm it's a genuinely different part. Offer that choice rather than silently failing.
      if (!force && /\b409\b/.test(e.message) && /allow_duplicate/.test(e.message)) {
        const detail = (e.message.match(/"detail"\s*:\s*"([^"]+)"/) || [])[1] || "";
        const codes = (detail.match(/\(([^)]+)\)/) || [])[1];
        if (window.confirm(
          `A part named “${nm.trim()}” already exists${codes ? ` (${codes})` : ""}.\n\n` +
          `Add this as a separate part anyway? It will get its own new code.`
        )) return create(true);
        return;
      }
      setError(e.message);
    }
  };

  const filtered = useMemo(() => {
    if (!rows) return [];
    const s = q.trim().toLowerCase();
    return rows.filter((r) => {
      if (s && !r.item_id.toLowerCase().includes(s) && !(r.item_name || "").toLowerCase().includes(s)) return false;
      if (fMod !== "all" && r.module_code !== fMod) return false;
      if (fType !== "all" && r.item_type !== fType) return false;
      if (fCost === "costed" && !Object.keys(r.costs || {}).length) return false;
      if (fCost === "uncosted" && Object.keys(r.costs || {}).length) return false;
      return true;
    });
  }, [rows, q, fMod, fType, fCost]);

  if (error) return <div className="page"><p className="err">{error}</p></div>;
  if (!rows) return <div className="page"><p className="muted">Loading…</p></div>;

  const costed = rows.filter((r) => Object.keys(r.costs || {}).length).length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Master list · {rows.length} items · {costed} costed</div>
          <h1 className="page-title">Catalog</h1>
          <p className="page-sub">Every part &amp; assembly we've used, with its cost at each volume scenario. This is the naming authority for new BOMs.</p>
        </div>
        <div className="page-actions">
          <button className="btn" onClick={() => setAdding((a) => !a)}>+ New item</button>
        </div>
      </div>

      {adding && (
        <div className="card" style={{ marginBottom: 14, display: "flex", gap: 8, alignItems: "end", maxWidth: 600, padding: 14, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 200 }}><span className="input-label">Name (free text)</span><input className="input" value={nm} onChange={(e) => setNm(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} autoFocus placeholder="e.g. Custom bracket" /></div>
          <div><span className="input-label">Type</span><select className="select" value={ntype} onChange={(e) => setNtype(e.target.value)}><option value="part">part</option><option value="assembly">assembly</option></select></div>
          <div><span className="input-label">Module</span><select className="select" value={nmod} onChange={(e) => setNmod(e.target.value)}>{modules.map((m) => <option key={m} value={m}>{m}</option>)}</select></div>
          <button className="btn" onClick={() => create()}>create</button>
          <p style={{ width: "100%", fontSize: 11, color: "var(--ink-3)", margin: "2px 0 0" }}>
            {(nmod === "UN" || nmod === "UNP")
              ? <><strong>{nmod}</strong> = universal: it keeps the {nmod} code wherever it's used.</>
              : <><strong>{nmod}</strong>: stays {nmod} while used only in {nmod}; becomes <strong>UN</strong> if it ends up shared across systems.</>}
            {" "}Add new module names in <strong>Admin → Reference data → Modules</strong>.
          </p>
        </div>
      )}

      <div style={{ marginBottom: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search code or name…" style={{ flex: 1, minWidth: 220, maxWidth: 380 }} />
        <select className="select" value={fMod} onChange={(e) => setFMod(e.target.value)}>
          <option value="all">All modules</option>
          {modules.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select className="select" value={fType} onChange={(e) => setFType(e.target.value)}>
          <option value="all">All types</option><option value="part">parts</option><option value="assembly">assemblies</option>
        </select>
        <select className="select" value={fCost} onChange={(e) => setFCost(e.target.value)}>
          <option value="all">All</option><option value="costed">Costed</option><option value="uncosted">Uncosted</option>
        </select>
        <span style={{ fontSize: 11.5, color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>{filtered.length}/{rows.length}</span>
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: COLS, gap: 10, padding: "10px 16px", borderBottom: "1px solid var(--hair)", fontSize: 10.5, color: "var(--ink-3)", fontFamily: "var(--font-mono)", textTransform: "uppercase", letterSpacing: ".05em" }}>
          <span>code</span><span>name</span><span>module</span>
          {TIERS.map((t) => <span key={t} style={{ textAlign: "right" }}>€ @{tierLabel(t)}</span>)}
        </div>
        <div style={{ maxHeight: "64vh", overflowY: "auto" }}>
          {filtered.map((r) => (
            <div key={r.item_id} onClick={() => onOpenPart?.(r.item_id)} style={{ display: "grid", gridTemplateColumns: COLS, gap: 10, padding: "8px 16px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13, cursor: "pointer" }}>
              <span className="mono" style={{ fontSize: 12 }}>{r.item_id}{r.item_type === "assembly" ? <span style={{ color: "var(--ink-4)" }} title="assembly"> ◆</span> : null}</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.item_name}
                {!r.in_bom && <span style={{ color: "var(--ink-4)", fontFamily: "var(--font-mono)", fontSize: 10 }} title="in the catalog but not in any BOM yet"> · catalog</span>}
              </span>
              <span><ModulePill code={r.module_code} /></span>
              {TIERS.map((t) => <span key={t} style={{ textAlign: "right" }}><CostCell c={r.costs?.[t]} /></span>)}
            </div>
          ))}
          {filtered.length === 0 && <div style={{ padding: 18, color: "var(--ink-3)" }}>No items match “{q}”.</div>}
        </div>
      </div>
    </div>
  );
}
