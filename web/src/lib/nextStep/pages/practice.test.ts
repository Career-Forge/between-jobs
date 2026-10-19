import { describe, expect, it } from "vitest";
import { PAGES, nextStepWithRow } from "..";
import type { NextStepInput, NextStepOutcome, ProfileFacts, SetupCheck } from "..";
import { findForbiddenWord } from "../../trail";
import { rules } from "./practice";

// The Practice pointer page has one row. It runs after the global rows, which this page does need:
// a person with no profile is sent there before being pointed at an application.

const ACTIVE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: true, importWaiting: false } };

function ask(input: Partial<NextStepInput> = {}) {
  return nextStepWithRow({ page: "practice", signedIn: true, profile: ACTIVE, credentials: { state: "ready", value: { modelKey: "ready" } }, ...input });
}

function step(outcome: NextStepOutcome) {
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

describe("the page is registered", () => {
  it("is one of the pages the registry found, with its one row", () => {
    expect(PAGES.map((page) => page.page)).toContain("practice");
    expect(rules.rows.map((row) => row.id)).toEqual(["R1"]);
  });
});

describe("R1", () => {
  it("points at Applications, and says what a session is grounded in", () => {
    const evaluation = ask();
    expect(evaluation.rowId).toBe("R1");
    const result = step(evaluation.outcome);
    expect(result.sentence).toBe("Practice lives inside each application.");
    expect(result.action).toEqual({ kind: "link", label: "Go to Applications", to: "/applications" });
    expect([...result.why]).toEqual(["practice questions cite each company's interview process when one was found"]);
    expect(result.secondary ?? null).toBeNull();
  });

  it("claims nothing the page cannot read", () => {
    const result = step(ask().outcome);
    expect(findForbiddenWord([result.sentence, ...result.why].join(" "))).toBeNull();
  });
});

describe("the global rows that run first", () => {
  it("G2: no active profile comes before the pointer", () => {
    const evaluation = ask({ profile: { state: "ready", value: { active: false, importWaiting: false } } });
    expect(evaluation.rowId).toBe("G2");
  });

  it("G3: no usable model key comes before the pointer", () => {
    expect(ask({ credentials: { state: "ready", value: { modelKey: "none" } } }).rowId).toBe("G3");
  });

  it("G1: a failed setup check comes before the pointer", () => {
    expect(ask({ credentials: { state: "failed", call: "GET /credentials", at: "09:14" } }).rowId).toBe("G1");
  });

  it("waits for the profile read rather than lighting a step and replacing it", () => {
    expect(ask({ profile: { state: "loading" } }).outcome).toEqual({ status: "loading" });
  });

  it("is held while the credentials read is still out", () => {
    expect(ask({ credentials: { state: "loading" } }).outcome).toEqual({ status: "loading" });
  });
});
