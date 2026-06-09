// Thin API client. The ZEF BOM backend base URL comes from VITE_API_BASE
// (defaults to /api, proxied to FastAPI by the Vite dev server).
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

// ── session ──
export const session = {
  token: () => localStorage.getItem("zef_token"),
  user: () => { try { return JSON.parse(localStorage.getItem("zef_user")); } catch { return null; } },
  set: (token, user) => { localStorage.setItem("zef_token", token); localStorage.setItem("zef_user", JSON.stringify(user)); },
  clear: () => { localStorage.removeItem("zef_token"); localStorage.removeItem("zef_user"); },
};
export function authHeaders() {
  const t = session.token();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 401) { session.clear(); window.location.reload(); }
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${res.statusText} — ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  authConfig: () => request("/auth/config"),
  googleLogin: (credential) => request("/auth/google", { method: "POST", body: JSON.stringify({ credential }) }),
  health: () => request("/health"),
  listItems: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/items${qs ? `?${qs}` : ""}`);
  },
  getItem: (id) => request(`/items/${encodeURIComponent(id)}`),
  fieldDefinitions: () => request("/field-definitions"),
  tree: (root, volume = 100) => {
    const qs = new URLSearchParams({ ...(root ? { root } : {}), volume }).toString();
    return request(`/tree?${qs}`);
  },
  rollup: (root, volume = 100) =>
    request(`/rollup?${new URLSearchParams({ root, volume })}`),
  whereUsed: (id) => request(`/items/${encodeURIComponent(id)}/where-used`),
  costingSummary: (volume = 100) =>
    request(`/costing/summary?${new URLSearchParams({ volume })}`),
  costingBreakdown: (root, volume = 100) =>
    request(`/costing/breakdown?${new URLSearchParams({ root, volume })}`),
  pending: (module) =>
    request(`/pending${module ? `?${new URLSearchParams({ module })}` : ""}`),

  // ── M4: edit + cost ──
  patchItem: (id, patch) =>
    request(`/items/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) }),
  costEvidence: (id) => request(`/items/${encodeURIComponent(id)}/cost-evidence`),
  addCostEvidence: (id, body) =>
    request(`/items/${encodeURIComponent(id)}/cost-evidence`, { method: "POST", body: JSON.stringify(body) }),
  deleteCostEvidence: (id, eid) =>
    request(`/items/${encodeURIComponent(id)}/cost-evidence/${eid}`, { method: "DELETE" }),
  decidedCost: (id) => request(`/items/${encodeURIComponent(id)}/decided-cost`),
  setDecidedCost: (id, body) =>
    request(`/items/${encodeURIComponent(id)}/decided-cost`, { method: "PUT", body: JSON.stringify(body) }),
  itemHistory: (id) => request(`/items/${encodeURIComponent(id)}/history`),
  history: (entityType) =>
    request(`/history${entityType ? `?${new URLSearchParams({ entity_type: entityType })}` : ""}`),

  // ── M5: uploads ──
  listUploads: () => request("/uploads"),
  getUpload: (id) => request(`/uploads/${encodeURIComponent(id)}`),
  createUpload: async (file, { notes, isTopLevel } = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    if (notes) fd.append("notes", notes);
    fd.append("is_top_level", isTopLevel ? "true" : "false");
    const res = await fetch(`${BASE}/uploads`, { method: "POST", body: fd, headers: authHeaders() });
    if (!res.ok) throw new Error(`${res.status} — ${await res.text().catch(() => res.statusText)}`);
    return res.json();
  },
  approveUpload: (id) => request(`/uploads/${encodeURIComponent(id)}/approve`, { method: "POST" }),
  rejectUpload: (id) => request(`/uploads/${encodeURIComponent(id)}/reject`, { method: "POST" }),

  // ── reference data (admin-managed dropdowns) ──
  reference: (category) =>
    request(`/reference${category ? `?${new URLSearchParams({ category })}` : ""}`),
  addReference: (body) => request("/reference", { method: "POST", body: JSON.stringify(body) }),
  deleteReference: (id) => request(`/reference/${id}`, { method: "DELETE" }),

  // ── archive (soft-delete) ──
  archiveItem: (id) => request(`/items/${encodeURIComponent(id)}`, { method: "DELETE" }),
  restoreItem: (id) => request(`/items/${encodeURIComponent(id)}/restore`, { method: "POST" }),
  archiveLink: (parent, child) =>
    request(`/items/${encodeURIComponent(parent)}/links/${encodeURIComponent(child)}`, { method: "DELETE" }),
  restoreLink: (parent, child) =>
    request(`/items/${encodeURIComponent(parent)}/links/${encodeURIComponent(child)}/restore`, { method: "POST" }),
  archive: () => request("/archive"),
  purgeItem: (id) => request(`/items/${encodeURIComponent(id)}/purge`, { method: "DELETE" }),
  purgeLink: (parent, child) =>
    request(`/items/${encodeURIComponent(parent)}/links/${encodeURIComponent(child)}/purge`, { method: "DELETE" }),

  // ── users (admin) ──
  listUsers: () => request("/users"),
  addUser: (body) => request("/users", { method: "POST", body: JSON.stringify(body) }),
  setUserRole: (email, role) => request(`/users/${encodeURIComponent(email)}`, { method: "PATCH", body: JSON.stringify({ role }) }),
  removeUser: (email) => request(`/users/${encodeURIComponent(email)}`, { method: "DELETE" }),

  // ── attachments (Drive) ──
  attachments: (id) => request(`/items/${encodeURIComponent(id)}/attachments`),
  ensureFolder: (id) => request(`/items/${encodeURIComponent(id)}/attachments/folder`, { method: "POST" }),
  uploadAttachment: async (id, file) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${BASE}/items/${encodeURIComponent(id)}/attachments`, { method: "POST", body: fd, headers: authHeaders() });
    if (!res.ok) throw new Error(`${res.status} — ${await res.text().catch(() => res.statusText)}`);
    return res.json();
  },
};
