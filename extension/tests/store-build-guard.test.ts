import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { assertStoreBuild, findStoreBuildProblems } from "@/scripts/store-build-guard";

// The store zip must never be built against a developer's leftover
// .env.local (Vite gives it higher precedence than .env.production): that
// ships an extension wired to http://localhost:8012 and the dev Supabase
// project, or -- with no env at all -- one that calls fetch("undefined/...").

const GOOD_ENV = {
  WXT_API_BASE_URL: "https://api.between-jobs.example",
  WXT_SUPABASE_URL: "https://abcdefgh.supabase.co",
  WXT_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_example",
};

describe("findStoreBuildProblems", () => {
  it("accepts a real https, non-local configuration with a real version", () => {
    expect(findStoreBuildProblems(GOOD_ENV, { version: "0.1.0" })).toEqual([]);
  });

  it("rejects the dev configuration a stray .env.local produces", () => {
    const problems = findStoreBuildProblems(
      { ...GOOD_ENV, WXT_API_BASE_URL: "http://localhost:8012" },
      { version: "0.1.0" },
    );
    expect(problems.join("\n")).toMatch(/WXT_API_BASE_URL/);
  });

  it.each([
    ["http://localhost:8012", /not https/],
    ["https://localhost:8012", /local address/],
    ["https://127.0.0.1:8012", /local address/],
    ["https://0.0.0.0", /local address/],
    ["https://[::1]:8012", /local address/],
    ["https://dev.localhost", /local address/],
    ["https://mybox.local", /local address/],
    ["http://api.example.com", /not https/],
    ["undefined", /not a valid URL/],
    ["api.example.com", /not a valid URL/],
    ["", /not set/],
  ])("rejects WXT_API_BASE_URL=%j", (value, expected) => {
    const problems = findStoreBuildProblems({ ...GOOD_ENV, WXT_API_BASE_URL: value }, { version: "0.1.0" });
    expect(problems.join("\n")).toMatch(expected);
  });

  it("rejects an unset API base URL (the bundle would call fetch('undefined/...'))", () => {
    const { WXT_API_BASE_URL: _omitted, ...rest } = GOOD_ENV;
    expect(findStoreBuildProblems(rest, { version: "0.1.0" }).join("\n")).toMatch(/WXT_API_BASE_URL is not set/);
  });

  it("rejects a local or cleartext Supabase URL and a missing publishable key", () => {
    const problems = findStoreBuildProblems(
      { WXT_API_BASE_URL: GOOD_ENV.WXT_API_BASE_URL, WXT_SUPABASE_URL: "http://localhost:54321" },
      { version: "0.1.0" },
    ).join("\n");
    expect(problems).toMatch(/WXT_SUPABASE_URL/);
    expect(problems).toMatch(/WXT_SUPABASE_PUBLISHABLE_KEY is not set/);
  });

  it("reports every problem at once, so one run tells you everything to fix", () => {
    expect(findStoreBuildProblems({}, { version: "0.0.0" })).toHaveLength(4);
  });

  it.each(["0.0.0", "0", "0.0.0.0", "", "1.x", "1.2.3.4.5", "v1.0.0", "-1.0.0"])(
    "rejects manifest version %j (Chrome: 1-4 integers, not all zero)",
    (version) => {
      expect(findStoreBuildProblems(GOOD_ENV, { version }).join("\n")).toMatch(/manifest version/);
    },
  );

  it.each(["0.1.0", "1", "1.0", "0.0.1", "10.20.30.40"])("accepts manifest version %j", (version) => {
    expect(findStoreBuildProblems(GOOD_ENV, { version })).toEqual([]);
  });

  it("rejects a manifest with no version at all", () => {
    expect(findStoreBuildProblems(GOOD_ENV, {}).join("\n")).toMatch(/manifest version/);
  });
});

describe("assertStoreBuild", () => {
  function manifestWith(version: string): string {
    const dir = mkdtempSync(path.join(tmpdir(), "bj-manifest-"));
    const file = path.join(dir, "manifest.json");
    writeFileSync(file, JSON.stringify({ manifest_version: 3, version }));
    return file;
  }

  it("throws, listing each problem and how to fix it", () => {
    let message = "";
    try {
      assertStoreBuild({ ...GOOD_ENV, WXT_API_BASE_URL: "http://localhost:8012" }, manifestWith("0.0.0"));
    } catch (e) {
      message = e instanceof Error ? e.message : String(e);
    }
    expect(message).toMatch(/Refusing to create a store zip/);
    expect(message).toMatch(/WXT_API_BASE_URL/);
    expect(message).toMatch(/manifest version/);
    expect(message).toMatch(/shell/);
  });

  it("passes silently for a good build", () => {
    expect(() => assertStoreBuild(GOOD_ENV, manifestWith("0.1.0"))).not.toThrow();
  });
});
