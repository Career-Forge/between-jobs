import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { NextStepAction, NextStepOutcome } from "../../lib/nextStep";
import "../../styles/paper.css";

// The next-step band: one per page, directly under the page header. It says in plain words what to
// do now, shows the facts that picked that step, and holds the page's ONLY cyan fill.
//
//   <NextStepBand outcome={nextStep(input)} onAction={(intent) => handlers[intent]?.()} />
//
//   lit      eyebrow "Next step", the sentence, a `why:` line, one cyan button (and at most one
//            outlined secondary). Used when the outcome carries an action.
//   unlit    the sentence and the why line, no button, and nothing else on the page turns cyan to
//            fill the gap. An outcome with `action: null`.
//   loading  the eyebrow and "Loading..." only, until the inputs have arrived.
//   none     renders nothing (legal pages: no next step is not the same as "nothing needs you").
//   failure  a lit or unlit band whose step has `tone: "failure"` carries the red cross before the
//            sentence.
//
// An action is a route link, a handler (`onAction` gets the action's `intent`), a form submit, or an
// external link; the band never fires anything itself and never submits for the person. When the
// sentence changes after the first one, it fades in over 200ms (not under prefers-reduced-motion)
// and is announced once through a polite live region. The fade is a class swap on the one body
// element, never a remount, so the action (or the sentence a skip link focused) keeps keyboard focus
// when the next step arrives.
//
// An action the band cannot honor -- an external address that is not http(s) -- is dropped before
// anything is decided, so the band is then unlit: no empty button row, and the sentence takes the
// focus id instead of pointing at a control that is not there.
//
// Not built: the phone's docked sticky 56px bar (optional, and not part of this component).

// The id of the element a "Skip to next step" link should focus: the button when the band is lit,
// otherwise the sentence (which takes focus without being a tab stop).
export const NEXT_STEP_FOCUS_ID = "next-step-focus";

export interface NextStepBandProps {
  outcome: NextStepOutcome;
  /** Called with the `intent` of a `button` action when it is pressed. */
  onAction?: (intent: string) => void;
}

// A route the band links to, or an address it opens, is data from a rule. An external address
// outside http(s) -- a javascript: URL smuggled in through a scraped posting -- is not a link.
function safeExternal(href: string): string | null {
  return /^https?:\/\//i.test(href) ? href : null;
}

// The action the band can really draw: an external address outside http(s) is no action at all.
function usableAction(action: NextStepAction | null | undefined): NextStepAction | null {
  if (action === null || action === undefined) return null;
  if (action.kind === "external" && safeExternal(action.href) === null) return null;
  return action;
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

function ActionControl({
  action,
  primary,
  onAction,
  focusId,
}: {
  action: NextStepAction;
  primary: boolean;
  onAction?: (intent: string) => void;
  focusId?: string;
}) {
  const button = primary ? "bj-primary bj-band-action" : "bj-band-action bj-band-secondary";
  const link = primary ? "bj-button-link bj-button-link-primary bj-band-action" : "bj-button-link bj-band-action bj-band-secondary";
  switch (action.kind) {
    case "link":
      return (
        <Link id={focusId} className={link} to={action.to}>
          {action.label}
        </Link>
      );
    case "external": {
      // Already filtered by usableAction; checked again so this component can never draw a bad link.
      const href = safeExternal(action.href);
      if (href === null) return null;
      return (
        <a id={focusId} className={link} href={href} target="_blank" rel="noopener noreferrer">
          {action.label}
        </a>
      );
    }
    case "submit":
      return (
        <button id={focusId} type="submit" form={action.form} className={button}>
          {action.label}
        </button>
      );
    case "button":
      return (
        <button id={focusId} type="button" className={button} onClick={() => onAction?.(action.intent)}>
          {action.label}
        </button>
      );
  }
}

export function NextStepBand({ outcome, onAction }: NextStepBandProps) {
  const step = outcome.status === "ready" ? outcome.step : null;
  const sentence = step?.sentence ?? null;
  const lastSentence = useRef<string | null>(null);
  // 0: no change yet. 1 and 2 alternate, each naming its own copy of the fade keyframes, so every
  // new sentence restarts the animation on the same element.
  const [fade, setFade] = useState<0 | 1 | 2>(0);
  const [announcement, setAnnouncement] = useState("");
  const hintId = useId();

  // The first sentence is the page arriving, not news: only a later, different one fades and is announced.
  useEffect(() => {
    if (sentence === null) return;
    if (lastSentence.current !== null && lastSentence.current !== sentence) {
      setFade((current) => (current === 1 ? 2 : 1));
      setAnnouncement(sentence);
    }
    lastSentence.current = sentence;
  }, [sentence]);

  if (outcome.status === "none") return null;

  const region = (
    <div className="bj-band-live" role="status" aria-live="polite">
      {announcement}
    </div>
  );

  if (step === null) {
    return (
      <section className="bj-band bj-band--loading" aria-label="Next step" aria-busy="true">
        <p className="bj-band-eyebrow">Next step</p>
        <p className="bj-band-loading">Loading...</p>
        {region}
      </section>
    );
  }

  const action = usableAction(step.action);
  const secondary = usableAction(step.secondary);
  const lit = action !== null;
  const focusOnSentence = !lit;
  const locked = lit && step.locked !== undefined;
  const bodyClass = fade === 0 ? "bj-band-body" : `bj-band-body bj-band-body--fade-${fade === 1 ? "a" : "b"}`;

  return (
    <section className={`bj-band ${lit ? "bj-band--lit" : "bj-band--unlit"}`} aria-label="Next step">
      <div className={bodyClass}>
        <div className="bj-band-text">
          <p className="bj-band-eyebrow">Next step</p>
          <p
            className="bj-band-sentence"
            id={focusOnSentence ? NEXT_STEP_FOCUS_ID : undefined}
            tabIndex={focusOnSentence ? -1 : undefined}
          >
            {step.tone === "failure" && (
              <span className="bj-band-glyph" aria-hidden="true">
                {"✕"}
              </span>
            )}
            {step.sentence}
          </p>
          {step.why.length > 0 && <p className="bj-band-why">why: {step.why.join(" · ")}</p>}
        </div>
        {action !== null && (
          <div className="bj-band-actions">
            {locked ? (
              <>
                <button id={NEXT_STEP_FOCUS_ID} type="button" className="bj-band-action bj-band-locked" aria-disabled="true" aria-describedby={hintId}>
                  <LockIcon />
                  {action.label}
                </button>
                <p id={hintId} className="bj-band-hint">
                  {step.locked?.hint}
                </p>
              </>
            ) : (
              <ActionControl action={action} primary onAction={onAction} focusId={NEXT_STEP_FOCUS_ID} />
            )}
            {secondary !== null && <ActionControl action={secondary} primary={false} onAction={onAction} />}
          </div>
        )}
      </div>
      {region}
    </section>
  );
}
