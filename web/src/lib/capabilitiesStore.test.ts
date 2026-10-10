import { describe, expect, it, vi } from "vitest";
import {
  CAPABILITIES_TIMEOUT_MS,
  engineKind,
  isTesterProgramRequired,
  type CapabilitiesFetcher,
} from "./capabilities";
import { MAX_CAPABILITY_RETRIES, createCapabilitiesStore } from "./capabilitiesStore";

// The one holder of "what was the server started with", and the rules for asking again. The hook
// that binds it to React is useCapabilities.ts (a source-scan in enrollmentWiring.test.ts pins
// that); the gate's use of it is components/EnrollmentGate.test.tsx.

const BODY = (testerProgramRequired: boolean, telegram = false) => ({
  telegram,
  tester_program_required: testerProgramRequired,
});

// A fetcher whose answers are scripted, one per ask, and which can be held open.
function scripted(...answers: (unknown | Error)[]) {
  const calls: string[] = [];
  const pending: { resolve: () => void }[] = [];
  let next = 0;
  const fetcher: CapabilitiesFetcher = <T,>(path: string) => {
    calls.push(path);
    const answer = answers[Math.min(next, answers.length - 1)];
    next += 1;
    return new Promise<T>((resolve, reject) => {
      const settle = () => (answer instanceof Error ? reject(answer) : resolve(answer as T));
      pending.push({ resolve: settle });
    });
  };
  async function answerNext(): Promise<void> {
    const waiting = pending.shift();
    if (waiting === undefined) throw new Error("no ask is waiting");
    waiting.resolve();
    // let the store's `.then` run
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  }
  return { calls, fetcher, answerNext, outstanding: () => pending.length };
}

const down = new Error("network down");

describe("asking", () => {
  it("starts as 'checking', asks once however many readers ask, and keeps a definite answer", async () => {
    const { calls, fetcher, answerNext } = scripted(BODY(true));
    const store = createCapabilitiesStore(fetcher);
    expect(store.getSnapshot()).toEqual({ kind: "checking" });

    store.ensure();
    store.ensure();
    store.ensure();
    expect(calls).toEqual(["/capabilities"]);
    await answerNext();

    const answer = store.getSnapshot();
    expect(answer.kind).toBe("ready");
    expect(isTesterProgramRequired(answer)).toBe(true);
    store.ensure();
    expect(calls).toHaveLength(1);
  });

  it("is 'unavailable' when the ask fails, or the answer is not the shape expected", async () => {
    const failed = scripted(down);
    const a = createCapabilitiesStore(failed.fetcher);
    a.ensure();
    await failed.answerNext();
    expect(a.getSnapshot()).toEqual({ kind: "unavailable" });

    const odd = scripted({ nope: true });
    const b = createCapabilitiesStore(odd.fetcher);
    b.ensure();
    await odd.answerNext();
    expect(b.getSnapshot()).toEqual({ kind: "unavailable" });
  });

  it("has a deadline for the ask: the shell draws nothing until it is answered", () => {
    expect(CAPABILITIES_TIMEOUT_MS).toBeGreaterThan(0);
    expect(CAPABILITIES_TIMEOUT_MS).toBeLessThanOrEqual(15_000);
  });
});

describe("after a failed ask", () => {
  // The bug this exists for: the gate, which sits above every page and never remounts, kept the
  // first failure for the whole visit while the Profile line, which asked later, learned the
  // server REQUIRES the programme. Every reader now looks at one answer.
  it("a later ask that succeeds is what EVERY reader sees, so the gate and the Profile line cannot disagree", async () => {
    const { fetcher, answerNext } = scripted(down, BODY(true));
    const store = createCapabilitiesStore(fetcher);
    const gate = vi.fn();
    const profileLine = vi.fn();
    store.subscribe(gate);
    store.subscribe(profileLine);

    store.ensure(); // the gate's first ask
    await answerNext();
    expect(store.getSnapshot()).toEqual({ kind: "unavailable" });
    expect(isTesterProgramRequired(store.getSnapshot())).toBe(false);

    store.ensure(); // a page that mounts later asks again (within the cap)
    await answerNext();

    expect(isTesterProgramRequired(store.getSnapshot())).toBe(true);
    // both readers were told, on both changes
    expect(gate).toHaveBeenCalledTimes(2);
    expect(profileLine).toHaveBeenCalledTimes(2);
  });

  it("retries on request, and says whether it asked", async () => {
    const { calls, fetcher, answerNext } = scripted(down, BODY(false));
    const store = createCapabilitiesStore(fetcher);
    expect(store.retry()).toBe(false); // nothing has failed yet
    store.ensure();
    await answerNext();

    expect(store.retry()).toBe(true);
    expect(calls).toHaveLength(2);
    await answerNext();
    expect(store.getSnapshot().kind).toBe("ready");
    expect(store.retry()).toBe(false); // known: nothing to retry
  });

  it("does not stack a retry on an ask that is still out", async () => {
    const { calls, fetcher, answerNext } = scripted(down, down);
    const store = createCapabilitiesStore(fetcher);
    store.ensure();
    await answerNext();

    expect(store.retry()).toBe(true);
    expect(store.retry()).toBe(false);
    expect(store.retry()).toBe(false);
    expect(calls).toHaveLength(2);
    await answerNext();
  });

  it(`retries at most ${MAX_CAPABILITY_RETRIES} times in all: a server that is down is not hammered by every navigation`, async () => {
    const { calls, fetcher, answerNext } = scripted(down);
    const store = createCapabilitiesStore(fetcher);
    store.ensure();
    await answerNext();

    let asked = 0;
    for (let i = 0; i < 10; i++) {
      if (store.retry()) {
        asked += 1;
        await answerNext();
      }
    }
    expect(asked).toBe(MAX_CAPABILITY_RETRIES);
    expect(calls).toHaveLength(1 + MAX_CAPABILITY_RETRIES);
    // `ensure`, which pages call when they mount, is held to the same cap
    store.ensure();
    expect(calls).toHaveLength(1 + MAX_CAPABILITY_RETRIES);
    expect(store.getSnapshot()).toEqual({ kind: "unavailable" });
    expect(MAX_CAPABILITY_RETRIES).toBe(3);
  });

  it("tells nobody again when it fails the same way again (they would only draw the same thing)", async () => {
    const { fetcher, answerNext } = scripted(down);
    const store = createCapabilitiesStore(fetcher);
    const listener = vi.fn();
    store.subscribe(listener);
    store.ensure();
    await answerNext();
    expect(listener).toHaveBeenCalledTimes(1);
    const unavailable = store.getSnapshot();

    store.retry();
    await answerNext();

    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).toBe(unavailable);
  });
});

describe("refresh: the server just contradicted what this app believed", () => {
  it("asks again even though an answer is known, and replaces it with the new one", async () => {
    const { calls, fetcher, answerNext } = scripted(BODY(false), BODY(true));
    const store = createCapabilitiesStore(fetcher);
    store.ensure();
    await answerNext();
    expect(isTesterProgramRequired(store.getSnapshot())).toBe(false);

    store.refresh();
    expect(calls).toHaveLength(2);
    // while it is out, what is known stays
    expect(isTesterProgramRequired(store.getSnapshot())).toBe(false);
    await answerNext();

    expect(isTesterProgramRequired(store.getSnapshot())).toBe(true);
  });

  it("leaves a known answer as it was when the refresh fails", async () => {
    const { fetcher, answerNext } = scripted(BODY(true), down);
    const store = createCapabilitiesStore(fetcher);
    store.ensure();
    await answerNext();
    const known = store.getSnapshot();

    store.refresh();
    await answerNext();

    expect(store.getSnapshot()).toBe(known);
  });

  it("turns 'unavailable' into the real answer without spending a retry", async () => {
    const { fetcher, answerNext } = scripted(down, BODY(true));
    const store = createCapabilitiesStore(fetcher);
    store.ensure();
    await answerNext();
    expect(store.getSnapshot().kind).toBe("unavailable");

    store.refresh();
    await answerNext();

    expect(isTesterProgramRequired(store.getSnapshot())).toBe(true);
  });

  it("collapses a burst of refusals into one request", async () => {
    const { calls, fetcher, answerNext } = scripted(BODY(true));
    const store = createCapabilitiesStore(fetcher);
    store.ensure();
    await answerNext();
    calls.length = 0;

    store.refresh();
    store.refresh();
    store.refresh();

    expect(calls).toHaveLength(1);
    await answerNext();
  });

  it("does not tell anyone when the new answer says the same thing", async () => {
    const { fetcher, answerNext } = scripted(BODY(true, true), BODY(true, true));
    const store = createCapabilitiesStore(fetcher);
    const listener = vi.fn();
    store.ensure();
    await answerNext();
    store.subscribe(listener);
    const known = store.getSnapshot();

    store.refresh();
    await answerNext();

    expect(listener).not.toHaveBeenCalled();
    expect(store.getSnapshot()).toBe(known);
  });
});

describe("the resume engine the server runs", () => {
  it("is part of the answer: a refresh that finds a different engine is told to every reader", async () => {
    const withEngine = (engine: string) => ({ ...BODY(false), engine });
    const { fetcher, answerNext } = scripted(withEngine("remote"), withEngine("generic"));
    const store = createCapabilitiesStore(fetcher);
    const listener = vi.fn();
    store.ensure();
    await answerNext();
    store.subscribe(listener);
    expect(engineKind(store.getSnapshot())).toBe("remote");

    store.refresh();
    await answerNext();

    expect(listener).toHaveBeenCalledTimes(1);
    expect(engineKind(store.getSnapshot())).toBe("generic");
  });
});

describe("subscribing", () => {
  it("stops telling a reader once it has unsubscribed", async () => {
    const { fetcher, answerNext } = scripted(BODY(false));
    const store = createCapabilitiesStore(fetcher);
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    unsubscribe();
    store.ensure();
    await answerNext();
    expect(listener).not.toHaveBeenCalled();
  });

  it("hands out the same snapshot object until something changes (what useSyncExternalStore needs)", () => {
    const { fetcher } = scripted(BODY(false));
    const store = createCapabilitiesStore(fetcher);
    expect(store.getSnapshot()).toBe(store.getSnapshot());
  });
});
