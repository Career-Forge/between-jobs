import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { apiFetch } from "../lib/api";
import {
  HiringPanelModel,
  type FocusRequest,
  focusTargetId,
} from "../lib/hiringSignalsPanelModel";
import { HiringSignalsPanelView, type PanelActions } from "./HiringSignalsPanelView";

// Hiring posts panel (Hiring Signals P3) -- a tracked application's own
// "who is posting about hiring at this company" lookup.
//
// The user clicks "Find hiring posts"; the server queries the search-index
// provider they connected (their own key) for recent public posts about this
// company and returns structured signals: a url, a few computed tags, counts.
// This panel shows them, lets the user open one inline (click-to-load, see
// HiringPostCard.tsx) and save it to this application. Nothing here fetches
// LinkedIn: the only thing that ever contacts it is the user's own browser,
// loading the official embed iframe after an explicit "Show post". Nothing
// posts, messages or contacts anyone -- a save stores a pointer (the numeric
// post id) and nothing else.
//
// This file is only wiring. The state machine (and every guard against
// overlapping clicks and stale replies) is `HiringPanelModel`
// (lib/hiringSignalsPanelModel.ts), what the state looks like is
// `HiringSignalsPanelView`, the requests are lib/hiringSignalsClient.ts, the
// wording is HiringSignalsViews.tsx and the cards HiringPostCard.tsx -- each
// testable without a browser.
//
// One model per application: the inner component is keyed by the application
// id, so opening another application starts from a clean state instead of
// showing the previous one's results.

export function HiringSignalsPanel({ applicationId }: { applicationId: string }) {
  return <ConnectedPanel key={applicationId} applicationId={applicationId} />;
}

function ConnectedPanel({ applicationId }: { applicationId: string }) {
  const [model] = useState(() => new HiringPanelModel(apiFetch, applicationId));
  // The third argument is the server snapshot: a static render (a test) has no
  // subscription to make, and React requires one.
  const state = useSyncExternalStore(model.subscribe, model.getState, model.getState);

  useEffect(() => {
    void model.loadSaves();
  }, [model]);

  useFocusRequests(state.focusRequest, model.consumeFocusRequest);

  const actions = useMemo<PanelActions>(
    () => ({
      setFreshness: model.setFreshness,
      search: (freshness) => void model.runSearch(freshness),
      toggleEmbed: model.toggleEmbed,
      save: (signal, queryLabel) => void model.saveSignal(signal, queryLabel),
      unsave: (save, key) => void model.removeSaved(save, key),
      reloadSaves: () => void model.loadSaves(),
    }),
    [model],
  );

  return <HiringSignalsPanelView state={state} now={new Date()} actions={actions} />;
}

// Moves keyboard focus where the model asked (see the model's FOCUS notes),
// once, and tells it so.
function useFocusRequests(request: FocusRequest | null, consume: (id: number) => void): void {
  useEffect(() => {
    if (request === null) return;
    document.getElementById(focusTargetId(request.target))?.focus();
    consume(request.id);
  }, [request, consume]);
}
