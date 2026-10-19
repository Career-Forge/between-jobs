import { savedCardKey, savesByActivity } from "../lib/hiringSignals";
import { tabResultHeadline } from "../lib/hiringSignalsTab";
import { TAB_SAVED_POSTS_HEADING_ID, type TabState } from "../lib/hiringSignalsTabModel";
import type { TabSignal } from "../lib/hiringSignalsTabTypes";
import { savedTrail } from "../lib/hiringSignalsTrail";
import { SEARCH_KEYS_PATH } from "../lib/nextStep/pages/hiring-signals";
import type { Freshness, SavedPost } from "../lib/hiringSignalsTypes";
import { SavedPostCard } from "./HiringPostCard";
import {
  type FormActions,
  type SavedSearchesActions,
  SavedSearchesSection,
  SearchForm,
} from "./HiringSignalsTabForm";
import { TabFailureView, TabResults } from "./HiringSignalsTabResults";
import { EmptyState, FailureState, SetupRequiredState } from "./paper/StateViews";
import { StaticTrailLine } from "./paper/TrailLine";
import "../styles/hiring-signals.css";

// The "Hiring signals" tab as a pure function of its state (Hiring Signals P4).
//
// HiringSignalsTab.tsx owns the wiring; this file is only what the state LOOKS like. It
// imports no API client, holds no hooks and takes its handlers as props, so a test can call
// it directly, walk the elements it returns and press its buttons (see testing/reactTree.ts).
// The page around it -- the header, the next-step band, and what to show before the server has
// said the feature is on -- is HiringSignalsPageView.tsx.
//
// LAYOUT, top to bottom: the search form (with the polite live region under its
// buttons, in the same block), the saved searches, the results, the saved posts. Saved searches sit
// above the results for the same reason Discover puts them there -- a returning
// person re-runs one and reads the outcome in place. Every block is a list-like section with
// hairline rules, not a card, and every row ends in its trail line, inline.
//
// THE LIVE REGION is always mounted so screen readers pick up its changes:
// the result headline and the save/remove confirmations. The running search is shown beside it, not in
// it, because the page's next-step band announces that sentence. Each notice is
// keyed by its own id, so the same words twice in a row are a new node and are
// announced again. A failure is not announced here: its own block is role="alert".
//
// TWO PAGE-LEVEL STATES are not errors and are worded calmly: the feature being
// switched off on this server (`SwitchedOffNote`), and not yet knowing whether it
// is (the page renders only its header and the band's "Loading..."). Neither is a red
// banner -- nothing has gone wrong; a server that has the feature off is a configuration,
// not a fault.

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
    <EmptyState heading="Hiring signals is switched off">
      <p>This server has the feature turned off, so there is nothing to search here. The rest of the app is unaffected.</p>
    </EmptyState>
  );
}

// The message the setup note carries when no key is saved. It names what the search needs; the link
// is where the keys are added.
export const SEARCH_KEY_NOTE = "Hiring signals searches with your own search provider.";

export function HiringSignalsTabView({
  state,
  now,
  actions,
  keysMissing = false,
  timeZone,
}: {
  state: TabState;
  now: Date;
  actions: TabActions;
  /** The credentials hold none of the four search keys: the results slot says so and links to where they are added. */
  keysMissing?: boolean;
  timeZone?: string;
}) {
  if (state.disabled) return <SwitchedOffNote />;

  const { search, saves, notice } = state;
  const busy = search.kind === "loading";
  const savedByActivity = savesByActivity(saves);
  // The setup note stands in the results slot until a search has answered; after one, the answer is
  // what the slot shows (including the server's own setup message).
  const showKeyNote = keysMissing && (search.kind === "idle" || (search.kind === "failed" && search.failure.kind !== "setup_required"));

  return (
    <div className="bj-hs-tab">
      <div className="bj-hs-panel bj-hs-block bj-hs-form-block">
        <SearchForm state={state} actions={actions} />
        {/* Drawn beside the live region, not in it: the page's next-step band already says it, and
            saying it twice is two announcements for one change. */}
        {busy && <div className="bj-small bj-muted">Searching for recent hiring posts...</div>}
        <div className="bj-small" role="status" aria-live="polite">
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

      <SavedSearchesSection state={state} actions={actions} now={now} timeZone={timeZone} />

      {showKeyNote && (
        <div className="bj-hs-panel bj-hs-block">
          <SetupRequiredState message={SEARCH_KEY_NOTE} missing={[{ label: "Add a search key", to: SEARCH_KEYS_PATH }]} />
        </div>
      )}

      {search.kind === "failed" && (
        <div className="bj-hs-panel bj-hs-block">
          <TabFailureView failure={search.failure} />
        </div>
      )}

      {search.kind === "ready" && (
        <section className="bj-hs-panel bj-hs-block" aria-label="Results">
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
            timeZone={timeZone}
          />
        </section>
      )}

      {(saves.length > 0 || state.savesStatus === "error") && (
        <section className="bj-hs-panel bj-hs-block" aria-labelledby={TAB_SAVED_POSTS_HEADING_ID}>
          <h2 id={TAB_SAVED_POSTS_HEADING_ID} className="bj-hs-heading" tabIndex={-1}>
            Saved posts
          </h2>
          {state.savesStatus === "error" && state.savesError !== null && (
            <FailureState message={state.savesError} onRetry={() => actions.reloadSaves()} retryLabel="Try again" />
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
                    trail={<StaticTrailLine trail={savedTrail(save, now, timeZone)} />}
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
