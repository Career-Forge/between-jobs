import { useCallback, useEffect, useReducer, useRef } from "react";
import { apiFetch } from "./api";
import type { SavedSearch } from "./discoverTypes";
import {
  deleteSavedSearch,
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
    remove: (id) => void act(id, () => deleteSavedSearch(apiFetch, id)),
    retry: () => void load(),
    add: (search) => dispatch({ type: "created", search }),
  };
}
