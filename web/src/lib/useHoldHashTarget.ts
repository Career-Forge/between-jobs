import { useEffect } from "react";

// Keeps the section a link arrived at in view while the page above it is still growing.
//
// useScrollToHash scrolls to the target once, on arrival. The workspace's panels then load and push the
// target further down; below 1280px the browser's scroll anchoring keeps it in place, but with the
// document layout's margin column (1280px and up) it does not, and the person is left looking at a
// section that was never the one they asked for while keyboard focus sits on an off-screen heading.
//
// So, from arrival, every time the page's height changes the target is brought back to the top, until
// the person takes over: any wheel, touch, pointer or key press ends it, as does the hash changing, the
// page unmounting, and a hard cap (a page that is still reflowing after that is not going to settle).
// A change that leaves the target where it is does nothing, since scrolling to a place already there
// moves nothing. Under prefers-reduced-motion and otherwise the correction is instant: it is repairing
// a scroll that already happened, not making a new one.

export const HOLD_LIMIT_MS = 20_000;

const TAKEOVER = ["wheel", "touchstart", "pointerdown", "keydown"] as const;

export function useHoldHashTarget(ready: boolean, hash: string): void {
  useEffect(() => {
    if (!ready || typeof ResizeObserver === "undefined") return;
    const targetId = hash.replace(/^#/, "");
    if (targetId === "") return;
    const hold = () => document.getElementById(targetId)?.scrollIntoView({ block: "start", behavior: "instant" });
    // observe() reports the size it starts with straight away; that is the arrival, not a change, and
    // acting on it would cut short the smooth scroll useScrollToHash has just started.
    let reported = false;
    const observer = new ResizeObserver(() => {
      if (!reported) reported = true;
      else hold();
    });
    const stop = () => {
      observer.disconnect();
      window.clearTimeout(cap);
      for (const type of TAKEOVER) window.removeEventListener(type, stop, true);
    };
    const cap = window.setTimeout(stop, HOLD_LIMIT_MS);
    for (const type of TAKEOVER) window.addEventListener(type, stop, { capture: true, passive: true });
    observer.observe(document.body);
    return stop;
  }, [ready, hash]);
}
