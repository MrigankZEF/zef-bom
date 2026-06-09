import { useEffect, useState } from "react";
import { api, session } from "./api";
import Tree from "./components/Tree.jsx";
import PartDrawer from "./components/PartDrawer.jsx";
import Uploads from "./components/Uploads.jsx";
import Costing from "./components/Costing.jsx";
import Pending from "./components/Pending.jsx";
import History from "./components/History.jsx";
import Admin from "./components/Admin.jsx";
import Login from "./components/Login.jsx";

const TABS = [
  { id: "browse", label: "Browse" },
  { id: "costing", label: "Costing" },
  { id: "pending", label: "Pending" },
  { id: "uploads", label: "Uploads" },
  { id: "history", label: "History" },
  { id: "admin", label: "Admin" },
];

export default function App() {
  const [authCfg, setAuthCfg] = useState(null); // null = loading
  const [user, setUser] = useState(session.user());
  const [route, setRoute] = useState("browse");
  const [openPart, setOpenPart] = useState(null);
  const [health, setHealth] = useState(null);
  const [version, setVersion] = useState(0);
  const [loginError, setLoginError] = useState(null);

  useEffect(() => { api.authConfig().then(setAuthCfg).catch(() => setAuthCfg({ enabled: false })); }, []);
  useEffect(() => { if (authCfg && (!authCfg.enabled || user)) api.health().then(setHealth).catch(() => {}); }, [authCfg, user]);

  if (authCfg === null) return <div style={{ padding: 40 }} className="muted">Loading…</div>;

  if (authCfg.enabled && !user) {
    return (
      <Login clientId={authCfg.client_id} serverError={loginError} onCredential={async (cred) => {
        setLoginError(null);
        try { const r = await api.googleLogin(cred); session.set(r.token, { email: r.email, name: r.name, role: r.role }); setUser(session.user()); }
        catch (e) { setLoginError(e.message.replace(/^\d+\s+\w+\s+—\s+/, "").replace(/^"|"$/g, "")); }
      }} />
    );
  }

  const logout = () => { session.clear(); setUser(null); window.location.reload(); };
  const isAdmin = !user || user.role === "admin"; // dev (no user) sees everything
  const visibleTabs = TABS.filter((t) => t.id !== "admin" || isAdmin);
  const activeRoute = visibleTabs.some((t) => t.id === route) ? route : "browse";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" />
          <div>
            <div className="brand-title">ZEF · BOM</div>
            <div className="brand-sub">inventory · costing</div>
          </div>
        </div>
        <nav className="nav-tabs">
          {visibleTabs.map((t) => (
            <button key={t.id} className={`nav-tab ${activeRoute === t.id ? "on" : ""}`} onClick={() => setRoute(t.id)}>{t.label}</button>
          ))}
        </nav>
        <div className="topbar-end">
          {health && <span className="mono">{health.items} items</span>}
          {user ? (
            <span className="user-chip">
              <span className="user-avatar">{(user.name || user.email || "?").slice(0, 1).toUpperCase()}</span>
              <span style={{ fontSize: 11 }}>{user.name || user.email}</span>
              <button className="btn ghost sm" onClick={logout} style={{ marginLeft: 4 }}>sign out</button>
            </span>
          ) : (
            <span className="mono" style={{ color: "var(--ink-4)" }}>dev (no auth)</span>
          )}
        </div>
      </header>

      <main style={{ marginRight: openPart ? "min(680px, 96vw)" : 0, transition: "margin-right 320ms cubic-bezier(.2,.8,.2,1)" }}>
        {activeRoute === "browse" && <Tree onOpenPart={setOpenPart} focus={openPart} version={version} />}
        {activeRoute === "costing" && <Costing onOpenPart={setOpenPart} />}
        {activeRoute === "pending" && <Pending onOpenPart={setOpenPart} version={version} />}
        {activeRoute === "uploads" && <Uploads onApplied={() => setVersion((v) => v + 1)} />}
        {activeRoute === "history" && <History onOpenPart={setOpenPart} version={version} />}
        {activeRoute === "admin" && <Admin onOpenPart={setOpenPart} onChanged={() => setVersion((v) => v + 1)} />}
      </main>

      {openPart && (
        <PartDrawer itemId={openPart} onClose={() => setOpenPart(null)} onOpenPart={setOpenPart} onChanged={() => setVersion((v) => v + 1)} />
      )}
    </div>
  );
}
