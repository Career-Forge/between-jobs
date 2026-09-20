import {
  FRESHNESS_OPTIONS,
  resultHeadline,
  savedCardKey,
  savesByActivity,
} from "../lib/hiringSignals";
import {
  type PanelState,
  SAVED_HEADING_ID,
  SEARCH_BUTTON_ID,
} from "../lib/hiringSignalsPanelModel";
import type { Freshness, HiringSignal, SavedPost } from "../lib/hiringSignalsTypes";
import { SavedPostCard } from "./HiringPostCard";
import { FailureView, SearchResults } from "./HiringSignalsViews";

// The "Hiring posts" panel as a pure function of its state (Hiring Signals P3).
//
// HiringSignalsPanel.tsx owns the model and the wiring; this file is only what
// the state LOOKS like. It imports no API client, holds no hooks and takes its
// handlers as props, so a test can call it directly, walk the elements it
// returns and press its buttons (see `testing/reactTree.ts`) -- which is how
// "the freshness pills are inert while a search runs" and "a switched-off
// feature renders nothing" are pinned without a browser.
//
// TWO STATES RENDER NOTHING, on purpose:
//   - `disabled` (FEATURE_DISABLED): the feature is switched off on the server.
//     No placeholder, no heading, no button.
//   - the first saved-posts reply has not arrived. The panel used to appear at
//     once and vanish a round trip later on a switched-off server, showing a
//     feature that then took itself back; waiting for the first reply (the
//     component asks for it on mount) means a server with the feature off
//     never shows it at all. If that request FAILS the panel shows, with the
//     failure -- the person can still search.
//
// BUSY CONTROLS ARE aria-disabled, NOT disabled. A disabled button drops
// keyboard focus, so pressing Enter on "Find hiring posts" sent focus back to
// the top of a very long page. `aria-disabled` keeps the focus, tells assistive
// technology the control is unavailable, and the model's handlers ignore the
// press (see hiringSignalsPanelModel.ts). The stylesheet dims them like
// :disabled.

export interface PanelActions {
  setFreshness: (freshness: Freshness) => void;
  search: (freshness: Freshness) => void;
  toggleEmbed: (key: string) => void;
  save: (signal: HiringSignal, queryLabel: string) => void;
  unsave: (save: SavedPost, key: string) => void;
  reloadSaves: () => void;
}

export function HiringSignalsPanelView({
  state,
  now,
  actions,
}: {
  state: PanelState;
  now: Date;
  actions: PanelActions;
}) {
  if (state.disabled || state.savesStatus === "loading") return null;

  const { search, freshness, saves, notice } = state;
  const busy = search.kind === "loading";
  const savedByActivity = savesByActivity(saves);

  return (
    <div className="bj-card bj-hs-panel">
      <h2>Hiring posts</h2>
      <p className="bj-muted bj-small">
        Recent public hiring posts about this company, found through the search provider you
        connected.
      </p>

      <div className="bj-hs-controls">
        <div className="bj-view-toggle bj-hs-window" role="group" aria-label="How recent">
          {FRESHNESS_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={freshness === option.value ? "bj-toggle-active" : undefined}
              aria-pressed={freshness === option.value}
              aria-disabled={busy ? true : undefined}
              onClick={() => actions.setFreshness(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          id={SEARCH_BUTTON_ID}
          className="bj-primary"
          aria-disabled={busy ? true : undefined}
          onClick={() => actions.search(freshness)}
        >
          {busy ? "Searching..." : search.kind === "idle" ? "Find hiring posts" : "Search again"}
        </button>
      </div>

      {/* Always mounted so screen readers pick up its changes: loading, the
          result headline, and the save/remove confirmations. Each notice is
          keyed by its own id, so the same words twice in a row are a new node
          and are announced again. */}
      <div className="bj-small" role="status" aria-live="polite">
        {busy && <span className="bj-muted">Searching for recent hiring posts...</span>}
        {search.kind === "ready" && !busy && (
          <strong>{resultHeadline(search.outcome.response.signals.length, search.outcome.unreadable)}</strong>
        )}
        {notice !== null && (
          <span key={notice.id} className="bj-muted">
            {" "}
            {notice.text}
          </span>
        )}
      </div>

      {search.kind === "failed" && (
        <FailureView failure={search.failure} onRetry={() => actions.search(search.freshness)} />
      )}

      {search.kind === "ready" && (
        <SearchResults
          outcome={search.outcome}
          now={now}
          savedByActivity={savedByActivity}
          savesLoaded={state.savesStatus === "ready"}
          savingIds={state.savingIds}
          removingIds={state.removingIds}
          openKeys={state.openKeys}
          cardErrors={state.cardErrors}
          onWiden={(wider) => actions.search(wider)}
          onToggleEmbed={actions.toggleEmbed}
          onSave={(signal) => actions.save(signal, search.outcome.response.query_label)}
          onUnsave={actions.unsave}
        />
      )}

      {(saves.length > 0 || state.savesStatus === "error") && (
        <section className="bj-hs-saved-section" aria-labelledby={SAVED_HEADING_ID}>
          <h3 id={SAVED_HEADING_ID} tabIndex={-1}>
            Saved posts
          </h3>
          {state.savesStatus === "error" && state.savesError !== null && (
            <div className="bj-error" role="alert">
              {state.savesError}{" "}
              <button type="button" onClick={() => actions.reloadSaves()}>
                Try again
              </button>
            </div>
          )}
          {saves.length > 0 && (
            <ul className="bj-hs-list">
              {saves.map((save) => {
                const key = savedCardKey(save.id);
                return (
                  <SavedPostCard
                    key={save.id}
                    save={save}
                    now={now}
                    embedOpen={state.openKeys.has(key)}
                    removing={state.removingIds.has(save.id)}
                    error={state.cardErrors[key] ?? null}
                    onToggleEmbed={() => actions.toggleEmbed(key)}
                    onRemove={() => actions.unsave(save, key)}
                  />
                );
              })}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
