import { readFileSync } from "node:fs";

// `wxt zip` bundles whatever env it can see into the extension: Vite
// inlines every `WXT_*` value at build time, and a stray gitignored
// `.env.local` on a developer machine (which has higher precedence than
// `.env.production`) is exactly what a store zip would otherwise pick up.
// The result is an extension that ships wired to http://localhost:8012 and
// the dev Supabase project -- or, with no env at all, one that calls
// `fetch("undefined/extension/...")`. Neither fails at build time.
//
// Applied to `zip` only, not `build`: `npm run build` is how this
// extension is loaded unpacked for real testing against a local API
// (README), and localhost is correct there. The zip is the store artifact.

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"]);

function urlProblem(name: string, raw: string | undefined): string | null {
  if (raw === undefined || raw.trim() === "") return `${name} is not set.`;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return `${name}=${JSON.stringify(raw)} is not a valid URL.`;
  }
  // Every backend request carries the user's bearer token, so a cleartext
  // origin would send it unencrypted.
  if (url.protocol !== "https:") {
    return `${name}=${JSON.stringify(raw)} is not https:// (the session token is sent to this origin).`;
  }
  const host = url.hostname.toLowerCase();
  if (LOCAL_HOSTS.has(host) || host.endsWith(".localhost") || host.endsWith(".local")) {
    return `${name}=${JSON.stringify(raw)} points at a local address.`;
  }
  return null;
}

/** Every reason this build must not be zipped for the store. Empty means fine. */
export function findStoreBuildProblems(
  env: Record<string, string | undefined>,
  manifest: { version?: unknown },
): string[] {
  const problems: string[] = [];
  for (const problem of [
    urlProblem("WXT_API_BASE_URL", env.WXT_API_BASE_URL),
    urlProblem("WXT_SUPABASE_URL", env.WXT_SUPABASE_URL),
  ]) {
    if (problem !== null) problems.push(problem);
  }
  if (env.WXT_SUPABASE_PUBLISHABLE_KEY === undefined || env.WXT_SUPABASE_PUBLISHABLE_KEY.trim() === "") {
    problems.push("WXT_SUPABASE_PUBLISHABLE_KEY is not set.");
  }
  // Chrome: one to four dot-separated integers, and "must not be all zero".
  const version = typeof manifest.version === "string" ? manifest.version : "";
  const parts = version.split(".");
  if (!/^\d+(\.\d+){0,3}$/.test(version) || parts.every((part) => Number(part) === 0)) {
    problems.push(`manifest version ${JSON.stringify(version)} is not a valid, non-zero Chrome extension version.`);
  }
  return problems;
}

export function assertStoreBuild(env: Record<string, string | undefined>, manifestPath: string): void {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as { version?: unknown };
  const problems = findStoreBuildProblems(env, manifest);
  if (problems.length === 0) return;
  throw new Error(
    [
      "Refusing to create a store zip from this build:",
      ...problems.map((problem) => `  - ${problem}`),
      "",
      "Pass the production values in the shell, which takes precedence over any .env.local, e.g.",
      "  WXT_API_BASE_URL=https://... WXT_SUPABASE_URL=https://... WXT_SUPABASE_PUBLISHABLE_KEY=... npm run zip",
      "and build store zips from a clean checkout. See README.md, \"Building for the store\".",
    ].join("\n"),
  );
}
