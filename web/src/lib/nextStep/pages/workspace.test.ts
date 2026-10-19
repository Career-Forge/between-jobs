import { describe, expect, it } from "vitest";
import { PAGES, nextStepWithRow } from "..";
import type { NextStep, NextStepAction, NextStepInput, NextStepOutcome, ProfileFacts, SetupCheck } from "..";
import { WORKSPACE_INTENTS, isWorkspaceFacts, rules, workspaceNotFound, type GenerationFact, type WorkspaceFacts } from "./workspace";

// The workspace's rows as a table: what the page has read, which row decides, and what the band says
// out. The global rows run before these, so the table also pins what they decide on this page. All
// names and numbers are synthetic.

const ACTIVE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: true, importWaiting: false } };
const NO_PROFILE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: false, importWaiting: false } };
const KEY_READY: SetupCheck<{ modelKey: "ready" }> = { state: "ready", value: { modelKey: "ready" } };
const POSTING = "https://jobs.example.com/roles/42";

function facts(generation: GenerationFact, extra: Partial<WorkspaceFacts> = {}): WorkspaceFacts {
  return { status: "saved", appliedOn: null, postingUrl: POSTING, generation, gmail: "unknown", ...extra };
}

function ask(input: Partial<NextStepInput<WorkspaceFacts>>) {
  return nextStepWithRow({ page: "workspace", signedIn: true, profile: ACTIVE, credentials: KEY_READY, ...input } as NextStepInput);
}

function step(outcome: NextStepOutcome): NextStep {
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

function describeAction(action: NextStepAction | null | undefined): string | null {
  if (action === null || action === undefined) return null;
  switch (action.kind) {
    case "button":
      return `button:${action.label}:${action.intent}`;
    case "link":
      return `link:${action.label}:${action.to}`;
    case "external":
      return `external:${action.label}:${action.href}`;
    case "submit":
      return `submit:${action.label}:${action.form}`;
  }
}

describe("the page is registered", () => {
  it("is one of the pages the registry found, with its own row ids", () => {
    expect(PAGES.map((page) => page.page)).toContain("workspace");
    expect(rules.rows.map((row) => row.id)).toEqual(["A0", "A1c", "A1b", "A1g", "A1", "A2", "A2u", "A3", "A4", "A5", "A6", "A7", "A8"]);
  });
});

describe("the table", () => {
  const rows: Array<{
    name: string;
    input: Partial<NextStepInput<WorkspaceFacts>>;
    row: string;
    sentence: string;
    action: string | null;
    why: string[];
    tone?: "failure";
    secondary?: string;
  }> = [
    {
      name: "no resume yet, nothing held back",
      input: { facts: facts({ state: "none" }) },
      row: "A1",
      sentence: "Generate a tailored resume for this posting.",
      action: "button:Generate resume:generate",
      why: ["status saved", "no resume yet", "profile active"],
    },
    {
      name: "a generation request is out",
      input: { facts: facts({ state: "generating" }) },
      row: "A1g",
      sentence: "Generate a tailored resume for this posting.",
      action: "button:Generate resume:generate",
      why: ["status saved", "generating"],
    },
    {
      name: "the generation request failed in this visit",
      input: { facts: facts({ state: "failed", at: "09:14" }) },
      row: "A1c",
      sentence: "Generation didn't finish. You didn't do anything wrong, and nothing was lost.",
      action: "button:Generate again:generate",
      why: ["POST /prepare failed", "09:14"],
      tone: "failure",
    },
    {
      name: "the fit check held the latest attempt back, no earlier resume",
      input: { facts: facts({ state: "held_back", ceiling: 3.6, earlierResume: false }) },
      row: "A1b",
      sentence: "No resume yet: the fit check held this draft back. Read why, then decide -- your call.",
      action: "button:Read the fit check:read-fit",
      secondary: "button:Generate anyway:generate-anyway",
      why: ["latest attempt held back", "ceiling 3.6 / 10"],
    },
    {
      name: "held back while an earlier resume still exists",
      input: { facts: facts({ state: "held_back", ceiling: 4.25, earlierResume: true }) },
      row: "A1b",
      sentence: "The fit check held the latest attempt back. Your earlier resume is still below.",
      action: "button:Read the fit check:read-fit",
      secondary: "button:Generate anyway:generate-anyway",
      why: ["latest attempt held back", "ceiling 4.3 / 10", "earlier resume kept"],
    },
    {
      name: "held back and the fit read has no score",
      input: { facts: facts({ state: "held_back", ceiling: null, earlierResume: false }) },
      row: "A1b",
      sentence: "No resume yet: the fit check held this draft back. Read why, then decide -- your call.",
      action: "button:Read the fit check:read-fit",
      secondary: "button:Generate anyway:generate-anyway",
      why: ["latest attempt held back"],
    },
    {
      name: "a resume with one flagged claim",
      input: { facts: facts({ state: "drafted", flagged: 1, unreadable: false, downloaded: false }) },
      row: "A2",
      sentence: "Read the 1 flagged claim, then download.",
      action: "button:Download PDF:download-pdf",
      why: ["status saved", "resume drafted", "1 claim flagged"],
    },
    {
      name: "a resume with three flagged claims",
      input: { facts: facts({ state: "drafted", flagged: 3, unreadable: false, downloaded: false }) },
      row: "A2",
      sentence: "Read the 3 flagged claims, then download.",
      action: "button:Download PDF:download-pdf",
      why: ["status saved", "resume drafted", "3 claims flagged"],
    },
    {
      name: "a resume with no flagged claims (said as flagged, never as verified)",
      input: { facts: facts({ state: "drafted", flagged: 0, unreadable: false, downloaded: false }) },
      row: "A3",
      sentence: "No claims flagged. Give it one read, then download.",
      action: "button:Download PDF:download-pdf",
      why: ["status saved", "resume drafted", "0 flagged, not the same as verified"],
    },
    {
      name: "a resume from before results were stored: the claim check cannot be counted",
      input: { facts: facts({ state: "drafted", flagged: null, unreadable: false, downloaded: false }) },
      row: "A2u",
      sentence: "Your resume is ready to review. Read it against your own background, then download.",
      action: "button:Download PDF:download-pdf",
      why: ["status saved", "resume drafted", "claim check result not stored"],
    },
    {
      name: "a resume whose stored result could not be read: said as couldn't be read, not as not stored",
      input: { facts: facts({ state: "drafted", flagged: null, unreadable: true, downloaded: false }) },
      row: "A2u",
      sentence: "Your resume is ready to review. Read it against your own background, then download.",
      action: "button:Download PDF:download-pdf",
      why: ["status saved", "resume drafted", "claim check result couldn't be read"],
    },
    {
      name: "the PDF was downloaded in this visit",
      input: { facts: facts({ state: "drafted", flagged: 2, unreadable: false, downloaded: true }) },
      row: "A4",
      sentence: "Submit on the employer's site, then mark it applied.",
      action: `external:Open the posting:${POSTING}`,
      why: ["PDF downloaded this session", "status saved"],
    },
    {
      name: "the PDF was downloaded and the posting has no web address",
      input: { facts: facts({ state: "drafted", flagged: 0, unreadable: false, downloaded: true }, { postingUrl: null }) },
      row: "A4",
      sentence: "Submit on the employer's site, then mark it applied.",
      action: null,
      why: ["PDF downloaded this session", "status saved"],
    },
    {
      name: "applied, Gmail connected",
      input: { facts: facts({ state: "none" }, { status: "applied", appliedOn: "Sep 22", gmail: "connected" }) },
      row: "A5",
      sentence: "Applied Sep 22. Waiting on them.",
      action: null,
      why: ["status applied", "a reply may move this automatically, or appear in Today for you to confirm"],
    },
    {
      name: "applied, Gmail not connected",
      input: { facts: facts({ state: "none" }, { status: "applied", appliedOn: "Sep 22", gmail: "not_connected" }) },
      row: "A5",
      sentence: "Applied Sep 22. Waiting on them.",
      action: null,
      why: ["status applied", "Gmail not connected -- move it yourself when they reply"],
    },
    {
      name: "applied, Gmail connected but it needs reconnecting",
      input: { facts: facts({ state: "none" }, { status: "applied", appliedOn: "Sep 22", gmail: "reconnect_needed" }) },
      row: "A5",
      sentence: "Applied Sep 22. Waiting on them.",
      action: null,
      why: ["status applied", "Gmail needs reconnecting -- move it yourself when they reply"],
    },
    {
      name: "applied, no date came back and Gmail is not known",
      input: { facts: facts({ state: "none" }, { status: "applied" }) },
      row: "A5",
      sentence: "Applied. Waiting on them.",
      action: null,
      why: ["status applied"],
    },
    {
      name: "screening",
      input: { facts: facts({ state: "none" }, { status: "screening" }) },
      row: "A6",
      sentence: "Practice for this interview.",
      action: "link:Go to Practice:#interview-practice",
      why: ["status screening"],
    },
    {
      name: "interviewing",
      input: { facts: facts({ state: "none" }, { status: "interviewing" }) },
      row: "A6",
      sentence: "Practice for this interview.",
      action: "link:Go to Practice:#interview-practice",
      why: ["status interviewing"],
    },
    {
      name: "offer",
      input: { facts: facts({ state: "none" }, { status: "offer" }) },
      row: "A7",
      sentence: "Offer stage. Nothing for us to draft -- your decision.",
      action: null,
      why: ["status offer"],
    },
    {
      name: "rejected",
      input: { facts: facts({ state: "none" }, { status: "rejected" }) },
      row: "A8",
      sentence: "At rest. Move it back any time.",
      action: null,
      why: ["status rejected"],
    },
    {
      name: "withdrawn",
      input: { facts: facts({ state: "none" }, { status: "withdrawn" }) },
      row: "A8",
      sentence: "At rest. Move it back any time.",
      action: null,
      why: ["status withdrawn"],
    },
  ];

  it.each(rows)("$name -> $row", ({ input, row, sentence, action, why, tone, secondary }) => {
    const answer = ask(input);
    expect(answer.rowId).toBe(row);
    const decided = step(answer.outcome);
    expect(decided.sentence).toBe(sentence);
    expect(decided.why).toEqual(why);
    expect(describeAction(decided.action)).toBe(action);
    expect(decided.tone).toBe(tone);
    expect(describeAction(decided.secondary) ?? undefined).toBe(secondary);
  });

  it("lights exactly the steps that carry one cyan action, and leaves the rest unlit", () => {
    const lit = rows.filter((entry) => step(ask(entry.input).outcome).action !== null).map((entry) => `${entry.row}`);
    expect(new Set(lit)).toEqual(new Set(["A1", "A1g", "A1c", "A1b", "A2", "A2u", "A3", "A4", "A6"]));
    const unlit = rows.filter((entry) => step(ask(entry.input).outcome).action === null).map((entry) => entry.row);
    expect(new Set(unlit)).toEqual(new Set(["A4", "A5", "A7", "A8"]));
  });

  it("only ever names a fact in a why line that the page was told: no model, no timestamp, no reviewed state", () => {
    for (const entry of rows) {
      const words = step(ask(entry.input).outcome).why.join(" ");
      expect(words).not.toMatch(/model|reviewed|generated at|ago/i);
    }
  });

  it("never offers a second cyan: the one secondary is an outlined button beside it", () => {
    const withSecondary = rows.filter((entry) => entry.secondary !== undefined);
    expect(withSecondary.map((entry) => entry.row)).toEqual(["A1b", "A1b", "A1b"]);
    for (const entry of withSecondary) expect(step(ask(entry.input).outcome).action).not.toBeNull();
  });
});

describe("waiting for the stored result", () => {
  it("lights nothing while the stored generation is being read on a Saved application", () => {
    const answer = ask({ facts: facts({ state: "reading" }) });
    expect(answer.rowId).toBe("A0");
    expect(answer.outcome).toEqual({ status: "loading" });
  });

  it("does not wait for it once the application has moved past Saved: those rows do not read it", () => {
    const answer = ask({ facts: facts({ state: "reading" }, { status: "applied", appliedOn: "Sep 22" }) });
    expect(answer.rowId).toBe("A5");
  });

  it("holds the band while the profile or the credentials are still being read", () => {
    for (const input of [{ profile: { state: "loading" } as const }, { credentials: { state: "loading" } as const }, { profile: undefined }]) {
      expect(ask({ ...input, facts: facts({ state: "none" }) }).outcome).toEqual({ status: "loading" });
    }
  });
});

describe("what the global rows decide on this page, before any of the above", () => {
  it("sends a person with no active profile to their profile, whatever the status", () => {
    for (const status of ["saved", "applied", "interviewing"]) {
      const answer = ask({ profile: NO_PROFILE, facts: facts({ state: "none" }, { status }) });
      expect(answer.rowId).toBe("G2");
      expect(step(answer.outcome).action).toMatchObject({ kind: "link", to: "/profile" });
    }
  });

  it("asks for a model key before the first step when there is no usable one", () => {
    const answer = ask({ credentials: { state: "ready", value: { modelKey: "none" } }, facts: facts({ state: "none" }) });
    expect(answer.rowId).toBe("G3");
    expect(step(answer.outcome).action).toMatchObject({ kind: "link", label: "Add a model key" });
  });

  it("says the setup check failed when the profile could not be read, with the call and the time", () => {
    const answer = ask({ profile: { state: "failed", call: "GET /profile/current", at: "09:14" }, facts: facts({ state: "none" }) });
    expect(answer.rowId).toBe("G1");
    expect(step(answer.outcome).why).toEqual(["GET /profile/current failed", "09:14"]);
  });

  it("says the page's own read failed with Try again, naming the application call", () => {
    const answer = ask({
      pageFailure: { what: "Couldn't load this application", call: "GET /applications/abc", at: "09:14" },
      facts: undefined,
    });
    expect(answer.rowId).toBe("F1");
    const decided = step(answer.outcome);
    expect(decided.sentence).toBe("Couldn't load this application. You didn't do anything wrong, and nothing was lost.");
    expect(decided.why).toEqual(["GET /applications/abc failed", "09:14"]);
    expect(decided.action).toMatchObject({ kind: "button", label: "Try again", intent: "retry" });
  });
});

describe("an application that was not found", () => {
  it("is a failure step with a way back, and no Try again: asking again cannot help", () => {
    const decided = workspaceNotFound("abc");
    expect(decided.sentence).toBe("This application wasn't found -- it may have been deleted.");
    expect(decided.tone).toBe("failure");
    expect(decided.action).toEqual({ kind: "link", label: "Go to Applications", to: "/applications" });
    expect(decided.why).toEqual(["GET /applications/abc returned not found"]);
  });
});

describe("facts the rows cannot read", () => {
  it.each([
    ["nothing", undefined],
    ["a string", "saved"],
    ["no status", { appliedOn: null, postingUrl: null, generation: { state: "none" }, gmail: "unknown" }],
    ["a date that is not text", { status: "applied", appliedOn: 5, postingUrl: null, generation: { state: "none" }, gmail: "unknown" }],
    ["a generation in a state that does not exist", { status: "saved", appliedOn: null, postingUrl: null, generation: { state: "done" }, gmail: "unknown" }],
    ["a failure with no time", { status: "saved", appliedOn: null, postingUrl: null, generation: { state: "failed" }, gmail: "unknown" }],
    ["a negative flagged count", { status: "saved", appliedOn: null, postingUrl: null, generation: { state: "drafted", flagged: -1, unreadable: false, downloaded: false }, gmail: "unknown" }],
    ["a flagged count that is not whole", { status: "saved", appliedOn: null, postingUrl: null, generation: { state: "drafted", flagged: 1.5, unreadable: false, downloaded: false }, gmail: "unknown" }],
    ["a drafted resume that does not say whether its result was unreadable", { status: "saved", appliedOn: null, postingUrl: null, generation: { state: "drafted", flagged: null, downloaded: false }, gmail: "unknown" }],
    ["a held-back result with a text ceiling", { status: "saved", appliedOn: null, postingUrl: null, generation: { state: "held_back", ceiling: "3.6", earlierResume: false }, gmail: "unknown" }],
    ["a Gmail state that does not exist", { status: "saved", appliedOn: null, postingUrl: null, generation: { state: "none" }, gmail: "maybe" }],
  ])("are refused (%s), and the band then shows nothing rather than a guess", (_name, value) => {
    expect(isWorkspaceFacts(value)).toBe(false);
    expect(ask({ facts: value as WorkspaceFacts }).outcome).toEqual({ status: "none" });
  });

  it("gives an unknown status no step: the band does not make one up", () => {
    expect(ask({ facts: facts({ state: "none" }, { status: "ghosted" }) }).outcome).toEqual({ status: "none" });
  });
});

describe("the intents are the ones the page registers handlers for", () => {
  it("are four, named once", () => {
    expect(WORKSPACE_INTENTS).toEqual({ generate: "generate", generateAnyway: "generate-anyway", downloadPdf: "download-pdf", readFit: "read-fit" });
  });
});
