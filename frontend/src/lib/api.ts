/**
 * Thin fetch wrapper. Uses Vite's dev proxy in development (/api → :8000) and
 * VITE_API_BASE_URL in production deploys.
 */
const BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

async function request<T>(
  path: string,
  init: RequestInit & { query?: Record<string, unknown> } = {},
): Promise<T> {
  const { query, ...rest } = init;
  let url = BASE + path;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") params.append(k, String(v));
    }
    const s = params.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const r = await fetch(url, {
    ...rest,
    headers: { "Content-Type": "application/json", ...(rest.headers || {}) },
  });
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json())?.detail || ""; } catch {}
    throw new Error(detail || `${r.status} ${r.statusText}`);
  }
  if (r.status === 204) return undefined as unknown as T;
  return r.json();
}

export const api = {
  get:    <T>(p: string, init?: any) => request<T>(p, { method: "GET", ...init }),
  post:   <T>(p: string, body?: any) => request<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put:    <T>(p: string, body?: any) => request<T>(p, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  patch:  <T>(p: string, body?: any) => request<T>(p, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(p: string) => request<T>(p, { method: "DELETE" }),
  uploadAudio: async <T>(p: string, audio: Blob, fields: Record<string, string>) => {
    const fd = new FormData();
    fd.append("audio", audio, "recording.webm");
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    const r = await fetch(BASE + p, { method: "POST", body: fd });
    if (!r.ok) {
      let d = ""; try { d = (await r.json())?.detail || ""; } catch {}
      throw new Error(d || `${r.status}`);
    }
    return r.json() as Promise<T>;
  },
};
