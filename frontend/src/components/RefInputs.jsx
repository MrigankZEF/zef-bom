import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon } from "./ui";

// Shared loader for a reference category (supplier/material/country/module).
export function useReference(category) {
  const [options, setOptions] = useState([]);
  const reload = () => api.reference(category).then(setOptions).catch(() => {});
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [category]);
  const add = async (value, extra = {}) => {
    if (!value) return;
    await api.addReference({ category, value, ...extra });
    reload();
  };
  return { options, add, reload };
}

// Single-value dropdown backed by reference data, with a "+ add new…" action.
export function RefSelect({ category, value, onChange, placeholder = "— select —" }) {
  const { options, add } = useReference(category);
  const onSel = async (e) => {
    if (e.target.value === "__add__") {
      const v = window.prompt(`Add a new ${category}:`);
      if (v && v.trim()) { await add(v.trim()); onChange(v.trim()); }
      return;
    }
    onChange(e.target.value);
  };
  const known = options.some((o) => o.value === value);
  return (
    <select className="select" value={value ?? ""} onChange={onSel}>
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o.id} value={o.value}>{o.label ? `${o.value} — ${o.label}` : o.value}</option>
      ))}
      {value && !known && <option value={value}>{value}</option>}
      <option value="__add__">＋ add new {category}…</option>
    </select>
  );
}

// Multi-value chips backed by reference data (e.g. materials).
export function MultiRef({ category, values, onChange }) {
  const { options, add } = useReference(category);
  const list = values || [];
  const toggle = (v) => onChange(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const addNew = async () => {
    const v = window.prompt(`Add a new ${category}:`);
    if (v && v.trim()) { await add(v.trim()); if (!list.includes(v.trim())) onChange([...list, v.trim()]); }
  };
  return (
    <div>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
        {list.length === 0 && <span style={{ color: "var(--ink-4)", fontSize: 12 }}>none</span>}
        {list.map((v) => (
          <span key={v} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11.5, padding: "2px 6px", borderRadius: 3, background: "var(--warm-3)", border: "1px solid var(--warm)" }}>
            {v}<button onClick={() => toggle(v)} style={{ border: 0, background: "none", cursor: "pointer", color: "var(--ink-3)", padding: 0, lineHeight: 1 }}><Icon name="close" size={9} /></button>
          </span>
        ))}
      </div>
      <select className="select" value="" onChange={(e) => { if (e.target.value === "__add__") addNew(); else if (e.target.value) toggle(e.target.value); }}>
        <option value="">+ add material…</option>
        {options.filter((o) => !list.includes(o.value)).map((o) => <option key={o.id} value={o.value}>{o.value}</option>)}
        <option value="__add__">＋ new material…</option>
      </select>
    </div>
  );
}
