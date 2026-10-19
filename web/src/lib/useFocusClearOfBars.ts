import { useEffect } from "react";
import { clearanceScroll, type BarEdges } from "./focusClearance";

// After keyboard focus lands on a control, scrolls the page just far enough that the sticky bars (the phone's
// top bar and tab bar, the workspace's section bar) are not on top of it (lib/focusClearance.ts says why the
// browser does not). The shell calls it once. It does nothing where no bar is drawn, for a control that is itself
// fixed or sticky or inside a modal (scrolling the page would not move it), or for a click on a button (the
// person is already looking at what they pressed).

// The bars that can sit over the top of the page, from the one at the very top down: the phone's top bar, then
// the workspace's section bar in whichever form the width gives it (a sticky row from 1280px, a dock from
// 960px, a sticky select row under the phone's top bar).
const TOP_BARS = [".bj-topbar", ".bj-jump--sticky", ".bj-jump--dock", ".bj-jump--select"];
const TAB_BAR = ".bj-tabbar";

function drawn(bar: Element): DOMRect | null {
  if (window.getComputedStyle(bar).display === "none") return null;
  const rect = bar.getBoundingClientRect();
  return rect.height === 0 ? null : rect;
}

// How far down the top bars reach. A bar counts only while it is stuck, that is, while it starts where the bars
// above it end (a sticky bar still in the flow of the page, further down, is not over anything).
function topEdge(): number | null {
  let edge: number | null = null;
  for (const selector of TOP_BARS) {
    for (const bar of document.querySelectorAll(selector)) {
      const rect = drawn(bar);
      if (rect !== null && rect.top <= (edge ?? 0) + 1 && rect.bottom > (edge ?? 0)) edge = rect.bottom;
    }
  }
  return edge;
}

function bottomEdge(): number | null {
  const bar = document.querySelector(TAB_BAR);
  const rect = bar === null ? null : drawn(bar);
  return rect === null ? null : rect.top;
}

function barEdges(): BarEdges | null {
  const top = topEdge();
  const bottom = bottomEdge();
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
