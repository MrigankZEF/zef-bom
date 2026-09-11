import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Icon, ModulePill, NumInput, toNum } from "./ui";
// Uploads lived on the top bar as its own tab. It is an occasional bulk operation in the
// same family as the catalog import and the restore — something you do a few times a
// month, not while working through a BOM — so it belongs here rather than taking one of
// eight places in the main navigation.
import Uploads from "./Uploads.jsx";

const CATEGORIES = [
  { key: "supplier", label: "Suppliers" },
  { key: "material", label: "Materials" },
  { key: "country", label: "Countries" },
  { key: "module", label: "Modules" },
  { key: "link_type", label: "Link types" },
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
          <p className="page-sub">Manage the dropdown lists used across the tool, restore archived items, keep the import duty rates, and run the bulk operations — OPML uploads, catalog import, backup and restore.</p>
        </div>
        <div className="page-actions">
          <div className="segmented-mini">
            <button className={sub === "users" ? "on" : ""} onClick={() => setSub("users")}>Users</button>
            <button className={sub === "reference" ? "on" : ""} onClick={() => setSub("reference")}>Reference data</button>
            <button className={sub === "archive" ? "on" : ""} onClick={() => setSub("archive")}>Archive</button>
            <button className={sub === "backup" ? "on" : ""} onClick={() => setSub("backup")}>Backup</button>
            <button className={sub === "duty" ? "on" : ""} onClick={() => setSub("duty")}>Duty rates</button>
            <button className={sub === "uploads" ? "on" : ""} onClick={() => setSub("uploads")}>OPML uploads</button>
            <button className={sub === "import" ? "on" : ""} onClick={() => setSub("import")}>Catalog import</button>
          </div>
        </div>
      </div>
      {sub === "users" && <Users />}
      {sub === "reference" && <Reference />}
      {sub === "archive" && <Archive onOpenPart={onOpenPart} onChanged={onChanged} />}
      {sub === "backup" && <><Backup /><Restore onChanged={onChanged} /></>}
      {sub === "duty" && <DutyRates />}
      {sub === "uploads" && <Uploads onApplied={onChanged} />}
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
  const catLabel = { assembly_cost_type: "cost type", link_type: "link type" }[cat] || cat;

  const load = () => api.reference(cat).then(setRows).catch((e) => setError(e.message));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [cat]);

  const add = async () => {
    if (!val.trim()) return;
    const body = { category: cat, value: val.trim(), label: label.trim() || null };
    if (cat === "assembly_cost_type") { if (rate === "") return; body.meta = { rate_eur_h: toNum(rate) }; }
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
          {cat === "assembly_cost_type" && <div style={{ width: 110 }}><span className="input-label">Rate €/hour</span><NumInput value={rate} onChange={setRate} onKeyDown={(e) => e.key === "Enter" && add()} /></div>}
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

function Backup() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);
  const [drive, setDrive] = useState(null);   // { enabled, backups: [] }

  const loadBackups = () => api.listBackups().then(setDrive).catch((e) => setError(e.message));
  useEffect(() => { loadBackups(); }, []);

  const download = async () => {
    setBusy(true); setError(null); setDone(false);
    try { await api.downloadBackup(); setDone(true); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const toDrive = async () => {
    setBusy(true); setError(null);
    try { await api.backupToDrive(); await loadBackups(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const fmtDate = (s) => { try { return new Date(s).toLocaleString(); } catch { return s; } };

  return (
    <div className="card" style={{ maxWidth: 680, padding: 18 }}>
      <div className="card-head"><span className="card-title">Backup — full database export</span></div>
      <p style={{ fontSize: 13, color: "var(--ink-2)", margin: "4px 0 12px" }}>
        A complete snapshot of the database as one <strong>.xlsx</strong> workbook — a sheet per table:
        items, BOM links (the full hierarchy), decided costs, cost evidence, assembly labor, custom fields, reference lists,
        uploads, change history and users. File attachments live in Drive and aren’t included here.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn" onClick={download} disabled={busy}>
          <Icon name="download" size={13} /> {busy ? "Working…" : "Backup now (.xlsx)"}
        </button>
        {drive?.enabled && (
          <button className="btn ghost" onClick={toDrive} disabled={busy}>
            <Icon name="box" size={13} /> Back up to Drive now
          </button>
        )}
        {done && <span style={{ fontSize: 12.5, color: "var(--ok)" }}>✓ Backup downloaded.</span>}
      </div>
      {error && <p className="err" style={{ marginTop: 10 }}>{error}</p>}

      <div style={{ marginTop: 16, borderTop: "1px solid var(--hair)", paddingTop: 14 }}>
        <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 8 }}>
          {drive?.enabled
            ? <>Snapshots on Google Drive (a monthly auto-backup runs on its own; the last 12 are kept). A copy is also saved automatically right before a catalog wipe.</>
            : <>Google Drive isn’t configured, so server-side and scheduled backups are off. Use <strong>Backup now</strong> to keep manual copies.</>}
        </div>
        {drive?.enabled && (
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            {(drive.backups || []).length === 0 && (
              <div style={{ padding: 12, fontSize: 12.5, color: "var(--ink-3)" }}>No Drive backups yet.</div>
            )}
            {(drive.backups || []).slice(0, 15).map((b) => (
              <div key={b.name} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px",
                borderBottom: "1px solid var(--hair-faint)", fontSize: 12.5 }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, flex: 1 }}>{b.name}</span>
                <span style={{ color: "var(--ink-3)" }}>{fmtDate(b.created)}</span>
                {b.url && <a className="btn ghost sm" href={b.url} target="_blank" rel="noreferrer">open</a>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Restore({ onChanged }) {
  const fileRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const onPick = () => { setPreview(null); setResult(null); setError(null); };
  const check = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) { setError("Pick a backup .xlsx first."); return; }
    setBusy(true); setError(null); setResult(null); setPreview(null);
    try { setPreview(await api.restorePreview(f)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const run = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) { setError("Pick the file again."); return; }
    if (!window.confirm(`This REPLACES all current BOM data with the backup (${preview?.total_items} items). A pre-restore backup is saved first. Continue?`)) return;
    setBusy(true); setError(null);
    try { const r = await api.restoreBackup(f); setResult(r); setPreview(null); onChanged?.(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const counts = preview?.counts || {};
  return (
    <div className="card" style={{ maxWidth: 680, padding: 18, marginTop: 16 }}>
      <div className="card-head"><span className="card-title">Restore from backup</span></div>
      <p style={{ fontSize: 13, color: "var(--ink-2)", margin: "4px 0 12px" }}>
        Rebuild the database from a backup <strong>.xlsx</strong> (the kind produced above).{" "}
        <strong style={{ color: "var(--accent)" }}>This replaces all current BOM data.</strong>{" "}
        A pre-restore backup is saved to Drive automatically first, so this is reversible.
        Sign-in access (Users) is preserved, not restored. <strong>Check the file first.</strong>
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input ref={fileRef} type="file" accept=".xlsx" className="input" style={{ paddingTop: 6, flex: 1 }} onChange={onPick} />
        <button className="btn" onClick={check} disabled={busy}>{busy && !preview ? "Checking…" : "Check backup"}</button>
      </div>
      {error && <p className="err" style={{ marginTop: 10 }}>{error}</p>}

      {preview && (
        <div className="card" style={{ marginTop: 12, padding: 14 }}>
          {!preview.ok ? (
            <p className="err" style={{ margin: 0, fontSize: 13 }}>✗ {preview.reason}</p>
          ) : (
            <>
              <p style={{ margin: "0 0 8px", fontSize: 13 }}>Preview — <strong>nothing changed yet.</strong></p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: "2px 18px", fontSize: 12.5, color: "var(--ink-2)" }}>
                {Object.entries(counts).map(([k, v]) => (
                  <div key={k} style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>{k}</span><span style={{ fontFamily: "var(--font-mono)" }}>{v}</span>
                  </div>
                ))}
              </div>
              {Object.keys(preview.column_issues || {}).length > 0 && (
                <p style={{ margin: "8px 0 0", fontSize: 11.5, color: "var(--warn)" }}>
                  Some columns differ from the current schema (extra/missing are ignored safely):{" "}
                  {Object.keys(preview.column_issues).join(", ")}.
                </p>
              )}
              <p style={{ margin: "8px 0 0", fontSize: 11.5, color: "var(--ink-3)" }}>{preview.users_note}</p>
              <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 10 }}>
                <button className="btn danger" onClick={run} disabled={busy}>
                  {busy ? "Restoring…" : `Restore ${preview.total_items} items (replace all)`}
                </button>
                <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>A pre-restore backup is saved first.</span>
              </div>
            </>
          )}
        </div>
      )}

      {result && (
        <div style={{ marginTop: 10, fontSize: 13 }}>
          <p style={{ color: "var(--ok)", margin: 0 }}>✓ Restored. Items: {result.counts?.Items ?? "?"}.</p>
          <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--ink-3)" }}>
            {result.backup?.saved
              ? <>A pre-restore backup was saved{result.backup.url ? <> — <a href={result.backup.url} target="_blank" rel="noreferrer">{result.backup.name}</a></> : ` (${result.backup.name})`}.</>
              : <>⚠ No pre-restore Drive backup ({result.backup?.reason || "drive not configured"}).</>}
          </p>
        </div>
      )}
    </div>
  );
}

function CatalogImport({ onChanged }) {
  const fileRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);   // dry-run plan; null until "Check file"
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Picking a new file invalidates any prior preview/result.
  const onPick = () => { setPreview(null); setResult(null); setError(null); };

  const check = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) { setError("Pick the ZEF BOM inventory .xlsx first."); return; }
    setBusy(true); setError(null); setResult(null); setPreview(null);
    try { setPreview(await api.previewImportCatalog(f)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const run = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) { setError("Pick the file again."); return; }
    if (!window.confirm(`This DELETES all ${preview?.current_item_count ?? "current"} items, BOMs and costs, then creates ${preview?.will_create} items from this Excel. Users and reference lists are kept. This cannot be undone. Continue?`)) return;
    setBusy(true); setError(null);
    try { const r = await api.importCatalog(f); setResult(r); setPreview(null); onChanged?.(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const reasons = preview?.skipped_reasons || {};
  const reasonLabel = { bad_or_missing_code: "bad/blank code", missing_name: "blank name", duplicate_in_file: "duplicate in file" };

  return (
    <div className="card" style={{ maxWidth: 640, padding: 18 }}>
      <div className="card-head"><span className="card-title">Wipe &amp; import catalog from Excel</span></div>
      <p style={{ fontSize: 13, color: "var(--ink-2)", margin: "4px 0 12px" }}>
        Upload the <strong>ZEF BOM inventory</strong> spreadsheet (Sheet1). This{" "}
        <strong style={{ color: "var(--accent)" }}>deletes all current items, BOMs and costs</strong> and rebuilds the catalog
        from the sheet — codes, names, and the 10k min/likely/max costs. Users, suppliers, materials and cost types are kept.
        Then re-import your BOMs on top. <strong>Check the file first</strong> — nothing is wiped until you confirm the preview.
      </p>
      <p style={{ fontSize: 12, color: "var(--ink-3)", margin: "0 0 12px", lineHeight: 1.6 }}>
        Reads these columns from <code style={{ fontSize: 11 }}>Sheet1</code>:{" "}
        <code style={{ fontSize: 11 }}>partnumber</code>, <code style={{ fontSize: 11 }}>partname</code> (required);{" "}
        <code style={{ fontSize: 11 }}>avg</code>, <code style={{ fontSize: 11 }}>Future_10k_min</code>,{" "}
        <code style={{ fontSize: 11 }}>Future_10k_max</code> (optional 10k costs).
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input ref={fileRef} type="file" accept=".xlsx,.xls" className="input" style={{ paddingTop: 6, flex: 1 }} onChange={onPick} />
        <button className="btn" onClick={check} disabled={busy}>{busy && !preview ? "Checking…" : "Check file"}</button>
      </div>

      {error && <p className="err" style={{ marginTop: 10 }}>{error}</p>}

      {preview && (
        <div className="card" style={{ marginTop: 12, padding: 14, background: "var(--surface-2, transparent)" }}>
          {preview.missing_required?.length > 0 ? (
            <p className="err" style={{ margin: 0, fontSize: 13 }}>
              ✗ This file is <strong>missing required column(s): {preview.missing_required.join(", ")}</strong>.
              <br />Columns found: <code style={{ fontSize: 11 }}>{preview.columns_found.join(", ") || "(none)"}</code>.
              <br />Won’t import — the format doesn’t match the ZEF inventory sheet.
            </p>
          ) : (
            <>
              <p style={{ margin: "0 0 8px", fontSize: 13, color: "var(--ink-1)" }}>
                Preview — <strong>nothing changed yet.</strong>
              </p>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: "var(--ink-2)", lineHeight: 1.7 }}>
                <li>Will create <strong>{preview.will_create}</strong> items
                  ({preview.with_10k_cost} with a 10k cost).</li>
                <li>Will skip <strong>{preview.skipped}</strong> rows
                  {preview.skipped > 0 && (
                    <> — {Object.entries(reasons).filter(([, n]) => n > 0)
                      .map(([k, n]) => `${n} ${reasonLabel[k] || k}`).join(", ")}</>
                  )}.</li>
                <li style={{ color: "var(--accent)" }}>
                  Will first <strong>delete all {preview.current_item_count}</strong> existing items (and their BOMs + costs).</li>
              </ul>
              {preview.will_create === 0 ? (
                <p className="err" style={{ margin: "10px 0 0", fontSize: 12.5 }}>
                  No valid rows — import is blocked so the database isn’t wiped for nothing.
                </p>
              ) : (
                <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 10 }}>
                  <button className="btn danger" onClick={run} disabled={busy}>
                    {busy ? "Importing…" : `Wipe & import ${preview.will_create} items`}
                  </button>
                  <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>This cannot be undone.</span>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {result && (
        <div style={{ marginTop: 10, fontSize: 13 }}>
          <p style={{ color: "var(--ok)", margin: 0 }}>
            ✓ Wiped and seeded <strong>{result.items_created}</strong> items ({result.with_10k_cost} with a 10k cost
            {typeof result.skipped === "number" ? `, ${result.skipped} skipped` : ""}).
            The catalog is fresh — Browse is empty until you import a BOM.
          </p>
          <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--ink-3)" }}>
            {result.backup?.saved
              ? <>A pre-wipe backup was saved to Drive{result.backup.url ? <> — <a href={result.backup.url} target="_blank" rel="noreferrer">{result.backup.name}</a></> : ` (${result.backup.name})`}.</>
              : <>⚠ No pre-wipe Drive backup was saved ({result.backup?.reason || "drive not configured"}). The previous data is gone — restore from an earlier backup if needed.</>}
          </p>
        </div>
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


// Duty rates: published facts about the world, typed in and dated.
//
// Auditable beats fresh. A landed cost that quietly changed under a quote is worse than one
// that is three months old and says so, which is why every row carries `valid_from` and
// where it came from, and why archiving keeps the row instead of deleting it.
//
// Matching is longest-prefix, so this table can be filled in coarsely — a 4-digit heading
// covers everything beneath it — and refined later without ever being wrong in between,
// only imprecise. The list shows which rows are headings for exactly that reason.
function DutyRates() {
  const [rows, setRows] = useState(null);
  const [dest, setDest] = useState("");
  const [defaultDest, setDefaultDest] = useState("PT");
  const [form, setForm] = useState({ hs_code: "", origin_country: "", rate_pct: "", valid_from: "", source: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const load = () =>
    api.dutyRates(dest || undefined)
      .then((r) => { setRows(r.rates); setDefaultDest(r.destination_default); })
      .catch((e) => setErr(e.message));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [dest]);

  const add = async () => {
    setBusy(true); setErr(null);
    try {
      await api.addDutyRate({
        hs_code: form.hs_code.trim(),
        destination_country: (dest || defaultDest).toUpperCase(),
        origin_country: form.origin_country.trim().toUpperCase(),
        rate_pct: Number(form.rate_pct),
        valid_from: form.valid_from || null,
        source: form.source.trim() || null,
        note: form.note.trim() || null,
      });
      setForm({ hs_code: "", origin_country: "", rate_pct: "", valid_from: "", source: "", note: "" });
      await load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const archive = async (r) => {
    if (!window.confirm(`Archive the ${r.rate_pct}% rate for ${r.hs_code}?\n\nThe row is kept — a landed cost computed with it should stay explicable.`)) return;
    try { await api.archiveDutyRate(r.id); await load(); } catch (e) { setErr(e.message); }
  };

  const digits = (form.hs_code.match(/\d/g) || []).length;
  const canAdd = digits >= 4 && form.rate_pct !== "" && Number(form.rate_pct) >= 0;

  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Import duty rates</span>
        <span className="card-meta">
          destination{" "}
          <input className="input mono" value={dest} placeholder={defaultDest} maxLength={2}
                 onChange={(e) => setDest(e.target.value.toUpperCase())}
                 style={{ width: 52, height: 26, display: "inline-block", textAlign: "center" }} />
        </span>
      </div>

      <p className="micro" style={{ color: "var(--ink-3)", margin: "0 0 12px" }}>
        Percent of the customs value. Longest HS prefix wins, so <span className="mono">8544</span> covers
        every code under it until a longer line is entered. Leave the origin blank for the
        third-country rate that applies unless a preference or a trade measure says otherwise.
        <strong> Import VAT does not belong here</strong> — it is recoverable, so it is never part of COGS.
      </p>

      {err && <p className="err" style={{ marginBottom: 10 }}>{err}</p>}

      <div style={{ display: "grid", gridTemplateColumns: "130px 70px 80px 130px 1fr auto", gap: 8, alignItems: "end", marginBottom: 14 }}>
        <Field label="HS code">
          <input className="input mono" value={form.hs_code} placeholder="8544.42.90"
                 onChange={(e) => setForm((f) => ({ ...f, hs_code: e.target.value }))} />
        </Field>
        <Field label="Origin">
          <input className="input mono" value={form.origin_country} placeholder="any" maxLength={2}
                 onChange={(e) => setForm((f) => ({ ...f, origin_country: e.target.value.toUpperCase() }))} />
        </Field>
        <Field label="Rate %">
          <input className="input mono" value={form.rate_pct} placeholder="2.7" inputMode="decimal"
                 onChange={(e) => setForm((f) => ({ ...f, rate_pct: e.target.value }))} />
        </Field>
        <Field label="Valid from">
          <input className="input" type="date" value={form.valid_from}
                 onChange={(e) => setForm((f) => ({ ...f, valid_from: e.target.value }))} />
        </Field>
        <Field label="Source">
          <input className="input" value={form.source} placeholder="TARIC export 2026-03-01"
                 onChange={(e) => setForm((f) => ({ ...f, source: e.target.value }))} />
        </Field>
        <button className="btn sm" onClick={add} disabled={busy || !canAdd}
                title={canAdd ? "" : "An HS code needs at least 4 digits, and a rate is required"}>
          {busy ? "Adding…" : "Add rate"}
        </button>
      </div>

      {rows === null ? <p className="muted">Loading…</p> : rows.length === 0 ? (
        <p className="muted">
          No rates for {dest || defaultDest} yet. Nothing breaks without them — a classified part
          with no rate reads as a gap, not as duty-free.
        </p>
      ) : (
        <div>
          {rows.map((r) => (
            <div key={r.id} className="duty-row">
              <span className="mono">
                {r.hs_code}
                {r.hs_code.length <= 4 && (
                  <span className="micro" style={{ color: "var(--ink-4)", marginLeft: 5 }}>heading</span>
                )}
              </span>
              <span className="mono">{r.origin_country || "any"}</span>
              <span className="mono">{r.rate_pct}%</span>
              <span className="micro" style={{ color: "var(--ink-3)" }}>{r.valid_from || "undated"}</span>
              <span className="micro" style={{ color: "var(--ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    title={[r.source, r.note].filter(Boolean).join(" · ")}>
                {r.source || <em>no source recorded</em>}
              </span>
              <button className="btn ghost sm" onClick={() => archive(r)} title="Archive — the row is kept">
                <Icon name="close" size={11} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const Field = ({ label, children }) => (
  <div style={{ minWidth: 0 }}><span className="input-label">{label}</span>{children}</div>
);
