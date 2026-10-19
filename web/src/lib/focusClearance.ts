// Keeping a keyboard's place visible on a phone. Under 960px the shell has two opaque bars over the page:
// the sticky top bar and the fixed tab bar. When Tab lands on a control that is only partly under one,
// the browser counts it as already visible and leaves it there, so the focused field can be more than
// half hidden (a textarea near the bottom of the screen, under the tab bar). The page's
// `scroll-padding` does not change that for focus, only for fragment links and scrollIntoView, so
// this works out how far to scroll instead. It is a pure function of where the control and the bars
// are; the hook that reads those and scrolls is lib/useFocusClearOfBars.ts.

export interface VerticalSpan {
  top: number;
  bottom: number;
}

/** Where the bars end: `top` is the lower edge of the top bar, `bottom` the upper edge of the tab bar (viewport y). */
export interface BarEdges {
  top: number;
  bottom: number;
}

/** Air kept between a focused control and a bar, so the focus ring is not cut off either. */
export const CLEARANCE_GAP = 8;

/**
 * How far to scroll the page, in px (positive is down), so the control is clear of both bars, or 0 when
 * it already is. A control taller than the room between the bars is lined up with the top bar, so its
 * start is what stays in view.
 */
export function clearanceScroll(rect: VerticalSpan, edges: BarEdges, gap: number = CLEARANCE_GAP): number {
  if (rect.bottom <= rect.top) return 0;
  const clearTop = edges.top + gap;
  const clearBottom = edges.bottom - gap;
  if (rect.top < clearTop) return rect.top - clearTop;
  if (rect.bottom > clearBottom) return Math.min(rect.bottom - clearBottom, rect.top - clearTop);
  return 0;
}
