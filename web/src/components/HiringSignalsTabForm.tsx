import { FRESHNESS_OPTIONS } from "../lib/hiringSignals";
import {
  LOCALE_OPTIONS,
  TAB_FORM_LABEL,
  TAB_LOCATION_HINT,
  checkForm,
  sameRequest,
  saveSearchGate,
  savedSearchLabel,
} from "../lib/hiringSignalsTab";
import {
  LOCATION_MAX_CHARS,
  QUERY_MAX_CHARS,
  type SavedHiringSearch,
  toLocaleChoice,
} from "../lib/hiringSignalsTabTypes";
import {
  TAB_LOCATION_ERROR_ID,
  TAB_LOCATION_INPUT_ID,
  TAB_QUERY_ERROR_ID,
  TAB_QUERY_INPUT_ID,
  TAB_SAVED_SEARCHES_HEADING_ID,
  TAB_SAVE_SEARCH_BUTTON_ID,
  TAB_SAVE_SEARCH_REASON_ID,
  TAB_SEARCH_BUTTON_ID,
  type TabState,
} from "../lib/hiringSignalsTabModel";
import type { Freshness } from "../lib/hiringSignalsTypes";
import type { LocaleChoice } from "../lib/hiringSignalsTabTypes";

// The two input-side blocks of the "Hiring signals" tab (Hiring Signals P4): the
// search form and the saved-searches list. Like the P3 panel's views these are
// pure functions of their props -- no hooks, no API client -- so a test can call
// them, walk what they return and press their buttons (testing/reactTree.ts).
//
// THE FORM IS A REAL <form>. Enter in either box submits it, IME composition is
// handled by the browser instead of a hand-rolled keydown check, and the primary
// action is the form's submit button. There is exactly one primary (cyan) action
// on the page: Search.
//
// BUSY CONTROLS ARE aria-disabled, NOT disabled (a disabled button drops
// keyboard focus, so a person who pressed Enter would land back at the top of a
// long page), and the model's handlers are the real guard. Search reads as busy
// only while the request in flight is EXACTLY what the boxes would send: edit
// either box (or the window, or the wording) and it is a different search again,
// which the model sends and lets supersede the slow one. A double press of the
// same words is the model's to ignore, so the button says so instead of looking
// pressable.
//
// "SAVE THIS SEARCH" IS UNAVAILABLE WITH A REASON. When the boxes hold no role, or
// a search that is already saved, the button is aria-disabled and the reason is
// visible text next to it (pointed at by aria-describedby), not only a tooltip.
// Pressing it anyway is still answered (the model shows the field error or
// announces the duplicate) -- an unavailable control must not be silent. The
// 25-search cap is NOT pre-checked: the server owns it, and its own message is
// shown under the button when it refuses.
//
// LABELS LEAD THE ACCESSIBLE NAME. A saved search's buttons read "Run" and
// "Delete" and are named "Run saved search: <its label>" / "Delete saved search:
// <its label>", so a screen reader's list of buttons is not a column of
// identical entries, and the visible word still leads the name.

export interface FormActions {
  setQuery: (query: string) => void;
  setLocation: (location: string) => void;
  setFreshness: (freshness: Freshness) => void;
  setLocale: (locale: LocaleChoice) => void;
  search: () => void;
  saveSearch: () => void;
}

export interface SavedSearchesActions {
  runSavedSearch: (search: SavedHiringSearch) => void;
  deleteSearch: (search: SavedHiringSearch) => void;
  reloadSearches: () => void;
}

const LOCATION_HINT_ID = "hst-location-hint";

export function SearchForm({ state, actions }: { state: TabState; actions: FormActions }) {
  const { form, fieldErrors, search } = state;
  const checked = checkForm(form);
  const searchingThis =
    search.kind === "loading" && checked.ok && sameRequest(search.request, checked.request);
  const gate = saveSearchGate(form, state.searches);
  const saveReason = !state.savingSearch && gate.kind === "blocked" ? gate.reason : null;
  const saveBusyOrBlocked = state.savingSearch || saveReason !== null;

  const locationDescribedBy = [
    LOCATION_HINT_ID,
    fieldErrors.location !== null ? TAB_LOCATION_ERROR_ID : null,
  ]
    .filter((id) => id !== null)
    .join(" ");

  return (
    <form
      className="bj-hs-tab-form"
      aria-label={TAB_FORM_LABEL}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        actions.search();
      }}
    >
      <label className="bj-field">
        <span>Role</span>
        <input
          id={TAB_QUERY_INPUT_ID}
          type="text"
          value={form.query}
          maxLength={QUERY_MAX_CHARS}
          placeholder="e.g. software engineer"
          autoComplete="off"
          aria-required="true"
          aria-invalid={fieldErrors.query !== null ? true : undefined}
          aria-describedby={fieldErrors.query !== null ? TAB_QUERY_ERROR_ID : undefined}
          onChange={(event) => actions.setQuery(event.target.value)}
        />
      </label>
      {fieldErrors.query !== null && (
        <div id={TAB_QUERY_ERROR_ID} className="bj-error" role="alert">
          {fieldErrors.query}
        </div>
      )}

      <label className="bj-field">
        <span>Location (optional)</span>
        <input
          id={TAB_LOCATION_INPUT_ID}
          type="text"
          value={form.location}
          maxLength={LOCATION_MAX_CHARS}
          placeholder="e.g. Bengaluru"
          autoComplete="off"
          aria-invalid={fieldErrors.location !== null ? true : undefined}
          aria-describedby={locationDescribedBy}
          onChange={(event) => actions.setLocation(event.target.value)}
        />
      </label>
      <div id={LOCATION_HINT_ID} className="bj-muted bj-small">
        {TAB_LOCATION_HINT}
      </div>
      {fieldErrors.location !== null && (
        <div id={TAB_LOCATION_ERROR_ID} className="bj-error" role="alert">
          {fieldErrors.location}
        </div>
      )}

      <div className="bj-hs-controls">
        <div className="bj-view-toggle bj-hs-window" role="group" aria-label="How recent">
          {FRESHNESS_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={form.freshness === option.value ? "bj-toggle-active" : undefined}
              aria-pressed={form.freshness === option.value}
              onClick={() => actions.setFreshness(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <label className="bj-hs-tab-wording">
          <span className="bj-muted bj-small">Wording</span>
          <select
            value={form.locale}
            onChange={(event) => actions.setLocale(toLocaleChoice(event.target.value))}
          >
            {LOCALE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="bj-actions">
        <button
          type="submit"
          id={TAB_SEARCH_BUTTON_ID}
          className="bj-primary"
          aria-disabled={searchingThis ? true : undefined}
        >
          {searchingThis ? "Searching..." : "Search"}
        </button>
        <button
          type="button"
          id={TAB_SAVE_SEARCH_BUTTON_ID}
          onClick={() => actions.saveSearch()}
          aria-disabled={saveBusyOrBlocked ? true : undefined}
          aria-describedby={saveReason !== null ? TAB_SAVE_SEARCH_REASON_ID : undefined}
        >
          {state.savingSearch ? "Saving..." : "Save this search"}
        </button>
      </div>
      {saveReason !== null && (
        <div id={TAB_SAVE_SEARCH_REASON_ID} className="bj-muted bj-small">
          {saveReason}
        </div>
      )}
      {state.saveSearchError !== null && (
        <div className="bj-error" role="alert">
          {state.saveSearchError}
        </div>
      )}

      <p className="bj-muted bj-small">
        Searches run only when you ask. A saved search keeps just the role and location -- it does
        not run or watch anything.
      </p>
    </form>
  );
}

export function SavedSearchesSection({
  state,
  actions,
}: {
  state: TabState;
  actions: SavedSearchesActions;
}) {
  const { searches, searchesStatus } = state;
  if (searches.length === 0 && searchesStatus !== "error") return null;

  return (
    <section className="bj-card bj-hs-panel" aria-labelledby={TAB_SAVED_SEARCHES_HEADING_ID}>
      <h2 id={TAB_SAVED_SEARCHES_HEADING_ID} tabIndex={-1}>
        Saved searches
      </h2>
      {searchesStatus === "error" && state.searchesError !== null && (
        <div className="bj-error" role="alert">
          {state.searchesError}{" "}
          <button type="button" onClick={() => actions.reloadSearches()}>
            Try again
          </button>
        </div>
      )}
      {state.deleteSearchError !== null && (
        <div className="bj-error" role="alert">
          {state.deleteSearchError}
        </div>
      )}
      {searches.length > 0 && (
        <>
          <p className="bj-muted bj-small">Run one to search it again with the window above.</p>
          <ul className="bj-hs-list">
            {searches.map((search) => {
              const label = savedSearchLabel(search);
              const deleting = state.deletingSearchIds.has(search.id);
              return (
                <li key={search.id} className="bj-hs-tab-search-row">
                  <span className="bj-hs-tab-search-label">{label}</span>
                  <div className="bj-hs-actions">
                    <button
                      type="button"
                      onClick={() => actions.runSavedSearch(search)}
                      aria-label={`Run saved search: ${label}`}
                    >
                      Run
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (!deleting) actions.deleteSearch(search);
                      }}
                      aria-disabled={deleting ? true : undefined}
                      aria-label={`Delete saved search: ${label}`}
                    >
                      {deleting ? "Deleting..." : "Delete"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
