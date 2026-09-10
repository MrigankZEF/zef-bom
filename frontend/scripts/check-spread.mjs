// Checks the three-point curve's geometry. `node scripts/check-spread.mjs`
//
// There is no test runner in this frontend, and adding one to prove twenty lines of path maths
// would be the wrong trade. This imports the real module — not a copy of it — and asserts the
// things that would actually go wrong: a peak drawn somewhere other than the likely value, and
// a path emitted for a range that has no width.

import { spreadPath, spreadX } from "../src/spread.js";

let failed = 0;
const check = (name, ok, detail = "") => {
  if (!ok) { failed += 1; console.log(`  FAIL  ${name}${detail ? " — " + detail : ""}`); }
  else console.log(`  ok    ${name}`);
};

const W = 120, H = 26;

// The degenerate case first, because it is the common one: a BOM costed from single-point
// figures has min === likely === max, and a zero-width curve is an artefact, not information.
check("no spread returns null", spreadPath(500, 500, 500, W, H) === null);
check("inverted range returns null", spreadPath(900, 500, 100, W, H) === null);
check("zero box returns null", spreadPath(1, 2, 3, 0, H) === null);

const p = spreadPath(100, 500, 900, W, H);
check("a real spread returns a path", typeof p === "string" && p.startsWith("M 0 26"));
check("the path closes", (p || "").trim().endsWith("Z"));

// The peak has to be AT the likely value — the whole reason for two cubics rather than one.
const peakOf = (min, likely, max) => {
  const path = spreadPath(min, likely, max, W, H);
  const m = path.match(/C [\d.]+ [\d.]+ [\d.]+ 0 ([\d.]+) 0/);
  return m ? Number(m[1]) : NaN;
};
check("peak sits at the likely value (centre)", Math.abs(peakOf(0, 50, 100) - W / 2) < 0.01,
      `got ${peakOf(0, 50, 100)}`);
check("peak follows a low likely", Math.abs(peakOf(0, 20, 100) - W * 0.2) < 0.01,
      `got ${peakOf(0, 20, 100)}`);
check("peak follows a high likely", Math.abs(peakOf(0, 90, 100) - W * 0.9) < 0.01,
      `got ${peakOf(0, 90, 100)}`);

// A likely value outside its own min/max happens through rounding, and must not draw off the
// end of the box.
const outside = spreadPath(100, 99, 900, W, H);
check("a likely below min is clamped, not drawn off the box",
      typeof outside === "string" && !/-\d/.test(outside), outside);

check("spreadX maps the ends", spreadX(100, 100, 900) === 0 && spreadX(900, 100, 900) === 1);
check("spreadX survives a zero-width range", spreadX(5, 5, 5) === 0.5);

console.log(failed ? `\n${failed} failed.` : "\nall good.");
process.exit(failed ? 1 : 0);
