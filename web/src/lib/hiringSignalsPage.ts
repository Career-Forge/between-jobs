// What the Hiring signals page tells the next-step band, as pure functions of what it holds: the
// shared status, the model's state and the search keys read from the credentials. The rows that use
// these facts are lib/nextStep/pages/hiring-signals.ts.

import type { PageFailure } from "./nextStep";
import type { HiringFacts, SavedSearchesFact } from "./nextStep/pages/hiring-signals";
import type { HiringStatus } from "./hiringSignalsStatus";
import { checkForm, findEquivalentSearch, sameRequest } from "./hiringSignalsTab";
import type { TabState } from "./hiringSignalsTabModel";
import type { TabCounts } from "./hiringSignalsTabTypes";

export const STATUS_FAILURE_WHAT = "Couldn't check whether hiring signals are available";
export const SEARCH_FAILURE_WHAT = "Couldn't finish the search";

// Posts the search found and did not show: every category the server's counts name as hidden. Null
// when the counts could not be read (or did not add up), so no number is made up.
export function hiddenOf(counts: TabCounts | null): number | null {
  if (counts === null) return null;
  return counts.rejected + counts.duplicates + counts.role_mismatch_hidden + counts.echoes_hidden + counts.job_seekers_hidden + counts.too_old_hidden;
}

// A search that failed in a way trying again can change: a plain error the server marked retryable
// (every provider failed, a 5xx, an unreadable reply) or the network. A setup failure, a missing thing
// and a request the server would refuse the same way again are not.
export function retryableSearchFailure(state: TabState): boolean {
  const search = state.search;
  return search.kind === "failed" && search.failure.kind === "error" && search.failure.retryable;
}

export function needsSearchKey(state: TabState): boolean {
  return state.search.kind === "failed" && state.search.failure.kind === "setup_required";
}

// Whether the results on the page are the results of what the boxes hold now. Changing the role, the
// location, the window or the wording after a search makes them another search's.
export function resultsMatchBoxes(state: TabState): boolean {
  const search = state.search;
  if (search.kind !== "ready") return false;
  const checked = checkForm(state.form);
  return checked.ok && sameRequest(search.request, checked.request);
}

function savedSearchesFact(state: TabState): SavedSearchesFact {
  if (state.searchesStatus === "loading") return { state: "loading" };
  if (state.searchesStatus === "error") return { state: "failed" };
  return { state: "ready", count: state.searches.length };
}

export function hiringFactsOf(input: { status: HiringStatus; state: TabState; searchKeys: number | null }): HiringFacts {
  const { status, state, searchKeys } = input;
  const search = state.search;
  const matches = resultsMatchBoxes(state);
  return {
    // A status that could not be read is the page's own failure (F1) and is never asked of these rows.
    status: status.kind === "enabled" ? "enabled" : status.kind === "disabled" ? "disabled" : "checking",
    switchedOff: state.disabled,
    searchKeys,
    needsKey: needsSearchKey(state),
    searching: search.kind === "loading",
    savedSearches: savedSearchesFact(state),
    results:
      matches && search.kind === "ready"
        ? { shown: search.outcome.response.signals.length, hidden: hiddenOf(search.outcome.response.counts), cached: search.outcome.response.cached }
        : null,
    resultsSaved: matches && search.kind === "ready" && findEquivalentSearch(state.searches, search.request.query, search.request.location) !== null,
  };
}

// The page's own data failing, for the band's F1: the status read, or a search that can be retried.
// `at` is the client time the failure was seen; the page works it out once per failure.
export function pageFailureOf(input: { status: HiringStatus; state: TabState; statusFailedAt: string; searchFailedAt: string }): PageFailure | null {
  if (input.status.kind === "unavailable") {
    return { what: STATUS_FAILURE_WHAT, call: "GET /hiring-signals/status", at: input.statusFailedAt, intent: "retry-status" };
  }
  if (input.status.kind === "enabled" && !input.state.disabled && retryableSearchFailure(input.state)) {
    return { what: SEARCH_FAILURE_WHAT, call: "POST /hiring-signals/search", at: input.searchFailedAt, intent: "retry-search" };
  }
  return null;
}
