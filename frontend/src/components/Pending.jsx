import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon, ModulePill, fmtWeight } from "./ui";

const FIELD_CHIPS = [
  { key: "any", label: "Any missing" },
  { key: "weight", label: "Weight" },
  { key: "material", label: "Material" },
  { key: "supplier_country", label: "Country" },
  { key: "cost", label: "Cost" },
];

export default function Pending({ onOpenPart, version }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [field, setField] = useState("any");
  const [moduleF, setModuleF] = useState("all");

  useEffect(() => {
    api.pending().then(setItems).catch((e) => setError(e.message));
  }, [version]);

  if (error) return <div className="page"><p className="err">{error}</p></div>;
  if (!items) return <div className="page"><p className="muted">Loading…</p></div>;

  const modules = [...new Set(items.map((i) => i.module_code).filter(Boolean))].sort();
  const counts = Object.fromEntries(
    FIELD_CHIPS.map((c) => [c.key, c.key === "any" ? items.length : items.filter((i) => i.missing.includes(c.key)).length])
  );
  const filtered = items.filter((i) => {
    if (moduleF !== "all" && i.module_code !== moduleF) return false;
    if (field !== "any" && !i.missing.includes(field)) return false;
    return true;
  });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Review queue</div>
          <h1 className="page-title">Pending items</h1>
          <p className="page-sub">Items still missing required data. Work through these in BOM-review meetings — click any row to fill it in.</p>
        </div>
        <div className="page-actions">
          <select className="select" value={moduleF} onChange={(e) => setModuleF(e.target.value)}>
            <option value="all">All modules</option>
            {modules.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        {FIELD_CHIPS.map((c) => (
          <button key={c.key} className="btn ghost sm"
            style={{ background: field === c.key ? "var(--ink)" : "transparent", color: field === c.key ? "var(--bg)" : "var(--ink)", borderColor: field === c.key ? "var(--ink)" : "var(--hair-strong)" }}
            onClick={() => setField(c.key)}>
            {c.label}<span style={{ marginLeft: 6, fontFamily: "var(--font-mono)", fontSize: 10.5, opacity: 0.75 }}>{counts[c.key]}</span>
          </button>
        ))}
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table className="tbl">
          <thead>
            <tr><th style={{ width: 100 }}>Part #</th><th>Name</th><th style={{ width: 70 }}>Module</th><th>Missing</th><th style={{ width: 80 }} className="num">Weight</th><th style={{ width: 120 }}>Material</th><th style={{ width: 28 }}></th></tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
              <tr key={p.item_id} onClick={() => onOpenPart(p.item_id)}>
                <td><span className="mono">{p.item_id}</span></td>
                <td>{p.item_name}</td>
                <td><ModulePill code={p.module_code} /></td>
                <td>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {p.missing.map((m) => (
                      <span key={m} style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, padding: "1px 6px", borderRadius: 2, background: "var(--accent-soft)", color: "var(--accent)" }}>{m}</span>
                    ))}
                  </div>
                </td>
                <td className="num">{p.weight_grams != null ? fmtWeight(p.weight_grams) : "—"}</td>
                <td style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: p.material ? "var(--ink)" : "var(--ink-4)" }}>{p.material || "—"}</td>
                <td><Icon name="chevR" size={14} /></td>
              </tr>
            ))}
            {filtered.length === 0 && <tr><td colSpan={7} style={{ padding: 20, textAlign: "center", color: "var(--ink-3)" }}>All clear in this view. 🎉</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
