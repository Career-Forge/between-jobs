import { useCallback, useEffect, useReducer, useRef } from "react";
import { NEXT_STEP_FOCUS_ID } from "../components/paper/NextStepBand";
import { apiFetch } from "./api";
import type { SavedSearch } from "./discoverTypes";
import {
  deleteSavedSearch,
  focusKeysAfterRemoving,
  initialSavedSearchesState,
  loadSavedSearches,
  savedSearchesReducer,
  setSavedSearchActive,
  type SavedSearchesState,
} from "./savedSearches";

// The saved-searches list bound to React: read when the page opens, and Pause / Resume / Delete as
// requests, one at a time (a second press while one is out has nothing to add). Both the Settings card
// and the Discover page use it, so they share what a failed read, a busy row and a new search do.
// `add` puts a search the page just created on top of the list.

export interface SavedSearchesHandle {
  state: SavedSearchesState;
  toggle: (id: string, isActive: boolean) => void;
  remove: (id: string) => void;
  retry: () => void;
  add: (search: SavedSearch) => void;
}

export function useSavedSearches(): SavedSearchesHandle {
  const [state, dispatch] = useReducer(savedSearchesReducer, initialSavedSearchesState);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      dispatch({ type: "load_started" });
      dispatch(await loadSavedSearches(apiFetch));
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Deleting a row takes the button that had focus away with it, and focus would fall to the top of the
  // page. When the row has gone, focus goes to the Delete button of its neighbour; when it was the only
  // one the card draws nothing, and focus goes to the page's next-step band. A person who has moved on
  // since (focus is on something real) is left where they are.
  const afterRemoving = useRef<{ id: string; keys: string[] } | null>(null);
  useEffect(() => {
    const pending = afterRemoving.current;
    if (pending === null || state.kind !== "ready") return;
    if (state.searches.some((search) => search.id === pending.id)) {
      // Still listed: the request is out, or it failed and the button still has focus.
      if (state.busyId === null) afterRemoving.current = null;
      return;
    }
    afterRemoving.current = null;
    const active = document.activeElement;
    if (active !== null && active !== document.body) return;
    for (const key of pending.keys) {
      const target = document.querySelector<HTMLElement>(`[data-focus-key="${key}"]`);
      if (target !== null) {
        target.focus();
        return;
      }
    }
    document.getElementById(NEXT_STEP_FOCUS_ID)?.focus();
  });

  const act = useCallback(async (id: string, run: () => ReturnType<typeof loadSavedSearches>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      dispatch({ type: "action_started", id });
      dispatch(await run());
    } finally {
      inFlight.current = false;
    }
  }, []);

  return {
    state,
    toggle: (id, isActive) => void act(id, () => setSavedSearchActive(apiFetch, id, isActive)),
    remove: (id) => {
      if (state.kind === "ready" && !inFlight.current) afterRemoving.current = { id, keys: focusKeysAfterRemoving(state.searches, id) };
      void act(id, () => deleteSavedSearch(apiFetch, id));
    },
    retry: () => void load(),
    add: (search) => dispatch({ type: "created", search }),
  };
}
