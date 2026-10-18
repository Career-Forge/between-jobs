import { describe, expect, it } from "vitest";
import { HOSTED_SITE_ORIGIN, resolveSiteOrigin } from "./siteOrigin";

describe("resolveSiteOrigin", () => {
  it("is the hosted site when nothing is set", () => {
    expect(resolveSiteOrigin(undefined)).toBe(HOSTED_SITE_ORIGIN);
    expect(resolveSiteOrigin("")).toBe(HOSTED_SITE_ORIGIN);
    expect(resolveSiteOrigin("   ")).toBe(HOSTED_SITE_ORIGIN);
  });

  it("takes a self-hoster's own origin, with or without a trailing slash, and keeps a port", () => {
    expect(resolveSiteOrigin("https://jobs.example.org")).toBe("https://jobs.example.org");
    expect(resolveSiteOrigin(" https://jobs.example.org/ ")).toBe("https://jobs.example.org");
    expect(resolveSiteOrigin("https://staging.example.org:8443")).toBe("https://staging.example.org:8443");
  });

  it.each(["jobs.example.org", "http://jobs.example.org", "https:jobs.example.org", "https://u:p@jobs.example.org", "https://jobs.example.org/app", "https://jobs.example.org/?x=1", "https://jobs.example.org/#top"])(
    "refuses %s, and never quotes it",
    (value) => {
      expect(() => resolveSiteOrigin(value)).toThrow(/VITE_SITE_URL/);
      try {
        resolveSiteOrigin(value);
      } catch (error) {
        expect(String(error)).not.toContain("example.org");
      }
    },
  );
});
