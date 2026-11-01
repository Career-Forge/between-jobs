import { useState } from "react";
import {
  arrange,
  arrangeAnnouncement,
  filterSummary,
  fitCounts,
  offeredFilters,
  offeredSorts,
  resolveFilter,
  resolveSort,
  TOOLBAR_MIN_ROWS,
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
//   toolbar       from three rows: keep one fit, sort, and open every row's detail (ResultsToolbar.tsx)
//   no rows       the status block, then "No results". "Nothing matched this search" is said only when
//                 every provider answered and none of its listings was dropped; otherwise the body says
//                 only that no listings are left, and the status block says why
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

const MORE_NOTE: Readonly<Record<SortMode, string>> = {
  returned: "Beyond this search's scoring batch -- sorted by tier and recency, not fit.",
  fit: "Beyond this search's scoring batch -- sorted by tier and recency, not fit.",
  newest: "Beyond this search's scoring batch -- not scored, newest first.",
};

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
  const sorts = offeredSorts({ tiersMeaningful, scoredRows: scored });
  // A choice that is not on offer is All or As returned, whatever was chosen before.
  const activeFilter = resolveFilter(filter, filters);
  const activeSort = resolveSort(sort, sorts);
  const arranged = arrange({ scored, more, filter: activeFilter, sort: activeSort });
  const shown = arranged.scored.length + arranged.more.length;
  // A tier choice leaves the unscored rows out; the count line says how many.
  const hiddenUnscored = activeFilter !== "all" && activeFilter !== "unscored" ? counts.unscored : 0;
  const showDetailsSwitch = [...arranged.scored, ...arranged.more].some((job) => fitViewOf(job, strategy).kind === "scored");

  const chooseFilter = (next: FitFilter) => {
    const kept = arrange({ scored, more, filter: next, sort: activeSort });
    setFilter(next);
    setAnnouncement(
      arrangeAnnouncement({
        kind: "filter",
        filter: next,
        shown: kept.scored.length + kept.more.length,
        total,
        hiddenUnscored: next !== "all" && next !== "unscored" ? counts.unscored : 0,
      }),
    );
  };
  const chooseSort = (next: SortMode) => {
    setSort(next);
    setAnnouncement(arrangeAnnouncement({ kind: "sort", sort: next }));
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
          Your model's reply couldn't be read, so these scores are placeholders. Rows are listed by source quality, not by fit.
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
          <p className="bj-disc-note">{MORE_NOTE[activeSort]}</p>
          <ul className="bj-disc-list">{arranged.more.map(row("more"))}</ul>
        </>
      )}
    </section>
  );
}
