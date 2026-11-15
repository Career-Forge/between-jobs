import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_REQUEST } from "./discoverFacts";
import { HOLD_MAX_AGE_MS, createDiscoverHold, type DiscoverHoldEntry } from "./discoverHold";
import { job, response } from "../testing/discoverFixtures";

// The in-memory holder of the last Discover answer, with a clock the test owns. What the page does with it
// (when it writes, when it clears) is pages/Discover.test.tsx; this is only the store's own rules.

const T0 = Date.UTC(2026, 10, 14, 12, 0, 0);

function entry(overrides: Partial<DiscoverHoldEntry> = {}): DiscoverHoldEntry {
  return { result: response({ scored: [job()] }), request: { ...EMPTY_REQUEST, q: "data engineer" }, tracked: {}, at: T0, ...overrides };
}

function clock(start: number) {
  const time = { now: start };
  return { time, read: () => time.now };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("the limit", () => {
  it("is two hours", () => {
    expect(HOLD_MAX_AGE_MS).toBe(2 * 60 * 60 * 1000);
  });
});

describe("a store with nothing in it", () => {
  it("reads null, and clearing it changes nothing", () => {
    const hold = createDiscoverHold(() => T0);
    expect(hold.read()).toBeNull();
    hold.clear();
    expect(hold.read()).toBeNull();
  });
});

describe("writing and reading", () => {
  it("hands back the very object that was written, not a copy of it", () => {
    const hold = createDiscoverHold(() => T0);
    const written = entry();
    hold.write(written);
    expect(hold.read()).toBe(written);
    expect(hold.read()?.result).toBe(written.result);
    expect(hold.read()).toBe(hold.read());
  });

  it("reads the same on every read: reading takes nothing out", () => {
    const hold = createDiscoverHold(() => T0 + 1000);
    hold.write(entry());
    const first = hold.read();
    const second = hold.read();
    expect(first).not.toBeNull();
    expect(second).toBe(first);
  });

  it("holds one answer: a write replaces the one before it", () => {
    const hold = createDiscoverHold(() => T0);
    const first = entry();
    const second = entry({ result: response({ scored: [job({ title: "Analyst", apply_url: "https://jobs.example/acme/2" })] }), request: { ...EMPTY_REQUEST, q: "analyst" } });
    hold.write(first);
    hold.write(second);
    expect(hold.read()).toBe(second);
    expect(hold.read()?.request.q).toBe("analyst");
  });

  it("keeps the tracked postings and the filters with the answer", () => {
    const hold = createDiscoverHold(() => T0);
    const tracked = { "https://jobs.example/acme/1": "app-1" };
    hold.write(entry({ tracked }));
    expect(hold.read()?.tracked).toBe(tracked);
    expect(hold.read()?.request).toEqual({ ...EMPTY_REQUEST, q: "data engineer" });
  });

  it("is emptied by clear, and a later write holds again", () => {
    const hold = createDiscoverHold(() => T0);
    hold.write(entry());
    hold.clear();
    expect(hold.read()).toBeNull();
    const again = entry({ at: T0 });
    hold.write(again);
    expect(hold.read()).toBe(again);
  });

  it("is a store of its own: two stores share nothing", () => {
    const one = createDiscoverHold(() => T0);
    const other = createDiscoverHold(() => T0);
    one.write(entry());
    expect(other.read()).toBeNull();
    other.write(entry({ at: T0 }));
    one.clear();
    expect(other.read()).not.toBeNull();
  });
});

describe("how old an answer may be", () => {
  it("holds an answer up to the limit and not a millisecond past it", () => {
    const { time, read } = clock(T0);
    const hold = createDiscoverHold(read);
    const written = entry();
    hold.write(written);

    time.now = T0 + HOLD_MAX_AGE_MS - 1;
    expect(hold.read()).toBe(written);
    time.now = T0 + HOLD_MAX_AGE_MS;
    expect(hold.read()).toBe(written);
    time.now = T0 + HOLD_MAX_AGE_MS + 1;
    expect(hold.read()).toBeNull();
  });

  it("counts from when the answer arrived, not from when it was last written to the store", () => {
    // A write that only updates what was tracked keeps the stamp it was given, so it does not extend the life.
    const { time, read } = clock(T0);
    const hold = createDiscoverHold(read);
    hold.write(entry({ at: T0 }));
    time.now = T0 + HOLD_MAX_AGE_MS - 10;
    hold.write({ ...hold.read()!, tracked: { "https://jobs.example/acme/1": "app-1" } });
    time.now = T0 + HOLD_MAX_AGE_MS + 10;
    expect(hold.read()).toBeNull();
  });

  it("reads null for a stamp that is not a readable number, whatever the clock says", () => {
    const hold = createDiscoverHold(() => T0);
    for (const at of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, "2026-11-14T12:00:00Z", null, undefined, {}] as unknown[]) {
      hold.write(entry({ at: at as number }));
      expect(hold.read(), String(at)).toBeNull();
    }
  });

  it("reads null for a stamp ahead of the clock, as after the clock was set back", () => {
    const hold = createDiscoverHold(() => T0);
    hold.write(entry({ at: T0 + 1 }));
    expect(hold.read()).toBeNull();
    hold.write(entry({ at: T0 }));
    expect(hold.read()).not.toBeNull();
  });

  it("reads the clock when it is asked, not when the store was made", () => {
    const hold = createDiscoverHold();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(T0);
    hold.write(entry({ at: T0 }));
    expect(hold.read()).not.toBeNull();
    vi.setSystemTime(T0 + HOLD_MAX_AGE_MS + 1);
    expect(hold.read()).toBeNull();
  });
});
