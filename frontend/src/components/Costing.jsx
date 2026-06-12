import { useEffect, useState } from "react";
import { api } from "../api";
import { fmtEURcompact, fmtPct, fmtWeight } from "./ui";
import Treemap, { colorFor } from "./Treemap.jsx";

const TIERS = [1, 100, 10000];
const tierLabel = (v) => (v >= 1000 ? `${v / 1000}k` : `${v}`);

export default function Costing({ onOpenPart }) {
  const [roots, setRoots] = useState(null);
  const [root, setRoot] = useState("");
  const [volume, setVolume] = useState(100);
  const [metric, setMetric] = useState("cost"); // cost | weight
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listItems({ top_level_only: true }).then((rs) => {
      setRoots(rs);
      if (rs[0] && !root) setRoot(rs[0].item_id);
    }).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!root) return;
    setData(null);
    api.costingBreakdown(root, volume).then(setData).catch((e) => setError(e.message));
  }, [root, volume]);

  if (error) return <div className="page"><p className="err">{error}</p></div>;
  if (!roots) return <div className="page"><p className="muted">Loading…</p></div>;
  if (roots.length === 0) return <div className="page"><p className="muted">No top-level BOMs yet — mark one via an upload, then come back.</p></div>;

  const fmt = metric === "cost" ? fmtEURcompact : fmtWeight;
  const tmItems = [
    ...(data?.parts || [])
      .map((p) => ({ id: p.item_id, label: p.item_name, value: metric === "cost" ? p.cost : p.weight_grams, colorKey: p.module_code }))
      .filter((i) => i.value > 0),
    ...(metric === "cost"
      ? (data?.assemblies || [])
          .filter((a) => a.cost > 0)
          .map((a) => ({ id: a.item_id, label: `${a.item_name} · assembly cost`, value: a.cost, color: "#1C1B1A", colorKey: "__asm__" }))
      : []),
  ];
  const legend = (() => {
    const seen = new Map();
    (data?.parts || []).forEach((p) => {
      const v = metric === "cost" ? p.cost : p.weight_grams;
      if (p.module_code && v > 0 && !seen.has(p.module_code)) seen.set(p.module_code, colorFor(p.module_code));
    });
    const arr = [...seen.entries()].map(([label, color]) => ({ label, color }));
    if (metric === "cost" && (data?.assemblies || []).some((a) => a.cost > 0)) arr.push({ label: "Assembly cost", color: "#1C1B1A" });
    return arr;
  })();
  const topPart = data?.parts?.[0];
  const sortedByWeight = [...(data?.parts || [])].sort((a, b) => b.weight_grams - a.weight_grams);
  const topWeight = sortedByWeight[0];
  const ct = data?.totals;
  const costRange = ct && ct.cost_min != null && (ct.cost_min < ct.cost || ct.cost_max > ct.cost)
    ? `${fmtEURcompact(ct.cost_min)}–${fmtEURcompact(ct.cost_max)}` : null;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Rollup · per top-level BOM</div>
          <h1 className="page-title">Costing</h1>
          <p className="page-sub">Pick a BOM and a production scenario. The treemaps show where the cost and the weight sit — handy for client conversations.</p>
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, marginBottom: 18, alignItems: "center", flexWrap: "wrap" }}>
        <select className="select" value={root} onChange={(e) => setRoot(e.target.value)} style={{ minWidth: 240 }}>
          {roots.map((r) => <option key={r.item_id} value={r.item_id}>{r.item_id} — {r.item_name}</option>)}
        </select>
        <div style={{ display: "flex", gap: 6 }}>
          {TIERS.map((v) => (
            <button key={v} className={`btn ${volume === v ? "" : "ghost"} sm`} onClick={() => setVolume(v)} style={{ minWidth: 60 }}>
              {tierLabel(v)} {v === 1 ? "unit" : "units"}
            </button>
          ))}
        </div>
      </div>

      {!data ? <p className="muted">Loading {root}…</p> : (
        <>
          <div className="kpi-grid">
            <div className="kpi accent">
              <span className="kpi-label">Unit cost — {data.root_name}</span>
              <span className="kpi-val">{data.totals.cost > 0 ? fmtEURcompact(data.totals.cost) : "—"}</span>
              <span className="kpi-sub">{ct?.assembly_cost > 0
                ? `parts ${fmtEURcompact(ct.parts_cost)} + assembly ${fmtEURcompact(ct.assembly_cost)}`
                : (costRange ? `range ${costRange}` : `@ ${volume.toLocaleString()} units`)}</span>
            </div>
            <div className="kpi">
              <span className="kpi-label">Total weight</span>
              <span className="kpi-val">{fmtWeight(data.totals.weight_grams)}</span>
              <span className="kpi-sub">rolled up</span>
            </div>
            <div className="kpi">
              <span className="kpi-label">Coverage</span>
              <span className="kpi-val">{fmtPct(data.totals.coverage)}</span>
              <span className="kpi-sub">{data.totals.covered}/{data.totals.total} parts costed</span>
            </div>
            <div className="kpi">
              <span className="kpi-label">Most expensive</span>
              <span className="kpi-val" style={{ fontSize: 18 }}>{topPart && topPart.cost > 0 ? fmtEURcompact(topPart.cost) : "—"}</span>
              <span className="kpi-sub">{topPart && topPart.cost > 0 ? topPart.item_name : "no costs yet"}</span>
            </div>
          </div>

          <div className="row-2" style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 16 }}>
            <div className="card">
              <div className="card-head"><span className="card-title">Cost vs volume</span><span className="card-meta">{data.root_name}</span></div>
              <VolumeChart tiers={data.tiers} selected={volume} />
              <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}>Line = most-likely · shaded = min–max range.</div>
            </div>

            <div className="card">
              <div className="card-head" style={{ alignItems: "center" }}>
                <span className="card-title">Breakdown treemap</span>
                <div className="segmented-mini">
                  <button className={metric === "cost" ? "on" : ""} onClick={() => setMetric("cost")}>Cost</button>
                  <button className={metric === "weight" ? "on" : ""} onClick={() => setMetric("weight")}>Weight</button>
                </div>
              </div>
              <Treemap items={tmItems} format={fmt} onSelect={onOpenPart} />
              {legend.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 10 }}>
                  {legend.map((l) => (
                    <span key={l.label} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--ink-3)" }}>
                      <span style={{ width: 11, height: 11, borderRadius: 2, background: l.color, display: "inline-block" }} /> {l.label}
                    </span>
                  ))}
                </div>
              )}
              <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--ink-3)" }}>
                Each tile is a part (sized by {metric === "cost" ? "total cost" : "total weight"}), coloured by module{metric === "cost" ? "; the dark tile is assembly cost" : ""}. Click a part to open it.
                {metric === "weight" && topWeight && topWeight.weight_grams > 0 && <> Heaviest: <strong>{topWeight.item_name}</strong> ({fmtWeight(topWeight.weight_grams)}).</>}
              </div>
            </div>
          </div>

          {data.totals.total - data.totals.covered > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <span style={{ fontSize: 13 }}><strong>{data.totals.total - data.totals.covered}</strong> parts in this BOM have no decided cost yet — the cost treemap and total are a floor until they're filled in (see <strong>Pending</strong>).</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function VolumeChart({ tiers, selected }) {
  const w = 320, h = 170, pad = { l: 50, r: 14, t: 16, b: 28 };
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  const max = Math.max(1, ...tiers.map((t) => t.total_max ?? t.total));
  const xs = tiers.map((_, i) => pad.l + (tiers.length === 1 ? iw / 2 : (i / (tiers.length - 1)) * iw));
  const y = (val) => pad.t + ih - (val / max) * ih;
  const ys = tiers.map((t) => y(t.total));
  const path = tiers.map((_, i) => `${i === 0 ? "M" : "L"} ${xs[i]} ${ys[i]}`).join(" ");
  const hasBand = tiers.some((t) => (t.total_max ?? t.total) > t.total || (t.total_min ?? t.total) < t.total);
  let band = tiers.map((t, i) => `${i === 0 ? "M" : "L"} ${xs[i]} ${y(t.total_max ?? t.total)}`).join(" ");
  for (let i = tiers.length - 1; i >= 0; i--) band += ` L ${xs[i]} ${y(tiers[i].total_min ?? tiers[i].total)}`;
  band += " Z";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {[0, 0.5, 1].map((g) => {
        const yy = pad.t + ih - g * ih;
        return <g key={g}><line x1={pad.l} x2={w - pad.r} y1={yy} y2={yy} stroke="var(--hair)" /><text x={pad.l - 8} y={yy + 3} textAnchor="end" fontFamily="var(--font-mono)" fontSize="9.5" fill="var(--ink-3)">€{((max * g) / 1000).toFixed(1)}k</text></g>;
      })}
      {hasBand && <path d={band} fill="var(--accent)" opacity="0.13" stroke="none" />}
      <path d={path} fill="none" stroke="var(--ink)" strokeWidth="1.5" />
      {tiers.map((t, i) => (
        <g key={t.volume}>
          <circle cx={xs[i]} cy={ys[i]} r={t.volume === selected ? 5 : 3} fill={t.volume === selected ? "var(--accent)" : "var(--bg)"} stroke={t.volume === selected ? "var(--accent)" : "var(--ink)"} strokeWidth="1.5" />
          <text x={xs[i]} y={h - 9} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="9.5" fill="var(--ink-3)">{t.volume >= 1000 ? `${t.volume / 1000}k` : t.volume}</text>
        </g>
      ))}
    </svg>
  );
}
