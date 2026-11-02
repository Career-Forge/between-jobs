import { SegmentedBar } from "../paper/SegmentedBar";
import { FitLadder } from "../paper/ScoreBlock";
import { FIT_LEVELS, fitPreviewOf, type FitView, type ScoredFit } from "../../lib/discoverRow";
import { isScoreWithin } from "../../lib/scoreRange";
import "../../styles/discover.css";

// The parts of one Discover row's fit (what the engine made of the model's reading of the posting). A row
// lays them out itself (ResultRow.tsx) so the score can lead the row on its own and the rest can sit
// where the reader's eye goes next.
//
//   FitScore     the gutter: the number out of 100 and the tier word, the four-cell ladder and the
//                "engine" mark. A placeholder row (the scoring model's reply could not be read) has the
//                hollow ladder that says "Placeholder" and no number; a row past the scoring batch, or
//                one whose bin the ladder does not know, has no gutter at all
//   FitReading   the model's one line, and one line of what the score left out (the lowest part with
//                its number, how many parts were not applied, a location that does not match), so a
//                closed row never reads as complete. A row past the scoring batch (or one whose bin the
//                ladder does not know) says only "Not scored"
//   FitToggle    "Why this score": opens the detail of that row
//   FitDetail    a bar out of 100 for every part that was applied, what was not applied and why, the
//                lowest part, and where the model read the job to be (a match, a mismatch or "can't
//                tell")
//
// Never a gauge, ring or radar. A part that was dropped is named, never drawn as 0. The tier word and
// the number are drawn for the eye and hidden from assistive technology, because the ladder's label
// ("Fit: Strong -- 88 / 100") says the same words once.

export function FitScore({ view }: { view: FitView }) {
  if (view.kind === "unscored") return null;
  if (view.kind === "placeholder") {
    return (
      <div className="bj-disc-score">
        <FitLadder levels={FIT_LEVELS} level={null} placeholder className="bj-disc-ladder" />
      </div>
    );
  }
  // A scored view always has a bin the ladder knows (fitViewOf makes any other bin an unscored row, which
  // has no gutter). A number that is not a score from 0 to 100 is not printed, here or in the ladder's
  // label: the tier word and the ladder stand alone, so what a sighted reader sees is what a screen
  // reader is told.
  const hasNumber = isScoreWithin(view.score100);
  return (
    <div className="bj-disc-score">
      <div className="bj-disc-score-text" aria-hidden="true">
        {hasNumber && (
          <span className="bj-disc-score-line">
            <span className="bj-disc-score-num">{view.score100}</span> <span className="bj-disc-score-of">/ 100</span>
          </span>
        )}
        <span className="bj-disc-score-tier">{view.level}</span>
      </div>
      <FitLadder levels={FIT_LEVELS} level={view.level} score={view.score100} className="bj-disc-ladder" showWords={false} />
    </div>
  );
}

export function FitReading({ view }: { view: FitView }) {
  if (view.kind === "unscored") {
    return (
      <div className="bj-fit bj-fit--unscored">
        <p className="bj-fit-unscored">Not scored</p>
      </div>
    );
  }
  if (view.kind === "placeholder") return null;
  const preview = fitPreviewOf(view);
  if (view.oneLiner === "" && preview === null) return null;
  return (
    <div className="bj-fit">
      {view.oneLiner !== "" && <p className="bj-fit-oneliner">{view.oneLiner}</p>}
      {preview !== null && <p className="bj-fit-preview">{preview}</p>}
    </div>
  );
}

export interface FitToggleProps {
  open: boolean;
  /** The id of the detail this opens, named only while it is there to be named. */
  detailId: string;
  /** What this row is, for a name of its own: "Why this score for Data Engineer at Acme". */
  label: string;
  onToggle: () => void;
}

export function FitToggle({ open, detailId, label, onToggle }: FitToggleProps) {
  return (
    <button type="button" className="bj-quiet bj-fit-toggle" aria-expanded={open} aria-controls={open ? detailId : undefined} aria-label={label} onClick={onToggle}>
      Why this score <span aria-hidden="true">{open ? "⌃" : "›"}</span>
    </button>
  );
}

export function FitDetail({ view, id }: { view: ScoredFit; id: string }) {
  return (
    <div className="bj-fit-detail" id={id}>
      {view.bars.length > 0 && (
        <div className="bj-fit-bars">
          {view.bars.map((bar) => (
            <SegmentedBar key={bar.key} label={bar.label} value={bar.value} max={100} detail={bar.detail} />
          ))}
        </div>
      )}
      <div className="bj-fit-notes">
        {view.notApplied.map((note) => (
          <p key={note}>{note}</p>
        ))}
        {view.lowest !== null && <p>Lowest part: {view.lowest}</p>}
        <p>{view.location.words}</p>
      </div>
    </div>
  );
}
