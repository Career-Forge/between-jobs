import { describe, expect, it } from "vitest";
import { PAGES, nextStepWithRow } from "..";
import type { NextStepInput, NextStepOutcome, ProfileFacts, SetupCheck } from "..";
import { isSettingsFacts, rules, settingsOutcome, type SettingsFacts } from "./settings";

// The Settings page's rows, as a table: what the account holds in, which row decides and what the
// band says out. The global rows (G0..F1) run before these, so the table also pins the cases they
// decide on this page.

const ACTIVE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: true, importWaiting: false } };
const NO_PROFILE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: false, importWaiting: false } };
const CREDS: SetupCheck<Record<string, never>> = { state: "ready", value: {} };

function facts(llm: SettingsFacts["llm"], extra: Partial<SettingsFacts> = {}): SettingsFacts {
  return { llm, chosenHere: null, ...extra };
}

function inputOf(input: Partial<NextStepInput<SettingsFacts>>): NextStepInput<SettingsFacts> {
  return { page: "settings", signedIn: true, profile: ACTIVE, credentials: CREDS, ...input } as NextStepInput<SettingsFacts>;
}

function ask(input: Partial<NextStepInput<SettingsFacts>>) {
  return nextStepWithRow(inputOf(input));
}

// What the page shows: the engine's answer, then settingsOutcome's say on it (as Settings.tsx does).
function shown(input: Partial<NextStepInput<SettingsFacts>>) {
  const full = inputOf(input);
  const evaluation = nextStepWithRow(full);
  return { evaluation, outcome: settingsOutcome(evaluation, full) };
}

function step(outcome: NextStepOutcome) {
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

describe("the page is registered", () => {
  it("is one of the pages the registry found, with its own row ids", () => {
    expect(PAGES.map((page) => page.page)).toContain("settings");
    expect(rules.rows.map((row) => row.id)).toEqual(["S1", "S2", "S2k", "S3", "S4u", "S4"]);
  });
});

describe("the table", () => {
  const rows: Array<{
    name: string;
    input: Partial<NextStepInput<SettingsFacts>>;
    row: string;
    sentence: string;
    action: string | null;
    why: string[];
  }> = [
    {
      name: "no model key saved",
      input: { facts: facts({ state: "none" }) },
      row: "S1",
      sentence: "Add a model key so Between Jobs can draft and score.",
      action: "button:Connect with OpenRouter:connect-openrouter",
      why: ["profile active", "no model key saved"],
    },
    {
      name: "a key is checked and has no model",
      input: { facts: facts({ state: "needs_model", providerName: "OpenAI" }) },
      row: "S2",
      sentence: "Your OpenAI key is saved. Choose a model to finish.",
      action: "button:Choose a model:choose-model",
      why: ["OpenAI key checked", "no model chosen"],
    },
    {
      name: "a key is not confirmed",
      input: { facts: facts({ state: "needs_key", providerName: "Anthropic" }) },
      row: "S2k",
      sentence: "Your Anthropic key isn't confirmed. Replace it to finish.",
      action: "button:Replace the key:replace-key",
      why: ["Anthropic key not confirmed"],
    },
    {
      name: "a model was chosen just now",
      input: { facts: facts({ state: "ready", names: ["OpenAI"], defaultKnown: true }, { chosenHere: "OpenAI" }) },
      row: "S3",
      sentence: "Your model is set. Search for a role next.",
      action: "link:Go to Discover:/discover",
      why: ["OpenAI model chosen this visit"],
    },
    {
      name: "a model is ready and nothing needs you",
      input: { facts: facts({ state: "ready", names: ["OpenRouter"], defaultKnown: true }) },
      row: "S4",
      sentence: "Your model is set. Everything else here is optional.",
      action: null,
      why: ["OpenRouter ready"],
    },
    {
      name: "several models are ready and the default is known",
      input: { facts: facts({ state: "ready", names: ["OpenRouter", "OpenAI", "Anthropic"], defaultKnown: true }) },
      row: "S4",
      sentence: "Your model is set. Everything else here is optional.",
      action: null,
      why: ["3 model providers ready"],
    },
    {
      name: "several models are ready and the page cannot tell which is the default",
      input: { facts: facts({ state: "ready", names: ["OpenRouter", "OpenAI", "Anthropic"], defaultKnown: false }) },
      row: "S4u",
      sentence: "You have 3 connected models, and this page can't tell which one is your default. Choose one below.",
      action: null,
      why: ["3 model providers ready", "the API can't say which model is the default"],
    },
    {
      name: "one model is ready and the page cannot tell whether it is the default",
      input: { facts: facts({ state: "ready", names: ["Anthropic"], defaultKnown: false }) },
      row: "S4u",
      sentence: "Your Anthropic key is ready, and this page can't tell whether it is your default model. Choose it below to make it the default.",
      action: null,
      why: ["Anthropic ready", "the API can't say which model is the default"],
    },
  ];

  it.each(rows)("$name -> $row", ({ input, row, sentence, action, why }) => {
    const answer = ask(input);
    expect(answer.rowId).toBe(row);
    const decided = step(answer.outcome);
    expect(decided.sentence).toBe(sentence);
    expect(decided.why).toEqual(why);
    if (action === null) {
      expect(decided.action).toBeNull();
    } else {
      const [kind, label, target] = action.split(":");
      expect(decided.action).toMatchObject(kind === "link" ? { kind, label, to: target } : { kind, label, intent: target });
    }
  });

  it("is lit by exactly one cyan action in every row but the last two, and unlit in S4 and S4u", () => {
    const lit = rows.filter((entry) => step(ask(entry.input).outcome).action !== null).map((entry) => entry.row);
    expect(lit).toEqual(["S1", "S2", "S2k", "S3"]);
  });

  it("leaves the 'profile active' fact out when the page was not told so, rather than assume it", () => {
    const answer = ask({ facts: facts({ state: "none" }), profile: undefined });
    // with no profile read the global row waits; the page row is never asked
    expect(answer.outcome.status).toBe("loading");
  });
});

describe("what the global rows decide on this page", () => {
  it("waits, and lights nothing, while the profile or the credentials are still being read", () => {
    for (const input of [{ profile: { state: "loading" } as const }, { credentials: { state: "loading" } as const }, { profile: undefined }]) {
      const answer = ask({ ...input, facts: facts({ state: "none" }) });
      expect(answer.outcome).toEqual({ status: "loading" });
    }
  });

  it("answers G2, a link to the profile, before any row here when no profile is active: the engine's own fact, which the page overrides only through settingsOutcome", () => {
    const answer = ask({ profile: NO_PROFILE, facts: facts({ state: "none" }) });
    expect(answer.rowId).toBe("G2");
    expect(step(answer.outcome).action).toMatchObject({ kind: "link", to: "/profile" });
  });

  it("says the setup check failed when the credentials could not be read, with the call and the time", () => {
    const answer = ask({ credentials: { state: "failed", call: "GET /credentials", at: "09:14" } });
    expect(answer.rowId).toBe("G1");
    const decided = step(answer.outcome);
    expect(decided.tone).toBe("failure");
    expect(decided.why).toEqual(["GET /credentials failed", "09:14"]);
    expect(decided.action).toMatchObject({ kind: "button", label: "Try again", intent: "retry-setup" });
  });

  it("says the same when the profile could not be read", () => {
    const answer = ask({ profile: { state: "failed", call: "GET /profile/current", at: "09:15" } });
    expect(answer.rowId).toBe("G1");
    expect(step(answer.outcome).why).toEqual(["GET /profile/current failed", "09:15"]);
  });
});

describe("settingsOutcome: no active profile, and the key decides what the band asks for", () => {
  // Rows S1, S2 and S2k in place of G2, with their own words (the same objects the table above runs).
  const replaced: Array<{ name: string; llm: SettingsFacts["llm"]; row: string; sentence: string; action: string; why: string[] }> = [
    {
      name: "no model key saved",
      llm: { state: "none" },
      row: "S1",
      sentence: "Add a model key so Between Jobs can draft and score.",
      action: "button:Connect with OpenRouter:connect-openrouter",
      why: ["no model key saved"],
    },
    {
      name: "a key is checked and has no model",
      llm: { state: "needs_model", providerName: "OpenAI" },
      row: "S2",
      sentence: "Your OpenAI key is saved. Choose a model to finish.",
      action: "button:Choose a model:choose-model",
      why: ["OpenAI key checked", "no model chosen"],
    },
    {
      name: "a key is not confirmed",
      llm: { state: "needs_key", providerName: "Anthropic" },
      row: "S2k",
      sentence: "Your Anthropic key isn't confirmed. Replace it to finish.",
      action: "button:Replace the key:replace-key",
      why: ["Anthropic key not confirmed"],
    },
  ];

  it.each(replaced)("puts the key's own step in G2's place for $name -> $row", ({ llm, sentence, action, why }) => {
    const { evaluation, outcome } = shown({ profile: NO_PROFILE, facts: facts(llm) });
    expect(evaluation.rowId).toBe("G2");
    const decided = step(outcome);
    expect(decided.sentence).toBe(sentence);
    expect(decided.why).toEqual(why);
    const [kind, label, intent] = action.split(":");
    expect(decided.action).toEqual({ kind, label, intent });
    expect(decided.tone).toBeUndefined();
  });

  it.each(replaced)("uses the words the page's own row uses with a profile, for $name: one sentence and one button, never a second version", ({ llm }) => {
    const { outcome } = shown({ profile: NO_PROFILE, facts: facts(llm) });
    const withProfile = step(ask({ profile: ACTIVE, facts: facts(llm) }).outcome);
    expect(step(outcome).sentence).toBe(withProfile.sentence);
    expect(step(outcome).action).toEqual(withProfile.action);
  });

  it("does not claim a profile is active when none is: S1 leaves that fact out", () => {
    const { outcome } = shown({ profile: NO_PROFILE, facts: facts({ state: "none" }) });
    expect(step(outcome).why).not.toContain("profile active");
  });

  it.each([
    ["one provider, default known", { state: "ready", names: ["OpenRouter"], defaultKnown: true }],
    ["one provider, default not known", { state: "ready", names: ["OpenRouter"], defaultKnown: false }],
    ["several providers", { state: "ready", names: ["OpenRouter", "OpenAI"], defaultKnown: false }],
  ] as const)("leaves G2, the link to the profile, in place when a usable key is saved (%s)", (_name, llm) => {
    const { evaluation, outcome } = shown({ profile: NO_PROFILE, facts: facts(llm) });
    expect(evaluation.rowId).toBe("G2");
    expect(outcome).toBe(evaluation.outcome);
    expect(step(outcome).action).toMatchObject({ kind: "link", label: "Set up your profile", to: "/profile" });
  });

  it("returns the engine's answer untouched when a profile is active, whatever the key", () => {
    for (const llm of [{ state: "none" }, { state: "needs_model", providerName: "OpenAI" }, { state: "ready", names: ["OpenAI"], defaultKnown: true }] as const) {
      const { evaluation, outcome } = shown({ profile: ACTIVE, facts: facts(llm) });
      expect(evaluation.rowId).not.toBe("G2");
      expect(outcome).toBe(evaluation.outcome);
    }
  });

  it("returns the engine's answer untouched while the profile or the keys are loading or failed to read", () => {
    for (const input of [
      { profile: { state: "loading" } as const },
      { credentials: { state: "loading" } as const },
      { profile: { state: "failed", call: "GET /profile/current", at: "09:15" } as const },
      { credentials: { state: "failed", call: "GET /credentials", at: "09:14" } as const },
    ]) {
      const { evaluation, outcome } = shown({ ...input, facts: facts({ state: "none" }) });
      expect(evaluation.rowId).not.toBe("G2");
      expect(outcome).toBe(evaluation.outcome);
    }
  });

  it("returns G2 untouched when the page has no facts, or facts its rows cannot read", () => {
    for (const value of [undefined, { chosenHere: null }, { llm: { state: "maybe" }, chosenHere: null }] as const) {
      const { evaluation, outcome } = shown({ profile: NO_PROFILE, facts: value as SettingsFacts | undefined });
      expect(evaluation.rowId).toBe("G2");
      expect(outcome).toBe(evaluation.outcome);
    }
  });

  it("replaces only G2: the same answer given under another row id is returned as it came", () => {
    const evaluation = ask({ profile: NO_PROFILE, facts: facts({ state: "none" }) });
    const input = inputOf({ profile: NO_PROFILE, facts: facts({ state: "none" }) });
    expect(settingsOutcome({ ...evaluation, rowId: "G2b" }, input)).toBe(evaluation.outcome);
    expect(settingsOutcome({ ...evaluation, rowId: null }, input)).toBe(evaluation.outcome);
  });
});

describe("search keys", () => {
  it("are not counted in any line the band says: the page holds no fact about them", () => {
    const answer = ask({ facts: facts({ state: "ready", names: ["OpenRouter"], defaultKnown: true }) });
    expect(JSON.stringify(step(answer.outcome))).not.toMatch(/search key|of 7|connected/);
    expect("search" in facts({ state: "none" })).toBe(false);
  });
});

describe("facts the page rows cannot read", () => {
  it.each([
    ["nothing", undefined],
    ["a string", "ready"],
    ["no llm", { chosenHere: null }],
    ["an unknown llm state", { llm: { state: "maybe" }, chosenHere: null }],
    ["a ready llm with no names", { llm: { state: "ready", names: [], defaultKnown: true }, chosenHere: null }],
    ["a ready llm that does not say whether the default is known", { llm: { state: "ready", names: ["OpenAI"] }, chosenHere: null }],
    ["a waiting key with no name", { llm: { state: "needs_model", providerName: "" }, chosenHere: null }],
    ["a model chosen by number", { llm: { state: "none" }, chosenHere: 5 }],
  ])("are refused (%s), and the band then shows nothing rather than a guess", (_name, value) => {
    expect(isSettingsFacts(value)).toBe(false);
    const answer = ask({ facts: value as SettingsFacts });
    expect(answer.outcome).toEqual({ status: "none" });
  });
});

describe("the one cyan decision", () => {
  it("is documented where the rows are: the band's action is the page's primary, every other button is outlined", async () => {
    const raw = (await import("./settings.ts?raw")).default as string;
    const source = raw.replace(/\n\/\/ ?/g, " ").replace(/\s+/g, " ");
    expect(source).toContain("WHICH CONTROL IS THE PAGE'S PRIMARY");
    expect(source).toContain('the cyan button IS "Connect with OpenRouter"');
    expect(source).toContain("every other control on the page are outlined");
  });
});
