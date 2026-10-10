// What the server was started with (GET /capabilities), held in ONE place that every reader looks
// at, with the rules for asking again.
//
// WHY A STORE AND NOT A HOOK'S OWN STATE. The tester-programme gate sits above every page of the
// signed-in shell and mounts once; the Profile line and the enrollment page are separate readers
// that mount and unmount as the person moves about. When each held its own copy, a failed first
// ask left the gate on "unavailable" for the whole session (the gate is the one reader that never
// remounts), while a page that asked later learned "required" and said so: two parts of one screen
// disagreeing. Here an answer learned by any reader is the answer for all of them.
//
// THE RULES.
//   - A definite answer ("ready") is kept; a failed ask never takes it back (a refresh that fails
//     leaves what was known).
//   - A failed ask is "unavailable", and it may be retried, at most MAX_CAPABILITY_RETRIES times in
//     all for as long as the page lives (a hard cap: a server that is down is not hammered by
//     every navigation).
//   - `refresh` asks again whatever is known. It is for the moment the server contradicts the
//     answer (a refusal for enrollment from a server this app believed did not require it). It is
//     driven by a person's action, never by a timer, and one ask at a time collapses a burst of
//     refusals into one request.
//
// Pure module: the fetcher is injected (api.ts pulls in the Supabase client, which throws at import
// time without env vars) and there is no React here -- useCapabilities.ts binds it.

import { loadCapabilities, type CapabilitiesFetcher, type CapabilitiesState } from "./capabilities";

export const MAX_CAPABILITY_RETRIES = 3;

export interface CapabilitiesStore {
  /** The current answer. Stable between changes (the same object until something changes). */
  getSnapshot: () => CapabilitiesState;
  subscribe: (listener: () => void) => () => void;
  /** Asks if nothing has been asked yet; after a failed ask, retries within the cap. */
  ensure: () => void;
  /** After a failed ask, asks again if the cap allows. Returns whether it asked. */
  retry: () => boolean;
  /** Asks again, whatever is known; a failure leaves a known answer as it was. */
  refresh: () => void;
}

const CHECKING: CapabilitiesState = { kind: "checking" };
const UNAVAILABLE: CapabilitiesState = { kind: "unavailable" };

function sameAnswer(a: CapabilitiesState, b: CapabilitiesState): boolean {
  if (a.kind !== "ready" || b.kind !== "ready") return a.kind === b.kind;
  return (
    a.capabilities.telegram === b.capabilities.telegram &&
    a.capabilities.testerProgramRequired === b.capabilities.testerProgramRequired &&
    a.capabilities.telegramBotUsername === b.capabilities.telegramBotUsername &&
    a.capabilities.engine === b.capabilities.engine
  );
}

export function createCapabilitiesStore(fetcher: CapabilitiesFetcher): CapabilitiesStore {
  let state: CapabilitiesState = CHECKING;
  let asking = false;
  let retriesUsed = 0;
  const listeners = new Set<() => void>();

  function set(next: CapabilitiesState): void {
    // A new answer that says the same thing is not a change: readers keep what they have and do
    // not draw again.
    if (sameAnswer(state, next)) return;
    state = next;
    for (const listener of [...listeners]) listener();
  }

  function ask(): void {
    if (asking) return;
    asking = true;
    void loadCapabilities(fetcher).then((next) => {
      asking = false;
      if (next.kind === "ready") {
        set(next);
      } else if (state.kind !== "ready") {
        set(UNAVAILABLE);
      }
    });
  }

  function retry(): boolean {
    if (state.kind !== "unavailable" || asking || retriesUsed >= MAX_CAPABILITY_RETRIES) {
      return false;
    }
    retriesUsed += 1;
    ask();
    return true;
  }

  return {
    getSnapshot: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    ensure: () => {
      if (state.kind === "checking") ask();
      else retry();
    },
    retry,
    refresh: ask,
  };
}
