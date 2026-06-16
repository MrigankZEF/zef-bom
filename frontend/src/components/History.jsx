import { useEffect, useState } from "react";
import { api } from "../api";
import { Pill } from "./ui";

const FILTERS = [
  { key: "", label: "All" },
  { key: "item", label: "Items" },
  { key: "bom_link", label: "Structure" },
  { key: "decided_cost", label: "Cost" },
  { key: "cost_evidence", label: "Evidence" },
];

const toneFor = (t) => (t === "create" ? "ok" : t === "remove" ? "accent" : "info");

export default function History({ onOpenPart, version }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    api.history(filter || undefined).then(setRows).catch((e) => setError(e.message));
  }, [filter, version]);

  if (error) return <div className="page"><p className="err">{error}</p></div>;

  const s = q.trim().toLowerCase();
  const shown = !rows ? [] : !s ? rows : rows.filter((h) =>
    [h.entity_id, h.field_changed, h.old_value, h.new_value, h.changed_by, h.entity_type, h.change_type]
      .some((v) => String(v ?? "").toLowerCase().includes(s)));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Audit</div>
          <h1 className="page-title">History</h1>
          <p className="page-sub">Every change, append-only — who changed what, when. The same log powers per-item history and "BOM as of date X".</p>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center", flexWrap: "wrap" }}>
        {FILTERS.map((f) => (
          <button key={f.key} className="btn ghost sm"
            style={{ background: filter === f.key ? "var(--ink)" : "transparent", color: filter === f.key ? "var(--bg)" : "var(--ink)", borderColor: filter === f.key ? "var(--ink)" : "var(--hair-strong)" }}
            onClick={() => setFilter(f.key)}>{f.label}</button>
        ))}
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Search code, field, value, user…" style={{ marginLeft: "auto", maxWidth: 320 }} />
      </div>

      {!rows ? <p className="muted">Loading…</p> : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          {shown.map((h) => {
            const isItem = h.entity_type === "item" || h.entity_type === "decided_cost" || h.entity_type === "cost_evidence" || h.entity_type === "field_value";
            return (
              <div key={h.id}
                onClick={() => isItem && /^[A-Z]/.test(h.entity_id) && onOpenPart(h.entity_id)}
                style={{ display: "grid", gridTemplateColumns: "150px 90px 1fr 150px", gap: 10, padding: "9px 16px", borderBottom: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 12.5, cursor: isItem ? "pointer" : "default" }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>{(h.changed_at || "").slice(0, 16).replace("T", " ")}</span>
                <span><Pill kind={toneFor(h.change_type)}>{h.change_type}</Pill></span>
                <span>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}>{h.entity_id}</span>
                  {" · "}
                  <strong>{h.field_changed || h.entity_type}</strong>
                  {h.old_value != null && <span style={{ color: "var(--ink-3)" }}> {h.old_value} →</span>}{" "}
                  <span>{h.new_value ?? ""}</span>
                </span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)", textAlign: "right" }}>{h.changed_by}</span>
              </div>
            );
          })}
          {shown.length === 0 && <div style={{ padding: 20, color: "var(--ink-3)" }}>{rows.length ? "No matches." : "No changes recorded."}</div>}
        </div>
      )}
    </div>
  );
}
