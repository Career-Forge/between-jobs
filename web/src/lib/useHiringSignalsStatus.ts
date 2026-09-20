import { useEffect, useSyncExternalStore } from "react";
import { apiFetch } from "./api";
import {
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

const store = new HiringSignalsStatusStore(apiFetch);

export function useHiringSignalsStatus(): HiringStatus {
  // The third argument is the server snapshot: a static render has no
  // subscription to make, and React requires one.
  const status = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => {
    void store.ensure();
  }, []);
  return status;
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
