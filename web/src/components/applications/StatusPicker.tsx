import { useId } from "react";
import { STATUS_LABELS, groupByStatus, statusCounts } from "../../lib/applicationsBoard";
import { ALL_STATUSES, type Application, type ApplicationStatus } from "../../lib/applicationsTypes";
import { statusOptionLabel } from "../../lib/applicationsView";
import { BoardCard } from "../ApplicationsBoard";
import type { MoveProblem } from "./ApplicationParts";
import "../../styles/applications.css";

// The Applications screen on a phone: one status at a time. A native "Showing status" select lists
// the seven statuses in pipeline order with their counts, and the list under it shows the cards of
// the chosen one, each with its own "Move to..." select (drag and drop does not work on touch).
// There is no List/Board toggle here, and no horizontal scrolling. Which status is showing is the
// page's to hold (it is the one it remembers for the next visit).

export interface StatusPickerProps {
  applications: readonly Application[];
  now: Date;
  status: ApplicationStatus;
  onStatusChange: (status: ApplicationStatus) => void;
  busyIds: ReadonlySet<string>;
  problems: Readonly<Record<string, MoveProblem | undefined>>;
  onChangeStatus: (applicationId: string, status: ApplicationStatus) => void;
  onRetry: (applicationId: string) => void;
}

export function StatusPicker({ applications, now, status, onStatusChange, busyIds, problems, onChangeStatus, onRetry }: StatusPickerProps) {
  const selectId = useId();
  const counts = statusCounts(applications);
  const shown = groupByStatus(applications)[status];
  return (
    <div className="bj-app-phone">
      <div className="bj-app-picker">
        <label htmlFor={selectId}>Showing status</label>
        <select
          id={selectId}
          className="bj-app-select bj-app-picker-select"
          data-role="picker"
          value={status}
          onChange={(event) => onStatusChange(event.target.value as ApplicationStatus)}
        >
          {ALL_STATUSES.map((option) => (
            <option key={option} value={option}>
              {statusOptionLabel(option, counts[option])}
            </option>
          ))}
        </select>
      </div>
      {shown.length === 0 ? (
        <p className="bj-app-column-empty">Nothing is under {STATUS_LABELS[status]} right now.</p>
      ) : (
        <ul className="bj-app-column-cards" aria-label={`${STATUS_LABELS[status]} applications`}>
          {shown.map((application) => (
            <BoardCard
              key={application.id}
              application={application}
              now={now}
              busy={busyIds.has(application.id)}
              problem={problems[application.id]}
              onChangeStatus={(next) => onChangeStatus(application.id, next)}
              onRetry={() => onRetry(application.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
