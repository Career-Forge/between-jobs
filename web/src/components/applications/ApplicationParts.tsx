import { useRef, useState, type KeyboardEvent } from "react";
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

// A move that did not go through: where it was going, the status the card had when it was asked, the
// key the request carried, and what the server said. The card is shown with this under it and a Retry
// that sends the same move again under the same key, so the server can tell a retry from a new move.
// `from` is what lets the page drop this once a re-read shows the card somewhere else: a request that
// failed on the wire may still have gone through.
export interface MoveProblem {
  to: ApplicationStatus;
  from: ApplicationStatus | null;
  idempotencyKey: string;
  message: string;
}

export function titleOf(application: Application): string {
  return application.snapshot?.title ?? "Untitled";
}

// How the card is named to a screen reader: the title alone is not enough, since the same title
// ("Software Engineer") is tracked at several companies.
export function nameOf(application: Application): string {
  const company = application.snapshot?.company_name;
  return company ? `"${titleOf(application)}" at ${company}` : `"${titleOf(application)}"`;
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
  const applied = formatAppliedDate(application.date_applied, now, undefined, application.status);
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

// Says what the page knows: the request did not come back as done. It does not say where the card
// is -- a request that failed on the wire may have gone through, and the card itself shows its status.
export function CardProblem({ problem, onRetry }: { problem: MoveProblem; onRetry: () => void }) {
  return (
    <div className="bj-app-problem">
      <FailureState message={`The move to ${STATUS_LABELS[problem.to]} didn't go through. ${problem.message}`} onRetry={onRetry} />
    </div>
  );
}

// The keys that move a closed select's value without opening it. On Windows and Linux Chromium and
// on Firefox each of them fires `change` at once, so reading the change as a decision would send a
// status change per Arrow press while the person is only looking through the options.
const STEP_KEYS: ReadonlySet<string> = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);

// A select whose choice is a decision: a choice made with the pointer or from the open list is
// committed at once, and one made by stepping the closed select with the keyboard is held until Enter
// (Tab, Escape or leaving drops it). The same rule as the Jump to select on the workspace page.
function useHeldSelect(committed: string, onCommit: (value: string) => void) {
  const [pending, setPending] = useState<string | null>(null);
  const stepping = useRef(false);
  return {
    value: pending ?? committed,
    onKeyDown: (event: KeyboardEvent<HTMLSelectElement>) => {
      if (event.key === "Enter" && pending !== null) {
        event.preventDefault();
        const value = pending;
        setPending(null);
        onCommit(value);
        return;
      }
      if (event.key === "Escape") setPending(null);
      const modified = event.altKey || event.ctrlKey || event.metaKey;
      stepping.current = !modified && (STEP_KEYS.has(event.key) || (event.key.length === 1 && event.key !== " "));
    },
    onKeyUp: () => {
      stepping.current = false;
    },
    onBlur: () => setPending(null),
    onChange: (value: string) => {
      const viaKeyboardStep = stepping.current;
      stepping.current = false;
      if (viaKeyboardStep) setPending(value);
      else {
        setPending(null);
        onCommit(value);
      }
    },
  };
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
  const held = useHeldSelect(application.status, (value) => {
    if (!busy) onChange(value as ApplicationStatus);
  });
  return (
    <select
      className="bj-app-select"
      data-role="status"
      aria-label={`Status of ${nameOf(application)}`}
      aria-disabled={busy ? true : undefined}
      value={held.value}
      onKeyDown={held.onKeyDown}
      onKeyUp={held.onKeyUp}
      onBlur={held.onBlur}
      onChange={(event) => held.onChange(event.target.value)}
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
  const held = useHeldSelect("", (value) => {
    if (value && !busy) onChange(value as ApplicationStatus);
  });
  return (
    <select
      className="bj-app-select"
      data-role="status"
      aria-label={`Move ${nameOf(application)} to a different stage`}
      aria-disabled={busy ? true : undefined}
      value={held.value}
      onKeyDown={held.onKeyDown}
      onKeyUp={held.onKeyUp}
      onBlur={held.onBlur}
      onChange={(event) => held.onChange(event.target.value)}
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
  return <ApplicationActionsSelect applicationId={application.id} title={application.snapshot?.title} company={application.snapshot?.company_name} />;
}
