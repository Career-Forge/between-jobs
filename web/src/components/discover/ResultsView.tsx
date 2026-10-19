import type { ReactNode } from "react";
import { parseWarning, providerName } from "../../lib/discover";
import { rowsOf, warningsOf } from "../../lib/discoverFacts";
import type { DiscoverResponse, JobCard } from "../../lib/discoverTypes";
import { isFallback } from "../../lib/discoverRow";
import { EmptyState } from "../paper/StateViews";
import { StaticTrailLine } from "../paper/TrailLine";
import { ResultRow } from "./ResultRow";
import "../../styles/discover.css";

// A finished search, as far as it came back. What arrived renders; what did not is named in its own
// words (each warning on its own line with a red mark, the listings the link check dropped), so a
// short list is never a silent one.
//
//   rows          the scored rows, then "More results": rows past the scoring batch, which have no
//                 score block at all and read "Not scored"
//   fallback      one gold notice above every row when the scoring model's reply could not be read:
//                 every bin reads "Placeholder" and no number is drawn
//   warnings      one line per warning, "✕ Brave Search couldn't complete that search · its message"
//   dropped       "3 dropped -- no longer live", when the link check dropped listings
//   no rows       "No results", with the warnings and the dropped count, no example rows. "Nothing matched
//                 this search" is said only when every provider answered and none of its listings was
//                 dropped; otherwise the body says only that no listings are left, and the warnings and
//                 the dropped count say why
//
// The band holds the next step (a search again, clear the filters); nothing here is cyan.

export const RESULTS_HEADING_ID = "bj-discover-results";

// "Brave Search couldn't complete that search · request failed (429)": a provider's failure says
// which provider and carries its own message after it; a note that names no provider is shown whole.
export function WarningLines({ warnings }: { warnings: readonly string[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="bj-disc-warnings" aria-label="What this search could not do">
      {warnings.map((warning, index) => {
        const line = parseWarning(warning);
        return (
          <li key={`${index}-${warning}`}>
            <StaticTrailLine
              trail={
                line.provider === null
                  ? { glyph: "failed", actor: line.message }
                  : { glyph: "failed", actor: `${providerName(line.provider)} couldn't complete that search`, basis: line.message }
              }
            />
          </li>
        );
      })}
    </ul>
  );
}

function DroppedNote({ count }: { count: number }): ReactNode {
  return count > 0 ? <p className="bj-disc-note">{count} dropped {"--"} no longer live</p> : null;
}

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
  const warnings = warningsOf(result);
  const dropped = typeof result.dead_removed === "number" && result.dead_removed > 0 ? result.dead_removed : 0;
  const strategy = typeof result.scoring_strategy === "string" ? result.scoring_strategy : "";

  if (scored.length + more.length === 0) {
    return (
      <div className="bj-disc-results">
        <WarningLines warnings={warnings} />
        <EmptyState heading="No results">
          <p>{warnings.length === 0 && dropped === 0 ? "Nothing matched this search." : "No listings are left to show."}</p>
          <DroppedNote count={dropped} />
        </EmptyState>
      </div>
    );
  }

  const row = (job: JobCard) => (
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
    />
  );

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
      <WarningLines warnings={warnings} />
      <DroppedNote count={dropped} />
      {scored.length > 0 && <ul className="bj-disc-list">{scored.map(row)}</ul>}
      {more.length > 0 && (
        <>
          <h2 className="bj-disc-heading bj-disc-heading--more">More results</h2>
          <p className="bj-disc-note">Beyond this search's scoring batch {"--"} sorted by tier and recency, not fit.</p>
          <ul className="bj-disc-list">{more.map(row)}</ul>
        </>
      )}
    </section>
  );
}
