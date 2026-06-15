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

// Top-of-review explainer: how each kind of node is handled. Collapsible; nothing is
// written until you click Approve.
function HowItWorks() {
  const [open, setOpen] = useState(true);
  const ROWS = [
    ["Known part", "number + name both match the catalog", "linked into the tree — no change to the part"],
    ["Brand-new part", "number + name are both new", "created in the catalog (a fresh part)"],
    ["Number drifted", "the number doesn’t match, but the name matches an existing part", "linked to that catalog part — numbering reconciled, no duplicate"],
    ["Number collision", "the number belongs to a different part, and the name is new", "you choose: rename the existing part, or add as a new part"],
  ];
  return (
    <div className="card" style={{ marginBottom: 16, padding: 0, overflow: "hidden" }}>
      <button onClick={() => setOpen((v) => !v)}
        style={{ width: "100%", border: 0, background: "transparent", cursor: "pointer",
          display: "flex", alignItems: "center", gap: 8, padding: "12px 16px", textAlign: "left" }}>
        <Icon name={open ? "chevD" : "chevR"} size={12} />
        <strong style={{ fontSize: 13 }}>How this review works</strong>
        <span style={{ flex: 1 }} />
        <span className="card-meta">nothing is saved until you click Approve</span>
      </button>
      {open && (
        <div style={{ borderTop: "1px solid var(--hair)" }}>
          {ROWS.map(([what, when, then]) => (
            <div key={what} style={{ display: "grid", gridTemplateColumns: "150px 1fr 1fr",
              gap: 12, padding: "9px 16px", borderBottom: "1px solid var(--hair-faint)", fontSize: 12.5 }}>
              <strong>{what}</strong>
              <span style={{ color: "var(--ink-3)" }}>{when}</span>
              <span>→ {then}</span>
            </div>
          ))}
          <div style={{ padding: "9px 16px", fontSize: 11.5, color: "var(--ink-3)" }}>
            After applying, the naming rules re-run automatically: a part used across two or more
            systems becomes <Mono>UN…</Mono>, otherwise it takes its system’s code.
          </div>
        </div>
      )}
    </div>
  );
}

export default function Uploads({ onApplied }) {
  const [step, setStep] = useState("list");   // list | upload | diff
  const [batches, setBatches] = useState([]);
  const [diff, setDiff] = useState(null);      // {id, status, diff, counts}
  const [notes, setNotes] = useState("");
  const [isTop, setIsTop] = useState(true);
  const [attachTo, setAttachTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [decisions, setDecisions] = useState({});  // {reusedNumber: "new"|"rename"|"skip"}
  const [reviews, setReviews] = useState({});       // {nodeText: {action, module, type, match}}
  const [modules, setModules] = useState(["UN"]);
  const [catItems, setCatItems] = useState([]);     // catalog, for "match existing" search
  const fileRef = useRef(null);

  const loadList = () => api.listUploads().then(setBatches).catch((e) => setError(e.message));
  useEffect(() => { loadList(); }, []);
  // Module choices + catalog (for resolving review items): UN + system codes + admin-added.
  useEffect(() => {
    Promise.all([api.catalog().catch(() => []), api.reference("module").catch(() => [])])
      .then(([cat, ref]) => {
        setCatItems(cat);
        const s = new Set(["UN"]);
        cat.forEach((r) => r.module_code && s.add(r.module_code));
        ref.forEach((m) => m.value && s.add(String(m.value).toUpperCase()));
        setModules([...s].sort((a, b) => (a === "UN" ? -1 : b === "UN" ? 1 : a.localeCompare(b))));
      });
  }, []);

  const openDiff = async (id) => {
    setError(null);
    try { const b = await api.getUpload(id); setDiff(b); setDecisions({}); setReviews({}); setStep("diff"); }
    catch (e) { setError(e.message); }
  };

  const doUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) { setError("Pick an OPML file first."); return; }
    setBusy(true); setError(null);
    try {
      const res = await api.createUpload(file, { notes, isTopLevel: isTop, attachTo: isTop ? null : (attachTo || null) });
      setDiff(res); setDecisions({}); setReviews({}); setStep("diff"); setNotes(""); loadList();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const approve = async () => {
    setBusy(true); setError(null);
    // Default any number-collision the user didn't touch to "add as new".
    const eff = {};
    (diff?.diff?.conflicts || []).forEach((c) => { if (c.number) eff[c.number] = decisions[c.number] || "new"; });
    try { await api.approveUpload(diff.id, eff, reviews); onApplied?.(); setStep("list"); loadList(); }
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
            {!isTop && <ParentPicker value={attachTo} onChange={setAttachTo} setError={setError} />}
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

      <HowItWorks />

      <div className="kpi-grid" style={{ gridTemplateColumns: "repeat(7,1fr)" }}>
        <KPI label="New parts" v={cn.new_parts} accent />
        <KPI label="Matched by name" v={cn.name_matched} />
        <KPI label="Renamed" v={cn.renamed} />
        <KPI label="Links +" v={cn.added} />
        <KPI label="Links −" v={cn.removed} />
        <KPI label="Qty Δ" v={cn.qty_changed} />
        <KPI label="Unchanged" v={cn.unchanged} />
      </div>

      {(cn.conflicts > 0 || cn.needs_review > 0 || cn.merges > 0) && (
        <div className="card" style={{ marginBottom: 16, borderColor: "var(--warn)" }}>
          <strong style={{ color: "var(--warn)" }}>Needs your choice:</strong>{" "}
          {cn.conflicts ? `${cn.conflicts} number collision(s) — pick rename or new below · ` : ""}{cn.needs_review ? `${cn.needs_review} need review · ` : ""}{cn.merges ? `${cn.merges} possible name-merge(s)` : ""}
        </div>
      )}

      <Section title="New parts" count={cn.new_parts}>
        {d.new_parts?.map((p) => (
          <Row key={p.number}><Mono>{p.number}</Mono><span style={{ flex: 1 }}>{p.name}</span>
            <Pill kind={p.allocated ? "warm" : "info"}>{p.allocated ? "auto-numbered" : "from Miro"}</Pill>
            <Pill kind={p.confidence === "high" ? "ok" : "warm"}>{p.confidence}</Pill></Row>
        ))}
      </Section>
      <Section title="Matched by name" count={cn.name_matched} tone="var(--ok)">
        {cn.name_matched > 0 && (
          <Row><span style={{ flex: 1, color: "var(--ink-3)", fontSize: 11.5 }}>
            These Miro nodes carried a part number that didn’t match the catalog, but their
            name did — so they were linked to the existing catalog item (numbering reconciled,
            no duplicates created).
          </span></Row>
        )}
        {d.name_matched?.map((m, i) => (
          <Row key={i}><Mono>{m.from}</Mono><span style={{ color: "var(--ink-3)" }}>→</span>
            <Mono>{m.to}</Mono><span style={{ flex: 1 }}>{m.name}</span></Row>
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
      <Section title="Number collisions — your choice" count={cn.conflicts} tone="var(--accent)">
        {cn.conflicts > 0 && (
          <Row><span style={{ flex: 1, color: "var(--ink-3)", fontSize: 11.5 }}>
            Miro reused a number that already belongs to a different catalog part, and the new
            name isn’t in the catalog. Pick what each one is. Default is <strong>Add as new</strong>.
          </span></Row>
        )}
        {d.conflicts?.map((c) => {
          const choice = decisions[c.number] || "new";
          const set = (v) => setDecisions((p) => ({ ...p, [c.number]: v }));
          return (
            <Row key={c.number}>
              <Mono>{c.number}</Mono>
              <span style={{ flex: 1 }}>
                Miro: <strong>{c.name}</strong>
                <span style={{ color: "var(--ink-3)" }}> · catalog has “{c.catalog_name || "—"}”</span>
              </span>
              <div style={{ display: "inline-flex", gap: 4 }}>
                {[["new", "Add as new"], ["rename", `Rename ${c.number}`], ["skip", "Skip"]].map(([v, label]) => (
                  <button key={v} className={`btn sm ${choice === v ? "" : "ghost"}`} onClick={() => set(v)}
                    title={v === "new" ? "Create a brand-new part with a fresh number; the catalog item keeps its name"
                      : v === "rename" ? `Rename catalog ${c.number} to “${c.name}”`
                      : "Ignore this node"}>{label}</button>
                ))}
              </div>
            </Row>
          );
        })}
      </Section>
      <Section title="Needs your review" count={cn.needs_review} tone="var(--warn)">
        {cn.needs_review > 0 && (
          <Row><span style={{ flex: 1, color: "var(--ink-3)", fontSize: 11.5 }}>
            The importer couldn’t place these on its own. Resolve each here — no need to go back to Miro.
          </span></Row>
        )}
        {d.needs_review?.map((c) => {
          const r = reviews[c.key] || {};
          const upd = (patch) => setReviews((p) => ({ ...p, [c.key]: { ...(p[c.key] || {}), ...patch } }));
          const act = r.action;
          return (
            <div key={c.key} style={{ padding: "10px 16px", borderBottom: "1px solid var(--hair-faint)", fontSize: 13 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ flex: 1, minWidth: 220 }}><strong>{c.name || c.cell}</strong>
                  <span style={{ color: "var(--ink-3)", fontSize: 11.5 }}> · {c.issue}</span></span>
                <div style={{ display: "inline-flex", gap: 4 }}>
                  {[["create", "Create new"], ["match", "Match existing"], ["skip", "Skip"]].map(([v, label]) => (
                    <button key={v} className={`btn sm ${act === v ? "" : "ghost"}`}
                      onClick={() => upd({ action: v, module: r.module || c.module_guess || "UN", type: r.type || c.type_guess || "part" })}>{label}</button>
                  ))}
                </div>
              </div>
              {act === "create" && (
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, paddingLeft: 4 }}>
                  <span style={{ color: "var(--ink-3)", fontSize: 12 }}>as</span>
                  <select className="select" style={{ height: 30 }} value={r.module || c.module_guess || "UN"} onChange={(e) => upd({ module: e.target.value })}>
                    {modules.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <select className="select" style={{ height: 30 }} value={r.type || c.type_guess || "part"} onChange={(e) => upd({ type: e.target.value })}>
                    <option value="part">part</option><option value="assembly">assembly</option>
                  </select>
                  <span style={{ color: "var(--ink-3)", fontSize: 11.5 }}>→ a new {(r.module || c.module_guess || "UN")} code is allocated</span>
                </div>
              )}
              {act === "match" && (() => {
                const sel = catItems.find((it) => it.item_id === r.match);
                if (sel) return (
                  <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 8, paddingLeft: 4, fontSize: 13 }}>
                    <span style={{ color: "var(--ink-3)", fontSize: 12 }}>links to</span>
                    <Mono>{sel.item_id}</Mono><span>{sel.item_name}</span>
                    <button className="btn ghost sm" onClick={() => upd({ match: undefined })}>change</button>
                  </div>
                );
                const q = (r.q || "").trim().toLowerCase();
                const results = q ? catItems.filter((it) => it.item_id.toLowerCase().includes(q) || (it.item_name || "").toLowerCase().includes(q)).slice(0, 6) : [];
                return (
                  <div style={{ marginTop: 8, paddingLeft: 4 }}>
                    <input className="input" style={{ height: 30, maxWidth: 340 }} placeholder="search the catalog by code or name…"
                      value={r.q || ""} onChange={(e) => upd({ q: e.target.value })} />
                    <div style={{ marginTop: 4 }}>
                      {results.map((it) => (
                        <div key={it.item_id} onClick={() => upd({ match: it.item_id, q: "" })}
                          style={{ cursor: "pointer", padding: "5px 8px", fontSize: 12.5, display: "flex", gap: 10, alignItems: "center", borderRadius: 4 }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--hair-faint)")}
                          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
                          <Mono>{it.item_id}</Mono><span style={{ flex: 1 }}>{it.item_name}</span>
                        </div>
                      ))}
                      {q && results.length === 0 && <div style={{ fontSize: 12, color: "var(--ink-3)", padding: "4px 8px" }}>No catalog item matches “{r.q}”.</div>}
                    </div>
                  </div>
                );
              })()}
            </div>
          );
        })}
      </Section>
      <Section title="Possible name-merges" count={cn.merges} tone="var(--warn)">
        {d.merges?.map((m, i) => <Row key={i}><strong>{m.name}</strong><span style={{ color: "var(--ink-3)", fontSize: 11.5 }}>same name, {m.child_variants?.length} different child sets — split in Miro if these are distinct</span></Row>)}
      </Section>

      {pending && (
        <div style={{ position: "sticky", bottom: 16, display: "flex", flexDirection: "column", gap: 8,
          background: "var(--bg-raised)", border: `1px solid ${error ? "var(--accent)" : "var(--hair-strong)"}`, borderRadius: 6, padding: "12px 16px", boxShadow: "var(--shadow-2)" }}>
          {error && <div className="err" style={{ margin: 0, fontSize: 13 }}>⚠ {error}</div>}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12.5, color: "var(--ink-2)" }}>Approving writes all changes at once + an audit entry. Resolve any review/collision rows above first.</span>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn ghost danger sm" onClick={reject} disabled={busy}>Reject</button>
              <button className="btn" onClick={approve} disabled={busy}><Icon name="check" /> {busy ? "Applying…" : "Approve batch"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ParentPicker({ value, onChange, setError }) {
  const [cat, setCat] = useState(null);
  const [q, setQ] = useState("");
  useEffect(() => { api.catalog().then(setCat).catch((e) => setError(e.message)); }, []);
  const s = q.trim().toLowerCase();
  const results = !s ? [] : (cat || []).filter((r) => r.item_id.toLowerCase().includes(s) || (r.item_name || "").toLowerCase().includes(s)).slice(0, 20);
  if (value) {
    const item = (cat || []).find((r) => r.item_id === value);
    return (
      <div>
        <span className="input-label">Attach this sub-BOM under</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
          <Mono>{value}</Mono><span style={{ flex: 1 }}>{item?.item_name || ""}</span>
          <button className="btn ghost sm" onClick={() => onChange("")}>change</button>
        </div>
      </div>
    );
  }
  return (
    <div>
      <span className="input-label">Attach this sub-BOM under (search a parent)</span>
      <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search code or name…" />
      {results.length > 0 && (
        <div className="card" style={{ marginTop: 4, padding: 0, maxHeight: 180, overflowY: "auto" }}>
          {results.map((r) => (
            <div key={r.item_id} onClick={() => { onChange(r.item_id); setQ(""); }}
              style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: 8, padding: "6px 10px", borderTop: "1px solid var(--hair-faint)", cursor: "pointer", fontSize: 13 }}>
              <Mono>{r.item_id}</Mono><span>{r.item_name}</span>
            </div>
          ))}
        </div>
      )}
      <p style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>Its root will hang under this item; codes re-adjust to the combined usage.</p>
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
