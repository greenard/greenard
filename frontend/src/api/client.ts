/** Client HTTP : jeton d'accès en mémoire, rafraîchissement transparent via cookie HttpOnly. */

export interface ApiErrorBody {
  code: string;
  message: string;
  params: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: ApiErrorBody,
  ) {
    super(body.message);
  }
}

const BASE = "/api/v1";
let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onLoggedOut: (() => void) | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function setLoggedOutHandler(fn: () => void) {
  onLoggedOut = fn;
}

export async function refreshAccessToken(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch(`${BASE}/auth/refresh`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-Requested-With": "greenard" },
    })
      .then(async (r) => {
        if (!r.ok) return false;
        accessToken = (await r.json()).access_token;
        return true;
      })
      .catch(() => false)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

async function parseError(r: Response): Promise<ApiError> {
  let body: ApiErrorBody = { code: "HTTP_ERROR", message: r.statusText, params: { status: r.status } };
  try {
    const j = await r.json();
    if (j?.error?.code) body = j.error;
    else if (j?.detail) body = { code: "VALIDATION_ERROR", message: JSON.stringify(j.detail), params: {} };
  } catch {
    /* corps non JSON */
  }
  return new ApiError(r.status, body);
}

export async function api<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const r = await fetch(`${BASE}${path}`, { ...init, headers, credentials: "same-origin" });
  if (r.status === 401 && retry && !path.startsWith("/auth/login")) {
    if (await refreshAccessToken()) return api<T>(path, init, false);
    accessToken = null;
    onLoggedOut?.();
  }
  if (!r.ok) throw await parseError(r);
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

export const get = <T,>(p: string) => api<T>(p);
export const post = <T,>(p: string, body?: unknown) =>
  api<T>(p, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) });
export const patch = <T,>(p: string, body: unknown) => api<T>(p, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T,>(p: string, body: unknown) => api<T>(p, { method: "PUT", body: JSON.stringify(body) });
export const del = <T,>(p: string) => api<T>(p, { method: "DELETE" });

/** Flux SSE de progression d'une tâche (fetch en flux : l'en-tête d'authentification est transmis). */
export async function streamTask<T>(taskId: number, onEvent: (t: T) => void, signal?: AbortSignal): Promise<void> {
  const open = () =>
    fetch(`${BASE}/tasks/${taskId}/events`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      credentials: "same-origin",
      signal,
    });
  let r = await open();
  if (r.status === 401 && (await refreshAccessToken())) r = await open();
  if (!r.ok || !r.body) throw await parseError(r);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) if (line.startsWith("data:")) onEvent(JSON.parse(line.slice(5)) as T);
    }
  }
}

/** Téléchargement authentifié d'un fichier (export). */
export async function downloadFile(path: string): Promise<void> {
  const go = () =>
    fetch(`${BASE}${path}`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      credentials: "same-origin",
    });
  let r = await go();
  if (r.status === 401 && (await refreshAccessToken())) r = await go();
  if (!r.ok) throw await parseError(r);
  const cd = r.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? "export";
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Image authentifiée (aperçus de terrain) → URL blob + en-têtes utiles. */
export async function fetchImage(path: string): Promise<{ url: string; headers: Headers }> {
  const go = () =>
    fetch(`${BASE}${path}`, { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}, credentials: "same-origin" });
  let r = await go();
  if (r.status === 401 && (await refreshAccessToken())) r = await go();
  if (!r.ok) throw await parseError(r);
  return { url: URL.createObjectURL(await r.blob()), headers: r.headers };
}
