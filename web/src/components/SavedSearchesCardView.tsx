import {
  SAVED_SEARCHES_HEADING,
  deleteFocusKey,
  SAVED_SEARCHES_NOTE,
  savedSearchActionLabel,
  savedSearchTrail,
  type SavedSearchesState,
} from "../lib/savedSearches";
import { savedSearchLabel } from "../lib/discover";
import { StaticTrailLine } from "./paper/TrailLine";
import "../styles/saved-searches.css";

// The card that lists a person's saved searches, with Pause / Resume and Delete (lib/savedSearches.ts
// holds the logic; components/SavedSearchesCard.tsx holds the state and the requests on Settings, and
// pages/Discover.tsx drives the same view with its own filters). A pure function of its props, with no
// hooks, so a test can render it and press its buttons.
//
// Each search is a list row: its label, then its trail as the last line -- "Checked 06:00" when the
// background matcher has really run over it, "Not checked yet" when no run is on record, and "Paused"
// after either. Pause and Delete are outlined buttons; none is cyan.
//
// It draws nothing while the list is loading, and nothing for a person who has no saved searches
// (a page has no use for an empty card). A failed lookup is NOT nothing: it says so, with a retry,
// because a search that still runs must not look as though it were not there. A search that could
// not be saved (`createError`, from Discover's "Save this search") is shown inside the card too, so
// the failure sits with the list it would have joined -- in every state of the list, including while
// it is loading or when its own read failed (the usual cause, the service being down, fails both).

export interface SavedSearchesActions {
  toggle: (id: string, isActive: boolean) => void;
  remove: (id: string) => void;
  retry: () => void;
}

export function SavedSearchesCardView({
  state,
  actions,
  now = new Date(),
  createError = null,
  timeZone,
}: {
  state: SavedSearchesState;
  actions: SavedSearchesActions;
  /** The moment "today" is read from, so a check earlier today reads as a time and an older one as a date. */
  now?: Date;
  /** A "Save this search" that failed, in the server's words. */
  createError?: string | null;
  timeZone?: string;
}) {
  // Inside the failed-read card, which is itself the alert, the failure is a plain block of that
  // alert (a nested alert would be announced twice).
  const createFailure = (alert: boolean) =>
    createError === null ? null : (
      <div className="bj-ss-error" role={alert ? "alert" : undefined}>
        {createError}
      </div>
    );

  if (state.kind === "loading") {
    if (createError === null) return null;
    return (
      <div className="bj-ss">
        <h2 className="bj-ss-heading">{SAVED_SEARCHES_HEADING}</h2>
        {createFailure(true)}
      </div>
    );
  }

  if (state.kind === "failed") {
    return (
      <div className="bj-ss" role="alert">
        <h2 className="bj-ss-heading">{SAVED_SEARCHES_HEADING}</h2>
        {createFailure(false)}
        <p>{state.message}</p>
        <div className="bj-ss-actions">
          <button type="button" onClick={() => actions.retry()}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (state.searches.length === 0 && createError === null) return null;

  return (
    <div className="bj-ss">
      <h2 className="bj-ss-heading">{SAVED_SEARCHES_HEADING}</h2>
      <p className="bj-ss-note">{SAVED_SEARCHES_NOTE}</p>
      {createFailure(true)}
      {state.error !== null && (
        <div className="bj-ss-error" role="alert">
          {state.error}
        </div>
      )}
      <ul className="bj-ss-list">
        {state.searches.map((search) => {
          // One request is out at a time (lib/useSavedSearches.ts), so while any row's is, every row's
          // buttons say so: a press on another row would be dropped without a word.
          const waiting = state.busyId !== null;
          return (
            <li key={search.id} className="bj-ss-row">
              <div className="bj-ss-text">
                <p className={search.is_active ? "bj-ss-label" : "bj-ss-label bj-ss-label--paused"}>{savedSearchLabel(search)}</p>
                <StaticTrailLine trail={savedSearchTrail(search, now, timeZone)} />
              </div>
              <div className="bj-ss-actions">
                <button
                  type="button"
                  aria-disabled={waiting ? true : undefined}
                  aria-label={savedSearchActionLabel(search.is_active ? "pause" : "resume", search)}
                  onClick={() => !waiting && actions.toggle(search.id, !search.is_active)}
                >
                  {search.is_active ? "Pause" : "Resume"}
                </button>
                <button
                  type="button"
                  aria-disabled={waiting ? true : undefined}
                  aria-label={savedSearchActionLabel("delete", search)}
                  data-focus-key={deleteFocusKey(search.id)}
                  onClick={() => !waiting && actions.remove(search.id)}
                >
                  Delete
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
