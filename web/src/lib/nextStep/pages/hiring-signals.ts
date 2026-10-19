// The Hiring signals page's rows. They run after the global rows. A failed status read and a search
// that failed in a way trying again can change are the page's own data failing, so they are the
// global F1 (the page passes `pageFailure`); everything else is here.
//
//   HS0  switched off on this server (the status says so, or any request answered FEATURE_DISABLED)
//   --   the status is still being asked: the band waits, and shows only "Loading..."
//   HS1  no search key: the credentials hold none of the four providers the search can use, or a
//        search answered SETUP_REQUIRED
//   HS2  ready and nothing on the page for what the boxes hold -- Search is the one cyan action; or
//        a search is in flight (no button)
//   HS3  results on the page for what the boxes hold: Save this search when it is not saved yet,
//        unlit when it is (or when the page cannot tell)
//
// "Results for what the boxes hold" is decided by the page, which compares the boxes with the
// search that produced the results the way the model compares requests; when the boxes were changed
// afterwards the page passes `results: null`, and the band is back at HS2 rather than describing
// posts that belong to another search.
//
// Hiring signals needs neither a profile nor a model key. The page tells the engine so
// (`profileNeeded: false`) and hands the global model-key row no `modelKey`, so a missing profile or a
// missing model key never takes the page's own step. The credentials are still read, for the search
// keys, and a failed read of them is the global G1.
//
// Every number in a why line is a count the page holds: posts shown, posts hidden (only when the
// server's counts could be read and add up), search keys connected, saved searches (only when that
// list loaded). Nothing is said about a cache time, because none is returned.

import { SETTINGS_PATH } from "../../settingsPaths";
import { definePage, type NextStepRow } from "../types";

// The section of Settings where the four search keys are added (id="search-keys"). Built from the
// Settings path, which lib/settingsPaths.ts owns.
export const SEARCH_KEYS_PATH = `${SETTINGS_PATH}#search-keys`;

// The id the search form carries, so the band's Search can submit it from outside.
export const HIRING_FORM_ID = "hiring-signals-form";

export type HiringStatusFact = "checking" | "enabled" | "disabled";

export type SavedSearchesFact = { state: "loading" } | { state: "failed" } | { state: "ready"; count: number };

export interface HiringResultsFact {
  /** Posts on screen. */
  shown: number;
  /** Posts the search found and did not show; null when the server's counts could not be read. */
  hidden: number | null;
  /** The reply came from a recent cached search. */
  cached: boolean;
}

export interface HiringFacts {
  status: HiringStatusFact;
  /** Some request answered FEATURE_DISABLED. */
  switchedOff: boolean;
  /** Search keys saved among Brave, Serper, Firecrawl and You.com; null when the credentials were not read. */
  searchKeys: number | null;
  /** A search answered SETUP_REQUIRED. */
  needsKey: boolean;
  /** A search is in flight. */
  searching: boolean;
  savedSearches: SavedSearchesFact;
  /** The page shows results and the boxes still hold the search that produced them. */
  results: HiringResultsFact | null;
  /** The saved-searches list holds a search equivalent to the one the results came from. */
  resultsSaved: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

export function isHiringFacts(value: unknown): value is HiringFacts {
  if (!isRecord(value)) return false;
  if (!["checking", "enabled", "disabled"].includes(String(value.status))) return false;
  if (typeof value.switchedOff !== "boolean" || typeof value.needsKey !== "boolean") return false;
  if (typeof value.searching !== "boolean" || typeof value.resultsSaved !== "boolean") return false;
  if (value.searchKeys !== null && !isCount(value.searchKeys)) return false;
  const saved = value.savedSearches;
  if (!isRecord(saved) || !["loading", "failed", "ready"].includes(String(saved.state))) return false;
  if (saved.state === "ready" && !isCount(saved.count)) return false;
  const results = value.results;
  if (results === null) return true;
  return isRecord(results) && isCount(results.shown) && (results.hidden === null || isCount(results.hidden)) && typeof results.cached === "boolean";
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

const HS0: NextStepRow<HiringFacts> = {
  id: "HS0",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || (facts.status !== "disabled" && !facts.switchedOff)) return null;
    return {
      sentence: "Hiring signals is switched off on this server. Nothing else is affected.",
      action: null,
      why: ["switched off on this server"],
    };
  },
};

// Not asked yet: nothing is lit and nothing is guessed. The band shows its eyebrow and "Loading...".
const HS_LOADING: NextStepRow<HiringFacts> = {
  id: "HSL",
  match: (input) => (input.facts?.status === "checking" ? "wait" : null),
};

const HS1: NextStepRow<HiringFacts> = {
  id: "HS1",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || (facts.searchKeys !== 0 && !facts.needsKey)) return null;
    return {
      sentence: "Hiring signals searches with your own search provider. Add a Brave, Serper, Firecrawl or You.com key first.",
      action: { kind: "link", label: "Add a search key", to: SEARCH_KEYS_PATH },
      why: ["no Brave, Serper, Firecrawl or You.com key"],
    };
  },
};

const HS2: NextStepRow<HiringFacts> = {
  id: "HS2",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.results !== null) return null;
    if (facts.searching) {
      return { sentence: "Searching for recent hiring posts...", action: null, why: ["searching with your own search keys"] };
    }
    return {
      sentence: "Search recent public hiring posts for a role.",
      action: { kind: "submit", label: "Search", form: HIRING_FORM_ID },
      why: [
        ...(facts.searchKeys === null ? [] : [plural(facts.searchKeys, "search key connected", "search keys connected")]),
        ...(facts.savedSearches.state === "ready" ? [plural(facts.savedSearches.count, "saved search", "saved searches")] : []),
      ],
    };
  },
};

const HS3: NextStepRow<HiringFacts> = {
  id: "HS3",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.results === null) return null;
    const results = facts.results;
    const counts = [`${results.shown} shown`, ...(results.hidden === null ? [] : [`${results.hidden} hidden`])];
    const cached = results.cached ? ["from a recent cached search"] : [];
    // The saved list has not answered: whether this search is saved is not known yet, and a step
    // lit now could be replaced a moment later.
    if (facts.savedSearches.state === "loading") return "wait";
    if (facts.savedSearches.state === "failed") {
      return { sentence: "Open the posts worth a closer look.", action: null, why: [...counts, "saved searches couldn't load", ...cached] };
    }
    if (facts.resultsSaved) {
      return { sentence: "Open the posts worth a closer look.", action: null, why: [...counts, "search saved", ...cached] };
    }
    return {
      sentence: "Save this search to run it again later. It won't run on its own.",
      action: { kind: "button", label: "Save this search", intent: "save-search" },
      why: [...counts, "search not saved", ...cached],
    };
  },
};

export const rules = definePage("hiring-signals", [HS0, HS_LOADING, HS1, HS2, HS3], { facts: isHiringFacts });
