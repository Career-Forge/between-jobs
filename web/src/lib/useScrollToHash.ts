import { useEffect } from "react";

// Scrolls to the element a URL hash names, once the page it is on can have it.
// Applications Kanban K3's cross-nav (Generate Docs / Research Company / ...) jumps
// to a panel of the application workspace this way.
//
// `ready` is whether the page has rendered the thing the hash points at (the
// targeted element does not exist while the workspace is loading). It depends on
// `hash` too, because a hash-only navigation does not remount the page. And
// `alsoWhen` lists whatever ELSE decides whether the target is visible: a panel
// that mounts only after an asynchronous answer (the Hiring posts panel, gated on
// the server's feature status) sits in a slot that is hidden while it is empty, so
// scrolling to it before it exists is a silent no-op, and nothing else would
// scroll again when it arrived.
//
// The scroll is instant under prefers-reduced-motion. If the target is a heading, or holds one, that
// has a tabindex (the Settings sections do), keyboard focus moves to it as well.
export function useScrollToHash(
  ready: boolean,
  hash: string,
  ...alsoWhen: readonly unknown[]
): void {
  useEffect(() => {
    if (!ready) return;
    const targetId = hash.replace(/^#/, "");
    if (!targetId) return;
    const target = document.getElementById(targetId);
    if (!target) return;
    target.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
    // Arriving by a link should leave the keyboard where the page now is, not at the top of it.
    // Only a heading that has asked to be focusable (a tabindex of -1) takes it.
    const heading = headingOf(target);
    if (heading !== null) heading.focus({ preventScroll: true });
  }, [ready, hash, ...alsoWhen]);
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const HEADINGS = "h1, h2, h3, h4, h5, h6";

// The target itself when it is a heading, otherwise the first heading inside it, if it can take focus.
function headingOf(target: Element): HTMLElement | null {
  const heading = target.matches?.(HEADINGS) ? target : (target.querySelector?.(HEADINGS) ?? null);
  if (heading === null || typeof (heading as HTMLElement).focus !== "function" || !heading.hasAttribute("tabindex")) return null;
  return heading as HTMLElement;
}
