import { useId } from "react";
import { ALL_STATUSES, type ApplicationStatus } from "../../lib/applicationsTypes";
import { STATUS_LABELS } from "../../lib/applicationsBoard";
import "../../styles/workspace.css";

// The status of the application, always on the page, so a band that guessed wrong (someone who applied
// but never moved the card) is one choice to correct. It changes the status through the same stage
// endpoint the Applications page uses; the page re-reads the application afterwards.
//
// While a change is out the select is aria-disabled and its changes are ignored (not `disabled`, so
// it keeps focus). A status the server sent that is not one of the seven is shown as it came, and is
// not something the person can choose back.

export interface StatusSelectProps {
  status: string;
  busy: boolean;
  onChange: (status: ApplicationStatus) => void;
}

export function StatusSelect({ status, busy, onChange }: StatusSelectProps) {
  const id = useId();
  const known = ALL_STATUSES.includes(status as ApplicationStatus);
  return (
    <div className="bj-ws-status">
      <label htmlFor={id} className="bj-ws-status-label">
        Status
      </label>
      <select
        id={id}
        className="bj-ws-status-select"
        value={status}
        aria-disabled={busy ? true : undefined}
        aria-busy={busy ? true : undefined}
        onChange={(event) => {
          if (busy) return;
          onChange(event.target.value as ApplicationStatus);
        }}
      >
        {!known && <option value={status}>{status}</option>}
        {ALL_STATUSES.map((value) => (
          <option key={value} value={value}>
            {STATUS_LABELS[value]}
          </option>
        ))}
      </select>
    </div>
  );
}
