import { useId, useRef, useState, type KeyboardEvent } from "react";
import { LANDING } from "../../content/landing";
import { BOARD_STATUSES, STAGES, type Stage, type StageId } from "../../content/landingCircuit";
import { StaticTrailLine } from "../paper/TrailLine";
import "../../styles/circuit.css";

// "Follow one role from posting to offer": five stages, each a toggle button, each with a card built
// from a fictional sample role, and a sample next step that rewrites for the stage in view. All five
// cards show side by side from 960px up; under it the circuit is a vertical list with a trace down
// its left edge, and only the stage in view shows its card.
//
// Everything drawn here is a picture. The depicted buttons are outlined spans, not controls, so the
// page's one cyan fill stays the hero's. The only things that take focus are the five stage buttons.
// The sample next step is a polite live region: picking a stage announces which one and what its
// sample step says (the stage's name comes first, so a listener knows what changed). Under
// prefers-reduced-motion nothing here fades or moves (circuit.css).

const DEFAULT_STAGE: StageId = "prepare";

// The width at which circuit.css lays the five stages out in a row. Where the browser cannot say
// (no matchMedia), the row is assumed, since it is the layout the keys were written for.
const ROW_LAYOUT_QUERY = "(min-width: 960px)";
function isRowLayout(): boolean {
  return typeof window.matchMedia !== "function" || window.matchMedia(ROW_LAYOUT_QUERY).matches;
}

function Mock({ children }: { children: string }) {
  return (
    <span className="bj-circuit-mock" aria-hidden="true">
      {children}
    </span>
  );
}

function Ladder({ filled, cells }: { filled: number; cells: number }) {
  return (
    <span className="bj-circuit-ladder" aria-hidden="true">
      {Array.from({ length: cells }, (_, index) => (
        <span key={index} className={index < filled ? "bj-circuit-ladder-cell bj-circuit-ladder-cell--on" : "bj-circuit-ladder-cell"} />
      ))}
    </span>
  );
}

function Lines({ widths }: { widths: readonly string[] }) {
  return (
    <span className="bj-circuit-lines" aria-hidden="true">
      {widths.map((width, index) => (
        <span key={index} className={`bj-circuit-line bj-circuit-line--${width}`} />
      ))}
    </span>
  );
}

function CardBody({ stage }: { stage: Stage }) {
  switch (stage.id) {
    case "find":
      return (
        <>
          <p className="bj-circuit-kicker">{stage.card.kicker}</p>
          <p className="bj-circuit-title">{LANDING.circuit.sampleRole}</p>
          <p className="bj-circuit-meta">{LANDING.circuit.sampleCompany} · Remote, US</p>
          <div className="bj-circuit-fit">
            <span>Fit</span>
            <Ladder filled={stage.card.fitFilled} cells={stage.card.fitCells} />
            <strong>{stage.card.fit}</strong>
          </div>
          <p className="bj-circuit-fact">
            <span className="bj-circuit-fact-label">{stage.card.lowestLabel}</span> {stage.card.lowest}
          </p>
          <Mock>{stage.card.mockAction}</Mock>
        </>
      );
    case "prepare":
      return (
        <>
          <p className="bj-circuit-kicker">{stage.card.kicker}</p>
          <p className="bj-circuit-title">{stage.card.title}</p>
          {stage.card.groups.map((group) => (
            <div key={group.label} className="bj-circuit-group">
              <p className="bj-circuit-group-label">{group.label}</p>
              <ul className="bj-circuit-checks">
                {group.items.map((item) => (
                  <li key={item.text}>
                    <span className={item.mark === "✓" ? "bj-circuit-mark bj-circuit-mark--pass" : "bj-circuit-mark"} aria-hidden="true">
                      {item.mark}
                    </span>
                    <span className="bj-circuit-check-text">{item.text}</span>
                    <span className="bj-circuit-check-state">{item.state}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      );
    case "reach":
      return (
        <>
          <p className="bj-circuit-kicker">{stage.card.kicker}</p>
          <p className="bj-circuit-meta">{stage.card.to}</p>
          <p className="bj-circuit-subject">{stage.card.subject}</p>
          <Lines widths={["full", "wide", "mid"]} />
          <p className="bj-circuit-fact">{stage.card.note}</p>
          <p className="bj-circuit-chip">{stage.card.badge}</p>
        </>
      );
    case "practice":
      return (
        <>
          <p className="bj-circuit-kicker">{stage.card.kicker}</p>
          <p className="bj-circuit-subject">{stage.card.question}</p>
          <Lines widths={["full", "mid"]} />
          <p className="bj-circuit-fact">
            <span className="bj-circuit-fact-label">{stage.card.feedbackLabel}</span> {stage.card.feedback}
          </p>
          <p className="bj-circuit-meta">{stage.card.note}</p>
        </>
      );
    case "apply":
      return (
        <>
          <p className="bj-circuit-kicker">{stage.card.kicker}</p>
          <p className="bj-circuit-meta">{stage.card.page}</p>
          <Mock>{stage.card.mockAction}</Mock>
          <p className="bj-circuit-fact">
            <span className="bj-circuit-fact-label">{stage.card.leftLabel}</span> {stage.card.left}
          </p>
        </>
      );
  }
}

export function CircuitDemo() {
  const [active, setActive] = useState<StageId>(DEFAULT_STAGE);
  const list = useRef<HTMLOListElement>(null);
  const idBase = useId();
  const current = STAGES.find((stage) => stage.id === active) ?? STAGES[0];
  const position = STAGES.indexOf(current) + 1;

  // In the row layout (960px and up) Left and Right, Home and End move focus between the stage
  // buttons, without wrapping. They only move focus: a stage is picked with Enter or Space, so
  // reading along the row never rewrites the page. Under 960px the stages are a vertical list a
  // screen apart, and the keys are left alone: Up and Down scroll the page, Home and End go to its
  // ends, and Tab already reaches each button.
  function onKeyDown(event: KeyboardEvent<HTMLOListElement>) {
    if (!isRowLayout()) return;
    const buttons = Array.from(list.current?.querySelectorAll<HTMLButtonElement>("button.bj-circuit-stage-button") ?? []);
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (at === -1) return;
    let next: number | null = null;
    if (event.key === "ArrowRight") next = Math.min(at + 1, buttons.length - 1);
    if (event.key === "ArrowLeft") next = Math.max(at - 1, 0);
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = buttons.length - 1;
    if (next === null || next === at) return;
    event.preventDefault();
    buttons[next].focus();
  }

  return (
    <div className="bj-circuit">
      <p className="bj-circuit-sample-label">{LANDING.circuit.sampleLabel}</p>
      <div className="bj-circuit-frame">
        <span className="bj-circuit-trunk" aria-hidden="true" />
        <div role="group" aria-label={LANDING.circuit.stagesLabel} className="bj-circuit-group-wrap">
          <ol className="bj-circuit-stages" ref={list} onKeyDown={onKeyDown}>
            {STAGES.map((stage, index) => {
              const on = stage.id === active;
              const cardId = `${idBase}-card-${stage.id}`;
              return (
                <li key={stage.id} className="bj-circuit-stage" data-active={on}>
                  <button
                    type="button"
                    className="bj-circuit-stage-button bj-selectable"
                    aria-pressed={on}
                    aria-controls={cardId}
                    onClick={() => setActive(stage.id)}
                  >
                    <span className="bj-circuit-node" aria-hidden="true">
                      {index + 1}
                    </span>
                    <span className="bj-circuit-stage-label">{stage.label}</span>
                  </button>
                  <span className="bj-circuit-drop" aria-hidden="true" />
                  <p className="bj-circuit-summary">{stage.summary}</p>
                  <article id={cardId} className="bj-circuit-card" aria-label={`Sample card: ${stage.label}`}>
                    <div className="bj-circuit-card-body">
                      <CardBody stage={stage} />
                    </div>
                    <StaticTrailLine trail={stage.trail} className="bj-circuit-trail" />
                  </article>
                </li>
              );
            })}
          </ol>
        </div>

        <div className="bj-circuit-board">
          <p className="bj-circuit-group-label">{LANDING.circuit.boardLabel}</p>
          <ol className="bj-circuit-statuses" aria-label="Board statuses, in order">
            {BOARD_STATUSES.map((status) => (
              <li key={status.label}>
                <span className={status.label === "Offer" ? "bj-circuit-mark bj-circuit-mark--pass" : "bj-circuit-mark"} aria-hidden="true">
                  {status.mark}
                </span>{" "}
                {status.label}
              </li>
            ))}
          </ol>
          <p className="bj-circuit-meta">{LANDING.circuit.boardText}</p>
        </div>

        <section className="bj-circuit-demo" aria-label={LANDING.circuit.demoLabel}>
          <p className="bj-circuit-demo-tag">{LANDING.circuit.demoLabel}</p>
          <div className="bj-circuit-demo-body" role="status" aria-live="polite" aria-atomic="true">
            <div key={current.id} className="bj-circuit-demo-text">
              <span className="bj-visually-hidden">
                {`Stage ${position} of ${STAGES.length}, ${current.label}. `}
              </span>
              <p className="bj-circuit-eyebrow">Next step</p>
              <p className="bj-circuit-demo-sentence">{current.demo.sentence}</p>
              <p className="bj-circuit-demo-why">why: {current.demo.why.join(" · ")}</p>
              {current.demo.action !== null && (
                <span className="bj-visually-hidden">{`The sample shows a ${current.demo.action} button.`}</span>
              )}
            </div>
          </div>
          {current.demo.action !== null && (
            <div className="bj-circuit-demo-action">
              <Mock>{current.demo.action}</Mock>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
