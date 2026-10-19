import { useState } from "react";
import { apiFetch } from "../lib/api";
import type { ForgeFitResult } from "../lib/generateTypes";
import { dimensionRows, fitBadgeLabel } from "../lib/honestFloor";
import { HONEST_READ_HEADING_ID } from "../lib/workspaceSections";
import { SegmentedBar } from "./paper/SegmentedBar";
import "../styles/workspace.css";

// The honest read: shown when the engine declined to write a resume (its fit gate held the draft back:
// a mismatch in seniority, or a fit too low to be worth the generation). The fit was computed either
// way and is shown instead of a bare "declined".
//
// It is calm on purpose. Nothing is red: a held-back draft is not a failure, it is the engine saying
// the posting's must-haves do not reach a strong match on the current profile, and the choice is the
// person's -- "Generate anyway" is always there, and so is "Park this application". The ceiling is on
// the fit read's own 0 to 10 scale, never converted to a "/ 100": it is a model's reading on a different
// scale from the ATS score, and a converted number would look more exact than it is. Keywords the
// posting asks for that the profile lacks are "Missing" in words with a muted outline, never a red chip.
//
// "Generate anyway" and "Park this application" are outlined buttons here. When the next-step band
// carries the same step it is the page's one cyan, and the copy in the body stays secondary.
//
// The heading is a focus target (`HONEST_READ_HEADING_ID`): the band's "Read the fit check" and a link
// that says "see why" arrive here, not at the top of the section.

export function HonestFloor({
  applicationId,
  fit,
  onGenerateAnyway,
  onParked,
}: {
  applicationId: string;
  fit: ForgeFitResult;
  onGenerateAnyway: () => void;
  /** Called once the application has been moved to Withdrawn, so the page can re-read its status. */
  onParked?: () => void;
}) {
  const [parking, setParking] = useState(false);
  const [parked, setParked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function parkApplication() {
    if (parking) return;
    setParking(true);
    setError(null);
    try {
      await apiFetch(`/applications/${applicationId}/stage`, {
        method: "POST",
        body: JSON.stringify({
          new_status: "withdrawn",
          idempotency_key: crypto.randomUUID(),
        }),
      });
      setParked(true);
      onParked?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to park this application");
    } finally {
      setParking(false);
    }
  }

  if (parked) {
    return (
      <div className="bj-ws-honest">
        <h3 id={HONEST_READ_HEADING_ID} tabIndex={-1} className="bj-ws-block-title">
          Honest read
        </h3>
        <p className="bj-muted bj-small">Parked -- find it again under "withdrawn" on Applications anytime.</p>
      </div>
    );
  }

  const dimensions = dimensionRows(fit.dimensions);

  return (
    <div className="bj-ws-honest">
      <div className="bj-ws-honest-head">
        <h3 id={HONEST_READ_HEADING_ID} tabIndex={-1} className="bj-ws-block-title">
          Honest read
        </h3>
        <span className="bj-ws-tag">{fitBadgeLabel(fit.recommendation)}</span>
      </div>

      <p className="bj-ws-ceiling">
        <span className="bj-ws-ceiling-label">Ceiling</span>
        <span className="bj-ws-ceiling-value">{fit.overall_score.toFixed(1)} / 10</span>
      </p>
      <p className="bj-small">
        This role's must-haves don't reach a strong match on your current profile. We won't inflate that number to sell you a generation.
      </p>

      {fit.visa_flag && (
        <p className="bj-ws-callout bj-ws-callout--danger bj-small" role="note">
          This posting doesn't mention sponsorship or a cap-exempt path, and your profile indicates you need one.
        </p>
      )}

      {dimensions.length > 0 && (
        <div className="bj-ws-honest-section">
          <h4 className="bj-ws-label">Why this read</h4>
          <div className="bj-ws-bars">
            {dimensions.map((row) => (
              <SegmentedBar key={row.key} label={`${row.label} · ${row.weightPct}% weight`} value={Math.round(row.value * 10) / 10} max={10} segments={10} valueText={`${Math.round(row.value * 10) / 10} / 10`} />
            ))}
          </div>
        </div>
      )}

      {(fit.keyword_hits.length > 0 || fit.keyword_gaps.length > 0) && (
        <div className="bj-ws-honest-section">
          <h4 className="bj-ws-label">Keywords</h4>
          <ul className="bj-ws-chips" aria-label="Keywords from the posting">
            {fit.keyword_hits.map((k) => (
              <li key={`hit-${k}`} className="bj-ws-chip">
                {k}
                <span className="bj-ws-chip-state">Matched</span>
              </li>
            ))}
            {fit.keyword_gaps.map((k) => (
              <li key={`gap-${k}`} className="bj-ws-chip bj-ws-chip--missing">
                {k}
                <span className="bj-ws-chip-state">Missing</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {fit.gaps.length > 0 && (
        <div className="bj-ws-honest-section">
          <h4 className="bj-ws-label">No evidence path</h4>
          <ul className="bj-ws-plain-list">
            {fit.gaps.map((g) => (
              <li key={g} className="bj-small">
                {g}
              </li>
            ))}
          </ul>
        </div>
      )}

      {fit.strengths.length > 0 && (
        <div className="bj-ws-honest-section">
          <h4 className="bj-ws-label">{fit.strengths.length > 1 ? "Still-honest angles" : "A still-honest angle"}</h4>
          <ul className="bj-ws-plain-list">
            {fit.strengths.map((s) => (
              <li key={s} className="bj-small">
                {s}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="bj-actions">
        <button type="button" aria-disabled={parking ? true : undefined} onClick={() => void parkApplication()}>
          {parking ? "Parking..." : "Park this application"}
        </button>
        <button type="button" onClick={onGenerateAnyway}>
          Generate anyway
        </button>
      </div>
      {error && (
        <p className="bj-error" role="alert">
          {error}
        </p>
      )}
      <p className="bj-ws-caption">Your call either way -- the platform advises, it never blocks.</p>
    </div>
  );
}
