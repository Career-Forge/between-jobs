import { useEffect } from "react";
import { clearanceScroll, type BarEdges } from "./focusClearance";

// After keyboard focus lands on a control, scrolls the page just far enough that the phone's top bar
// and tab bar are not on top of it (lib/focusClearance.ts says why the browser does not). The shell
// calls it once. It does nothing on a wide screen, where both bars are display:none, for a control
// that is itself fixed or sticky or inside a modal (scrolling the page would not move it), or for a
// click on a button (the person is already looking at what they pressed).

const TOP_BAR = ".bj-topbar";
const TAB_BAR = ".bj-tabbar";

function shownEdge(selector: string, edge: "top" | "bottom"): number | null {
  const bar = document.querySelector<HTMLElement>(selector);
  if (bar === null || window.getComputedStyle(bar).display === "none") return null;
  const rect = bar.getBoundingClientRect();
  return rect.height === 0 ? null : rect[edge];
}

function barEdges(): BarEdges | null {
  const top = shownEdge(TOP_BAR, "bottom");
  const bottom = shownEdge(TAB_BAR, "top");
  if (top === null && bottom === null) return null;
  return { top: top ?? 0, bottom: bottom ?? window.innerHeight };
}

function keyboardFocus(element: HTMLElement): boolean {
  try {
    return element.matches(":focus-visible");
  } catch {
    return true;
  }
}

// Fixed and sticky things stay where they are when the page scrolls, and a modal sits above the bars.
function staysPut(element: HTMLElement): boolean {
  if (element.closest("dialog, [aria-modal='true']") !== null) return true;
  for (let node: HTMLElement | null = element; node !== null; node = node.parentElement) {
    const position = window.getComputedStyle(node).position;
    if (position === "fixed" || position === "sticky") return true;
  }
  return false;
}

export function useFocusClearOfBars(): void {
  useEffect(() => {
    let frame = 0;
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !keyboardFocus(target)) return;
      // After the browser's own scroll for this focus, which happens once the event has been handled.
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        if (document.activeElement !== target || staysPut(target)) return;
        const edges = barEdges();
        if (edges === null) return;
        const by = clearanceScroll(target.getBoundingClientRect(), edges);
        if (by !== 0) window.scrollBy({ top: by, behavior: "instant" });
      });
    };
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      window.cancelAnimationFrame(frame);
    };
  }, []);
}
