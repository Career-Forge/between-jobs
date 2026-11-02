import { useState } from "react";
import {
  arrange,
  arrangeAnnouncement,
  filterSummary,
  fitCounts,
  offeredFilters,
  offeredSorts,
  reordersByDate,
  resolveFilter,
  resolveSort,
  TOOLBAR_MIN_ROWS,
  type Arranged,
  type FitFilter,
  type SortMode,
} from "../../lib/discoverArrange";
import { rowsOf, searchNotesOf } from "../../lib/discoverFacts";
import type { DiscoverResponse, JobCard } from "../../lib/discoverTypes";
import { fitViewOf, isFallback } from "../../lib/discoverRow";
import { EmptyState } from "../paper/StateViews";
import { ResultRow } from "./ResultRow";
import { ResultsToolbar } from "./ResultsToolbar";
import { SearchStatus } from "./SearchStatus";
import "../../styles/discover.css";

// A finished search, as far as it came back. What arrived renders; what did not is named in its own
// words in the status block (lib/discoverFacts.ts searchNotesOf, SearchStatus.tsx), so a short list is
// never a silent one.
//
//   rows          the scored rows, then "More results": rows past the scoring batch, which have no
//                 score block at all and read "Not scored"
//   fallback      one gold notice above every row when the scoring model's reply could not be read:
//                 every bin reads "Placeholder" and no number is drawn
//   status        the failures, the dropped listings and the sources, above the rows
//   toolbar       from three rows, and only while it has something to offer: keep one fit, sort (when two
//                 sorts are on offer) and open every row's detail (ResultsToolbar.tsx)
//   no rows       the status block, then "No results". "Nothing matched this search" is said only when
//                 every provider answered and none of its listings was dropped; otherwise the body says
//                 only that no listings are left, and the status block says why
//
// The words about order are said per section, from what that section did: "newest first" only for a section
// that had at least two readable dates and so was put in date order (reordersByDate), and the returned-order
// words for a section that kept the order it came in. Newest is on offer when either section can be reordered,
// so one section's dates never speak for the other.
//
// A person's choices -- the fit, the sort, which details are open and the sentence last announced --
// are state of this view and live only in memory. They start fresh whenever a different search answer
// arrives (the page unmounts this view while a search runs, and the derived reset below covers an answer
// that replaces another in place), because a new search is a new question and "Strong" must not hide
// rows from it unasked. Tracking a row or saving a search keeps the same answer, so it keeps them.
//
// The band holds the next step (a search again, clear the filters); nothing here is cyan.

export const RESULTS_HEADING_ID = "bj-discover-results";

export const NONE_SCORED_NOTE = "None of these results were scored, so there is no fit to filter or sort by.";

// What the More results note says about its section's order: the search's own ("tier and recency") unless
// that section was put in date order.
const MORE_NOTE_RETURNED = "Beyond this search's scoring batch -- sorted by tier and recency, not fit.";
const MORE_NOTE_NEWEST = "Beyond this search's scoring batch -- not scored, newest first.";

// What the fallback notice says about the order of the rows it speaks for (the scored section): the search's
// own ("by source quality") unless that section was put in date order. Best fit is never offered under the
// fallback.
const FALLBACK_ORDER_RETURNED = "Rows are listed by source quality, not by fit.";
const FALLBACK_ORDER_NEWEST = "Rows are listed newest first, not by fit.";

type Section = "scored" | "more";

export interface ResultsViewProps {
  result: DiscoverResponse;
  now: Date;
  tracking: ReadonlySet<string>;
  tracked: Readonly<Record<string, string>>;
  trackErrors: Readonly<Record<string, string>>;
  onTrack: (job: JobCard) => void;
  timeZone?: string;
}

export function ResultsView({ result, now, tracking, tracked, trackErrors, onTrack, timeZone }: ResultsViewProps) {
  const [filter, setFilter] = useState<FitFilter>("all");
  const [sort, setSort] = useState<SortMode>("returned");
  // "Show score details": every row's "Why this score". A row's own press is an override of it, keyed by
  // section as well as address (one posting can appear in both lists, and opening one must not open the
  // other) and cleared when the switch is used, since the switch means every row.
  const [allOpen, setAllOpen] = useState(false);
  const [overrides, setOverrides] = useState<Readonly<Record<string, boolean>>>({});
  const [announcement, setAnnouncement] = useState("");
  // The derived-state reset: a different answer starts from All, As returned, details closed. Done while
  // rendering, so the old choices are never drawn against the new rows.
  const [seen, setSeen] = useState(result);
  if (seen !== result) {
    setSeen(result);
    setFilter("all");
    setSort("returned");
    setAllOpen(false);
    setOverrides({});
    setAnnouncement("");
  }

  const scored = rowsOf(result.scored);
  const more = rowsOf(result.more);
  const notes = searchNotesOf(result);
  const strategy = typeof result.scoring_strategy === "string" ? result.scoring_strategy : "";

  if (scored.length + more.length === 0) {
    return (
      <div className="bj-disc-results">
        <SearchStatus notes={notes} showSources={false} />
        <EmptyState heading="No results">
          <p>{notes.warnings.length === 0 && notes.dropped === 0 ? "Nothing matched this search." : "No listings are left to show."}</p>
        </EmptyState>
      </div>
    );
  }

  const total = scored.length + more.length;
  const tiersMeaningful = !isFallback(strategy);
  const counts = fitCounts([...scored, ...more]);
  const filters = offeredFilters(counts, tiersMeaningful);
  // A choice that is not on offer is All or As returned, whatever was chosen before.
  const activeFilter = resolveFilter(filter, filters);
  // The sorts on offer are those that could change the rows a fit choice keeps: with only unscored rows kept
  // there is nothing for Best fit to rank, and with fewer than two dated rows in a section Newest could not
  // move anything in it.
  const keptBy = (choice: FitFilter) => arrange({ scored, more, filter: choice, sort: "returned", timeZone, now });
  const sortsOf = (kept: Arranged) => offeredSorts({ tiersMeaningful, scoredRows: kept.scored, moreRows: kept.more, timeZone, now });
  const kept = keptBy(activeFilter);
  const sorts = sortsOf(kept);
  const activeSort = resolveSort(sort, sorts);
  const arranged = arrange({ scored, more, filter: activeFilter, sort: activeSort, timeZone, now });
  const shown = arranged.scored.length + arranged.more.length;
  // Each section's order, said from what it did: Newest puts a section in date order only when it holds two
  // readable dates, and a section that kept its order keeps the returned-order words.
  const scoredByDate = activeSort === "newest" && reordersByDate(kept.scored, timeZone, now);
  const moreByDate = activeSort === "newest" && reordersByDate(kept.more, timeZone, now);
  // A tier choice leaves the unscored rows out; the count line says how many.
  const hiddenUnscored = activeFilter !== "all" && activeFilter !== "unscored" ? counts.unscored : 0;
  const showDetailsSwitch = [...arranged.scored, ...arranged.more].some((job) => fitViewOf(job, strategy).kind === "scored");

  // What is stored is what is shown. A press stores the choice even when it is the one already showing, so
  // a choice the page had quietly set aside is never left behind to come back unchosen later; the press is
  // announced only when it changed what is shown.
  const chooseFilter = (next: FitFilter) => {
    const keptByNext = keptBy(next);
    // The sort that stays when this fit is chosen: the one shown, or As returned when the fit leaves it off
    // offer. It is stored now, so a sort the fit hid is not brought back unchosen by the next fit.
    const nextSort = resolveSort(activeSort, sortsOf(keptByNext));
    setFilter(next);
    setSort(nextSort);
    if (next === activeFilter && nextSort === activeSort) return;
    setAnnouncement(
      arrangeAnnouncement({
        kind: "filter",
        filter: next,
        shown: keptByNext.scored.length + keptByNext.more.length,
        total,
        hiddenUnscored: next !== "all" && next !== "unscored" ? counts.unscored : 0,
        sort: nextSort === activeSort ? undefined : nextSort,
      }),
    );
  };
  const chooseSort = (next: SortMode) => {
    setSort(next);
    if (next !== activeSort) setAnnouncement(arrangeAnnouncement({ kind: "sort", sort: next }));
  };
  const chooseAllOpen = (open: boolean) => {
    setAllOpen(open);
    setOverrides({});
  };

  const row = (section: Section) => (job: JobCard) => {
    const key = `${section}:${job.apply_url}`;
    const open = overrides[key] ?? allOpen;
    return (
      <ResultRow
        key={job.apply_url}
        job={job}
        scoringStrategy={strategy}
        now={now}
        trackedApplicationId={tracked[job.apply_url] ?? null}
        tracking={tracking.has(job.apply_url)}
        trackError={trackErrors[job.apply_url] ?? null}
        onTrack={onTrack}
        timeZone={timeZone}
        detailsOpen={open}
        onToggleDetails={() => setOverrides((current) => ({ ...current, [key]: !open }))}
      />
    );
  };

  return (
    <section className="bj-disc-results" aria-labelledby={RESULTS_HEADING_ID}>
      <h2 className="bj-disc-heading" id={RESULTS_HEADING_ID} tabIndex={-1}>
        Results
      </h2>
      {isFallback(strategy) && scored.length > 0 && (
        <p className="bj-disc-fallback">
          <span className="bj-trail-glyph bj-trail-glyph--gold" aria-hidden="true">
            {"◆"}
          </span>{" "}
          Your model's reply couldn't be read, so these scores are placeholders. {scoredByDate ? FALLBACK_ORDER_NEWEST : FALLBACK_ORDER_RETURNED}
        </p>
      )}
      <SearchStatus notes={notes} showSources />
      {total >= TOOLBAR_MIN_ROWS && (
        <ResultsToolbar
          counts={counts}
          offeredFilters={filters}
          filter={activeFilter}
          onFilter={chooseFilter}
          offeredSorts={sorts}
          sort={activeSort}
          onSort={chooseSort}
          allOpen={allOpen}
          onAllOpen={chooseAllOpen}
          showDetailsSwitch={showDetailsSwitch}
          countLine={filterSummary(activeFilter, shown, total, hiddenUnscored)}
          announcement={announcement}
        />
      )}
      {scored.length === 0 && more.length > 0 && !isFallback(strategy) && <p className="bj-disc-note">{NONE_SCORED_NOTE}</p>}
      {arranged.scored.length > 0 && <ul className="bj-disc-list">{arranged.scored.map(row("scored"))}</ul>}
      {arranged.more.length > 0 && (
        <>
          <h2 className="bj-disc-heading bj-disc-heading--more">More results</h2>
          <p className="bj-disc-note">{moreByDate ? MORE_NOTE_NEWEST : MORE_NOTE_RETURNED}</p>
          <ul className="bj-disc-list">{arranged.more.map(row("more"))}</ul>
        </>
      )}
    </section>
  );
}
