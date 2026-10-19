// The Settings page's rows. They run after the global ones, so by the time one is asked the profile
// has been read and is active (the global G2 and G2b answer first when it is not), the credentials
// read has answered, and the page is not in a whole-page failure. Every sentence is chosen by code,
// and every fact in a `why` line is a field the page read.
//
//   S1  no model key at all        "Add a model key ..."        Connect with OpenRouter (the page's one cyan)
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
// WHICH CONTROL IS THE PAGE'S PRIMARY. The band holds the only cyan fill, and where its action also
// exists in the page's body the body copy is drawn as an ordinary outlined button (one action, one
// cyan, once). So in S1 the cyan button IS "Connect with OpenRouter": it starts the same sign-in the
// outlined button inside the AI-provider section does. That button, "Validate and save", and every
// other control on the page are outlined; they are never promoted to cyan because a person happens to
// be typing in them. In S2 the band's action opens the model picker, and "Use this model" is outlined.
// The band never sends a person to a particular provider unless OpenRouter is the answer: it is the
// one provider with a one-click connection, and the page offers the others one chip below it.
//
// S3 is session-only: "chosen just now" is known only in the visit that chose the model, like the
// workspace's "PDF downloaded this session". After a reload the page shows S4.

import { MODEL_CHOICE_LABEL } from "../../modelKey";
import { definePage, type NextStepRow } from "../types";

export type SettingsLlm =
  | { state: "none" }
  | { state: "needs_model" | "needs_key"; providerName: string }
  | { state: "ready"; names: readonly string[]; defaultKnown: boolean };

export interface SettingsFacts {
  llm: SettingsLlm;
  search: { connected: number; total: number };
  /** The name of the provider whose model was chosen on this visit, or null. */
  chosenHere: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

export function isSettingsFacts(value: unknown): value is SettingsFacts {
  if (!isRecord(value) || !isRecord(value.llm) || !isRecord(value.search)) return false;
  if (!isCount(value.search.connected) || !isCount(value.search.total)) return false;
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
      why: [`${facts.chosenHere} model chosen this visit`, `${facts.search.connected} of ${facts.search.total} search keys connected`],
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
      why: [readyWhy(facts.llm.names), `${facts.search.connected} of ${facts.search.total} search keys connected`],
    };
  },
};

export const rules = definePage("settings", [S1, S2, S2K, S3, S4U, S4], { facts: isSettingsFacts });
