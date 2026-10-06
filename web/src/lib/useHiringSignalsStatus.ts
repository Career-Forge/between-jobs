import { useEffect, useSyncExternalStore } from "react";
import { useAuth } from "../auth";
import { apiFetch } from "./api";
import {
  CHECKING,
  HiringSignalsStatusStore,
  type HiringStatus,
  isHiringSignalsEnabled,
} from "./hiringSignalsStatus";

// The one shared reader of GET /hiring-signals/status (Hiring Signals P4) bound to
// React. The store, its once-per-session answer and its retry-after-failure rules
// are hiringSignalsStatus.ts; this file is only the wiring, and is the only place
// that imports the API client, so that module stays loadable from a plain test.
//
// Every consumer calls `ensure()` when it mounts: the first asks the server, the
// rest join that request or read the answer already kept. StrictMode's second
// mount is one of those.
//
// The answer is the signed-in person's own (the server can limit the feature to chosen
// people), so the store is told who is signed in, and a reader whose person is not the one the
// store holds sees "checking", never the previous person's answer.

const store = new HiringSignalsStatusStore(apiFetch);

export function useHiringSignalsStatus(): HiringStatus {
  const userId = useAuth().session?.user.id ?? null;
  // The third argument is the server snapshot: a static render has no
  // subscription to make, and React requires one.
  const kept = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => {
    store.setPerson(userId);
    // Nobody signed in has nothing to ask about.
    if (userId !== null) void store.ensure();
  }, [userId]);
  return store.isFor(userId) ? kept : CHECKING;
}

// For entry points (a nav item, a menu entry, a panel) that should exist only
// while the feature is known to be on: not while it is still being asked about,
// and not when asking failed.
export function useHiringSignalsEnabled(): boolean {
  return isHiringSignalsEnabled(useHiringSignalsStatus());
}

// The tab's "Try again" after the status request itself failed.
export function refreshHiringSignalsStatus(): void {
  void store.refresh();
}
