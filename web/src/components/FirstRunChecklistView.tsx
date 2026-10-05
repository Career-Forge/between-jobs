import { Link } from "react-router-dom";
import type { FirstRunStep, FirstRunView } from "../lib/firstRun";

// The first-run checklist card, as a function of its props (the same split as
// AccountCardView): what it shows for a derived view, and what its Dismiss button does.
// Deciding what is done, and whether the card shows at all, is lib/firstRun.ts.
//
// Accessible by construction: a labelled region, an ordered list, and every step says
// its state in words -- "Done" is text, not just a tick -- so a screen reader announces
// it. An unknown step says so rather than looking unticked.

const TITLE_ID = "bj-first-run-title";

function StepState({ step, isNext }: { step: FirstRunStep; isNext: boolean }) {
  if (step.status === "done") return <span className="bj-badge-emerald">Done</span>;
  if (step.status === "unknown") {
    return <span className="bj-badge-muted">Can&apos;t check right now</span>;
  }
  return isNext ? (
    <span className="bj-badge-gold">Next</span>
  ) : (
    <span className="bj-visually-hidden">To do</span>
  );
}

function mark(step: FirstRunStep): string {
  if (step.status === "done") return "✓";
  return step.status === "unknown" ? "?" : "○";
}

export function FirstRunChecklistView({
  view,
  onDismiss,
}: {
  view: FirstRunView;
  onDismiss: () => void;
}) {
  if (!view.visible) return null;

  return (
    <section className="bj-card bj-first-run" aria-labelledby={TITLE_ID}>
      <div className="bj-card-header">
        <h2 id={TITLE_ID}>Getting started</h2>
        <button type="button" onClick={onDismiss} aria-label="Dismiss the getting started checklist">
          Dismiss
        </button>
      </div>
      <p className="bj-muted bj-small">
        {view.doneCount} of {view.steps.length} done.
        {view.nextStep !== null && ` Next: ${view.nextStep.label}.`}
      </p>
      <ol className="bj-first-run-steps">
        {view.steps.map((step) => (
          <li key={step.id} className="bj-first-run-step" data-status={step.status}>
            <span className="bj-first-run-mark" aria-hidden="true">
              {mark(step)}
            </span>
            <div className="bj-first-run-body">
              <div className="bj-first-run-title">
                <Link to={step.to}>{step.label}</Link>
                <StepState step={step} isNext={view.nextStep?.id === step.id} />
              </div>
              <div className="bj-muted bj-small">{step.hint}</div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
