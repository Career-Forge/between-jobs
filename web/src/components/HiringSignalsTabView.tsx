import type { ReactNode } from "react";
import { savedCardKey, savesByActivity } from "../lib/hiringSignals";
import { TAB_INTRO, tabResultHeadline } from "../lib/hiringSignalsTab";
import { TAB_SAVED_POSTS_HEADING_ID, type TabState } from "../lib/hiringSignalsTabModel";
import type { TabSignal } from "../lib/hiringSignalsTabTypes";
import type { HiringStatus } from "../lib/hiringSignalsStatus";
import type { Freshness, SavedPost } from "../lib/hiringSignalsTypes";
import { SavedPostCard } from "./HiringPostCard";
import {
  type FormActions,
  type SavedSearchesActions,
  SavedSearchesSection,
  SearchForm,
} from "./HiringSignalsTabForm";
import { TabFailureView, TabResults } from "./HiringSignalsTabResults";

// The "Hiring signals" tab as a pure function of its state (Hiring Signals P4).
//
// HiringSignalsTab.tsx owns the model and the wiring; this file is only what the
// state LOOKS like. It imports no API client, holds no hooks and takes its
// handlers as props, so a test can call it directly, walk the elements it returns
// and press its buttons (see testing/reactTree.ts).
//
// LAYOUT, top to bottom: the search form (with the polite live region under its
// buttons, in the same card), the saved searches, the results, the saved posts. Saved searches sit
// above the results for the same reason Discover puts them there -- a returning
// person re-runs one and reads the outcome in place.
//
// THE LIVE REGION is always mounted so screen readers pick up its changes:
// loading, the result headline, and the save/remove confirmations. Each notice is
// keyed by its own id, so the same words twice in a row are a new node and are
// announced again. A failure is not announced here: its own block is role="alert".
//
// TWO PAGE-LEVEL STATES are not errors and are worded calmly: the feature being
// switched off on this server (`SwitchedOffNote`), and not yet knowing whether it
// is (the shell renders only the heading). Neither is a red banner -- nothing has
// gone wrong; a server that has the feature off is a configuration, not a fault.

export interface TabActions extends FormActions, SavedSearchesActions {
  widen: (freshness: Freshness) => void;
  retry: () => void;
  toggleEmbed: (key: string) => void;
  save: (signal: TabSignal, queryLabel: string) => void;
  unsave: (save: SavedPost, key: string) => void;
  reloadSaves: () => void;
}

export function SwitchedOffNote() {
  return (
    <div className="bj-empty">
      <h2>Hiring signals is switched off</h2>
      <p>
        This server has the feature turned off, so there is nothing to search here. The rest of the
        app is unaffected.
      </p>
    </div>
  );
}

export function HiringSignalsTabView({
  state,
  now,
  actions,
}: {
  state: TabState;
  now: Date;
  actions: TabActions;
}) {
  if (state.disabled) return <SwitchedOffNote />;

  const { search, saves, notice } = state;
  const busy = search.kind === "loading";
  const savedByActivity = savesByActivity(saves);

  return (
    <div className="bj-hs-tab">
      <p className="bj-muted bj-hs-tab-intro">{TAB_INTRO}</p>

      <div className="bj-card bj-hs-panel">
        <SearchForm state={state} actions={actions} />
        <div className="bj-small" role="status" aria-live="polite">
          {busy && <span className="bj-muted">Searching for recent hiring posts...</span>}
          {search.kind === "ready" && !busy && (
            <strong>
              {tabResultHeadline(search.outcome.response.signals.length, search.outcome.unreadable)}
            </strong>
          )}
          {notice !== null && (
            <span key={notice.id} className="bj-muted">
              {" "}
              {notice.text}
            </span>
          )}
        </div>
      </div>

      <SavedSearchesSection state={state} actions={actions} />

      {search.kind === "failed" && (
        <div className="bj-card bj-hs-panel">
          <TabFailureView failure={search.failure} onRetry={() => actions.retry()} />
        </div>
      )}

      {search.kind === "ready" && (
        <section className="bj-card bj-hs-panel" aria-label="Results">
          <TabResults
            outcome={search.outcome}
            request={search.request}
            now={now}
            savedByActivity={savedByActivity}
            savesLoaded={state.savesStatus === "ready"}
            savingIds={state.savingIds}
            removingIds={state.removingIds}
            openKeys={state.openKeys}
            cardErrors={state.cardErrors}
            onWiden={actions.widen}
            onToggleEmbed={actions.toggleEmbed}
            onSave={(signal) => actions.save(signal, search.outcome.response.query_label)}
            onUnsave={actions.unsave}
          />
        </section>
      )}

      {(saves.length > 0 || state.savesStatus === "error") && (
        <section className="bj-card bj-hs-panel" aria-labelledby={TAB_SAVED_POSTS_HEADING_ID}>
          <h2 id={TAB_SAVED_POSTS_HEADING_ID} tabIndex={-1}>
            Saved posts
          </h2>
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

// The page around the tab: its heading, and what to show before the server has
// said the feature is on. `children` is the connected tab, mounted only when the
// status is "enabled" -- so a server with the feature off never gets a saves or
// searches request from this page, and a person who reaches /hiring-signals
// directly sees a calm explanation instead of a form that cannot work.
export function HiringSignalsPageShell({
  status,
  onRetryStatus,
  children,
}: {
  status: HiringStatus;
  onRetryStatus: () => void;
  children: ReactNode;
}) {
  return (
    <div>
      <h1>Hiring signals</h1>
      {status.kind === "enabled" && children}
      {status.kind === "disabled" && <SwitchedOffNote />}
      {status.kind === "unavailable" && (
        <div className="bj-hs-callout" role="alert">
          <div>Could not check whether hiring signals are available on this server.</div>
          <div>
            <button type="button" onClick={onRetryStatus}>
              Try again
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
