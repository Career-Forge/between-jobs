import { describe, expect, it } from "vitest";
import { findForbiddenWord } from "../../trail";
import { nextStep, nextStepWithRow } from "../index";
import type { NextStep, NextStepInput, SetupCheck } from "../types";
import {
  IMPORT_REVIEW_PATH,
  PROFILE_INTENTS,
  SET_UP_PATH,
  isProfilePageFacts,
  profileOutcome,
  reviewStep,
  type ProfilePageFacts,
  type WaitingImport,
} from "./profile";

// One row per case: what the Profile page has read, the row that decides, and what the band says. The
// rows are a pure function of the facts, so there is no page here, only the table.

const ready = <T>(value: T): SetupCheck<T> => ({ state: "ready", value });

const FILE = (overrides: Partial<Extract<WaitingImport, { source: "file" }>> = {}): WaitingImport => ({
  source: "file",
  found: 26,
  leftOut: 4,
  remaining: 3,
  ...overrides,
});

function facts(overrides: Partial<ProfilePageFacts> = {}): ProfilePageFacts {
  return { waiting: null, onReview: false, activeSince: "Sep 21", ...overrides };
}

function input(page: ProfilePageFacts, overrides: Partial<NextStepInput> = {}): NextStepInput {
  return {
    page: "profile",
    signedIn: true,
    profile: ready({ active: true, importWaiting: page.waiting !== null }),
    credentials: ready({ modelKey: "ready" as const }),
    facts: page,
    ...overrides,
  };
}

function stepOf(page: ProfilePageFacts, overrides: Partial<NextStepInput> = {}): NextStep {
  const outcome = nextStep(input(page, overrides));
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

describe("the Profile rows", () => {
  const table: {
    name: string;
    row: string;
    facts: ProfilePageFacts;
    sentence: string;
    action: { label: string; to?: string; intent?: string } | null;
    why: string[];
    locked?: string;
  }[] = [
    {
      name: "a resume file's draft is waiting and the person is elsewhere on the page",
      row: "PR1",
      facts: facts({ waiting: FILE() }),
      sentence: "Your import is waiting. Check every field, then activate.",
      action: { label: "Review the import", to: "/profile#import-review" },
      why: ["import waiting for review", "converted by your model, probabilistic", "not active until you confirm"],
    },
    {
      name: "a JSON import is waiting and the person is elsewhere on the page",
      row: "PR1",
      facts: facts({ waiting: { source: "json" } }),
      sentence: "Your import is waiting. Check every field, then activate.",
      action: { label: "Review the import", to: "/profile#import-review" },
      why: ["import waiting for review", "parsed by code, no model", "not active until you confirm"],
    },
    {
      name: "on the review of a file draft with fields still to check, the button is locked",
      row: "IR1",
      facts: facts({ waiting: FILE(), onReview: true }),
      sentence: "Check every field, then make this your profile.",
      action: { label: "Confirm and make active", intent: "use-import" },
      why: ["26 fields found", "4 left out", "3 still to check", "draft inactive"],
      locked: "Check the 3 remaining fields to unlock this.",
    },
    {
      name: "one field left to check says so in the singular",
      row: "IR1",
      facts: facts({ waiting: FILE({ remaining: 1 }), onReview: true }),
      sentence: "Check every field, then make this your profile.",
      action: { label: "Confirm and make active", intent: "use-import" },
      why: ["26 fields found", "4 left out", "1 still to check", "draft inactive"],
      locked: "Check the 1 remaining field to unlock this.",
    },
    {
      name: "every field checked unlocks the button",
      row: "IR1",
      facts: facts({ waiting: FILE({ remaining: 0 }), onReview: true }),
      sentence: "Check every field, then make this your profile.",
      action: { label: "Confirm and make active", intent: "use-import" },
      why: ["26 fields found", "4 left out", "0 still to check", "draft inactive"],
    },
    {
      name: "a server that did not say what was left out gets no left-out slot",
      row: "IR1",
      facts: facts({ waiting: FILE({ leftOut: null }), onReview: true }),
      sentence: "Check every field, then make this your profile.",
      action: { label: "Confirm and make active", intent: "use-import" },
      why: ["26 fields found", "3 still to check", "draft inactive"],
      locked: "Check the 3 remaining fields to unlock this.",
    },
    {
      name: "one field found is a singular",
      row: "IR1",
      facts: facts({ waiting: FILE({ found: 1, remaining: 0, leftOut: 0 }), onReview: true }),
      sentence: "Check every field, then make this your profile.",
      action: { label: "Confirm and make active", intent: "use-import" },
      why: ["1 field found", "0 left out", "0 still to check", "draft inactive"],
    },
    {
      name: "on the review of a JSON import there is no list to check, so nothing is locked",
      row: "IR1",
      facts: facts({ waiting: { source: "json" }, onReview: true }),
      sentence: "Check what was read, then make this your profile.",
      action: { label: "Confirm and make active", intent: "activate-json" },
      why: ["import waiting for review", "parsed by code, no model", "draft inactive"],
    },
    {
      name: "nothing is waiting: unlit, with the date the profile became active",
      row: "PR0",
      facts: facts(),
      sentence: "Your profile is active. Edits save a new version.",
      action: null,
      why: ["profile active since Sep 21"],
    },
    {
      name: "nothing is waiting and no activation date was returned: no date is guessed",
      row: "PR0",
      facts: facts({ activeSince: null }),
      sentence: "Your profile is active. Edits save a new version.",
      action: null,
      why: ["profile active"],
    },
  ];

  it.each(table)("$row: $name", (row) => {
    const result = nextStepWithRow(input(row.facts));
    expect(result.rowId).toBe(row.row);
    if (result.outcome.status !== "ready") throw new Error("expected a step");
    const step = result.outcome.step;
    expect(step.sentence).toBe(row.sentence);
    expect(step.why).toEqual(row.why);
    if (row.action === null) {
      expect(step.action).toBeNull();
    } else if (row.action.to !== undefined) {
      expect(step.action).toEqual({ kind: "link", label: row.action.label, to: row.action.to });
    } else {
      expect(step.action).toEqual({ kind: "button", label: row.action.label, intent: row.action.intent });
    }
    expect("locked" in step ? step.locked?.hint : undefined).toBe(row.locked);
  });

  it("never says 'verified' in a sentence, a button or a why line", () => {
    for (const waiting of [null, { source: "json" } as const, FILE()]) {
      for (const onReview of [false, true]) {
        const step = stepOf(facts({ waiting, onReview }));
        const words = [step.sentence, ...step.why, step.action?.label ?? "", "locked" in step ? (step.locked?.hint ?? "") : ""];
        for (const word of words) expect(findForbiddenWord(word), word).toBeNull();
      }
    }
  });

  it("is never lit without an action the page can carry out, and an unlit band has no lock", () => {
    for (const waiting of [null, { source: "json" } as const, FILE(), FILE({ remaining: 0 })]) {
      for (const onReview of [false, true]) {
        const step = stepOf(facts({ waiting, onReview: onReview && waiting !== null }));
        if (step.action === null) expect("locked" in step && step.locked !== undefined).toBe(false);
      }
    }
  });
});

describe("the rows come after the global ones", () => {
  it("G2 answers first with no active profile, whatever the page facts say", () => {
    const result = nextStepWithRow(input(facts(), { profile: ready({ active: false, importWaiting: false }) }));
    expect(result.rowId).toBe("G2");
  });

  it("G2b answers first with no active profile and a waiting import", () => {
    const result = nextStepWithRow(input(facts({ waiting: FILE() }), { profile: ready({ active: false, importWaiting: true }) }));
    expect(result.rowId).toBe("G2b");
  });

  it("G3 answers before a waiting import when no model key is usable", () => {
    const result = nextStepWithRow(input(facts({ waiting: { source: "json" } }), { credentials: ready({ modelKey: "none" as const }) }));
    expect(result.rowId).toBe("G3");
  });

  it("G1 answers when the profile read failed, and the page rows wait while it is still being read", () => {
    const failed = nextStepWithRow(input(facts(), { profile: { state: "failed", call: "GET /profile/current", at: "09:14" } }));
    expect(failed.rowId).toBe("G1");
    const loading = nextStepWithRow(input(facts(), { profile: { state: "loading" } }));
    expect(loading.outcome).toEqual({ status: "loading" });
  });

  it("holds the band while the credentials read is out, since the page asked for it", () => {
    const loading = nextStepWithRow(input(facts(), { credentials: { state: "loading" } }));
    expect(loading.outcome).toEqual({ status: "loading" });
  });
});

describe("profileOutcome: what the global rows say on the Profile page itself", () => {
  const setup = (page: ProfilePageFacts, active: boolean) =>
    nextStepWithRow(input(page, { profile: ready({ active, importWaiting: page.waiting !== null }) }));

  it("points G2 at the import section instead of at the page it is on", () => {
    const page = facts();
    const outcome = profileOutcome(setup(page, false), page);
    if (outcome.status !== "ready") throw new Error("expected a step");
    expect(outcome.step.action).toEqual({ kind: "link", label: "Set up your profile", to: SET_UP_PATH });
    expect(outcome.step.sentence).toBe("Everything here starts from your profile. Import it first.");
  });

  it("points G2b at the review when the person is not on it", () => {
    const page = facts({ waiting: FILE(), onReview: false });
    const outcome = profileOutcome(setup(page, false), page);
    if (outcome.status !== "ready") throw new Error("expected a step");
    expect(outcome.step.action).toEqual({ kind: "link", label: "Review the import", to: IMPORT_REVIEW_PATH });
  });

  it("puts the review's own step in place of G2b while the person is on the review", () => {
    const page = facts({ waiting: FILE(), onReview: true });
    const outcome = profileOutcome(setup(page, false), page);
    if (outcome.status !== "ready") throw new Error("expected a step");
    expect(outcome.step).toEqual(reviewStep(page));
    expect(outcome.step.action).toEqual({ kind: "button", label: "Confirm and make active", intent: PROFILE_INTENTS.useImport });
  });

  it("leaves every other answer exactly as the engine gave it", () => {
    const page = facts({ waiting: FILE(), onReview: true });
    for (const evaluation of [
      nextStepWithRow(input(page, { profile: { state: "loading" } })),
      nextStepWithRow(input(page, { profile: { state: "failed", call: "GET /profile/current", at: "09:14" } })),
      nextStepWithRow(input(page, { credentials: ready({ modelKey: "none" as const }) })),
      setup(facts(), true),
    ]) {
      expect(profileOutcome(evaluation, page)).toBe(evaluation.outcome);
    }
  });
});

describe("the one-step button when it would take the place of a profile in use", () => {
  it.each([
    ["a JSON import", { source: "json" } as WaitingImport, "Check what was read, then make this your profile."],
    ["a file draft", FILE({ remaining: 0 }), "Check every field, then make this your profile."],
  ])("says so in the sentence beside the button for %s, and only then", (_name, waiting, sentence) => {
    expect(reviewStep(facts({ waiting, onReview: true, replacesCurrent: true }))?.sentence).toBe(`${sentence} It replaces your current one.`);
    expect(reviewStep(facts({ waiting, onReview: true, replacesCurrent: false }))?.sentence).toBe(sentence);
    expect(reviewStep(facts({ waiting, onReview: true }))?.sentence).toBe(sentence);
  });
});

describe("the facts guard", () => {
  it("accepts what the page builds", () => {
    expect(isProfilePageFacts(facts())).toBe(true);
    expect(isProfilePageFacts(facts({ waiting: { source: "json" } }))).toBe(true);
    expect(isProfilePageFacts(facts({ waiting: FILE({ leftOut: null }) }))).toBe(true);
  });

  it.each([
    ["no facts", undefined],
    ["a non-boolean onReview", { waiting: null, onReview: "yes", activeSince: null }],
    ["a number for the date", { waiting: null, onReview: false, activeSince: 3 }],
    ["more fields to check than found", { waiting: { source: "file", found: 1, leftOut: 0, remaining: 2 }, onReview: false, activeSince: null }],
    ["a negative count", { waiting: { source: "file", found: 1, leftOut: -1, remaining: 0 }, onReview: false, activeSince: null }],
    ["an unknown source", { waiting: { source: "email" }, onReview: false, activeSince: null }],
    ["a non-boolean replacesCurrent", { waiting: null, onReview: false, activeSince: null, replacesCurrent: "yes" }],
  ])("refuses %s, so no step is made up from it", (_name, value) => {
    expect(isProfilePageFacts(value)).toBe(false);
    const unreadable: NextStepInput = { ...input(facts()), facts: value };
    expect(nextStep(unreadable)).toEqual({ status: "none" });
  });
});
