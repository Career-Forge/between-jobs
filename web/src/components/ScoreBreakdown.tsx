import { useState, type ReactNode } from "react";
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
import { GlyphMark } from "./paper/TrailLine";
import { ScoreBlock } from "./paper/ScoreBlock";
import { SegmentedBar } from "./paper/SegmentedBar";
import { TRAIL_GLYPHS } from "../lib/trail";
import "../styles/workspace.css";

// The ATS score: an honest number and exactly what is movable and what is not. Code weights signals a
// model extracted ("engine ✦"); it is never a gauge, a ring or a bare number, and it always adds up:
// five segmented bars, one cell per point, whose values sum to the total.
//
//   Semantic coverage /35 · Experience quality /30 · Hard requirements /15 · Quantified impact /10 ·
//   Company alignment · structure /10
//
// Each bar says its value in words and takes its "near max" or "has headroom" from `barState()`, never
// by hand. The one exact, earned-by-a-fact lever behind experience quality (the management credit, a
// flat +5 gated on a yes or no) is drawn as hatched headroom after the filled cells, never solid. The
// captions come from the engine's own details, and carry the model mark only where their words say a
// model "judged" something.
//
// Dealbreakers the posting lists and the profile does not cover are rows the person answers: "This
// works for me" saves that it is true (PATCH /resume-documents/{id}/assertions) and the row reads
// "Asserted"; the total shown beside it is an ADJUSTED one, labeled exact and "confirmed once you
// generate again", because the stored score only changes on the next generation, which is what scores
// against the assertion. A cap or a penalty is a neutral outline note; a penalty that really was applied
// carries a red cross and its words.

// The plain horizontal bar the Interview practice panel and Discover still draw their own scores with.
// It is kept, unchanged, for them: the workspace's ATS score and the honest read use the segmented bars
// of components/paper/SegmentedBar.tsx instead. When those two screens move to the segmented bar this
// goes, with the rules it reads in app.css (.bj-score-row, .bj-score-bar-*).
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
        <span className="bj-muted">{`${value} / ${max} · ${barState(value, max)}`}</span>
      </div>
      <div className="bj-score-bar-track">
        <div className="bj-score-bar-fill" style={{ width: `${pct}%` }} />
        {headroomPct > 0 && <div className="bj-score-bar-headroom" style={{ left: `${pct}%`, width: `${headroomPct}%` }} />}
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

// A caption from the engine. A violet model mark goes only on one whose words say "judged".
function caption(text: string | undefined): ReactNode {
  if (text === undefined) return undefined;
  return /\bjudged\b/i.test(text) ? (
    <>
      <GlyphMark name="model" /> {text}
    </>
  ) : (
    text
  );
}

export function ScoreBreakdown({ applicationId, attempts }: { applicationId: string; attempts: AtsAttempt[] }) {
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
    if (assertState.kind === "saving") return;
    setAssertState({ kind: "saving", requirement });
    try {
      const document = await apiFetch<ResumeDocument>(`/resume-documents/mine?application_id=${applicationId}`);
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
  const adjustedTotal = assertedThisSession.length > 0 ? adjustedOverallScore(final.breakdown, adjustedHardReq) : null;

  return (
    <div className="bj-ws-score">
      <ScoreBlock
        label="ATS score"
        value={final.overall_score}
        from={regenerated ? first.overall_score : null}
        caption={`${final.confidence} confidence`}
        footer="An honest number, not a verdict -- here's exactly what's movable and what isn't."
      >
        <p className="bj-ws-rating">Rating: {final.rating}</p>
        {adjustedTotal !== null && (
          <p className="bj-ws-adjusted" role="status">
            Adjusted: {final.overall_score} {"->"} {adjustedTotal} · exact -- confirmed once you generate again.
          </p>
        )}
        <div className="bj-ws-bars">
          {scoreRows(final.breakdown).map((row) => {
            const detail =
              row.key === "experience_quality"
                ? experienceCaption
                : row.key === "quantification"
                  ? quantCaption
                  : row.key === "company_and_structure"
                    ? companyCaption
                    : undefined;
            const headroom = row.key === "experience_quality" ? managementHeadroom : 0;
            return (
              <SegmentedBar
                key={row.key}
                label={row.label}
                value={row.value}
                max={row.max}
                valueText={`${row.value} / ${row.max} · ${barState(row.value, row.max)}`}
                hatched={headroom > 0 ? headroom : null}
                detail={
                  headroom > 0 ? (
                    <>
                      +{headroom} exact -- crediting management/leadership experience.
                      {detail !== undefined && <> {caption(detail)}</>}
                    </>
                  ) : (
                    caption(detail)
                  )
                }
              />
            );
          })}
        </div>
      </ScoreBlock>

      {spamPenaltyApplied && (
        <p className="bj-ws-callout" role="note">
          <span className={`bj-trail-glyph bj-trail-glyph--${TRAIL_GLYPHS.failed.tone}`} aria-hidden="true">
            {TRAIL_GLYPHS.failed.char}
          </span>{" "}
          Company alignment took an 8-point penalty -- the extraction flagged likely keyword-stuffing (e.g. hidden/white-on-white text) in this generated resume.
        </p>
      )}

      {clusters.length > 0 && (
        <div className="bj-score-disclosure">
          <button type="button" className="bj-score-disclosure-toggle" onClick={() => setClustersOpen((open) => !open)} aria-expanded={clustersOpen}>
            {clustersOpen ? "Hide" : "Show"} skill-cluster breakdown ({clusters.length})
          </button>
          {clustersOpen && (
            <ul className="bj-ws-clusterlist">
              {clusters.map((c, i) => (
                <li key={`${c.cluster_name}-${i}`} className="bj-ws-clusterrow">
                  <span className="bj-small">{c.cluster_name}</span>
                  <span className="bj-muted bj-small">
                    {Math.round(c.coverage_score)}% coverage · {Math.round(c.points_earned)} / {c.points_possible} pts
                    {c.years_ratio < 1 && ` · ${Math.round(c.years_ratio * 100)}% of years matched`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {capBinding && (
        <p className="bj-ws-callout" role="note">
          Hard requirements below 8/15 cap the total at 65, regardless of everything else -- that's what's holding this score down, not the resume's quality.
        </p>
      )}

      {missingDealbreakers.length > 0 && (
        <ul className="bj-ws-dealbreakers" aria-label="Dealbreakers the posting lists">
          {missingDealbreakers.map((req) => {
            const asserted = assertedThisSession.includes(req);
            const saving = assertState.kind === "saving" && assertState.requirement === req;
            // Every still-missing item shows the SAME gain, honestly -- the formula only counts how many
            // are missing, not which specific one clears next, so clearing any one of them moves the
            // score by the same amount.
            return (
              <li key={req} className="bj-ws-dealbreaker">
                <span className="bj-ws-dealbreaker-state">
                  <span className={`bj-trail-glyph bj-trail-glyph--${asserted ? "emerald" : "danger"}`} aria-hidden="true">
                    {asserted ? "✓" : "✕"}
                  </span>{" "}
                  {asserted ? "Asserted" : "Missing"}
                </span>
                <div className="bj-ws-dealbreaker-body">
                  <p className="bj-ws-dealbreaker-label">{req}</p>
                  {asserted ? (
                    <p className="bj-ws-caption">Saved -- this becomes part of your real score next generation.</p>
                  ) : (
                    <>
                      <p className="bj-ws-caption">
                        This is about you, not your resume -- if it's actually true for you, saying so is worth
                        {gain > 0 ? ` +${gain} exact` : " something once enough are cleared"}.
                      </p>
                      <div className="bj-actions">
                        <button type="button" aria-disabled={saving ? true : undefined} onClick={() => void assertDealbreaker(req)}>
                          {saving ? "Saving..." : "This works for me"}
                        </button>
                      </div>
                      {assertState.kind === "error" && assertState.requirement === req && (
                        <p className="bj-error bj-small" role="alert">
                          {assertState.message}
                        </p>
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
        <ul className="bj-ws-tagged-list" aria-label="Requirements with weak coverage">
          {weakRequirements.map((req) => (
            <li key={req} className="bj-small">
              <span className="bj-ws-tag">Weak coverage</span> {req}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
