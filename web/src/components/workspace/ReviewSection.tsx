import { flaggedCount, isClaimWarning } from "../../lib/claimWarnings";
import { latestAttempt } from "../../lib/scoreBreakdown";
import type { GenerationController } from "../../lib/useGeneration";
import { SECTION_IDS } from "../../lib/workspaceSections";
import { claimTrail, scoreTrail } from "../../lib/workspaceTrails";
import { ClaimWarningsBanner } from "../ClaimWarningsBanner";
import { ExportChecklist } from "../ExportChecklist";
import { GapInterview } from "../GapInterview";
import { DocumentBlock } from "../paper/DocumentLayout";
import { SectionChapter } from "../paper/SectionChapter";
import { LoadingState } from "../paper/StateViews";
import { ScoreBreakdown } from "../ScoreBreakdown";
import { SubmitBlock, type SubmitBlockProps } from "./SubmitBlock";
import "../../styles/workspace.css";

// Review: what the checks found about the draft, and what is left for the person to read. It follows
// the generation, so what it shows is the latest attempt's:
//
//   a drafted result     the ATS score (engine ✦) with its five bars, the gap interview when the score
//                        is under 70, and the claim check; each with its own trail and receipt
//   no result to read    a resume exists but this page has no outcome for it to show: none is stored (it
//                        predates stored results), the latest attempt was held back, or the stored one
//                        could not be read (a failed read, or a run that did not finish dropped the one
//                        on screen). The three are worded apart. The export checks below read the
//                        resume itself, and the section says so rather than showing a score it has not
//   no resume            an empty state naming what will appear here
//
// The export checks are on request (they recompile the PDF) and are shown for any resume that exists.
// The last block is the person's own step: Between Jobs never submits an application, so the section
// ends with how to apply on the employer's site and a way to record it, while the status is Saved.

export interface ReviewSectionProps {
  applicationId: string;
  controller: GenerationController;
  submit: Omit<SubmitBlockProps, "visible"> | null;
}

export function ReviewSection({ applicationId, controller, submit }: ReviewSectionProps) {
  const { state, hasResume, read } = controller;
  const result = state.kind === "ready" ? state.result : null;
  const drafted = result !== null && result.resume !== null;
  const final = drafted ? latestAttempt(result.ats_attempts) : null;
  const claimWarnings = drafted ? result.warnings.filter(isClaimWarning) : [];

  return (
    <div className="bj-ws-section">
      <SectionChapter id={SECTION_IDS.review} title="Review">
        <p>What the checks found about your draft, and what is left for you to read before you send it.</p>
      </SectionChapter>

      {drafted && (
        <>
          {result.ats_attempts.length > 0 && (
            <DocumentBlock trail={scoreTrail(result).trail} receipt={scoreTrail(result).receipt} receiptTitle={scoreTrail(result).receiptTitle}>
              <ScoreBreakdown applicationId={applicationId} attempts={result.ats_attempts} />
            </DocumentBlock>
          )}
          {final !== null && final.overall_score < 70 && (
            <DocumentBlock>
              <GapInterview applicationId={applicationId} attempts={result.ats_attempts} onRegenerate={() => void controller.generate()} />
            </DocumentBlock>
          )}
          <DocumentBlock trail={claimTrail(flaggedCount(result.warnings)).trail} receipt={claimTrail(flaggedCount(result.warnings)).receipt} receiptTitle={claimTrail(0).receiptTitle}>
            <ClaimWarningsBanner warnings={claimWarnings} />
          </DocumentBlock>
        </>
      )}

      {!drafted && read === "loading" && state.kind === "idle" && (
        <DocumentBlock>
          <LoadingState rows={2} rowHeight={48} label="Loading your last generation..." />
        </DocumentBlock>
      )}

      {!drafted && hasResume && (read !== "loading" || state.kind !== "idle") && (
        <DocumentBlock>
          <p className="bj-muted bj-small">
            {result !== null
              ? "The score and claim check are for the latest attempt, which was held back, so they are not shown. The export checks below read your earlier resume."
              : controller.resultUnreadable
                ? "This page couldn't show the score or claim check for your resume this time, so it can't say whether any are stored. The export checks below read the resume itself."
                : "This page has no stored score or claim check for your resume. The export checks below read the resume itself."}
          </p>
        </DocumentBlock>
      )}

      {!hasResume && read !== "loading" && state.kind !== "generating" && (
        <DocumentBlock>
          <div className="bj-state bj-state--empty">
            <h3 className="bj-state-heading">Nothing to review yet</h3>
            <div className="bj-state-body">
              <p>Once a resume is drafted, its ATS score, its claim check and its export checks appear here.</p>
            </div>
          </div>
        </DocumentBlock>
      )}

      {hasResume && (
        <DocumentBlock>
          <ExportChecklist checklist={controller.checklist} onRun={() => void controller.loadChecklist()} />
        </DocumentBlock>
      )}

      {submit !== null && (
        <DocumentBlock>
          <SubmitBlock {...submit} visible={hasResume} />
        </DocumentBlock>
      )}
    </div>
  );
}
