import { useEffect, useSyncExternalStore } from "react";
import { useAuth } from "../auth";
import { apiFetch } from "./api";
import { CAPABILITIES_TIMEOUT_MS, type CapabilitiesState } from "./capabilities";
import { createCapabilitiesStore } from "./capabilitiesStore";
import { deadlineSignal } from "./deadline";

// The one reader of GET /capabilities bound to React. The answer lives in a module-level store
// (capabilitiesStore.ts) read through useSyncExternalStore, so every reader -- the gate above the
// pages, the Profile line, the enrollment page -- sees the same one, and one that any of them
// learns reaches the rest at once. A definite answer is kept for the session; a failed ask is
// retried a few times at most (the cap is the store's). The reading and deciding is
// capabilities.ts, and the rules for asking again are the store's; this is only the wiring, and the
// only place that imports the API client, so those modules stay loadable from a test.

const store = createCapabilitiesStore(<T>(path: string) =>
  apiFetch<T>(path, { signal: deadlineSignal(CAPABILITIES_TIMEOUT_MS) }),
);

export function useCapabilities(): CapabilitiesState {
  // Part of the answer is the signed-in person's own (whether Gmail is open to them), so the store
  // is told who that is, and starts over when it becomes someone else.
  const userId = useAuth().session?.user.id ?? null;
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);

  useEffect(() => {
    // In this order, and no other: setPerson starts the store over when it is someone else (back
    // to "checking", nothing in flight), and ensure then asks for them. Asking first would see the
    // previous person's answer, do nothing, and be followed by a reset with no ask in flight: the
    // next person would wait on "Loading..." until they reloaded. (useCapabilities.test.ts runs it.)
    store.setPerson(userId);
    // Nobody signed in has nothing to ask about.
    if (userId !== null) store.ensure();
  }, [userId]);

  return state;
}

/** After a failed ask, asks again if the retry cap allows. Returns whether it asked. */
export function retryCapabilities(): boolean {
  return store.retry();
}

/** Asks again whatever is known: the server just contradicted what this app believed about it. */
export function refreshCapabilities(): void {
  store.refresh();
}
