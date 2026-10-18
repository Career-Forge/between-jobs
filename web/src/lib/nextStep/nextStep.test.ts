import { describe, expect, it } from "vitest";
import { REGISTER_PATH } from "../publicRoutes";
import { findForbiddenWord } from "../trail";
import { evaluate, evaluateWithRow } from "./engine";
import { GLOBAL_ROWS } from "./global";
import { nextStep, nextStepWithRow } from "./index";
import { definePage, type NextStep, type NextStepInput, type NextStepRow, type ProfileFacts, type SetupCheck } from "./types";

// One row of the rule table per case: the input a page can read, the row that decides, and what it says.

const ready = <T>(value: T): SetupCheck<T> => ({ state: "ready", value });
const profile = (facts: Partial<ProfileFacts> = {}): SetupCheck<ProfileFacts> =>
  ready({ active: true, importWaiting: false, ...facts });
const credentials = ready({} as Record<string, never>);

// A signed-in page whose setup has been read and is fine; each case overrides what it is about.
function inApp(overrides: Partial<NextStepInput> = {}): NextStepInput {
  return { page: "today", signedIn: true, profile: profile(), credentials, ...overrides };
}

function stepOf(input: NextStepInput): NextStep {
  const outcome = nextStep(input);
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

interface Row {
  name: string;
  id: string;
  input: NextStepInput;
  sentence: string;
  action: NextStep["action"];
  why: string[];
  tone?: "failure";
}

const ROWS: Row[] = [
  {
    name: "G0 signed out, on any app address, signs in",
    id: "G0",
    input: { page: "login", signedIn: false },
    sentence: "Sign in to your job search workspace.",
    action: { kind: "submit", label: "Sign in", form: "auth-form" },
    why: ["you're signed out"],
  },
  {
    name: "G0 signed out, in register mode, creates the account",
    id: "G0",
    input: { page: "login", signedIn: false, authMode: "register" },
    sentence: "Sign in to your job search workspace.",
    action: { kind: "submit", label: "Create account", form: "auth-form" },
    why: ["you're signed out"],
  },
  {
    name: "G0 signed out, on a deep link such as the workspace",
    id: "G0",
    input: { page: "workspace", signedIn: false },
    sentence: "Sign in to your job search workspace.",
    action: { kind: "submit", label: "Sign in", form: "auth-form" },
    why: ["you're signed out"],
  },
  {
    name: "L0 the landing page, signed out",
    id: "L0",
    input: { page: "landing", signedIn: false },
    sentence: "Create an account, or sign in if you have one.",
    action: { kind: "link", label: "Create an account", to: REGISTER_PATH },
    why: ["you're signed out"],
  },
  {
    name: "G1 the profile read failed",
    id: "G1",
    input: inApp({ profile: { state: "failed", call: "GET /profile/current", at: "09:14" } }),
    sentence: "Couldn't check your setup. You didn't do anything wrong, and nothing was lost.",
    action: { kind: "button", label: "Try again", intent: "retry-setup" },
    why: ["GET /profile/current failed", "09:14"],
    tone: "failure",
  },
  {
    name: "G1 the credentials read failed",
    id: "G1",
    input: inApp({ credentials: { state: "failed", call: "GET /credentials", at: "09:14" } }),
    sentence: "Couldn't check your setup. You didn't do anything wrong, and nothing was lost.",
    action: { kind: "button", label: "Try again", intent: "retry-setup" },
    why: ["GET /credentials failed", "09:14"],
    tone: "failure",
  },
  {
    name: "G1 both reads failed, and both are named",
    id: "G1",
    input: inApp({
      profile: { state: "failed", call: "GET /profile/current", at: "09:14" },
      credentials: { state: "failed", call: "GET /credentials", at: "09:15" },
    }),
    sentence: "Couldn't check your setup. You didn't do anything wrong, and nothing was lost.",
    action: { kind: "button", label: "Try again", intent: "retry-setup" },
    why: ["GET /profile/current failed", "GET /credentials failed", "09:14"],
    tone: "failure",
  },
  {
    name: "G1 beats G2 while the other read is still loading",
    id: "G1",
    input: inApp({
      profile: ready({ active: false, importWaiting: true }),
      credentials: { state: "failed", call: "GET /credentials", at: "09:14" },
    }),
    sentence: "Couldn't check your setup. You didn't do anything wrong, and nothing was lost.",
    action: { kind: "button", label: "Try again", intent: "retry-setup" },
    why: ["GET /credentials failed", "09:14"],
    tone: "failure",
  },
  {
    name: "G2b no active profile and an import waiting",
    id: "G2b",
    input: inApp({ profile: ready({ active: false, importWaiting: true }) }),
    sentence: "Your import is waiting. Check every field, then activate.",
    action: { kind: "link", label: "Review the import", to: "/profile#import-review" },
    why: ["import waiting for review", "not active until you confirm"],
  },
  {
    name: "G2 no active profile",
    id: "G2",
    input: inApp({ profile: ready({ active: false, importWaiting: false }) }),
    sentence: "Everything here starts from your profile. Import it first.",
    action: { kind: "link", label: "Set up your profile", to: "/profile" },
    why: ["no active profile"],
  },
  {
    name: "G2 on the profile page itself goes to the import card",
    id: "G2",
    input: inApp({ page: "profile", profile: ready({ active: false, importWaiting: false }) }),
    sentence: "Everything here starts from your profile. Import it first.",
    action: { kind: "link", label: "Set up your profile", to: "/profile#set-up" },
    why: ["no active profile"],
  },
  {
    name: "F1 the page's own data request failed",
    id: "F1",
    input: inApp({ pageFailure: { what: "Couldn't load Today", call: "GET /today", at: "09:14" } }),
    sentence: "Couldn't load Today. You didn't do anything wrong, and nothing was lost.",
    action: { kind: "button", label: "Try again", intent: "retry" },
    why: ["GET /today failed", "09:14"],
    tone: "failure",
  },
  {
    name: "F1 names the handler the page retries with",
    id: "F1",
    input: inApp({ pageFailure: { what: "Couldn't finish the search", call: "GET /discover", at: "21:03", intent: "search-again" } }),
    sentence: "Couldn't finish the search. You didn't do anything wrong, and nothing was lost.",
    action: { kind: "button", label: "Try again", intent: "search-again" },
    why: ["GET /discover failed", "21:03"],
    tone: "failure",
  },
];

describe("the rule table", () => {
  it.each(ROWS.map((row) => [row.name, row] as const))("%s", (_name, row) => {
    const evaluation = nextStepWithRow(row.input);
    expect(evaluation.rowId).toBe(row.id);
    expect(evaluation.outcome).toEqual({
      status: "ready",
      step: {
        sentence: row.sentence,
        action: row.action,
        why: row.why,
        ...(row.tone === undefined ? {} : { tone: row.tone }),
      },
    });
  });

  it("LG gives the legal pages no band, signed in or out", () => {
    for (const page of ["privacy", "terms"]) {
      for (const signedIn of [false, true]) {
        const evaluation = nextStepWithRow(inApp({ page, signedIn }));
        expect(evaluation).toEqual({ outcome: { status: "none" }, rowId: "LG" });
      }
    }
  });

  it("answers `none` for a signed-in landing address and for a page with no rows of its own", () => {
    expect(nextStep({ page: "landing", signedIn: true })).toEqual({ status: "none" });
    expect(nextStep(inApp({ page: "a-page-nobody-registered" }))).toEqual({ status: "none" });
  });

  it("every step names at least one fact and is a real sentence", () => {
    for (const row of ROWS) {
      const step = stepOf(row.input);
      expect(step.why.length).toBeGreaterThan(0);
      expect(step.sentence).toMatch(/[.]$/);
    }
  });
});

describe("loading", () => {
  it("holds a signed-in page until both setup reads have answered, and lights nothing meanwhile", () => {
    const cases: Partial<NextStepInput>[] = [
      { profile: { state: "loading" } },
      { credentials: { state: "loading" } },
      { profile: undefined },
      { credentials: undefined },
      { profile: { state: "loading" }, credentials: { state: "loading" } },
    ];
    for (const overrides of cases) {
      const evaluation = nextStepWithRow({ ...inApp(), ...overrides });
      expect(evaluation.outcome).toEqual({ status: "loading" });
      expect(evaluation.rowId).toBe("SETUP");
    }
  });

  it("does not light G2 while the credentials read could still fail (G1 outranks it)", () => {
    const input = inApp({ profile: ready({ active: false, importWaiting: false }), credentials: { state: "loading" } });
    expect(nextStep(input)).toEqual({ status: "loading" });
  });

  it("a failed read is a step even though the other one is still loading", () => {
    const input = inApp({ profile: { state: "failed", call: "GET /profile/current", at: "09:14" }, credentials: { state: "loading" } });
    expect(nextStepWithRow(input).rowId).toBe("G1");
  });

  it("does not make the signed-out pages wait for reads they never make", () => {
    expect(nextStepWithRow({ page: "landing", signedIn: false }).rowId).toBe("L0");
    expect(nextStepWithRow({ page: "login", signedIn: false }).rowId).toBe("G0");
  });
});

describe("evaluation order", () => {
  const step = (sentence: string): NextStep => ({ sentence, action: null, why: [sentence] });
  const row = (id: string, result: ReturnType<NextStepRow["match"]>): NextStepRow => ({ id, match: () => result });

  it("the first matching row wins", () => {
    const evaluation = evaluateWithRow(inApp(), [], [definePage("today", [row("A", null), row("B", step("second")), row("C", step("third"))])]);
    expect(evaluation.rowId).toBe("B");
    expect(evaluation.outcome).toEqual({ status: "ready", step: step("second") });
  });

  it("global rows come before the page's rows, whatever the page says", () => {
    const evaluation = evaluateWithRow(inApp(), [row("G", step("global"))], [definePage("today", [row("P", step("page"))])]);
    expect(evaluation.rowId).toBe("G");
  });

  it("with the real table, a signed-in page without a profile gets G2 and never its own row", () => {
    const pageRow = row("T1", step("the page's own"));
    const input = inApp({ profile: ready({ active: false, importWaiting: false }) });
    expect(evaluateWithRow(input, GLOBAL_ROWS, [definePage("today", [pageRow])]).rowId).toBe("G2");
    // ... and once the setup is fine, the page's row is reached.
    expect(evaluateWithRow(inApp(), GLOBAL_ROWS, [definePage("today", [pageRow])]).rowId).toBe("T1");
  });

  it("only the asking page's rows are consulted", () => {
    const asked: string[] = [];
    const spy = (id: string): NextStepRow => ({ id, match: () => (asked.push(id), null) });
    evaluate(inApp({ page: "discover" }), [], [definePage("today", [spy("today-row")]), definePage("discover", [spy("discover-row")])]);
    expect(asked).toEqual(["discover-row"]);
  });

  it("a row that has to wait stops the search: no later row is tried", () => {
    const asked: string[] = [];
    const spy = (id: string): NextStepRow => ({ id, match: () => (asked.push(id), step(id)) });
    const outcome = evaluate(inApp(), [row("W", "wait")], [definePage("today", [spy("page")])]);
    expect(outcome).toEqual({ status: "loading" });
    expect(asked).toEqual([]);
  });

  it("a row can say the page has no step, and the answer is `none`", () => {
    expect(evaluate(inApp(), [row("N", "none")], [])).toEqual({ status: "none" });
  });

  it("no row applying is `none`, never an invented step", () => {
    expect(evaluate(inApp(), [row("A", null)], [])).toEqual({ status: "none" });
  });

  it("is a pure function of its input: the same input gives the same answer, and the input is not changed", () => {
    const input = inApp({ profile: ready({ active: false, importWaiting: true }) });
    const before = JSON.stringify(input);
    expect(nextStep(input)).toEqual(nextStep(input));
    expect(JSON.stringify(input)).toBe(before);
  });
});

describe("what a step is allowed to say", () => {
  // Each free-text field of the input is a unique token. Whatever the step says that is not static
  // copy must be one of them: with the tokens removed, no digit, time or request is left.
  const TOKENS = { call: "CALL-1", at: "AT-1", what: "WHAT-1", intent: "intent-1" };
  const inputs: NextStepInput[] = [
    inApp({ profile: { state: "failed", call: TOKENS.call, at: TOKENS.at } }),
    inApp({ credentials: { state: "failed", call: TOKENS.call, at: TOKENS.at } }),
    inApp({ pageFailure: { what: TOKENS.what, call: TOKENS.call, at: TOKENS.at, intent: TOKENS.intent } }),
    inApp({ profile: ready({ active: false, importWaiting: true }) }),
    inApp({ profile: ready({ active: false, importWaiting: false }) }),
    inApp({ page: "profile", profile: ready({ active: false, importWaiting: false }) }),
    { page: "login", signedIn: false },
    { page: "login", signedIn: false, authMode: "register" },
    { page: "landing", signedIn: false },
  ];

  function wordsOf(step: NextStep): string {
    const actions = [step.action, step.secondary ?? null].flatMap((action) => (action === null ? [] : [action.label]));
    return [step.sentence, ...step.why, ...actions].join(" ");
  }

  it.each(inputs.map((input, index) => [`input ${index + 1} (${input.page})`, input] as const))(
    "%s: nothing in the words comes from anywhere but the input",
    (_name, input) => {
      let words = wordsOf(stepOf(input));
      for (const token of Object.values(TOKENS)) words = words.split(token).join("");
      expect(words).not.toMatch(/\d/);
      expect(words).not.toMatch(/\b(GET|POST|PUT|PATCH|DELETE)\b/);
      expect(words.toLowerCase()).not.toMatch(/verified|unknown model|openrouter|gmail/);
    },
  );

  it("no sentence, why line, button or hint says `verified` or `unknown model`", () => {
    for (const input of inputs) {
      const step = stepOf(input);
      const strings = [wordsOf(step), step.locked?.hint ?? ""];
      for (const string of strings) expect(findForbiddenWord(string)).toBeNull();
    }
    for (const row of ROWS) expect(findForbiddenWord(wordsOf(stepOf(row.input)))).toBeNull();
  });

  it("a signed-out visitor is told one thing, and never anything about an account, profile or key", () => {
    for (const page of ["login", "landing", "workspace"]) {
      const step = stepOf({ page, signedIn: false });
      expect(step.why).toEqual(["you're signed out"]);
      expect(wordsOf(step).toLowerCase()).not.toMatch(/profile|key|model/);
    }
  });

  it("when the failing call and time are supplied they are used as given", () => {
    const step = stepOf(inApp({ pageFailure: { what: "Couldn't load Today", call: "GET /today", at: "09:14" } }));
    expect(step.why).toEqual(["GET /today failed", "09:14"]);
  });
});

describe("the shape of a step", () => {
  it("cannot carry a lock or a secondary without an action to hang them on", () => {
    // Both would be dropped silently by the band, so the types refuse them (tsc checks this file).
    // @ts-expect-error a locked step needs an action
    const locked: NextStep = { sentence: "s.", action: null, why: ["w"], locked: { hint: "h" } };
    // @ts-expect-error a secondary needs a cyan action beside it
    const secondary: NextStep = { sentence: "s.", action: null, why: ["w"], secondary: { kind: "button", label: "L", intent: "i" } };
    expect([locked.action, secondary.action]).toEqual([null, null]);
  });
});
