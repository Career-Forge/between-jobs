import { describe, expect, it } from "vitest";
import { findForbiddenWord } from "../../trail";
import { nextStep, nextStepWithRow } from "../index";
import type { NextStep, NextStepInput, SetupCheck } from "../types";
import {
  REVIEW_HASH,
  AUTO_MOVE_PERCENT,
  confidencePercent,
  PROPOSAL_STEPS,
  isMappableProposal,
  isTodayFacts,
  parseTodayIntent,
  todayIntent,
  todayLit,
  type TodayFacts,
  type TodayItemFact,
} from "./today";

// One row per case: what Today has read, the row that decides, and what the band says. The rows are
// a pure function of the facts, so there is no page here, only the table.

const ready = <T>(value: T): SetupCheck<T> => ({ state: "ready", value });

function item(overrides: Partial<TodayItemFact> & Pick<TodayItemFact, "id" | "kind">): TodayItemFact {
  return {
    createdAt: "2026-09-22T10:00:00Z",
    applicationId: null,
    job: null,
    company: null,
    proposal: null,
    match: null,
    atsScore: null,
    trackedApplicationId: null,
    fit: null,
    ...overrides,
  };
}

function facts(items: readonly TodayItemFact[] | "loading", overrides: Partial<TodayFacts> = {}): TodayFacts {
  return {
    feed: items === "loading" ? { state: "loading" } : { state: "ready", items },
    names: "settled",
    savedSearches: { state: "ready", total: 2, active: 2 },
    ...overrides,
  };
}

function input(today: TodayFacts, overrides: Partial<NextStepInput> = {}): NextStepInput {
  return {
    page: "today",
    signedIn: true,
    profile: ready({ active: true, importWaiting: false }),
    credentials: ready({} as Record<string, never>),
    facts: today,
    ...overrides,
  };
}

function stepOf(today: TodayFacts): NextStep {
  const outcome = nextStep(input(today));
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

const proposal = (overrides: Partial<TodayItemFact> = {}) =>
  item({
    id: "p1",
    kind: "status_proposal",
    applicationId: "app-1",
    company: "Northstar Labs",
    proposal: { proposedType: "interview.requested", confidence: 0.72, status: "pending" },
    ...overrides,
  });
const held = (overrides: Partial<TodayItemFact> = {}) =>
  item({ id: "f1", kind: "resume_failed", applicationId: "app-2", job: "Data Engineer at Acme", fit: "held", ...overrides });
const ready_ = (overrides: Partial<TodayItemFact> = {}) =>
  item({ id: "r1", kind: "resume_ready", applicationId: "app-3", job: "ML Engineer at Orbit", atsScore: 72, ...overrides });
const match = (overrides: Partial<TodayItemFact> = {}) =>
  item({
    id: "m1",
    kind: "high_fit_job",
    match: { title: "Analytics Engineer", company: "Initech", score100: 74, bin: "Strong", searchLabel: '"analytics engineer"' },
    ...overrides,
  });
const tracked = item({ id: "t1", kind: "job_tracked", applicationId: "app-4" });
const staged = item({ id: "s1", kind: "stage_changed", applicationId: "app-4" });

interface Row {
  name: string;
  id: string;
  facts: TodayFacts;
  sentence: string;
  action: NextStep["action"];
  secondary?: NextStep["secondary"];
  why: string[];
}

const ROWS: Row[] = [
  {
    name: "T1 a proposal with a stage to move to",
    id: "T1",
    facts: facts([proposal()]),
    sentence: "Northstar Labs may have requested an interview. Read their words, then decide.",
    action: { kind: "button", label: "Accept: move to Interviewing", intent: "accept:p1" },
    why: ["status proposal", "72%", "under the 85% auto-move line"],
  },
  {
    name: "T1 an assessment, which moves the application to Screening",
    id: "T1",
    facts: facts([proposal({ proposal: { proposedType: "assessment.received", confidence: 0.6, status: "pending" } })]),
    sentence: "Northstar Labs may have sent an assessment. Read their words, then decide.",
    action: { kind: "button", label: "Accept: move to Screening", intent: "accept:p1" },
    why: ["status proposal", "60%", "under the 85% auto-move line"],
  },
  {
    name: "T1 an offer",
    id: "T1",
    facts: facts([proposal({ proposal: { proposedType: "offer.received", confidence: 0.5, status: "pending" } })]),
    sentence: "Northstar Labs may have made an offer. Read their words, then decide.",
    action: { kind: "button", label: "Accept: move to Offer", intent: "accept:p1" },
    why: ["status proposal", "50%", "under the 85% auto-move line"],
  },
  {
    name: "T1 a rejection",
    id: "T1",
    facts: facts([proposal({ proposal: { proposedType: "application.rejected", confidence: 0.55, status: "pending" } })]),
    sentence: "Northstar Labs may have turned this application down. Read their words, then decide.",
    action: { kind: "button", label: "Accept: move to Rejected", intent: "accept:p1" },
    why: ["status proposal", "55%", "under the 85% auto-move line"],
  },
  {
    name: "T1 with no company the application's name is not guessed",
    id: "T1",
    facts: facts([proposal({ company: null })]),
    sentence: "A reply to your application may have requested an interview. Read the words, then decide.",
    action: { kind: "button", label: "Accept: move to Interviewing", intent: "accept:p1" },
    why: ["status proposal", "72%", "under the 85% auto-move line"],
  },
  {
    name: "T1 wins over a held-back resume, a ready one and a high fit job",
    id: "T1",
    facts: facts([held(), ready_(), match(), proposal()]),
    sentence: "Northstar Labs may have requested an interview. Read their words, then decide.",
    action: { kind: "button", label: "Accept: move to Interviewing", intent: "accept:p1" },
    why: ["status proposal", "72%", "under the 85% auto-move line"],
  },
  {
    name: "T2 a resume the fit check held back: See why, with Generate anyway beside it",
    id: "T2",
    facts: facts([held()]),
    sentence: "The fit check held back your draft for Data Engineer at Acme. Read why, then decide.",
    action: { kind: "link", label: "See why", to: "/applications/app-2#generate" },
    secondary: { kind: "button", label: "Generate anyway", intent: "generate-anyway:f1" },
    why: ["held back by the fit check", "no resume generated"],
  },
  {
    name: "T2 with no job name the sentence names none",
    id: "T2",
    facts: facts([held({ job: null })]),
    sentence: "The fit check held back your draft. Read why, then decide.",
    action: { kind: "link", label: "See why", to: "/applications/app-2#generate" },
    secondary: { kind: "button", label: "Generate anyway", intent: "generate-anyway:f1" },
    why: ["held back by the fit check", "no resume generated"],
  },
  {
    name: "T2 a card whose stored result could not be read says only what the card is, not that no resume exists",
    id: "T2",
    facts: facts([held({ fit: "unread" })]),
    sentence: "The fit check held back your draft for Data Engineer at Acme. Read why, then decide.",
    action: { kind: "link", label: "See why", to: "/applications/app-2#generate" },
    secondary: { kind: "button", label: "Generate anyway", intent: "generate-anyway:f1" },
    why: ["held back by the fit check"],
  },
  {
    name: "T3 a held-back card whose application has a resume now is not lit: the card is out of date",
    id: "T3",
    facts: facts([held({ fit: "moved_on" }), ready_()]),
    sentence: "Your resume for ML Engineer at Orbit is ready for review.",
    action: { kind: "link", label: "Open workspace", to: "/applications/app-3#ch-check" },
    why: ["resume ready", "ATS score 72 / 100"],
  },
  {
    name: "T5 a held-back card that has moved on is only an update below",
    id: "T5",
    facts: facts([held({ fit: "moved_on" })]),
    sentence: "Nothing needs a decision right now. 1 update below.",
    action: null,
    why: ["0 items need a decision", "2 saved searches active"],
  },
  {
    name: "T2 wins over a ready resume and a high fit job",
    id: "T2",
    facts: facts([ready_(), match(), held()]),
    sentence: "The fit check held back your draft for Data Engineer at Acme. Read why, then decide.",
    action: { kind: "link", label: "See why", to: "/applications/app-2#generate" },
    secondary: { kind: "button", label: "Generate anyway", intent: "generate-anyway:f1" },
    why: ["held back by the fit check", "no resume generated"],
  },
  {
    name: "T3 a resume ready for review, with its ATS score",
    id: "T3",
    facts: facts([ready_()]),
    sentence: "Your resume for ML Engineer at Orbit is ready for review.",
    action: { kind: "link", label: "Open workspace", to: "/applications/app-3#ch-check" },
    why: ["resume ready", "ATS score 72 / 100"],
  },
  {
    name: "T3 without a score in the item the why line has none",
    id: "T3",
    facts: facts([ready_({ atsScore: null })]),
    sentence: "Your resume for ML Engineer at Orbit is ready for review.",
    action: { kind: "link", label: "Open workspace", to: "/applications/app-3#ch-check" },
    why: ["resume ready"],
  },
  {
    name: "T3 without a job name",
    id: "T3",
    facts: facts([ready_({ job: null })]),
    sentence: "Your resume is ready for review.",
    action: { kind: "link", label: "Open workspace", to: "/applications/app-3#ch-check" },
    why: ["resume ready", "ATS score 72 / 100"],
  },
  {
    name: "T3 wins over a high fit job",
    id: "T3",
    facts: facts([match(), ready_()]),
    sentence: "Your resume for ML Engineer at Orbit is ready for review.",
    action: { kind: "link", label: "Open workspace", to: "/applications/app-3#ch-check" },
    why: ["resume ready", "ATS score 72 / 100"],
  },
  {
    name: "T4 a high fit job not tracked yet: Track",
    id: "T4",
    facts: facts([match()]),
    sentence: "A Strong match landed: Analytics Engineer at Initech.",
    action: { kind: "button", label: "Track", intent: "track:m1" },
    why: ["high fit job", "74 / 100", 'saved search "analytics engineer"'],
  },
  {
    name: "T4 once tracked in this visit the button becomes Open workspace",
    id: "T4",
    facts: facts([match({ trackedApplicationId: "app-9" })]),
    sentence: "A Strong match landed: Analytics Engineer at Initech.",
    action: { kind: "link", label: "Open workspace", to: "/applications/app-9" },
    why: ["high fit job", "74 / 100", 'saved search "analytics engineer"'],
  },
  {
    name: "T4 with no company and no matching saved search, neither is named",
    id: "T4",
    facts: facts([match({ match: { title: "Analytics Engineer", company: null, score100: 71, bin: "Strong", searchLabel: null } })]),
    sentence: "A Strong match landed: Analytics Engineer.",
    action: { kind: "button", label: "Track", intent: "track:m1" },
    why: ["high fit job", "71 / 100"],
  },
  {
    name: "T4 a bin the page does not know is not called Strong",
    id: "T4",
    facts: facts([match({ match: { title: "Analytics Engineer", company: "Initech", score100: 71, bin: "Excellent", searchLabel: null } })]),
    sentence: "A match landed: Analytics Engineer at Initech.",
    action: { kind: "button", label: "Track", intent: "track:m1" },
    why: ["high fit job", "71 / 100"],
  },
  {
    name: "T5a an empty feed and no saved search",
    id: "T5a",
    facts: facts([], { savedSearches: { state: "ready", total: 0, active: 0 } }),
    sentence: "Save a search in Discover so Strong matches can land here.",
    action: { kind: "link", label: "Go to Discover", to: "/discover" },
    why: ["0 items", "0 saved searches"],
  },
  {
    name: "T5 an empty feed with saved searches",
    id: "T5",
    facts: facts([], { savedSearches: { state: "ready", total: 3, active: 2 } }),
    sentence: "Nothing needs a decision right now.",
    action: null,
    why: ["0 items need a decision", "2 saved searches active"],
  },
  {
    name: "T5 one saved search is singular",
    id: "T5",
    facts: facts([], { savedSearches: { state: "ready", total: 1, active: 1 } }),
    sentence: "Nothing needs a decision right now.",
    action: null,
    why: ["0 items need a decision", "1 saved search active"],
  },
  {
    name: "T5 only logged items below: counted, and still unlit",
    id: "T5",
    facts: facts([tracked, staged]),
    sentence: "Nothing needs a decision right now. 2 updates below.",
    action: null,
    why: ["0 items need a decision", "2 saved searches active"],
  },
  {
    name: "T5 one update below is singular",
    id: "T5",
    facts: facts([tracked]),
    sentence: "Nothing needs a decision right now. 1 update below.",
    action: null,
    why: ["0 items need a decision", "2 saved searches active"],
  },
  {
    name: "T5 a proposal with no stage to move to is for Dismiss only, so nothing is lit",
    id: "T5",
    facts: facts([proposal({ proposal: { proposedType: "recruiter.replied", confidence: 0.4, status: "pending" } })]),
    sentence: "Nothing needs a decision right now. 1 update below.",
    action: null,
    why: ["0 items need a decision", "2 saved searches active"],
  },
  {
    name: "T5 a proposal that was already decided is not lit: Accept and Dismiss would both be refused",
    id: "T5",
    facts: facts([proposal({ proposal: { proposedType: "interview.requested", confidence: 0.72, status: "dismissed" } })]),
    sentence: "Nothing needs a decision right now. 1 update below.",
    action: null,
    why: ["0 items need a decision", "2 saved searches active"],
  },
  {
    name: "T5 an accepted proposal is not lit either",
    id: "T5",
    facts: facts([proposal({ proposal: { proposedType: "interview.requested", confidence: 0.72, status: "accepted" } })]),
    sentence: "Nothing needs a decision right now. 1 update below.",
    action: null,
    why: ["0 items need a decision", "2 saved searches active"],
  },
  {
    name: "T3 a decided proposal does not take the band ahead of a real next step",
    id: "T3",
    facts: facts([proposal({ proposal: { proposedType: "interview.requested", confidence: 0.72, status: "dismissed" } }), ready_()]),
    sentence: "Your resume for ML Engineer at Orbit is ready for review.",
    action: { kind: "link", label: "Open workspace", to: "/applications/app-3#ch-check" },
    why: ["resume ready", "ATS score 72 / 100"],
  },
  {
    name: "T5 an item with no application can never light T2 or T3",
    id: "T5",
    facts: facts([held({ applicationId: null }), ready_({ applicationId: null })]),
    sentence: "Nothing needs a decision right now. 2 updates below.",
    action: null,
    why: ["0 items need a decision", "2 saved searches active"],
  },
  {
    name: "T5 with the saved searches unreadable the why line leaves their count out, and an empty feed is not T5a",
    id: "T5",
    facts: facts([], { savedSearches: { state: "failed" } }),
    sentence: "Nothing needs a decision right now.",
    action: null,
    why: ["0 items need a decision"],
  },
];

describe("Today's rows", () => {
  for (const row of ROWS) {
    it(row.name, () => {
      const evaluation = nextStepWithRow(input(row.facts));
      expect(evaluation.rowId).toBe(row.id);
      if (evaluation.outcome.status !== "ready") throw new Error("expected a step");
      const step = evaluation.outcome.step;
      expect(step.sentence).toBe(row.sentence);
      expect(step.action).toEqual(row.action);
      expect(step.secondary ?? undefined).toEqual(row.secondary);
      expect(step.why).toEqual(row.why);
      expect(step.tone).toBeUndefined();
    });
  }

  it("has a case for every one of Today's rows", () => {
    expect(new Set(ROWS.map((row) => row.id))).toEqual(new Set(["T1", "T2", "T3", "T4", "T5a", "T5"]));
  });

  it("says nothing a trail never says", () => {
    for (const row of ROWS) {
      const step = stepOf(row.facts);
      const words = [step.sentence, ...step.why, step.action?.label ?? ""].join(" ");
      expect(findForbiddenWord(words)).toBeNull();
    }
  });

  it("never says an item was read, reviewed or opened: nothing returned says so", () => {
    for (const row of ROWS) {
      const step = stepOf(row.facts);
      expect(step.why.join(" ")).not.toMatch(/not reviewed|reviewed|unread|not read/i);
    }
  });
});

describe("the choice among items", () => {
  it("takes the newest within a row", () => {
    const older = proposal({ id: "old", createdAt: "2026-09-20T10:00:00Z", company: "Older Co" });
    const newer = proposal({ id: "new", createdAt: "2026-09-22T10:00:00Z", company: "Newer Co" });
    expect(todayLit([older, newer])?.item.id).toBe("new");
    expect(todayLit([newer, older])?.item.id).toBe("new");
  });

  it("reads times as instants, not as text, so a different offset still orders correctly", () => {
    const early = held({ id: "early", createdAt: "2026-09-22T23:00:00-05:00" });
    const late = held({ id: "late", createdAt: "2026-09-23T01:00:00Z" });
    // 23:00 at -05:00 is 04:00Z on the 23rd: the later instant, though it sorts first as text.
    expect(todayLit([late, early])?.item.id).toBe("early");
  });

  it("keeps the served order for items whose times are equal or unreadable", () => {
    const a = held({ id: "a", createdAt: "not a time" });
    const b = held({ id: "b", createdAt: "not a time" });
    expect(todayLit([a, b])?.item.id).toBe("a");
    const c = held({ id: "c", createdAt: "2026-09-22T10:00:00Z" });
    const d = held({ id: "d", createdAt: "2026-09-22T10:00:00Z" });
    expect(todayLit([c, d])?.item.id).toBe("c");
  });

  it("lights an item with an application over a newer one without", () => {
    const withoutApplication = held({ id: "none", applicationId: null, createdAt: "2026-09-23T10:00:00Z" });
    const withApplication = held({ id: "has", createdAt: "2026-09-20T10:00:00Z" });
    expect(todayLit([withoutApplication, withApplication])?.item.id).toBe("has");
  });

  it("holds nothing when no item has a step", () => {
    expect(todayLit([tracked, staged])).toBeNull();
    expect(todayLit([])).toBeNull();
    expect(todayLit([item({ id: "x", kind: "high_fit_job" })])).toBeNull();
  });
});

describe("what the band waits for", () => {
  it("waits, lit by nothing, while the feed is being read", () => {
    expect(nextStepWithRow(input(facts("loading")))).toEqual({ outcome: { status: "loading" }, rowId: "T1" });
  });

  it("waits for the names a sentence uses, so it is not rewritten a moment after it shows", () => {
    for (const items of [[proposal()], [held()], [ready_()]]) {
      expect(nextStep(input(facts(items, { names: "loading" })))).toEqual({ status: "loading" });
    }
  });

  it("waits for a held-back card's stored result, which may take the card back", () => {
    expect(nextStep(input(facts([held({ fit: "loading" })])))).toEqual({ status: "loading" });
    // It also holds back what the card would outrank, so a lower step is not lit and then replaced.
    expect(nextStep(input(facts([held({ fit: "loading" }), ready_()])))).toEqual({ status: "loading" });
    expect(nextStep(input(facts([held({ fit: "loading" }), match()])))).toEqual({ status: "loading" });
    expect(nextStep(input(facts([held({ fit: "loading" })], { savedSearches: { state: "ready", total: 0, active: 0 } })))).toEqual({ status: "loading" });
  });

  it("does not wait for a read that cannot change the answer", () => {
    // A proposal outranks any held-back card.
    expect(nextStep(input(facts([proposal(), held({ fit: "loading" })]))).status).toBe("ready");
    // An older card still being read cannot replace the newer one that is already lit.
    const newer = held({ id: "newer", createdAt: "2026-09-22T10:00:00Z", fit: "held" });
    const older = held({ id: "older", createdAt: "2026-09-21T10:00:00Z", fit: "loading" });
    expect(nextStepWithRow(input(facts([newer, older]))).rowId).toBe("T2");
    // A card with no application is never lit, so its read does not matter.
    expect(nextStep(input(facts([held({ applicationId: null, fit: "loading" }), ready_()]))).status).toBe("ready");
  });

  it("does not wait for names it does not use", () => {
    expect(nextStep(input(facts([match()], { names: "loading" }))).status).toBe("ready");
  });

  it("waits for the saved searches only where the answer depends on them", () => {
    const loading = { savedSearches: { state: "loading" } } as const;
    expect(nextStep(input(facts([], loading)))).toEqual({ status: "loading" });
    expect(nextStep(input(facts([tracked], loading)))).toEqual({ status: "loading" });
    expect(nextStep(input(facts([proposal()], loading))).status).toBe("ready");
  });
});

describe("the global rows keep their place ahead of Today's", () => {
  it("shows the page's failed read, not a step, when GET /today failed", () => {
    const failed = input(facts([proposal()]), { pageFailure: { what: "Couldn't load Today", call: "GET /today", at: "09:14" } });
    expect(nextStepWithRow(failed).rowId).toBe("F1");
  });

  it("shows the profile step when there is no active profile, even with an item waiting", () => {
    const noProfile = input(facts([proposal()]), { profile: ready({ active: false, importWaiting: false }) });
    expect(nextStepWithRow(noProfile).rowId).toBe("G2");
  });

  it("shows the setup failure first", () => {
    const failed = input(facts([proposal()]), { profile: { state: "failed", call: "GET /profile/current", at: "09:14" } });
    expect(nextStepWithRow(failed).rowId).toBe("G1");
  });
});

describe("facts the page cannot read", () => {
  it("lights nothing for facts of the wrong shape", () => {
    for (const junk of ["junk", null, 42, {}, { feed: { state: "ready" }, names: "settled", savedSearches: { state: "ready" } }]) {
      expect(isTodayFacts(junk)).toBe(false);
      expect(nextStepWithRow({ page: "today", signedIn: true, profile: ready({ active: true, importWaiting: false }), credentials: ready({}), facts: junk })).toEqual({
        outcome: { status: "none" },
        rowId: null,
      });
    }
  });
});

describe("the review anchor", () => {
  it("is the anchor of the workspace's Review section, which the ready step links to", () => {
    expect(REVIEW_HASH).toBe("ch-check");
    const step = stepOf(facts([ready_()]));
    expect(step.action).toEqual({ kind: "link", label: "Open workspace", to: `/applications/app-3#${REVIEW_HASH}` });
  });
});

describe("proposals and intents", () => {
  it("maps exactly the five types the server can accept into a stage", () => {
    expect(Object.keys(PROPOSAL_STEPS).sort()).toEqual(
      ["application.rejected", "assessment.received", "interview.requested", "interview.scheduled", "offer.received"].sort(),
    );
    for (const type of ["application.acknowledged", "recruiter.replied", "unknown", "constructor", "toString"]) {
      expect(isMappableProposal(type)).toBe(false);
    }
  });

  it("states the server's auto-move line", () => {
    expect(AUTO_MOVE_PERCENT).toBe(85);
  });

  it("leaves the percentage and the line out of a confidence that is not a share", () => {
    for (const confidence of [Number.NaN, -0.1, 1.5]) {
      const step = stepOf(facts([proposal({ proposal: { proposedType: "interview.requested", confidence, status: "pending" } })]));
      expect(step.why).toEqual(["status proposal"]);
    }
  });

  it("does not say a proposal is under the line when it is not", () => {
    const step = stepOf(facts([proposal({ proposal: { proposedType: "interview.requested", confidence: 0.9, status: "pending" } })]));
    expect(step.why).toEqual(["status proposal", "90%"]);
  });

  it("shows the share the stored confidence stands for, not a float artifact of it", () => {
    // 0.57 * 100 is 56.99999999999999 and 0.29 * 100 is 28.999999999999996: neither is one short.
    for (const [confidence, shown] of [[0.57, "57%"], [0.58, "58%"], [0.29, "29%"], [0.07, "7%"], [1, "100%"], [0, "0%"]] as const) {
      const step = stepOf(facts([proposal({ proposal: { proposedType: "interview.requested", confidence, status: "pending" } })]));
      expect(step.why[1]).toBe(shown);
    }
  });

  it("rounds a share down, so it is never shown higher than it is", () => {
    expect(confidencePercent(0.579)).toBe(57);
    expect(confidencePercent(0.8499)).toBe(84);
    expect(confidencePercent(Number.NaN)).toBeNull();
  });

  it("puts the line on the confidence itself: 0.85 is on it, not under it", () => {
    const at = (confidence: number) => stepOf(facts([proposal({ proposal: { proposedType: "interview.requested", confidence, status: "pending" } })])).why;
    expect(at(0.85)).toEqual(["status proposal", "85%"]);
    expect(at(0.8499)).toEqual(["status proposal", "84%", "under the 85% auto-move line"]);
  });

  it("round-trips an intent with the item id in it", () => {
    for (const name of ["accept", "track", "generate-anyway"] as const) {
      expect(parseTodayIntent(todayIntent(name, "item:with:colons"))).toEqual({ name, itemId: "item:with:colons" });
    }
    for (const bad of ["", "accept", "accept:", "retry", "dismiss:1", ":1"]) expect(parseTodayIntent(bad)).toBeNull();
  });
});
