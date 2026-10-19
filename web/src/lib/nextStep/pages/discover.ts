// Discover's rows: what the band says on the job-search page. They run after the global rows, so by
// the time one is asked the profile has been read and is active (G2 answers first when it is not),
// the credentials read has answered, and a failed search is not in play (F1 answers for it).
//
//   D1   setup is missing: no usable model key (read from the credentials, or the server said so)
//   D2   ready, no search run yet           "Search company job boards ..."     Search (the page's one cyan)
//   D3   searching                          "Searching..."                      (no button)
//   D4b  results, the scoring fell back     "Your model's reply couldn't be read ..."   Search again
//   D4   results                            "Track the jobs worth a draft."     (unlit)
//   D5   results, nothing                    "No results. ..."                   Clear filters, or unlit with no filter to clear
//
// Discover scores with the person's own model, so the model key is read here and not by the global
// G3 row: the page does not hand G3 a `modelKey`, and D1 says the same thing in Discover's words,
// the way Settings has its own rows for the same states.
//
// A search never runs on its own. D2 is "ready, nothing run", and its button submits the filter
// form from outside (the form's own Search is drawn as an outlined button, because the band holds
// the page's one cyan fill). The elapsed counter of D3 is not part of the sentence: the sentence
// would change every second and be announced every second. The page draws the counter beside it.
//
// Every number in a why line is a count of what the response held. A provider that was not asked
// (no key) leaves no warning, so none is named; a provider that ran and failed is named by the id
// the server put in front of its message.

import { MODEL_CHOICE_LABEL, type ModelKeyReading } from "../../modelKey";
import { MODEL_PROVIDER_PATH } from "../../settingsPaths";
import { definePage, type NextStep, type NextStepRow } from "../types";

// The id the filter form carries, so the band's button can submit it from outside.
export const DISCOVER_FORM_ID = "discover-form";

export type DiscoverPhase = "idle" | "searching" | "results" | "setup";

export interface ProviderCount {
  provider: string;
  count: number;
}

export interface DiscoverResultsFact {
  /** Rows the model scored, and rows past the scoring batch (shown unscored). */
  scored: number;
  more: number;
  /** Live-search rows the link check dropped. */
  deadRemoved: number;
  /** Rows per `provider`, most first. */
  providers: readonly ProviderCount[];
  /** Providers that ran and failed, by the id in front of their warning. */
  failedProviders: readonly string[];
  /** Warnings that name no provider (the registry could not be searched, and so on). */
  otherNotes: number;
  /** The scoring model's reply could not be read, so every score is a placeholder. */
  fallback: boolean;
  /** Filter boxes (search, location, companies, remote only) that held something for this search. */
  filtersSet: number;
  /** The companies filter was one of them. */
  companiesSet: boolean;
}

export interface DiscoverFacts {
  phase: DiscoverPhase;
  /** What the credentials read says about the model keys; null when it could not be told. */
  key: ModelKeyReading | null;
  /** Search providers with a saved key; null when the credentials were not read. */
  searchProviders: number | null;
  /** Present while the page shows a finished search (phase "results"). */
  results: DiscoverResultsFact | null;
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isResultsFact(value: unknown): value is DiscoverResultsFact {
  if (!isRecord(value)) return false;
  return (
    isCount(value.scored) &&
    isCount(value.more) &&
    isCount(value.deadRemoved) &&
    isCount(value.otherNotes) &&
    isCount(value.filtersSet) &&
    typeof value.fallback === "boolean" &&
    typeof value.companiesSet === "boolean" &&
    Array.isArray(value.providers) &&
    value.providers.every((entry) => isRecord(entry) && typeof entry.provider === "string" && isCount(entry.count)) &&
    Array.isArray(value.failedProviders) &&
    value.failedProviders.every((entry) => typeof entry === "string")
  );
}

export function isDiscoverFacts(value: unknown): value is DiscoverFacts {
  if (!isRecord(value)) return false;
  if (!["idle", "searching", "results", "setup"].includes(String(value.phase))) return false;
  if (value.key !== null && !["ready", "needs_model", "needs_key", "none"].includes(String(value.key))) return false;
  if (value.searchProviders !== null && !isCount(value.searchProviders)) return false;
  return value.results === null || isResultsFact(value.results);
}

function profileActive(input: { profile?: { state: string; value?: { active: boolean } } }): boolean {
  return input.profile?.state === "ready" && input.profile.value?.active === true;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

// ── D1 ─────────────────────────────────────────────────────────────────────

const NO_KEY = {
  none: {
    sentence: "Discover scores jobs with your model. Add a model key first.",
    label: "Add a model key",
    why: "no model key saved",
  },
  needs_model: {
    sentence: "Discover scores jobs with your model. Your key is saved -- choose a model first.",
    label: MODEL_CHOICE_LABEL,
    why: "a key is saved, no model chosen",
  },
  needs_key: {
    sentence: "Discover scores jobs with your model. Add a model key first.",
    label: "Add a model key",
    why: "a key is saved, not confirmed",
  },
} as const;

const D1: NextStepRow<DiscoverFacts> = {
  id: "D1",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined) return null;
    // The server's own answer (a 409 on the search) outranks what the credentials said: it is what
    // actually refused. Otherwise only a page that has not searched yet is held at setup, so a
    // finished search is never replaced by a step that is out of date.
    const serverSaid = facts.phase === "setup";
    const readSaid = facts.phase === "idle" && facts.key !== null && facts.key !== "ready";
    if (!serverSaid && !readSaid) return null;
    const words = NO_KEY[facts.key === "needs_model" ? "needs_model" : facts.key === "needs_key" ? "needs_key" : "none"];
    return {
      sentence: words.sentence,
      action: { kind: "link", label: words.label, to: MODEL_PROVIDER_PATH },
      why: [...(profileActive(input) ? ["profile active"] : []), serverSaid && facts.key === "ready" ? "the server says setup is missing" : words.why],
    };
  },
};

// ── D2 ─────────────────────────────────────────────────────────────────────

const D2: NextStepRow<DiscoverFacts> = {
  id: "D2",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.phase !== "idle" || facts.key !== "ready") return null;
    return {
      sentence: "Search company job boards and your search providers.",
      action: { kind: "submit", label: "Search", form: DISCOVER_FORM_ID },
      why: [
        "profile active",
        "model key set",
        ...(facts.searchProviders === null ? [] : [plural(facts.searchProviders, "search provider connected", "search providers connected")]),
      ],
    };
  },
};

// ── D3 ─────────────────────────────────────────────────────────────────────

const D3: NextStepRow<DiscoverFacts> = {
  id: "D3",
  match: (input) => {
    if (input.facts?.phase !== "searching") return null;
    return { sentence: "Searching...", action: null, why: ["link checks can take about 20 seconds"] };
  },
};

// ── D4b, D4, D5 ────────────────────────────────────────────────────────────

// The gap a partial result names: providers that failed, then a count of notes that name none.
function gapOf(results: DiscoverResultsFact): string[] {
  return [
    ...results.failedProviders.map((provider) => `${provider} failed`),
    ...(results.otherNotes > 0 ? [plural(results.otherNotes, "search note", "search notes")] : []),
  ];
}

function countsOf(results: DiscoverResultsFact): string[] {
  return [
    `${results.scored} scored`,
    ...(results.more > 0 ? [`${results.more} more, unscored`] : []),
    ...(results.deadRemoved > 0 ? [`${results.deadRemoved} dropped -- no longer live`] : []),
    ...results.providers.map((entry) => `${entry.provider} ${entry.count}`),
    ...gapOf(results),
  ];
}

const D4B: NextStepRow<DiscoverFacts> = {
  id: "D4b",
  match: (input) => {
    const facts = input.facts;
    if (facts?.phase !== "results" || facts.results === null || !facts.results.fallback) return null;
    if (facts.results.scored + facts.results.more === 0) return null;
    return {
      sentence: "Your model's reply couldn't be read. These scores are placeholders.",
      action: { kind: "submit", label: "Search again", form: DISCOVER_FORM_ID },
      why: ["scoring fell back", "every bin shows Placeholder", ...gapOf(facts.results)],
    };
  },
};

const D4: NextStepRow<DiscoverFacts> = {
  id: "D4",
  match: (input) => {
    const facts = input.facts;
    if (facts?.phase !== "results" || facts.results === null) return null;
    if (facts.results.scored + facts.results.more === 0) return null;
    // Unlit: the choice is per row (Track), so no page-level action is the right one.
    const step: NextStep = { sentence: "Track the jobs worth a draft.", action: null, why: countsOf(facts.results) };
    return step;
  },
};

const D5: NextStepRow<DiscoverFacts> = {
  id: "D5",
  match: (input) => {
    const facts = input.facts;
    if (facts?.phase !== "results" || facts.results === null) return null;
    const results = facts.results;
    if (results.scored + results.more > 0) return null;
    const why = [
      "0 results",
      `filters: ${results.filtersSet} set`,
      ...(results.deadRemoved > 0 ? [`${results.deadRemoved} dropped -- no longer live`] : []),
      ...gapOf(results),
    ];
    // With no filter set there is nothing to widen or clear: the step is unlit and says so.
    if (results.filtersSet === 0) {
      return { sentence: "No results, and no filters were set.", action: null, why };
    }
    return {
      sentence: results.companiesSet ? "No results. Widen your filters or clear Companies." : "No results. Widen your filters.",
      action: { kind: "button", label: "Clear filters", intent: "clear-filters" },
      why,
    };
  },
};

export const rules = definePage("discover", [D1, D2, D3, D4B, D4, D5], { facts: isDiscoverFacts });
