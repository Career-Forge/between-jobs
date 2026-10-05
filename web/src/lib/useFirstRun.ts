import { useEffect, useState } from "react";
import { apiFetch } from "./api";
import {
  browserStorage,
  initialFirstRunState,
  loadFirstRunFacts,
  onFirstRunDismissed,
  onFirstRunFactsLoaded,
  shouldLoadFirstRun,
  viewOfFirstRun,
  type FirstRunView,
} from "./firstRun";

// The first-run checklist bound to React. Everything it decides -- what is read from
// storage and under whose key, what is written, whether anything is requested, what the
// card shows -- is lib/firstRun.ts; this is only the wiring (the one place that imports the
// API client and touches the browser's storage), so that module stays loadable from a test.
// Every call below passes `userId`: the flags are per person.

export function useFirstRun(userId: string): { view: FirstRunView; dismiss: () => void } {
  const [state, setState] = useState(() => initialFirstRunState(browserStorage(), userId));
  const load = shouldLoadFirstRun(state);

  useEffect(() => {
    if (!load) return;
    let cancelled = false;
    void loadFirstRunFacts(apiFetch).then((facts) => {
      // Read and written outside the state updater: an updater runs twice under StrictMode.
      if (cancelled) return;
      const loaded = onFirstRunFactsLoaded(facts, browserStorage(), userId);
      setState((current) => ({ ...current, ...loaded }));
    });
    return () => {
      cancelled = true;
    };
  }, [load, userId]);

  return {
    view: viewOfFirstRun(state),
    dismiss: () => setState(onFirstRunDismissed(state, browserStorage(), userId)),
  };
}
