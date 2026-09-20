// Hiring Signals P4 -- "is this feature switched on?", asked of the server once.
//
// The server can turn the whole feature off (DISABLE_HIRING_SIGNALS), in which
// case every hiring-signals route answers 404 FEATURE_DISABLED. Until P4 the web
// app only found that out by trying: P3's per-application panel asked for its
// saved posts, got the 404 and hid itself, and the static "Find Hiring Posts"
// entry in the Applications menu (which has nothing to ask) showed regardless --
// a link to a panel that was never going to appear. The status route
// (GET /hiring-signals/status -> { enabled }) exists to answer that question
// without side effects, and this module is its one reader.
//
// ONE ANSWER, SHARED. The nav, the Applications menus, the application
// workspace and the tab itself all need the same fact, so it lives in a single
// store instead of a request per consumer. A definite answer (enabled or
// disabled) is kept for the session -- the flag is a server setting, not
// something a person toggles mid-visit. A FAILED ask is deliberately NOT kept:
// the next consumer to mount asks again, and the tab offers a retry, so a network
// blip while the shell was loading does not switch the feature off until reload.
//
// WHILE IT IS NOT KNOWN, THE FEATURE IS HIDDEN. Showing a nav item that then
// disappears is worse than one that appears a moment later, and the cost of the
// wait is one small request the shell already needs to make. So `checking` and
// `unavailable` both hide the entry points; only `enabled` shows them.
//
// Pure module: the fetcher is injected (api.ts pulls in the Supabase client,
// which throws at import time without env vars) and there is no React here --
// the hook that binds this to the app lives in useHiringSignalsStatus.ts.

import { CROSS_NAV_HASH, type CrossNavItem } from "./applicationsBoard";
import { classifyFailure } from "./hiringSignals";
import type { Fetcher } from "./hiringSignalsClient";
import { isRecord } from "./hiringSignalsTypes";

export const STATUS_PATH = "/hiring-signals/status";

export type HiringStatus =
  // Not asked yet, or the ask is in flight.
  | { kind: "checking" }
  | { kind: "enabled" }
  // The server has the feature switched off (or does not have it at all).
  | { kind: "disabled" }
  // The last ask failed (network, auth, a server error). Asking again is allowed.
  | { kind: "unavailable" };

// Whether the entry points (the nav item, the Applications menu jump, the workspace
// panel) may be shown: only for a DEFINITE "on". Not while the answer is still being
// fetched and not when asking failed -- showing an entry that then disappears is worse
// than one that appears a moment later. The one definition of that rule, so the hook
// and its test cannot disagree about it.
export function isHiringSignalsEnabled(status: HiringStatus): boolean {
  return status.kind === "enabled";
}

function errorStatus(error: unknown): number | null {
  const status = error instanceof Error ? (error as { status?: unknown }).status : undefined;
  return typeof status === "number" ? status : null;
}

// How a failed status request reads. FEATURE_DISABLED is "switched off" (the
// contract says the status route itself is exempt, but a server that answers it
// anyway means the same). A plain 404 is a server that has no such route --
// an older build -- and the feature is equally not there. Everything else is
// "could not tell", which is not the same thing as "off".
export function classifyStatusError(error: unknown): HiringStatus {
  const failure = classifyFailure(error, "");
  if (failure.kind === "disabled" || failure.kind === "not_found") return { kind: "disabled" };
  if (errorStatus(error) === 404) return { kind: "disabled" };
  return { kind: "unavailable" };
}

// `enabled` must be exactly a boolean; anything else is a reply this page cannot
// read, i.e. "could not tell".
export function parseStatusBody(raw: unknown): HiringStatus {
  if (!isRecord(raw) || typeof raw.enabled !== "boolean") return { kind: "unavailable" };
  return { kind: raw.enabled ? "enabled" : "disabled" };
}

export class HiringSignalsStatusStore {
  private snapshot: HiringStatus = { kind: "checking" };
  private readonly listeners = new Set<() => void>();
  private pending: Promise<void> | null = null;

  constructor(private readonly fetcher: Fetcher) {}

  // Arrow properties, so they can be handed to `useSyncExternalStore` unbound.
  getSnapshot = (): HiringStatus => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private set(next: HiringStatus): void {
    if (next.kind === this.snapshot.kind) return;
    this.snapshot = next;
    for (const listener of this.listeners) listener();
  }

  // Asks the server unless the answer is already known or already being fetched.
  // Safe to call from every consumer on mount (and twice, under StrictMode).
  ensure = (): Promise<void> => {
    if (this.snapshot.kind === "enabled" || this.snapshot.kind === "disabled") {
      return Promise.resolve();
    }
    return this.request();
  };

  // Asks again after a failure (the tab's "Try again"). Joins a request that is
  // already in flight instead of starting a second one.
  refresh = (): Promise<void> => this.request();

  private request(): Promise<void> {
    if (this.pending !== null) return this.pending;
    const pending = this.run();
    // Recorded BEFORE anyone is told anything, so a listener that reacts to the
    // change by asking again joins this request instead of starting another.
    this.pending = pending;
    this.set({ kind: "checking" });
    return pending;
  }

  private async run(): Promise<void> {
    let next: HiringStatus;
    try {
      next = parseStatusBody(await this.fetcher<unknown>(STATUS_PATH));
    } catch (error) {
      next = classifyStatusError(error);
    }
    this.pending = null;
    this.set(next);
  }
}

// The Applications menus (list rows and board cards) carry a static "Find Hiring
// Posts" jump to the workspace panel. It is only offered when the feature is
// known to be on -- otherwise it would scroll to a panel that never renders.
export function crossNavItemsFor(
  items: readonly CrossNavItem[],
  hiringSignalsEnabled: boolean,
): readonly CrossNavItem[] {
  return hiringSignalsEnabled
    ? items
    : items.filter((item) => item.hash !== CROSS_NAV_HASH.hiringPosts);
}
