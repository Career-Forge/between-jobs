import { NEXT_STEP_FOCUS_ID } from "./paper/NextStepBand";

// "Skip to next step": the first tab stop on a page that has a next-step band. It moves focus to the
// band's button, or to its sentence when the band has no button, and leaves the address bar alone.
// A page with no band (the legal pages) has only "Skip to content" (SkipLink.tsx), which follows.

export function NextStepSkipLink() {
  return (
    <a
      className="bj-skip-link"
      href={`#${NEXT_STEP_FOCUS_ID}`}
      onClick={(event) => {
        const target = document.getElementById(NEXT_STEP_FOCUS_ID);
        if (target === null) return;
        event.preventDefault();
        target.focus();
      }}
    >
      Skip to next step
    </a>
  );
}
