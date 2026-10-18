// The security headers a production build ships (Cloudflare Pages reads them from a file named
// `_headers` at the root of the published directory).
//
// The Content-Security-Policy names the exact origins the browser app may talk to, and those are
// not constants: they are the API and Supabase hosts the bundle is built against. So the policy is
// generated at build time from the same VITE_* values Vite inlines into the bundle, and a
// self-hoster gets the right policy for THEIR hosts without editing anything. The plugin that
// calls this module lives in vite.config.ts.
//
// Pure functions only (no filesystem, no process access): @types/node is not installed in this
// project, and the plugin hands the finished text to Vite to write into the output directory.
//
// What the app actually loads, from reading the source and the built output:
//   - scripts: one same-origin module bundle, no inline script, no eval or `new Function`.
//   - styles: one same-origin stylesheet plus the inline <style> in index.html (hashed below). React's
//     `style={...}` props are applied through the CSSOM (element.style), which style-src does not
//     govern, so no `style-src-attr` and no 'unsafe-inline' are needed. An inline `style="..."`
//     attribute in the HTML IS governed, cannot be hashed (no 'unsafe-hashes' is set), and so is
//     refused at build time (inlineAttributes below), as is an inline event-handler attribute.
//   - fetches: the API (api.ts) and Supabase auth (supabase.ts). Supabase's REST, storage and
//     realtime clients are never used, so no websocket is opened and no wss: origin is listed.
//   - frames: the click-to-load LinkedIn post embed (components/HiringPostCard.tsx), nothing else.
//   - images and fonts: the self-hosted typefaces (src/fonts.css) and the favicon and touch icons named
//     in index.html, all files from the site's own origin. There is no <img> yet. vite.config.ts turns
//     off asset inlining so an image or font can never arrive as a data: URL.
//   - the web app manifest (public/site.webmanifest), fetched by the browser from the site's own
//     origin. default-src 'none' would refuse it, so it has a directive of its own.
//   - PDF and JSON downloads: fetched as blobs from the API and saved through an <a download>, which
//     no CSP directive governs. Nothing is previewed in a frame.
//   - Google sign-in: a top-level navigation through Supabase, which no CSP directive governs.
// HSTS and COOP/COEP are deliberately not set here: HSTS is a one-way commitment the operator makes
// in the hosting dashboard, and COOP/COEP can break cross-origin sign-in popups and embeds.

import { isLocalHost } from "./apiBaseGuard.ts";

export const API_ENV = "VITE_API_BASE_URL";
export const SUPABASE_ENV = "VITE_SUPABASE_URL";

// The one third-party origin the app embeds. src/lib/hiringSignals.ts pins the embed address to this
// host exactly (EMBED_URL_PREFIX); securityHeaders.test.ts fails if the two ever disagree.
export const LINKEDIN_EMBED_ORIGIN = "https://www.linkedin.com";

// Cloudflare Pages silently ignores a `_headers` line longer than this, which for the CSP line would
// mean shipping no policy at all. The generator refuses to write such a line instead.
export const MAX_HEADERS_LINE = 2000;

// ── origins ────────────────────────────────────────────────────────────────

export type OriginResult = { ok: true; origin: string } | { ok: false; problem: string };

// A CSP host-source is dot-separated labels of letters, digits and hyphens: no wildcard (the `*`
// the URL parser lets through), no IPv6 literal (not expressible in a source), no underscore, and
// no `;` or `,` -- which the URL parser also lets through, and which would end a directive or a
// whole policy.
const HOST_LABEL = "[a-z0-9](?:[a-z0-9-]*[a-z0-9])?";
const CSP_HOST = new RegExp(`^${HOST_LABEL}(?:\\.${HOST_LABEL})*$`);

/** The exact origin (scheme, host, non-default port) a CSP may name for `raw`, or why it cannot.
 *  Problems name the variable and never quote the value: it may carry credentials. */
export function parseOrigin(name: string, raw: string | undefined): OriginResult {
  const bad = (problem: string): OriginResult => ({ ok: false, problem: `${name} ${problem}` });
  if (raw === undefined || raw.trim() === "") return bad("is not set.");
  const trimmed = raw.trim();
  // `new URL` is forgiving ("https:api.example.com" reads as a host), so the string as written must
  // look like https://host first -- the same rule scripts/apiBaseGuard.ts applies.
  if (!/^https:\/\/[^/\s]/i.test(trimmed)) return bad("is not an https:// URL.");
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return bad("is not a valid URL.");
  }
  if (url.username !== "" || url.password !== "") return bad("carries credentials in the URL.");
  if (trimmed.includes("?") || trimmed.includes("#")) return bad("has a query string or fragment.");
  // WHATWG parsing has lowercased the host and turned an internationalised name into its ASCII
  // (punycode) form, which is the spelling a CSP source must use.
  if (!CSP_HOST.test(url.hostname)) {
    return bad(
      "has a host a Content-Security-Policy cannot name (use a plain DNS name or IPv4 address: " +
        "letters, digits, hyphens and dots; no wildcard, underscore or IPv6 literal).",
    );
  }
  if (isLocalHost(url.hostname)) return bad("points at a local address.");
  // `url.origin` leaves the port out when it is the scheme's default, which is also how a CSP
  // source reads a missing port.
  return { ok: true, origin: url.origin };
}

// ── inline styles and scripts ──────────────────────────────────────────────

/** The CSP source for an inline block: 'sha256-<base64 of the SHA-256 of its text>'. The browser
 *  hashes the UTF-8 text after the HTML parser has normalised line endings to LF, so this does too. */
export async function cspHash(text: string): Promise<string> {
  const normalised = text.replace(/\r\n?/g, "\n");
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(normalised)));
  let binary = "";
  for (const byte of digest) binary += String.fromCharCode(byte);
  return `'sha256-${btoa(binary)}'`;
}

// <script> types a browser runs (an empty or missing type is classic JavaScript). Anything else --
// application/json, application/ld+json, a text/template -- is a data block that script-src does
// not govern.
const EXECUTABLE_SCRIPT_TYPES = new Set(["", "module", "importmap", "text/javascript", "application/javascript"]);

/** The text of every inline <style> and executable inline <script> in `html`. Comments are skipped,
 *  and so is any script with a src. Meant for Vite's own generated HTML, not arbitrary markup: a
 *  `>` inside a quoted attribute value is not handled. A wrong guess here can only add a hash for
 *  text that is in the build's own HTML, or leave a block unhashed so the browser refuses it. */
export function inlineBlocks(html: string): { styles: string[]; scripts: string[] } {
  const styles: string[] = [];
  const scripts: string[] = [];
  const withoutComments = html.replace(/<!--[\s\S]*?-->/g, "");
  for (const match of withoutComments.matchAll(/<(style|script)(?=[\s/>])([^>]*)>([\s\S]*?)<\/\1\s*>/gi)) {
    const [, tag, attributes, body] = match;
    if (tag.toLowerCase() === "style") {
      styles.push(body);
      continue;
    }
    if (/\ssrc\s*=/i.test(attributes)) continue;
    const type = /\stype\s*=\s*["']?([^"'\s>]*)/i.exec(attributes)?.[1] ?? "";
    if (EXECUTABLE_SCRIPT_TYPES.has(type.toLowerCase())) scripts.push(body);
  }
  return { styles, scripts };
}

// A start tag, with quoted attribute values handled so a `>` inside one does not end the tag early.
// Group 2 is everything after the tag name.
const START_TAG = /<([a-z][^\s/>]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/gi;
// One attribute inside that text: a name, then optionally `=` and a quoted or bare value.
const ATTRIBUTE = /([^\s"'<>/=]+)(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?/g;
// <style> and <script> elements, so their text (CSS, JavaScript) is not read as markup. Group 1 is
// the start tag and group 3 the end tag, which are kept.
const RAW_TEXT_ELEMENT = /(<(style|script)(?=[\s/>])(?:"[^"]*"|'[^']*'|[^>"'])*>)[\s\S]*?(<\/\2\s*>)/gi;

/** The names, lower-cased and in document order, of the inline `style` and event-handler (`onclick`,
 *  `onload`, ...) attributes in `html`. The policy refuses all of them: an attribute cannot be hashed
 *  without 'unsafe-hashes', and the policy sets neither that nor 'unsafe-inline'. Comments, and the
 *  text of <style> and <script> elements, are skipped, and so are attributes that merely contain the
 *  word (`data-style`). Meant for Vite's own generated HTML, like inlineBlocks. */
export function inlineAttributes(html: string): string[] {
  const markup = html.replace(/<!--[\s\S]*?-->/g, "").replace(RAW_TEXT_ELEMENT, "$1$3");
  const found: string[] = [];
  for (const tag of markup.matchAll(START_TAG)) {
    for (const attribute of tag[2].matchAll(ATTRIBUTE)) {
      const name = attribute[1].toLowerCase();
      if (name === "style" || /^on[a-z]+$/.test(name)) found.push(name);
    }
  }
  return found;
}

async function hashesOf(blocks: readonly string[]): Promise<string[]> {
  const hashes = await Promise.all(blocks.map(cspHash));
  return [...new Set(hashes)].sort();
}

// ── the policy ─────────────────────────────────────────────────────────────

export interface PolicyInput {
  apiOrigin: string;
  supabaseOrigin: string;
  /** 'sha256-...' sources for the inline <style> blocks the built HTML keeps. */
  styleHashes: readonly string[];
  /** 'sha256-...' sources for inline <script> blocks. None today, and the build test pins that. */
  scriptHashes: readonly string[];
}

/** The Content-Security-Policy value. Every directive is explicit; `default-src 'none'` makes
 *  anything not listed (media, manifests, workers' blob: scripts) a refusal rather than a guess. */
export function buildPolicy(input: PolicyInput): string {
  const directives: Array<[name: string, sources: readonly string[]]> = [
    ["default-src", ["'none'"]],
    ["script-src", ["'self'", ...input.scriptHashes]],
    ["style-src", ["'self'", ...input.styleHashes]],
    ["img-src", ["'self'"]],
    ["font-src", ["'self'"]],
    ["manifest-src", ["'self'"]],
    // The API carries the session token and Supabase handles sign-in; the same-origin site itself
    // is never fetched from script, so 'self' is not listed. Both can be one host: listed once.
    ["connect-src", [...new Set([input.apiOrigin, input.supabaseOrigin])]],
    ["frame-src", [LINKEDIN_EMBED_ORIGIN]],
    ["worker-src", ["'none'"]],
    ["object-src", ["'none'"]],
    ["base-uri", ["'self'"]],
    ["form-action", ["'self'"]],
    ["frame-ancestors", ["'none'"]],
    ["upgrade-insecure-requests", []],
  ];
  return directives.map(([name, sources]) => [name, ...sources].join(" ")).join("; ");
}

// Powerful features the app does not use (it reads no camera, microphone, location, sensor or
// device, takes no payment, and only WRITES the clipboard, which stays at its default). Switching
// them off here also keeps the LinkedIn embed and anything injected into the page from asking.
export const PERMISSIONS_POLICY = [
  "accelerometer",
  "bluetooth",
  "camera",
  "clipboard-read",
  "display-capture",
  "geolocation",
  "gyroscope",
  "hid",
  "magnetometer",
  "microphone",
  "midi",
  "payment",
  "serial",
  "usb",
  "xr-spatial-tracking",
]
  .map((feature) => `${feature}=()`)
  .join(", ");

// Hashed filenames never change, so a year and `immutable`; the HTML names them, so it must be
// revalidated or a new deploy would not be picked up.
export const IMMUTABLE_CACHE = "public, max-age=31536000, immutable";

const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/;

/** The `_headers` file. `assetsPrefix` is the URL path of the hashed assets ("/assets/").
 *
 *  Cloudflare applies every matching rule in file order, joining repeated headers with a comma, so
 *  `/*` (no-cache for the HTML) also matches the assets; the second rule removes that value with `!`
 *  before setting its own. The two lines of that second rule are applied removal first. */
export function renderHeadersFile(policy: string, assetsPrefix: string): string {
  // Defence in depth: every piece written into the file must be one line, or a stray newline would
  // start a rule of its own. The origins and path names are validated long before this; the message
  // names the piece and never quotes it.
  for (const [piece, value] of [
    ["policy", policy],
    ["assets path", assetsPrefix],
    ["permissions policy", PERMISSIONS_POLICY],
  ]) {
    if (CONTROL_CHARACTER.test(value)) {
      throw new Error(
        `Refusing to build: the ${piece} for the generated _headers file contains a control character, ` +
          "which would split a line of the file.",
      );
    }
  }
  const lines = [
    "# Generated by web/scripts/securityHeaders.ts during `vite build`. Do not edit: change the",
    "# generator, or the VITE_API_BASE_URL / VITE_SUPABASE_URL it reads. See web/README.md.",
    "",
    "/*",
    `  Content-Security-Policy: ${policy}`,
    "  X-Content-Type-Options: nosniff",
    "  Referrer-Policy: strict-origin-when-cross-origin",
    `  Permissions-Policy: ${PERMISSIONS_POLICY}`,
    "  X-Frame-Options: DENY",
    "  Cache-Control: no-cache",
    "",
    `${assetsPrefix}*`,
    "  ! Cache-Control",
    `  Cache-Control: ${IMMUTABLE_CACHE}`,
    "",
  ];
  for (const line of lines) {
    if (line.length > MAX_HEADERS_LINE) {
      throw new Error(
        `Refusing to build: a line of the generated _headers file is ${line.length} characters, ` +
          `over the ${MAX_HEADERS_LINE} Cloudflare Pages accepts (it would ignore the line, and with it the policy).`,
      );
    }
  }
  return lines.join("\n");
}

// ── putting it together ────────────────────────────────────────────────────

// One path name: what a file or directory is called in practice. This is also what is safe to write
// into a `_headers` line, where a space, a newline, `:` (a placeholder) and `*` (a splat) mean
// something else. `%` is allowed in the base only, where Vite percent-encodes a space.
const ASSETS_DIR_NAME = /^[A-Za-z0-9._~-]+$/;
const BASE_NAME = /^[A-Za-z0-9._~%-]+$/;
const isPlainName = (pattern: RegExp, name: string): boolean => pattern.test(name) && name !== "." && name !== "..";

/** The URL path prefix of the hashed assets, with both slashes ("/assets/"), from Vite's resolved
 *  `base` and `build.assetsDir`. A relative base ("./") means the site is served from the root.
 *  Refuses, in the style of the other build checks and without quoting the value, what it cannot
 *  express: no assets directory, names that are not plain, and a base on another origin. */
export function assetsPrefixFor(base: string, assetsDir: string): string {
  const dir = assetsDir.replace(/^\/+|\/+$/g, "");
  // With no assets directory the hashed files sit beside the HTML, and a cache rule for "everything
  // under the root" would make the HTML immutable too.
  if (dir === "") {
    throw new Error(
      "Refusing to build: build.assetsDir must name a directory, so the hashed files can be cached on their own.",
    );
  }
  if (!dir.split("/").every((name) => isPlainName(ASSETS_DIR_NAME, name))) {
    throw new Error(
      "Refusing to build: build.assetsDir must be a relative path of plain names (letters, digits and . _ ~ -, " +
        "separated by /), because it is written into the _headers file as a URL path.",
    );
  }
  // An absolute (https://cdn/...) or protocol-relative (//cdn/...) base puts the bundle on another origin.
  if (/^(?:[a-z][a-z0-9+.-]*:)?\/\//i.test(base)) {
    throw new Error(
      'Refusing to build: --base / `base` must be a path on the site\'s own origin (such as "/" or "/app/"). ' +
        "The Content-Security-Policy allows scripts and styles from 'self' only and the cache rule is a path on this host, " +
        "so assets served from another origin would be blocked.",
    );
  }
  let root = "/";
  if (base.startsWith("/")) {
    root = base.replace(/\/*$/, "/");
    // "/" has no names; "/app/" has one.
    if (!root.split("/").slice(1, -1).every((name) => isPlainName(BASE_NAME, name))) {
      throw new Error(
        "Refusing to build: --base / `base` must be a path of plain names (letters, digits and . _ ~ - %, " +
          "separated by /), because it is written into the _headers file as a URL path.",
      );
    }
  }
  return `${root}${dir}/`;
}

/** The two origins the policy needs, or an error that names every variable that is wrong. */
export function resolveOrigins(env: Record<string, unknown>): { apiOrigin: string; supabaseOrigin: string } {
  const read = (name: string): string | undefined => {
    const value = env[name];
    return typeof value === "string" ? value : undefined;
  };
  const api = parseOrigin(API_ENV, read(API_ENV));
  const supabase = parseOrigin(SUPABASE_ENV, read(SUPABASE_ENV));
  if (api.ok && supabase.ok) return { apiOrigin: api.origin, supabaseOrigin: supabase.origin };
  const problems = [api, supabase].flatMap((result) => (result.ok ? [] : [result.problem]));
  throw new Error(
    [
      "Refusing to build: the Content-Security-Policy for this bundle cannot be generated.",
      ...problems.map((problem) => `  - ${problem}`),
      "",
      "The policy lets the browser contact exactly the API and Supabase hosts this bundle is built against,",
      "so both must be https:// origins you control (not localhost). Pass the production values in the shell, e.g.",
      `  ${API_ENV}=https://api.example.com ${SUPABASE_ENV}=https://<project>.supabase.co npm run build`,
      "See web/README.md, \"Security headers\".",
    ].join("\n"),
  );
}

export interface HeadersInput {
  /** The resolved Vite env (shell over .env.local over .env), as `config.env`. */
  env: Record<string, unknown>;
  /** The built HTML documents, whose inline styles and scripts get hashed into the policy. */
  htmlDocuments: readonly string[];
  /** URL path of the hashed assets directory, with both slashes: "/assets/". */
  assetsPrefix: string;
}

/** The complete `_headers` file for a build. */
export async function buildHeadersFile(input: HeadersInput): Promise<string> {
  const { apiOrigin, supabaseOrigin } = resolveOrigins(input.env);
  // Not hashable, and the policy would refuse them in the browser only: dev and `vite preview` never
  // apply the file, so this is where a mistake can be caught before it ships.
  const attributes = [...new Set(input.htmlDocuments.flatMap(inlineAttributes))].sort();
  if (attributes.length > 0) {
    throw new Error(
      `Refusing to build: the built HTML has inline attributes (${attributes.join(", ")}) that the ` +
        "Content-Security-Policy would block. Move styles into the stylesheet and event handlers into a " +
        'script module (see web/README.md, "When something is blocked").',
    );
  }
  const blocks = input.htmlDocuments.map(inlineBlocks);
  const policy = buildPolicy({
    apiOrigin,
    supabaseOrigin,
    styleHashes: await hashesOf(blocks.flatMap((b) => b.styles)),
    scriptHashes: await hashesOf(blocks.flatMap((b) => b.scripts)),
  });
  return renderHeadersFile(policy, input.assetsPrefix);
}
