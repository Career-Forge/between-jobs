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
});
