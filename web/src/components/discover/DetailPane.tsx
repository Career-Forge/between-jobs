import { useId } from "react";
import type { JobCard } from "../../lib/discoverTypes";
import { fitViewOf, rowReceipt, rowTrail } from "../../lib/discoverRow";
import { FailureState } from "../paper/StateViews";
import { TrailLine } from "../paper/TrailLine";
import { FitDetail, FitReading, FitScore } from "./FitBlock";
import { IdentityMeta, RowActions, postingBody, useTrackPress, whatOf } from "./RowParts";
import "../../styles/discover.css";

// The pane beside the list on a screen of 1280px and wider: the chosen row in full. It is a labelled
// region ("Selected job") that stays in view while the list scrolls, and scrolls inside itself when it is
// taller than the window. It holds everything the full row holds, with nothing folded away:
//
//   who and where, with the score gutter beside it
//   the actions the row has: View posting (only for an http(s) address) and Track, or Open workspace
//   the model's one line and the line of what the score left out
//   "Why this score": a bar for every part that was applied, what was not applied, the lowest part and
//   the model's reading of the location
//   the posting's own text, whole, and the sponsorship line
//   the trail line, with the receipt behind it
//
// What it says about a row is worked out by the same code as the row (lib/discoverRow.ts, FitBlock.tsx,
// RowParts.tsx), so the rules hold here too. A placeholder shows the hollow ladder that reads
// "Placeholder", the posting text and the actions, with no number, no model line and no detail. A row
// that was not scored reads "Not scored" and carries nothing of the response's own reading. The receipt
// names the fallback when the scoring fell back. The posting's words are drawn as text only.
//
// Choosing another row changes what is drawn and nothing else: focus is not moved and nothing is
// announced, because the pressed state of the row's title already says which row is on show. A failed
// Track for the row on show is drawn here with a Retry, without the alert role: the row itself carries
// the alert, so the failure is spoken once. With no row to show, the pane says so in one line.

export const PANE_LABEL = "Selected job";
export const PANE_EMPTY = "No job to show.";

export interface DetailPaneProps {
  /** The id the rows' title buttons name as the thing they control. */
  id: string;
  /** The row on show, with the key that names it, or null when no row is on show. */
  selected: { key: string; job: JobCard } | null;
  /** The scoring strategy of the whole response: only "neutral_fallback" changes what is said. */
  scoringStrategy: string;
  now: Date;
  /** The application a Track in this visit created for the row on show, when it did. */
  trackedApplicationId: string | null;
  tracking: boolean;
  trackError: string | null;
  onTrack: (job: JobCard) => void;
  timeZone?: string;
}

export function DetailPane({ id, selected, ...rest }: DetailPaneProps) {
  const labelId = useId();
  return (
    <section className="bj-disc-pane" id={id} aria-labelledby={labelId}>
      <h2 className="bj-disc-pane-label" id={labelId}>
        {PANE_LABEL}
      </h2>
      {/* Keyed by the row, so an open receipt or a pressed Track of one job is not carried over to the next. */}
      {selected === null ? <p className="bj-disc-note">{PANE_EMPTY}</p> : <PaneJob key={selected.key} job={selected.job} {...rest} />}
    </section>
  );
}

function PaneJob({ job, scoringStrategy, now, trackedApplicationId, tracking, trackError, onTrack, timeZone }: Omit<DetailPaneProps, "id" | "selected"> & { job: JobCard }) {
  const press = useTrackPress(job, tracking, trackedApplicationId, onTrack);
  const fit = fitViewOf(job, scoringStrategy);
  const what = whatOf(job);
  const detailId = useId();
  const body = postingBody(job, fit.kind);
  return (
    <>
      <div className={`bj-disc-pane-head bj-disc-pane-head--${fit.kind}`}>
        <div className="bj-disc-main">
          <h3 className="bj-disc-title">{job.title}</h3>
          <IdentityMeta job={job} />
        </div>
        <FitScore view={fit} />
      </div>
      {fit.kind === "unscored" && <FitReading view={fit} />}
      <RowActions job={job} trackedApplicationId={trackedApplicationId} tracking={tracking} press={press} />
      {trackError !== null && (
        <div className="bj-disc-problem">
          <FailureState message={`Couldn't track ${what}: ${trackError}`} onRetry={press.track} announce={false} />
        </div>
      )}
      {fit.kind === "scored" && <FitReading view={fit} />}
      {fit.kind === "scored" && (
        <>
          <h4 className="bj-disc-pane-heading">Why this score</h4>
          <FitDetail view={fit} id={detailId} />
        </>
      )}
      {body !== null && (
        <>
          <h4 className="bj-disc-pane-heading">Posting</h4>
          <div className="bj-disc-body">{body}</div>
        </>
      )}
      <div className="bj-disc-trail">
        <TrailLine trail={rowTrail(job, now, timeZone)} receipt={rowReceipt(job, scoringStrategy)} receiptTitle={`Where this result came from: ${job.title}`} />
      </div>
    </>
  );
}
