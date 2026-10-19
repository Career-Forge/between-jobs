import { Link } from "react-router-dom";
import { flaggedCount, isClaimWarning } from "../lib/claimWarnings";
import type { PrepareApplicationResult } from "../lib/generateTypes";
import { notScoredNote } from "../lib/scoreBreakdown";
import type { GenerationController } from "../lib/useGeneration";
import { generationTrail } from "../lib/workspaceTrails";
import { HonestFloor } from "./HonestFloor";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState, SetupRequiredState, type StateLink } from "./paper/StateViews";
import { SetupRequiredNotice } from "./SetupRequiredNotice";
import "../styles/workspace.css";

// Generate: runs the resume engine against this job and says what came of it. The state and the
// requests are the generation hook's (lib/useGeneration.ts); this draws them, and the page lights the
// next-step band from the same state.
//
// What each outcome looks like:
//   drafted      "Resume drafted", the generation's trail and receipt, the engine's cautions as neutral
//                outline callouts (a pinned entry that did not fully fit, anything else it said), and the
//                downloads. The score, the claim check and the export checks are the Review section's.
//   held back    the honest read (HonestFloor): the fit check declined to write a resume. A resume made
//                earlier is still downloadable, and the panel says the latest attempt declined.
//   nothing      "The engine didn't produce a resume for this job."
//   failed       a callout naming what could not be done, with a Retry unless the band carries
//                "Generate again" (`retryInBand`); a missing profile or key keeps its link instead.
// A result read back from storage (after a reload) says so, with no date: none came back with it.
//
// The Generate button and Download PDF are outlined here. When the next-step band carries the same step
// it is the page's one cyan, and the body's copy stays secondary (one action, one cyan, once).
//
// Per-bullet evidence is not shown: `evidence_fact_ids` is always empty today, so a per-bullet display
// would be decoration with nothing behind it.

// A pin conflict rides the same warnings list as everything else, told by the exact prefix the engine's
// allocator writes. It is broken out of the plain cautions because a pin is the person's own explicit
// choice failing to be fully honored.
function isPinWarning(w: string): boolean {
  return w.startsWith("pinned item");
}

// Where the score would be, when the engine that wrote the resume did not score it.
function NotScoredNote({ result }: { result: PrepareApplicationResult }) {
  const note = notScoredNote(result);
  return note === null ? null : <p className="bj-ws-caption">{note}</p>;
}

export interface GeneratePanelProps {
  applicationId: string;
  controller: GenerationController;
  /** What is already known to be missing before a run could work (no profile, no usable model key). */
  setupMissing?: { message: string; missing: readonly [StateLink, ...StateLink[]] } | null;
  /** The band carries "Generate again" for a failed run, so the failure here has no Retry of its own. */
  retryInBand?: boolean;
  /** The band carries the cover-letter choice, so it is not drawn twice. */
  coverLetterInBand?: boolean;
  /** An application was parked from the honest read: the page re-reads its status. */
  onParked?: () => void;
}

export function GeneratePanel({ applicationId, controller, setupMissing = null, retryInBand = false, coverLetterInBand = false, onParked }: GeneratePanelProps) {
  const { state, read, hasResume, hasCoverLetter, downloading, downloadError } = controller;
  const generating = state.kind === "generating";
  // The restore can land on a DECLINED latest attempt (the honest read) while an OLDER successful
  // generation's resume still exists and is downloadable: both are true at once, so a note says so
  // instead of letting it read as a contradiction.
  const showingDeclineWithAnOlderResume = hasResume && state.kind === "ready" && state.result.resume === null;

  return (
    <div className="bj-ws-generate">
      <p className="bj-ws-intro">Runs the resume engine against this job: a real model call, on the provider key you saved.</p>

      {setupMissing !== null ? (
        <SetupRequiredState message={setupMissing.message} missing={setupMissing.missing} />
      ) : (
        <>
          <div className="bj-actions">
            <button type="button" aria-disabled={generating ? true : undefined} onClick={() => void controller.generate()}>
              {generating ? "Generating..." : "Generate resume"}
            </button>
          </div>
          {!coverLetterInBand && (
            <label className="bj-ws-check bj-small">
              <input type="checkbox" checked={controller.wantCoverLetter} onChange={(e) => controller.setWantCoverLetter(e.target.checked)} disabled={generating} />
              Also generate a cover letter (a real, extra model call)
            </label>
          )}
        </>
      )}

      {read === "loading" && state.kind === "idle" && <LoadingState rows={2} rowHeight={48} label="Loading your last generation..." />}
      {read === "failed" && state.kind === "idle" && (
        <FailureState message="Couldn't read your last generation. A resume you made earlier is not affected." onRetry={controller.retryRead} />
      )}

      {generating && <LoadingState rows={2} rowHeight={48} label="Generating..." />}
      {state.kind === "error" && <FailureState message={state.message} onRetry={retryInBand ? undefined : () => void controller.generate()} />}
      {state.kind === "setup" && <SetupRequiredNotice notice={state.notice} />}

      {state.kind === "ready" && (
        <ResultView
          applicationId={applicationId}
          result={state.result}
          restored={state.restored}
          earlierResume={showingDeclineWithAnOlderResume}
          onGenerateAnyway={() => void controller.generate(true)}
          onParked={onParked}
        />
      )}

      {showingDeclineWithAnOlderResume && <p className="bj-ws-caption">From an earlier successful generation -- the latest attempt above declined.</p>}
      {hasResume && (
        <div className="bj-actions">
          <button type="button" aria-disabled={downloading === "resume" ? true : undefined} onClick={() => downloading === null && void controller.downloadResume()}>
            {downloading === "resume" ? "Compiling PDF..." : "Download PDF"}
          </button>
          {hasCoverLetter && (
            <button type="button" aria-disabled={downloading === "cover" ? true : undefined} onClick={() => downloading === null && void controller.downloadCoverLetter()}>
              {downloading === "cover" ? "Compiling PDF..." : "Download cover letter"}
            </button>
          )}
        </div>
      )}
      {downloadError && (
        <p className="bj-error" role="alert">
          {downloadError}
        </p>
      )}
    </div>
  );
}

function ResultView({
  applicationId,
  result,
  restored,
  earlierResume,
  onGenerateAnyway,
  onParked,
}: {
  applicationId: string;
  result: PrepareApplicationResult;
  restored: boolean;
  earlierResume: boolean;
  onGenerateAnyway: () => void;
  onParked?: () => void;
}) {
  const flagged = flaggedCount(result.warnings);
  const trail = generationTrail(result, { restored, flagged, earlierResume });

  if (result.resume === null) {
    if (!result.fit) {
      return <p className="bj-muted bj-small">The engine didn't produce a resume for this job.</p>;
    }
    // The gate's own decline reason is already folded into `warnings` as plain text; the honest read
    // supersedes it with the full reading, so the warnings list is deliberately skipped in this branch.
    return (
      <div className="bj-ws-result">
        <HonestFloor applicationId={applicationId} fit={result.fit} onGenerateAnyway={onGenerateAnyway} onParked={onParked} />
        <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />
      </div>
    );
  }

  const pinWarnings = result.warnings.filter(isPinWarning);
  const cautions = result.warnings.filter((w) => !isPinWarning(w) && !isClaimWarning(w));
  return (
    <div className="bj-ws-result">
      <h3 className="bj-ws-block-title">Resume drafted</h3>
      <p className="bj-small">
        Its score, claim check and export checks are in Review below.
        {restored && " Read back from your last stored generation: no time came back with it."}
      </p>
      <NotScoredNote result={result} />
      {pinWarnings.length > 0 && (
        <div className="bj-ws-callout" role="note">
          <p>Some mandatory entries didn't fully fit:</p>
          <ul className="bj-small">
            {pinWarnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
          <p className="bj-small">
            Every pinned entry is still included -- only its bullet count fell short. <Link to="/profile">Edit profile</Link> to unpin an entry or lower its minimum.
          </p>
        </div>
      )}
      {cautions.length > 0 && (
        <ul className="bj-ws-cautions" aria-label="Cautions from the engine">
          {cautions.map((w) => (
            <li key={w} className="bj-ws-callout bj-small">
              {w}
            </li>
          ))}
        </ul>
      )}
      <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />
    </div>
  );
}
