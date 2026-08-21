import { useEffect, useRef, useState } from "react";
import { squarify, colorAt } from "./Treemap.jsx";

// Hierarchical treemap for the Costing tab. Two layouts, chosen by `depth`:
//   depth = 1  → drill-down: one level at a time; click an assembly to zoom in.
//   depth > 1  → nested overview: N levels at once. An assembly is drawn as a card — white
//                body, rounded corners, a soft shadow and a coloured title bar — so the
//                nesting reads as stacked cards rather than boxes drawn inside boxes.
// Parts are solid; an assembly's own process cost is drawn as a hatched tile, so material vs
// assembly cost differs by pattern as well as colour.
// Heat mode is strictly one red ramp — module colours appear only in Module mode.
// The viewBox is sized to the real pixel width, so text stays a constant size at any width.

// ── ZEF design tokens, inlined ────────────────────────────────────────────────────────
// The system is explicit: warm bone surfaces, charcoal ink, hairlines, ONE red, and
// "no drop shadows". Concrete values (not var()) so the exported PNG matches the screen.
const BG = "#F5F2EE";          // --bg
const INK = "#1C1B1A";         // --ink
const INK_3 = "#6B6862";       // --ink-3
const HAIR = "rgba(28,27,26,0.12)";        // --hair
const HAIR_STRONG = "rgba(28,27,26,0.22)"; // --hair-strong
const NO_MODULE = "#8C857A";   // assembly with no module tag — warm grey

// Cost intensity: bone → ochre → ZEF red. A single-hue beige-to-red ramp goes muddy in the
// middle, so it passes through --warn ochre; every stop is a system colour.
const HEAT = [[237, 233, 226], [201, 138, 43], [184, 0, 31]];
const heatRgb = (v, maxV) => {
  const t = maxV > 0 ? Math.sqrt(Math.max(0, v) / maxV) : 0;
  const k = t * (HEAT.length - 1);
  const i = Math.min(HEAT.length - 2, Math.floor(k)), f = k - i;
  return HEAT[i].map((c, j) => c + (HEAT[i + 1][j] - c) * f);
};

const hex2rgb = (h) => { const m = String(h).replace("#", ""); return [0, 2, 4].map((i) => parseInt(m.slice(i, i + 2), 16)); };
const css = (a) => `rgb(${a.map((v) => Math.round(v)).join(",")})`;
const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
const lum = (rgb) => (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255;
const inkOn = (rgb) => (lum(rgb) > 0.6 ? INK : "#ffffff");

// An assembly is a miniature of the app's own .card: bone body, hairline border, uppercase
// display title over a hairline rule. Depth is carried by a wash of the assembly's colour
// and by the title size — never by a raised bevel or a filled title bar.
const headH = (level) => (level === 0 ? 22 : level === 1 ? 19 : level === 2 ? 16.5 : 15);
const headFont = (level) => (level === 0 ? 11.5 : level === 1 ? 10.4 : level === 2 ? 9.4 : 8.8);
const leafCap = (level, nested) => (nested ? headFont(level) + 2 : 15);
// Module mode leans on the wash to say WHICH subsystem a card is; Heat mode keeps it faint so
// the red ramp on the parts stays the only thing carrying data.
const cardWash = (level, byModule) =>
  (byModule ? 0.84 : 0.9) + Math.min(0.08, level * 0.025);   // toward bone as you nest
const cardLine = (level) => (level === 0 ? HAIR_STRONG : HAIR);
const cardR = (level) => (level === 0 ? 5 : level === 1 ? 4 : 3);
const framePad = (level) => (level === 0 ? 3 : level === 1 ? 2.5 : 2);
const INSET = 0.75;  // hairline gutter between neighbouring cards

const fitFont = (w, h, max) => Math.min(max, Math.max(8, Math.min(w / 6.4, h / 2.4)));
const clip = (s, w, fs, cw = 0.54) => {
  const max = Math.max(1, Math.floor((w - 6) / (fs * cw)));
  return !s ? "" : s.length > max ? s.slice(0, Math.max(1, max - 1)) + "…" : s;
};

// One naming rule for the whole component: a split instance is numbered ("Cell 3"),
// anything else is the plain item name. Every label site goes through this.
const tileLabel = (t) => {
  const name = t?.isAsm ? "assembly work" : t?.node?.item_name || "";
  return t?.instances ? `${name} ${t.instance}` : name;
};

// Equal-value instances are packed into a grid rather than scattered by the squarify rows —
// identical parts stay adjacent AND come out near-square instead of as thin slivers.
function gridCells(x, y, w, h, n) {
  let best = null;
  for (let cols = 1; cols <= n; cols++) {
    if (n % cols) continue;
    const score = Math.abs(Math.log((w / cols) / (h / (n / cols))));
    if (!best || score < best.score) best = { cols, rows: n / cols, score };
  }
  if (best && best.score <= Math.log(2.2)) {   // an exact grid — every cell the same area
    const cw = w / best.cols, ch = h / best.rows;
    return Array.from({ length: n }, (_, i) => ({
      x: x + (i % best.cols) * cw, y: y + Math.floor(i / best.cols) * ch, w: cw, h: ch,
    }));
  }
  const cols = Math.max(1, Math.round(Math.sqrt((n * w) / h)));  // prime counts: stretch the last row
  const rows = Math.ceil(n / cols), ch = h / rows, out = [];
  for (let r = 0; r < rows; r++) {
    const count = Math.min(cols, n - r * cols), cw = w / count;
    for (let c = 0; c < count; c++) out.push({ x: x + c * cw, y: y + r * ch, w: cw, h: ch });
  }
  return out;
}

// Every module in the BOM, in a stable order. Colours are handed out by position in this
// list — so they're always distinct, and a module keeps its colour as you drill in and out.
function collectModules(n, acc = new Set()) {
  if (n?.module_code) acc.add(n.module_code);
  for (const c of n?.children || []) collectModules(c, acc);
  return acc;
}

function componentInstances(n) {
  return (n.children || []).reduce((s, c) => {
    const cc = c.children && c.children.length ? componentInstances(c) : 1;
    return s + (c.quantity || 1) * cc;
  }, 0);
}

// A part used ×26 can be drawn as one ×26 tile or as 26 individual tiles (what the Plotly
// tool did). Splitting is capped — beyond this many instances the tiles are unreadable slivers.
const SPLIT_MAX = 48;

export default function CostTreemap({ node, metric = "cost", colorMode = "cost", scenario = "likely",
  depth = 1, split = true, svgRef, format, onOpenPart }) {
  // Each step is {id, single, inst}: `single` marks a drill into ONE instance of a ×N part,
  // so the view that opens sums to the tile you actually clicked, not the whole ×N group.
  const [path, setPath] = useState([]);
  const [hover, setHover] = useState(null);
  const [boxW, setBoxW] = useState(760);
  const rootRef = useRef(null);
  const wrapRef = useRef(null);
  useEffect(() => { setPath([]); }, [node?.item_id]);
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

  const moduleOrder = [...collectModules(node)].sort();
  const moduleColor = (code) => (code && moduleOrder.includes(code)
    ? colorAt(moduleOrder.indexOf(code)) : NO_MODULE);

  const width = Math.max(360, boxW);   // viewBox = real pixels → constant text size at any width
  const nested = Math.max(1, depth) > 1;
  const height = Math.round(Math.max(nested ? 460 : 380, Math.min(width * (nested ? 0.62 : 0.55), 820)));

  const costOf = (c) => metric !== "cost" ? (c.rollup_weight_grams || 0)
    : scenario === "min" ? (c.rollup_cost_min ?? c.rollup_cost ?? 0)
    : scenario === "max" ? (c.rollup_cost_max ?? c.rollup_cost ?? 0)
    : (c.rollup_cost ?? 0);

  // The assembly's own process cost, taken straight from the API. Deriving it as
  // parent − Σ(children × qty) drifts by cents once every term is rounded to 2dp.
  const ownCost = (n) => {
    if (metric !== "cost") return 0;
    const v = scenario === "min" ? n.assembly_cost_min
      : scenario === "max" ? n.assembly_cost_max : n.assembly_cost;
    if (v != null) return v;
    return Math.max(0, costOf(n) - (n.children || []).reduce((s, c) => s + costOf(c) * (c.quantity || 1), 0));
  };

  const chain = [node];
  const steps = [];
  let mult = 1, cur = node;
  for (const step of path) {
    const nxt = (cur.children || []).find((c) => c.item_id === step.id);
    if (!nxt) break;
    chain.push(nxt); steps.push(step);
    mult *= step.single && split ? 1 : nxt.quantity || 1;
    cur = nxt;
  }
  const focus = chain[chain.length - 1];
  const bomTotal = costOf(node) || 0;

  const contrib = (c) => costOf(c) * (c.quantity || 1);
  const splittable = (c) => {
    const q = c.quantity || 1;
    return split && Number.isInteger(q) && q >= 2 && q <= SPLIT_MAX;
  };
  const childItems = (parent) => {
    const items = [];
    for (const c of parent.children || []) {
      if (contrib(c) > 0) items.push({ node: c, value: contrib(c), splitInto: splittable(c) ? c.quantity : 0 });
    }
    if (metric === "cost") {
      const own = ownCost(parent);
      // hide tiny assembly-work slivers when showing many levels at once (reduces clutter)
      const floor = nested ? Math.max(0.01, costOf(parent) * 0.03) : 0.005;
      if (own > floor) items.push({ isAsm: true, value: own, module: parent.module_code });
    }
    return items;
  };

  const tiles = [];
  const maxLevel = Math.max(1, depth);

  const emit = (r, c, instance, instances, level, absMult, ppath, levelTotal) => {
    const unitMult = instance ? 1 : c.quantity || 1;
    const scale = unitMult * absMult;
    const step = { id: c.item_id, single: !!instance, inst: instance || null };
    const base = {
      node: c, x: r.x, y: r.y, w: r.w, h: r.h, level, path: [...ppath, step],
      instance, instances, abs: costOf(c) * scale, own: ownCost(c) * scale,
      pctLevel: levelTotal ? ((costOf(c) * unitMult) / levelTotal) * 100 : null,
    };
    const hh = headH(level), pad = framePad(level);
    const canNest = maxLevel > 1 && c.children?.length && level + 1 < maxLevel
      && r.h > hh + 28 && r.w > 64 && childItems(c).length > 0;
    if (canNest) {
      tiles.push({ ...base, container: true, headH: hh });
      layout(c, r.x + pad, r.y + hh + 1, r.w - 2 * pad, r.h - hh - pad - 1, level + 1, scale, base.path);
    } else {
      tiles.push({ ...base, leaf: true });
    }
  };

  function layout(parent, x, y, w, h, level, absMult, ppath) {
    const items = childItems(parent);
    if (!items.length) return;
    const levelTotal = maxLevel <= 1 ? items.reduce((s, t) => s + t.value, 0) || 1 : null;
    for (const r of squarify(items, x, y, w, h)) {
      if (r.isAsm) {
        tiles.push({ isAsm: true, module: r.module, x: r.x, y: r.y, w: r.w, h: r.h, level,
          abs: r.value * absMult, pctLevel: levelTotal ? (r.value / levelTotal) * 100 : null });
        continue;
      }
      if (r.splitInto > 1) {
        gridCells(r.x, r.y, r.w, r.h, r.splitInto).forEach((cell, i) =>
          emit(cell, r.node, i + 1, r.splitInto, level, absMult, ppath, levelTotal));
      } else {
        emit(r, r.node, null, null, level, absMult, ppath, levelTotal);
      }
    }
  }
  layout(focus, 0, 0, width, height, 0, mult, []);

  // Colour scale ignores the frames — otherwise the big assemblies wash out every real part.
  const maxAbs = Math.max(...tiles.filter((t) => !t.container).map((t) => t.abs), 0);
  const modules = [...new Set(tiles.filter((t) => !t.isAsm && t.node?.module_code).map((t) => t.node.module_code))].sort();
  const hasAsm = tiles.some((t) => t.isAsm);
  const byModule = colorMode === "module";

  // Heat mode is one red ramp end to end: frames get a red that pales with depth (structure,
  // not data), parts get the true cost heat. Module colours never appear here.
  // In Heat mode the accent belongs to the parts, so a card's marker is a calm ink-grey and
  // depth alone separates the levels; Module mode gives each card its subsystem colour.
  const frameRgb = (t) => byModule
    ? hex2rgb(moduleColor(t.node?.module_code))
    : mix(hex2rgb(INK), hex2rgb(BG), Math.min(0.55, 0.12 + t.level * 0.14));
  const asmRgb = (t) => (byModule ? hex2rgb(moduleColor(t.module)) : heatRgb(t.abs, maxAbs));

  const hatches = [];
  const hatchId = (rgb) => {
    const key = css(rgb);
    let i = hatches.indexOf(key);
    if (i < 0) { hatches.push(key); i = hatches.length - 1; }
    return "hx" + i;
  };
  for (const t of tiles) if (t.isAsm) hatchId(asmRgb(t));
  const legendHatch = byModule ? hex2rgb(NO_MODULE) : HEAT[HEAT.length - 1];
  const legendHatchId = hatchId(legendHatch);

  // Legend lives inside the SVG so it's always visible AND lands in the exported PNG.
  const legendItems = [];
  if (byModule) {
    legendItems.push({ type: "title", text: "Modules" });
    for (const m of modules) legendItems.push({ type: "swatch", color: moduleColor(m), text: m });
    if (!modules.length) legendItems.push({ type: "title", text: "— none tagged" });
  } else {
    legendItems.push({ type: "grad", text: metric === "cost" ? "cheaper → pricier" : "lighter → heavier" });
  }
  if (hasAsm) legendItems.push({ type: "hatch", id: legendHatchId, text: "assembly work (hatched)" });
  if (nested) legendItems.push({ type: "title", text: byModule
    ? "Cards = assemblies, coloured by module · paler = deeper · solid tiles = parts"
    : "Cards = assemblies · paler = deeper · solid tiles = parts" });
  const LEG_FS = 10.5;
  const legW = (e) => (e.type === "grad" ? 60 : e.type === "title" ? 0 : 13) + (e.type === "title" ? 0 : 5)
    + e.text.length * 5.9 + 14;
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
    if (t.node?.has_children) { setHover(null); setPath([...steps, ...(t.path || [])]); }
    else onOpenPart?.(t.node?.item_id);
  };
  const onMove = (e, t) => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (r) setHover({ tile: t, x: e.clientX - r.left, y: e.clientY - r.top });
  };
  const crumb = (n, i) => {
    const st = steps[i - 1];
    return i > 0 && st?.inst && split ? `${n.item_name} ${st.inst}` : n.item_name;
  };

  return (
    <div ref={rootRef}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4, marginBottom: 8, fontSize: 12 }}>
        {chain.map((n, i) => (
          <span key={n.item_id + i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            {i > 0 && <span style={{ color: "var(--ink-4)" }}>›</span>}
            <button onClick={() => setPath(steps.slice(0, i))} disabled={i === chain.length - 1}
              style={{ border: 0, background: "transparent", padding: "2px 4px", borderRadius: 4, fontSize: 12,
                cursor: i === chain.length - 1 ? "default" : "pointer",
                color: i === chain.length - 1 ? "var(--ink)" : "var(--accent)", fontWeight: i === chain.length - 1 ? 600 : 400 }}>
              {crumb(n, i)}
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
            <defs>
              {hatches.map((c, i) => (
                <pattern key={c} id={"hx" + i} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="7" height="7" fill={c} />
                  <line x1="0" y1="0" x2="0" y2="7" strokeWidth="2.6"
                    stroke={inkOn(c.match(/\d+/g).map(Number)) === INK ? INK : "#ffffff"}
                    opacity={inkOn(c.match(/\d+/g).map(Number)) === INK ? 0.22 : 0.5} />
                </pattern>
              ))}
              <linearGradient id="heatKey" x1="0" x2="1">
                {HEAT.map((c, i) => <stop key={i} offset={i / (HEAT.length - 1)} stopColor={css(c)} />)}
              </linearGradient>
            </defs>
            <rect x="0" y="0" width={width} height={totalH} fill={BG} />

            {tiles.map((t, i) => {
              const key = (t.node?.item_id || "asm") + "_" + i;
              const isHover = hover && hover.tile === t;

              if (t.container) {   // assembly card: white body, soft shadow, coloured title bar
                const rgb = frameRgb(t);
                const x = t.x + INSET, y = t.y + INSET, w = Math.max(0, t.w - INSET * 2), h = Math.max(0, t.h - INSET * 2);
                const fs = headFont(t.level);
                const money = format ? format(t.abs) : String(Math.round(t.abs));
                const showMoney = t.w > 170;
                return (
                  <g key={key} onClick={() => onTile(t)} onMouseMove={(e) => onMove(e, t)} style={{ cursor: "pointer" }}>
                    <rect x={x} y={y} width={w} height={h} fill={css(mix(rgb, hex2rgb(BG), cardWash(t.level, byModule)))}
                      stroke={byModule ? css(mix(rgb, hex2rgb(BG), 0.55)) : cardLine(t.level)} strokeWidth="1" rx={cardR(t.level)} />
                    <rect x={x} y={y} width={byModule ? 4 : 2.5} height={t.headH} fill={css(rgb)}
                      rx={byModule ? 0 : 1.25} />
                    <line x1={x} y1={y + t.headH} x2={x + w} y2={y + t.headH}
                      stroke={byModule ? css(rgb) : cardLine(t.level)} strokeWidth={byModule ? 1.4 : 1}
                      opacity={byModule ? 0.6 : 1} />
                    <text x={x + (byModule ? 10 : 8)} y={y + t.headH * 0.7} fill={INK} fontSize={fs} fontWeight="700"
                      fontFamily="var(--font-display)" letterSpacing="0.08em" style={{ pointerEvents: "none" }}>
                      {clip(tileLabel(t).toUpperCase(), (showMoney ? w - 64 : w - 4) - (byModule ? 6 : 4), fs, 0.72)}
                    </text>
                    {showMoney && (
                      <text x={x + w - 6} y={y + t.headH * 0.7} textAnchor="end" fill={INK_3}
                        fontSize={fs - 0.5} fontFamily="var(--font-mono)" style={{ pointerEvents: "none" }}>{money}</text>
                    )}
                  </g>
                );
              }

              const rgb = t.isAsm ? asmRgb(t) : byModule ? hex2rgb(moduleColor(t.node?.module_code)) : heatRgb(t.abs, maxAbs);
              const fill = t.isAsm ? `url(#${hatchId(asmRgb(t))})` : css(rgb);
              const ink = inkOn(rgb);
              const drill = t.node?.has_children;
              const fs = fitFont(t.w, t.h, leafCap(t.level, nested));
              const vertical = t.w < 40 && t.h > 52 && t.h > t.w * 1.5;
              const showLabel = vertical || (t.w > 17 && t.h > 10);
              const showVal = !vertical && showLabel && t.h > fs * 2.6 + 6 && t.w > 44;
              const room = vertical ? t.h : drill && t.w > 26 ? t.w - 14 : t.w;
              let text = clip(tileLabel(t), room, fs);
              if (text === "…" || text === "") text = t.instances ? String(t.instance) : tileLabel(t).slice(0, 1);
              return (
                <g key={key} onClick={() => onTile(t)} onMouseMove={(e) => onMove(e, t)}
                  style={{ cursor: t.isAsm ? "default" : "pointer" }}>
                  <rect x={t.x + 0.4} y={t.y + 0.4} width={Math.max(0, t.w - 0.8)} height={Math.max(0, t.h - 0.8)}
                    fill={fill} stroke={BG} strokeWidth="0.8" rx="2" opacity={hover && !isHover ? 0.88 : 1} />
                  {drill && t.w > 26 && t.h > 24 && !vertical && (
                    <text x={t.x + t.w - 5} y={t.y + fs + 1} textAnchor="end" fill={ink} fontSize={Math.min(12, fs)} opacity="0.6" style={{ pointerEvents: "none" }}>⤢</text>
                  )}
                  {showLabel && (vertical ? (
                    <text transform={`translate(${t.x + t.w / 2 + fs * 0.36} ${t.y + 5}) rotate(90)`}
                      fill={ink} fontSize={fs} fontWeight="500" style={{ pointerEvents: "none" }}>{text}</text>
                  ) : (
                    <text x={t.x + 4} y={t.y + fs + 2.5} fill={ink} fontSize={fs} fontWeight="500" style={{ pointerEvents: "none" }}>{text}</text>
                  ))}
                  {showVal && (
                    <text x={t.x + 4} y={t.y + fs * 2.1 + 4} fill={ink} fontSize={Math.max(8.5, fs * 0.82)} opacity="0.85"
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
                      return <text key={ei} x={x} y={y} fontSize={LEG_FS} fontWeight="700" fill={INK_3}
                        fontFamily="var(--font-display)" letterSpacing="0.06em">{e.text}</text>;
                    }
                    const bw = e.type === "grad" ? 60 : 13;
                    const bh = e.type === "grad" ? 9 : 11;
                    const paint = e.type === "grad" ? "url(#heatKey)"
                      : e.type === "hatch" ? `url(#${e.id})` : e.color;
                    return (
                      <g key={ei}>
                        <rect x={x} y={y - bh + 1} width={bw} height={bh} rx="2" fill={paint} />
                        <text x={x + bw + 5} y={y} fontSize={LEG_FS} fill={INK_3}>{e.text}</text>
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
    const qty = tile.instances ? 1 : n?.quantity || 1;
    const tag = tile.instances ? ` · ${tile.instance} of ${tile.instances}` : qty > 1 ? ` · ×${qty}` : "";
    rows.push([n?.has_children ? "Assembly" : "Part", `${n?.module_code || ""}${tag}`]);
    rows.push([`Rolled-up ${isCost ? (scenario === "likely" ? "cost" : scenario + " cost") : "weight"}`, fmt(abs)]);
    if (isCost && n?.has_children) {
      rows.push(["  ├ parts", fmt(abs - (tile.own || 0))]);
      rows.push(["  └ assembly work", (tile.own || 0) > 0 ? fmt(tile.own) : "—"]);
    }
    if (isCost && (n?.rollup_cost_min ?? 0) !== (n?.rollup_cost_max ?? 0)) {
      const unit = costFieldSafe(n, scenario);
      const scale = unit > 0 ? abs / unit : qty;
      rows.push(["Range (min–max)", `${fmt((n.rollup_cost_min || 0) * scale)} – ${fmt((n.rollup_cost_max || 0) * scale)}`]);
    }
    rows.push([tile.pctLevel != null ? "Share" : "Share of BOM",
      tile.pctLevel != null ? `${tile.pctLevel.toFixed(1)}% of level · ${pctBom.toFixed(1)}% of BOM` : `${pctBom.toFixed(1)}%`]);
    if (n?.has_children) rows.push(["Components", `${componentInstances(n).toLocaleString()} parts`]);
    if (isCost && n?.assembly_priced === false) rows.push(["⚠ Assembly cost", "no rate/time set — counted as 0"]);
    if (isCost && n?.coverage != null && n.coverage < 1) rows.push(["⚠ Costed", `${Math.round(n.coverage * 100)}% — a floor`]);
  }
  return (
    <div style={{
      position: "absolute", top: flipY ? y - 14 : y + 14, left: flip ? x - 14 : x + 14,
      transform: `${flip ? "translateX(-100%)" : ""} ${flipY ? "translateY(-100%)" : ""}`.trim() || "none",
      pointerEvents: "none", zIndex: 20, maxWidth: 300,
      background: "var(--bg-raised, #fff)", border: "1px solid var(--hair-strong)", borderRadius: 8,
      boxShadow: "var(--shadow-2, 0 6px 24px rgba(0,0,0,0.14))", padding: "9px 11px", fontSize: 12,
    }}>
      <div style={{ fontWeight: 600, marginBottom: 5, display: "flex", gap: 6, alignItems: "baseline" }}>
        <span>{tile.isAsm ? "Assembly work" : tileLabel(tile)}</span>
        {!tile.isAsm && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--ink-3)" }}>{n?.item_id}</span>}
      </div>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 14, lineHeight: 1.5 }}>
          <span style={{ color: "var(--ink-3)", whiteSpace: "pre" }}>{k}</span>
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
