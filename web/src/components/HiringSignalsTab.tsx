import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { apiFetch } from "../lib/api";
import { HiringTabModel, tabFocusTargetId } from "../lib/hiringSignalsTabModel";
import { useFocusRequests } from "../lib/useFocusRequests";
import { HiringSignalsTabView, type TabActions } from "./HiringSignalsTabView";

// The standalone "Hiring signals" tab (Hiring Signals P4) -- recent public
// hiring posts for a ROLE, with no company, found through the search provider the
// person connected.
//
// The person types a role and optionally a metro and presses Search; the server
// queries their own provider's index and returns structured signals: a url, a few
// computed tags, counts. This tab shows them, lets the person open one inline
// (click-to-load, see HiringPostCard.tsx) and save it, and lets them save and
// re-run the SEARCH itself (a role and a location). Nothing here fetches
// LinkedIn: the only thing that ever contacts it is the person's own browser,
// loading the official embed iframe after an explicit "Show post". Nothing posts,
// messages or contacts anyone, and nothing runs unless the person asks -- a saved
// search is a bookmark, not a watch.
//
// This file is only wiring. The state machine (and every guard against
// overlapping searches, double presses and stale replies) is `HiringTabModel`
// (lib/hiringSignalsTabModel.ts), what the state looks like is
// `HiringSignalsTabView`, the requests are lib/hiringSignalsTabClient.ts, the
// wording is lib/hiringSignalsTab.ts -- each testable without a browser.
//
// It is mounted only once the server has said the feature is on (see
// pages/HiringSignals.tsx), so it starts asking for the two saved lists at once.

export function HiringSignalsTab() {
  const [model] = useState(() => new HiringTabModel(apiFetch));
  // The third argument is the server snapshot: a static render has no
  // subscription to make, and React requires one.
  const state = useSyncExternalStore(model.subscribe, model.getState, model.getState);

  useEffect(() => {
    void model.loadSaves();
    void model.loadSearches();
  }, [model]);

  useFocusRequests(state.focusRequest, model.consumeFocusRequest, tabFocusTargetId);

  const actions = useMemo<TabActions>(
    () => ({
      setQuery: model.setQuery,
      setLocation: model.setLocation,
      setFreshness: model.setFreshness,
      setLocale: model.setLocale,
      search: () => void model.search(),
      saveSearch: () => void model.saveCurrentSearch(),
      runSavedSearch: (search) => void model.runSavedSearch(search),
      deleteSearch: (search) => void model.deleteSearch(search),
      reloadSearches: () => void model.loadSearches(),
      widen: (freshness) => void model.widen(freshness),
      retry: () => void model.retry(),
      toggleEmbed: model.toggleEmbed,
      save: (signal, queryLabel) => void model.saveSignal(signal, queryLabel),
      unsave: (save, key) => void model.removeSaved(save, key),
      reloadSaves: () => void model.loadSaves(),
    }),
    [model],
  );

  return <HiringSignalsTabView state={state} now={new Date()} actions={actions} />;
}
