import { useEffect, useRef, useState } from "react";
import { squarify, colorFor } from "./Treemap.jsx";

// Hierarchical treemap for the Costing tab. Two layouts, chosen by `depth`:
//   depth = 1  → drill-down: one level at a time; click an assembly to zoom in.
//   depth > 1  → nested overview: N levels at once. Assemblies become *frames* — a neutral
//                box with a dark title bar (darker = higher up the tree) holding their children;
//                only real parts/leaves carry the cost/module colour. That keeps the structure
//                readable instead of a wall of same-coloured rectangles.
// The viewBox is sized to the real pixel width, so text stays a constant size at any width;
// label sizes adapt to each tile so big tiles get big, readable type.

const ASM = "#1C1B1A";
const COOL = [239, 227, 214];
const HOT = [184, 0, 31];
const FRAME_FILL = "rgba(28,27,26,0.05)";
const lerp = (a, b, t) => `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(",")})`;
const costColor = (v, maxV) => lerp(COOL, HOT, maxV > 0 ? Math.sqrt(Math.max(0, v) / maxV) : 0);

// Assembly frames: a title bar that gets lighter the deeper you go, so depth is readable at a glance.
const headH = (level) => (level === 0 ? 23 : level === 1 ? 20 : level === 2 ? 17 : 15);
const headFont = (level) => (level === 0 ? 14 : level === 1 ? 12 : level === 2 ? 10.8 : level === 3 ? 10 : 9.5);
// A child must never shout louder than the assembly holding it: a tile's type is capped at its
// own level's header size, so type shrinks as you go deeper — that IS the depth cue.
const leafCap = (level, nested) => (nested ? headFont(level) : 15);
const bandFill = (level) => `rgba(28,27,26,${Math.max(0.3, 0.8 - level * 0.17)})`;
const framePad = (level) => (level === 0 ? 4 : level === 1 ? 3 : 2);

// Label size scales with the tile — capped so it never dominates, floored so it stays legible.
const fitFont = (w, h, max) => Math.min(max, Math.max(9.5, Math.min(w / 7.2, h / 2.9)));
const clip = (s, w, fs) => {
  const max = Math.max(1, Math.floor((w - 10) / (fs * 0.56)));
  return !s ? "" : s.length > max ? s.slice(0, Math.max(1, max - 1)) + "…" : s;
};

function componentInstances(n) {
  return (n.children || []).reduce((s, c) => {
    const cc = c.children && c.children.length ? componentInstances(c) : 1;
    return s + (c.quantity || 1) * cc;
  }, 0);
}

export default function CostTreemap({ node, metric = "cost", colorMode = "cost", scenario = "likely",
  depth = 1, svgRef, format, onOpenPart }) {
  const [pathIds, setPathIds] = useState([]);
  const [hover, setHover] = useState(null);
  const [boxW, setBoxW] = useState(760);
  const rootRef = useRef(null);
  const wrapRef = useRef(null);
  useEffect(() => { setPathIds([]); }, [node?.item_id]);
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (w > 0) setBoxW(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  if (!node) return null;

  // viewBox = real pixels → constant text size at any width
  const width = Math.max(360, boxW);
  const nested = Math.max(1, depth) > 1;
  const height = Math.round(Math.max(nested ? 460 : 380, Math.min(width * (nested ? 0.62 : 0.55), 820)));

  const costOf = (c) => metric !== "cost" ? (c.rollup_weight_grams || 0)
    : scenario === "min" ? (c.rollup_cost_min ?? c.rollup_cost ?? 0)
    : scenario === "max" ? (c.rollup_cost_max ?? c.rollup_cost ?? 0)
    : (c.rollup_cost ?? 0);

  const chain = [node];
  let cur = node;
  for (const id of pathIds) {
    const nxt = (cur.children || []).find((c) => c.item_id === id);
    if (!nxt) break;
    chain.push(nxt); cur = nxt;
  }
  const focus = chain[chain.length - 1];
  const mult = chain.reduce((m, n) => m * (n.quantity || 1), 1);
  const bomTotal = costOf(node) || 0;

  const contrib = (c) => costOf(c) * (c.quantity || 1);
  const childItems = (parent) => {
    const items = (parent.children || []).map((c) => ({ node: c, value: contrib(c) })).filter((t) => t.value > 0);
    if (metric === "cost") {
      const own = costOf(parent) - (parent.children || []).reduce((s, c) => s + contrib(c), 0);
      // hide tiny assembly-work slivers when showing many levels at once (reduces clutter)
      const floor = nested ? Math.max(0.01, costOf(parent) * 0.03) : 0.005;
      if (own > floor) items.push({ isAsm: true, value: own });
    }
    return items;
  };

  const tiles = [];
  const maxLevel = Math.max(1, depth);
  if (maxLevel <= 1) {
    const items = childItems(focus);
    const levelTotal = items.reduce((s, t) => s + t.value, 0) || 1;
    for (const r of squarify(items, 0, 0, width, height)) {
      tiles.push({ node: r.node, isAsm: r.isAsm, x: r.x, y: r.y, w: r.w, h: r.h, level: 0,
        path: r.node ? [r.node.item_id] : null,
        abs: r.value * mult, pctLevel: (r.value / levelTotal) * 100 });
    }
  } else {
    const layout = (parent, x, y, w, h, level, absMult, ppath) => {
      const items = childItems(parent);
      if (!items.length) return;
      for (const r of squarify(items, x, y, w, h)) {
        const abs = r.value * absMult;
        if (r.isAsm) { tiles.push({ isAsm: true, x: r.x, y: r.y, w: r.w, h: r.h, level, abs }); continue; }
        const c = r.node;
        const cpath = [...ppath, c.item_id];
        const hh = headH(level), pad = framePad(level);
        const canNest = c.children && c.children.length && level + 1 < maxLevel
          && r.h > hh + 26 && r.w > 62 && childItems(c).length > 0;
        if (canNest) {
          tiles.push({ node: c, x: r.x, y: r.y, w: r.w, h: r.h, level, abs, path: cpath, container: true, headH: hh });
          layout(c, r.x + pad, r.y + hh, r.w - 2 * pad, r.h - hh - pad, level + 1, (c.quantity || 1) * absMult, cpath);
        } else {
          tiles.push({ node: c, x: r.x, y: r.y, w: r.w, h: r.h, level, abs, path: cpath, leaf: true });
        }
      }
    };
    layout(focus, 0, 0, width, height, 0, mult, []);
  }
  // Colour scale ignores the frames — otherwise the big assemblies wash out every real part.
  const maxAbs = Math.max(...tiles.filter((t) => !t.container).map((t) => t.abs), 0);
  const maxFrameLevel = Math.max(0, ...tiles.filter((t) => t.container).map((t) => t.level));
  const modules = [...new Set(tiles.filter((t) => !t.isAsm && !t.container && t.node?.module_code)
    .map((t) => t.node.module_code))].sort();
  const hasAsm = tiles.some((t) => t.isAsm);

  // Legend lives inside the SVG so it's always visible AND lands in the exported PNG.
  // Concrete colours only — CSS vars are swapped to white on export.
  const legendItems = [];
  if (colorMode === "module") {
    legendItems.push({ type: "title", text: "Modules" });
    for (const m of modules) legendItems.push({ type: "swatch", color: colorFor(m), text: m });
    if (!modules.length) legendItems.push({ type: "title", text: "— none tagged" });
  } else {
    legendItems.push({ type: "grad", text: metric === "cost" ? "cheaper → pricier" : "lighter → heavier" });
  }
  if (hasAsm) legendItems.push({ type: "swatch", color: ASM, text: "assembly work" });
  if (nested) {
    legendItems.push({ type: "title", text: "Levels" });
    for (let l = 0; l <= maxFrameLevel; l++) {
      legendItems.push({ type: "band", color: bandFill(l), text: l === 0 ? "top assemblies" : `level ${l + 1}` });
    }
    legendItems.push({ type: "title", text: "coloured tiles = parts" });
  }
  const LEG_FS = 10.5;
  const legW = (e) => (e.type === "grad" ? 60 : e.type === "band" ? 20 : e.type === "swatch" ? 13 : 0)
    + (e.type === "title" ? 0 : 5) + e.text.length * 5.9 + 14;
  const legRows = [];
  { let row = [], rw = 0;
    for (const e of legendItems) {
      const w = legW(e);
      if (rw + w > width - 4 && row.length) { legRows.push(row); row = []; rw = 0; }
      row.push(e); rw += w;
    }
    if (row.length) legRows.push(row); }
  const legendH = legRows.length * 17 + 10;
  const totalH = height + legendH;

  const onTile = (t) => {
    if (t.isAsm) return;
    if (t.node?.has_children) { setHover(null); setPathIds([...pathIds, ...(t.path || [t.node.item_id])]); }
    else onOpenPart?.(t.node?.item_id);
  };
  const onMove = (e, t) => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (r) setHover({ tile: t, x: e.clientX - r.left, y: e.clientY - r.top });
  };

  return (
    <div ref={rootRef}>
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

      {tiles.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--ink-3)" }}>No {metric === "cost" ? "costed" : "weighed"} items at this level yet.</div>
      ) : (
        <div ref={wrapRef} style={{ position: "relative" }} onMouseLeave={() => setHover(null)}>
          <svg ref={svgRef} viewBox={`0 0 ${width} ${totalH}`} style={{ width: "100%", height: "auto", display: "block", fontFamily: "var(--font-body)" }}>
            <rect x="0" y="0" width={width} height={totalH} fill="var(--bg, #fff)" />
            {tiles.map((t, i) => {
              const key = (t.node?.item_id || "asm") + "_" + i;
              const isHover = hover && hover.tile === t;

              if (t.container) {  // assembly frame: neutral body + dark title bar
                const fs = headFont(t.level);
                const money = format ? format(t.abs) : String(Math.round(t.abs));
                const showMoney = t.w > 170;
                return (
                  <g key={key} onClick={() => onTile(t)} onMouseMove={(e) => onMove(e, t)} style={{ cursor: "pointer" }}>
                    <rect x={t.x + 0.5} y={t.y + 0.5} width={Math.max(0, t.w - 1)} height={Math.max(0, t.h - 1)}
                      fill={FRAME_FILL} stroke={`rgba(28,27,26,${0.42 - t.level * 0.08})`}
                      strokeWidth={t.level === 0 ? 1.6 : 1.1} rx="3" />
                    <rect x={t.x + 0.5} y={t.y + 0.5} width={Math.max(0, t.w - 1)} height={t.headH - 1}
                      fill={bandFill(t.level)} rx="2" />
                    <text x={t.x + 6} y={t.y + t.headH * 0.7} fill="#fff" fontSize={fs} fontWeight="700"
                      letterSpacing="0.02em" style={{ pointerEvents: "none" }}>
                      {clip(t.node?.item_name, showMoney ? t.w - 62 : t.w, fs)}
                    </text>
                    {showMoney && (
                      <text x={t.x + t.w - 6} y={t.y + t.headH * 0.7} textAnchor="end" fill="#fff" opacity="0.82"
                        fontSize={fs - 1.5} fontFamily="var(--font-mono)" style={{ pointerEvents: "none" }}>{money}</text>
                    )}
                  </g>
                );
              }

              const label = t.isAsm ? "assembly work" : t.node?.item_name;
              const fill = t.isAsm ? ASM : colorMode === "module" ? colorFor(t.node?.module_code) : costColor(t.abs, maxAbs);
              const light = colorMode === "cost" && !t.isAsm ? (t.abs / (maxAbs || 1)) < 0.42 : false;
              const ink = light ? "#1C1B1A" : "#fff";
              const drill = t.node?.has_children;
              const fs = fitFont(t.w, t.h, leafCap(t.level, nested));
              const showLabel = t.w > 40 && t.h > 17;
              const showVal = showLabel && t.h > fs * 2.7 + 8;
              return (
                <g key={key} onClick={() => onTile(t)} onMouseMove={(e) => onMove(e, t)}
                  style={{ cursor: t.isAsm ? "default" : "pointer" }}>
                  <rect x={t.x + 0.4} y={t.y + 0.4} width={Math.max(0, t.w - 0.8)} height={Math.max(0, t.h - 0.8)}
                    fill={fill} stroke="var(--bg)" strokeWidth="1" rx="1.5"
                    opacity={hover && !isHover ? 0.9 : 1} />
                  {drill && t.w > 24 && t.h > 22 && (
                    <text x={t.x + t.w - 4} y={t.y + fs + 1} textAnchor="end" fill={ink} fontSize={Math.min(12, fs)} opacity="0.6" style={{ pointerEvents: "none" }}>⤢</text>
                  )}
                  {showLabel && (
                    <text x={t.x + 6} y={t.y + fs + 3} fill={ink} fontSize={fs} fontWeight="600" style={{ pointerEvents: "none" }}>
                      {clip(label, drill && t.w > 24 ? t.w - 14 : t.w, fs)}
                    </text>
                  )}
                  {showVal && (
                    <text x={t.x + 6} y={t.y + fs * 2.2 + 5} fill={ink} fontSize={Math.max(9.5, fs * 0.82)} opacity="0.85"
                      fontFamily="var(--font-mono)" style={{ pointerEvents: "none" }}>
                      {format ? format(t.abs) : t.abs}
                    </text>
                  )}
                </g>
              );
            })}

            {legRows.map((r, ri) => {
              let cx = 2;
              const y = height + 12 + ri * 17;
              return (
                <g key={"leg" + ri}>
                  {r.map((e, ei) => {
                    const x = cx; cx += legW(e);
                    if (e.type === "title") {
                      return <text key={ei} x={x} y={y} fontSize={LEG_FS} fontWeight="700" fill="#6B645C">{e.text}</text>;
                    }
                    const bw = e.type === "grad" ? 60 : e.type === "band" ? 20 : 13;
                    const bh = e.type === "grad" ? 9 : e.type === "band" ? 9 : 11;
                    return (
                      <g key={ei}>
                        {e.type === "grad"
                          ? <><defs><linearGradient id={`lg${ri}_${ei}`} x1="0" x2="1"><stop offset="0" stopColor="rgb(239,227,214)" /><stop offset="1" stopColor="rgb(184,0,31)" /></linearGradient></defs>
                              <rect x={x} y={y - bh + 1} width={bw} height={bh} rx="2" fill={`url(#lg${ri}_${ei})`} /></>
                          : <rect x={x} y={y - bh + 1} width={bw} height={bh} rx="2" fill={e.color} />}
                        <text x={x + bw + 5} y={y} fontSize={LEG_FS} fill="#57514B">{e.text}</text>
                      </g>
                    );
                  })}
                </g>
              );
            })}
          </svg>

          {hover && (
            <Tooltip tile={hover.tile} x={hover.x} y={hover.y}
              flip={hover.x > (wrapRef.current?.offsetWidth || 800) * 0.6}
              flipY={hover.y > (wrapRef.current?.offsetHeight || 400) - 180 && hover.y > 180}
              metric={metric} scenario={scenario} format={format} bomTotal={bomTotal} />
          )}
        </div>
      )}
    </div>
  );
}

function Tooltip({ tile, x, y, flip, flipY, metric, scenario, format, bomTotal }) {
  const fmt = (v) => (format ? format(v) : String(Math.round(v)));
  const abs = tile.abs;
  const pctBom = bomTotal > 0 ? (abs / bomTotal) * 100 : 0;
  const n = tile.node;
  const isCost = metric === "cost";
  const rows = [];
  if (tile.isAsm) {
    rows.push(["This assembly's own work", fmt(abs)]);
  } else {
    const qty = n?.quantity || 1;
    rows.push([n?.has_children ? "Assembly" : "Part", `${n?.module_code || ""}${qty > 1 ? ` · ×${qty}` : ""}`]);
    rows.push([`Rolled-up ${isCost ? (scenario === "likely" ? "cost" : scenario + " cost") : "weight"}`, fmt(abs)]);
    if (isCost && (n?.rollup_cost_min ?? 0) !== (n?.rollup_cost_max ?? 0)) {
      const unit = costFieldSafe(n, scenario);
      const scale = unit > 0 ? abs / unit : qty;
      rows.push(["Range (min–max)", `${fmt((n.rollup_cost_min || 0) * scale)} – ${fmt((n.rollup_cost_max || 0) * scale)}`]);
    }
    rows.push([tile.pctLevel != null ? "Share" : "Share of BOM",
      tile.pctLevel != null ? `${tile.pctLevel.toFixed(1)}% of level · ${pctBom.toFixed(1)}% of BOM` : `${pctBom.toFixed(1)}%`]);
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
        {!tile.isAsm && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>{n?.item_id}</span>}
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

function costFieldSafe(n, scenario) {
  return scenario === "min" ? (n.rollup_cost_min ?? n.rollup_cost)
    : scenario === "max" ? (n.rollup_cost_max ?? n.rollup_cost)
    : (n.rollup_cost ?? 0);
}
