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

export function Pill({ kind = "warm", title, children }) {
  return <span className={`pill ${kind}`} title={title}>{children}</span>;
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

// Compact euros. The scale goes past "k" because the COGS ladder deals in whole-year
// overhead pools: at @10k the fixtures reach EUR 218,170,000, which as "218170.0k" is a
// number nobody can read at a glance. Below EUR 1,000 it stays exact to the cent, because
// that is the range a part price lives in and rounding one would lose real information.
export const fmtEURcompact = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return "€ " + (v / 1e9).toFixed(2) + "bn";
  if (a >= 1e6) return "€ " + (v / 1e6).toFixed(1) + "M";
  if (a >= 1000) return "€ " + (v / 1000).toFixed(1) + "k";
  return "€ " + v.toFixed(2);
};

export const fmtPct = (v, dp = 0) => (v == null ? "—" : (v * 100).toFixed(dp) + "%");

export const fmtWeight = (g) => {
  if (g == null) return "—";
  return g >= 1000 ? (g / 1000).toFixed(2) + " kg" : Math.round(g) + " g";
};

// ── the COGS ladder's shared primitives (M7) ────────────────────────────────
// Which rung a facility row lands in, keyed by its basis. The backend serves the same
// mapping on every breakdown line; this is here so a row schema alone is enough to tag a
// matrix row, with no extra round trip.
export const LAYER_OF_BASIS = {
  direct: "L1", scrap: "L1",
  // `area` is priced (x rent) rather than reference-only, and amortised tooling is
  // depreciation — both land in the overhead layer.
  oh: "L2", fte: "L2", area: "L2", toolTotal: "L2",
  post: "L3", warranty: "L3",
};

const LAYER_TITLE = {
  L1: "Layer 1 — direct manufacturing cost, per plant.",
  L2: "Layer 2 — the whole-year overhead pool, divided by the plants built that year.",
  L3: "Layer 3 — added after the plant leaves the hall.",
};

export function LayerTag({ basis }) {
  const l = LAYER_OF_BASIS[basis];
  if (!l) return <span className="layer-tag none" title="Carried for display — never costed.">—</span>;
  return <span className="layer-tag" title={LAYER_TITLE[l]}>{l}</span>;
}

// A rate row inherits DOWNWARD when locked; a quantity row is counted once at the top.
// The two directions are the whole point of the control, so the title says which one this
// row does rather than explaining locking in the abstract.
export function LockToggle({ locked, inherits, disabled, soloNote, onChange }) {
  const what = soloNote
    ? (locked
        ? "This row is locked, which no longer changes anything: with no sub-items every row is entered here anyway. Click to clear the lock."
        : "Locking applies when a facility has sub-items — it decides which level a row is entered on. With none, everything is entered here already.")
    : inherits
    ? "Lock: enter this rate once on the facility and every sub-item is costed at it."
    : "Lock: enter this once on the facility and count it once — the sub-items contribute nothing for this row.";
  return (
    <button
      type="button"
      className={`lock ${locked ? "on" : ""}`}
      role="checkbox"
      aria-checked={locked}
      disabled={disabled}
      title={locked ? `${what}\n\nClick to unlock — sub-item values are left exactly as they are.` : what}
      onClick={() => !disabled && onChange(!locked)}
    >
      {locked && <Icon name="check" size={10} />}
    </button>
  );
}

// label · source facility · value, with a hairline-ruled total. The lines come from the
// backend already computed, so nothing here adds anything up except the total it is asked
// to display — and that is passed in, not summed.
export function BreakdownList({ lines, total, totalLabel = "Total", fmt, empty = "Nothing entered yet." }) {
  if (!lines?.length) return <p className="muted" style={{ margin: "6px 0 0", fontSize: 12.5 }}>{empty}</p>;
  return (
    <div className="breakdown">
      {lines.map((l, i) => (
        <div className="breakdown-row" key={`${l.src}-${l.row_key}-${i}`}>
          <span className="breakdown-label">{l.label}</span>
          <span className="breakdown-src mono" title="The facility this figure is entered on">{l.src}</span>
          {/* A line is not always money: scrap and warranty contribute a rate, and showing
              "EUR 1.50" for a 1.5% yield loss would read as a cost rather than a loss. */}
          <span className="breakdown-val mono">
            {l.unit === "pct" ? `${Number(l.value).toFixed(2)}%` : fmt(l.value)}
          </span>
        </div>
      ))}
      {total != null && (
        <div className="breakdown-row total">
          <span className="breakdown-label">{totalLabel}</span>
          <span />
          <span className="breakdown-val mono">{fmt(total)}</span>
        </div>
      )}
    </div>
  );
}
