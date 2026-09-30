import { createContext, useContext, useEffect, useRef, useState } from "./vendor/preact-htm.js";

export async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

// App-wide context: server state, refresh signal, job starter, toasts, drawer.
export const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

export function useFetch(url, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const { version } = useApp();
  useEffect(() => {
    let live = true;
    if (!url) return;
    api("GET", url).then((d) => live && (setData(d), setError(null))).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [url, version, ...deps]);
  return [data, error, setData];
}

export function useKey(handler, deps = []) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const fn = (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      const typing = tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable;
      ref.current(e, typing);
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, deps);
}

export const money = (v) => (v || v === 0) && v !== "" ? `$${Number(v).toFixed(2)}` : "—";

export const STATUS = {
  draft: ["Draft", ""],
  exported: ["Exported", "accent"],
  listed: ["Listed", "good"],
  ended: ["Ended", ""],
  sold: ["Sold", "good"],
};

export const PACKAGE_LABELS = {
  card: "Card (envelope)",
  small: "Small box < 1 lb",
  medium: "Box 1–5 lb",
  large: "Box 5–10 lb",
};

export function isIdentified(item) {
  return !!(item.title && item.title.trim());
}

export function itemLabel(item) {
  return item.title || item.ai?.label || "Not identified yet";
}

// Upload files to a batch in chunks so HEIC conversion never times out one giant request.
export async function uploadFiles(batchId, files, onProgress) {
  const list = [...files].filter((f) => /\.(jpe?g|png|webp|heic|heif)$/i.test(f.name));
  let done = 0;
  const errors = [];
  for (let i = 0; i < list.length; i += 6) {
    const fd = new FormData();
    list.slice(i, i + 6).forEach((f) => fd.append("photos", f));
    const r = await api("POST", `/api/batches/${batchId}/upload`, fd);
    errors.push(...r.errors);
    done += Math.min(6, list.length - i);
    onProgress?.(done, list.length);
  }
  return { count: list.length, errors };
}

export function timeAgo(iso) {
  if (!iso) return "";
  const d = new Date(iso.replace(" ", "T") + (iso.includes("T") || iso.endsWith("Z") ? "" : "Z"));
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
