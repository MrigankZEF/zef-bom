import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon, ModulePill, fmtWeight } from "./ui";
import { tierLabel } from "../tiers";

const FIELD_CHIPS = [
  { key: "any", label: "Any missing" },
  { key: "weight", label: "Weight" },
  { key: "material", label: "Material" },
  { key: "supplier_country", label: "Country" },
  { key: "cost", label: "Cost" },
  // A facility sub-item with nothing entered at a tier is the same kind of gap as a part
  // with no decided cost, and belongs in the same queue rather than being discoverable only
  // by opening every drawer. It comes from /cogs/pending, not /pending — `tree.py` stays
  // untouched by the ladder — so the two lists are merged here.
  { key: "facility", label: "Facilities" },
];

export default function Pending({ onOpenPart, onOpenFacilities, version }) {
  const [items, setItems] = useState(null);
  const [facGaps, setFacGaps] = useState(null);
  const [error, setError] = useState(null);
  const [field, setField] = useState("any");
  const [moduleF, setModuleF] = useState("all");

  useEffect(() => {
    api.pending().then(setItems).catch((e) => setError(e.message));
    // A missing facility figure is a gap in the COGS ladder, not in the BOM, so a failure
    // here must not blank the part queue — it degrades to "no facility gaps known".
    api.cogsPending().then(setFacGaps).catch(() => setFacGaps([]));
  }, [version]);

  if (error) return <div className="page"><p className="err">{error}</p></div>;
  if (!items) return <div className="page"><p className="muted">Loading…</p></div>;

  const modules = [...new Set(items.map((i) => i.module_code).filter(Boolean))].sort();
  // One row per facility sub-item, carrying the tiers it has gaps at — three rows per
  // sub-item would be the same gap said three times.
  const facRows = Object.values((facGaps || []).reduce((acc, g) => {
    const k = `${g.facility_id}:${g.item_id}`;
    acc[k] = acc[k] || { ...g, tiers: [], missingAll: new Set() };
    acc[k].tiers.push(g.volume_tier);
    g.missing.forEach((m) => acc[k].missingAll.add(m));
    return acc;
  }, {}));

  const counts = Object.fromEntries(
    FIELD_CHIPS.map((c) => [
      c.key,
      c.key === "any" ? items.length
        : c.key === "facility" ? facRows.length
        : items.filter((i) => i.missing.includes(c.key)).length,
    ])
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

      {field === "facility" ? (
        <FacilityGaps rows={facRows} onOpenFacilities={onOpenFacilities} loading={facGaps === null} />
      ) : (
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
      )}
    </div>
  );
}

// Facility gaps, in the same voice as the part queue above. Not items — they have no part
// number, no module and no weight — so they get their own columns rather than being forced
// into the parts table with four empty cells.
function FacilityGaps({ rows, onOpenFacilities, loading }) {
  if (loading) return <p className="muted">Loading facility gaps…</p>;
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 110 }}>Facility</th>
            <th style={{ width: 110 }}>Sub-item</th>
            <th>Name</th>
            <th style={{ width: 110 }}>Tiers</th>
            <th>Nothing entered for</th>
            <th style={{ width: 28 }}></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.facility_id}:${r.item_id}`} onClick={() => onOpenFacilities && onOpenFacilities()}
                title="Open Facilities">
              <td><span className="mono">{r.facility_code}</span></td>
              <td><span className="mono">{r.item_code}</span></td>
              <td>{r.item_name}</td>
              <td>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
                  {r.tiers.sort((a, b) => a - b).map(tierLabel).join(" · ")}
                </span>
              </td>
              <td>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {[...r.missingAll].map((m) => (
                    <span key={m} style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, padding: "1px 6px", borderRadius: 2, background: "var(--accent-soft)", color: "var(--accent)" }}>{m}</span>
                  ))}
                </div>
              </td>
              <td><Icon name="chevR" size={14} /></td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={6} style={{ padding: 20, textAlign: "center", color: "var(--ink-3)" }}>
              Every facility sub-item has figures at all three tiers. 🎉
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
