import { SegmentedBar } from "../paper/SegmentedBar";
import { FitLadder } from "../paper/ScoreBlock";
import { FIT_LEVELS, type FitView } from "../../lib/discoverRow";
import "../../styles/discover.css";

// The fit block of one Discover row (what the engine made of the model's reading of the posting).
//
//   scored       the four-cell ladder with "Good -- 61 / 100" and the "engine" mark, the model's one
//                line, a bar out of 100 for every part that was applied, what was not applied and
//                why, the lowest part, and where the model read the job to be (a match, a mismatch
//                or "can't tell")
//   placeholder  the scoring model's reply could not be read: a hollow ladder that says
//                "Placeholder", with no number and no bars
//   unscored     a row past the scoring batch: no block at all, only the words "Not scored"
//
// Never a gauge, ring or radar. A part that was dropped is named, never drawn as 0.

export function FitBlock({ view }: { view: FitView }) {
  if (view.kind === "unscored") {
    return (
      <div className="bj-fit bj-fit--unscored">
        <p className="bj-fit-unscored">Not scored</p>
      </div>
    );
  }
  if (view.kind === "placeholder") {
    return (
      <div className="bj-fit">
        <FitLadder levels={FIT_LEVELS} level={null} placeholder />
      </div>
    );
  }
  return (
    <div className="bj-fit">
      <FitLadder levels={FIT_LEVELS} level={view.level} score={view.score100} />
      {view.oneLiner !== "" && <p className="bj-fit-oneliner">{view.oneLiner}</p>}
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
