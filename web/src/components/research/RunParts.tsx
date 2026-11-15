import type { Ref } from "react";
import type { Failure } from "../../lib/setupRequired";
import { failedSearchCount, searchesLead, type ResearchRun } from "../../lib/research";
import { FailureState, PartialState } from "../paper/StateViews";
import { SetupRequiredNotice } from "../SetupRequiredNotice";
import "../../styles/research.css";

// The pieces the three research runs (Company intel, Contacts, Events) draw the same way.

// The button that makes a run. It is outlined: the page's one cyan fill is the next-step band's. While a
// run is out it is aria-disabled, not disabled, so a focused button keeps focus and a screen reader still
// finds it; the press itself is ignored by the hook (lib/useResearchRun.ts).
//
// `busy` is a run being out (the label says so); `held` is the section still reading what it showed
// before, when a press has nothing to do yet (the label stays). `describedBy` is the id of the note on the page
// that says what a press costs, so the button reads it out with its name; the caller passes it only while the
// note is drawn.
export function RunButton({
  label,
  busyLabel,
  busy,
  held = false,
  onRun,
  buttonRef,
  describedBy,
}: {
  label: string;
  busyLabel: string;
  busy: boolean;
  held?: boolean;
  onRun: () => void;
  buttonRef?: Ref<HTMLButtonElement>;
  describedBy?: string;
}) {
  return (
    <div className="bj-actions bj-rs-bar">
      <button ref={buttonRef} type="button" aria-describedby={describedBy} aria-disabled={busy || held ? true : undefined} onClick={onRun}>
        {busy ? busyLabel : label}
      </button>
    </div>
  );
}

// A failed run. A missing key or a provider turning the key down is a setup notice with its link and no
// Retry (asking again cannot work until it is fixed); anything else is a danger callout whose Retry
// makes the run again. Either way what the section showed before is still there. Retry clears the callout
// it is pressed in, so focus goes on to the section's run button (`restoreFocusTo`) rather than the page body.
export function RunProblem({ problem, onRetry, restoreFocusTo }: { problem: Failure | null; onRetry: () => void; restoreFocusTo?: () => HTMLElement | null }) {
  if (problem === null) return null;
  if (problem.kind === "setup") return <SetupRequiredNotice notice={problem.notice} />;
  return <FailureState message={problem.message} onRetry={onRetry} restoreFocusTo={restoreFocusTo} />;
}

// What did not complete in a run: one line per search that failed or was skipped, in the server's own
// words, under a lead that says how many.
export function RunWarnings({ run }: { run: Pick<ResearchRun, "warnings"> }) {
  const count = failedSearchCount(run);
  if (count === 0) return null;
  return <PartialState lead={searchesLead(count)} notices={run.warnings.filter((warning) => warning.trim() !== "")} />;
}
