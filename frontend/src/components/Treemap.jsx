// Lightweight squarified treemap in SVG (no d3). Sizes rects by `value`.
// Categorical scale drawn from the ZEF data-viz tokens (slate / moss / ochre / red / ink),
// then muted variants of the same five. No hues from outside the design system.
const PALETTE = [
  "#3D5A6B", // --data-6 slate
  "#6B7A55", // --data-4 moss
  "#A8751F", // --data-5 ochre
  "#B8001F", // --accent-ink
  "#3D3B38", // --ink-2
  "#7E8FA0", // slate, lifted
  "#9AA882", // moss, lifted
  "#C79A4B", // ochre, lifted
  "#8C7B6B", // taupe
];
// Assign by POSITION in a known list, never by hashing the name: hashing collides silently
// (AEC, UN and MS all landed on the same swatch), and the collisions move whenever the palette
// or the module list changes. Callers that know their full category list should use colorAt.
export const colorAt = (i) => PALETTE[((i % PALETTE.length) + PALETTE.length) % PALETTE.length];
export const colorFor = (key) => {
  let h = 0;
  for (const ch of String(key || "")) h = (h * 31 + ch.charCodeAt(0)) % 997;
  return PALETTE[h % PALETTE.length];
};

export function squarify(items, x, y, w, h) {
  // Biggest first — squarified layout fills from the top-left, so sorting descending puts the
  // largest contributor in the top-left corner and walks down in order.
  const sorted = [...items].sort((a, b) => b.value - a.value);
  const total = sorted.reduce((s, i) => s + i.value, 0) || 1;
  const scaled = sorted.map((i) => ({ ...i, area: (i.value / total) * (w * h) }));
  const out = [];
  let rect = { x, y, w, h };
  let row = [];
  const remaining = scaled.slice();

  const worst = (r, length) => {
    const sum = r.reduce((s, a) => s + a.area, 0);
    const mx = Math.max(...r.map((a) => a.area));
    const mn = Math.min(...r.map((a) => a.area));
    const s2 = sum * sum, l2 = length * length;
    return Math.max((l2 * mx) / s2, s2 / (l2 * mn));
  };
  const layoutRow = (r, rc, horizontal) => {
    const sum = r.reduce((s, a) => s + a.area, 0);
    if (horizontal) {
      const rh = sum / rc.w; let cx = rc.x;
      for (const a of r) { const rw = a.area / rh; out.push({ ...a, x: cx, y: rc.y, w: rw, h: rh }); cx += rw; }
      return { x: rc.x, y: rc.y + rh, w: rc.w, h: rc.h - rh };
    }
    const rw = sum / rc.h; let cy = rc.y;
    for (const a of r) { const rh = a.area / rw; out.push({ ...a, x: rc.x, y: cy, w: rw, h: rh }); cy += rh; }
    return { x: rc.x + rw, y: rc.y, w: rc.w - rw, h: rc.h };
  };

  while (remaining.length) {
    const horizontal = rect.w >= rect.h;
    const length = horizontal ? rect.w : rect.h;
    if (row.length === 0) { row.push(remaining.shift()); continue; }
    if (worst(row, length) >= worst([...row, remaining[0]], length)) {
      row.push(remaining.shift());
    } else { rect = layoutRow(row, rect, horizontal); row = []; }
  }
  if (row.length) layoutRow(row, rect, rect.w >= rect.h);
  return out;
}

export default function Treemap({ items, width = 760, height = 380, format, onSelect }) {
  const data = (items || []).filter((i) => i.value > 0);
  if (data.length === 0) {
    return <div style={{ padding: 40, textAlign: "center", color: "var(--ink-3)" }}>No data to show — enter some {format ? "values" : "costs"} first.</div>;
  }
  const rects = squarify(data, 0, 0, width, height);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", display: "block", fontFamily: "var(--font-body)" }}>
      {rects.map((r) => {
        const showLabel = r.w > 54 && r.h > 26;
        const fill = r.color || (r.colorKey ? colorFor(r.colorKey) : "#1C1B1A");
        const clickable = onSelect && !String(r.id).startsWith("__");
        return (
          <g key={r.id} onClick={() => clickable && onSelect(r.id)} style={{ cursor: clickable ? "pointer" : "default" }}>
            <title>{`${r.label}\n${format ? format(r.value) : r.value}`}</title>
            <rect x={r.x + 0.5} y={r.y + 0.5} width={Math.max(0, r.w - 1)} height={Math.max(0, r.h - 1)}
              fill={fill} opacity={0.88} stroke="var(--bg)" strokeWidth="1" rx="1" />
            {showLabel && (
              <>
                <text x={r.x + 6} y={r.y + 15} fill="#fff" fontSize="10.5" fontWeight="500" style={{ pointerEvents: "none" }}>
                  {r.label.length > Math.floor(r.w / 6) ? r.label.slice(0, Math.floor(r.w / 6) - 1) + "…" : r.label}
                </text>
                {r.h > 40 && (
                  <text x={r.x + 6} y={r.y + 29} fill="#fff" fontSize="10" opacity="0.8" fontFamily="var(--font-mono)" style={{ pointerEvents: "none" }}>
                    {format ? format(r.value) : r.value}
                  </text>
                )}
              </>
            )}
          </g>
        );
      })}
    </svg>
  );
}
