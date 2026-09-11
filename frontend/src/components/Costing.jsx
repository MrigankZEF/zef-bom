import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { BreakdownList, Icon, fmtEURcompact, fmtPct, fmtWeight } from "./ui";
import CostTreemap from "./CostTreemap.jsx";

import { TIERS, tierLabel } from "../tiers";
import { spreadPath, spreadX } from "../spread";

// Costing keeps its own tier CONTROL — it is never on screen with Browse, so two controls
// are not the confusion this fixes; two independent values were. The value lives in App,
// so switching Browse -> Costing lands on the tier you were already looking at.
export default function Costing({ onOpenPart, tier, setTier }) {
  const [roots, setRoots] = useState(null);
  const [root, setRoot] = useState("");
  const volume = tier, setVolume = setTier;   // local names, shared value
  const [metric, setMetric] = useState("cost"); // cost | weight
  // Branch by default: the treemap's AREA already says what a thing costs, so colour is
  // better spent on which subsystem a block belongs to than on saying the price twice.
  const [colorMode, setColorMode] = useState("branch"); // branch | cost (heat) | module
  const [expanded, setExpanded] = useState(false);    // full-width treemap
  const [scenario, setScenario] = useState("likely"); // min | likely | max (cost only)
  const [depth, setDepth] = useState(1);              // 1 = drill-down; >1 = nested overview
  const [split, setSplit] = useState(true);           // draw a ×26 part as 26 numbered tiles, not one
  const [data, setData] = useState(null);
  const [tree, setTree] = useState(null);
  // BOM+ is this tab exactly as it was, plus the direct-cost rungs. COGM and COGS are the
  // ladder, cumulative — each shows every rung up to its own and no further.
  const [view, setView] = useState("bom");         // bom | cogm | cogs
  const [ladder, setLadder] = useState(null);
  const [lines, setLines] = useState(null);        // the breakdown, per layer
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
    setLadder(null); setLines(null);
    api.costingBreakdown(root, volume).then(setData).catch((e) => setError(e.message));
    api.tree(root, volume).then(setTree).catch((e) => setError(e.message));
    // The ladder is fetched for every view, not just the COGS ones: BOM+ shows the direct
    // rungs, and switching view should never be a loading state.
    api.cogsLadder(root, volume).then(setLadder).catch((e) => setError(e.message));
    api.cogsBreakdown(volume).then((b) => setLines(b.lines)).catch((e) => setError(e.message));
  }, [root, volume]);

  if (error) return <div className="page"><p className="err">{error}</p></div>;
  if (!roots) return <div className="page"><p className="muted">Loading…</p></div>;
  if (roots.length === 0) return <div className="page"><p className="muted">No top-level BOMs yet — mark one via an upload, then come back.</p></div>;

  const fmt = metric === "cost" ? fmtEURcompact : fmtWeight;
  const exportPng = () => svgRef.current && exportSvgToPng(svgRef.current, `${(data?.root_name || root || "bom")}-cost-treemap.png`);
  const sortedByWeight = [...(data?.parts || [])].sort((a, b) => b.weight_grams - a.weight_grams);
  const topWeight = sortedByWeight[0];
  const ct = data?.totals;
  // The headline follows the Min/Likely/Max switch. Sizing the tiles by the max estimate
  // while the big number kept showing the likely one meant the total on screen didn't match
  // the picture beside it, and there was no way to read the max total at all.
  const scenarioCost = (t) => !t ? null
    : scenario === "min" ? t.cost_min : scenario === "max" ? t.cost_max : t.cost;
  const shownCost = scenarioCost(ct);
  // Density, so the unit is fixed at kg however light the BOM is — `fmtWeight` flips to grams
  // under 1 kg, and a tile whose unit moves cannot be compared between two BOMs. Null rather
  // than Infinity when nothing has been weighed.
  const kg = (ct?.weight_grams || 0) / 1000;
  const eurPerKg = kg > 0 && shownCost > 0 ? shownCost / kg : null;
  // Every unweighed part drags the real €/kg down, so the figure on screen is a ceiling, not
  // an estimate. Saying which it is costs one clause.
  const unweighed = ct?.weight_missing?.length || 0;
  // Drives the whole tile's layout, so it is worked out once here rather than three times in
  // the markup. Without a range there is nothing to spread and the tile stays as it was.
  const hasSpread = !!(ct && ct.cost_min > 0 && ct.cost_max > ct.cost_min);

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
        <span style={{ flex: 1 }} />
        {/* Cumulative: COGM is COGS minus the post-manufacturing rung, and BOM+ is the BOM
            plus the direct rung. Each view shows every rung up to its own. */}
        <div className="seg" role="group" aria-label="Cost view">
          <button aria-pressed={view === "bom"} onClick={() => setView("bom")}
                  title="The BOM as it rolls up, plus the direct-cost rungs on top of it">BOM+</button>
          <button aria-pressed={view === "cogm"} onClick={() => setView("cogm")}
                  title="Fully burdened manufacturing cost — direct plus this tier's share of the overhead pool">COGM</button>
          <button aria-pressed={view === "cogs"} onClick={() => setView("cogs")}
                  title="Cost of goods sold per plant — COGM plus freight, install and the warranty accrual">COGS</button>
        </div>
      </div>

      {view !== "bom" && (
        <LadderView view={view} ladder={ladder} lines={lines} volume={volume}
                    rootName={data?.root_name || root} />
      )}

      {view === "bom" && !data ? <p className="muted">Loading {root}…</p> : view === "bom" && (
        <>
          <div className={`kpi-grid${hasSpread ? " with-spread" : ""}`}>
            <div className={`kpi accent${hasSpread ? " spread" : ""}`}>
              <span className="kpi-label">
                Unit cost — {data.root_name}{scenario !== "likely" && <> · <strong>{scenario}</strong></>}
              </span>
              {hasSpread ? (
                <CostSpread min={ct.cost_min} likely={ct.cost} max={ct.cost_max}
                            at={shownCost} scenario={scenario} fmt={fmtEURcompact} />
              ) : (
                <>
                  <span className="kpi-val">{shownCost > 0 ? fmtEURcompact(shownCost) : "—"}</span>
                  <CostSpread min={ct?.cost_min} likely={ct?.cost} max={ct?.cost_max}
                              at={shownCost} scenario={scenario} />
                </>
              )}
              <span className="kpi-sub">{ct?.assembly_cost > 0
                ? `parts ${fmtEURcompact(ct.parts_cost)} + assembly ${fmtEURcompact(ct.assembly_cost)}`
                : `@ ${volume.toLocaleString()} units`}</span>
            </div>
            {/* The other three share the second column, in a grid of their own — see
                .kpi-rest. That is what lets the unit-cost tile line up with the card below. */}
            <div className="kpi-rest">
            <div className="kpi">
              <span className="kpi-label">Total weight</span>
              <span className="kpi-val">{fmtWeight(data.totals.weight_grams)}</span>
              {/* Weight coverage sits on the tile whose number it qualifies, not with the cost
                  coverage — a rolled-up weight over half-weighed parts is as wrong as an
                  unpriced BOM, and it is what € / kg divides by. */}
              <span className="kpi-sub">{data.totals.weight_total
                ? `${data.totals.weight_covered}/${data.totals.weight_total} parts weighed${
                    unweighed > 0 ? ` · ${unweighed} missing` : ""}`
                : "rolled up"}</span>
            </div>
            <div className="kpi">
              <span className="kpi-label">Coverage</span>
              <span className="kpi-val">{fmtPct(data.totals.coverage)}</span>
              <span className="kpi-sub">{data.totals.covered}/{data.totals.total} inputs priced{
                data.totals.parts_total != null && <> · {data.totals.parts_covered}/{data.totals.parts_total} parts</>}</span>
            </div>
            {/* Was "Most expensive", which restated the first row of the breakdown below and
                the biggest block in the treemap beside it. € / kg is the one figure on this
                screen that neither of those shows. */}
            <div className="kpi">
              <span className="kpi-label">Cost per kg{scenario !== "likely" && <> · <strong>{scenario}</strong></>}</span>
              <span className="kpi-val">{eurPerKg != null ? `€ ${eurPerKg.toLocaleString(undefined, { maximumFractionDigits: eurPerKg < 100 ? 1 : 0 })}` : "—"}</span>
              <span className="kpi-sub">{eurPerKg == null
                ? "no weight rolled up"
                : `over ${fmtWeight(data.totals.weight_grams)}${unweighed > 0 ? ` · ${unweighed} parts unweighed, so a ceiling` : ""}`}</span>
            </div>
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
                    <button className={colorMode === "branch" ? "on" : ""} onClick={() => setColorMode("branch")}
                            title="One colour per top-level branch, kept all the way down">Branch</button>
                    <button className={colorMode === "cost" ? "on" : ""} onClick={() => setColorMode("cost")}
                            title="Cost intensity — a single red ramp">Heat</button>
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
                  <div className="segmented-mini" title="Draw a part used ×26 as one tile, or as 26 individual tiles">
                    <button className={!split ? "on" : ""} onClick={() => setSplit(false)}>Grouped</button>
                    <button className={split ? "on" : ""} onClick={() => setSplit(true)}>Each</button>
                  </div>
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
                    depth={depth} split={split} svgRef={svgRef} format={fmt} onOpenPart={onOpenPart} />
                : <p className="muted" style={{ padding: 20 }}>Loading structure…</p>}
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
                <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                  Tiles sized by rolled-up {metric === "cost" ? "cost" : "weight"}, biggest first. Click an assembly (⤢) to drill in, a part to open it.
                  {split && " Each shows every unit separately (up to 48 per part)."}
                  {metric === "weight" && topWeight && topWeight.weight_grams > 0 && <> Heaviest: <strong>{topWeight.item_name}</strong> ({fmtWeight(topWeight.weight_grams)}).</>}
                </span>
              </div>
            </div>
          </div>

          {ladder && <DirectRungs ladder={ladder} lines={lines} volume={volume} />}

          {data.totals.total - data.totals.covered > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <span style={{ fontSize: 13 }}>
                <strong>{data.totals.total - data.totals.covered}</strong> inputs in this BOM aren't priced yet — the cost treemap and total are a floor until they're filled in (see <strong>Pending</strong>).
                {data.totals.missing_assembly?.length > 0 && <>
                  {" "}That includes <strong>{data.totals.missing_assembly.length}</strong> assembl{data.totals.missing_assembly.length === 1 ? "y" : "ies"} with
                  no assembly cost at all: <span className="mono" style={{ fontSize: 12 }}>{data.totals.missing_assembly.join(", ")}</span>.
                </>}
              </span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// The three-point estimate, drawn.
//
// The three figures are placed WHERE THE CURVE PUTS THEM, not spaced evenly: min at the left
// edge, max at the right, and the most-likely at its true position across the range. On the
// real BOM that is 41% — so the likely figure sits left of centre and the tile says, without a
// word, that the downside is tighter than the upside. Evenly spaced figures would have thrown
// that away, and a figure printed on top of the curve (the first attempt) just collided with it.
//
// See src/spread.js for the curve itself and why it is a shape rather than three numbers.
function CostSpread({ min, likely, max, at, scenario, fmt }) {
  // Compact form, for the tile that has no range to show: a tick and a caption.
  if (!fmt) {
    const W = 132, H = 26;
    if (!(min > 0)) return null;
    return (
      <svg width={W} height={H} style={{ display: "block", marginTop: 2 }} aria-label="single-point estimate">
        <line x1={W / 2} y1="3" x2={W / 2} y2={H - 1} stroke="var(--accent)" strokeWidth="2" />
        <line x1="0" y1={H - 1} x2={W} y2={H - 1} stroke="var(--hair)" strokeWidth="1" />
        <text x={W / 2 + 6} y={H - 5} fontSize="9.5" fill="var(--ink-4)">one estimate</text>
      </svg>
    );
  }

  // A viewBox rather than a pixel width, so the curve stretches to whatever the tile is —
  // which is now the width of the Cost vs volume card below it. preserveAspectRatio="none"
  // because the horizontal axis is the range and the vertical is decoration: stretching it
  // sideways costs nothing, and letting it letterbox would leave the range short of the box.
  // A viewBox rather than a pixel width, so the curve stretches to whatever the tile is —
  // which is the width of the Cost vs volume card below it. preserveAspectRatio="none" because
  // the horizontal axis is the range and the vertical is decoration: stretching it sideways
  // costs nothing, and letterboxing would leave the range short of the box.
  const VB = 320, CURVE_H = 32, PAD = 2;
  // Drawn PAD short of the top and pushed down by it, so the peak's own stroke is not clipped
  // by the edge of the viewBox — which read as the distribution having its top sliced off.
  const path = spreadPath(min, likely, max, VB, CURVE_H - PAD);
  if (path === null) return null;
  const x = spreadX(at ?? likely, min, max) * VB;
  // MIN and MAX are set small and in FRONT of their figures rather than under them: the figures
  // are the content and want one line each, and a word beneath every one turned three numbers
  // into six things to read.
  const Tag = ({ children }) => (
    <span style={{ fontFamily: "var(--font-body)", fontSize: 9, letterSpacing: "0.1em",
                   color: "var(--ink-3)", marginRight: 5, verticalAlign: "0.55em" }}>{children}</span>
  );
  // Evenly spaced, and all three at one size. Positioning the middle figure over the curve's
  // actual peak was truer — it showed the lean — but at this width it crowded whichever end it
  // leaned towards. The lean is still on screen: the tick below sits at the real value.
  return (
    <div style={{ position: "relative", width: "100%", marginTop: 2 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10 }}>
        <span style={{ whiteSpace: "nowrap" }}>
          <Tag>MIN</Tag><span className="kpi-val" style={{ fontSize: 23 }}>{fmt(min)}</span>
        </span>
        <span style={{ whiteSpace: "nowrap" }}>
          <span className="kpi-val" style={{ fontSize: 23 }}>{fmt(at ?? likely)}</span>
        </span>
        <span style={{ whiteSpace: "nowrap" }}>
          <Tag>MAX</Tag><span className="kpi-val" style={{ fontSize: 23 }}>{fmt(max)}</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${VB} ${CURVE_H}`} preserveAspectRatio="none"
           width="100%" height={CURVE_H} style={{ display: "block", marginTop: 6 }}
           aria-label={`cost range ${min} to ${max}, most likely ${likely}`}>
        <path d={path} transform={`translate(0 ${PAD})`} fill="var(--accent-soft)"
              stroke="var(--accent)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <line x1="0" y1={CURVE_H - 0.5} x2={VB} y2={CURVE_H - 0.5} stroke="var(--hair-strong)"
              strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {/* Where the figure above actually falls in the range — the one place the asymmetry
            still shows now that the numbers are evenly spaced. */}
        <line x1={x} y1={PAD} x2={x} y2={CURVE_H} stroke="var(--accent)"
              strokeWidth={scenario === "likely" ? 1.5 : 2}
              strokeDasharray={scenario === "likely" ? "" : "2 2"}
              vectorEffect="non-scaling-stroke" />
      </svg>
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

// Rasterise the treemap SVG to a PNG and download it (for decks/emails). The treemap paints in
// concrete ZEF token values, so the export matches the screen; the only var()s left are the font
// stacks, which don't resolve in an isolated SVG and are swapped for concrete equivalents.
const PAGE = "#FFFFFF";  // --bg; the export sits on the same white ground as the app

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
    const FONTS = {
      "var(--font-display)": "'Space Grotesk', 'Helvetica Neue', sans-serif",
      "var(--font-mono)": "'JetBrains Mono', ui-monospace, monospace",
      "var(--font-body)": "Inter, 'Helvetica Neue', sans-serif",
    };
    clone.querySelectorAll("[font-family]").forEach((el) => {
      const f = FONTS[el.getAttribute("font-family")];
      if (f) el.setAttribute("font-family", f);
    });
    clone.querySelectorAll("[stroke]").forEach((el) => { if ((el.getAttribute("stroke") || "").includes("var(")) el.setAttribute("stroke", PAGE); });
    clone.querySelectorAll("[fill]").forEach((el) => { if ((el.getAttribute("fill") || "").includes("var(")) el.setAttribute("fill", PAGE); });
    const str = new XMLSerializer().serializeToString(clone);
    const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(str);
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = () => rej(new Error("render failed")); img.src = url; });
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = w * scale; canvas.height = h * scale;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = PAGE; ctx.fillRect(0, 0, canvas.width, canvas.height);
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

// ── the COGS ladder, read-only ────────────────────────────────────────────────
// Read-only on purpose: every figure here is either from the BOM tool or entered once on
// Facilities. Nothing on this screen computes anything — `ladder` arrives with every rung
// and every intermediate already on it, and these components render fields.

const layerLines = (lines, layer) => (lines || []).filter((l) => l.layer === layer);

// The three assumptions a reader cannot infer and the numbers depend on. Served with the
// ladder rather than written here, so they cannot drift from the arithmetic that needs them.
function LadderNotes({ ladder }) {
  return (
    <ul className="micro" style={{ color: "var(--ink-3)", margin: "10px 0 0", paddingLeft: 18 }}>
      {(ladder.notes || []).map((n) => <li key={n} style={{ marginBottom: 2 }}>{n}</li>)}
    </ul>
  );
}

function FloorNote({ ladder }) {
  const c = ladder.coverage;
  if (!c?.is_floor) return null;
  const bits = [];
  if (c.missing.length) bits.push(`${c.missing.length} part${c.missing.length === 1 ? "" : "s"} with no decided cost`);
  if (c.missing_assembly.length) bits.push(`${c.missing_assembly.length} assembl${c.missing_assembly.length === 1 ? "y" : "ies"} with no time or rate`);
  if (c.missing_quote.length) bits.push(`${c.missing_quote.length} bought-in assembl${c.missing_quote.length === 1 ? "y" : "ies"} with no quote`);
  return (
    <div className="banner warn" style={{ marginBottom: 16 }}>
      <Icon name="alert" size={15} className="ico" />
      <div>
        <h4>This is a floor, not a price</h4>
        <p>
          {bits.join(", ")} — so every rung above the BOM is understated.{" "}
          {/* Named explicitly, because the KPI on BOM+ reads 100%: that one counts leaf
              parts, this one counts assemblies too, and two bare percentages a click apart
              would look like a contradiction rather than two different questions. */}
          Coverage across parts <em>and</em> assemblies is {fmtPct(c.coverage)}; fill the
          gaps in <strong>Pending</strong>.
          {c.below_boundary.length > 0 && ` ${c.below_boundary.length} more items sit under a bought-in assembly and are deliberately not costed.`}
        </p>
      </div>
    </div>
  );
}

// The rungs BOM+ adds under the treemap: what the BOM becomes once scrap and the direct
// facility costs are on it. Stops before overhead — that is COGM's rung.
function DirectRungs({ ladder, lines, volume }) {
  const wide = { gridTemplateColumns: "minmax(0,1fr) 120px" };
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <span className="card-title">1 · Direct manufacturing cost</span>
        <span className="card-meta">per plant @ {volume.toLocaleString()}</span>
      </div>
      <div className="derived" style={wide}>
        <span>BOM, rolled up — material {fmtEURcompact(ladder.bom)} + our labour {fmtEURcompact(ladder.labour)}</span>
        <span>{fmtEURcompact(ladder.bom_raw)}</span>
      </div>
      <div className="derived" style={wide}>
        <span>
          Grossed for scrap — {ladder.scrap_pct.toFixed(3)}% loss, applied to the whole BOM
          cost because a scrapped part loses the hours already in it
        </span>
        <span>{fmtEURcompact(ladder.bom_adj)}</span>
      </div>
      <div className="derived" style={wide}>
        <span>Direct facility cost — consumables, metered utilities, tooling amortisation</span>
        <span>{fmtEURcompact(ladder.other)}</span>
      </div>
      <div className="derived total" style={wide}>
        <span>Direct manufacturing cost</span>
        <span>{fmtEURcompact(ladder.direct)}</span>
      </div>
      <div style={{ marginTop: 10 }}>
        <BreakdownList lines={layerLines(lines, "direct")} fmt={fmtEURcompact}
                       total={ladder.other} totalLabel="Entered on Facilities"
                       empty="No direct facility costs entered yet — the BOM is the whole direct cost." />
      </div>
      <p className="micro" style={{ color: "var(--ink-4)", marginTop: 8 }}>
        Switch to <strong>COGM</strong> to add this tier&apos;s share of the overhead pool.
      </p>
    </div>
  );
}

function LadderView({ view, ladder, lines, volume, rootName }) {
  if (!ladder) return <p className="muted">Loading the ladder…</p>;
  const showPost = view === "cogs";
  const headline = showPost ? ladder.cogs_unit : ladder.burdened;
  const narrow = { gridTemplateColumns: "minmax(0,1fr) 110px" };

  return (
    <>
      <FloorNote ladder={ladder} />

      <div className="kpi-grid">
        <div className="kpi accent">
          <span className="kpi-label">{showPost ? "COGS per plant" : "COGM per plant"} — {rootName}</span>
          <span className="kpi-val">{fmtEURcompact(headline)}</span>
          <span className="kpi-sub">@ {volume.toLocaleString()} plants/yr</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Direct</span>
          <span className="kpi-val">{fmtEURcompact(ladder.direct)}</span>
          <span className="kpi-sub">BOM + scrap + consumables</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Overhead / plant</span>
          <span className="kpi-val">{fmtEURcompact(ladder.overhead)}</span>
          <span className="kpi-sub">
            {fmtEURcompact(ladder.pool_total)} pool ÷ {volume.toLocaleString()}
          </span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Overhead share</span>
          <span className="kpi-val">{fmtPct(ladder.overhead_share, 1)}</span>
          <span className="kpi-sub">of {showPost ? "COGS" : "COGM"}</span>
        </div>
      </div>

      <Waterfall ladder={ladder} showPost={showPost} />

      <div style={{ display: "grid", gridTemplateColumns: showPost ? "1fr 1fr 1fr" : "1fr 1fr", gap: 16, marginTop: 16 }}>
        <div className="card">
          <div className="card-head">
            <span className="card-title">1 · Direct manufacturing cost</span>
            <span className="card-meta">per plant</span>
          </div>
          <div className="derived" style={narrow}>
            <span>BOM, grossed for {ladder.scrap_pct.toFixed(3)}% scrap</span>
            <span>{fmtEURcompact(ladder.bom_adj)}</span>
          </div>
          <BreakdownList lines={layerLines(lines, "direct")} fmt={fmtEURcompact}
                         total={ladder.direct} totalLabel="Direct"
                         empty="The BOM is the whole direct cost — nothing entered on Facilities." />
          <p className="micro" style={{ color: "var(--ink-3)", marginTop: 8 }}>
            The BOM contributes {fmtEURcompact(ladder.bom)} of material and{" "}
            {fmtEURcompact(ladder.labour)} of our own assembly labour
            {ladder.assembly_minutes > 0 && `, ${Math.round(ladder.assembly_minutes)} minutes a plant`}.
          </p>
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">2 · Manufacturing overhead</span>
            <span className="card-meta">pool ÷ {volume.toLocaleString()}</span>
          </div>
          <BreakdownList lines={layerLines(lines, "overhead")} fmt={fmtEURcompact}
                         total={ladder.pool_total} totalLabel="Pool, full year"
                         empty="No overhead entered on Facilities yet." />
          <div className="derived total" style={{ ...narrow, marginTop: 6 }}>
            <span>Per plant</span>
            <span>{fmtEURcompact(ladder.overhead)}</span>
          </div>
          <p className="micro" style={{ color: "var(--ink-3)", marginTop: 8 }}>
            {fmtPct(ladder.overhead_share, 1)} of the {showPost ? "COGS" : "COGM"} figure.
            {volume === 1 && " At one plant a year, that plant absorbs a whole year of it — which is what this column means, not what a prototype costs."}
          </p>
        </div>

        {showPost && (
          <div className="card">
            <div className="card-head">
              <span className="card-title">3 · Post-manufacturing</span>
              <span className="card-meta">produced = sold</span>
            </div>
            <BreakdownList lines={layerLines(lines, "post")} fmt={fmtEURcompact}
                           total={ladder.post} totalLabel="Freight + install"
                           empty="Nothing entered for after the plant leaves the hall." />
            <div className="derived" style={{ ...narrow, marginTop: 6 }}>
              <span>Warranty accrual — {ladder.warranty_pct.toFixed(2)}% of burdened</span>
              <span>{fmtEURcompact(ladder.warranty)}</span>
            </div>
            <div className="derived total" style={narrow}>
              <span>COGS per plant</span>
              <span>{fmtEURcompact(ladder.cogs_unit)}</span>
            </div>
            <p className="micro" style={{ color: "var(--ink-3)", marginTop: 8 }}>
              Warranty is a percentage of the burdened cost, so it moves whenever the overhead
              pool does.
            </p>
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-head"><span className="card-title">What these numbers assume</span></div>
        <LadderNotes ladder={ladder} />
        <p className="micro" style={{ color: "var(--ink-3)", marginTop: 10 }}>
          The BOM comes from the BOM tool — every other cost is entered on{" "}
          <strong>Facilities</strong>.
        </p>
      </div>
    </>
  );
}

// A stacked bar rather than a true waterfall: the rungs are cumulative and all positive, so
// stacking them shows both each rung's size and the total in one read. Widths are a share of
// the headline figure — the only division on this screen, and it is a layout measurement
// rather than a cost.
function Waterfall({ ladder, showPost }) {
  const total = showPost ? ladder.cogs_unit : ladder.burdened;
  if (!total) return null;
  const segs = [
    { k: "bom", label: "BOM + scrap", v: ladder.bom_adj, c: "var(--data-1)" },
    { k: "dir", label: "Direct facility", v: ladder.other, c: "var(--warm)" },
    { k: "oh", label: "Overhead / plant", v: ladder.overhead, c: "var(--info)" },
    ...(showPost ? [
      { k: "post", label: "Freight + install", v: ladder.post, c: "var(--warn)" },
      { k: "warr", label: "Warranty", v: ladder.warranty, c: "var(--accent)" },
    ] : []),
  ].filter((s) => s.v > 0);

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <span className="card-title">{showPost ? "COGS" : "COGM"} per plant, by rung</span>
        <span className="card-meta mono">{fmtEURcompact(total)}</span>
      </div>
      <div style={{ display: "flex", height: 26, borderRadius: 3, overflow: "hidden", border: "1px solid var(--hair)" }}>
        {segs.map((s) => (
          <div key={s.k} style={{ width: `${(s.v / total) * 100}%`, background: s.c }}
               title={`${s.label} — ${fmtEURcompact(s.v)} (${((s.v / total) * 100).toFixed(1)}%)`} />
        ))}
      </div>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 8 }}>
        {segs.map((s) => (
          <span key={s.k} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, color: "var(--ink-2)" }}>
            <span style={{ width: 9, height: 9, background: s.c, borderRadius: 2, display: "inline-block" }} />
            {s.label}
            <span className="mono" style={{ color: "var(--ink-3)" }}>{fmtEURcompact(s.v)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
