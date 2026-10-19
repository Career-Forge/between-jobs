import { Link } from "react-router-dom";
import { formatAppliedDate, otherStatuses, STATUS_LABELS } from "../../lib/applicationsBoard";
import { cardTrail, STATUS_CHIPS } from "../../lib/applicationsView";
import { ALL_STATUSES, type Application, type ApplicationStatus } from "../../lib/applicationsTypes";
import { FailureState } from "../paper/StateViews";
import { TrailLine } from "../paper/TrailLine";
import { ApplicationActionsSelect } from "../ApplicationActionsSelect";
import "../../styles/applications.css";

// The parts an application card is made of, shared by the list card, the board card and the phone's
// per-status list: what the card says (title, company, status chip, resume badge, applied date,
// trail) and the controls that act on it. What is said comes from lib/applicationsView.ts; this only
// draws it.

// A move that did not go through: where it was going, and what the server said. The card stays where
// it was, with this under it and a Retry that sends the same move again.
export interface MoveProblem {
  to: ApplicationStatus;
  message: string;
}

export function titleOf(application: Application): string {
  return application.snapshot?.title ?? "Untitled";
}

export function companyLine(application: Application): string {
  const snapshot = application.snapshot;
  return `${snapshot?.company_name ?? "Unknown company"}${snapshot?.location_text ? ` -- ${snapshot.location_text}` : ""}`;
}

// A neutral outline chip with a shape: the word carries the status, the glyph only repeats it.
export function StatusChip({ status }: { status: ApplicationStatus }) {
  const chip = STATUS_CHIPS[status] ?? STATUS_CHIPS.saved;
  return (
    <span className={`bj-app-chip bj-app-chip--${chip.tone}`}>
      <span className="bj-app-chip-glyph" aria-hidden="true">
        {chip.glyph}
      </span>
      {STATUS_LABELS[status] ?? STATUS_LABELS.saved}
    </span>
  );
}

// "✦ Resume drafted": a draft exists. It is a model's output, not a checked one, so the badge never
// reads as a pass.
export function ResumeBadge() {
  return (
    <span className="bj-app-badge">
      <span className="bj-app-badge-glyph" aria-hidden="true">
        {"✦"}
      </span>
      Resume drafted
    </span>
  );
}

export function CardIdentity({ application }: { application: Application }) {
  return (
    <div className="bj-app-identity">
      <h3 className="bj-app-title">{titleOf(application)}</h3>
      <p className="bj-app-company">{companyLine(application)}</p>
    </div>
  );
}

// The status chip (on the list's cards), the resume badge beside it and the applied date, when there
// is something to say. A board card sits in the column that names its status, so it has no chip.
export function CardMeta({ application, now, showStatus = false }: { application: Application; now: Date; showStatus?: boolean }) {
  const applied = formatAppliedDate(application.date_applied, now);
  if (!showStatus && !application.resume_exists && applied === null) return null;
  return (
    <p className="bj-app-meta">
      {showStatus && <StatusChip status={application.status} />}
      {application.resume_exists && <ResumeBadge />}
      {applied !== null && <span className="bj-app-applied">{applied}</span>}
    </p>
  );
}

// The card's last line, inline at every width.
export function CardTrail({ application, now }: { application: Application; now: Date }) {
  return <TrailLine trail={cardTrail(application, now)} />;
}

export function CardProblem({ problem, onRetry }: { problem: MoveProblem; onRetry: () => void }) {
  return (
    <div className="bj-app-problem">
      <FailureState
        message={`Couldn't move this to ${STATUS_LABELS[problem.to]}. It is still where it was. ${problem.message}`}
        onRetry={onRetry}
      />
    </div>
  );
}

// The list's status control: always visible, always the card's real status, so a wrong one is a
// single choice to correct. A request out for the card holds it still (aria-disabled, not disabled,
// so focus stays) and ignores a change made meanwhile.
export function StatusSelect({
  application,
  busy,
  onChange,
}: {
  application: Application;
  busy: boolean;
  onChange: (status: ApplicationStatus) => void;
}) {
  return (
    <select
      className="bj-app-select"
      data-role="status"
      aria-label={`Status of "${titleOf(application)}"`}
      aria-disabled={busy ? true : undefined}
      value={application.status}
      onChange={(event) => {
        if (!busy) onChange(event.target.value as ApplicationStatus);
      }}
    >
      {ALL_STATUSES.map((status) => (
        <option key={status} value={status}>
          {STATUS_LABELS[status]}
        </option>
      ))}
    </select>
  );
}

// The board's per-card move: a required keyboard and touch fallback, since drag and drop has neither.
export function MoveSelect({
  application,
  busy,
  onChange,
}: {
  application: Application;
  busy: boolean;
  onChange: (status: ApplicationStatus) => void;
}) {
  return (
    <select
      className="bj-app-select"
      data-role="status"
      aria-label={`Move "${titleOf(application)}" to a different stage`}
      aria-disabled={busy ? true : undefined}
      value=""
      onChange={(event) => {
        const value = event.target.value;
        if (value && !busy) onChange(value as ApplicationStatus);
      }}
    >
      <option value="">Move to...</option>
      {otherStatuses(application.status).map((status) => (
        <option key={status} value={status}>
          {STATUS_LABELS[status]}
        </option>
      ))}
    </select>
  );
}

export function OpenLink({ application, label }: { application: Application; label: string }) {
  return (
    <Link className="bj-app-link" to={`/applications/${application.id}`}>
      {label}
    </Link>
  );
}

export function CardActions({ application }: { application: Application }) {
  return <ApplicationActionsSelect applicationId={application.id} title={application.snapshot?.title} />;
}
