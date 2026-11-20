import { useId } from "react";
import type { JobCard } from "../../lib/discoverTypes";
import { fitViewOf, rowReceipt, rowTrail } from "../../lib/discoverRow";
import { FailureState } from "../paper/StateViews";
import { TrailLine } from "../paper/TrailLine";
import { FitDetail, FitReading, FitScore, FitToggle } from "./FitBlock";
import { IdentityMeta, RowActions, postingBody, rowKindClass, useTrackPress, whatOf } from "./RowParts";
import "../../styles/discover.css";

// One search result as a full list row, in the order a person reads it: who and where (title, company and
// place, pay), then the fit score in a gutter on the left, what the posting says, the model's one line
// and what the score left out, the row's actions, and its trail as the last line (inline at every
// width, as a list-like surface's trails are). This is the row of a screen under 1280px; from there the
// list is compact rows beside a pane that shows one of them in full (CompactRow.tsx, DetailPane.tsx).
//
// A row is one of three kinds, by what it has to say:
//
//   scored        the gutter, the posting text, the model's line, "Why this score" and its detail
//   placeholder   the scoring model's reply could not be read: a hollow ladder that says "Placeholder",
//                 the posting text and the actions, with no number and nothing to open
//   unscored      past the scoring batch, or with a bin the engine does not have: one band -- identity
//                 and the words "Not scored" with the actions -- with no gutter, no model line and no detail
//
// The rows in the engine's top bin (Strong, which a person reads as "Good") are lifted onto a slightly
// lighter surface; the other bins carry only a class so a later rule can find them. The classes, the
// rule for which address may become a link, the posting's text and the actions are RowParts.tsx, shared
// with the other two drawings of a result.

export interface ResultRowProps {
  job: JobCard;
  /** The scoring strategy of the whole response: only "neutral_fallback" changes what a row says. */
  scoringStrategy: string;
  now: Date;
  /** The application a Track in this visit created, when it did. */
  trackedApplicationId: string | null;
  tracking: boolean;
  /** A failed Track, shown on this row with its own Retry. */
  trackError: string | null;
  onTrack: (job: JobCard) => void;
  timeZone?: string;
  /** "Why this score" is open on this row. Closed by default. */
  detailsOpen?: boolean;
  onToggleDetails?: () => void;
}

export function ResultRow({ job, scoringStrategy, now, trackedApplicationId, tracking, trackError, onTrack, timeZone, detailsOpen = false, onToggleDetails }: ResultRowProps) {
  const press = useTrackPress(job, tracking, trackedApplicationId, onTrack);
  const fit = fitViewOf(job, scoringStrategy);
  const what = whatOf(job);
  const detailId = useId();
  const open = fit.kind === "scored" && detailsOpen;
  const rowClass = ["bj-disc-row", rowKindClass(fit), open ? "bj-disc-row--open" : null].filter((name) => name !== null).join(" ");
  const body = postingBody(job, fit.kind);
  return (
    <li className={rowClass}>
      <div className="bj-disc-main">
        <h3 className="bj-disc-title">{job.title}</h3>
        <IdentityMeta job={job} />
      </div>
      <FitScore view={fit} />
      {body !== null && <div className="bj-disc-body">{body}</div>}
      <FitReading view={fit} />
      <RowActions job={job} trackedApplicationId={trackedApplicationId} tracking={tracking} press={press} />
      {fit.kind === "scored" && <FitToggle open={open} detailId={detailId} label={`Why this score for ${what}`} onToggle={() => onToggleDetails?.()} />}
      {fit.kind === "scored" && open && <FitDetail view={fit} id={detailId} />}
      {trackError !== null && (
        <div className="bj-disc-problem">
          <FailureState message={`Couldn't track ${what}: ${trackError}`} onRetry={press.track} />
        </div>
      )}
      <div className="bj-disc-trail">
        <TrailLine trail={rowTrail(job, now, timeZone)} receipt={rowReceipt(job, scoringStrategy)} receiptTitle={`Where this result came from: ${job.title}`} />
      </div>
    </li>
  );
}
