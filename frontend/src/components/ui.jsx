// Shared UI primitives + formatters, ported from the ZEF prototype.

const ICONS = {
  chevR: "M6 4l4 4-4 4",
  chevD: "M4 6l4 4 4-4",
  chevL: "M10 4L6 8l4 4",
  search: "M7 12a5 5 0 100-10 5 5 0 000 10zm4-1l3 3",
  close: "M4 4l8 8M12 4l-8 8",
  alert: "M8 1l7 13H1L8 1zm0 5v4m0 2v.5",
  check: "M3 8l3.5 3.5L13 5",
  box: "M8 1.5l6 3v7l-6 3-6-3v-7l6-3zM2 4.5l6 3 6-3M8 7.5v7",
  download: "M8 2v7m0 0l3-3m-3 3L5 6M3 13h10",
};

export function Icon({ name, size = 14, className = "", style }) {
  const d = ICONS[name] || ICONS.box;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

export function Pill({ kind = "warm", children }) {
  return <span className={`pill ${kind}`}>{children}</span>;
}

export function ModulePill({ code }) {
  return <span className={`module-pill ${code || ""}`}>{code || "—"}</span>;
}

// ── numbers in ──────────────────────────────────────────────────────────────
// We never write a thousands separator, so a comma can only ever mean a decimal
// point. `type="number"` can't hold one — the browser reports value "" and the
// typed digits vanish silently — so these fields are text + inputMode="decimal"
// (keeps the numeric keypad on touch) and we rewrite the comma as it's typed.
export function NumInput({ value, onChange, className = "input mono", ...rest }) {
  return (
    <input
      {...rest}
      type="text"
      inputMode="decimal"
      className={className}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value.replace(",", "."))}
    />
  );
}

// The single parse helper for anything typed into a NumInput. Blank → null so a
// cleared field clears the stored value; a comma that arrived by paste (never
// through onChange) is still handled; garbage → null rather than NaN.
export const toNum = (v) => {
  if (v === "" || v == null) return null;
  const n = Number(String(v).replace(",", "."));
  return Number.isFinite(n) ? n : null;
};

// ── formatters ──────────────────────────────────────────────────────────────
export const fmtEUR = (v) =>
  v == null ? "—" : "€ " + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const fmtEURcompact = (v) => {
  if (v == null) return "—";
  if (v >= 1000) return "€ " + (v / 1000).toFixed(1) + "k";
  return "€ " + v.toFixed(2);
};

export const fmtPct = (v, dp = 0) => (v == null ? "—" : (v * 100).toFixed(dp) + "%");

export const fmtWeight = (g) => {
  if (g == null) return "—";
  return g >= 1000 ? (g / 1000).toFixed(2) + " kg" : Math.round(g) + " g";
};
