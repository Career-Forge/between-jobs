import {
  SAVED_SEARCHES_HEADING,
  SAVED_SEARCHES_NOTE,
  savedSearchActionLabel,
  type SavedSearchesState,
} from "../lib/savedSearches";
import { savedSearchLabel } from "../lib/discover";

// The card that lists a person's saved searches with Pause / Resume and Delete (lib/savedSearches.ts
// holds the logic; components/SavedSearchesCard.tsx holds the state and the requests). A pure
// function of its props, with no hooks, so a test can render it and press its buttons.
//
// It draws nothing while the list is loading, and nothing for a person who has no saved searches
// (an Integrations page has no use for an empty card). A failed lookup is NOT nothing: it says so,
// with a retry, because a search that still runs must not look as though it were not there.

export interface SavedSearchesActions {
  toggle: (id: string, isActive: boolean) => void;
  remove: (id: string) => void;
  retry: () => void;
}

export function SavedSearchesCardView({
  state,
  actions,
}: {
  state: SavedSearchesState;
  actions: SavedSearchesActions;
}) {
  if (state.kind === "loading") return null;

  if (state.kind === "failed") {
    return (
      <div className="bj-card" role="alert">
        <h2>{SAVED_SEARCHES_HEADING}</h2>
        <p>{state.message}</p>
        <div className="bj-actions">
          <button type="button" onClick={() => actions.retry()}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (state.searches.length === 0) return null;

  return (
    <div className="bj-card">
      <h2>{SAVED_SEARCHES_HEADING}</h2>
      <p className="bj-muted bj-small">{SAVED_SEARCHES_NOTE}</p>
      {state.error !== null && (
        <div className="bj-error" role="alert">
          {state.error}
        </div>
      )}
      <div className="bj-discover-saved-search-list">
        {state.searches.map((search) => {
          const waiting = state.busyId === search.id;
          return (
            <div key={search.id} className="bj-discover-saved-search-row">
              <span className={search.is_active ? undefined : "bj-muted"}>
                {savedSearchLabel(search)}
                {search.is_active ? "" : " (paused)"}
              </span>
              <div className="bj-actions">
                <button
                  type="button"
                  disabled={waiting}
                  aria-label={savedSearchActionLabel(search.is_active ? "pause" : "resume", search)}
                  onClick={() => actions.toggle(search.id, !search.is_active)}
                >
                  {search.is_active ? "Pause" : "Resume"}
                </button>
                <button
                  type="button"
                  disabled={waiting}
                  aria-label={savedSearchActionLabel("delete", search)}
                  onClick={() => actions.remove(search.id)}
                >
                  Delete
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
