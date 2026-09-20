import { Link } from "react-router-dom";
import {
  APPLICATION_MISSING_MESSAGE,
  CACHED_NOTE,
  type Failure,
  UNCLASSIFIED_LEGEND,
  countsSummary,
  emptyStateNote,
  freshnessLabel,
  isSignalSaved,
  resultSource,
  searchCardKey,
  speciesLegendLines,
  widerFreshness,
} from "../lib/hiringSignals";
import type {
  Freshness,
  HiringSignal,
  SavedPost,
  SearchOutcome,
} from "../lib/hiringSignalsTypes";
import { SignalCard } from "./HiringPostCard";

// The two presentational branches of the "Hiring posts" panel that carry real
// wording -- what to say when a search failed, and what to say about a result
// list (including the honest empty one) -- split out of HiringSignalsPanel.tsx
// for the same reason HiringPostCard.tsx is: they take everything as props and
// import no API client, so HiringSignalsViews.test.tsx can render them to
// static markup and pin the copy and the states that cannot be checked by
// running the app authenticated.
//
// The wording rules, all deliberate:
//   - An empty result is an empty RESULT, not a finding about the company. The
//     search index sees only part of what is posted (and a provider whose index
//     holds no LinkedIn posts at all returns nothing for every company), so the
//     page never says "no one is hiring": it says what the search returned,
//     what could not be shown and why, and that coverage depends on the
//     provider (`emptyStateNote`). It is still not an error state, and comes
//     with the counts sentence and, when the window is not already the widest,
//     a one-click way to widen it.
//   - A short list is never a silent one: the counts sentence names every
//     category that was hidden, and owns up to entries this page could not
//     display.
//   - "Unclassified" is explained once above the cards, whenever one is shown:
//     it means no known post pattern matched, not that the post is a hiring
//     call. Every other tag on screen is explained in visible text too (the
//     tooltip alone is out of reach of keyboard, touch and screen-reader users).
//   - Setup-needed is a to-do, so it links to where the fix is (the
//     Integrations page, the route credential_resolver's settings_path points
//     at) instead of just describing it. Try again is offered only when trying
//     again can change the outcome.

export function FailureView({
  failure,
  onRetry,
}: {
  failure: Failure;
  onRetry: () => void;
}) {
  if (failure.kind === "setup_required") {
    return (
      <div className="bj-hs-callout" role="alert">
        {/* The server's own message already says what is missing; only fall
            back to ours if it sent none. */}
        <div>
          {failure.message !== ""
            ? failure.message
            : "Connect a search provider to find hiring posts."}
        </div>
        <Link to="/profile/integrations">Open Integrations settings</Link>
      </div>
    );
  }
  if (failure.kind === "not_found") {
    return (
      <div className="bj-error" role="alert">
        {APPLICATION_MISSING_MESSAGE} <Link to="/applications">Back to Applications</Link>
      </div>
    );
  }
  return (
    <div className="bj-error" role="alert">
      {failure.message}
      {failure.retryable && (
        <>
          {" "}
          <button type="button" onClick={onRetry}>
            Try again
          </button>
        </>
      )}
    </div>
  );
}

export interface SearchResultsProps {
  outcome: SearchOutcome;
  now: Date;
  savedByActivity: ReadonlyMap<string, SavedPost>;
  savesLoaded: boolean;
  savingIds: ReadonlySet<string>;
  removingIds: ReadonlySet<string>;
  openKeys: ReadonlySet<string>;
  cardErrors: Readonly<Record<string, string>>;
  onWiden: (wider: Freshness) => void;
  onToggleEmbed: (key: string) => void;
  onSave: (signal: HiringSignal) => void;
  onUnsave: (save: SavedPost, key: string) => void;
}

export function SearchResults({
  outcome,
  now,
  savedByActivity,
  savesLoaded,
  savingIds,
  removingIds,
  openKeys,
  cardErrors,
  onWiden,
  onToggleEmbed,
  onSave,
  onUnsave,
}: SearchResultsProps) {
  const { response, unreadable } = outcome;
  const signals = response.signals;
  const summary = countsSummary({
    counts: response.counts,
    shown: signals.length,
    unreadable,
    freshness: response.freshness,
  });
  const wider = signals.length === 0 ? widerFreshness(response.freshness) : null;
  const emptyNote = emptyStateNote({
    provider: response.provider,
    counts: response.counts,
    unreadable,
  });

  return (
    <div className="bj-hs-results">
      <div className="bj-muted bj-small bj-hs-card-text">
        {resultSource(response.query_label, response.provider)}
      </div>
      {response.cached && <div className="bj-muted bj-small">{CACHED_NOTE}</div>}
      {summary !== null && <div className="bj-muted bj-small">{summary}</div>}

      {signals.length === 0 ? (
        <div className="bj-hs-empty">
          {emptyNote !== null && <div className="bj-small">{emptyNote}</div>}
          {wider !== null && (
            <div className="bj-actions">
              <button type="button" onClick={() => onWiden(wider)}>
                Search the {freshnessLabel(wider)}
              </button>
            </div>
          )}
        </div>
      ) : (
        <>
          {signals.some((s) => s.species === "unclassified") && (
            <div className="bj-muted bj-small">{UNCLASSIFIED_LEGEND}</div>
          )}
          {speciesLegendLines(signals).map((line) => (
            <div key={line.species} className="bj-muted bj-small">
              {line.label}: {line.description}
            </div>
          ))}
          <ul className="bj-hs-list">
            {signals.map((signal) => {
              const key = searchCardKey(signal.activity_id);
              const save = savedByActivity.get(signal.activity_id) ?? null;
              const saved = isSignalSaved(signal, savedByActivity, savesLoaded);
              return (
                <SignalCard
                  key={signal.activity_id}
                  signal={signal}
                  now={now}
                  saved={saved}
                  saving={savingIds.has(signal.activity_id)}
                  embedOpen={openKeys.has(key)}
                  error={cardErrors[key] ?? null}
                  onToggleEmbed={() => onToggleEmbed(key)}
                  onSave={() => onSave(signal)}
                  onUnsave={save === null ? null : () => onUnsave(save, key)}
                  unsaving={save !== null && removingIds.has(save.id)}
                />
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
