import { useEffect, useRef, useState } from "react";
import { squarify, colorFor } from "./Treemap.jsx";

// Hierarchical, drill-down treemap for the Costing tab. Sized by rolled-up cost (or weight);
// click an assembly to zoom into its sub-tree, the breadcrumb to zoom back out, a leaf to open
// it. Colour by cost-intensity ("where's the money") or by module. A Min/Likely/Max scenario
// switches which rolled-up cost drives the sizing. A floating tooltip shows the detail.
//
// Data is one nested `node` from GET /tree (each carries rollup_cost[/min/max] per unit +
// quantity under its parent), so a child's contribution = cost × quantity, and a parent's total
// = Σ(children) + its own assembly work — the tiles add up at every level.

const ASM = "#1C1B1A";
const COOL = [239, 227, 214];
const HOT = [184, 0, 31];
const lerp = (a, b, t) => `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(",")})`;
const costColor = (v, maxV) => lerp(COOL, HOT, maxV > 0 ? Math.sqrt(Math.max(0, v) / maxV) : 0);

// Total leaf-part instances contained in a node (per one of it) — the "component count".
function componentInstances(n) {
  return (n.children || []).reduce((s, c) => {
    const cc = c.children && c.children.length ? componentInstances(c) : 1;
    return s + (c.quantity || 1) * cc;
  }, 0);
}

export default function CostTreemap({ node, metric = "cost", colorMode = "cost", scenario = "likely",
  format, onOpenPart, width = 820, height = 440 }) {
  const [pathIds, setPathIds] = useState([]);
  const [hover, setHover] = useState(null);   // { tile, x, y }
  const wrapRef = useRef(null);
  useEffect(() => { setPathIds([]); }, [node?.item_id]);

  if (!node) return null;

  // cost field for the chosen scenario (weight has no min/max — always the plain weight)
  const costOf = (c) => metric !== "cost" ? (c.rollup_weight_grams || 0)
    : scenario === "min" ? (c.rollup_cost_min ?? c.rollup_cost ?? 0)
    : scenario === "max" ? (c.rollup_cost_max ?? c.rollup_cost ?? 0)
    : (c.rollup_cost ?? 0);

  // resolve drill path
  const chain = [node];
  let cur = node;
  for (const id of pathIds) {
    const nxt = (cur.children || []).find((c) => c.item_id === id);
    if (!nxt) break;
    chain.push(nxt); cur = nxt;
  }
  const focus = chain[chain.length - 1];
  const mult = chain.reduce((m, n) => m * (n.quantity || 1), 1);   // qty from root to focus

  const contrib = (c) => costOf(c) * (c.quantity || 1);
  let tiles = (focus.children || [])
    .map((c) => ({ id: c.item_id, node: c, label: c.item_name, value: contrib(c) }))
    .filter((t) => t.value > 0);
  if (metric === "cost") {
    const own = costOf(focus) - (focus.children || []).reduce((s, c) => s + contrib(c), 0);
    if (own > 0.005) tiles.push({ id: "__asm__", label: "assembly work", value: own, isAsm: true });
  }
  const levelTotal = tiles.reduce((s, t) => s + t.value, 0) || 1;
  const maxV = Math.max(...tiles.map((t) => t.value), 0);
  const bomTotal = costOf(node) || 0;
  const rects = tiles.length ? squarify(tiles, 0, 0, width, height) : [];

  const onTile = (t) => {
    if (t.isAsm) return;
    if (t.node?.has_children) { setHover(null); setPathIds([...pathIds, t.id]); }
    else onOpenPart?.(t.id);
  };
  const onMove = (e, t) => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (r) setHover({ tile: t, x: e.clientX - r.left, y: e.clientY - r.top });
  };

  const cw = wrapRef.current?.offsetWidth || 800;
  const ch = wrapRef.current?.offsetHeight || 400;

  return (
    <div>
      {/* breadcrumb */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4, marginBottom: 8, fontSize: 12 }}>
        {chain.map((n, i) => (
          <span key={n.item_id} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            {i > 0 && <span style={{ color: "var(--ink-4)" }}>›</span>}
            <button onClick={() => setPathIds(pathIds.slice(0, i))} disabled={i === chain.length - 1}
              style={{ border: 0, background: "transparent", padding: "2px 4px", borderRadius: 4, fontSize: 12,
                cursor: i === chain.length - 1 ? "default" : "pointer",
                color: i === chain.length - 1 ? "var(--ink)" : "var(--accent)", fontWeight: i === chain.length - 1 ? 600 : 400 }}>
              {n.item_name}
            </button>
          </span>
        ))}
        {chain.length > 1 && <span style={{ marginLeft: 6, fontSize: 11, color: "var(--ink-3)" }}>· click a tile to drill in, a name above to go back</span>}
      </div>

      {rects.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--ink-3)" }}>No {metric === "cost" ? "costed" : "weighed"} items at this level yet.</div>
      ) : (
        <div ref={wrapRef} style={{ position: "relative" }} onMouseLeave={() => setHover(null)}>
          <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", display: "block", fontFamily: "var(--font-body)" }}>
            {rects.map((r) => {
              const abs = r.value * mult;
              const fill = r.isAsm ? ASM : colorMode === "module" ? colorFor(r.node?.module_code) : costColor(r.value, maxV);
              const light = colorMode === "cost" && !r.isAsm ? (r.value / (maxV || 1)) < 0.45 : false;
              const ink = light ? "#1C1B1A" : "#fff";
              const drill = r.node?.has_children;
              const showLabel = r.w > 50 && r.h > 24;
              const pctBom = bomTotal > 0 ? (abs / bomTotal) * 100 : 0;
              return (
                <g key={r.id} onClick={() => onTile(r)} onMouseMove={(e) => onMove(e, r)}
                  style={{ cursor: r.isAsm ? "default" : "pointer" }}>
                  <rect x={r.x + 0.5} y={r.y + 0.5} width={Math.max(0, r.w - 1)} height={Math.max(0, r.h - 1)}
                    fill={fill} stroke="var(--bg)" strokeWidth="1.5" rx="1.5"
                    opacity={hover && hover.tile.id !== r.id ? 0.82 : 1} />
                  {drill && r.w > 16 && r.h > 16 && (
                    <text x={r.x + r.w - 5} y={r.y + 13} textAnchor="end" fill={ink} fontSize="11" opacity="0.7" style={{ pointerEvents: "none" }}>⤢</text>
                  )}
                  {showLabel && (
                    <>
                      <text x={r.x + 6} y={r.y + 15} fill={ink} fontSize="10.5" fontWeight="600" style={{ pointerEvents: "none" }}>
                        {r.label.length > Math.floor(r.w / 6.2) ? r.label.slice(0, Math.max(1, Math.floor(r.w / 6.2) - 1)) + "…" : r.label}
                      </text>
                      {r.h > 38 && (
                        <text x={r.x + 6} y={r.y + 29} fill={ink} fontSize="10" opacity="0.85" fontFamily="var(--font-mono)" style={{ pointerEvents: "none" }}>
                          {format ? format(abs) : abs}{!r.isAsm && pctBom >= 1 ? `  ${pctBom.toFixed(0)}%` : ""}
                        </text>
                      )}
                    </>
                  )}
                </g>
              );
            })}
          </svg>

          {hover && (
            <Tooltip tile={hover.tile} x={hover.x} y={hover.y}
              flip={hover.x > cw * 0.6} flipY={hover.y > ch - 180 && hover.y > 180}
              mult={mult} metric={metric} scenario={scenario} format={format}
              levelTotal={levelTotal} bomTotal={bomTotal} />
          )}
        </div>
      )}
    </div>
  );
}

function Tooltip({ tile, x, y, flip, flipY, mult, metric, scenario, format, levelTotal, bomTotal }) {
  const fmt = (v) => (format ? format(v) : String(Math.round(v)));
  const abs = tile.value * mult;
  const pctLevel = (tile.value / levelTotal) * 100;
  const pctBom = bomTotal > 0 ? (abs / bomTotal) * 100 : 0;
  const n = tile.node;
  const isCost = metric === "cost";
  const rows = [];
  if (tile.isAsm) {
    rows.push(["This assembly's own work", fmt(abs)]);
    rows.push(["Share of this level", `${pctLevel.toFixed(1)}%`]);
  } else {
    const qty = n?.quantity || 1;
    rows.push([n?.has_children ? "Assembly" : "Part", `${n?.module_code || ""}${qty > 1 ? ` · ×${qty}` : ""}`]);
    rows.push([`Rolled-up ${isCost ? (scenario === "likely" ? "cost" : scenario + " cost") : "weight"}`, fmt(abs)]);
    if (isCost && (n?.rollup_cost_min ?? 0) !== (n?.rollup_cost_max ?? 0)) {
      rows.push(["Range (min–max)", `${fmt((n.rollup_cost_min || 0) * qty * mult)} – ${fmt((n.rollup_cost_max || 0) * qty * mult)}`]);
    }
    rows.push(["Share", `${pctLevel.toFixed(1)}% of level · ${pctBom.toFixed(1)}% of BOM`]);
    if (n?.has_children) rows.push(["Components", `${componentInstances(n).toLocaleString()} parts`]);
    if (isCost && n?.coverage != null && n.coverage < 1) rows.push(["⚠ Costed", `${Math.round(n.coverage * 100)}% — a floor`]);
  }
  return (
    <div style={{
      position: "absolute", top: flipY ? y - 14 : y + 14, left: flip ? x - 14 : x + 14,
      transform: `${flip ? "translateX(-100%)" : ""} ${flipY ? "translateY(-100%)" : ""}`.trim() || "none",
      pointerEvents: "none", zIndex: 20, maxWidth: 280,
      background: "var(--bg-raised, #fff)", border: "1px solid var(--hair-strong)", borderRadius: 8,
      boxShadow: "var(--shadow-2, 0 6px 24px rgba(0,0,0,0.14))", padding: "9px 11px", fontSize: 12,
    }}>
      <div style={{ fontWeight: 600, marginBottom: 5, display: "flex", gap: 6, alignItems: "baseline" }}>
        <span>{tile.isAsm ? "Assembly work" : n?.item_name}</span>
        {!tile.isAsm && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>{tile.id}</span>}
      </div>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 14, lineHeight: 1.5 }}>
          <span style={{ color: "var(--ink-3)" }}>{k}</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, textAlign: "right" }}>{v}</span>
        </div>
      ))}
      {!tile.isAsm && (
        <div style={{ marginTop: 5, paddingTop: 5, borderTop: "1px solid var(--hair-faint)", fontSize: 11, color: "var(--ink-3)" }}>
          {n?.has_children ? "click to drill in" : "click to open the part"}
        </div>
      )}
    </div>
  );
}
