import { describe, expect, it } from "vitest";
import { CROSS_NAV_ITEMS } from "./applicationsBoard";
import type { Fetcher } from "./hiringSignalsClient";
import {
  HiringSignalsStatusStore,
  STATUS_PATH,
  classifyStatusError,
  crossNavItemsFor,
  isHiringSignalsEnabled,
  parseStatusBody,
} from "./hiringSignalsStatus";
import { NAV, navItemsFor } from "./nav";

// The shared "is Hiring signals switched on?" reader, and the two places its
// answer changes what a person sees: the Applications menus' "Find Hiring Posts"
// entry and the primary nav's "Hiring signals" item. Nothing here touches a
// network: the fetcher is a fake the test controls.

class FakeApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
    public readonly retryable?: boolean,
  ) {
    super(message);
  }
}

interface Pending {
  path: string;
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
}

// A fetcher whose every call waits for the test.
function wire() {
  const calls: Pending[] = [];
  const fetcher: Fetcher = <T>(path: string): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      calls.push({ path, resolve: (v) => resolve(v as T), reject });
    });
  return { calls, fetcher };
}

describe("parseStatusBody", () => {
  it("reads exactly { enabled: boolean }", () => {
    expect(parseStatusBody({ enabled: true })).toEqual({ kind: "enabled" });
    expect(parseStatusBody({ enabled: false })).toEqual({ kind: "disabled" });
  });

  it("is 'could not tell' -- never 'off' -- for anything else", () => {
    for (const bad of [null, undefined, "x", 1, [], {}, { enabled: "true" }, { enabled: 1 }, { on: true }]) {
      expect(parseStatusBody(bad)).toEqual({ kind: "unavailable" });
    }
  });
});

describe("classifyStatusError", () => {
  it("reads FEATURE_DISABLED as switched off", () => {
    expect(classifyStatusError(new FakeApiError(404, "off", "FEATURE_DISABLED"))).toEqual({
      kind: "disabled",
    });
  });

  it("reads a server that has no such route (any 404) as not offering the feature", () => {
    expect(classifyStatusError(new FakeApiError(404, "gone", "NOT_FOUND"))).toEqual({ kind: "disabled" });
    expect(classifyStatusError(new FakeApiError(404, "Request failed (404)"))).toEqual({
      kind: "disabled",
    });
  });

  it("reads everything else as 'could not tell', which is not the same as off", () => {
    for (const error of [
      new FakeApiError(401, "Not signed in."),
      new FakeApiError(500, "boom", "INTERNAL"),
      new FakeApiError(503, "busy", "PROVIDER_UNAVAILABLE", true),
      new TypeError("Failed to fetch"),
      "boom",
      undefined,
    ]) {
      expect(classifyStatusError(error)).toEqual({ kind: "unavailable" });
    }
  });
});

describe("HiringSignalsStatusStore", () => {
  it("starts out 'checking' -- unknown, not off", () => {
    const { fetcher } = wire();
    expect(new HiringSignalsStatusStore(fetcher).getSnapshot()).toEqual({ kind: "checking" });
  });

  it("asks the status route once, however many consumers ask at once", async () => {
    const { fetcher, calls } = wire();
    const store = new HiringSignalsStatusStore(fetcher);
    const asks = [store.ensure(), store.ensure(), store.ensure()];
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe(STATUS_PATH);
    expect(STATUS_PATH).toBe("/hiring-signals/status");
    calls[0].resolve({ enabled: true });
    await Promise.all(asks);
    expect(store.getSnapshot()).toEqual({ kind: "enabled" });
  });

  it("keeps a definite answer for the session: later consumers do not ask again", async () => {
    for (const [reply, kind] of [
      [{ enabled: true }, "enabled"],
      [{ enabled: false }, "disabled"],
    ] as const) {
      const { fetcher, calls } = wire();
      const store = new HiringSignalsStatusStore(fetcher);
      const first = store.ensure();
      calls[0].resolve(reply);
      await first;
      await store.ensure();
      await store.ensure();
      expect(calls).toHaveLength(1);
      expect(store.getSnapshot().kind).toBe(kind);
    }
  });

  it("reads FEATURE_DISABLED (and a missing route) as disabled, and keeps that too", async () => {
    for (const error of [
      new FakeApiError(404, "off", "FEATURE_DISABLED"),
      new FakeApiError(404, "Request failed (404)"),
    ]) {
      const { fetcher, calls } = wire();
      const store = new HiringSignalsStatusStore(fetcher);
      const asked = store.ensure();
      calls[0].reject(error);
      await asked;
      expect(store.getSnapshot()).toEqual({ kind: "disabled" });
      await store.ensure();
      expect(calls).toHaveLength(1);
    }
  });

  it("does NOT remember a failed ask: the next consumer asks again, so a blip does not switch it off until reload", async () => {
    const { fetcher, calls } = wire();
    const store = new HiringSignalsStatusStore(fetcher);
    const first = store.ensure();
    calls[0].reject(new TypeError("Failed to fetch"));
    await first;
    expect(store.getSnapshot()).toEqual({ kind: "unavailable" });

    const second = store.ensure();
    expect(calls).toHaveLength(2);
    expect(store.getSnapshot()).toEqual({ kind: "checking" });
    calls[1].resolve({ enabled: true });
    await second;
    expect(store.getSnapshot()).toEqual({ kind: "enabled" });
  });

  it("refresh asks again after a failure, and joins a request already in flight instead of doubling it", async () => {
    const { fetcher, calls } = wire();
    const store = new HiringSignalsStatusStore(fetcher);
    const first = store.ensure();
    calls[0].reject(new FakeApiError(500, "boom", "INTERNAL"));
    await first;
    const a = store.refresh();
    const b = store.refresh();
    expect(calls).toHaveLength(2);
    calls[1].resolve({ enabled: false });
    await Promise.all([a, b]);
    expect(store.getSnapshot()).toEqual({ kind: "disabled" });
  });

  it("treats an unreadable reply as 'could not tell'", async () => {
    const { fetcher, calls } = wire();
    const store = new HiringSignalsStatusStore(fetcher);
    const asked = store.ensure();
    calls[0].resolve({ enabled: "yes" });
    await asked;
    expect(store.getSnapshot()).toEqual({ kind: "unavailable" });
  });

  it("notifies subscribers of each change, and stops after unsubscribe", async () => {
    const { fetcher, calls } = wire();
    const store = new HiringSignalsStatusStore(fetcher);
    const seen: string[] = [];
    const unsubscribe = store.subscribe(() => seen.push(store.getSnapshot().kind));
    const asked = store.ensure();
    calls[0].resolve({ enabled: true });
    await asked;
    expect(seen).toEqual(["enabled"]);
    unsubscribe();
    // A later re-check changes nothing that anyone is still listening to.
    const again = store.refresh();
    calls[1].resolve({ enabled: false });
    await again;
    expect(store.getSnapshot()).toEqual({ kind: "disabled" });
    expect(seen).toEqual(["enabled"]);
  });

  it("records a request BEFORE telling anyone, so a listener that reacts by asking joins it instead of doubling it", async () => {
    const { fetcher, calls } = wire();
    const store = new HiringSignalsStatusStore(fetcher);
    const first = store.ensure();
    calls[0].reject(new TypeError("Failed to fetch"));
    await first;
    expect(store.getSnapshot()).toEqual({ kind: "unavailable" });

    // A consumer that asks whenever the store says it is checking.
    store.subscribe(() => {
      if (store.getSnapshot().kind === "checking") void store.ensure();
    });
    const again = store.refresh();
    expect(calls).toHaveLength(2);
    calls[1].resolve({ enabled: true });
    await again;
    expect(calls).toHaveLength(2);
    expect(store.getSnapshot()).toEqual({ kind: "enabled" });
  });
});

describe("crossNavItemsFor", () => {
  it("keeps the full menu when the feature is known to be on", () => {
    expect(crossNavItemsFor(CROSS_NAV_ITEMS, true)).toEqual(CROSS_NAV_ITEMS);
  });

  it("drops exactly 'Find Hiring Posts' -- and nothing else -- when it is not", () => {
    const shown = crossNavItemsFor(CROSS_NAV_ITEMS, false);
    expect(shown.map((item) => item.label)).toEqual([
      "Generate Docs",
      "Research Company",
      "Find Contacts",
      "Find Events",
      "Practice Interview",
      "Your Play",
    ]);
    expect(shown).toHaveLength(CROSS_NAV_ITEMS.length - 1);
    expect(shown.some((item) => item.hash === "hiring-posts")).toBe(false);
  });

  it("does not mutate the shared constant", () => {
    const before = [...CROSS_NAV_ITEMS];
    crossNavItemsFor(CROSS_NAV_ITEMS, false);
    expect(CROSS_NAV_ITEMS).toEqual(before);
    expect(CROSS_NAV_ITEMS.some((item) => item.hash === "hiring-posts")).toBe(true);
  });
});

describe("the primary nav", () => {
  it("puts Hiring signals between Discover and Applications", () => {
    const labels = navItemsFor(true).map((item) => item.label);
    expect(labels).toEqual([
      "Today",
      "Discover",
      "Hiring signals",
      "Applications",
      "Practice",
      "Profile",
    ]);
    const item = navItemsFor(true).find((entry) => entry.label === "Hiring signals");
    expect(item).toMatchObject({ to: "/hiring-signals", end: false });
  });

  it("hides that one item -- and only that one -- when the feature is not known to be on", () => {
    expect(navItemsFor(false).map((item) => item.label)).toEqual([
      "Today",
      "Discover",
      "Applications",
      "Practice",
      "Profile",
    ]);
  });

  it("leaves every other entry exactly as it was", () => {
    const others = NAV.filter((item) => item.requiresHiringSignals !== true);
    expect(others.map((item) => item.to)).toEqual([
      "/",
      "/discover",
      "/applications",
      "/practice",
      "/profile",
    ]);
    expect(others.find((item) => item.to === "/")?.end).toBe(true);
  });
});


describe("isHiringSignalsEnabled", () => {
  it("is true only for a definite yes", () => {
    expect(isHiringSignalsEnabled({ kind: "enabled" })).toBe(true);
  });

  it("is false while the answer is unknown, when the server says no, and when asking failed", () => {
    expect(isHiringSignalsEnabled({ kind: "checking" })).toBe(false);
    expect(isHiringSignalsEnabled({ kind: "disabled" })).toBe(false);
    expect(isHiringSignalsEnabled({ kind: "unavailable" })).toBe(false);
  });
});
