import { useState } from "react";
import { Link } from "react-router-dom";
import type { FirstRunStep, FirstRunView } from "../lib/firstRun";
import { formatTrailDate } from "../lib/trail";
import "../styles/today.css";

// The setup checklist, as a function of its props (the same split as AccountCardView): what it
// shows for a derived view, and what its Hide button does. Deciding what is done, and how it is
// presented, is lib/firstRun.ts.
//
// Five steps, each one a link to where it is done, in three states and never a guessed tick:
//   ◆ To do            gold, with a short hint
//   ✓ Done             emerald, with the real fact when one came back ("Active since Sep 12")
//   ○ Can't tell yet   muted: every read worked and none holds the answer
//   ○ Couldn't check   muted, with a secondary Try again: a read it depends on failed
// The words carry every state; the glyphs are decoration. It holds no cyan fill: the next-step band
// holds the page's only one.
//
// Open it is the list. Closed it is one 44px line that opens it again: "✓ Setup ready" when all five
// are done, otherwise the counts ("Setup · 2 to do"). Hiding it never hides it for good.

const TITLE_ID = "bj-setup-title";

function stateOf(step: FirstRunStep): { words: string; tone: "todo" | "done" | "unknown"; glyph: string } {
  if (step.status === "done") return { words: "Done", tone: "done", glyph: "✓" };
  if (step.status === "todo") return { words: "To do", tone: "todo", glyph: "◆" };
  return { words: step.cause === "errored" ? "Couldn't check" : "Can't tell yet", tone: "unknown", glyph: "○" };
}

function noteOf(step: FirstRunStep, now: Date): string | null {
  if (step.status === "todo") return step.hint;
  if (step.status !== "done" || step.fact === null) return null;
  if (step.fact.kind === "text") return step.fact.text;
  const since = formatTrailDate(step.fact.at, { now });
  return since === null ? null : `Active since ${since}`;
}

function countsLine(view: FirstRunView): string {
  const parts = ["Setup"];
  if (view.todoCount > 0) parts.push(`${view.todoCount} to do`);
  if (view.unknownCount > 0) parts.push(`${view.unknownCount} can't tell`);
  return parts.join(" · ");
}

export function FirstRunChecklistView({
  view,
  onDismiss,
  onRetry,
  now = new Date(),
}: {
  view: FirstRunView;
  onDismiss: () => void;
  /** Reads the account again; without it a step that errored has no Try again. */
  onRetry?: () => void;
  now?: Date;
}) {
  // null: follow the view (open while a step is to do, closed otherwise); a click decides.
  const [chosen, setChosen] = useState<boolean | null>(null);
  if (view.presentation === "hidden") return null;
  const open = chosen ?? view.presentation === "expanded";

  if (!open) {
    return (
      <button
        type="button"
        className="bj-setup-line"
        aria-expanded={false}
        onClick={() => setChosen(true)}
      >
        {view.finished ? (
          <>
            <span className="bj-setup-line-glyph--done" aria-hidden="true">
              {"✓"}
            </span>
            Setup ready
          </>
        ) : (
          countsLine(view)
        )}
        <span aria-hidden="true">{"›"}</span>
      </button>
    );
  }

  return (
    <section className="bj-setup" aria-labelledby={TITLE_ID}>
      <div className="bj-setup-head">
        <h2 id={TITLE_ID} className="bj-setup-title">
          Setup
        </h2>
        <span className="bj-setup-count">
          {view.doneCount} of {view.steps.length} done
        </span>
        <button
          type="button"
          className="bj-quiet"
          aria-label="Hide the setup checklist"
          onClick={() => {
            // A finished account only folds the list away; hiding an unfinished one is remembered.
            if (!view.finished) onDismiss();
            setChosen(false);
          }}
        >
          Hide
        </button>
      </div>
      <ol className="bj-setup-steps">
        {view.steps.map((step) => {
          const state = stateOf(step);
          const note = noteOf(step, now);
          return (
            <li key={step.id} className="bj-setup-step" data-status={step.status}>
              <span className={`bj-setup-state bj-setup-state--${state.tone}`}>
                <span className="bj-setup-glyph" aria-hidden="true">
                  {state.glyph}
                </span>
                {state.words}
              </span>
              <Link className="bj-setup-link" to={step.to}>
                {step.label}
              </Link>
              {note !== null && <p className="bj-setup-note">{note}</p>}
              {step.status === "unknown" && step.cause === "errored" && onRetry !== undefined && (
                <button type="button" className="bj-setup-retry" aria-label={`Try again: ${step.label}`} onClick={onRetry}>
                  Try again
                </button>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
