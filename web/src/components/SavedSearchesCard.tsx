import { useCallback, useEffect, useReducer, useRef } from "react";
import { apiFetch } from "../lib/api";
import {
  deleteSavedSearch,
  initialSavedSearchesState,
  loadSavedSearches,
  savedSearchesReducer,
  setSavedSearchActive,
} from "../lib/savedSearches";
import { SavedSearchesCardView } from "./SavedSearchesCardView";

// The person's saved searches, with Pause / Resume and Delete, on the Integrations page. Thin glue
// over the tested pieces (lib/savedSearches.ts, components/SavedSearchesCardView.tsx). It is here,
// and not only on Discover, because the tester-programme gate leaves Integrations open to someone
// who has not joined or has withdrawn, and a saved search keeps running in the background for them
// until it is paused or deleted.
export function SavedSearchesCard() {
  const [state, dispatch] = useReducer(savedSearchesReducer, initialSavedSearchesState);
  // One request at a time: a second press while one is out has nothing to add.
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

  async function act(id: string, run: () => ReturnType<typeof loadSavedSearches>) {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      dispatch({ type: "action_started", id });
      dispatch(await run());
    } finally {
      inFlight.current = false;
    }
  }

  return (
    <SavedSearchesCardView
      state={state}
      actions={{
        toggle: (id, isActive) => void act(id, () => setSavedSearchActive(apiFetch, id, isActive)),
        remove: (id) => void act(id, () => deleteSavedSearch(apiFetch, id)),
        retry: () => void load(),
      }}
    />
  );
}
