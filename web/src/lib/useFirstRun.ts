import { useEffect, useState } from "react";
import {
  browserStorage,
  factsArrived,
  initialFirstRunState,
  onFirstRunDismissed,
  onFirstRunFactsLoaded,
  viewOfFirstRun,
  type FirstRunFacts,
  type FirstRunView,
} from "./firstRun";

// The first-run checklist bound to React. Everything it decides -- what is read from
// storage and under whose key, what is written, what the card shows -- is lib/firstRun.ts; this is
// only the wiring (the one place that touches the browser's storage), so that module stays loadable
// from a test. It asks the API for nothing: Today reads the account once and hands the answers in.
// Every call below passes `userId`: the flags are per person.

export function useFirstRun(userId: string, facts: FirstRunFacts): { view: FirstRunView; dismiss: () => void } {
  const [state, setState] = useState(() => initialFirstRunState(browserStorage(), userId));
  const arrived = factsArrived(facts);

  useEffect(() => {
    if (!arrived) return;
    // Read and written outside the state updater: an updater runs twice under StrictMode.
    const loaded = onFirstRunFactsLoaded(facts, browserStorage(), userId);
    setState((current) => ({ ...current, ...loaded }));
  }, [arrived, facts, userId]);

  return {
    view: viewOfFirstRun(state),
    dismiss: () => setState(onFirstRunDismissed(state, browserStorage(), userId)),
  };
}
