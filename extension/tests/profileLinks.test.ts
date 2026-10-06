import { describe, expect, it } from "vitest";
import { chooseLinkForBox, linkKind, linkWantFromLabel, type LinkKind, type LinkWant } from "@/lib/profileLinks";

describe("linkKind classifies a link by what it is, from its host", () => {
  const rows: Array<[string, LinkKind | null]> = [
    ["https://github.com/jane", "github"],
    ["https://www.github.com/jane", "github"],
    ["HTTPS://GitHub.com/Jane", "github"],
    ["github.com/jane", "github"], // a scheme-less link still has a host
    ["https://jane.github.io", "site"], // GitHub Pages is a website, not a GitHub profile
    ["https://gist.github.com/jane/1", "site"],
    ["https://github.com.evil.example/jane", "site"],
    ["https://linkedin.com/in/jane", "linkedin"],
    ["https://uk.linkedin.com/in/jane", "linkedin"],
    ["https://jane.dev", "site"],
    ["https://www.jane.example.com/portfolio", "site"],
    ["jane", null], // a bare handle has no host
    ["", null],
    ["   ", null],
    ["https://", null],
  ];
  it.each(rows)("%j -> %j", (url, expected) => {
    expect(linkKind(url)).toBe(expected);
  });
});

describe("linkWantFromLabel reads what a box asks for", () => {
  const rows: Array<[string | null, LinkWant]> = [
    ["GitHub URL", "github"],
    ["Github", "github"],
    ["Link to your Git Hub profile", "github"],
    ["Portfolio URL", "site"],
    ["Personal website", "site"],
    ["Other website", "site"],
    ["Blog", "site"],
    ["Other (portfolio, GitHub etc)", "either"], // names both
    ["GitHub or portfolio", "either"],
    ["Link", "either"], // names neither
    ["Other", "either"],
    ["", "either"],
    [null, "either"], // no readable label
  ];
  it.each(rows)("%j -> %s", (label, expected) => {
    expect(linkWantFromLabel(label)).toBe(expected);
  });
});

describe("chooseLinkForBox", () => {
  const GITHUB = "https://github.com/jane";
  const SITE = "https://jane.dev";

  it("a box that asks for GitHub gets the GitHub-host link -- never the portfolio, even though the map prefers it", () => {
    expect(chooseLinkForBox([SITE, GITHUB], "github")).toEqual({ value: GITHUB });
  });

  it("...even when the person typed the GitHub address into the portfolio field (the host decides, not the field)", () => {
    expect(chooseLinkForBox([GITHUB, SITE], "github")).toEqual({ value: GITHUB });
    expect(chooseLinkForBox(["https://jane.dev", "https://github.com/jane"], "github")).toEqual({ value: GITHUB });
  });

  it("a box that asks for GitHub, with no GitHub link in the profile, is left empty and says why", () => {
    expect(chooseLinkForBox([SITE, ""], "github")).toEqual({
      value: null,
      skipped: "the form asks for a GitHub link and your profile has none",
    });
  });

  it("a GitHub entry that is not a github.com address is not reported as 'has none'", () => {
    const UNUSABLE = "the form asks for a GitHub profile link, and no link in your profile is a github.com address";
    for (const github of ["alice", "@alice", "https://gist.github.com/alice", "https://alice.github.io"]) {
      expect(chooseLinkForBox([SITE, github], "github"), github).toEqual({ value: null, skipped: UNUSABLE });
      expect(chooseLinkForBox(["", github], "github"), github).toEqual({ value: null, skipped: UNUSABLE });
    }
  });

  it("a portfolio-only profile keeps the plain 'has none' wording, and a LinkedIn link is not a GitHub link", () => {
    const NONE = "the form asks for a GitHub link and your profile has none";
    expect(chooseLinkForBox([SITE, ""], "github")).toEqual({ value: null, skipped: NONE });
    expect(chooseLinkForBox(["https://linkedin.com/in/jane", ""], "github")).toEqual({ value: null, skipped: NONE });
  });

  it("a real github.com link next to an unusable one is still used", () => {
    expect(chooseLinkForBox(["alice", GITHUB], "github")).toEqual({ value: GITHUB });
  });

  it("a box that asks for GitHub, with nothing at all in the profile, is left empty without a fuss", () => {
    expect(chooseLinkForBox(["", ""], "github")).toEqual({ value: null });
  });

  it("a box that asks for a website prefers a real website over a GitHub profile", () => {
    expect(chooseLinkForBox([GITHUB, SITE], "site")).toEqual({ value: SITE });
    expect(chooseLinkForBox([SITE, GITHUB], "site")).toEqual({ value: SITE });
  });

  it("...and takes the GitHub link when that is all there is: it is a website too", () => {
    expect(chooseLinkForBox(["", GITHUB], "site")).toEqual({ value: GITHUB });
  });

  it("a box whose label is ambiguous keeps the map's own order", () => {
    expect(chooseLinkForBox([SITE, GITHUB], "either")).toEqual({ value: SITE });
    expect(chooseLinkForBox([GITHUB, SITE], "either")).toEqual({ value: GITHUB });
    expect(chooseLinkForBox(["", GITHUB], "either")).toEqual({ value: GITHUB });
    expect(chooseLinkForBox(["", ""], "either")).toEqual({ value: null });
  });
});
