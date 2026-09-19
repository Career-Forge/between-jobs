import { describe, expect, it } from "vitest";
import { canonicalLookupUrl, detectAtsType, isAtsType } from "@/lib/atsHosts";

describe("detectAtsType", () => {
  it.each([
    ["jobs.lever.co", "lever"],
    ["job-boards.greenhouse.io", "greenhouse"],
    ["boards.greenhouse.io", "greenhouse"],
    ["jobs.ashbyhq.com", "ashby"],
    ["greenhouse.io", "greenhouse"],
  ])("%s -> %s", (host, expected) => {
    expect(detectAtsType(host)).toBe(expected);
  });

  it.each(["example.com", "lever.co.evil.example", "evillever.co", "notgreenhouse.io.example.com", ""])(
    "%s -> null",
    (host) => {
      expect(detectAtsType(host)).toBeNull();
    },
  );
});

describe("isAtsType", () => {
  it("accepts exactly the three supported ATSs", () => {
    expect(["lever", "greenhouse", "ashby"].every(isAtsType)).toBe(true);
    for (const bad of ["workday", "", "LEVER", null, undefined, 3, {}, "../lever"]) {
      expect(isAtsType(bad)).toBe(false);
    }
  });
});

// AB-06 -- the extension used to send `location.href` verbatim: the query
// string and fragment (utm_*, lever-source, gh_src, and on some ATS links
// per-candidate tokens) ended up in server and proxy access logs, and the
// form route (`/apply`, `/application`) never matched the posting URL the
// job was tracked under, so Lever and Ashby pages read as "untracked".
describe("canonicalLookupUrl", () => {
  it("strips the Lever /apply form route so the tab matches the tracked posting URL", () => {
    expect(canonicalLookupUrl("https://jobs.lever.co/acme/aaaa-1111/apply", "lever")).toBe(
      "https://jobs.lever.co/acme/aaaa-1111",
    );
  });

  it("strips the Ashby /application form route", () => {
    expect(canonicalLookupUrl("https://jobs.ashbyhq.com/acme/bbbb-2222/application", "ashby")).toBe(
      "https://jobs.ashbyhq.com/acme/bbbb-2222",
    );
  });

  it("drops the query string and fragment -- tracking parameters and tokens never leave the browser", () => {
    const url = "https://jobs.lever.co/acme/aaaa-1111/apply?lever-source=LinkedIn&token=SECRET&email=a%40b.com#section";
    const result = canonicalLookupUrl(url, "lever");
    expect(result).toBe("https://jobs.lever.co/acme/aaaa-1111");
    expect(result).not.toMatch(/SECRET|token|email|lever-source|#/);
  });

  it("drops a query string on Greenhouse, where the form lives on the posting URL itself", () => {
    expect(canonicalLookupUrl("https://job-boards.greenhouse.io/acme/jobs/123?gh_src=abc", "greenhouse")).toBe(
      "https://job-boards.greenhouse.io/acme/jobs/123",
    );
  });

  it("drops trailing slashes, with or without the form route", () => {
    expect(canonicalLookupUrl("https://jobs.lever.co/acme/aaaa-1111/", "lever")).toBe("https://jobs.lever.co/acme/aaaa-1111");
    expect(canonicalLookupUrl("https://jobs.lever.co/acme/aaaa-1111/apply/", "lever")).toBe("https://jobs.lever.co/acme/aaaa-1111");
  });

  it("only strips a form route that belongs to that ATS", () => {
    expect(canonicalLookupUrl("https://job-boards.greenhouse.io/acme/jobs/apply", "greenhouse")).toBe(
      "https://job-boards.greenhouse.io/acme/jobs/apply",
    );
    expect(canonicalLookupUrl("https://jobs.lever.co/acme/aaaa-1111/application", "lever")).toBe(
      "https://jobs.lever.co/acme/aaaa-1111/application",
    );
  });

  it("keeps two different posting ids distinct", () => {
    const a = canonicalLookupUrl("https://jobs.lever.co/acme/aaaa-1111/apply", "lever");
    const b = canonicalLookupUrl("https://jobs.lever.co/acme/bbbb-2222/apply", "lever");
    expect(a).not.toBe(b);
  });

  it("returns null for a URL that isn't on the expected ATS's own host", () => {
    expect(canonicalLookupUrl("https://jobs.lever.co/acme/aaaa-1111", "greenhouse")).toBeNull();
    expect(canonicalLookupUrl("https://evil.example/acme/aaaa-1111", "lever")).toBeNull();
    expect(canonicalLookupUrl("https://lever.co.evil.example/acme", "lever")).toBeNull();
  });

  it("returns null for non-https and unparseable input", () => {
    expect(canonicalLookupUrl("http://jobs.lever.co/acme/aaaa-1111", "lever")).toBeNull();
    expect(canonicalLookupUrl("javascript:alert(1)", "lever")).toBeNull();
    expect(canonicalLookupUrl("not a url", "lever")).toBeNull();
    expect(canonicalLookupUrl("", "lever")).toBeNull();
  });
});
