import { useState } from "react";
import { rowsOf, searchNotesOf } from "../../lib/discoverFacts";
import type { DiscoverResponse, JobCard } from "../../lib/discoverTypes";
import { isFallback } from "../../lib/discoverRow";
import { EmptyState } from "../paper/StateViews";
import { ResultRow } from "./ResultRow";
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
//   no rows       the status block, then "No results". "Nothing matched this search" is said only when
//                 every provider answered and none of its listings was dropped; otherwise the body says
//                 only that no listings are left, and the status block says why
//
// The band holds the next step (a search again, clear the filters); nothing here is cyan.

export const RESULTS_HEADING_ID = "bj-discover-results";

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
  const scored = rowsOf(result.scored);
  const more = rowsOf(result.more);
  const notes = searchNotesOf(result);
  const strategy = typeof result.scoring_strategy === "string" ? result.scoring_strategy : "";
  // Which rows have "Why this score" open. Held here, in memory only, and keyed by section as well as
  // address: one posting can appear in both lists, and opening one must not open the other.
  const [opened, setOpened] = useState<Readonly<Record<string, boolean>>>({});

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

  const row = (section: "scored" | "more") => (job: JobCard) => {
    const key = `${section}:${job.apply_url}`;
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
        detailsOpen={opened[key] ?? false}
        onToggleDetails={() => setOpened((current) => ({ ...current, [key]: !(current[key] ?? false) }))}
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
      {scored.length > 0 && <ul className="bj-disc-list">{scored.map(row("scored"))}</ul>}
      {more.length > 0 && (
        <>
          <h2 className="bj-disc-heading bj-disc-heading--more">More results</h2>
          <p className="bj-disc-note">Beyond this search's scoring batch {"--"} sorted by tier and recency, not fit.</p>
          <ul className="bj-disc-list">{more.map(row("more"))}</ul>
        </>
      )}
    </section>
  );
}
