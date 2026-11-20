// What a person is shown for a job's fit. The engine sorts a score into four bins (Strong, Good, Mixed,
// Poor: ScoreBin in discoverTypes.ts) and the API sends those words. This file is the one place that turns
// a bin into the grade drawn on screen. It is display only: the sort by score, the rule for which rows
// read as scored, and every request keep the engine's bins.
//
//   engine bin     grade shown   colour (tokens.css)
//   Strong         Good          --bj-fit-good, green
//   Good           Decent        --bj-fit-decent, yellow
//   Mixed, Poor    Bad fit       --bj-fit-bad, rose
//
// THE WORD "GOOD" MOVES. The engine's second bin is named "Good", and it is shown as "Decent". The word
// "Good" on screen means the engine's best bin, Strong. That is on purpose: the top bin is the only one
// the saved-search matcher brings to a person as a match, so "Good" is kept for exactly those jobs and a
// second-bin job is not called by the same word. When reading code, a "Good" in API data or a fixture is
// the engine's bin; a "Good" in a label, a chip or a sentence is this file's top grade.
//
// A bin this file does not know has no grade. The caller draws it as "Not scored": an unreadable bin is
// never guessed into a grade.

import type { ScoreBin } from "./discoverTypes";

export type FitGrade = "good" | "decent" | "bad";

/** The grades as a ladder's rungs, lowest first. */
export const GRADE_LADDER: readonly FitGrade[] = ["bad", "decent", "good"];

/** The word a person reads for each grade. */
export const GRADE_LABEL: Readonly<Record<FitGrade, string>> = {
  good: "Good",
  decent: "Decent",
  bad: "Bad fit",
};

// A Map, not an object: a bin is text from a response, and an object would answer "constructor" or
// "toString" with something that is not a grade.
const GRADE_OF_BIN: ReadonlyMap<unknown, FitGrade> = new Map<ScoreBin, FitGrade>([
  ["Strong", "good"],
  ["Good", "decent"],
  ["Mixed", "bad"],
  ["Poor", "bad"],
]);

/** The grade shown for an engine bin; null for anything that is not one of the engine's four bins. */
export function gradeOfBin(bin: unknown): FitGrade | null {
  return GRADE_OF_BIN.get(bin) ?? null;
}

/** True when `bin` is one of the engine's four bins, which is exactly when it has a grade. */
export function isEngineBin(bin: unknown): bin is ScoreBin {
  return GRADE_OF_BIN.has(bin);
}

/** True when `value` is one of the three grades (and not, say, an engine bin or a label). */
export function isFitGrade(value: unknown): value is FitGrade {
  return GRADE_LADDER.some((grade) => grade === value);
}
