import type { DiscoverRequest } from "./discoverFacts";
import type { DiscoverResponse } from "./discoverTypes";

// The last finished Discover answer, held in memory so that a person who opens a job from the results and
// comes back finds the list where they left it instead of an empty page and a search to run again. A search
// is a full discovery: it calls every search provider the person has connected, checks the links, and makes
// one scoring call on their own model key, so making them repeat it to get back to the same rows would spend
// their keys for nothing.
//
// MEMORY ONLY. This is a plain object behind a closure: nothing here is written to browser storage, history
// state or a module-level singleton. A reload, a closed tab or a phone tab the browser discarded starts empty,
// and so does a different person (the store is created per signed-in person, see lib/useDiscoverHold.tsx).
//
// What is held is the last answer that came back with rows, not the state of the page. A search that is
// running, that failed or that asked for setup leaves it alone, so a failed "Search again" does not take the
// earlier list away; an answer with no rows and "Clear filters" empty it (pages/Discover.tsx decides when).

// How long a held answer may be shown again. The scores, the link checks and the postings themselves age, so
// an old list is worse than an empty page that asks for a new search. A limit chosen to be about one sitting.
export const HOLD_MAX_AGE_MS = 2 * 60 * 60 * 1000;

export interface DiscoverHoldEntry {
  /** The answer exactly as it came back. */
  result: DiscoverResponse;
  /** The filters that produced it: the form is filled from them, so "Search again" repeats that search. */
  request: DiscoverRequest;
  /** Postings the person tracked from the page, by apply_url, each with the application it became. */
  tracked: Readonly<Record<string, string>>;
  /** When the answer arrived, in milliseconds since the epoch, on the clock that wrote it. */
  at: number;
}

export interface DiscoverHold {
  /**
   * The held answer, or null when there is none, when it is older than HOLD_MAX_AGE_MS, or when its stamp is
   * not a readable time (not a finite number, or ahead of the clock, as after the clock was set back). The
   * stored object itself is returned, not a copy: the results view starts its own choices afresh whenever
   * the answer it is given is a different object, so a copy would be taken for a new search. Reading
   * changes nothing, so reading twice (a development build runs a state initialiser twice) is the same as
   * reading once.
   */
  read(): DiscoverHoldEntry | null;
  /** Holds this answer in place of any other. */
  write(entry: DiscoverHoldEntry): void;
  /** Holds nothing. */
  clear(): void;
}

// `now` is read each time it is needed, not bound when the store is made, so a clock replaced after that
// (a test's fake timers) is the one that counts.
export function createDiscoverHold(now: () => number = () => Date.now()): DiscoverHold {
  let held: DiscoverHoldEntry | null = null;
  return {
    read() {
      if (held === null) return null;
      const stamp: unknown = held.at;
      if (typeof stamp !== "number" || !Number.isFinite(stamp)) return null;
      const age = now() - stamp;
      if (!Number.isFinite(age) || age < 0 || age > HOLD_MAX_AGE_MS) return null;
      return held;
    },
    write(entry) {
      held = entry;
    },
    clear() {
      held = null;
    },
  };
}
