// Where the API lives (launch plan P2.3).
//
// In development the API is reached through Vite's dev proxy at /api
// (vite.config.ts strips the prefix), so the browser never sees a second
// origin. A deployed build has no such proxy -- the SPA and the API are
// different hosts -- so VITE_API_BASE_URL names the API's origin, and
// scripts/apiBaseGuard.ts refuses to build for production without it.
//
// Pure module: api.ts pulls in the Supabase client, which throws at import time
// without env vars, so the URL logic lives here where a plain test can load it.

export const DEV_API_BASE = "/api";

/** The base every request path is appended to. Unset or blank means the dev
 *  proxy; a trailing slash on a configured base is dropped so `base + "/x"`
 *  never doubles it. */
export function resolveApiBase(raw: string | undefined): string {
  const trimmed = (raw ?? "").trim().replace(/\/+$/, "");
  return trimmed === "" ? DEV_API_BASE : trimmed;
}

/** `path` is the route as the API spells it ("/profile/..."), leading slash
 *  included; one is added if a caller leaves it off. */
export function buildApiUrl(base: string, path: string): string {
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}
