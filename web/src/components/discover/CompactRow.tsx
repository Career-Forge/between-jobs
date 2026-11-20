import type { JobCard } from "../../lib/discoverTypes";
import { fitViewOf } from "../../lib/discoverRow";
import { FailureState } from "../paper/StateViews";
import { FitReading, FitScore } from "./FitBlock";
import { IdentityMeta, RowActions, rowKindClass, useTrackPress, whatOf } from "./RowParts";
import "../../styles/discover.css";

// One search result as a compact row, for a screen of 1280px and wider, where the list sits beside a pane
// that shows one row in full (DetailPane.tsx). The row holds what is needed to choose between jobs and to
// act on one: the score gutter (the number, the grade word and the ladder), who and where, and the same
// actions the full row has. The posting's text, the model's line, "Why this score" and the trail are not
// here: the pane draws them, opened out, for the row that is chosen.
//
// The three kinds of row say what they say on the full row. A placeholder has the hollow ladder that reads
// "Placeholder" and no number. A row past the scoring batch (or with a bin the engine does not have) has
// no gutter and reads "Not scored", so a row with no score is never mistaken for one with nothing to say.
//
// Choosing a row is a real button: the job's title. It is pressed (aria-pressed) on the row the pane is
// showing and names the pane it controls, and the shared selection style fills it, so which row is on
// show is said in words to a screen reader and by the fill to the eye. Its name starts with the words on
// show and adds the company, because two rows can carry the same title ("Data Engineer at Acme, show
// details"). Pressing it moves nothing: focus stays on the title, and the pane beside the list changes.
// Every row's controls are in the tab order, in reading order; there are no arrow keys to learn.
//
// A failed Track is shown on the row with its own Retry and announced from here. The pane shows the same
// failure for the row it holds without announcing it again.

export interface CompactRowProps {
  job: JobCard;
  /** The scoring strategy of the whole response: only "neutral_fallback" changes what a row says. */
  scoringStrategy: string;
  /** The application a Track in this visit created, when it did. */
  trackedApplicationId: string | null;
  tracking: boolean;
  /** A failed Track, shown on this row with its own Retry. */
  trackError: string | null;
  onTrack: (job: JobCard) => void;
  /** The pane is showing this row. */
  selected: boolean;
  /** The id of the pane, which the title button controls. */
  paneId: string;
  onSelect: () => void;
}

export function CompactRow({ job, scoringStrategy, trackedApplicationId, tracking, trackError, onTrack, selected, paneId, onSelect }: CompactRowProps) {
  const press = useTrackPress(job, tracking, trackedApplicationId, onTrack);
  const fit = fitViewOf(job, scoringStrategy);
  const what = whatOf(job);
  return (
    <li className={`bj-disc-row bj-disc-row--compact ${rowKindClass(fit)}`}>
      <div className="bj-disc-main">
        <h3 className="bj-disc-title">
          <button type="button" className="bj-disc-select bj-selectable" aria-pressed={selected} aria-controls={paneId} aria-label={`${what}, show details`} onClick={onSelect}>
            {job.title}
          </button>
        </h3>
        <IdentityMeta job={job} />
      </div>
      <FitScore view={fit} />
      {fit.kind === "unscored" && <FitReading view={fit} />}
      <RowActions job={job} trackedApplicationId={trackedApplicationId} tracking={tracking} press={press} />
      {trackError !== null && (
        <div className="bj-disc-problem">
          <FailureState message={`Couldn't track ${what}: ${trackError}`} onRetry={press.track} />
        </div>
      )}
    </li>
  );
}
