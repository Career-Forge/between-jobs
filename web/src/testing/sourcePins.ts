// Helpers for tests that pin the shape of a function by reading its source, for code the package
// cannot run (there is no DOM environment). Only used by tests.

/** The code (comments dropped) between the first `catch (e) {` in `source` and `call`, or null when
 *  either is missing. "" means the call is the first statement of the catch block: no guard, no
 *  early return and no other statement runs before it. */
export function codeBetweenCatchAndCall(source: string, call: string): string | null {
  const opening = "catch (e) {";
  const caught = source.indexOf(opening);
  if (caught === -1) return null;
  const at = source.indexOf(call, caught);
  if (at === -1) return null;
  return source
    .slice(caught + opening.length, at)
    .replace(/\/\/.*$/gm, "")
    .trim();
}

/** The source of one function: from the first `start` to the first `end` after it (exclusive), or
 *  null when either is missing. */
export function sliceBetween(source: string, start: string, end: string): string | null {
  const from = source.indexOf(start);
  if (from === -1) return null;
  const to = source.indexOf(end, from + start.length);
  return to === -1 ? null : source.slice(from, to);
}
