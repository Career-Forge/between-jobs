import { describe, expect, it } from "vitest";
import { PAGES, nextStepWithRow } from "..";
import type { NextStepInput, NextStepOutcome, ProfileFacts, SetupCheck } from "..";
import { ALL_STATUSES, type ApplicationStatus } from "../../applicationsTypes";
import { findForbiddenWord } from "../../trail";
import { START_TRACKING_INTENT, YOUR_MOVE, isApplicationsFacts, rules, type ApplicationsFacts, type StatusCounts } from "./applications";

// The Applications page's rows as a table: what the list says in, the row that decides, and what
// the band says out. The global rows run first, so the table also pins the cases they decide here.

const ACTIVE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: true, importWaiting: false } };
const NO_PROFILE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: false, importWaiting: false } };
const KEY_READY: SetupCheck<{ modelKey: "ready" }> = { state: "ready", value: { modelKey: "ready" } };

function counts(overrides: Partial<Record<ApplicationStatus, number>> = {}): StatusCounts {
  return Object.fromEntries(ALL_STATUSES.map((status) => [status, overrides[status] ?? 0])) as StatusCounts;
}

const ready = (overrides: Partial<Record<ApplicationStatus, number>> = {}): ApplicationsFacts => ({
  list: { state: "ready", counts: counts(overrides) },
});
const LOADING: ApplicationsFacts = { list: { state: "loading" } };

function ask(input: Partial<NextStepInput<ApplicationsFacts>>) {
  return nextStepWithRow({ page: "applications", signedIn: true, profile: ACTIVE, credentials: KEY_READY, ...input } as NextStepInput);
}

function step(outcome: NextStepOutcome) {
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

function actionWords(action: NonNullable<ReturnType<typeof step>>["action"]): string | null {
  if (action === null) return null;
  switch (action.kind) {
    case "button":
      return `button:${action.label}:${action.intent}`;
    case "link":
      return `link:${action.label}:${action.to}`;
    default:
      return action.kind;
  }
}

describe("the page is registered", () => {
  it("is one of the pages the registry found, with its own row ids", () => {
    expect(PAGES.map((page) => page.page)).toContain("applications");
    expect(rules.rows.map((row) => row.id)).toEqual(["P0", "P1", "P2"]);
  });
});

describe("the table", () => {
  const rows: Array<{
    name: string;
    facts: ApplicationsFacts;
    row: string;
    sentence: string;
    action: string | null;
    why: string[];
  }> = [
    {
      name: "P1 nothing tracked: start with the paste form",
      facts: ready(),
      row: "P1",
      sentence: "Track your first job -- paste a posting or fetch it by URL.",
      action: `button:Start tracking:${START_TRACKING_INTENT}`,
      why: ["0 applications"],
    },
    {
      name: "P2 saved, screening, interviewing and offer are your move; applied is theirs",
      facts: ready({ saved: 2, screening: 1, interviewing: 1, offer: 1, applied: 3 }),
      row: "P2",
      sentence: "Your move on 5 · their move on 3.",
      action: null,
      why: ["5 saved, screening, interviewing, offer", "3 applied"],
    },
    {
      name: "P2 a single saved job",
      facts: ready({ saved: 1 }),
      row: "P2",
      sentence: "Your move on 1 · their move on 0.",
      action: null,
      why: ["1 saved, screening, interviewing, offer", "0 applied"],
    },
    {
      name: "P2 rejected and withdrawn are at rest: counted in neither, and the page is still not empty",
      facts: ready({ rejected: 2, withdrawn: 1 }),
      row: "P2",
      sentence: "Your move on 0 · their move on 0.",
      action: null,
      why: ["0 saved, screening, interviewing, offer", "0 applied"],
    },
    {
      name: "P2 only applied: nothing is yours to do",
      facts: ready({ applied: 4 }),
      row: "P2",
      sentence: "Your move on 0 · their move on 4.",
      action: null,
      why: ["0 saved, screening, interviewing, offer", "4 applied"],
    },
    {
      name: "P0 the list is still being read: nothing is lit",
      facts: LOADING,
      row: "P0",
      sentence: "",
      action: null,
      why: [],
    },
  ];

  for (const entry of rows) {
    it(entry.name, () => {
      const evaluation = ask({ facts: entry.facts });
      expect(evaluation.rowId).toBe(entry.row);
      if (entry.row === "P0") {
        expect(evaluation.outcome).toEqual({ status: "loading" });
        return;
      }
      const result = step(evaluation.outcome);
      expect(result.sentence).toBe(entry.sentence);
      expect(actionWords(result.action)).toBe(entry.action);
      expect([...result.why]).toEqual(entry.why);
      expect(result.secondary ?? null).toBeNull();
    });
  }
});

describe("what the page may say", () => {
  it("lights the band for an empty list only: a list with rows has no single right button", () => {
    for (const [name, facts] of [
      ["empty", ready()],
      ["one saved", ready({ saved: 1 })],
      ["everything applied", ready({ applied: 9 })],
    ] as const) {
      const result = step(ask({ facts }).outcome);
      expect(result.action === null, name).toBe(name !== "empty");
    }
  });

  it("never says a thing the page cannot read (verified, a model's name, a date it was not given)", () => {
    for (const facts of [ready(), ready({ saved: 3, applied: 1 })]) {
      const result = step(ask({ facts }).outcome);
      expect(findForbiddenWord([result.sentence, ...result.why].join(" "))).toBeNull();
      expect(result.why.join(" ")).not.toMatch(/\d{4}-\d{2}/);
    }
  });

  it("counts the four statuses where the next move is yours, in pipeline order", () => {
    expect([...YOUR_MOVE]).toEqual(["saved", "screening", "interviewing", "offer"]);
  });
});

describe("the global rows that run first", () => {
  it("G2 sends a person with no profile to their profile before the list is read", () => {
    const evaluation = ask({ profile: NO_PROFILE, facts: ready() });
    expect(evaluation.rowId).toBe("G2");
    expect(actionWords(step(evaluation.outcome).action)).toBe("link:Set up your profile:/profile");
  });

  it("G3 asks for a model key when the credentials say there is none", () => {
    const evaluation = ask({ credentials: { state: "ready", value: { modelKey: "none" } }, facts: ready({ saved: 1 }) });
    expect(evaluation.rowId).toBe("G3");
  });

  it("G1 says the setup check failed, naming the call", () => {
    const evaluation = ask({ profile: { state: "failed", call: "GET /profile/current", at: "09:14" }, facts: ready() });
    expect(evaluation.rowId).toBe("G1");
    expect(step(evaluation.outcome).why).toEqual(["GET /profile/current failed", "09:14"]);
  });

  it("F1 says the page's own read failed, with Try again, ahead of the page's rows", () => {
    const evaluation = ask({
      pageFailure: { what: "Couldn't load your applications", call: "GET /applications", at: "09:14", intent: "retry" },
      facts: LOADING,
    });
    expect(evaluation.rowId).toBe("F1");
    const result = step(evaluation.outcome);
    expect(result.sentence).toBe("Couldn't load your applications. You didn't do anything wrong, and nothing was lost.");
    expect(actionWords(result.action)).toBe("button:Try again:retry");
    expect(result.tone).toBe("failure");
    expect([...result.why]).toEqual(["GET /applications failed", "09:14"]);
  });

  it("holds the band in loading until the profile read has answered", () => {
    expect(ask({ profile: { state: "loading" }, facts: ready() }).outcome).toEqual({ status: "loading" });
  });
});

describe("the facts guard", () => {
  it("accepts a loading answer and a full set of counts", () => {
    expect(isApplicationsFacts(LOADING)).toBe(true);
    expect(isApplicationsFacts(ready({ saved: 2 }))).toBe(true);
  });

  it("refuses anything else, so the page's rows are not run on facts they cannot read", () => {
    for (const bad of [
      null,
      {},
      { list: null },
      { list: { state: "ready" } },
      { list: { state: "ready", counts: { saved: 1 } } },
      { list: { state: "ready", counts: { ...counts(), saved: -1 } } },
      { list: { state: "ready", counts: { ...counts(), saved: 1.5 } } },
      { list: { state: "ready", counts: { ...counts(), saved: "1" } } },
      { list: { state: "unknown" } },
    ]) {
      expect(isApplicationsFacts(bad)).toBe(false);
    }
    const outcome = nextStepWithRow({ page: "applications", signedIn: true, profile: ACTIVE, credentials: KEY_READY, facts: { list: { state: "ready" } } });
    expect(outcome.outcome).toEqual({ status: "none" });
  });
});
