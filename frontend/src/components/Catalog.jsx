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
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [nm, setNm] = useState("");
  const [ntype, setNtype] = useState("part");

  useEffect(() => { setRows(null); api.catalog().then(setRows).catch((e) => setError(e.message)); }, [version]);

  const create = async () => {
    if (!nm.trim()) return;
    try {
      const r = await api.createCatalogItem({ item_name: nm.trim(), item_type: ntype });
      setNm(""); setAdding(false);
      api.catalog().then(setRows);
      onOpenPart?.(r.item_id);
    } catch (e) { setError(e.message); }
  };

  const filtered = useMemo(() => {
    if (!rows) return [];
    const s = q.trim().toLowerCase();
    if (!s) return rows;
    return rows.filter((r) => r.item_id.toLowerCase().includes(s) || (r.item_name || "").toLowerCase().includes(s));
  }, [rows, q]);

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
          <button className="btn" onClick={create}>create</button>
          <p style={{ width: "100%", fontSize: 11, color: "var(--ink-3)", margin: "2px 0 0" }}>Gets a placeholder <strong>UN</strong> code. When you add it into a BOM, it re-codes to that system (or stays UN if used across systems).</p>
        </div>
      )}

      <div style={{ marginBottom: 14, maxWidth: 380 }}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search code or name…" />
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
