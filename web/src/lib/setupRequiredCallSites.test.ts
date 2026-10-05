import { describe, expect, it } from "vitest";

// A SETUP_REQUIRED reply is only as helpful as the places that show it: a component that
// goes back to `if (e.code === "SETUP_REQUIRED") setError(e.message)` shows the sentence and
// drops the link to the page that fixes it, and nothing else (the package has no DOM test
// setup, and the code still type-checks) would notice. So this reads the source of the app:
//
//   1. no source outside lib/setupRequired.ts decides "is this a setup error" by comparing
//      the code itself, except hiringSignals.ts's classifier, which keeps its own failure
//      model and takes its link from setupRequiredNotice;
//   2. every place that was converted still routes a failure through the setup helpers
//      (`failureOf`, `problemOf`, or `setupRequiredNotice` itself), at least as many times as
//      it did when it was wired up -- AND still draws the result as a notice, because a
//      component that keeps calling the helper and then shows only `.message` has the same
//      bug with the call count intact;
//   3. the converted components hand the helper's result to their state whole, and no
//      error branch of theirs still builds a message from the error by hand.
//
// vite's `?raw` import, not `node:fs`: @types/node is not installed here.
const globbed = import.meta.glob(
  ["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../**/*.test.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

// A file next to this one is keyed "./name.ts"; every key is spelled "../dir/name.ts".
const sources: Record<string, string> = Object.fromEntries(
  Object.entries(globbed).map(([key, source]) => [
    key.startsWith("./") ? `../lib/${key.slice(2)}` : key,
    source,
  ]),
);

// Where the raw code string is allowed to appear: the module that owns the decision, the
// list of codes, the client that carries the fields, and prose in comments.
const ALLOWED_TO_NAME_THE_CODE = new Set([
  "../lib/setupRequired.ts",
  "../lib/apiErrorCodes.ts",
  "../lib/api.ts",
  "../lib/hiringSignals.ts",
]);

interface Site {
  // Places that turn a failure into a notice or a message through the setup helpers. A new
  // one is fine; one fewer is a revert.
  calls: number;
  // Places that draw a notice: <SetupRequiredNotice notice=... /> or <ProblemView problem=... />.
  draws: number;
  // Code that must be there: the notice goes into state whole, and comes out to the screen whole.
  keeps?: readonly RegExp[];
  // Code that must not be: an error branch that spells its message out from the error itself.
  forbids?: readonly RegExp[];
}

const BY_HAND = /kind: "error", message: e instanceof Error/;
const NOTICE_FROM_STATE = /<SetupRequiredNotice notice=\{state\.notice\} \/>/;
const SETUP_BRANCH = /if \(state\.kind === "setup"\) \{\s*return <SetupRequiredNotice notice=\{state\.notice\} \/>;\s*\}/;

const SITES: Record<string, Site> = {
  "../components/CompanyIntelPanel.tsx": {
    calls: 1,
    draws: 1,
    keeps: [/setState\(\{ kind: "setup", notice: setup \}\)/, NOTICE_FROM_STATE],
  },
  "../components/ContactFinderPanel.tsx": {
    calls: 5, // generate, enrich, find LinkedIn, draft, push to Gmail
    draws: 5,
    keeps: [
      /setState\(\{ kind: "setup", notice: setup \}\)/,
      /setEnrichError\(setup\)/,
      /setLinkedInError\(setup\)/,
      /setDraftError\(setup\)/,
      /setPushError\(setup\)/,
      NOTICE_FROM_STATE,
      /<ProblemView problem=\{enrichError\} \/>/,
      /<ProblemView problem=\{linkedInError\} \/>/,
      /<ProblemView problem=\{draftError\} \/>/,
      /<ProblemView problem=\{pushError\} \/>/,
    ],
  },
  "../components/InterviewPracticePanel.tsx": {
    calls: 1,
    draws: 1,
    keeps: [/message: setup\.message,\s*setup,/, /<SetupRequiredNotice notice=\{state\.setup\} \/>/],
  },
  "../components/PositioningBriefPanel.tsx": {
    calls: 1,
    draws: 1,
    keeps: [/setState\(\{ kind: "setup", notice: setup \}\)/, NOTICE_FROM_STATE],
  },
  "../components/WarmPathEventsPanel.tsx": {
    calls: 1,
    draws: 1,
    keeps: [/setState\(\{ kind: "setup", notice: setup \}\)/, NOTICE_FROM_STATE],
  },
  "../components/GeneratePanel.tsx": {
    calls: 1,
    draws: 1,
    keeps: [
      /setGenerate\(failureOf\(e, /,
      /generate\.kind === "setup" && <SetupRequiredNotice notice=\{generate\.notice\} \/>/,
    ],
    forbids: [/setGenerate\(\{ kind: "error"/],
  },
  // The three panels that read the resume document: all open with the workspace, and the
  // first thing a new account hits there is "no profile yet" (and then "no model key").
  "../components/HeaderComposer.tsx": {
    calls: 2, // load, save
    draws: 1,
    keeps: [/setState\(failureOf\(e, "Failed to load"\)\)/, SETUP_BRANCH],
    forbids: [BY_HAND],
  },
  "../components/ShapeSettingsPanel.tsx": {
    calls: 2, // load, save
    draws: 1,
    keeps: [/setState\(failureOf\(e, "Failed to load"\)\)/, SETUP_BRANCH],
    forbids: [BY_HAND],
  },
  "../components/TailorPanel.tsx": {
    calls: 2, // load, save
    draws: 1,
    keeps: [/setState\(failureOf\(e, "Failed to load"\)\)/, SETUP_BRANCH],
    forbids: [BY_HAND],
  },
  "../pages/Discover.tsx": {
    calls: 1,
    draws: 1,
    keeps: [/setState\(failureOf\(e, "Failed to search"\)\)/, /state\.kind === "setup" && \(/, NOTICE_FROM_STATE],
    forbids: [/setState\(\{ kind: "error", message: friendlyApiMessage\(e, "Failed to search"\)/],
  },
  "../pages/Integrations.tsx": {
    calls: 1, // connect Gmail
    draws: 1,
    keeps: [/setError\(problemOf\(e, /, /<ProblemView problem=\{error\} \/>/],
  },
  "../pages/Applications.tsx": {
    calls: 1, // track a job from a URL
    draws: 1,
    keeps: [/setError\(problemOf\(e, /, /<ProblemView problem=\{error\} \/>/],
  },
  // Its own failure model (a classifier the panels read); the link comes from the notice.
  "../lib/hiringSignals.ts": { calls: 1, draws: 0 },
};

// Code, not comments: a `//` line is prose.
function codeOnly(source: string): string {
  return source
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

const CALL = /\b(setupRequiredNotice|failureOf|problemOf)\(/g;
const DRAW = /<(SetupRequiredNotice notice|ProblemView problem)=/g;

describe("every place a SETUP_REQUIRED error is shown", () => {
  it("finds the source of the app (so the checks below are not looking at nothing)", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(40);
    expect(sources["../lib/setupRequired.ts"]).toBeTypeOf("string");
  });

  it("does not compare the error code itself outside the few modules that own it", () => {
    const offenders = Object.entries(sources)
      .filter(([file]) => !ALLOWED_TO_NAME_THE_CODE.has(file))
      .filter(([, source]) => codeOnly(source).includes('"SETUP_REQUIRED"'))
      .map(([file]) => file);
    expect(offenders).toEqual([]);
  });

  it("does not show a setup error as bare text anywhere", () => {
    for (const [file, source] of Object.entries(sources)) {
      // The old shape of every branch: the code check followed straight by a text-only state.
      expect(codeOnly(source), file).not.toMatch(/e\.code === "SETUP_REQUIRED"/);
      expect(codeOnly(source), file).not.toMatch(/\bAdd (a|an) [A-Za-z ]+ key in Integrations\./);
    }
  });

  it("only draws a notice through the two components that draw its link", () => {
    // A notice drawn by hand (`{state.notice.message}`) is the sentence without the link.
    for (const [file, source] of Object.entries(sources)) {
      if (file === "../components/SetupRequiredNotice.tsx") continue;
      expect(codeOnly(source), file).not.toMatch(/\bnotice\.message\b/);
    }
  });

  describe.each(Object.entries(SITES))("%s", (file, site) => {
    const source = sources[file];

    it("is found", () => {
      expect(source).toBeTypeOf("string");
    });

    it(`routes a failure through the setup helpers at least ${site.calls} time(s)`, () => {
      expect((codeOnly(source).match(CALL) ?? []).length).toBeGreaterThanOrEqual(site.calls);
    });

    it(`draws the notice (link and all) at least ${site.draws} time(s)`, () => {
      expect((codeOnly(source).match(DRAW) ?? []).length).toBeGreaterThanOrEqual(site.draws);
    });

    for (const pattern of site.keeps ?? []) {
      it(`keeps ${pattern}`, () => {
        expect(codeOnly(source)).toMatch(pattern);
      });
    }

    for (const pattern of site.forbids ?? []) {
      it(`no longer has ${pattern}`, () => {
        expect(codeOnly(source)).not.toMatch(pattern);
      });
    }
  });
});
