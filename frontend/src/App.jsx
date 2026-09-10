import { useEffect, useState } from "react";
import { api, session } from "./api";
import Tree from "./components/Tree.jsx";
import PartDrawer from "./components/PartDrawer.jsx";
import Costing from "./components/Costing.jsx";
import Catalog from "./components/Catalog.jsx";
import Pending from "./components/Pending.jsx";
import Facilities from "./components/Facilities.jsx";
import History from "./components/History.jsx";
import Admin from "./components/Admin.jsx";
import Login from "./components/Login.jsx";
import { DEFAULT_TIER } from "./tiers.js";

const TABS = [
  { id: "browse", label: "Browse" },
  { id: "catalog", label: "Catalog" },
  { id: "costing", label: "Costing" },
  { id: "facilities", label: "Facilities" },
  { id: "pending", label: "Pending" },
  { id: "history", label: "History" },
  { id: "admin", label: "Admin" },
];

// What a viewer is allowed to open. The other tabs stay on the bar but arrive greyed and
// inert: pulling them off entirely would make the tool a different shape for different
// people, and leave a viewer wondering where Catalog went. Dead-but-present says the
// screen exists and is not theirs. Admin is the exception — it stays hidden, as before.
const VIEWER_TABS = new Set(["browse", "costing"]);

export default function App() {
  const [authCfg, setAuthCfg] = useState(null); // null = loading
  const [user, setUser] = useState(session.user());
  const [route, setRoute] = useState("browse");
  const [openPart, setOpenPart] = useState(null);
  // ONE volume tier for the whole app. Browse and the drawer used to hold separate copies of
  // this, so opening the drawer over a tree read at @10k could show @100 beside it with
  // nothing on screen explaining the difference. Costing keeps its own control — it is never
  // on screen with Browse — but shares this value, so switching tabs keeps the tier you
  // chose. Not persisted: every load starts at DEFAULT_TIER.
  const [tier, setTier] = useState(DEFAULT_TIER);
  // The milestone Browse is comparing against, or null for live. Lifted here for the same
  // reason the tier was: the drawer renders beside the tree, and a drawer offering editable
  // fields while the tree shows a frozen BOM would invite edits to a state nobody is
  // looking at. { id, name, taken_at, root_item_id }.
  const [compare, setCompare] = useState(null);
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
  const role = user ? user.role : "admin"; // dev (no user) sees everything
  const isAdmin = role === "admin";
  const isViewer = role === "viewer";
  const visibleTabs = TABS.filter((t) => t.id !== "admin" || isAdmin);
  const allowed = (id) => !isViewer || VIEWER_TABS.has(id);
  const activeRoute = visibleTabs.some((t) => t.id === route) && allowed(route) ? route : "browse";
  // A viewer cannot write, so the drawer — which is all editable fields — never opens for
  // them. Handing the tabs a handler that does nothing keeps the guard in one place
  // instead of threading a flag through every row that offers a click.
  const showPart = isViewer ? () => {} : setOpenPart;

  return (
    <div className={`app${isViewer ? " read-only" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <img src="/zef-logo.png" alt="ZEF" className="brand-logo" style={{ height: 32, width: "auto", display: "block" }} />
          <div>
            <div className="brand-title">ZEF · BOM</div>
            <div className="brand-sub">inventory · costing</div>
          </div>
        </div>
        <nav className="nav-tabs">
          {visibleTabs.map((t) => {
            const off = !allowed(t.id);
            // Changing tab closes the drawer. It used to survive the move, so a part opened
            // in Browse hung over Facilities — a panel about an item, beside a screen that
            // has nothing to do with items, still pushing the page 680px sideways. The
            // drawer belongs to what you were looking at.
            return (
              <button key={t.id} className={`nav-tab ${activeRoute === t.id ? "on" : ""}`}
                      disabled={off} aria-disabled={off || undefined}
                      title={off ? "Your access is view-only" : undefined}
                      onClick={() => { setRoute(t.id); setOpenPart(null); }}>{t.label}</button>
            );
          })}
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
        {activeRoute === "browse" && <Tree onOpenPart={showPart} readOnly={isViewer} focus={openPart} version={version} tier={tier} setTier={setTier} compare={compare} setCompare={setCompare} />}
        {activeRoute === "catalog" && <Catalog onOpenPart={showPart} version={version} />}
        {activeRoute === "costing" && <Costing onOpenPart={showPart} tier={tier} setTier={setTier} compare={compare} setCompare={setCompare} />}
        {activeRoute === "facilities" && <Facilities version={version} onChanged={() => setVersion((v) => v + 1)} />}
        {activeRoute === "pending" && <Pending onOpenPart={showPart} onOpenFacilities={() => setRoute("facilities")} version={version} />}
        {activeRoute === "history" && <History onOpenPart={showPart} version={version} />}
        {activeRoute === "admin" && <Admin onOpenPart={showPart} onChanged={() => setVersion((v) => v + 1)} />}
      </main>

      {openPart && !isViewer && (
        <PartDrawer itemId={openPart} tier={tier} onClose={() => setOpenPart(null)} onOpenPart={showPart}
                    onChanged={() => setVersion((v) => v + 1)}
                    frozenBy={activeRoute === "browse" ? compare : null} />
      )}
    </div>
  );
}
