import { describe, expect, it } from "vitest";
import { PAGES, nextStepWithRow } from "..";
import type { NextStepInput, NextStepOutcome, ProfileFacts, SetupCheck } from "..";
import { isSettingsFacts, rules, type SettingsFacts } from "./settings";

// The Settings page's rows, as a table: what the account holds in, which row decides and what the
// band says out. The global rows (G0..F1) run before these, so the table also pins the cases they
// decide on this page.

const ACTIVE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: true, importWaiting: false } };
const NO_PROFILE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: false, importWaiting: false } };
const CREDS: SetupCheck<Record<string, never>> = { state: "ready", value: {} };
const SEARCH = { connected: 2, total: 7 };

function facts(llm: SettingsFacts["llm"], extra: Partial<SettingsFacts> = {}): SettingsFacts {
  return { llm, search: SEARCH, chosenHere: null, ...extra };
}

function ask(input: Partial<NextStepInput<SettingsFacts>>) {
  return nextStepWithRow({ page: "settings", signedIn: true, profile: ACTIVE, credentials: CREDS, ...input } as NextStepInput);
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
      why: ["OpenAI model chosen this visit", "2 of 7 search keys connected"],
    },
    {
      name: "a model is ready and nothing needs you",
      input: { facts: facts({ state: "ready", names: ["OpenRouter"], defaultKnown: true }) },
      row: "S4",
      sentence: "Your model is set. Everything else here is optional.",
      action: null,
      why: ["OpenRouter ready", "2 of 7 search keys connected"],
    },
    {
      name: "several models are ready and the default is known",
      input: { facts: facts({ state: "ready", names: ["OpenRouter", "OpenAI", "Anthropic"], defaultKnown: true }) },
      row: "S4",
      sentence: "Your model is set. Everything else here is optional.",
      action: null,
      why: ["3 model providers ready", "2 of 7 search keys connected"],
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

  it("sends a person with no active profile to their profile before anything here, and the connect action is then not the page's cyan", () => {
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

describe("facts the page rows cannot read", () => {
  it.each([
    ["nothing", undefined],
    ["a string", "ready"],
    ["no llm", { search: SEARCH, chosenHere: null }],
    ["an unknown llm state", { llm: { state: "maybe" }, search: SEARCH, chosenHere: null }],
    ["a ready llm with no names", { llm: { state: "ready", names: [], defaultKnown: true }, search: SEARCH, chosenHere: null }],
    ["a ready llm that does not say whether the default is known", { llm: { state: "ready", names: ["OpenAI"] }, search: SEARCH, chosenHere: null }],
    ["a waiting key with no name", { llm: { state: "needs_model", providerName: "" }, search: SEARCH, chosenHere: null }],
    ["a negative count", { llm: { state: "none" }, search: { connected: -1, total: 7 }, chosenHere: null }],
    ["a model chosen by number", { llm: { state: "none" }, search: SEARCH, chosenHere: 5 }],
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
