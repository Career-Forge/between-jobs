import { useEffect, useRef } from "react";
import { NEXT_STEP_FOCUS_ID } from "../components/paper/NextStepBand";

// Keeps keyboard focus on the next-step band when the button that had it is replaced. A band's
// button is often the thing a person pressed to start a request (Search, Save this search, Try
// again), and while the request runs the band says what is happening with no button, so the control
// that held focus is gone and focus would drop to the top of the page. The page `arm`s this when the
// band's button was pressed; after the render that replaced it, focus goes to the band's own
// sentence (it takes focus without being a tab stop), and stays there until the request has
// settled. Focus that is anywhere real is never taken: the person has moved on.

export interface BandFocus {
  /** The band's step was just activated: hold focus on the band across the change. */
  arm: () => void;
  /** Arm only when focus is on the band's own button (a form submitted from outside it). */
  armIfBandFocused: () => void;
}

export function useBandFocus(settled: boolean): BandFocus {
  const armed = useRef(false);
  useEffect(() => {
    if (!armed.current) return;
    const active = document.activeElement;
    // Focus on the band itself is still ours (the sentence it was handed may be swapped for a button
    // when the step changes); focus anywhere else is a person who has moved on.
    const onBand = active instanceof HTMLElement && active.closest(".bj-band") !== null;
    if (active !== null && active !== document.body && !onBand) {
      armed.current = false;
      return;
    }
    const target = document.getElementById(NEXT_STEP_FOCUS_ID);
    if (target !== null && target !== active) target.focus();
    if (settled) armed.current = false;
  });
  return {
    arm: () => {
      armed.current = true;
    },
    armIfBandFocused: () => {
      const active = document.activeElement;
      armed.current = active instanceof HTMLElement && active.id === NEXT_STEP_FOCUS_ID;
    },
  };
}
