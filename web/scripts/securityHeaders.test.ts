import { describe, expect, it } from "vitest";
import indexHtml from "../index.html?raw";
import { EMBED_URL_PREFIX } from "../src/lib/hiringSignals";
import { isLocalHost } from "./apiBaseGuard";
import {
  IMMUTABLE_CACHE,
  LINKEDIN_EMBED_ORIGIN,
  MAX_HEADERS_LINE,
  PERMISSIONS_POLICY,
  assetsPrefixFor,
  buildHeadersFile,
  buildPolicy,
  cspHash,
  inlineAttributes,
  inlineBlocks,
  parseOrigin,
  renderHeadersFile,
  resolveOrigins,
} from "./securityHeaders";

// Hashes below were computed with Python's hashlib, not with the code under test; the two inline
// vectors were also reported by Chrome in its own CSP violation messages.
const HASH_EMPTY = "'sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU='";
const HASH_ABC = "'sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0='";
const HASH_A_LF_B = "'sha256-fhj3NzEbLcOy8mndeDlrA1HxT7Zu+oefdoyyMYGIPHg='";
const HASH_E_ACUTE = "'sha256-SplVfkAzw1Od4utlRyAXytX5VX96BiWgnxw/biumnEw='";
const HASH_BODY_MARGIN = "'sha256-IAdwN3biDCQ3brrgp1m8kBEsPQYyqfREKOEfeoREopc='"; // body{margin:0}
const HASH_PAGE_STYLE = "'sha256-OYcD7JiWW7rbCN6k+SxszRxvmN2TdJUeNS4OKszT0FI='"; // an arbitrary second style vector, not tied to index.html
const HASH_WINDOW_X = "'sha256-ZwqldfsJdNVMda3s8g8jPJXfQBqJpWcMt0bbYCrrSVw='"; // window.__x=1

const API = "https://api.example.com";
const SUPABASE = "https://project.supabase.example";

// Pinned as a literal rather than read from the module, so removing or adding a feature fails a test.
const GOLDEN_PERMISSIONS =
  "accelerometer=(), bluetooth=(), camera=(), clipboard-read=(), display-capture=(), geolocation=(), " +
  "gyroscope=(), hid=(), magnetometer=(), microphone=(), midi=(), payment=(), serial=(), usb=(), " +
  "xr-spatial-tracking=()";

describe("cspHash", () => {
  it.each([
    ["", HASH_EMPTY],
    ["abc", HASH_ABC],
    ["body{margin:0}", HASH_BODY_MARGIN],
    ["window.__x=1", HASH_WINDOW_X],
    ["é", HASH_E_ACUTE],
  ])("hashes %j", async (text, expected) => {
    expect(await cspHash(text)).toBe(expected);
  });

  it("hashes the text the HTML parser produces: CRLF and lone CR become LF", async () => {
    expect(await cspHash("a\nb")).toBe(HASH_A_LF_B);
    expect(await cspHash("a\r\nb")).toBe(HASH_A_LF_B);
    expect(await cspHash("a\rb")).toBe(HASH_A_LF_B);
    expect(await cspHash("a\n\nb")).not.toBe(HASH_A_LF_B);
    expect(await cspHash("a\r\n\r\nb")).toBe(await cspHash("a\n\nb"));
  });
});

// ── origins ────────────────────────────────────────────────────────────────

describe("parseOrigin", () => {
  it.each([
    ["a plain origin", "https://api.example.com", "https://api.example.com"],
    ["a trailing slash", "https://api.example.com/", "https://api.example.com"],
    ["surrounding whitespace", "  https://api.example.com  ", "https://api.example.com"],
    ["a base path, which is not part of an origin", "https://api.example.com/v1/base/", "https://api.example.com"],
    ["an upper-case host and scheme", "HTTPS://API.Example.COM", "https://api.example.com"],
    ["a port", "https://api.example.com:8443", "https://api.example.com:8443"],
    ["a port after a path", "https://api.example.com:8443/v1", "https://api.example.com:8443"],
    ["the default port, which a CSP source leaves out", "https://api.example.com:443", "https://api.example.com"],
    ["an internationalised host, as punycode", "https://bücher.example", "https://xn--bcher-kva.example"],
    ["an IPv4 address", "https://203.0.113.7", "https://203.0.113.7"],
    ["hyphens in labels", "https://my-api.eu-west-1.example.com", "https://my-api.eu-west-1.example.com"],
    ["a hosted Supabase project", "https://abcdefghijklmnop.supabase.co", "https://abcdefghijklmnop.supabase.co"],
    ["a single-label name", "https://intranet", "https://intranet"],
  ])("accepts %s", (_label, raw, origin) => {
    expect(parseOrigin("VITE_X", raw)).toEqual({ ok: true, origin });
  });

  it.each([
    ["undefined", undefined, /VITE_X is not set\./],
    ["empty", "", /VITE_X is not set\./],
    ["blank", "   ", /VITE_X is not set\./],
    ["cleartext http", "http://api.example.com", /not an https:\/\/ URL/],
    ["another scheme", "ftp://api.example.com", /not an https:\/\/ URL/],
    ["a bare host", "api.example.com", /not an https:\/\/ URL/],
    ["a path", "/api", /not an https:\/\/ URL/],
    ["a scheme with no slashes", "https:api.example.com", /not an https:\/\/ URL/],
    ["a scheme with one slash", "https:/api.example.com", /not an https:\/\/ URL/],
    ["a scheme and nothing else", "https://", /not an https:\/\/ URL/],
    ["https:// only inside the value", "http://api.example.com/https://other.example", /not an https:\/\/ URL/],
    ["a scheme that merely ends in https", "xhttps://api.example.com", /not an https:\/\/ URL/],
    ["a leading character before https", " ;https://api.example.com", /not an https:\/\/ URL/],
    ["an empty authority", "https:///api.example.com", /not an https:\/\/ URL/],
    ["a space in the host", "https://api .example.com", /not a valid URL/],
    ["an out-of-range port", "https://api.example.com:99999", /not a valid URL/],
    ["userinfo", "https://user:pass@api.example.com", /credentials/],
    ["a bare username", "https://user@api.example.com", /credentials/],
    ["a password with no username", "https://:pw@api.example.com", /credentials/],
    ["userinfo that reads like another host", "https://api.example.com@evil.example", /credentials/],
    ["a query", "https://api.example.com?x=1", /query string or fragment/],
    ["a query after a path", "https://api.example.com/v1?x=1", /query string or fragment/],
    ["an empty query", "https://api.example.com/?", /query string or fragment/],
    ["a fragment", "https://api.example.com#top", /query string or fragment/],
    ["an empty fragment", "https://api.example.com/v1#", /query string or fragment/],
    ["a wildcard subdomain", "https://*.example.com", /cannot name/],
    ["a wildcard host", "https://*", /cannot name/],
    ["a wildcard label", "https://api.*.com", /cannot name/],
    ["an IPv6 literal", "https://[2001:db8::1]", /cannot name/],
    ["an IPv6 literal with a port", "https://[2001:db8::1]:8443/v1", /cannot name/],
    ["an underscore", "https://my_api.example.com", /cannot name/],
    ["a trailing dot", "https://api.example.com.", /cannot name/],
    ["a semicolon, which would start a new directive", "https://api.example.com;script-src", /cannot name/],
    ["a comma, which would start a new policy", "https://a.example.com,b.example.com", /cannot name/],
    ["a quote", "https://a.example.com'x", /cannot name/],
    ["a leading hyphen", "https://-api.example.com", /cannot name/],
    ["a trailing hyphen", "https://api-.example.com", /cannot name/],
    ["an empty label", "https://api..example.com", /cannot name/],
  ])("rejects %s", (_label, raw, problem) => {
    const result = parseOrigin("VITE_X", raw);
    expect(result.ok).toBe(false);
    expect(result.ok ? "" : result.problem).toMatch(problem);
  });

  it.each([
    "https://localhost",
    "https://LOCALHOST:8000",
    "https://127.0.0.1",
    "https://0.0.0.0:8000",
    "https://api.localhost",
    "https://printer.local",
    "https://2130706433", // 127.0.0.1 written as one number: the URL parser normalises it
    "https://0x7f.1",
  ])("rejects a local address (%s)", (raw) => {
    const result = parseOrigin("VITE_X", raw);
    expect(result.ok).toBe(false);
    expect(result.ok ? "" : result.problem).toMatch(/local address/);
  });

  it.each([
    [undefined, "VITE_X is not set."],
    ["http://a.example", "VITE_X is not an https:// URL."],
    ["https://a b.example", "VITE_X is not a valid URL."],
    ["https://u:p@a.example", "VITE_X carries credentials in the URL."],
    ["https://a.example/?q", "VITE_X has a query string or fragment."],
    [
      "https://*.example",
      "VITE_X has a host a Content-Security-Policy cannot name (use a plain DNS name or IPv4 address: " +
        "letters, digits, hyphens and dots; no wildcard, underscore or IPv6 literal).",
    ],
    ["https://localhost", "VITE_X points at a local address."],
  ])("says exactly what is wrong with %j", (raw, problem) => {
    expect(parseOrigin("VITE_X", raw)).toEqual({ ok: false, problem });
  });

  it("names the variable it was given, first", () => {
    for (const raw of [undefined, "http://x.example", "https://u:p@x.example", "https://*.example", "https://localhost"]) {
      const result = parseOrigin("VITE_SOMETHING", raw);
      expect(result.ok ? "" : result.problem.startsWith("VITE_SOMETHING ")).toBe(true);
    }
  });

  it("never prints the value, which can carry credentials", () => {
    const secret = "hunter2-sentinel";
    for (const raw of [
      `https://user:${secret}@host-sentinel.example`,
      `https://:${secret}@host-sentinel.example`,
      `http://host-sentinel.example/${secret}`,
      `https://host-sentinel.example/?token=${secret}`,
      `https://host-sentinel.example#${secret}`,
      `https://*.host-sentinel.example/${secret}`,
      `https://host-sentinel.example:99999/${secret}`,
      `https://localhost/${secret}`,
      `host-sentinel.example/${secret}`,
    ]) {
      const result = parseOrigin("VITE_X", raw);
      expect(result.ok).toBe(false);
      const problem = result.ok ? "" : result.problem;
      expect(problem).not.toContain(secret);
      expect(problem).not.toContain("host-sentinel");
    }
  });
});

describe("isLocalHost", () => {
  it.each(["localhost", "LocalHost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "a.localhost", "b.local", "B.LOCAL"])(
    "%s is local",
    (host) => expect(isLocalHost(host)).toBe(true),
  );
  it.each(["example.com", "localhost.example.com", "notlocal", "mylocalhost", "api.local.example.com", "127.0.0.2"])(
    "%s is not",
    (host) => expect(isLocalHost(host)).toBe(false),
  );
});

describe("resolveOrigins", () => {
  it("returns both origins", () => {
    expect(resolveOrigins({ VITE_API_BASE_URL: `${API}/v1/`, VITE_SUPABASE_URL: SUPABASE })).toEqual({
      apiOrigin: API,
      supabaseOrigin: SUPABASE,
    });
  });

  it("reads each from its own variable", () => {
    const { apiOrigin, supabaseOrigin } = resolveOrigins({
      VITE_API_BASE_URL: "https://one.example",
      VITE_SUPABASE_URL: "https://two.example",
    });
    expect(apiOrigin).toBe("https://one.example");
    expect(supabaseOrigin).toBe("https://two.example");
  });

  it("names every variable that is wrong, in one error, with the fix", () => {
    const run = () => resolveOrigins({ VITE_API_BASE_URL: "http://sentinel-a.example", VITE_SUPABASE_URL: undefined });
    expect(run).toThrowError(/Refusing to build: the Content-Security-Policy for this bundle cannot be generated/);
    expect(run).toThrowError(/- VITE_API_BASE_URL is not an https:\/\/ URL/);
    expect(run).toThrowError(/- VITE_SUPABASE_URL is not set/);
    expect(run).toThrowError(/npm run build/);
    expect(run).toThrowError(/web\/README\.md/);
  });

  it("words the whole error as the operator reads it", () => {
    const tail = [
      "",
      "The policy lets the browser contact exactly the API and Supabase hosts this bundle is built against,",
      "so both must be https:// origins you control (not localhost). Pass the production values in the shell, e.g.",
      "  VITE_API_BASE_URL=https://api.example.com VITE_SUPABASE_URL=https://<project>.supabase.co npm run build",
      'See web/README.md, "Security headers".',
    ];
    const head = "Refusing to build: the Content-Security-Policy for this bundle cannot be generated.";
    const messageOf = (env: Record<string, unknown>): string => {
      try {
        resolveOrigins(env);
      } catch (error) {
        return (error as Error).message;
      }
      throw new Error("expected resolveOrigins to throw");
    };
    expect(messageOf({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: undefined })).toBe(
      [head, "  - VITE_SUPABASE_URL is not set.", ...tail].join("\n"),
    );
    expect(messageOf({ VITE_API_BASE_URL: "http://a.example", VITE_SUPABASE_URL: "https://localhost" })).toBe(
      [
        head,
        "  - VITE_API_BASE_URL is not an https:// URL.",
        "  - VITE_SUPABASE_URL points at a local address.",
        ...tail,
      ].join("\n"),
    );
  });

  it("reports only the variable that is wrong", () => {
    expect(() => resolveOrigins({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: "" })).toThrowError(
      /- VITE_SUPABASE_URL is not set/,
    );
    try {
      resolveOrigins({ VITE_API_BASE_URL: API, VITE_SUPABASE_URL: "" });
    } catch (error) {
      expect(String(error)).not.toMatch(/- VITE_API_BASE_URL/);
    }
    expect(() => resolveOrigins({ VITE_API_BASE_URL: "", VITE_SUPABASE_URL: SUPABASE })).toThrowError(
      /- VITE_API_BASE_URL is not set/,
    );
    try {
      resolveOrigins({ VITE_API_BASE_URL: "", VITE_SUPABASE_URL: SUPABASE });
    } catch (error) {
      expect(String(error)).not.toMatch(/- VITE_SUPABASE_URL/);
    }
  });

  it("treats a value that is not a string as unset", () => {
    expect(() => resolveOrigins({ VITE_API_BASE_URL: 42, VITE_SUPABASE_URL: SUPABASE })).toThrowError(
      /VITE_API_BASE_URL is not set/,
    );
    expect(() => resolveOrigins({})).toThrowError(/VITE_API_BASE_URL is not set[\s\S]*VITE_SUPABASE_URL is not set/);
  });

  it("never prints a value", () => {
    const run = () =>
      resolveOrigins({
        VITE_API_BASE_URL: "https://user:hunter2-sentinel@api.sentinel.example",
        VITE_SUPABASE_URL: "http://supabase.sentinel.example",
      });
    expect(run).toThrowError(/credentials/);
    expect(run).not.toThrowError(/hunter2|sentinel/);
  });
});

// ── inline blocks ──────────────────────────────────────────────────────────

describe("inlineBlocks", () => {
  it("finds the page's own inline style and no script in the source index.html", () => {
    const { styles, scripts } = inlineBlocks(indexHtml);
    expect(styles).toHaveLength(1);
    // The pre-paint rule, whatever its colour: a design change to it must not fail this test.
    expect(styles[0]).toMatch(/\bhtml\s*\{[^}]*\bbackground:/);
    expect(scripts).toEqual([]); // the module script has a src
  });

  it("returns a block's text exactly, whitespace included", () => {
    expect(inlineBlocks("<style>\n  a { b: c }\n</style>").styles).toEqual(["\n  a { b: c }\n"]);
    expect(inlineBlocks("<script>  x  </script>").scripts).toEqual(["  x  "]);
    expect(inlineBlocks("<style></style>").styles).toEqual([""]);
  });

  it("finds every block, in order, whatever the tag's case or attributes", () => {
    const html = `<head><STYLE media="print">one</STYLE><style>two</style><Script>three</Script>
      <script type="module">four</script><script nonce="x" defer>five</script></head>`;
    expect(inlineBlocks(html)).toEqual({ styles: ["one", "two"], scripts: ["three", "four", "five"] });
  });

  it("accepts a closing tag with whitespace", () => {
    expect(inlineBlocks("<style>a</style >").styles).toEqual(["a"]);
    expect(inlineBlocks("<script>a</script\n>").scripts).toEqual(["a"]);
  });

  it("skips a script with a src, in any case or quoting", () => {
    expect(
      inlineBlocks(
        `<script src="/a.js"></script><script type="module" crossorigin src="/b.js"></script><script SRC='/c.js'></script><script defer src=/d.js></script>`,
      ).scripts,
    ).toEqual([]);
  });

  it("skips data blocks, which script-src does not govern, and keeps executable types", () => {
    const html = [
      `<script type="application/json">{"a":1}</script>`,
      `<script type="application/ld+json">{}</script>`,
      `<script type="text/template"><p></p></script>`,
      `<script type="module">m</script>`,
      `<script type="MODULE">upper</script>`,
      `<script type='module'>single</script>`,
      `<script type=module>bare</script>`,
      `<script type="importmap">{}</script>`,
      `<script type="text/javascript">tj</script>`,
      `<script type="application/javascript">aj</script>`,
      `<script type="">empty</script>`,
    ].join("");
    expect(inlineBlocks(html).scripts).toEqual(["m", "upper", "single", "bare", "{}", "tj", "aj", "empty"]);
  });

  it("reads an attribute name in any case", () => {
    expect(inlineBlocks(`<script TYPE="application/json">{}</script><script Type='module'>m</script>`).scripts).toEqual(["m"]);
    expect(inlineBlocks(`<script SRC="/a.js"></script>`).scripts).toEqual([]);
  });

  it("keeps a script whose attribute names merely end in src or type", () => {
    const html = `<script data-src="x">f()</script><script data-type="application/json">g()</script>`;
    expect(inlineBlocks(html).scripts).toEqual(["f()", "g()"]);
  });

  it("does not take a tag whose name merely starts like style or script", () => {
    expect(inlineBlocks("<style=x>a</style>")).toEqual({ styles: [], scripts: [] });
    expect(inlineBlocks("<script=x>a</script>")).toEqual({ styles: [], scripts: [] });
    expect(inlineBlocks("<style/>a</style>").styles).toEqual(["a"]);
  });

  it("ignores blocks inside HTML comments", () => {
    const html = "<!-- <style>commented</style> <script>commented()</script> --><style>live</style><!--x--><script>live()</script>";
    expect(inlineBlocks(html)).toEqual({ styles: ["live"], scripts: ["live()"] });
  });

  it("does not mistake other elements that start with the same letters", () => {
    expect(inlineBlocks("<style-box>x</style-box><scripted>y</scripted><stylesheet>z</stylesheet>")).toEqual({
      styles: [],
      scripts: [],
    });
  });

  it("finds nothing in a document with neither", () => {
    expect(inlineBlocks("<html><body><p>hi</p><link rel=stylesheet href=/a.css></body></html>")).toEqual({
      styles: [],
      scripts: [],
    });
  });

  it("keeps a block's own line endings, for the hash to normalise", () => {
    expect(inlineBlocks("<style>a\r\nb</style>").styles).toEqual(["a\r\nb"]);
  });
});

describe("inlineAttributes", () => {
  it("finds an inline style attribute", () => {
    expect(inlineAttributes(`<body style="x">`)).toEqual(["style"]);
    expect(inlineAttributes(`<body style='background:#000'>`)).toEqual(["style"]);
    expect(inlineAttributes(`<body style=color:red>`)).toEqual(["style"]);
    expect(inlineAttributes(`<body style = "x">`)).toEqual(["style"]);
  });

  it("finds an event-handler attribute, in any case", () => {
    expect(inlineAttributes(`<body onload="f()">`)).toEqual(["onload"]);
    expect(inlineAttributes(`<a onClick=f>`)).toEqual(["onclick"]);
    expect(inlineAttributes(`<a ONCLICK="f()" OnMouseOver='g()'>`)).toEqual(["onclick", "onmouseover"]);
    expect(inlineAttributes(`<STYLE media="print" STYLE="x">a</STYLE>`)).toEqual(["style"]);
  });

  it("finds them in document order, across tags and lines, on a tag that has other attributes", () => {
    const html = `<html><body\n  class="a"\n  style="x"\n  data-k=v><p onclick="f()" id=p>t</p></body></html>`;
    expect(inlineAttributes(html)).toEqual(["style", "onclick"]);
  });

  it("finds one on a script or style element's own start tag", () => {
    expect(inlineAttributes(`<script type="module" onerror="f()" src="/a.js"></script>`)).toEqual(["onerror"]);
    expect(inlineAttributes(`<style onload="f()">a{}</style>`)).toEqual(["onload"]);
  });

  it("is not fooled by a > inside a quoted value", () => {
    expect(inlineAttributes(`<a title="a > b" style="x">`)).toEqual(["style"]);
    expect(inlineAttributes(`<a title='a > b' onclick='f()'>`)).toEqual(["onclick"]);
  });

  it("ignores an attribute whose name merely contains the word", () => {
    expect(inlineAttributes(`<a data-style="x" data-onclick="y" xstyle="z" styled="w" onx-y="v" on="u">`)).toEqual([]);
    expect(inlineAttributes(`<a class="style" title="onclick=f()" data-x="style=y">`)).toEqual([]);
  });

  it("ignores the words inside a <style> or <script> body, which is CSS or JavaScript, not markup", () => {
    expect(inlineAttributes(`<style>a { style=1 } <p style="x"> onclick=f</style>`)).toEqual([]);
    expect(inlineAttributes(`<script>document.write('<a onclick="f()" style="x">')</script>`)).toEqual([]);
    expect(inlineAttributes(`<script type="module">if (a<b && c>d) { x.style = 1 }</script>`)).toEqual([]);
    // ... but not what follows the element.
    expect(inlineAttributes(`<style>a{}</style><p style="x">`)).toEqual(["style"]);
  });

  it("ignores a commented-out tag", () => {
    expect(inlineAttributes(`<!-- <body style="x" onload="f()"> --><body>`)).toEqual([]);
    expect(inlineAttributes(`<!--x--><p style="x"><!--y-->`)).toEqual(["style"]);
  });

  it("finds nothing in markup with neither, or in text that only looks like a tag", () => {
    expect(inlineAttributes(`<!doctype html><html lang="en"><head><meta charset="UTF-8" /><title>T</title></head><body>a < b style=x</body></html>`)).toEqual([]);
    expect(inlineAttributes("")).toEqual([]);
  });

  it("finds none in the source index.html, and the pre-paint style stays a <style> block", () => {
    expect(inlineAttributes(indexHtml)).toEqual([]);
    expect(inlineBlocks(indexHtml).styles).toHaveLength(1);
  });
});

// ── the policy ─────────────────────────────────────────────────────────────

const FIXED_INPUT = {
  apiOrigin: API,
  supabaseOrigin: SUPABASE,
  styleHashes: [HASH_BODY_MARGIN],
  scriptHashes: [],
};

const GOLDEN_POLICY =
  "default-src 'none'; " +
  "script-src 'self'; " +
  `style-src 'self' ${HASH_BODY_MARGIN}; ` +
  "img-src 'self'; " +
  "font-src 'self'; " +
  "manifest-src 'self'; " +
  "connect-src https://api.example.com https://project.supabase.example; " +
  "frame-src https://www.linkedin.com; " +
  "worker-src 'none'; " +
  "object-src 'none'; " +
  "base-uri 'self'; " +
  "form-action 'self'; " +
  "frame-ancestors 'none'; " +
  "upgrade-insecure-requests";

function directivesOf(policy: string): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const part of policy.split("; ")) {
    const [name, ...sources] = part.split(" ");
    expect(map.has(name)).toBe(false); // a repeated directive would be ignored by the browser
    map.set(name, sources);
  }
  return map;
}

describe("buildPolicy", () => {
  it("is exactly the pinned policy for a fixed input", () => {
    expect(buildPolicy(FIXED_INPUT)).toBe(GOLDEN_POLICY);
  });

  it("never allows eval, inline script, a wildcard, or a scheme-wide source", () => {
    const policy = buildPolicy({ ...FIXED_INPUT, styleHashes: [HASH_PAGE_STYLE, HASH_BODY_MARGIN], scriptHashes: [] });
    expect(policy).not.toContain("'unsafe-eval'");
    expect(policy).not.toContain("'unsafe-inline'");
    expect(policy).not.toContain("'unsafe-hashes'");
    expect(policy).not.toContain("'wasm-unsafe-eval'");
    expect(policy).not.toContain("'strict-dynamic'");
    for (const [, sources] of directivesOf(policy)) {
      for (const source of sources) {
        expect(source).not.toBe("*");
        expect(source).not.toContain("*");
        expect(source).not.toMatch(/^[a-z][a-z0-9+.-]*:$/i); // https:, data:, blob:, http:, ws:
        expect(source).not.toMatch(/^http:/i);
      }
    }
    const scriptSrc = directivesOf(policy).get("script-src");
    expect(scriptSrc).toEqual(["'self'"]);
  });

  it("has every directive once, and the locked-down ones locked down", () => {
    const directives = directivesOf(buildPolicy(FIXED_INPUT));
    expect([...directives.keys()]).toEqual([
      "default-src",
      "script-src",
      "style-src",
      "img-src",
      "font-src",
      "manifest-src",
      "connect-src",
      "frame-src",
      "worker-src",
      "object-src",
      "base-uri",
      "form-action",
      "frame-ancestors",
      "upgrade-insecure-requests",
    ]);
    expect(directives.get("default-src")).toEqual(["'none'"]);
    expect(directives.get("object-src")).toEqual(["'none'"]);
    expect(directives.get("worker-src")).toEqual(["'none'"]);
    expect(directives.get("frame-ancestors")).toEqual(["'none'"]);
    expect(directives.get("base-uri")).toEqual(["'self'"]);
    expect(directives.get("form-action")).toEqual(["'self'"]);
    expect(directives.get("img-src")).toEqual(["'self'"]);
    expect(directives.get("font-src")).toEqual(["'self'"]);
    expect(directives.get("manifest-src")).toEqual(["'self'"]);
    expect(directives.get("upgrade-insecure-requests")).toEqual([]);
  });

  it("lists each origin exactly once, in connect-src only", () => {
    const policy = buildPolicy(FIXED_INPUT);
    expect(policy.split(API).length - 1).toBe(1);
    expect(policy.split(SUPABASE).length - 1).toBe(1);
    expect(directivesOf(policy).get("connect-src")).toEqual([API, SUPABASE]);
    expect(directivesOf(policy).get("connect-src")).not.toContain("'self'");
  });

  it("lists one origin once when the API and Supabase share a host", () => {
    const policy = buildPolicy({ ...FIXED_INPUT, supabaseOrigin: API });
    expect(directivesOf(policy).get("connect-src")).toEqual([API]);
    expect(policy.split(API).length - 1).toBe(1);
  });

  it("opens one third-party origin to frames: the one the LinkedIn embed uses", () => {
    expect(directivesOf(buildPolicy(FIXED_INPUT)).get("frame-src")).toEqual([LINKEDIN_EMBED_ORIGIN]);
    // The policy and the code that builds the iframe address must name the same host.
    expect(new URL(EMBED_URL_PREFIX).origin).toBe(LINKEDIN_EMBED_ORIGIN);
    expect(LINKEDIN_EMBED_ORIGIN).toBe("https://www.linkedin.com");
  });

  it("puts style hashes in style-src and script hashes in script-src, and nowhere else", () => {
    const policy = buildPolicy({
      ...FIXED_INPUT,
      styleHashes: [HASH_BODY_MARGIN, HASH_PAGE_STYLE],
      scriptHashes: [HASH_WINDOW_X],
    });
    const directives = directivesOf(policy);
    expect(directives.get("style-src")).toEqual(["'self'", HASH_BODY_MARGIN, HASH_PAGE_STYLE]);
    expect(directives.get("script-src")).toEqual(["'self'", HASH_WINDOW_X]);
    for (const hash of [HASH_BODY_MARGIN, HASH_PAGE_STYLE, HASH_WINDOW_X]) {
      expect(policy.split(hash).length - 1).toBe(1);
    }
  });

  it("is a single line with no characters that could split a header", () => {
    const policy = buildPolicy(FIXED_INPUT);
    expect(policy).not.toMatch(/[\r\n,]/);
    expect(policy).not.toMatch(/;\s*;/);
    expect(policy.endsWith(";")).toBe(false);
  });
});

// ── the file ───────────────────────────────────────────────────────────────

describe("PERMISSIONS_POLICY", () => {
  it("is exactly the pinned list", () => {
    expect(PERMISSIONS_POLICY).toBe(GOLDEN_PERMISSIONS);
  });

  it("switches off the powerful features the app never uses", () => {
    for (const feature of ["camera", "microphone", "geolocation", "payment", "usb", "serial", "hid", "bluetooth"]) {
      expect(PERMISSIONS_POLICY).toContain(`${feature}=()`);
    }
  });

  it("leaves alone what the app does use, and allows nothing", () => {
    expect(PERMISSIONS_POLICY).not.toContain("clipboard-write");
    expect(PERMISSIONS_POLICY).not.toContain("fullscreen");
    expect(PERMISSIONS_POLICY).not.toContain("self");
    expect(PERMISSIONS_POLICY).not.toContain("*");
    for (const entry of PERMISSIONS_POLICY.split(", ")) expect(entry).toMatch(/^[a-z-]+=\(\)$/);
  });
});

const GOLDEN_FILE =
  [
    "# Generated by web/scripts/securityHeaders.ts during `vite build`. Do not edit: change the",
    "# generator, or the VITE_API_BASE_URL / VITE_SUPABASE_URL it reads. See web/README.md.",
    "",
    "/*",
    `  Content-Security-Policy: ${GOLDEN_POLICY}`,
    "  X-Content-Type-Options: nosniff",
    "  Referrer-Policy: strict-origin-when-cross-origin",
    `  Permissions-Policy: ${GOLDEN_PERMISSIONS}`,
    "  X-Frame-Options: DENY",
    "  Cache-Control: no-cache",
    "",
    "/assets/*",
    "  ! Cache-Control",
    "  Cache-Control: public, max-age=31536000, immutable",
  ].join("\n") + "\n";

// What Cloudflare Pages does with a `_headers` file, as its open-source implementation does it
// (workers-sdk: parseHeaders and attachCustomHeaders): rules are read top to bottom; a request takes
// the headers of EVERY rule whose pattern matches, in file order; within a rule the `! Name` removals
// run before the sets; a name set by more than one matching rule is joined with a comma.
interface Rule {
  path: string;
  set: Record<string, string>;
  unset: string[];
}

function parseHeadersFile(text: string): { rules: Rule[]; invalid: string[] } {
  const rules: Rule[] = [];
  const invalid: string[] = [];
  let rule: Rule | undefined;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line === "" || line.startsWith("#")) continue;
    if (line.length > MAX_HEADERS_LINE) {
      invalid.push(`too long: ${line.slice(0, 30)}`);
      continue;
    }
    if (/^([^\s]+:\/\/|^\/)/.test(line)) {
      if (rule) rules.push(rule);
      rule = { path: line, set: {}, unset: [] };
      continue;
    }
    if (!line.includes(":")) {
      if (rule && line.startsWith("! ")) rule.unset.push(line.replace("! ", "").trim().toLowerCase());
      else invalid.push(`expected name: value: ${line}`);
      continue;
    }
    const [rawName, ...rawValue] = line.split(":");
    const name = rawName.trim().toLowerCase();
    const value = rawValue.join(":").trim();
    if (name === "" || name.includes(" ") || value === "" || !rule) {
      invalid.push(`bad header line: ${line.slice(0, 30)}`);
      continue;
    }
    rule.set[name] = rule.set[name] ? `${rule.set[name]}, ${value}` : value;
  }
  if (rule) rules.push(rule);
  return { rules, invalid };
}

function headersServedFor(file: string, pathname: string): Record<string, string> {
  const out: Record<string, string> = {};
  const seen = new Set<string>();
  for (const rule of parseHeadersFile(file).rules) {
    const pattern = new RegExp(`^${rule.path.split("*").map((p) => p.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`);
    if (!pattern.test(pathname)) continue;
    for (const name of rule.unset) delete out[name];
    for (const [name, value] of Object.entries(rule.set)) {
      out[name] = seen.has(name) && name in out ? `${out[name]}, ${value}` : value;
      seen.add(name);
    }
  }
  return out;
}

describe("renderHeadersFile", () => {
  const file = renderHeadersFile(GOLDEN_POLICY, "/assets/");

  it("is exactly the pinned file for a fixed policy", () => {
    expect(file).toBe(GOLDEN_FILE);
  });

  it("parses cleanly the way Cloudflare Pages parses it", () => {
    const { rules, invalid } = parseHeadersFile(file);
    expect(invalid).toEqual([]);
    expect(rules.map((r) => r.path)).toEqual(["/*", "/assets/*"]);
    expect(rules[1].unset).toEqual(["cache-control"]);
  });

  it("serves the policy and the security headers on the HTML, wherever the route", () => {
    for (const path of ["/", "/index.html", "/login", "/applications/42", "/privacy", "/anything/else"]) {
      const headers = headersServedFor(file, path);
      expect(headers["content-security-policy"]).toBe(GOLDEN_POLICY);
      expect(headers["x-content-type-options"]).toBe("nosniff");
      expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
      expect(headers["permissions-policy"]).toBe(GOLDEN_PERMISSIONS);
      expect(headers["x-frame-options"]).toBe("DENY");
      expect(headers["cache-control"]).toBe("no-cache");
    }
  });

  it("caches the hashed assets for a year and nothing else, with no revalidation value left over", () => {
    for (const path of ["/assets/index-AbC123.js", "/assets/index-AbC123.css", "/assets/a/b.woff2"]) {
      const headers = headersServedFor(file, path);
      expect(headers["cache-control"]).toBe(IMMUTABLE_CACHE);
      expect(headers["cache-control"]).not.toContain("no-cache");
      expect(headers["content-security-policy"]).toBe(GOLDEN_POLICY);
    }
    expect(headersServedFor(file, "/assetsfoo/x.js")["cache-control"]).toBe("no-cache");
    expect(headersServedFor(file, "/assets")["cache-control"]).toBe("no-cache");
    expect(headersServedFor(file, "/other/assets/x.js")["cache-control"]).toBe("no-cache");
  });

  it("sets each header once per matching rule, and no HSTS, COOP or COEP", () => {
    const lower = file.toLowerCase();
    for (const name of ["strict-transport-security", "cross-origin-opener-policy", "cross-origin-embedder-policy"]) {
      expect(lower).not.toContain(name);
    }
    const headers = headersServedFor(file, "/");
    expect(Object.keys(headers).sort()).toEqual([
      "cache-control",
      "content-security-policy",
      "permissions-policy",
      "referrer-policy",
      "x-content-type-options",
      "x-frame-options",
    ]);
  });

  it("uses the assets prefix it is given", () => {
    const other = renderHeadersFile(GOLDEN_POLICY, "/app/static/");
    expect(parseHeadersFile(other).rules.map((r) => r.path)).toEqual(["/*", "/app/static/*"]);
    expect(headersServedFor(other, "/app/static/x.js")["cache-control"]).toBe(IMMUTABLE_CACHE);
    expect(headersServedFor(other, "/assets/x.js")["cache-control"]).toBe("no-cache");
  });

  it.each([
    ["a newline in the policy", "default-src 'none'\n  X-Evil: 1", "/assets/", /policy .* control character/],
    ["a carriage return in the policy", "default-src 'none'\r", "/assets/", /policy .* control character/],
    ["a tab in the policy", "default-src\t'none'", "/assets/", /policy .* control character/],
    ["a newline in the assets path", GOLDEN_POLICY, "/a\n  X-Evil: 1\n/z/", /assets path .* control character/],
    ["a DEL in the assets path", GOLDEN_POLICY, "/a\u007f/", /assets path .* control character/],
  ])("refuses %s, naming the piece and not quoting it", (_label, policy, prefix, message) => {
    expect(() => renderHeadersFile(policy, prefix)).toThrowError(message);
    expect(() => renderHeadersFile(policy, prefix)).not.toThrowError(/X-Evil/);
  });

  it("ends with a newline and has no carriage return", () => {
    expect(file.endsWith("\n")).toBe(true);
    expect(file).not.toContain("\r");
  });

  it("accepts a line of exactly the length Cloudflare allows and refuses one character more", () => {
    const prefix = "  Content-Security-Policy: ";
    const fits = "x".repeat(MAX_HEADERS_LINE - prefix.length);
    expect(() => renderHeadersFile(fits, "/assets/")).not.toThrow();
    expect(parseHeadersFile(renderHeadersFile(fits, "/assets/")).invalid).toEqual([]);
    expect(() => renderHeadersFile(`${fits}x`, "/assets/")).toThrowError(
      /2001 characters, over the 2000 Cloudflare Pages accepts/,
    );
  });
});

describe("assetsPrefixFor", () => {
  it.each([
    ["/", "assets", "/assets/"],
    ["/", "/assets/", "/assets/"],
    ["/", "static/js", "/static/js/"],
    ["/app/", "assets", "/app/assets/"],
    ["/app", "assets", "/app/assets/"],
    ["./", "assets", "/assets/"],
    ["", "assets", "/assets/"],
  ])("base %j and assetsDir %j give %j", (base, dir, expected) => {
    expect(assetsPrefixFor(base, dir)).toBe(expected);
  });

  it("refuses an empty assets directory, which would make the HTML immutable", () => {
    expect(() => assetsPrefixFor("/", "")).toThrowError(/assetsDir must name a directory/);
    expect(() => assetsPrefixFor("/", "/")).toThrowError(/assetsDir must name a directory/);
  });

  it.each([
    ["an https CDN", "https://cdn.example.com/x/"],
    ["an http CDN", "http://cdn.example.com/"],
    ["a protocol-relative CDN", "//cdn.example.com/x/"],
    ["an upper-case scheme", "HTTPS://cdn.example.com/"],
    ["a bare origin", "https://cdn.example.com"],
  ])("refuses a base on another origin: %s", (_label, base) => {
    expect(() => assetsPrefixFor(base, "assets")).toThrowError(/Refusing to build: --base \/ `base` must be a path on the site's own origin/);
    expect(() => assetsPrefixFor(base, "assets")).not.toThrowError(/cdn\.example/);
  });

  it.each([
    ["a newline, which would start a rule of its own", "st\n  X-Evil: 1"],
    ["a CRLF", "a\r\nb"],
    ["a space", "a b"],
    ["a colon, which Cloudflare reads as a placeholder", "a:b"],
    ["an asterisk, which it reads as a splat", "a*"],
    ["a hash", "a#b"],
    ["a question mark", "a?b"],
    ["a percent sign", "a%20b"],
    ["a dot segment", "./assets"],
    ["a parent segment", ".."],
    ["a parent segment inside", "a/../b"],
    ["an empty segment", "a//b"],
    ["a tab", "a\tb"],
    ["a non-ASCII name", "assétss"],
  ])("refuses an assets directory with %s, and does not quote it", (_label, dir) => {
    expect(() => assetsPrefixFor("/", dir)).toThrowError(/Refusing to build: build\.assetsDir must be a relative path of plain names/);
    expect(() => assetsPrefixFor("/", dir)).not.toThrowError(/X-Evil|a b|\n/);
  });

  it.each([
    ["an asterisk", "/a*/"],
    ["a colon", "/a:b/"],
    ["a space", "/a b/"],
    ["a newline", "/a\n  X-Evil: 1\n/b/"],
    ["a hash", "/a#b/"],
    ["a question mark", "/a?b/"],
    ["a parent segment", "/a/../b/"],
    ["an empty segment", "/a//b/"],
  ])("refuses a base with %s", (_label, base) => {
    expect(() => assetsPrefixFor(base, "assets")).toThrowError(/Refusing to build: --base \/ `base` must be a path of plain names/);
    expect(() => assetsPrefixFor(base, "assets")).not.toThrowError(/X-Evil/);
  });

  it("accepts the percent-encoding Vite gives a base with a space, and plain nested names", () => {
    expect(assetsPrefixFor("/a%20b/", "assets")).toBe("/a%20b/assets/");
    expect(assetsPrefixFor("/my-app_v1.2/sub~dir/", "static/js")).toBe("/my-app_v1.2/sub~dir/static/js/");
  });

  it("still treats a relative base as the root", () => {
    for (const base of ["", "./", ".", "../", "assets/"]) expect(assetsPrefixFor(base, "assets")).toBe("/assets/");
  });
});

// ── putting it together ────────────────────────────────────────────────────

const ENV = { VITE_API_BASE_URL: API, VITE_SUPABASE_URL: SUPABASE };
const PAGE = `<!doctype html><html><head><style>body{margin:0}</style>
<script type="module" crossorigin src="/assets/index-x.js"></script></head><body></body></html>`;

describe("buildHeadersFile", () => {
  it("is exactly the pinned file for a fixed environment and page", async () => {
    expect(await buildHeadersFile({ env: ENV, htmlDocuments: [PAGE], assetsPrefix: "/assets/" })).toBe(GOLDEN_FILE);
  });

  it("hashes the source page's inline style into style-src, whatever that style says", async () => {
    // Derived rather than pinned: editing the rule in index.html must keep working. cspHash itself is
    // pinned by independent vectors above, and the real build is checked in securityHeadersBuild.test.ts.
    const [style] = inlineBlocks(indexHtml).styles;
    const file = await buildHeadersFile({ env: ENV, htmlDocuments: [indexHtml], assetsPrefix: "/assets/" });
    expect(file).toContain(`style-src 'self' ${await cspHash(style)};`);
  });

  it("hashes inline scripts into script-src, so the policy never needs 'unsafe-inline'", async () => {
    const file = await buildHeadersFile({
      env: ENV,
      htmlDocuments: ["<style>body{margin:0}</style><script>window.__x=1</script>"],
      assetsPrefix: "/assets/",
    });
    expect(file).toContain(`script-src 'self' ${HASH_WINDOW_X};`);
    expect(file).toContain(`style-src 'self' ${HASH_BODY_MARGIN};`);
    expect(file).not.toContain("unsafe-inline");
  });

  it("collects the blocks of every document, once each, in a stable order", async () => {
    const a = "<style>b</style><style>a</style>";
    const b = "<style>a</style><style>c</style>";
    const forward = await buildHeadersFile({ env: ENV, htmlDocuments: [a, b], assetsPrefix: "/assets/" });
    const backward = await buildHeadersFile({ env: ENV, htmlDocuments: [b, a], assetsPrefix: "/assets/" });
    expect(forward).toBe(backward);
    const hashes = [...forward.matchAll(/'sha256-[A-Za-z0-9+/=]+'/g)].map((m) => m[0]);
    expect(hashes).toHaveLength(3);
    expect(hashes).toEqual([...hashes].sort());
  });

  it("adds no hash for a page with no inline blocks", async () => {
    const file = await buildHeadersFile({ env: ENV, htmlDocuments: ["<p>x</p>"], assetsPrefix: "/assets/" });
    expect(file).toContain("script-src 'self'; style-src 'self'; img-src");
    expect(file).not.toContain("sha256-");
  });

  it("is the same twice", async () => {
    const input = { env: ENV, htmlDocuments: [PAGE], assetsPrefix: "/assets/" };
    expect(await buildHeadersFile(input)).toBe(await buildHeadersFile(input));
  });

  it.each([
    ["an inline style attribute", `<body style="background:#070607"><style>a{}</style>`, "style"],
    ["an event-handler attribute", `<body onload="f()">`, "onload"],
  ])("refuses a page with %s, which the policy would block only in the browser", async (_label, html, name) => {
    const attempt = buildHeadersFile({ env: ENV, htmlDocuments: [PAGE, html], assetsPrefix: "/assets/" });
    await expect(attempt).rejects.toThrowError(
      new RegExp(`Refusing to build: the built HTML has inline attributes \\(${name}\\) that the Content-Security-Policy would block`),
    );
    await expect(attempt).rejects.toThrowError(/web\/README\.md/);
    await expect(attempt).rejects.not.toThrowError(/background|f\(\)/);
  });

  it("names each offending attribute once, sorted", async () => {
    const html = `<a onclick="f()" style="x"><b onclick="g()" style="y"></b></a>`;
    await expect(buildHeadersFile({ env: ENV, htmlDocuments: [html], assetsPrefix: "/assets/" })).rejects.toThrowError(
      /inline attributes \(onclick, style\)/,
    );
  });

  it("refuses a bad origin before it writes anything", async () => {
    await expect(
      buildHeadersFile({ env: { ...ENV, VITE_SUPABASE_URL: "http://x.example" }, htmlDocuments: [PAGE], assetsPrefix: "/assets/" }),
    ).rejects.toThrowError(/VITE_SUPABASE_URL is not an https/);
  });
});
