// A production build must know where the API is (launch plan P2.3).
//
// Vite inlines every VITE_* value at build time. Without VITE_API_BASE_URL the
// bundle falls back to the dev-proxy path /api, which on the deployed host is
// the SPA's own origin: every request 404s, and nothing fails at build time.
// The opposite slip is as quiet -- a stray gitignored .env.local (higher
// precedence than .env.production) bakes http://localhost:8012 into the bundle
// that gets deployed. The same guard the extension applies to its store zip
// (extension/scripts/store-build-guard.ts), for the same reasons.
//
// Applied to every `vite build`: a built bundle has no dev proxy, so whatever
// it is pointed at is its real API. `npm run dev` is how you work against a
// local one.

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"]);

/** Why this value can't be baked into a production bundle, or null if it can. */
export function apiBaseProblem(raw: string | undefined): string | null {
  const name = "VITE_API_BASE_URL";
  if (raw === undefined || raw.trim() === "") return `${name} is not set.`;
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return `${name}=${JSON.stringify(raw)} is not a valid absolute URL.`;
  }
  // Every request carries the user's bearer token, so a cleartext origin would
  // send it unencrypted.
  if (url.protocol !== "https:") {
    return `${name}=${JSON.stringify(raw)} is not https:// (the session token is sent to this origin).`;
  }
  const host = url.hostname.toLowerCase();
  if (LOCAL_HOSTS.has(host) || host.endsWith(".localhost") || host.endsWith(".local")) {
    return `${name}=${JSON.stringify(raw)} points at a local address.`;
  }
  return null;
}

/** Throws, with what to do about it, unless `raw` may be built into production. */
export function assertApiBase(raw: string | undefined): void {
  const problem = apiBaseProblem(raw);
  if (problem === null) return;
  throw new Error(
    [
      "Refusing to build: the API address for this bundle is wrong.",
      `  - ${problem}`,
      "",
      "Pass the production value in the shell, which takes precedence over any .env.local, e.g.",
      "  VITE_API_BASE_URL=https://api.example.com npm run build",
      "and build the deployed bundle from a clean checkout. For local work use `npm run dev`,",
      "which reaches the API through the dev proxy and needs no value.",
    ].join("\n"),
  );
}
