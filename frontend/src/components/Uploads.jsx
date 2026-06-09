import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Icon, Pill } from "./ui";

function Section({ title, count, tone, children }) {
  if (!count) return null;
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden", marginBottom: 16 }}>
      <div className="card-head" style={{ padding: "12px 16px", marginBottom: 0,
        borderBottom: "1px solid var(--hair)" }}>
        <span className="card-title" style={{ color: tone }}>{title}</span>
        <span className="card-meta">{count}</span>
      </div>
      <div>{children}</div>
    </div>
  );
}

const Row = ({ children }) => (
  <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--hair-faint)", fontSize: 13,
    display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>{children}</div>
);
const Mono = ({ children }) => (
  <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{children}</span>
);

export default function Uploads({ onApplied }) {
  const [step, setStep] = useState("list");   // list | upload | diff
  const [batches, setBatches] = useState([]);
  const [diff, setDiff] = useState(null);      // {id, status, diff, counts}
  const [notes, setNotes] = useState("");
  const [isTop, setIsTop] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  const loadList = () => api.listUploads().then(setBatches).catch((e) => setError(e.message));
  useEffect(() => { loadList(); }, []);

  const openDiff = async (id) => {
    setError(null);
    try { const b = await api.getUpload(id); setDiff(b); setStep("diff"); }
    catch (e) { setError(e.message); }
  };

  const doUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) { setError("Pick an OPML file first."); return; }
    setBusy(true); setError(null);
    try {
      const res = await api.createUpload(file, { notes, isTopLevel: isTop });
      setDiff(res); setStep("diff"); setNotes(""); loadList();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const approve = async () => {
    setBusy(true); setError(null);
    try { await api.approveUpload(diff.id); onApplied?.(); setStep("list"); loadList(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const reject = async () => {
    setBusy(true);
    try { await api.rejectUpload(diff.id); setStep("list"); loadList(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  // ── list ──
  if (step === "list") {
    return (
      <div className="page">
        <Head title="OPML uploads" sub="Drop a Miro export to review and apply changes. Every batch stays in history."
          action={<button className="btn" onClick={() => setStep("upload")}><Icon name="box" /> New upload</button>} />
        {error && <p className="err">{error}</p>}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="tbl">
            <thead><tr><th style={{ width: 150 }}>Uploaded</th><th>File</th><th>By</th><th>Summary</th><th style={{ width: 110 }}>Status</th><th style={{ width: 90 }}></th></tr></thead>
            <tbody>
              {batches.map((b) => (
                <tr key={b.id}>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--ink-3)" }}>{(b.uploaded_at || "").slice(0, 16).replace("T", " ")}</td>
                  <td><Mono>{b.source_filename}</Mono></td>
                  <td style={{ fontSize: 12.5 }}>{b.uploaded_by}</td>
                  <td style={{ fontSize: 12 }}>{summarize(b.counts)}</td>
                  <td><StatusPill status={b.status} /></td>
                  <td>{b.status === "pending_review"
                    ? <button className="btn ghost sm" onClick={() => openDiff(b.id)}>Review</button>
                    : <button className="btn ghost sm" onClick={() => openDiff(b.id)}>View</button>}</td>
                </tr>
              ))}
              {batches.length === 0 && <tr><td colSpan={6} style={{ padding: 20, color: "var(--ink-3)" }}>No uploads yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // ── upload ──
  if (step === "upload") {
    return (
      <div className="page">
        <Head title="New OPML upload" sub="Export your Miro mind-map as OPML, then drop it here. The parser diffs it against the live BOM."
          action={<button className="btn ghost" onClick={() => setStep("list")}><Icon name="chevL" /> Back</button>} />
        {error && <p className="err">{error}</p>}
        <div className="card" style={{ maxWidth: 620 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div>
              <span className="input-label">OPML file</span>
              <input ref={fileRef} type="file" accept=".opml,.xml,text/xml" className="input" style={{ paddingTop: 6 }} />
            </div>
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
              <input type="checkbox" checked={isTop} onChange={(e) => setIsTop(e.target.checked)} />
              This is a top-level BOM (mark its root accordingly)
            </label>
            <div>
              <span className="input-label">Notes</span>
              <textarea className="input" style={{ height: 56, padding: 8 }} value={notes}
                onChange={(e) => setNotes(e.target.value)} placeholder="What changed in this export…" />
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button className="btn" onClick={doUpload} disabled={busy}><Icon name="check" /> {busy ? "Parsing…" : "Upload & parse"}</button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── diff ──
  const d = diff.diff || {};
  const cn = d.counts || {};
  const st = d.structural || {};
  const pending = diff.status === "pending_review";
  return (
    <div className="page">
      <Head title="Review diff" sub={`${diff.source_filename || ""} · ${diff.status}`}
        action={<button className="btn ghost" onClick={() => setStep("list")}><Icon name="chevL" /> Back</button>} />
      {error && <p className="err">{error}</p>}

      <div className="kpi-grid" style={{ gridTemplateColumns: "repeat(6,1fr)" }}>
        <KPI label="New parts" v={cn.new_parts} accent />
        <KPI label="Renamed" v={cn.renamed} />
        <KPI label="Links +" v={cn.added} />
        <KPI label="Links −" v={cn.removed} />
        <KPI label="Qty Δ" v={cn.qty_changed} />
        <KPI label="Unchanged" v={cn.unchanged} />
      </div>

      {(cn.conflicts > 0 || cn.needs_review > 0 || cn.merges > 0) && (
        <div className="card" style={{ marginBottom: 16, borderColor: "var(--warn)" }}>
          <strong style={{ color: "var(--warn)" }}>Needs attention:</strong>{" "}
          {cn.conflicts ? `${cn.conflicts} conflict(s) · ` : ""}{cn.needs_review ? `${cn.needs_review} need review · ` : ""}{cn.merges ? `${cn.merges} possible name-merge(s)` : ""}
        </div>
      )}

      <Section title="New parts" count={cn.new_parts}>
        {d.new_parts?.map((p) => (
          <Row key={p.number}><Mono>{p.number}</Mono><span style={{ flex: 1 }}>{p.name}</span>
            <Pill kind={p.allocated ? "warm" : "info"}>{p.allocated ? "auto-numbered" : "from Miro"}</Pill>
            <Pill kind={p.confidence === "high" ? "ok" : "warm"}>{p.confidence}</Pill></Row>
        ))}
      </Section>
      <Section title="Renamed" count={cn.renamed}>
        {d.renamed?.map((r) => (
          <Row key={r.number}><Mono>{r.number}</Mono><span style={{ color: "var(--ink-3)" }}>{r.from}</span> → <strong>{r.to}</strong></Row>
        ))}
      </Section>
      <Section title="Links added" count={cn.added} tone="var(--ok)">
        {st.added?.map((l, i) => <Row key={i}><Mono>{l.parent}</Mono> → <Mono>{l.child}</Mono><span style={{ color: "var(--ink-3)" }}>qty {l.qty}</span></Row>)}
      </Section>
      <Section title="Links removed" count={cn.removed} tone="var(--accent)">
        {st.removed?.map((l, i) => <Row key={i}><Mono>{l.parent}</Mono> → <Mono>{l.child}</Mono><Pill kind="accent">removed</Pill></Row>)}
      </Section>
      <Section title="Quantity changes" count={cn.qty_changed}>
        {st.qty_changed?.map((l, i) => <Row key={i}><Mono>{l.parent}</Mono> → <Mono>{l.child}</Mono><span style={{ color: "var(--ink-3)" }}>qty <strong>{l.from}</strong> → <strong>{l.to}</strong></span></Row>)}
      </Section>
      <Section title="Conflicts" count={cn.conflicts} tone="var(--accent)">
        {d.conflicts?.map((c, i) => <Row key={i}><Mono>{c.number || "—"}</Mono><span style={{ flex: 1 }}>{c.name}</span><span style={{ color: "var(--ink-3)", fontSize: 11.5 }}>{c.issue}</span></Row>)}
      </Section>
      <Section title="Needs review" count={cn.needs_review} tone="var(--warn)">
        {d.needs_review?.map((c, i) => <Row key={i}><span style={{ flex: 1 }}>{c.cell}</span><span style={{ color: "var(--ink-3)", fontSize: 11.5 }}>{c.issue} · guess {c.module_guess || "?"}</span></Row>)}
      </Section>
      <Section title="Possible name-merges" count={cn.merges} tone="var(--warn)">
        {d.merges?.map((m, i) => <Row key={i}><strong>{m.name}</strong><span style={{ color: "var(--ink-3)", fontSize: 11.5 }}>same name, {m.child_variants?.length} different child sets — split in Miro if these are distinct</span></Row>)}
      </Section>

      {pending && (
        <div style={{ position: "sticky", bottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center",
          background: "var(--bg-raised)", border: "1px solid var(--hair-strong)", borderRadius: 6, padding: "12px 16px", boxShadow: "var(--shadow-2)" }}>
          <span style={{ fontSize: 12.5, color: "var(--ink-2)" }}>Approving writes all changes atomically + an audit entry. Conflicts must be fixed in Miro first.</span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn ghost danger sm" onClick={reject} disabled={busy}>Reject</button>
            <button className="btn" onClick={approve} disabled={busy}><Icon name="check" /> {busy ? "Applying…" : "Approve batch"}</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Head({ title, sub, action }) {
  return (
    <div className="page-head">
      <div><div className="page-eyebrow">Uploads</div><h1 className="page-title">{title}</h1><p className="page-sub">{sub}</p></div>
      <div className="page-actions">{action}</div>
    </div>
  );
}
function KPI({ label, v, accent }) {
  return (
    <div className={`kpi ${accent ? "accent" : ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-val">{v ?? 0}</span>
    </div>
  );
}
function StatusPill({ status }) {
  if (status === "approved") return <Pill kind="ok">approved</Pill>;
  if (status === "rejected") return <Pill kind="accent">rejected</Pill>;
  return <Pill kind="warn">review</Pill>;
}
function summarize(c) {
  if (!c) return "—";
  return `${c.new_parts || 0} new · ${c.added || 0}+ ${c.removed || 0}− links · ${c.qty_changed || 0} qty`;
}
