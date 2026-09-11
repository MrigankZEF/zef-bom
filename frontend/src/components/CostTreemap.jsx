import { useEffect, useRef, useState } from "react";
import { squarify, colorAt } from "./Treemap.jsx";

// Hierarchical treemap for the Costing tab. Two layouts, chosen by `depth`:
//   depth = 1  → drill-down: one level at a time; click an assembly to zoom in.
//   depth > 1  → nested overview: N levels at once. An assembly is drawn as a card whose
//                children fill it completely, with its title floating over the top.
//                Nothing in a card is chrome that costs area: area IS the number here, so a
//                title band of its own would take value away from the tiles beneath it and
//                two identically priced parts at different depths would come out different
//                sizes. Measured before this: 1.31x apart, 17% of the canvas carrying
//                nothing. Now 1.00x, and the whole canvas is value.
// Parts are solid; an assembly's own process cost is drawn as a hatched tile, so material vs
// assembly cost differs by pattern as well as colour.
// Heat mode is strictly one red ramp — module colours appear only in Module mode.
// The viewBox is sized to the real pixel width, so text stays a constant size at any width.

// ── ZEF design tokens, inlined ────────────────────────────────────────────────────────
// The system is explicit: a white ground with warm fills on it, charcoal ink, hairlines,
// ONE red, and "no drop shadows". Concrete values (not var()) so the exported PNG matches
// the screen — which means this has to be changed in step with --bg, not left to drift.
const BG = "#FFFFFF";          // --bg
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

// An assembly is a miniature of the app's own .card: a warm wash for a body, a hairline
// border, and an uppercase display title floating over its contents. Depth is carried by
// the wash and by the title size — never by a raised bevel or a filled title bar.
const headH = (level) => (level === 0 ? 22 : level === 1 ? 19 : level === 2 ? 16.5 : 15);
const headFont = (level) => (level === 0 ? 11.5 : level === 1 ? 10.4 : level === 2 ? 9.4 : 8.8);
const leafCap = (level, nested) => (nested ? headFont(level) + 2 : 15);
// Module mode leans on the wash to say WHICH subsystem a card is; Heat mode keeps it faint so
// the red ramp on the parts stays the only thing carrying data.
const cardWash = (level, byModule) =>
  (byModule ? 0.84 : 0.9) + Math.min(0.08, level * 0.025);   // toward bone as you nest
const cardLine = (level) => (level === 0 ? HAIR_STRONG : HAIR);
// Gone from the card, and none of it missed: rounded corners, the nested indent, the coloured
// stub before a title, and the gutter inset. At seven levels deep a 3px radius only softens
// the one edge that says where a box ends, and the indent restated what the title rule
// already said. Every one of them also took a FIXED number of pixels out of a box whose size
// varies by three orders of magnitude, which is a tax the small cards paid and the big ones
// did not. Neighbours are separated by the hairline stroke instead — drawn on the boundary,
// so it costs no area. Depth still reads three ways: the wash shifts, the title font shrinks,
// and every card keeps its border.

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
//
// Every cell must come out the SAME AREA, because in a treemap area IS the number: two cells
// costing the same that are drawn 3x apart make the picture lie. An exact rows x cols grid
// gives that for free, but only when the count has a divisor that suits the box. Otherwise
// each row gets a height proportional to how many cells it holds — a row of 6 out of 26 takes
// 6/26 of the height — so its cells are wider and shorter and still cover w*h/n exactly.
function gridCells(x, y, w, h, n) {
  if (n <= 1) return [{ x, y, w, h }];
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
  const cols = Math.max(1, Math.round(Math.sqrt((n * w) / h)));
  const rows = Math.ceil(n / cols);
  const out = [];
  let cy = y;
  for (let r = 0; r < rows; r++) {
    const count = Math.min(cols, n - r * cols);
    const rh = (h * count) / n;          // this row's share of the height = its share of the cells
    const cw = w / count;                // ...so every cell is exactly w*h/n
    for (let c = 0; c < count; c++) out.push({ x: x + c * cw, y: cy, w: cw, h: rh });
    cy += rh;
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

// Branch colours: the shared PALETTE, reordered.
//
// It opens on the accent red because the first branch of the BOM is the machine itself — AEC —
// and the product is what the red is for everywhere else in the app. After that the order is
// chosen for maximum separation between NEIGHBOURING branches rather than by palette position,
// since adjacent indices are what land next to each other in the picture. Indices into
// PALETTE, so module colouring and the Catalog keep the order they had.
//   3 red · 0 slate · 2 ochre · 1 moss · 5 slate-lifted · 7 ochre-lifted · 6 moss-lifted
//   · 8 taupe · 4 ink
const BRANCH_HUES = [3, 0, 2, 1, 5, 7, 6, 8, 4];
const branchColor = (i) => colorAt(BRANCH_HUES[((i % BRANCH_HUES.length) + BRANCH_HUES.length) % BRANCH_HUES.length]);

// A part used ×26 can be drawn as one ×26 tile or as 26 individual tiles (what the Plotly
// tool did). Splitting is capped — beyond this many instances the tiles are unreadable slivers.
const SPLIT_MAX = 48;

export default function CostTreemap({ node, metric = "cost", colorMode = "cost", scenario = "likely",
  depth = 1, split = true, svgRef, format, onOpenPart, path: pathProp, onPath }) {
  // Each step is {id, single, inst}: `single` marks a drill into ONE instance of a ×N part,
  // so the view that opens sums to the tile you actually clicked, not the whole ×N group.
  //
  // The drill path is internal by default and CONTROLLED when `onPath` is supplied. That is
  // for the side-by-side comparison: two treemaps drilled to different depths compare
  // nothing, so one path drives both. A step that does not exist on one side simply stops
  // resolving there, which is already how `steps` handles a stale path.
  const [pathLocal, setPathLocal] = useState([]);
  const controlled = typeof onPath === "function";
  const path = controlled ? (pathProp || []) : pathLocal;
  const setPath = controlled ? onPath : setPathLocal;
  const [hover, setHover] = useState(null);
  const [boxW, setBoxW] = useState(760);
  const rootRef = useRef(null);
  const wrapRef = useRef(null);
  // Only the uncontrolled copy resets itself — when the path is owned outside, resetting it
  // here would fight the owner on every re-render.
  useEffect(() => { if (!controlled) setPathLocal([]); /* eslint-disable-next-line */ }, [node?.item_id]);
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
  // Colour survives a drill-down. Hues are handed out at the top of whatever view you are
  // looking at, so without this, drilling into a block opened a view coloured from scratch —
  // the same thing in a different colour, one click apart. The palette is ROTATED by the
  // position of each branch stepped into, so the block you clicked keeps its colour as the
  // first hue of the view it opens.
  //
  // Stepping into the BIGGEST branch shifts by nothing, which is the case that matters: the
  // biggest block is red, so drilling into it opens a view whose biggest block is red too, all
  // the way down. Step into the third-largest instead and its own colour leads the new view.
  let hueShift = 0;
  let mult = 1, cur = node;
  for (const step of path) {
    const nxt = (cur.children || []).find((c) => c.item_id === step.id);
    if (!nxt) break;
    // Size order, matching how the hues were handed out in the view being clicked from.
    const sibs = (cur.children || [])
      .map((c) => ({ id: c.item_id, v: costOf(c) * (c.quantity || 1) }))
      .filter((s) => s.v > 0)
      .sort((a, b) => b.v - a.v);
    const idx = sibs.findIndex((s) => s.id === step.id);
    if (idx > 0) hueShift += idx;
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
      // The nested view used to hide an assembly's own cost when it came to under 3% of the
      // card, to cut clutter. But the card had ALREADY been sized on a total that included it,
      // so the remaining children renormalised over a smaller sum and quietly grew to fill the
      // gap — the last of the three reasons two identically priced parts came out different
      // sizes. A sliver that is 3% of its card is thin; a picture that misstates 3% of every
      // card is worse.
      const floor = 0.005;
      if (own > floor) items.push({ isAsm: true, value: own, module: parent.module_code });
    }
    return items;
  };

  const tiles = [];
  const maxLevel = Math.max(1, depth);

  const emit = (r, c, instance, instances, level, absMult, ppath, levelTotal, branch) => {
    const unitMult = instance ? 1 : c.quantity || 1;
    const scale = unitMult * absMult;
    const step = { id: c.item_id, single: !!instance, inst: instance || null };
    const base = {
      node: c, x: r.x, y: r.y, w: r.w, h: r.h, level, path: [...ppath, step], branch,
      // How many of this item the tile stands for, multiplied out along the whole path from
      // the focus. A tile that is not split into instances is a GROUP, and without this the
      // flyout gave a rolled-up figure with no way to see how many things were in it.
      units: scale,
      instance, instances, abs: costOf(c) * scale, own: ownCost(c) * scale,
      pctLevel: levelTotal ? ((costOf(c) * unitMult) / levelTotal) * 100 : null,
    };
    const hh = headH(level);
    const canNest = maxLevel > 1 && c.children?.length && level + 1 < maxLevel
      && r.h > hh + 28 && r.w > 64 && childItems(c).length > 0;
    if (canNest) {
      tiles.push({ ...base, container: true, headH: hh });
      layout(c, r.x, r.y, r.w, r.h, level + 1, scale, base.path, branch);
    } else {
      tiles.push({ ...base, leaf: true });
    }
  };

  function layout(parent, x, y, w, h, level, absMult, ppath, branch) {
    const items = childItems(parent);
    if (!items.length) return;
    const levelTotal = maxLevel <= 1 ? items.reduce((s, t) => s + t.value, 0) || 1 : null;
    // Branch identity is handed out at the TOP level only and then inherited all the way down,
    // so a whole subtree reads as one family of colour.
    //
    // Handed out in SIZE order, which `squarify` already returns: biggest first. So the biggest
    // branch is always the first hue — the accent red — and it is the same block that sits in
    // the top-left corner. That is also what makes the drill-down keep its colour: step into
    // the biggest branch and the biggest thing inside it is the first hue again, so red stays
    // red however deep you go. One index per squarify result rather than per emitted tile,
    // because a part split into 26 instances is one branch, not 26.
    let seq = 0;
    for (const r of squarify(items, x, y, w, h)) {
      const br = level === 0 ? seq++ : branch;
      if (r.isAsm) {
        tiles.push({ isAsm: true, module: r.module, x: r.x, y: r.y, w: r.w, h: r.h, level,
          branch: br, units: absMult,
          abs: r.value * absMult, pctLevel: levelTotal ? (r.value / levelTotal) * 100 : null });
        continue;
      }
      if (r.splitInto > 1) {
        gridCells(r.x, r.y, r.w, r.h, r.splitInto).forEach((cell, i) =>
          emit(cell, r.node, i + 1, r.splitInto, level, absMult, ppath, levelTotal, br));
      } else {
        emit(r, r.node, null, null, level, absMult, ppath, levelTotal, br);
      }
    }
  }
  layout(focus, 0, 0, width, height, 0, mult, [], 0);

  // Colour scale ignores the frames — otherwise the big assemblies wash out every real part.
  const maxAbs = Math.max(...tiles.filter((t) => !t.container).map((t) => t.abs), 0);
  const modules = [...new Set(tiles.filter((t) => !t.isAsm && t.node?.module_code).map((t) => t.node.module_code))].sort();
  const hasAsm = tiles.some((t) => t.isAsm);
  const byModule = colorMode === "module";
  // Branch mode is the default, and the reason is that area already carries cost. Spending
  // colour on price as well says the same thing twice and leaves the question the picture
  // cannot otherwise answer — which of these blocks belong together — unanswered. So each
  // top-level subsystem takes a hue and keeps it all the way down, and depth is a tint of that
  // hue rather than a separate signal. Heat and Module are still there for when cost intensity
  // or subsystem tagging is the actual question.
  const byBranch = colorMode === "branch";
  const branches = [...new Set(tiles.filter((t) => t.level === 0).map((t) => t.branch))]
    .sort((a, b) => a - b);
  const branchName = (b) => {
    const t = tiles.find((x) => x.level === 0 && x.branch === b);
    return t ? tileLabel(t) || t.node?.item_id || "—" : "—";
  };
  // Deeper is paler, of one hue. Capped well short of the background so the deepest card is
  // still recognisably its branch's colour rather than washed out to nothing.
  const branchRgb = (t, extra = 0) =>
    mix(hex2rgb(branchColor((t.branch || 0) + hueShift)), hex2rgb(BG), Math.min(0.62, extra + t.level * 0.13));

  // Heat mode is one red ramp end to end: frames get a red that pales with depth (structure,
  // not data), parts get the true cost heat. Module colours never appear here.
  // In Heat mode the accent belongs to the parts, so a card's marker is a calm ink-grey and
  // depth alone separates the levels; Module mode gives each card its subsystem colour.
  const frameRgb = (t) => byBranch
    ? branchRgb(t, 0.34)
    : byModule
      ? hex2rgb(moduleColor(t.node?.module_code))
      : mix(hex2rgb(INK), hex2rgb(BG), Math.min(0.55, 0.12 + t.level * 0.14));
  const asmRgb = (t) => (byBranch ? branchRgb(t, 0.1)
    : byModule ? hex2rgb(moduleColor(t.module)) : heatRgb(t.abs, maxAbs));

  // Where each assembly's floating title goes.
  //
  // A card's children fill it completely, so a child that sits in its parent's top-left corner
  // starts at the SAME y — and both titles land on the same line, one printed over the other.
  // Titles are therefore placed shallowest-first and each one drops below any already-placed
  // title it would collide with, which puts a nested card's name on the line under its
  // parent's instead of on top of it. A title with nowhere left to go inside its own card is
  // dropped rather than drawn somewhere it does not belong — the card still has its border,
  // its wash and its tooltip.
  const capLabels = [];
  const capPlaced = [];
  for (const t of tiles.filter((x) => x.container).sort((a, b) => a.level - b.level)) {
    const fs = headFont(t.level), bh = t.headH;
    const money = format ? format(t.abs) : String(Math.round(t.abs));
    const label = clip(tileLabel(t).toUpperCase(), t.w - 12, fs, 0.72);
    if (!label) continue;
    const labelW = label.length * fs * 0.72;
    const moneyW = money.length * (fs - 0.5) * 0.62;
    // The cost sits right after the name, not pinned to the far edge of the card. Pinned, a
    // wide card put its name and its figure a whole screen apart, and once titles stack up
    // there is nothing left to say which figure belongs to which name.
    const showMoney = labelW + moneyW + 26 <= t.w - 6;
    const plateW = Math.min(t.w - 6, labelW + (showMoney ? moneyW + 12 : 0) + 14);
    // Top-left, where a name is looked for. It was tried on the right, because cards nest from
    // the top-left and so pile their titles into that one corner — but the right-hand corner
    // put a name a long way from the block it names and bought no clarity for it. The pile-up
    // is handled by the drop-down below instead, and now that a branch keeps one colour all the
    // way down, a stack of titles at the left edge reads as the nesting it is.
    const plateX = t.x;
    let y = t.y;
    for (let guard = 0; guard < 12; guard++) {
      const hit = capPlaced.find((p) => p.y < y + bh && y < p.y + p.h
        && p.x < plateX + plateW && plateX < p.x + p.w);
      if (!hit) break;
      y = hit.y + hit.h;
    }
    if (y + bh > t.y + t.h) continue;   // no room left inside its own card
    capPlaced.push({ x: plateX, y, w: plateW, h: bh });
    capLabels.push({ t, fs, bh, y, label, labelW, plateW, plateX, money, showMoney });
  }

  // A tile's own label has to dodge those plates too. The label that gets buried is always the
  // DEEPEST one at a shared corner: the plates are drawn after every tile, so a leaf sitting in
  // the top-left of a nested card had its name painted over by the names of the two or three
  // cards above it. Nothing was wrong with the placement — it was simply underneath.
  const clearOfCaps = (x, y, w, h) => {
    let ny = y;
    for (let guard = 0; guard < 12; guard++) {
      const hit = capPlaced.find((p) => p.y < ny + h && ny < p.y + p.h && p.x < x + w && x < p.x + p.w);
      if (!hit) break;
      ny = hit.y + hit.h;
    }
    return ny;
  };

  const hatches = [];
  const hatchId = (rgb) => {
    const key = css(rgb);
    let i = hatches.indexOf(key);
    if (i < 0) { hatches.push(key); i = hatches.length - 1; }
    return "hx" + i;
  };
  for (const t of tiles) if (t.isAsm) hatchId(asmRgb(t));
  const legendHatch = byModule || byBranch ? hex2rgb(NO_MODULE) : HEAT[HEAT.length - 1];
  const legendHatchId = hatchId(legendHatch);

  // Legend lives inside the SVG so it's always visible AND lands in the exported PNG.
  const legendItems = [];
  if (byBranch) {
    for (const b of branches) legendItems.push({ type: "swatch", color: branchColor(b + hueShift), text: branchName(b) });
  } else if (byModule) {
    legendItems.push({ type: "title", text: "Modules" });
    for (const m of modules) legendItems.push({ type: "swatch", color: moduleColor(m), text: m });
    if (!modules.length) legendItems.push({ type: "title", text: "— none tagged" });
  } else {
    legendItems.push({ type: "grad", text: metric === "cost" ? "cheaper → pricier" : "lighter → heavier" });
  }
  if (hasAsm) legendItems.push({ type: "hatch", id: legendHatchId, text: "assembly work (hatched)" });
  if (nested) legendItems.push({ type: "title", text: byBranch
    ? `Size = ${metric === "cost" ? "cost" : "weight"} · colour = which top-level branch · paler = deeper`
    : byModule
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
    const r = rootRef.current?.getBoundingClientRect();
    if (r) setHover({ tile: t, x: e.clientX - r.left, y: e.clientY - r.top });
  };
  // The assembly you are INSIDE isn't a tile, so it had no flyout while every child had one.
  // Give the breadcrumb's current entry the same tooltip, built from the focused node.
  const focusTile = {
    node: focus, abs: costOf(focus) * mult, own: ownCost(focus) * mult, pctLevel: null,
  };
  const crumb = (n, i) => {
    const st = steps[i - 1];
    return i > 0 && st?.inst && split ? `${n.item_name} ${st.inst}` : n.item_name;
  };

  return (
    <div ref={rootRef} style={{ position: "relative" }} onMouseLeave={() => setHover(null)}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4, marginBottom: 8, fontSize: 12 }}>
        {chain.map((n, i) => (
          <span key={n.item_id + i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            {i > 0 && <span style={{ color: "var(--ink-4)" }}>›</span>}
            {i === chain.length - 1 ? (
              // A span, not a disabled button: a disabled element fires no mouse events, so
              // the flyout never appeared. Same look, and now it can be hovered.
              <span onMouseMove={(e) => onMove(e, focusTile)}
                style={{ padding: "2px 4px", fontSize: 12, color: "var(--ink)", fontWeight: 600,
                  textDecoration: "underline dotted var(--ink-4)", textUnderlineOffset: 3 }}>
                {crumb(n, i)}
              </span>
            ) : (
              <button onClick={() => setPath(steps.slice(0, i))} title={`Back to ${n.item_name}`}
                style={{ border: 0, background: "transparent", padding: "2px 4px", borderRadius: 4,
                  fontSize: 12, cursor: "pointer", color: "var(--accent)" }}>
                {crumb(n, i)}
              </button>
            )}
          </span>
        ))}
        {chain.length > 1 && <span style={{ marginLeft: 6, fontSize: 11, color: "var(--ink-3)" }}>· click a tile to drill in, a name above to go back</span>}
      </div>

      {tiles.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--ink-3)" }}>No {metric === "cost" ? "costed" : "weighed"} items at this level yet.</div>
      ) : (
        <div ref={wrapRef}>
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

              // A card is only its body here — the wash the children sit on. Its title is drawn
              // in a second pass after every tile, because the children now cover the card
              // completely and would paint over a label drawn at this point.
              if (t.container) {
                const rgb = frameRgb(t);
                return (
                  <g key={key} onClick={() => onTile(t)} onMouseMove={(e) => onMove(e, t)} style={{ cursor: "pointer" }}>
                    <rect x={t.x} y={t.y} width={t.w} height={t.h}
                      fill={byBranch ? css(rgb) : css(mix(rgb, hex2rgb(BG), cardWash(t.level, byModule)))}
                      stroke={byModule || byBranch ? css(mix(rgb, hex2rgb(INK), 0.14)) : cardLine(t.level)}
                      strokeWidth="1" />
                  </g>
                );
              }

              const rgb = t.isAsm ? asmRgb(t)
                : byBranch ? branchRgb(t)
                : byModule ? hex2rgb(moduleColor(t.node?.module_code))
                : heatRgb(t.abs, maxAbs);
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
              // Drop the label clear of any assembly title sitting over this tile's top-left.
              const lineH = fs + 5;
              const ty = vertical ? t.y : clearOfCaps(t.x, t.y, Math.min(t.w, text.length * fs * 0.6 + 10), lineH);
              // ...but only if there is still room for it inside the tile once moved.
              const fits = ty + lineH <= t.y + t.h;
              const showVal2 = showVal && ty + lineH + fs <= t.y + t.h;
              return (
                <g key={key} onClick={() => onTile(t)} onMouseMove={(e) => onMove(e, t)}
                  style={{ cursor: t.isAsm ? "default" : "pointer" }}>
                  <rect x={t.x + 0.4} y={t.y + 0.4} width={Math.max(0, t.w - 0.8)} height={Math.max(0, t.h - 0.8)}
                    fill={fill} stroke={BG} strokeWidth="0.8" opacity={hover && !isHover ? 0.72 : 1} />
                  {drill && t.w > 26 && t.h > 24 && !vertical && (
                    <text x={t.x + t.w - 5} y={t.y + fs + 1} textAnchor="end" fill={ink} fontSize={Math.min(12, fs)} opacity="0.6" style={{ pointerEvents: "none" }}>⤢</text>
                  )}
                  {showLabel && (vertical ? (
                    <text transform={`translate(${t.x + t.w / 2 + fs * 0.36} ${t.y + 5}) rotate(90)`}
                      fill={ink} fontSize={fs} fontWeight="500" style={{ pointerEvents: "none" }}>{text}</text>
                  ) : fits && (
                    <text x={t.x + 4} y={ty + fs + 2.5} fill={ink} fontSize={fs} fontWeight="500" style={{ pointerEvents: "none" }}>{text}</text>
                  ))}
                  {showVal2 && (
                    <text x={t.x + 4} y={ty + fs * 2.1 + 4} fill={ink} fontSize={Math.max(8.5, fs * 0.82)} opacity="0.85"
                      fontFamily="var(--font-mono)" style={{ pointerEvents: "none" }}>
                      {format ? format(t.abs) : t.abs}
                    </text>
                  )}
                </g>
              );
            })}

            {/* Assembly titles, over the top of everything — see capLabels above for where
                each one ends up and why. The plate sits under the TEXT only: a full-width band
                would hide a stripe of the picture across every card, which is most of what the
                old title bar was doing wrong even before it took area. */}
            {capLabels.map(({ t, fs, bh, y, label, labelW, plateW, plateX, money, showMoney }, i) => {
              const rgb = frameRgb(t);
              const plate = mix(hex2rgb(BG), rgb, byModule ? 0.16 : byBranch ? 0.1 : 0.06);
              return (
                <g key={"cap" + (t.node?.item_id || "asm") + i}
                  onClick={() => onTile(t)} onMouseMove={(e) => onMove(e, t)} style={{ cursor: "pointer" }}>
                  {/* Not quite opaque: the tile under it stays readable as a shape, so the
                      picture still reads as one continuous area map. */}
                  <rect x={plateX} y={y} width={plateW} height={bh} fill={css(plate)} opacity="0.94" />
                  <text x={plateX + 7} y={y + bh * 0.72} fill={INK} fontSize={fs} fontWeight="700"
                    fontFamily="var(--font-display)" letterSpacing="0.08em" style={{ pointerEvents: "none" }}>
                    {label}
                  </text>
                  {showMoney && (
                    <text x={plateX + 7 + labelW + 10} y={y + bh * 0.72} fill={INK_3}
                      fontSize={fs - 0.5} fontFamily="var(--font-mono)" style={{ pointerEvents: "none" }}>{money}</text>
                  )}
                </g>
              );
            })}

            {/* The hovered block, ringed. Last of the passes on purpose: a card is completely
                covered by its children, so an outline drawn where the card is drawn would be
                painted over by the very tiles it is meant to enclose. Two strokes — a pale one
                under a dark one — so the ring holds up over a dark red tile and a bone one
                alike, without a drop shadow. */}
            {hover?.tile && (
              <g style={{ pointerEvents: "none" }}>
                <rect x={hover.tile.x + 0.5} y={hover.tile.y + 0.5}
                  width={Math.max(0, hover.tile.w - 1)} height={Math.max(0, hover.tile.h - 1)}
                  fill="none" stroke={BG} strokeWidth="3.5" opacity="0.75" />
                <rect x={hover.tile.x + 0.5} y={hover.tile.y + 0.5}
                  width={Math.max(0, hover.tile.w - 1)} height={Math.max(0, hover.tile.h - 1)}
                  fill="none" stroke={INK} strokeWidth="1.5" />
              </g>
            )}

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
                        <rect x={x} y={y - bh + 1} width={bw} height={bh} fill={paint} />
                        <text x={x + bw + 5} y={y} fontSize={LEG_FS} fill={INK_3}>{e.text}</text>
                      </g>
                    );
                  })}
                </g>
              );
            })}
          </svg>

        </div>
      )}

      {hover && (
        <Tooltip tile={hover.tile} x={hover.x} y={hover.y}
          flip={hover.x > (rootRef.current?.offsetWidth || 800) * 0.6}
          flipY={hover.y > (rootRef.current?.offsetHeight || 400) - 180 && hover.y > 180}
          metric={metric} scenario={scenario} format={format} bomTotal={bomTotal} />
      )}
    </div>
  );
}

// Quantities are floats because a BOM line can be metres or kilograms, but the overwhelming
// majority are whole units and "26.000" reads like a measurement rather than a count.
const fmtUnits = (n) => (Math.abs(n - Math.round(n)) < 0.0005
  ? Math.round(n).toLocaleString()
  : n.toLocaleString(undefined, { maximumFractionDigits: 3 }));

function Tooltip({ tile, x, y, flip, flipY, metric, scenario, format, bomTotal }) {
  const fmt = (v) => (format ? format(v) : String(Math.round(v)));
  const abs = tile.abs;
  const pctBom = bomTotal > 0 ? (abs / bomTotal) * 100 : 0;
  const n = tile.node;
  const isCost = metric === "cost";
  const rows = [];
  const units = tile.units || 1;
  const grouped = units > 1.0001;   // float: quantities can be fractional (metres, kg)
  const each = grouped ? abs / units : null;
  if (tile.isAsm) {
    rows.push(["This assembly's own work", fmt(abs)]);
    if (grouped) rows.push(["Across", `${fmtUnits(units)} builds · ${fmt(each)} each`]);
  } else {
    const qty = tile.instances ? 1 : n?.quantity || 1;
    const tag = tile.instances ? ` · ${tile.instance} of ${tile.instances}` : qty > 1 ? ` · ×${qty}` : "";
    rows.push([n?.has_children ? "Assembly" : "Part", `${n?.module_code || ""}${tag}`]);
    rows.push([`Rolled-up ${isCost ? (scenario === "likely" ? "cost" : scenario + " cost") : "weight"}`, fmt(abs)]);
    // One tile can stand for many of the same thing — 26 cells' worth of membrane is drawn as
    // one block. The rolled-up figure alone left no way to tell that from a single expensive
    // part, so say how many are in it and what one of them costs.
    if (grouped) rows.push(["In this tile", `${fmtUnits(units)} × ${fmt(each)}`]);
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
