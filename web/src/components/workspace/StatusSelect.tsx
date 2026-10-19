import { useId, useRef, useState, type KeyboardEvent } from "react";
import { ALL_STATUSES, type ApplicationStatus } from "../../lib/applicationsTypes";
import { STATUS_LABELS } from "../../lib/applicationsBoard";
import "../../styles/workspace.css";

// The status of the application, always on the page, so a band that guessed wrong (someone who applied
// but never moved the card) is one choice to correct. It changes the status through the same stage
// endpoint the Applications page uses; the page re-reads the application afterwards.
//
// A choice made with a pointer, or in the open list, is made at once. A step on the closed select with
// the keyboard (Arrow, Home, End, type-ahead) fires `change` for every press in a browser, and each
// change here is a recorded stage change that drives the band and the status history; so a keyboard step
// is only held, with a line saying so, until Enter. Escape or leaving the select drops it. Otherwise a
// keyboard user could not reach a distant status (Offer) without recording every one in between.
//
// While a change is out the select is aria-disabled and its changes are ignored (not `disabled`, so
// it keeps focus). A status the server sent that is not one of the seven is shown as it came, and is
// not something the person can choose back.

export interface StatusSelectProps {
  status: string;
  busy: boolean;
  onChange: (status: ApplicationStatus) => void;
}

// The keys that move a closed select's value without opening it (and so fire `change` one step at a time).
const STEP_KEYS: ReadonlySet<string> = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);

export function StatusSelect({ status, busy, onChange }: StatusSelectProps) {
  const id = useId();
  const hintId = useId();
  const known = ALL_STATUSES.includes(status as ApplicationStatus);
  const [pending, setPending] = useState<ApplicationStatus | null>(null);
  const stepping = useRef(false);

  const onKeyDown = (event: KeyboardEvent<HTMLSelectElement>) => {
    if (event.key === "Enter" && pending !== null) {
      event.preventDefault();
      const chosen = pending;
      setPending(null);
      if (!busy && chosen !== status) onChange(chosen);
      return;
    }
    if (event.key === "Escape") setPending(null);
    const modified = event.altKey || event.ctrlKey || event.metaKey;
    stepping.current = !modified && (STEP_KEYS.has(event.key) || (event.key.length === 1 && event.key !== " "));
  };

  return (
    <div className="bj-ws-status">
      <label htmlFor={id} className="bj-ws-status-label">
        Status
      </label>
      <select
        id={id}
        className="bj-ws-status-select"
        value={pending ?? status}
        aria-disabled={busy ? true : undefined}
        aria-busy={busy ? true : undefined}
        aria-describedby={pending !== null ? hintId : undefined}
        onKeyDown={onKeyDown}
        onKeyUp={() => {
          stepping.current = false;
        }}
        onBlur={() => setPending(null)}
        onChange={(event) => {
          const chosen = event.target.value as ApplicationStatus;
          const viaKeyboardStep = stepping.current;
          stepping.current = false;
          if (busy) return;
          if (viaKeyboardStep) {
            setPending(chosen === status ? null : chosen);
            return;
          }
          setPending(null);
          onChange(chosen);
        }}
      >
        {!known && <option value={status}>{status}</option>}
        {ALL_STATUSES.map((value) => (
          <option key={value} value={value}>
            {STATUS_LABELS[value]}
          </option>
        ))}
      </select>
      {pending !== null && (
        <span id={hintId} className="bj-ws-status-hint" role="status">
          Press Enter to set {STATUS_LABELS[pending]}. Escape cancels.
        </span>
      )}
    </div>
  );
}
