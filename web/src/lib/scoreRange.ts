// What counts as a score a page may print and rank by: a real number from 0 to the top of the scale
// (100 for a fit score and for each part of it). NaN, Infinity, text, a missing value and a number outside
// the scale are no score at all -- not shown as written ("150 / 100"), not rounded or clamped into range,
// and not sorted on -- and what remains of a row (its tier word and the ladder) stands alone, as for a
// score that never came. A part of the score outside the scale is a part with no number, as a missing one
// is: no bar, no number beside the lowest part, no line in the receipt. A fraction inside the scale is a
// real score and is printed as it came.
//
// One predicate, so the number a row prints, the label the ladder gives it, the parts it lists and the
// order "Best fit" makes can never disagree about what was a score.

export function isScoreWithin(value: unknown, max = 100): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= max;
}
