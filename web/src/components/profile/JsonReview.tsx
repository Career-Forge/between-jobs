import type { ReactNode } from "react";
import { IMPORT_REVIEW_ID } from "../../lib/nextStep/pages/profile";
import type { ProfileVersion } from "../../lib/profileTypes";
import "../../styles/profile.css";

// The preview of a JSON import: what the importer counted and any warnings it raised, for the person to
// look at before the version becomes their profile. The JSON was parsed by code from their own template,
// with no model involved, and it is saved but inactive until they activate it.
//
// The review has the id `import-review` (the next-step band's link lands on it) and its heading takes
// focus when a link arrives. Its two buttons are outlined: while this review is open the band holds the
// page's one cyan button ("Confirm and make active"), which does what "Looks good -- activate" does.
// `trail` is the review's trail line, drawn under the counts (the page builds it, from the version).

const TITLE_ID = "bj-json-review-title";

export function JsonReview({
  version,
  busy,
  error,
  onActivate,
  onCancel,
  trail,
}: {
  version: ProfileVersion;
  busy: boolean;
  error: string | null;
  onActivate: () => void;
  onCancel: () => void;
  trail?: ReactNode;
}) {
  const personal = version.canonical_json.personal;
  const stats = version.stats ?? {};
  const warnings = version.warnings ?? [];

  return (
    <section id={IMPORT_REVIEW_ID} className="bj-prof-block" aria-labelledby={TITLE_ID}>
      <div className="bj-card-header">
        <h2 id={TITLE_ID} className="bj-prof-title" tabIndex={-1}>
          Review before saving
        </h2>
        <span className="bj-badge-gold">
          <span aria-hidden="true">{"◆"}</span> Pending
        </span>
      </div>
      <p className="bj-muted">
        Parsed deterministically -- no AI touched this. Check it, then activate.
      </p>
      <div>
        <strong>{personal.name}</strong>
        {personal.headline && <span className="bj-muted"> -- {personal.headline}</span>}
      </div>
      <dl className="bj-stat-row">
        <Stat label="Experience" value={stats.experience ?? 0} />
        <Stat label="Projects" value={stats.projects ?? 0} />
        <Stat label="Education" value={stats.education ?? 0} />
        <Stat label="Skills" value={stats.skills ?? 0} />
      </dl>
      {warnings.length > 0 && (
        <ul className="bj-warnings" aria-label="Warnings from the importer">
          {warnings.map((w) => (
            <li key={w}>Note: {w}</li>
          ))}
        </ul>
      )}
      {trail}
      {error && (
        <div className="bj-error" role="alert">
          {error}
        </div>
      )}
      <div className="bj-actions">
        <button type="button" onClick={onActivate} disabled={busy}>
          Looks good -- activate
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    // Term first, then its value, as a definition list requires; the number is drawn above its label.
    <div className="bj-stat">
      <dt className="bj-stat-label">{label}</dt>
      <dd className="bj-stat-value">{value}</dd>
    </div>
  );
}
