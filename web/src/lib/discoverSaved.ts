// What the closed saved-searches row on Discover says about the list under it, as a pure function:
// how many searches there are, one short hint, and whether something went wrong that a person should
// see without opening it. The row is components/discover/SavedSearchesFold.tsx; the list, its states
// and its requests are lib/savedSearches.ts.
//
// A closed row must not hide a failure. A failed read, a refused save and a failed pause, resume or
// delete each make `attention` true, and the closed header carries their words as its hint. A request
// that is merely under way ("Updating...", "Saving...") shows as a hint only: it is started from the
// open row or the form, and forcing the row open on every save would shove the results down the page.

import { isChecked, type SavedSearchesState } from "./savedSearches";

export interface SavedSummary {
  count: number;
  hint: string;
  /** Something needs a person's eye: the row opens by itself, and a person can then close it. */
  attention: boolean;
}

function searches(count: number): string {
  return `${count} ${count === 1 ? "search" : "searches"}`;
}

/**
 * null means no row at all: with no searches, or none known yet, there is nothing to fold away.
 * `justSaved` is a save that has just landed; a healthy list then leads its hint with that news, because
 * a closed row is the only place a person who did not open it would see that the save worked.
 */
export function savedSearchesSummary(input: { state: SavedSearchesState; createError: string | null; saving: boolean; justSaved?: boolean }): SavedSummary | null {
  const { state, createError, saving, justSaved = false } = input;
  const count = state.kind === "ready" ? state.searches.length : 0;
  if (state.kind === "failed") return { count, hint: "Couldn't be read", attention: true };
  if (createError !== null) return { count, hint: "A search couldn't be saved", attention: true };
  if (state.kind !== "ready") return null;
  if (state.error !== null) return { count, hint: "An update didn't go through", attention: true };
  if (state.busyId !== null) return { count, hint: "Updating...", attention: false };
  if (saving && count > 0) return { count, hint: "Saving...", attention: false };
  if (count === 0) return null;
  const lead = justSaved ? "Search saved -- " : "";
  const active = state.searches.filter((search) => search.is_active).length;
  if (active === 0) return { count, hint: `${lead}${count === 1 ? "1 search, paused" : `${searches(count)}, all paused`}`, attention: false };
  if (active < count) return { count, hint: `${lead}${searches(count)}, ${active} active`, attention: false };
  // All are running. "Checked" is said only for a list in which the background matcher has really run
  // over at least one search; a search saved a moment ago has not been looked at yet.
  const checked = state.searches.some(isChecked);
  return { count, hint: `${lead}${searches(count)}, ${checked ? "checked in the background" : "not checked yet"}`, attention: false };
}
