import { useEffect } from "react";
import { useLocation } from "react-router-dom";

// An in-page link such as /#circuit has to do two things in this app, and the router does neither:
// scroll to the section, and put keyboard focus there, so the next Tab starts inside it instead of
// back at the top of the page. This hook does both whenever the address's fragment changes, on
// arrival as well as on a click, and again when the same link is followed twice (a navigation to the
// same address still gets a new location key).
//
// Focus goes to the section's first heading, which the page gives tabIndex={-1}; the section itself
// when it has none. The scroll is instant for anyone who has asked their system for less motion.

const HEADING = "h1, h2, h3, h4, h5, h6";

function fragmentId(hash: string): string | null {
  const raw = hash.replace(/^#/, "");
  if (raw === "") return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

export function useHashTarget(): void {
  const { hash, key } = useLocation();
  useEffect(() => {
    const id = fragmentId(hash);
    if (id === null) return;
    const target = document.getElementById(id);
    if (target === null) return;
    const reduced = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    const heading = target.matches(HEADING) ? target : (target.querySelector<HTMLElement>(HEADING) ?? target);
    if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
  }, [hash, key]);
}
