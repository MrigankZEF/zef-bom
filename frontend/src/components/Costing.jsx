import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtEURcompact, fmtPct, fmtWeight } from "./ui";
import CostTreemap from "./CostTreemap.jsx";

const TIERS = [1, 100, 10000];
const tierLabel = (v) => (v >= 1000 ? `${v / 1000}k` : `${v}`);

export default function Costing({ onOpenPart }) {
  const [roots, setRoots] = useState(null);
  const [root, setRoot] = useState("");
  const [volume, setVolume] = useState(100);
  const [metric, setMetric] = useState("cost"); // cost | weight
  const [colorMode, setColorMode] = useState("cost"); // cost (heat) | module
  const [expanded, setExpanded] = useState(false);    // full-width treemap
  const [scenario, setScenario] = useState("likely"); // min | likely | max (cost only)
  const [depth, setDepth] = useState(1);              // 1 = drill-down; >1 = nested overview
  const [data, setData] = useState(null);
  const [tree, setTree] = useState(null);
  const [error, setError] = useState(null);
  const svgRef = useRef(null);

  useEffect(() => {
    api.listItems({ top_level_only: true }).then((rs) => {
      setRoots(rs);
      if (rs[0] && !root) setRoot(rs[0].item_id);
    }).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!root) return;
    setData(null); setTree(null);
    api.costingBreakdown(root, volume).then(setData).catch((e) => setError(e.message));
    api.tree(root, volume).then(setTree).catch((e) => setError(e.message));
  }, [root, volume]);

  if (error) return <div className="page"><p className="err">{error}</p></div>;
  if (!roots) return <div className="page"><p className="muted">Loading…</p></div>;
  if (roots.length === 0) return <div className="page"><p className="muted">No top-level BOMs yet — mark one via an upload, then come back.</p></div>;

  const fmt = metric === "cost" ? fmtEURcompact : fmtWeight;
  const exportPng = () => svgRef.current && exportSvgToPng(svgRef.current, `${(data?.root_name || root || "bom")}-cost-treemap.png`);
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

          <div className="row-2" style={{ display: "grid", gridTemplateColumns: expanded ? "1fr" : "1fr 2.3fr", gap: 16 }}>
            <div className="card" style={expanded ? { maxWidth: 520 } : undefined}>
              <div className="card-head"><span className="card-title">Cost vs volume</span><span className="card-meta">{data.root_name}</span></div>
              <VolumeChart tiers={data.tiers} selected={volume} />
              <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}>Line = most-likely · shaded = min–max range.</div>
            </div>

            <div className="card">
              <div className="card-head" style={{ alignItems: "center" }}>
                <span className="card-title">Cost breakdown</span>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
                  <div className="segmented-mini">
                    <button className={metric === "cost" ? "on" : ""} onClick={() => setMetric("cost")}>Cost</button>
                    <button className={metric === "weight" ? "on" : ""} onClick={() => setMetric("weight")}>Weight</button>
                  </div>
                  <div className="segmented-mini">
                    <button className={colorMode === "cost" ? "on" : ""} onClick={() => setColorMode("cost")}>Heat</button>
                    <button className={colorMode === "module" ? "on" : ""} onClick={() => setColorMode("module")}>Module</button>
                  </div>
                  {metric === "cost" && (
                    <div className="segmented-mini" title="Which cost estimate sizes the tiles">
                      {["min", "likely", "max"].map((s) => (
                        <button key={s} className={scenario === s ? "on" : ""} onClick={() => setScenario(s)}>
                          {s[0].toUpperCase() + s.slice(1)}
                        </button>
                      ))}
                    </div>
                  )}
                  <select className="select" value={depth}
                    style={{ height: 30, fontSize: 12, padding: "0 26px 0 9px",
                      backgroundPosition: "calc(100% - 14px) 13px, calc(100% - 10px) 13px" }}
                    onChange={(e) => setDepth(Number(e.target.value))} title="How many BOM levels to show at once">
                    <option value={1}>Drill-down</option>
                    <option value={2}>2 levels</option>
                    <option value={3}>3 levels</option>
                    <option value={9}>Overview</option>
                  </select>
                  <button className="btn ghost sm" onClick={() => setExpanded((v) => !v)}
                    title={expanded ? "Back to side-by-side" : "Expand the treemap to full width"}>
                    {expanded ? "⤡ Collapse" : "⤢ Expand"}
                  </button>
                  <button className="btn ghost sm" onClick={exportPng} title="Save the treemap as a PNG image">Save PNG</button>
                </div>
              </div>
              {tree
                ? <CostTreemap node={tree} metric={metric} colorMode={colorMode} scenario={scenario}
                    depth={depth} svgRef={svgRef} format={fmt} onOpenPart={onOpenPart} />
                : <p className="muted" style={{ padding: 20 }}>Loading structure…</p>}
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
                <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                  Tiles sized by rolled-up {metric === "cost" ? "cost" : "weight"}. Click an assembly (⤢) to drill in, a part to open it.
                  {metric === "weight" && topWeight && topWeight.weight_grams > 0 && <> Heaviest: <strong>{topWeight.item_name}</strong> ({fmtWeight(topWeight.weight_grams)}).</>}
                </span>
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

// Rasterise the treemap SVG to a PNG and download it (for decks/emails). CSS-var colours don't
// exist in an isolated SVG, so they're swapped to white; concrete rgb/hex colours are kept.
async function exportSvgToPng(svg, filename) {
  try {
    const vb = svg.viewBox?.baseVal;
    const w = Math.round((vb && vb.width) || svg.clientWidth || 820);
    const h = Math.round((vb && vb.height) || svg.clientHeight || 440);
    const clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", w);
    clone.setAttribute("height", h);
    clone.style.fontFamily = "Inter, Arial, Helvetica, sans-serif";
    clone.querySelectorAll("[stroke]").forEach((el) => { if ((el.getAttribute("stroke") || "").includes("var(")) el.setAttribute("stroke", "#ffffff"); });
    clone.querySelectorAll("[fill]").forEach((el) => { if ((el.getAttribute("fill") || "").includes("var(")) el.setAttribute("fill", "#ffffff"); });
    const str = new XMLSerializer().serializeToString(clone);
    const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(str);
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = () => rej(new Error("render failed")); img.src = url; });
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = w * scale; canvas.height = h * scale;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.drawImage(img, 0, 0, w, h);
    canvas.toBlob((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }, "image/png");
  } catch (e) {
    alert("Couldn't export the image: " + e.message);
  }
}
