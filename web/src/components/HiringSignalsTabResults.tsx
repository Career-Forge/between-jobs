import { Link } from "react-router-dom";
import {
  AGGREGATOR_LEGEND,
  CACHED_NOTE,
  type Failure,
  UNCLASSIFIED_LEGEND,
  freshnessLabel,
  isSignalSaved,
  searchCardKey,
  speciesLegendLines,
  widerFreshness,
} from "../lib/hiringSignals";
import {
  TAB_NOT_FOUND_MESSAGE,
  TAB_YOU_COM_NO_LINKEDIN_NOTE,
  type TabSearchRequest,
  looseRoleNote,
  tabCountsSummary,
  tabEmptyHints,
  tabEmptyNote,
  tabRegistryMatchNote,
  tabResultSource,
  tabRoleHint,
  tabTruncationNote,
} from "../lib/hiringSignalsTab";
import type { TabSearchOutcome, TabSignal } from "../lib/hiringSignalsTabTypes";
import type { Freshness, SavedPost } from "../lib/hiringSignalsTypes";
import { SignalCard } from "./HiringPostCard";
import { FailureView } from "./HiringSignalsViews";

// What the "Hiring signals" tab says about a search (Hiring Signals P4): the
// failure branch and the result list, including the honest empty one. Pure
// functions of their props, like the P3 panel's views, so a test can render them
// to markup or walk and press them without a browser.
//
// THE WORDING RULES are P3's, re-applied to a search that has no company:
//   - An empty result is an empty RESULT, not a finding about the role. The
//     search index sees only part of what is posted (and a provider whose index
//     holds no LinkedIn posts at all returns nothing for every role), so the page
//     never says "no one is hiring": it says what the search returned, what could
//     not be shown and why, and that coverage depends on the provider. It is not
//     an error state; it carries the counts sentence, hints that are each tied to
//     something true of THIS search, and -- when the window is not already the
//     widest -- a one-click way to widen it.
//   - A short list is never a silent one: the counts sentence names every
//     category that was hidden (with this tab's meaning for each: a post is hidden
//     for not containing the ROLE words, never for "not being about this
//     company"), and owns up to entries this page could not display.
//   - "Unclassified" is explained once above the cards, whenever one is shown: it
//     means no known post pattern matched, not that the post is a hiring call.
//     Every other tag on screen, and the aggregator tag, is explained in visible
//     text too (a tooltip alone is out of reach of keyboard, touch and
//     screen-reader users).
//   - A failure is worded by kind. Setup-needed is a to-do, so it links to where
//     the fix is; Try again is offered only when trying again can change the
//     outcome; and a missing thing is reworded, because P3's wording for it names
//     an application.
//
// The cards themselves are P3's SignalCard, unchanged in behavior: click-to-load
// embeds behind the strict validator, computed facts only.

export function TabFailureView({ failure, onRetry }: { failure: Failure; onRetry: () => void }) {
  if (failure.kind === "not_found") {
    return (
      <div className="bj-error" role="alert">
        {TAB_NOT_FOUND_MESSAGE}
      </div>
    );
  }
  // setup_required (a link to Integrations) and every other error, retry button
  // included, are worded exactly as on the per-application panel.
  return <FailureView failure={failure} onRetry={onRetry} />;
}

export interface TabResultsProps {
  outcome: TabSearchOutcome;
  request: TabSearchRequest;
  now: Date;
  savedByActivity: ReadonlyMap<string, SavedPost>;
  savesLoaded: boolean;
  savingIds: ReadonlySet<string>;
  removingIds: ReadonlySet<string>;
  openKeys: ReadonlySet<string>;
  cardErrors: Readonly<Record<string, string>>;
  onWiden: (wider: Freshness) => void;
  onToggleEmbed: (key: string) => void;
  onSave: (signal: TabSignal) => void;
  onUnsave: (save: SavedPost, key: string) => void;
}

export function TabResults({
  outcome,
  request,
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
}: TabResultsProps) {
  const { response, unreadable } = outcome;
  const signals = response.signals;
  const summary = tabCountsSummary({
    counts: response.counts,
    shown: signals.length,
    unreadable,
    freshness: response.freshness,
  });
  // Only the empty branch below offers it, so there is nothing to gate here: a
  // result with cards never shows a widen button, and the widest window has no
  // wider one (null).
  const wider = widerFreshness(response.freshness);
  const emptyNote = tabEmptyNote({
    provider: response.provider,
    counts: response.counts,
    unreadable,
  });
  const hints = tabEmptyHints({ counts: response.counts, location: request.location });
  // Notes that are true of THIS search whatever is on screen: a role so short it
  // matches other senses of the same letters, a search that stopped at the
  // provider's cap, and (with cards showing) a role worded more narrowly than the
  // posts. The empty branch already carries its own role hint (`hints`).
  const looseNote = looseRoleNote(request.query);
  const truncatedNote = tabTruncationNote(response.counts);
  const roleHint = signals.length > 0 ? tabRoleHint(response.counts) : null;

  return (
    <div className="bj-hs-results">
      <div className="bj-muted bj-small bj-hs-card-text">
        {tabResultSource(response.query_label, response.provider, response.locale)}
      </div>
      {response.cached && <div className="bj-muted bj-small">{CACHED_NOTE}</div>}
      {summary !== null && <div className="bj-muted bj-small">{summary}</div>}
      {truncatedNote !== null && <div className="bj-muted bj-small">{truncatedNote}</div>}
      {roleHint !== null && <div className="bj-muted bj-small">{roleHint}</div>}
      {looseNote !== null && <div className="bj-muted bj-small">{looseNote}</div>}

      {signals.length === 0 ? (
        <div className="bj-hs-empty">
          {emptyNote !== null && <div className="bj-small">{emptyNote}</div>}
          {emptyNote === TAB_YOU_COM_NO_LINKEDIN_NOTE && (
            <div className="bj-small">
              <Link to="/profile/integrations">Open Integrations settings</Link>
            </div>
          )}
          {hints.map((hint) => (
            <div key={hint} className="bj-muted bj-small">
              {hint}
            </div>
          ))}
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
          {signals.some((s) => s.aggregator === true) && (
            <div className="bj-muted bj-small">{AGGREGATOR_LEGEND}</div>
          )}
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
                  registryNoteFor={tabRegistryMatchNote}
                />
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
