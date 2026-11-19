// The Settings page's rows. They run after the global ones, so by the time one is asked the profile
// has been read and is active (the global G2 and G2b answer first when it is not; `settingsOutcome`
// below is the one place this page overrides that), the credentials read has answered, and the page
// is not in a whole-page failure. Every sentence is chosen by code, and every fact in a `why` line is
// a field the page read.
//
//   S1  no model key at all        "Add a model key ..."        Connect with OpenRouter (the page's one gold)
//   S2  a key is checked, no model "Your X key is saved ..."    Choose a model
//   S2k a key is not confirmed     "Your X key isn't ..."       Replace the key
//   S3  a model was chosen just now "Your model is set ..."     Go to Discover
//   S4  a model is ready           "... Everything else here is optional."   (unlit)
//   S4u models are ready, the default is not known   "... can't tell which is your default"   (unlit)
//
// The API has no route that reads the default model back, so "your model is set" is said only where
// the page can know which model that is (`defaultKnown`, from knownDefaultProvider in
// lib/settings/llmProviders.ts, the same answer the Default model card gives). Where it cannot, S4u
// says so, in the card's words, instead of the page contradicting itself.
//
// WHICH CONTROL IS THE PAGE'S PRIMARY. The band holds the only gold fill, and where its action also
// exists in the page's body the body copy is drawn as an ordinary outlined button (one action, one
// gold, once). So in S1 the gold button IS "Connect with OpenRouter": it starts the same sign-in the
// outlined button inside the AI-provider section does. That button, "Validate and save", and every
// other control on the page are outlined; they are never promoted to gold because a person happens to
// be typing in them. In S2 the band's action opens the model picker, and "Use this model" is outlined.
// The band never sends a person to a particular provider unless OpenRouter is the answer: it is the
// one provider with a one-click connection, and the page offers the others one chip below it.
//
// The optional search and data keys are not part of this page's next step and are not counted in any
// `why` line: nothing waits on them, and a count of them read as a setup score.
//
// S3 is session-only: "chosen just now" is known only in the visit that chose the model, like the
// workspace's "PDF downloaded this session". After a reload the page shows S4.
//
// WHEN THERE IS NO ACTIVE PROFILE YET. The global row G2 answers before the rows above and sends a
// person to the Profile page to import one. Reading a resume file there uses the person's own model,
// so that page's file card asks for the same key this page adds, and a person with neither would be
// sent back and forth between this band and that card's key notice, with no key added at the end of it.
// (Pasting the profile as JSON there needs no key, but it is not the step the band points at.)
// `settingsOutcome` breaks that from this side: when G2 decided and the key is not usable (none,
// needs_model or needs_key) it returns the first of S1, S2 and S2k that applies, the very row objects
// above, so the words for each state are the same wherever they are said. When the key is usable, G2
// stands and the band sends the person on to their profile.

import { MODEL_CHOICE_LABEL } from "../../modelKey";
import { definePage, type NextStepInput, type NextStepOutcome, type NextStepRow } from "../types";

export type SettingsLlm =
  | { state: "none" }
  | { state: "needs_model" | "needs_key"; providerName: string }
  | { state: "ready"; names: readonly string[]; defaultKnown: boolean };

export interface SettingsFacts {
  llm: SettingsLlm;
  /** The name of the provider whose model was chosen on this visit, or null. */
  chosenHere: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isSettingsFacts(value: unknown): value is SettingsFacts {
  if (!isRecord(value) || !isRecord(value.llm)) return false;
  if (value.chosenHere !== null && typeof value.chosenHere !== "string") return false;
  const llm = value.llm;
  switch (llm.state) {
    case "none":
      return true;
    case "needs_model":
    case "needs_key":
      return typeof llm.providerName === "string" && llm.providerName !== "";
    case "ready":
      return (
        Array.isArray(llm.names) &&
        llm.names.length > 0 &&
        llm.names.every((name) => typeof name === "string" && name !== "") &&
        typeof llm.defaultKnown === "boolean"
      );
    default:
      return false;
  }
}

function profileActive(input: { profile?: { state: string; value?: { active: boolean } } }): boolean {
  return input.profile?.state === "ready" && input.profile.value?.active === true;
}

const S1: NextStepRow<SettingsFacts> = {
  id: "S1",
  match: (input) => {
    if (input.facts?.llm.state !== "none") return null;
    return {
      sentence: "Add a model key so Between Jobs can draft and score.",
      action: { kind: "button", label: "Connect with OpenRouter", intent: "connect-openrouter" },
      why: [...(profileActive(input) ? ["profile active"] : []), "no model key saved"],
    };
  },
};

const S2: NextStepRow<SettingsFacts> = {
  id: "S2",
  match: (input) => {
    const llm = input.facts?.llm;
    if (llm?.state !== "needs_model") return null;
    return {
      sentence: `Your ${llm.providerName} key is saved. Choose a model to finish.`,
      action: { kind: "button", label: MODEL_CHOICE_LABEL, intent: "choose-model" },
      why: [`${llm.providerName} key checked`, "no model chosen"],
    };
  },
};

const S2K: NextStepRow<SettingsFacts> = {
  id: "S2k",
  match: (input) => {
    const llm = input.facts?.llm;
    if (llm?.state !== "needs_key") return null;
    return {
      sentence: `Your ${llm.providerName} key isn't confirmed. Replace it to finish.`,
      action: { kind: "button", label: "Replace the key", intent: "replace-key" },
      why: [`${llm.providerName} key not confirmed`],
    };
  },
};

function readyWhy(names: readonly string[]): string {
  return names.length === 1 ? `${names[0]} ready` : `${names.length} model providers ready`;
}

const S3: NextStepRow<SettingsFacts> = {
  id: "S3",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.llm.state !== "ready" || facts.chosenHere === null) return null;
    return {
      sentence: "Your model is set. Search for a role next.",
      action: { kind: "link", label: "Go to Discover", to: "/discover" },
      why: [`${facts.chosenHere} model chosen this visit`],
    };
  },
};

const S4U: NextStepRow<SettingsFacts> = {
  id: "S4u",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.llm.state !== "ready" || facts.llm.defaultKnown) return null;
    const names = facts.llm.names;
    return {
      sentence:
        names.length === 1
          ? `Your ${names[0]} key is ready, and this page can't tell whether it is your default model. Choose it below to make it the default.`
          : `You have ${names.length} connected models, and this page can't tell which one is your default. Choose one below.`,
      action: null,
      why: [readyWhy(names), "the API can't say which model is the default"],
    };
  },
};

const S4: NextStepRow<SettingsFacts> = {
  id: "S4",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.llm.state !== "ready" || !facts.llm.defaultKnown) return null;
    return {
      sentence: "Your model is set. Everything else here is optional.",
      action: null,
      why: [readyWhy(facts.llm.names)],
    };
  },
};

export const rules = definePage("settings", [S1, S2, S2K, S3, S4U, S4], { facts: isSettingsFacts });

// What the page shows for the answer the engine gave, given the row that decided it. Only G2 with a key
// that is not usable is replaced; every other answer, and an input whose facts cannot be read, is
// returned as it came. "Not usable" needs no check of its own: S1, S2 and S2k each match one of the
// three unusable readings and nothing else, so with a usable key none of them answers and G2 stands.
export function settingsOutcome(
  evaluation: { outcome: NextStepOutcome; rowId: string | null },
  input: NextStepInput<SettingsFacts>,
): NextStepOutcome {
  const { outcome, rowId } = evaluation;
  if (rowId !== "G2" || !isSettingsFacts(input.facts)) return outcome;
  for (const row of [S1, S2, S2K]) {
    const result = row.match(input);
    if (result !== null && result !== "wait" && result !== "none") return { status: "ready", step: result };
  }
  return outcome;
}
