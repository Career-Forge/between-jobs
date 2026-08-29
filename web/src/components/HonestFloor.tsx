import { useState } from "react";
import { apiFetch } from "../lib/api";
import type { ForgeFitResult } from "../lib/generateTypes";
import { dimensionRows, fitBadgeLabel } from "../lib/honestFloor";
import { ScoreBar } from "./ScoreBreakdown";

// Honest Floor (S4c, honest-score-surfaces.md) -- Reality Check artboard 4
// ("Honest read"), ported from the reviewed design canvas (StretchState.dc.html).
// Shown only when the engine DECLINED to generate (gates.py's REJECT_MISMATCH
// or SKIP_LOW_SCORE) -- `fit` was already computed either way (forge_score.py
// runs before the gate check) and previously just thrown away
// (`ForgeApplyResult`'s own `extra="ignore"`); this surfaces it instead of
// the bare decline string GeneratePanel used to show alone.
//
// One departure from the mockup: no "Ceiling ~58/100" conversion. forge_score's
// overall_score is 0-10, a different LLM read on a different scale than
// AtsScoreBreakdown's own honest 0-100 -- showing it as a fake "/100" would be
// exactly the fabricated-looking precision this whole plan refuses elsewhere.
// Shown as what it actually is: X/10.

export function HonestFloor({
  applicationId,
  fit,
  onGenerateAnyway,
}: {
  applicationId: string;
  fit: ForgeFitResult;
  onGenerateAnyway: () => void;
}) {
  const [parking, setParking] = useState(false);
  const [parked, setParked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function parkApplication() {
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
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to park this application");
    } finally {
      setParking(false);
    }
  }

  if (parked) {
    return (
      <div className="bj-honest-floor bj-muted bj-small">
        Parked -- find it again under "withdrawn" on Applications anytime.
      </div>
    );
  }

  const dimensions = dimensionRows(fit.dimensions);

  return (
    <div className="bj-honest-floor">
      <div className="bj-honest-floor-header">
        <h3 className="bj-honest-floor-title">Honest read</h3>
        <span className="bj-badge-muted">{fitBadgeLabel(fit.recommendation)}</span>
      </div>

      <div className="bj-honest-floor-ceiling">
        <span className="bj-honest-floor-ceiling-label">Ceiling</span>
        <span className="bj-honest-floor-ceiling-value">{fit.overall_score.toFixed(1)} / 10</span>
      </div>
      <p className="bj-small">
        This role's must-haves don't reach a strong match on your current profile. We won't
        inflate that number to sell you a generation.
      </p>

      {fit.visa_flag && (
        <div className="bj-danger-callout bj-small">
          This posting doesn't mention sponsorship or a cap-exempt path, and your profile
          indicates you need one.
        </div>
      )}

      {dimensions.length > 0 && (
        <div className="bj-honest-floor-section">
          <span className="bj-honest-floor-section-label">Why this read</span>
          <div className="bj-score-rows">
            {dimensions.map((row) => (
              <ScoreBar
                key={row.key}
                label={`${row.label} · ${row.weightPct}% weight`}
                value={Math.round(row.value * 10) / 10}
                max={10}
              />
            ))}
          </div>
        </div>
      )}

      {(fit.keyword_hits.length > 0 || fit.keyword_gaps.length > 0) && (
        <div className="bj-honest-floor-section">
          <span className="bj-honest-floor-section-label">Keywords</span>
          <div className="bj-skill-chips">
            {fit.keyword_hits.map((k) => (
              <span key={`hit-${k}`} className="bj-skill-chip bj-badge-emerald">
                {k}
                <span className="bj-skill-chip-state">Matched</span>
              </span>
            ))}
            {fit.keyword_gaps.map((k) => (
              <span key={`gap-${k}`} className="bj-skill-chip bj-badge-danger">
                {k}
                <span className="bj-skill-chip-state">Missing</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {fit.gaps.length > 0 && (
        <div className="bj-honest-floor-section">
          <span className="bj-honest-floor-section-label">No evidence path</span>
          <ul className="bj-checklist">
            {fit.gaps.map((g) => (
              <li key={g} className="bj-checklist-item">
                <span className="bj-badge-muted">no path</span>
                <span className="bj-small">{g}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {fit.strengths.length > 0 && (
        <div className="bj-honest-floor-callout bj-small">
          <span className="bj-honest-floor-callout-label">
            {fit.strengths.length > 1 ? "Still-honest angles:" : "A still-honest angle:"}
          </span>{" "}
          {fit.strengths.length > 1 ? (
            <ul className="bj-checklist">
              {fit.strengths.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          ) : (
            fit.strengths[0]
          )}
        </div>
      )}

      <div className="bj-actions">
        <button onClick={() => void parkApplication()} disabled={parking}>
          {parking ? "Parking..." : "Park this application"}
        </button>
        <button className="bj-gap-ghost" onClick={onGenerateAnyway}>
          Generate anyway
        </button>
      </div>
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-muted bj-small">
        Your call either way -- the platform advises, it never blocks.
      </div>
    </div>
  );
}
