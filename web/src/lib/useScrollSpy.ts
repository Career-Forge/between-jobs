import { useEffect, useState } from "react";

// Which section of a long page is being read, for the jump bar to mark. It watches the page's own
// scrolling and sets nothing but a value: it never moves focus and never scrolls, so a person who is
// tabbing through the page is not pulled anywhere, and a smooth scroll is left to whoever started it.
//
// A section is "reached" once its top is above a line a little down the viewport (35% of its height,
// never less than 160px: far enough below a sticky bar that a section a link has just scrolled to counts
// as reached). The marked section is the last one reached, in the order the ids were given. At the very
// bottom of the page the last section is marked, since a short last section can never get its top that
// high. Before the first section is reached nothing is marked (null).

const LINE_FRACTION = 0.35;
const LINE_MINIMUM = 160;
const BOTTOM_SLACK = 2;

export function readingLine(viewportHeight: number): number {
  return Math.max(LINE_MINIMUM, viewportHeight * LINE_FRACTION);
}

// Pure: the id of the last section whose top is at or above `line`, given each section's top (null for
// one that is not on the page), or the last present one when the page is scrolled to its end.
export function reachedSection(tops: readonly { id: string; top: number | null }[], line: number, atBottom: boolean): string | null {
  let reached: string | null = null;
  let lastPresent: string | null = null;
  for (const { id, top } of tops) {
    if (top === null) continue;
    lastPresent = id;
    if (top <= line) reached = id;
  }
  return atBottom && lastPresent !== null ? lastPresent : reached;
}

export function useScrollSpy(ids: readonly string[], enabled: boolean): string | null {
  const [active, setActive] = useState<string | null>(null);
  const key = ids.join("\n");

  useEffect(() => {
    if (!enabled || ids.length === 0) {
      setActive(null);
      return;
    }
    let frame: number | null = null;
    let pending = false;
    const measure = () => {
      const root = document.documentElement;
      const tops = ids.map((id) => {
        const element = document.getElementById(id);
        return { id, top: element === null ? null : element.getBoundingClientRect().top };
      });
      const atBottom = window.innerHeight + window.scrollY >= root.scrollHeight - BOTTOM_SLACK && root.scrollHeight > window.innerHeight;
      setActive(reachedSection(tops, readingLine(window.innerHeight), atBottom));
    };
    // One measure per frame however many scroll events arrive.
    const schedule = () => {
      if (pending) return;
      pending = true;
      const run = () => {
        pending = false;
        measure();
      };
      if (typeof window.requestAnimationFrame === "function") frame = window.requestAnimationFrame(run);
      else run();
    };
    measure();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame !== null && typeof window.cancelAnimationFrame === "function") window.cancelAnimationFrame(frame);
    };
    // `ids` is read through `key`: a new array with the same ids must not restart the watch.
  }, [key, enabled]);

  return active;
}

// Whether `element` has scrolled out of view off the TOP of the screen (not merely below the fold).
// False where IntersectionObserver is missing, so a browser without it just never docks anything.
export function useScrolledPast(element: HTMLElement | null, enabled: boolean): boolean {
  const [past, setPast] = useState(false);
  useEffect(() => {
    if (!enabled || element === null || typeof IntersectionObserver === "undefined") {
      setPast(false);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry !== undefined) setPast(!entry.isIntersecting && entry.boundingClientRect.top < 0);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [element, enabled]);
  return past;
}
