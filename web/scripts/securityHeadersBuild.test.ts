import { build, resolveConfig, type InlineConfig, type Logger, type Plugin } from "vite";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cspHash, inlineAttributes, inlineBlocks } from "./securityHeaders";

// These run the real production build, with the project's own vite.config.ts, in memory
// (`write: false`): what a build emits is what a deploy would publish. Nothing is written to disk
// (there is no @types/node here to reach the filesystem with, and no need to), and `envDir: false`
// keeps a developer's .env.local from changing what is asserted.

// `.pathname` keeps the URL's percent-encoding ("My%20Projects" for a folder with a space), so it is
// decoded to get a real path. node:url's fileURLToPath is not an option: @types/node is not installed.
const here = (relative: string): string => decodeURIComponent(new URL(relative, import.meta.url).pathname);
const ROOT = here("../");
const API = "https://api.example.com";
const SUPABASE = "https://project.supabase.example";

interface Emitted {
  type: "asset" | "chunk";
  fileName: string;
  source?: string | Uint8Array;
  code?: string;
}

async function runBuild(env: Record<string, string | undefined>, inline: InlineConfig = {}): Promise<Emitted[]> {
  vi.stubEnv("NODE_ENV", "production");
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
  const result = await build({
    root: ROOT,
    envDir: false,
    logLevel: "silent",
    ...inline,
    build: { write: false, ...inline.build },
  });
  const outputs = Array.isArray(result) ? result : [result];
  return outputs.flatMap((o) => (o as unknown as { output: Emitted[] }).output);
}

const text = (file: Emitted | undefined): string => {
  if (file === undefined) throw new Error("file not emitted");
  return typeof file.source === "string" ? file.source : new TextDecoder().decode(file.source);
};

afterEach(() => vi.unstubAllEnvs());

describe("the production build's security headers", () => {
  it("emits _headers at the root of the output, from the env the bundle is built with", async () => {
    const files = await runBuild({
      VITE_API_BASE_URL: API,
      VITE_SUPABASE_URL: SUPABASE,
      VITE_SUPABASE_PUBLISHABLE_KEY: "placeholder",
    });
    const headers = files.filter((f) => f.fileName === "_headers");
    expect(headers).toHaveLength(1);
    expect(headers[0].type).toBe("asset");
    // Emitted by name, with no directory: Rollup writes it into the output directory and nowhere else.
    expect(files.filter((f) => f.fileName.endsWith("_headers"))).toHaveLength(1);

    const file = text(headers[0]);
    expect(file).toContain(`connect-src ${API} ${SUPABASE};`);
    expect(file).toContain("/assets/*");
    expect(file).not.toContain("unsafe-inline");
    expect(file).not.toContain("unsafe-eval");
  });

  it("hashes the inline style Vite actually emitted, and the page has no inline script", async () => {
    const files = await runBuild({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE });
    const html = text(files.find((f) => f.fileName === "index.html"));
    const style = /<style>([\s\S]*?)<\/style>/.exec(html);
    expect(style).not.toBeNull();
    const expected = await cspHash((style as RegExpExecArray)[1]);
    expect(text(files.find((f) => f.fileName === "_headers"))).toContain(`style-src 'self' ${expected};`);

    // The tripwire for `script-src 'self'` with nothing after it: an inline script in the built page
    // would be hashed into the policy by the generator, so a new one has to be noticed here. The same
    // goes for an inline style or event-handler attribute, which cannot be hashed at all.
    expect(html).not.toMatch(/<script(?![^>]*\ssrc=)[^>]*>/i);
    expect(inlineBlocks(html).scripts).toEqual([]);
    expect(inlineAttributes(html)).toEqual([]);
    expect(text(files.find((f) => f.fileName === "_headers"))).toMatch(/script-src 'self';/);
  });

  it("names the host the bundle calls, not a path under it", async () => {
    const files = await runBuild({ VITE_API_BASE_URL: `${API}/v1/`, VITE_SUPABASE_URL: `${SUPABASE}/` });
    const file = text(files.find((f) => f.fileName === "_headers"));
    expect(file).toContain(`connect-src ${API} ${SUPABASE};`);
    // ... and the bundle really does carry that base, so the policy and the code cannot disagree.
    const code = files.filter((f) => f.type === "chunk").map((f) => f.code ?? "").join("\n");
    expect(code).toContain(API);
  });

  it("never lets Vite turn an asset into a data: URL, which img-src and font-src would refuse", async () => {
    vi.stubEnv("VITE_API_BASE_URL", API);
    vi.stubEnv("VITE_SUPABASE_URL", SUPABASE);
    const config = await resolveConfig({ root: ROOT, envDir: false, logLevel: "silent" }, "build", "production");
    expect(config.build.assetsInlineLimit).toBe(0);
  });

  it.each([
    ["unset", undefined],
    ["blank", "   "],
  ])("still fails the build through the existing guard when VITE_API_BASE_URL is %s", async (_label, value) => {
    await expect(runBuild({ VITE_API_BASE_URL: value, VITE_SUPABASE_URL: SUPABASE })).rejects.toThrowError(
      /Refusing to build: the API address for this bundle is wrong[\s\S]*VITE_API_BASE_URL is not set/,
    );
  });

  it("refuses an API address the guard accepts but a CSP cannot name", async () => {
    await expect(
      runBuild({ VITE_API_BASE_URL: "https://[2001:db8::1]:8443", VITE_SUPABASE_URL: SUPABASE }),
    ).rejects.toThrowError(/Content-Security-Policy[\s\S]*- VITE_API_BASE_URL has a host a Content-Security-Policy cannot name/);
  });

  it.each([
    ["unset", undefined, /- VITE_SUPABASE_URL is not set/],
    ["http", "http://sentinel-host.example", /- VITE_SUPABASE_URL is not an https:\/\/ URL/],
    ["local", "https://sentinel.localhost", /- VITE_SUPABASE_URL points at a local address/],
    ["a wildcard", "https://*.sentinel-host.example", /- VITE_SUPABASE_URL has a host/],
  ])("refuses a Supabase address that is %s, naming the variable and not the value", async (_label, value, message) => {
    const attempt = runBuild({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: value });
    await expect(attempt).rejects.toThrowError(message);
    await expect(attempt).rejects.not.toThrowError(/sentinel/);
  });

  it.each([
    ["userinfo", "https://user:sentinelsecret@api.example.com", /carries credentials/],
    ["a password with no username", "https://:sentinelsecret@api.example.com", /carries credentials/],
    ["a token in the query", "https://api.example.com/?token=sentinelsecret", /query string or fragment/],
    ["a token in the fragment", "https://api.example.com/#sentinelsecret", /query string or fragment/],
    ["cleartext http", "http://sentinelsecret.example.com", /not https/],
    ["a local address", "https://localhost/sentinelsecret", /local address/],
    ["something that is not a URL", "sentinelsecret", /not a valid absolute URL/],
  ])("refuses an API address with %s, naming the variable and not the value", async (_label, value, message) => {
    // The older API guard runs first and the build log is a CI log: neither may quote a credential.
    const attempt = runBuild({ VITE_API_BASE_URL: value, VITE_SUPABASE_URL: SUPABASE });
    await expect(attempt).rejects.toThrowError(/VITE_API_BASE_URL/);
    await expect(attempt).rejects.toThrowError(message);
    await expect(attempt).rejects.not.toThrowError(/sentinelsecret/);
  });

  it("refuses a Supabase address with credentials the same way", async () => {
    const attempt = runBuild({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: "https://:sentinelsecret@project.supabase.example" });
    await expect(attempt).rejects.toThrowError(/- VITE_SUPABASE_URL carries credentials/);
    await expect(attempt).rejects.not.toThrowError(/sentinelsecret/);
  });

  it("fails while the config resolves, before any bundling, when an origin is unusable", async () => {
    vi.stubEnv("VITE_API_BASE_URL", API);
    vi.stubEnv("VITE_SUPABASE_URL", "http://sentinel-host.example");
    await expect(
      resolveConfig({ root: ROOT, envDir: false, logLevel: "silent" }, "build", "production"),
    ).rejects.toThrowError(/- VITE_SUPABASE_URL is not an https:\/\/ URL/);
  });

  it("is only part of `vite build`: the dev server resolves without either address", async () => {
    vi.stubEnv("VITE_API_BASE_URL", API);
    vi.stubEnv("VITE_SUPABASE_URL", SUPABASE);
    const built = await resolveConfig({ root: ROOT, envDir: false, logLevel: "silent" }, "build", "production");
    expect(built.plugins.map((p) => p.name)).toContain("between-jobs:security-headers");

    vi.stubEnv("VITE_API_BASE_URL", undefined);
    vi.stubEnv("VITE_SUPABASE_URL", undefined);
    const served = await resolveConfig({ root: ROOT, envDir: false, logLevel: "silent" }, "serve", "development");
    expect(served.plugins.map((p) => p.name)).not.toContain("between-jobs:security-headers");
    expect(served.command).toBe("serve");
  });

  it("hashes the inline style of every HTML page in the bundle, and reads nothing else", async () => {
    const extra: Plugin = {
      name: "test:extra-assets",
      generateBundle() {
        this.emitFile({ type: "asset", fileName: "notes.txt", source: "<style>from-a-text-file</style><script>alert(1)</script>" });
        this.emitFile({ type: "asset", fileName: "second.html", source: "<!doctype html><style>from-a-second-page</style>" });
      },
    };
    const files = await runBuild({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE }, { plugins: [extra] });
    const file = text(files.find((f) => f.fileName === "_headers"));
    expect(file).toContain(await cspHash("from-a-second-page"));
    expect(file).not.toContain(await cspHash("from-a-text-file"));
    expect(file).not.toContain(await cspHash("alert(1)"));
    expect(file).toMatch(/script-src 'self';/);
  });

  it("puts the immutable cache rule on the assets directory the build really uses", async () => {
    const files = await runBuild(
      { VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE },
      { base: "/app/", build: { assetsDir: "static" } as InlineConfig["build"] },
    );
    const file = text(files.find((f) => f.fileName === "_headers"));
    expect(file).toContain("\n/app/static/*\n  ! Cache-Control\n");
    expect(file).not.toContain("/assets/*");
    expect(files.some((f) => f.fileName.startsWith("static/"))).toBe(true);
  });

  it.each([
    ["an https CDN", "https://cdn.example.com/x/"],
    ["a protocol-relative CDN", "//cdn.example.com/x/"],
  ])("refuses a base on another origin (%s), where the policy would blank the page", async (_label, base) => {
    const attempt = runBuild({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE }, { base });
    await expect(attempt).rejects.toThrowError(/--base \/ `base` must be a path on the site's own origin/);
    await expect(attempt).rejects.not.toThrowError(/cdn\.example/);
  });

  it.each([
    ["a newline", "st\n  X-Evil: 1"],
    ["a space", "a b"],
    ["a colon", "a:b"],
    ["an asterisk", "a*"],
  ])("refuses an assets directory with %s, so no extra rule can reach _headers", async (_label, assetsDir) => {
    // A refused build emits nothing, `_headers` included: the promise resolves to no output at all.
    const attempt = runBuild(
      { VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE },
      { build: { assetsDir } as InlineConfig["build"] },
    );
    await expect(attempt).rejects.toThrowError(/build\.assetsDir must be a relative path of plain names/);
    await expect(attempt).rejects.not.toThrowError(/X-Evil/);
  });

  it("refuses a base with a character that means something in _headers", async () => {
    await expect(
      runBuild({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE }, { base: "/a*/" }),
    ).rejects.toThrowError(/--base \/ `base` must be a path of plain names/);
  });

  it("refuses an unusable asset path while the config resolves, before any bundling", async () => {
    vi.stubEnv("VITE_API_BASE_URL", API);
    vi.stubEnv("VITE_SUPABASE_URL", SUPABASE);
    await expect(
      resolveConfig({ root: ROOT, envDir: false, logLevel: "silent", base: "https://cdn.example.com/" }, "build", "production"),
    ).rejects.toThrowError(/must be a path on the site's own origin/);
  });

  it.each([
    ["an inline style attribute", '<body style="background:#070607">', "style"],
    ["an inline event-handler attribute", '<body onload="document.title=1">', "onload"],
  ])("refuses a page with %s, which the policy would block only in the browser", async (_label, tag, name) => {
    const edit: Plugin = {
      name: "test:inline-attribute",
      transformIndexHtml: (html) => html.replace("<body>", tag),
    };
    const attempt = runBuild({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE }, { plugins: [edit] });
    await expect(attempt).rejects.toThrowError(`inline attributes (${name})`);
    await expect(attempt).rejects.not.toThrowError(/070607|document\.title/);
  });

  it("takes the policy from the resolved env, so values that live in an .env file count", async () => {
    // Nothing in the shell: the only source of the three values is the committed fixture directory.
    vi.stubEnv("VITE_API_BASE_URL", undefined);
    vi.stubEnv("VITE_SUPABASE_URL", undefined);
    const files = await runBuild({}, { envDir: here("./fixtures/envdir/") });
    expect(text(files.find((f) => f.fileName === "_headers"))).toContain(
      "connect-src https://api.from-env-file.example https://project.from-env-file.example;",
    );
  });

  it("lets a value in the shell win over the same value in an .env file", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.from-shell.example");
    vi.stubEnv("VITE_SUPABASE_URL", undefined);
    const files = await runBuild({}, { envDir: here("./fixtures/envdir/") });
    expect(text(files.find((f) => f.fileName === "_headers"))).toContain(
      "connect-src https://api.from-shell.example https://project.from-env-file.example;",
    );
  });

  it("hashes an HTML page emitted as bytes, not only as a string", async () => {
    const extra: Plugin = {
      name: "test:bytes-page",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "bytes.html",
          source: new TextEncoder().encode("<!doctype html><style>from-a-bytes-page</style>"),
        });
      },
    };
    const files = await runBuild({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE }, { plugins: [extra] });
    // The test is only meaningful if the page really reaches the plugin as a Uint8Array.
    expect(files.find((f) => f.fileName === "bytes.html")?.source).toBeInstanceOf(Uint8Array);
    expect(text(files.find((f) => f.fileName === "_headers"))).toContain(await cspHash("from-a-bytes-page"));
  });

  it("prints the whole _headers file once at info level, and never the env", async () => {
    const info: string[] = [];
    const warn: string[] = [];
    const logger: Logger = {
      hasWarned: false,
      info: (message) => void info.push(message),
      warn: (message) => void warn.push(message),
      warnOnce: (message) => void warn.push(message),
      error: () => undefined,
      clearScreen: () => undefined,
      hasErrorLogged: () => false,
    };
    const files = await runBuild(
      { VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE, VITE_SUPABASE_PUBLISHABLE_KEY: "sentinel-publishable-key" },
      { logLevel: "info", customLogger: logger },
    );
    const headers = text(files.find((f) => f.fileName === "_headers"));
    const printed = info.filter((message) => message.includes("Writing _headers"));
    expect(printed).toHaveLength(1);
    expect(printed[0]).toContain(headers);
    expect(warn.filter((message) => message.includes("Writing _headers"))).toEqual([]);
    expect([...info, ...warn].join("\n")).not.toContain("sentinel-publishable-key");
  });
});
