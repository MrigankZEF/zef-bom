import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon, ModulePill, fmtWeight } from "./ui";
import { tierLabel } from "../tiers";

// The chips are DERIVED from what the queue actually contains, not declared here. A fixed list
// drifts from the backend's vocabulary in both directions: it showed a "Cost" chip that could
// never match (the API emits `cost@1`, never a bare `cost`, and the counter is an exact array
// membership test, so it read 0 on every database there has ever been), while `asm_time@*` —
// the single commonest gap — had no chip at all. Deriving them fixes the stale list, the dead
// chip and the chips that sat there reading 0, in one move.
//
// A tier-suffixed label groups to its stem, so `cost@1 / @100 / @10k` are one "Cost" chip
// rather than three. The row's own chips already say which tier, and the chip's tooltip lists
// the tiers it covers.
const groupOf = (m) => (m.includes("@") ? m.slice(0, m.indexOf("@")) : m);

const GROUP_LABELS = {
  weight: "Weight",
  material: "Material",
  supplier_country: "Country",
  cost: "Cost",
  asm_time: "Assembly time",
  quote: "Quote",
  cost_type: "Cost type",
};
// Display order for the groups we know about. Anything the backend starts emitting that is not
// in here still gets a chip — appended, labelled with its raw key — because a new kind of gap
// going unnoticed is the failure this whole change exists to stop.
const GROUP_ORDER = ["weight", "material", "supplier_country", "cost", "asm_time", "quote", "cost_type"];

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

  // One pass over the queue: how many ITEMS each group has (not how many labels — an item
  // missing all three tiers of a cost is one item with a cost gap), and which tiers appear.
  const groups = new Map();
  for (const i of items) {
    for (const g of new Set(i.missing.map(groupOf))) {
      const e = groups.get(g) || { key: g, count: 0, tiers: new Set() };
      e.count += 1;
      groups.set(g, e);
    }
    for (const m of i.missing) {
      if (m.includes("@")) groups.get(groupOf(m))?.tiers.add(m.slice(m.indexOf("@") + 1));
    }
  }
  const known = GROUP_ORDER.filter((k) => groups.has(k));
  const unknown = [...groups.keys()].filter((k) => !GROUP_ORDER.includes(k)).sort();
  const chips = [
    { key: "any", label: "Any missing", count: items.length },
    ...[...known, ...unknown].map((k) => ({
      key: k,
      label: GROUP_LABELS[k] || k,
      count: groups.get(k).count,
      tiers: [...groups.get(k).tiers],
    })),
    // A facility sub-item with nothing entered at a tier is the same kind of gap as a part
    // with no decided cost, and belongs in the same queue rather than being discoverable only
    // by opening every drawer. It comes from /cogs/pending, not /pending — `tree.py` stays
    // untouched by the ladder — so the two lists are merged here. Kept while the list is still
    // loading, so the chip does not appear a moment after the others.
    ...(facRows.length > 0 || facGaps === null
      ? [{ key: "facility", label: "Facilities", count: facRows.length }] : []),
  ];
  // A chip can vanish under the current selection — fix a gap and its group empties — so fall
  // back to "any" rather than showing an empty table with a dead chip selected.
  const activeField = chips.some((c) => c.key === field) ? field : "any";
  const filtered = items.filter((i) => {
    if (moduleF !== "all" && i.module_code !== moduleF) return false;
    if (activeField !== "any" && !i.missing.some((m) => groupOf(m) === activeField)) return false;
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
        {chips.map((c) => (
          <button key={c.key} className="btn ghost sm"
            title={c.tiers?.length ? `at ${c.tiers.join(", ")}` : undefined}
            style={{ background: activeField === c.key ? "var(--ink)" : "transparent", color: activeField === c.key ? "var(--bg)" : "var(--ink)", borderColor: activeField === c.key ? "var(--ink)" : "var(--hair-strong)" }}
            onClick={() => setField(c.key)}>
            {c.label}<span style={{ marginLeft: 6, fontFamily: "var(--font-mono)", fontSize: 10.5, opacity: 0.75 }}>{c.count}</span>
          </button>
        ))}
      </div>

      {activeField === "facility" ? (
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
