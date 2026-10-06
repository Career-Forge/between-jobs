// A deadline for a request that has none of its own. `fetch` waits as long as the browser or a
// proxy lets it, and a page that is drawn only once the answer arrives (the tester-programme gate
// in front of the signed-in shell) would stay blank for that long. A request given one of these
// fails with a timeout instead, which the callers already read as "could not be asked".
//
// Pure module: nothing here touches the API client.

/** A signal that aborts after `ms` milliseconds, or undefined where the platform has no
 *  `AbortSignal.timeout` (the request then has no deadline, exactly as before this existed). */
export function deadlineSignal(ms: number): AbortSignal | undefined {
  if (typeof AbortSignal === "undefined" || typeof AbortSignal.timeout !== "function") {
    return undefined;
  }
  return AbortSignal.timeout(ms);
}

/** Whether a thrown value is a request that was aborted or timed out (its deadline passing, or
 *  the page leaving). Read structurally: a DOMException is not always an `Error`. */
export function isTimeoutError(error: unknown): boolean {
  if (typeof error !== "object" || error === null || !("name" in error)) return false;
  const name = (error as { name?: unknown }).name;
  return name === "TimeoutError" || name === "AbortError";
}
