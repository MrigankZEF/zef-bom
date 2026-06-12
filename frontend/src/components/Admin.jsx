import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Icon, ModulePill } from "./ui";

const CATEGORIES = [
  { key: "supplier", label: "Suppliers" },
  { key: "material", label: "Materials" },
  { key: "country", label: "Countries" },
  { key: "module", label: "Modules" },
  { key: "assembly_cost_type", label: "Assembly cost types" },
];

export default function Admin({ onOpenPart, onChanged }) {
  const [sub, setSub] = useState("reference");

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Admin</div>
          <h1 className="page-title">Admin</h1>
          <p className="page-sub">Manage the dropdown lists used across the tool, and restore archived items.</p>
        </div>
        <div className="page-actions">
          <div className="segmented-mini">
            <button className={sub === "users" ? "on" : ""} onClick={() => setSub("users")}>Users</button>
            <button className={sub === "reference" ? "on" : ""} onClick={() => setSub("reference")}>Reference data</button>
            <button className={sub === "archive" ? "on" : ""} onClick={() => setSub("archive")}>Archive</button>
            <button className={sub === "import" ? "on" : ""} onClick={() => setSub("import")}>Catalog import</button>
          </div>
        </div>
      </div>
      {sub === "users" && <Users />}
      {sub === "reference" && <Reference />}
      {sub === "archive" && <Archive onOpenPart={onOpenPart} onChanged={onChanged} />}
      {sub === "import" && <CatalogImport onChanged={onChanged} />}
    </div>
  );
}

const ROLES = ["admin", "editor", "viewer"];

function Users() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("viewer");

  const load = () => api.listUsers().then(setRows).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const add = async () => {
    if (!email.trim()) return;
    try { await api.addUser({ email: email.trim().toLowerCase(), name: name.trim() || null, role }); setEmail(""); setName(""); load(); }
    catch (e) { setError(e.message); }
  };
  const changeRole = async (em, r) => { try { await api.setUserRole(em, r); load(); } catch (e) { setError(e.message); } };
  const remove = async (em) => { if (!window.confirm(`Remove ${em}'s access?`)) return; try { await api.removeUser(em); load(); } catch (e) { setError(e.message); } };

  if (error && !rows) return <p className="err">{error}{error.includes("403") || error.toLowerCase().includes("admin") ? " — only admins can manage users." : ""}</p>;
  if (!rows) return <p className="muted">Loading…</p>;

  return (
    <div>
      {error && <p className="err">{error}</p>}
      <div className="card" style={{ maxWidth: 720, padding: 0, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1.5fr 110px 70px", gap: 8, padding: 14, borderBottom: "1px solid var(--hair)", alignItems: "end" }}>
          <div><span className="input-label">Email (must be @zeroemissionfuels.com)</span><input className="input" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@zeroemissionfuels.com" /></div>
          <div><span className="input-label">Name (optional)</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div><span className="input-label">Role</span><select className="select" value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></div>
          <button className="btn" onClick={add}><Icon name="check" /> add</button>
        </div>
        {rows.map((u) => (
          <div key={u.email} style={{ display: "grid", gridTemplateColumns: "1fr 110px 70px", gap: 8, padding: "9px 14px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13 }}>
            <span>{u.name ? <strong>{u.name}</strong> : null} <span style={{ color: "var(--ink-3)", fontFamily: "var(--font-mono)", fontSize: 11.5 }}>{u.email}</span></span>
            <select className="select" value={u.role} onChange={(e) => changeRole(u.email, e.target.value)}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
            <button className="btn ghost sm danger" onClick={() => remove(u.email)}>remove</button>
          </div>
        ))}
        {rows.length === 0 && <div style={{ padding: 16, color: "var(--ink-3)" }}>No users yet.</div>}
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
        <strong>admin</strong> = full access + manage users · <strong>editor</strong> = can change BOM data · <strong>viewer</strong> = read-only.
        People not listed here can't sign in at all.
      </p>
    </div>
  );
}

function Reference() {
  const [cat, setCat] = useState("supplier");
  const [rows, setRows] = useState([]);
  const [val, setVal] = useState("");
  const [label, setLabel] = useState("");
  const [rate, setRate] = useState("");
  const [error, setError] = useState(null);
  const catLabel = cat === "assembly_cost_type" ? "cost type" : cat;

  const load = () => api.reference(cat).then(setRows).catch((e) => setError(e.message));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [cat]);

  const add = async () => {
    if (!val.trim()) return;
    const body = { category: cat, value: val.trim(), label: label.trim() || null };
    if (cat === "assembly_cost_type") { if (rate === "") return; body.meta = { rate_eur_h: Number(rate) }; }
    try { await api.addReference(body); setVal(""); setLabel(""); setRate(""); load(); }
    catch (e) { setError(e.message); }
  };
  const remove = async (id) => { try { await api.deleteReference(id); load(); } catch (e) { setError(e.message); } };

  return (
    <div>
      <div className="segmented-mini" style={{ marginBottom: 16 }}>
        {CATEGORIES.map((c) => <button key={c.key} className={cat === c.key ? "on" : ""} onClick={() => setCat(c.key)}>{c.label}</button>)}
      </div>
      {error && <p className="err">{error}</p>}
      <div className="card" style={{ maxWidth: 640, padding: 0, overflow: "hidden" }}>
        <div style={{ display: "flex", gap: 8, padding: 14, borderBottom: "1px solid var(--hair)", alignItems: "end" }}>
          <div style={{ flex: 1 }}><span className="input-label">New {catLabel}</span><input className="input" value={val} onChange={(e) => setVal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} /></div>
          {cat === "module" && <div style={{ flex: 1 }}><span className="input-label">Label (optional)</span><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></div>}
          {cat === "assembly_cost_type" && <div style={{ width: 110 }}><span className="input-label">Rate €/hour</span><input className="input mono" type="number" value={rate} onChange={(e) => setRate(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} /></div>}
          <button className="btn" onClick={add}><Icon name="check" /> add</button>
        </div>
        {rows.map((r) => (
          <div key={r.id} style={{ display: "flex", gap: 10, padding: "8px 14px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13 }}>
            <span style={{ flex: 1 }}>{r.value}{r.label ? <span style={{ color: "var(--ink-3)" }}> — {r.label}</span> : ""}{r.meta?.region ? <span style={{ color: "var(--ink-3)", fontFamily: "var(--font-mono)", fontSize: 11 }}> · {r.meta.region}</span> : ""}{r.meta?.rate_eur_h != null ? <span style={{ color: "var(--ink-3)", fontFamily: "var(--font-mono)", fontSize: 11 }}> · €{r.meta.rate_eur_h}/h</span> : ""}</span>
            <button className="btn ghost sm danger" title="Remove" onClick={() => remove(r.id)}><Icon name="close" size={11} /></button>
          </div>
        ))}
        {rows.length === 0 && <div style={{ padding: 16, color: "var(--ink-3)" }}>None yet.</div>}
      </div>
    </div>
  );
}

function CatalogImport({ onChanged }) {
  const fileRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const run = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) { setError("Pick the ZEF BOM inventory .xlsx first."); return; }
    if (!window.confirm("This DELETES all current items, BOMs and costs, then rebuilds the catalog from this Excel. Users and reference lists are kept. This cannot be undone. Continue?")) return;
    setBusy(true); setError(null); setResult(null);
    try { const r = await api.importCatalog(f); setResult(r); onChanged?.(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ maxWidth: 640, padding: 18 }}>
      <div className="card-head"><span className="card-title">Wipe &amp; import catalog from Excel</span></div>
      <p style={{ fontSize: 13, color: "var(--ink-2)", margin: "4px 0 12px" }}>
        Upload the <strong>ZEF BOM inventory</strong> spreadsheet (Sheet1). This{" "}
        <strong style={{ color: "var(--accent)" }}>deletes all current items, BOMs and costs</strong> and rebuilds the catalog
        from the sheet — codes, names, and the 10k min/likely/max costs. Users, suppliers, materials and cost types are kept.
        Then re-import your BOMs on top.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input ref={fileRef} type="file" accept=".xlsx,.xls" className="input" style={{ paddingTop: 6, flex: 1 }} />
        <button className="btn danger" onClick={run} disabled={busy}>{busy ? "Importing…" : "Wipe & import"}</button>
      </div>
      {error && <p className="err" style={{ marginTop: 10 }}>{error}</p>}
      {result && (
        <p style={{ marginTop: 10, fontSize: 13, color: "var(--ok)" }}>
          ✓ Wiped and seeded <strong>{result.items_created}</strong> items ({result.with_10k_cost} with a 10k cost).
          The catalog is fresh — Browse is empty until you import a BOM.
        </p>
      )}
    </div>
  );
}

function Archive({ onOpenPart, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = () => api.archive().then(setData).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const restoreItem = async (id) => { try { await api.restoreItem(id); load(); onChanged?.(); } catch (e) { setError(e.message); } };
  const restoreLink = async (p, c) => { try { await api.restoreLink(p, c); load(); onChanged?.(); } catch (e) { setError(e.message); } };
  const purgeItem = async (id) => {
    if (!window.confirm(`Permanently delete ${id} and all its costs/links? This CANNOT be undone.`)) return;
    try { await api.purgeItem(id); load(); onChanged?.(); } catch (e) { setError(e.message); }
  };
  const purgeLink = async (p, c) => {
    if (!window.confirm(`Permanently delete the link ${p} → ${c}? This CANNOT be undone.`)) return;
    try { await api.purgeLink(p, c); load(); onChanged?.(); } catch (e) { setError(e.message); }
  };

  if (error) return <p className="err">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div className="row-2" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div className="card-head" style={{ padding: "14px 18px 10px" }}><span className="card-title">Archived items · {data.items.length}</span></div>
        {data.items.map((it) => (
          <div key={it.item_id} style={{ display: "grid", gridTemplateColumns: "84px 1fr 56px 64px 60px", gap: 8, padding: "8px 18px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 13 }}>
            <span className="mono" style={{ fontSize: 12, cursor: "pointer" }} title="Open details" onClick={() => onOpenPart?.(it.item_id)}>{it.item_id}</span>
            <span style={{ cursor: "pointer" }} title="Open details" onClick={() => onOpenPart?.(it.item_id)}>{it.item_name}</span>
            <ModulePill code={it.module_code} />
            <button className="btn ghost sm" onClick={() => restoreItem(it.item_id)}>restore</button>
            <button className="btn ghost sm danger" onClick={() => purgeItem(it.item_id)}>delete</button>
          </div>
        ))}
        {data.items.length === 0 && <div style={{ padding: 16, color: "var(--ink-3)" }}>No archived items.</div>}
      </div>
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div className="card-head" style={{ padding: "14px 18px 10px" }}><span className="card-title">Removed links · {data.links.length}</span></div>
        {data.links.map((l, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 64px 60px", gap: 8, padding: "8px 18px", borderTop: "1px solid var(--hair-faint)", alignItems: "center", fontSize: 12.5 }}>
            <span><span className="mono">{l.parent}</span> → <span className="mono">{l.child}</span> <span style={{ color: "var(--ink-3)" }}>({l.child_name})</span></span>
            <button className="btn ghost sm" onClick={() => restoreLink(l.parent, l.child)}>restore</button>
            <button className="btn ghost sm danger" onClick={() => purgeLink(l.parent, l.child)}>delete</button>
          </div>
        ))}
        {data.links.length === 0 && <div style={{ padding: 16, color: "var(--ink-3)" }}>No removed links.</div>}
      </div>
    </div>
  );
}
