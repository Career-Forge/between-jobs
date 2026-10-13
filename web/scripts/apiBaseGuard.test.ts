import { describe, expect, it } from "vitest";
import { apiBaseProblem, assertApiBase } from "./apiBaseGuard";

describe("apiBaseProblem", () => {
  it("accepts an https origin", () => {
    expect(apiBaseProblem("https://api.between-jobs.tech")).toBeNull();
    expect(apiBaseProblem("https://api.between-jobs.tech/")).toBeNull();
    expect(apiBaseProblem("  https://api.example.com  ")).toBeNull();
  });

  it.each([undefined, "", "   "])("rejects an unset value (%j)", (raw) => {
    expect(apiBaseProblem(raw)).toMatch(/not set/);
  });

  it("rejects what is not a URL", () => {
    expect(apiBaseProblem("/api")).toMatch(/not a valid absolute URL/);
    expect(apiBaseProblem("api.between-jobs.tech")).toMatch(/not a valid absolute URL/);
  });

  it("accepts a path on the origin, because route paths are appended to it", () => {
    expect(apiBaseProblem("https://example.com/api/v1")).toBeNull();
    expect(apiBaseProblem("https://example.com/api/v1/")).toBeNull();
  });

  it.each([
    ["a query string", "https://api.example.com?x=1"],
    ["a query string after a path", "https://api.example.com/v1?x=1"],
    ["an empty query", "https://api.example.com/v1?"],
    ["a fragment", "https://api.example.com#top"],
    ["an empty fragment", "https://api.example.com/v1#"],
  ])("rejects %s, since paths are appended to the string and would land inside it", (_l, raw) => {
    expect(apiBaseProblem(raw)).toMatch(/query string or fragment/);
  });

  it.each([
    ["userinfo", "https://user:pass@api.example.com"],
    ["a bare username", "https://user@api.example.com"],
    ["a password with no username", "https://:pass@api.example.com"],
  ])("rejects %s, which would put credentials in every request", (_l, raw) => {
    expect(apiBaseProblem(raw)).toMatch(/credentials/);
  });

  it.each([
    "https:api.example.com",
    "https:/api.example.com",
    "https:///api.example.com",
    "https://",
  ])("rejects a spelling the URL parser forgives but a string base cannot use (%s)", (raw) => {
    expect(apiBaseProblem(raw)).not.toBeNull();
  });

  it("rejects a cleartext origin, since the session token goes to it", () => {
    expect(apiBaseProblem("http://api.between-jobs.tech")).toMatch(/not https/);
  });

  it.each([
    "https://localhost:8012",
    "https://127.0.0.1",
    "https://0.0.0.0:8000",
    "https://[::1]:8000",
    "https://api.localhost",
    "https://printer.local",
    "https://LOCALHOST",
  ])("rejects a local address (%s)", (raw) => {
    expect(apiBaseProblem(raw)).toMatch(/local address/);
  });
});

describe("assertApiBase", () => {
  it("does nothing for a usable value", () => {
    expect(() => assertApiBase("https://api.example.com")).not.toThrow();
  });

  it("names the variable, the problem and the fix", () => {
    expect(() => assertApiBase(undefined)).toThrowError(/VITE_API_BASE_URL is not set[\s\S]*npm run build/);
  });

  it("shows the whole build command, both addresses, so following the hint does not hit a second error", () => {
    expect(() => assertApiBase(undefined)).toThrowError(
      /VITE_API_BASE_URL=https:\/\/api\.example\.com VITE_SUPABASE_URL=https:\/\/<project>\.supabase\.co npm run build/,
    );
  });
});

// The build log is a CI log, which strangers can read on a public repository, and an API address
// pasted with a token in it is the kind of mistake this guard exists to catch.
describe("the guard never prints the value it rejects", () => {
  const secret = "hunter2-sentinel";
  it.each([
    ["userinfo", `https://user:${secret}@host-sentinel.example`],
    ["a password with no username", `https://:${secret}@host-sentinel.example`],
    ["cleartext http with a path", `http://host-sentinel.example/${secret}`],
    ["an out-of-range port", `https://host-sentinel.example:99999/${secret}`],
    ["a query string", `https://host-sentinel.example/?t=${secret}`],
    ["a fragment", `https://host-sentinel.example/#${secret}`],
    ["a local address", `https://localhost/${secret}`],
    ["something that is not a URL", `host-sentinel.example/${secret}`],
    ["a spelling the URL parser forgives", `https:host-sentinel.example/${secret}`],
  ])("%s", (_label, raw) => {
    const problem = apiBaseProblem(raw);
    expect(problem).not.toBeNull();
    expect(problem).not.toContain(secret);
    expect(problem).not.toContain("host-sentinel");
    const run = () => assertApiBase(raw);
    expect(run).toThrowError(/VITE_API_BASE_URL/);
    expect(run).not.toThrowError(new RegExp(`${secret}|host-sentinel`));
  });
});
