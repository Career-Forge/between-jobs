import { describe, expect, it } from "vitest";
import { CLEARANCE_GAP, clearanceScroll } from "./focusClearance";

// A phone 800px tall: a 56px top bar and a 65px tab bar, so the clear band is y 56 to 735.
const EDGES = { top: 56, bottom: 735 };

describe("how far to scroll so a focused control is clear of the bars", () => {
  const CASES: [string, { top: number; bottom: number }, number][] = [
    ["fully clear, in the middle", { top: 300, bottom: 380 }, 0],
    ["just clear of the tab bar with the gap to spare", { top: 600, bottom: 735 - CLEARANCE_GAP }, 0],
    ["a field half under the tab bar (the real case: 698 to 777)", { top: 698, bottom: 777 }, 777 - (735 - CLEARANCE_GAP)],
    ["a button one pixel into the gap", { top: 650, bottom: 728 }, 1],
    ["a field entirely under the tab bar", { top: 760, bottom: 800 }, 800 - (735 - CLEARANCE_GAP)],
    ["a control under the top bar (Shift+Tab)", { top: 30, bottom: 80 }, 30 - (56 + CLEARANCE_GAP)],
    ["a control just below the top bar's gap", { top: 56 + CLEARANCE_GAP, bottom: 130 }, 0],
    ["a control with no size", { top: 700, bottom: 700 }, 0],
  ];
  it.each(CASES)("%s", (_name, rect, expected) => {
    expect(clearanceScroll(rect, EDGES)).toBe(expected);
  });

  it("lines a control taller than the room between the bars up with the top bar, so its start stays in view", () => {
    // 900px tall: it can not fit in the 679px band, and scrolling it clear of the tab bar would hide its start.
    expect(clearanceScroll({ top: 200, bottom: 1100 }, EDGES)).toBe(200 - (56 + CLEARANCE_GAP));
  });

  it("with no top bar (the edge at 0) only the tab bar matters", () => {
    expect(clearanceScroll({ top: 4, bottom: 50 }, { top: 0, bottom: 735 })).toBe(4 - CLEARANCE_GAP);
    expect(clearanceScroll({ top: 20, bottom: 50 }, { top: 0, bottom: 735 })).toBe(0);
  });
});
