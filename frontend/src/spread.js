// The shape of a three-point cost estimate.
//
// A rolled-up cost is min / most-likely / max, and the KPI tile printed one number in 28px
// type — which reads as a fact. It is not one: on the real BOM the AEC stack is € 6.9k with a
// € 4.6k–€ 10.8k range, and a reader who sees only the middle figure is being told something
// far more precise than anybody knows.
//
// Drawn rather than written, because the useful part is the SHAPE. Whether the spread is tight
// or wide, and whether it leans expensive or cheap, is a glance from a curve and a paragraph
// from three numbers. `likely` is genuinely not the midpoint — a quoted part with a downside
// risk skews one way, a machined part with a rough upper estimate the other — so the curve is
// deliberately asymmetric and its peak sits where the likely value actually is.
//
// Kept out of the JSX so it can be checked without a browser: `scripts/check-spread.mjs`.

/** Where a value sits across the range, 0..1. Guards the zero-width case. */
export const spreadX = (v, min, max) => (max > min ? (v - min) / (max - min) : 0.5);

/**
 * An SVG path for the curve: a smooth unimodal hump, zero at min and max, peaking at likely.
 *
 * Two cubics rather than one, so the peak lands exactly on `likely` instead of wherever a
 * single curve happened to put it. Returns null when there is no spread to draw — min === max
 * is the common case for a BOM costed from single-point figures, and a "curve" of zero width
 * is a rendering artefact, not information. The caller draws a spike instead.
 */
export function spreadPath(min, likely, max, w, h) {
  if (!(max > min) || !Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return null;
  // Clamped, because a rolled-up likely can sit a hair outside a rounded min/max and a peak
  // drawn off the end of the box looks like a bug rather than a rounding artefact.
  const t = Math.min(1, Math.max(0, spreadX(likely, min, max)));
  const px = t * w;
  const left = px, right = w - px;
  const seg = (from, to, span) =>
    `C ${from + span * 0.42} ${h} ${from + span * 0.58} 0 ${to} 0`;
  const back = (from, to, span) =>
    `C ${from + span * 0.42} 0 ${from + span * 0.58} ${h} ${to} ${h}`;
  return [
    `M 0 ${h}`,
    left > 0 ? seg(0, px, left) : `L ${px} 0`,
    right > 0 ? back(px, w, right) : `L ${w} ${h}`,
    "Z",
  ].join(" ");
}
