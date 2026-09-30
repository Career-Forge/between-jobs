import { describe, expect, it } from "vitest";
import { buildApiUrl, DEV_API_BASE, resolveApiBase } from "./apiUrl";

describe("resolveApiBase", () => {
  it("falls back to the dev proxy when nothing is configured", () => {
    expect(resolveApiBase(undefined)).toBe(DEV_API_BASE);
    expect(resolveApiBase("")).toBe("/api");
    expect(resolveApiBase("   ")).toBe("/api");
  });

  it("uses a configured origin as given", () => {
    expect(resolveApiBase("https://api.between-jobs.tech")).toBe("https://api.between-jobs.tech");
  });

  it("drops trailing slashes and surrounding whitespace", () => {
    expect(resolveApiBase(" https://api.between-jobs.tech/ ")).toBe("https://api.between-jobs.tech");
    expect(resolveApiBase("https://api.between-jobs.tech///")).toBe("https://api.between-jobs.tech");
  });

  it("keeps a path on the base", () => {
    expect(resolveApiBase("https://example.com/v1/")).toBe("https://example.com/v1");
  });
});

describe("buildApiUrl", () => {
  it("joins the dev base and a route", () => {
    expect(buildApiUrl("/api", "/profile/active")).toBe("/api/profile/active");
  });

  it("joins a configured origin and a route without doubling slashes", () => {
    const base = resolveApiBase("https://api.between-jobs.tech/");
    expect(buildApiUrl(base, "/applications/1/resume.pdf")).toBe(
      "https://api.between-jobs.tech/applications/1/resume.pdf",
    );
  });

  it("keeps the query string of a route", () => {
    expect(buildApiUrl("/api", "/today?limit=5&cursor=a%20b")).toBe("/api/today?limit=5&cursor=a%20b");
  });

  it("adds a missing leading slash", () => {
    expect(buildApiUrl("/api", "health")).toBe("/api/health");
  });
});
