// The arithmetic behind a segmented bar: how many of its cells are filled for a value out of a
// maximum. Pure, so the one place that rounds is the one place that is tested.
//
// A bar is never a bare number and never a gauge: it is a row of cells, filled or hollow, drawn next
// to the words that say the value. The cells must therefore never say something the words do not:
//
//  - a value of 0 (or below, or unknown) fills nothing, and a value at or above the maximum fills
//    everything;
//  - a value strictly between the two never rounds to an empty bar or to a full one, so "some" never
//    reads as "none" and "almost" never reads as "all";
//  - when there is one cell per point, the filled count is the value itself;
//  - more value never fills fewer cells.

// Cells for a maximum when the caller does not choose: one per point where that fits a phone
// (35 cells of 7px with 2px gaps is 315px), otherwise twenty.
export const MAX_ONE_PER_POINT = 35;
export const COARSE_SEGMENTS = 20;

export function defaultSegments(max: number): number {
  return Number.isInteger(max) && max >= 1 && max <= MAX_ONE_PER_POINT ? max : COARSE_SEGMENTS;
}

function usable(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function filledSegments(value: number | null | undefined, max: number, segments: number): number {
  if (!usable(value) || !usable(max) || max <= 0 || !Number.isInteger(segments) || segments < 1) return 0;
  if (value <= 0) return 0;
  if (value >= max) return segments;
  const rounded = Math.round((value * segments) / max);
  return Math.min(segments - 1, Math.max(1, rounded));
}

// Cells for the exact-but-unearned part (management credit only): drawn hatched after the filled
// ones, so it can never be mistaken for earned points. Zero when there is no such headroom or no
// room left; at least one cell when there is both, so a real headroom is never invisible.
export function hatchedSegments(value: number | null | undefined, headroom: number | null | undefined, max: number, segments: number): number {
  if (!usable(value) || !usable(headroom) || headroom <= 0) return 0;
  const filled = filledSegments(value, max, segments);
  if (filled >= segments) return 0;
  const through = filledSegments(Math.min(value + headroom, max), max, segments);
  return Math.min(segments - filled, Math.max(1, through - filled));
}
