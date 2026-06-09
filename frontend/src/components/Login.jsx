import { useEffect, useRef, useState } from "react";

// "Sign in with Google" screen using Google Identity Services (loaded in index.html).
export default function Login({ clientId, onCredential, serverError }) {
  const btn = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let tries = 0;
    const init = () => {
      if (!window.google?.accounts?.id) {
        if (tries++ < 40) return setTimeout(init, 100); // wait for the GIS script
        return setError("Google sign-in failed to load. Check your connection.");
      }
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: (resp) => onCredential(resp.credential),
      });
      if (btn.current) {
        window.google.accounts.id.renderButton(btn.current, { theme: "outline", size: "large", text: "signin_with", shape: "rectangular" });
      }
    };
    init();
  }, [clientId]);

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "var(--bg)" }}>
      <div className="card" style={{ textAlign: "center", padding: 40, maxWidth: 360 }}>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 16 }}>
          <span className="brand-mark" style={{ width: 32, height: 32 }} />
        </div>
        <h1 style={{ fontSize: 24 }}>ZEF · BOM</h1>
        <p className="muted" style={{ margin: "8px 0 20px" }}>Sign in with your ZEF Google account.</p>
        <div ref={btn} style={{ display: "flex", justifyContent: "center" }} />
        {error && <p className="err" style={{ marginTop: 16 }}>{error}</p>}
        {serverError && <p className="err" style={{ marginTop: 16 }}>{serverError}</p>}
      </div>
    </div>
  );
}
