import { useState } from "react";
import { apiFetch } from "../lib/api";
import type { AtsAttempt } from "../lib/generateTypes";
import type { ResumeDocument } from "../lib/headerComposerTypes";
import {
  adjustedOverallScore,
  barState,
  companyAlignmentCaption,
  experienceManagementHeadroom,
  experienceYearsCaption,
  hardReqCapIsBinding,
  hardReqScoreForMissingCount,
  nextDealbreakerGain,
  parseGaps,
  quantificationCaption,
  scoreRows,
  whiteTextSpamPenaltyApplied,
} from "../lib/scoreBreakdown";

// Headroom Ledger (honest-score-surfaces.md, S1 + S2 + S3) -- Reality Check
// artboard 1 ("Verdict + headroom"). S1's bars/checklist read data the API
// already sends; S2 adds the dealbreaker-assertion action itself -- "this is
// actually true for me" -> PATCH /resume-documents/{id}/assertions -> an
// honestly-labeled ADJUSTED total shown immediately, without pretending the
// stored ats_attempts changed. The real score only updates on the next
// generation, which is what actually re-scores against the assertion
// server-side (ats_score.apply_dealbreaker_assertions). S3 widens the engine
// return shape (additive, formula untouched) and surfaces two more honest
// details: the one EXACT discrete lever behind experience_quality (the
// management-bonus dashed segment -- not a fuzzy estimate, a computed exact
// value) and the per-cluster arithmetic behind semantic_coverage, disclosed
// on demand rather than always-open (progressive disclosure).

function barStateLabel(value: number, max: number): string {
  const state = barState(value, max);
  return `${value} / ${max} · ${state}`;
}

// Exported for HonestFloor.tsx's own dimension bars (ForgeScore chart-UI
// pass) -- same bar, same visual language, a different data source (a
// pre-generation 0-10 LLM read instead of a post-generation deterministic
// formula). `detail` is a plain explanatory caption (NOT an exact,
// actionable lever like `headroomLabel` -- kept as a separate prop so the
// two meanings never blur together on one bar).
export function ScoreBar({
  label,
  value,
  max,
  headroom = 0,
  headroomLabel,
  detail,
}: {
  label: string;
  value: number;
  max: number;
  headroom?: number;
  headroomLabel?: string;
  detail?: string;
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const headroomPct = max > 0 ? Math.min((headroom / max) * 100, 100 - pct) : 0;
  return (
    <div className="bj-score-row">
      <div className="bj-score-row-header bj-small">
        <span>{label}</span>
        <span className="bj-muted">{barStateLabel(value, max)}</span>
      </div>
      <div className="bj-score-bar-track">
        <div className="bj-score-bar-fill" style={{ width: `${pct}%` }} />
        {headroomPct > 0 && (
          <div
            className="bj-score-bar-headroom"
            style={{ left: `${pct}%`, width: `${headroomPct}%` }}
          />
        )}
      </div>
      {headroom > 0 && headroomLabel && (
        <div className="bj-muted bj-small">
          +{headroom} exact -- {headroomLabel}
        </div>
      )}
      {detail && <div className="bj-muted bj-small">{detail}</div>}
    </div>
  );
}

type AssertState =
  | { kind: "idle" }
  | { kind: "saving"; requirement: string }
  | { kind: "error"; requirement: string; message: string };

export function ScoreBreakdown({
  applicationId,
  attempts,
}: {
  applicationId: string;
  attempts: AtsAttempt[];
}) {
  const [assertState, setAssertState] = useState<AssertState>({ kind: "idle" });
  const [assertedThisSession, setAssertedThisSession] = useState<string[]>([]);
  const [clustersOpen, setClustersOpen] = useState(false);

  if (attempts.length === 0) return null;
  const final = attempts[attempts.length - 1];
  const first = attempts[0];
  const regenerated = attempts.length > 1;
  const { missingDealbreakers, weakRequirements } = parseGaps(final.gaps);
  const stillMissingCount = Math.max(missingDealbreakers.length - assertedThisSession.length, 0);
  const gain = nextDealbreakerGain(stillMissingCount);
  const capBinding = hardReqCapIsBinding(final.breakdown, final.overall_score);
  const managementHeadroom = experienceManagementHeadroom(final.detail?.experience);
  const clusters = final.detail?.clusters ?? [];
  const experienceCaption = experienceYearsCaption(final.detail?.experience);
  const quantCaption = quantificationCaption(final.detail?.quantification);
  const companyCaption = companyAlignmentCaption(final.detail?.company_alignment);
  const spamPenaltyApplied = whiteTextSpamPenaltyApplied(final.detail?.company_alignment);

  async function assertDealbreaker(requirement: string) {
    setAssertState({ kind: "saving", requirement });
    try {
      const document = await apiFetch<ResumeDocument>(
        `/resume-documents/mine?application_id=${applicationId}`,
      );
      const existing = document.assertions ?? [];
      const updated = existing.includes(requirement) ? existing : [...existing, requirement];
      await apiFetch(`/resume-documents/${document.id}/assertions`, {
        method: "PATCH",
        body: JSON.stringify({ assertions: updated }),
      });
      setAssertedThisSession((prev) => (prev.includes(requirement) ? prev : [...prev, requirement]));
      setAssertState({ kind: "idle" });
    } catch (e) {
      setAssertState({
        kind: "error",
        requirement,
        message: e instanceof Error ? e.message : "Failed to save",
      });
    }
  }

  const adjustedHardReq = hardReqScoreForMissingCount(stillMissingCount);
  const adjustedTotal =
    assertedThisSession.length > 0
      ? adjustedOverallScore(final.breakdown, adjustedHardReq)
      : null;

  return (
    <div className="bj-score-breakdown">
      <div className="bj-score-header">
        <span className="bj-badge-gold">{final.rating}</span>
        {regenerated && (
          <span className="bj-muted bj-small">
            {first.overall_score} → {final.overall_score}
          </span>
        )}
      </div>
      <div className="bj-score-numeral-row">
        <span className="bj-score-numeral">{final.overall_score}</span>
        <span className="bj-muted bj-small">/ 100 · {final.confidence} confidence</span>
      </div>
      {adjustedTotal !== null && (
        <div className="bj-score-adjusted bj-small">
          Adjusted: {final.overall_score} → {adjustedTotal} · exact -- confirmed once you
          generate again.
        </div>
      )}
      <p className="bj-muted bj-small">
        An honest number, not a verdict -- here's exactly what's movable and what isn't.
      </p>

      <div className="bj-score-rows">
        {scoreRows(final.breakdown).map((row) => (
          <ScoreBar
            key={row.key}
            label={row.label}
            value={row.value}
            max={row.max}
            headroom={row.key === "experience_quality" ? managementHeadroom : 0}
            headroomLabel={
              row.key === "experience_quality"
                ? "crediting management/leadership experience"
                : undefined
            }
            detail={
              row.key === "experience_quality"
                ? experienceCaption
                : row.key === "quantification"
                  ? quantCaption
                  : row.key === "company_and_structure"
                    ? companyCaption
                    : undefined
            }
          />
        ))}
      </div>

      {spamPenaltyApplied && (
        <div className="bj-danger-callout bj-small">
          Company alignment took an 8-point penalty -- the extraction flagged likely
          keyword-stuffing (e.g. hidden/white-on-white text) in this generated resume.
        </div>
      )}

      {clusters.length > 0 && (
        <div className="bj-score-disclosure">
          <button
            type="button"
            className="bj-score-disclosure-toggle"
            onClick={() => setClustersOpen((open) => !open)}
            aria-expanded={clustersOpen}
          >
            {clustersOpen ? "Hide" : "Show"} skill-cluster breakdown ({clusters.length})
          </button>
          {clustersOpen && (
            <ul className="bj-score-cluster-list">
              {clusters.map((c, i) => (
                <li key={`${c.cluster_name}-${i}`} className="bj-score-cluster-row">
                  <span className="bj-small">{c.cluster_name}</span>
                  <span className="bj-muted bj-small">
                    {Math.round(c.coverage_score)}% coverage · {Math.round(c.points_earned)} /{" "}
                    {c.points_possible} pts
                    {c.years_ratio < 1 && ` · ${Math.round(c.years_ratio * 100)}% of years matched`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {capBinding && (
        <div className="bj-score-cap-callout bj-small">
          Hard requirements below 8/15 cap the total at 65, regardless of everything
          else -- that's what's holding this score down, not the resume's quality.
        </div>
      )}

      {missingDealbreakers.length > 0 && (
        <ul className="bj-checklist">
          {missingDealbreakers.map((req) => {
            const asserted = assertedThisSession.includes(req);
            const saving = assertState.kind === "saving" && assertState.requirement === req;
            // Every still-missing item shows the SAME gain, honestly -- the
            // formula only counts how many are missing, not which specific
            // one clears next, so clearing any one of them moves the score
            // by the same amount.
            return (
              <li key={req} className="bj-checklist-item">
                <span className={asserted ? "bj-badge-emerald" : "bj-badge-danger"}>
                  {asserted ? "Asserted" : "Missing dealbreaker"}
                </span>
                <div>
                  <div className="bj-checklist-label">{req}</div>
                  {asserted ? (
                    <div className="bj-muted bj-small">
                      Saved -- this becomes part of your real score next generation.
                    </div>
                  ) : (
                    <>
                      <div className="bj-muted bj-small">
                        This is about you, not your resume -- if it's actually true for
                        you, saying so is worth
                        {gain > 0 ? ` +${gain} exact` : " something once enough are cleared"}.
                      </div>
                      <div className="bj-actions">
                        <button onClick={() => void assertDealbreaker(req)} disabled={saving}>
                          {saving ? "Saving..." : "This works for me"}
                        </button>
                      </div>
                      {assertState.kind === "error" && assertState.requirement === req && (
                        <div className="bj-error bj-small">{assertState.message}</div>
                      )}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {weakRequirements.length > 0 && (
        <ul className="bj-checklist">
          {weakRequirements.map((req) => (
            <li key={req} className="bj-checklist-item">
              <span className="bj-badge-muted">Weak coverage</span>
              <div className="bj-checklist-label">{req}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
