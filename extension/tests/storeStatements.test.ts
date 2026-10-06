import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { CONSENT_VERSION } from "@/lib/consent";
import { scanSource } from "./helpers/neverSubmit";

// The store-facing text (the privacy policy, the listing pack, the permission justifications)
// and the in-product disclosure make claims about what the extension sends and does. They were
// pinned here deliberately: when the code changes a claim, one of these fails until the text
// is brought back in line. Read from the files as they are, whitespace-normalised.

const read = (relative: string) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8")
    .replace(/^\s*>[ \t]?/gmu, "") // block-quote markers
    .replace(/\s+/gu, " ");

const PRIVACY = read("../store/PRIVACY.md");
const LISTING = read("../store/LISTING.md");
const PERMISSIONS = read("../store/PERMISSIONS.md");
const README = read("../README.md");
const APP = read("../entrypoints/sidepanel/App.tsx");
const readRaw = (relative: string) => readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");
const GREENHOUSE_SOURCE = readRaw("../lib/greenhouse.ts");
const LISTING_RAW = readRaw("../store/LISTING.md");
const PERMISSIONS_RAW = readRaw("../store/PERMISSIONS.md");

describe("what is said about analytics and usage data", () => {
  it("no document claims the extension has no analytics at all", () => {
    for (const [name, text] of [["PRIVACY", PRIVACY], ["LISTING", LISTING], ["App.tsx", APP]] as const) {
      expect(text, name).not.toMatch(/\bhas no analytics\b/iu);
      expect(text, name).not.toMatch(/no analytics, no advertising, no crash reporting/iu);
      expect(text, name).not.toMatch(/No analytics, telemetry, usage statistics/iu);
    }
  });

  it("each says what it has: no THIRD-PARTY analytics, and the one count-only report to the service itself", () => {
    expect(PRIVACY).toContain("no third-party analytics");
    expect(LISTING).toContain("no third-party analytics");
    expect(APP).toContain("no third-party analytics");
    expect(PRIVACY).toContain("request 12");
    expect(LISTING).toContain("counts only");
    expect(APP).toContain("counts only");
  });

  it("the request table names the fill report and exactly what it carries", () => {
    const row = PRIVACY.split("| 12 |")[1]?.split("| 13 |")[0] ?? "";
    expect(row).toContain("Report a fill");
    for (const part of ["application's id", "which site", "how many fields it tried", "how many it filled", "`ok`, `partial` or `failed`"]) {
      expect(row, part).toContain(part);
    }
    expect(row).toContain("No field names, no values, no page address and no page text");
  });

  it("...and the saved-answer count (request 13) carries only the answer's id", () => {
    const row = PRIVACY.split("| 13 |")[1]?.split("| --- |")[0] ?? "";
    expect(row).toContain("Count a saved answer's use");
    expect(row).toContain("The saved answer's id");
  });

  it("the listing's Privacy practices table ticks User activity for it, as analytics", () => {
    const row = LISTING.split("| User activity |")[1]?.split("| Website content |")[0] ?? "";
    expect(row).toContain("**Yes**");
    expect(row).toContain("REPORT_FILL_OUTCOME");
    expect(row).toContain("Analytics");
  });
});

describe("what is said about consent", () => {
  it("the listing names the version in force, so raising it makes this fail until the text is re-read", () => {
    expect(LISTING).toContain(`\`CONSENT_VERSION\` is ${CONSENT_VERSION}`);
    expect(PRIVACY).toContain("`CONSENT_VERSION` was raised to 2");
    expect(CONSENT_VERSION).toBe(2);
  });

  it("the policy says nothing is read or sent before agreeing, and that all three contexts check the flag", () => {
    expect(PRIVACY).toContain("reads nothing from any page and sends nothing anywhere");
    expect(PRIVACY).toContain("the background worker and the part that runs alongside web pages read it");
    expect(PERMISSIONS).toContain("the extension reads nothing from a page and sends nothing until that is valid");
    expect(README).toContain("nothing is sent, until the person has agreed");
  });
});

describe("what is said about clicking and choosing", () => {
  it("every document that says the extension never clicks also says the one exception", () => {
    expect(PRIVACY).toContain("the one exception is picking an entry in Greenhouse's country list");
    expect(PERMISSIONS).toContain("apart from picking that one country entry");
    expect(LISTING).toContain("The one list it fills is Greenhouse's country field");
    expect(README).toContain("The one list it fills is Greenhouse's country field");
  });

  it("the source really has exactly that one click and one focus, in the function the policy's notes name", () => {
    const violations = scanSource("greenhouse.ts", GREENHOUSE_SOURCE);
    expect(violations.filter((v) => v.rule === "click")).toHaveLength(1);
    expect(violations.filter((v) => v.rule === "focus")).toHaveLength(1);
    expect(PRIVACY).toContain("`activateListboxOption` in `lib/greenhouse.ts`");
  });
});

describe("what is said about the AI provider and work authorization", () => {
  it("no longer says the profile summary holds the work-authorization text", () => {
    expect(PRIVACY).not.toContain("any work-authorization text in your profile,");
    expect(PRIVACY).toContain("it does not include the work-authorization text in your profile");
    expect(PRIVACY).toContain("only when the question itself is about work authorization");
  });
});

describe("the in-product disclosure lists everything the extension sends under it", () => {
  // The screen is what a person agrees to, so it names what the code sends: not only the counts.
  const PHRASES = [
    "which of your applications",
    "one word for how it went -- never what is in any field",
    "when you fill a saved answer exactly as it was saved, tell the service which saved answer was used",
    "the country the job names (when it names one)",
  ];

  it("App.tsx's screen has each of them (and the question's kind, written as JSX)", () => {
    for (const phrase of PHRASES) expect(APP, phrase).toContain(phrase);
    expect(APP).toContain("the question&apos;s kind (when it is one of a few common ones)");
  });

  it("the listing's copy of the screen says the same", () => {
    for (const phrase of PHRASES) expect(LISTING, phrase).toContain(phrase);
    expect(LISTING).toContain("the question's kind (when it is one of a few common ones)");
  });

  it("the consent version's note says what version 2 covers beyond the counts", () => {
    expect(LISTING).toContain("Version 2 also covers");
    expect(PRIVACY).toContain("Version 2 also covers");
  });
});

describe("what is said about the sign-out order, and what the service returns", () => {
  it("request 3 names the Between Jobs API first and the sign-in service second, as the code does", () => {
    const row = PRIVACY.split("| 3 |")[1]?.split("| 4 |")[0] ?? "";
    expect(row).toContain("Between Jobs API, then the sign-in service");
    expect(row.indexOf("access token")).toBeLessThan(row.indexOf("session token"));
    expect(PRIVACY).toContain("Request 3's first call (to the Between Jobs API)");
  });

  it("'what you get back' describes the trimmed payload: no fit assessment, and the country is used", () => {
    const gets = PRIVACY.split("**What you get back.**")[1]?.split("**The AI provider.**")[0] ?? "";
    expect(gets).not.toMatch(/fit assessment/iu);
    expect(gets).toContain("the references the extension needs to download");
    expect(gets).toContain("The extension uses the profile details, the two files and the job's country");
    expect(PRIVACY).not.toContain("returns the whole stored `prepare_result`");
  });

  it("a refresh of an expired session is described as following agreement", () => {
    const row = PRIVACY.split("| 2 |")[1]?.split("| 3 |")[0] ?? "";
    expect(row).toContain("have agreed to the disclosure screen");
  });
});

describe("what is said about recognizing self-identification questions, and about in-flight actions", () => {
  it("the README and the listing say the self-ID filter works by wording, as the policy does", () => {
    expect(README).toContain("recognized by their wording");
    expect(LISTING).toContain("by their wording and skips them");
    expect(PRIVACY).toContain("It recognizes them by their wording");
  });

  it("the policy says a fill that has started writing finishes, and that the flag is read before each request", () => {
    expect(PRIVACY).toContain("a fill that has started writing finishes the page it is on and then stops");
    expect(PRIVACY).toContain("re-read it before every request and before a fill starts writing");
  });

  it("the policy's note on the click says it lands only on a plain list entry", () => {
    expect(PRIVACY).toContain("only a plain entry of that list");
    expect(README).toContain("lands only on a plain entry of a listbox");
  });
});

describe("the store pack's own numbers match the text they describe", () => {
  const fenced = (LISTING_RAW.match(/```text\n([\s\S]*?)\n```/u) ?? [])[1] ?? "";
  const count = (word: string) => (fenced.match(new RegExp(word, "gu")) ?? []).length;

  it("LISTING.md states the detailed description's length", () => {
    expect(fenced.length).toBeGreaterThan(1000);
    expect(LISTING).toContain(`Section 2 below (${fenced.length.toLocaleString("en-US")} characters)`);
  });

  it("...and how often it names each service and the product, all under the keyword-spam threshold", () => {
    const words = { Lever: 2, Ashby: 2, Greenhouse: 3, "Between Jobs": 5 } as const;
    for (const [word, times] of Object.entries(words)) expect(count(word), word).toBe(times);
    for (const word of Object.keys(words)) expect(count(word), word).toBeLessThanOrEqual(5);
    expect(LISTING).toContain(
      "names Lever and Ashby twice each, Greenhouse three times and the product name five times",
    );
  });

  it("PERMISSIONS.md states the length of its two longest justification paragraphs", () => {
    const quoted = PERMISSIONS_RAW.match(/(?:^> .*\n)+/gmu) ?? [];
    const lengths = quoted
      .map((block) => block.replace(/^> ?/gmu, "").replace(/\n/gu, " ").trim())
      .filter((text) => !text.startsWith("**DRAFT"))
      .map((text) => text.length)
      .sort((a, b) => b - a);
    expect(lengths.length).toBeGreaterThan(5);
    expect(PERMISSIONS).toContain(`the storage one (${lengths[0]} characters) and the combined host-permission one (${lengths[1]})`);
    expect(lengths[2]!).toBeLessThan(500);
  });
});
