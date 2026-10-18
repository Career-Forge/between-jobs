import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  STILL_LOADING,
  completedKey,
  dismissedKey,
  searchedKey,
  type FirstRunFacts,
  type StorageLike,
} from "./firstRun";
import { useFirstRun } from "./useFirstRun";

// The first-run hook's own decisions, which only exist where React and the browser meet: under
// whose key it reads and writes the browser's storage, what it writes when the checklist is hidden
// or the account is seen finished, and when it does nothing at all. The pure rules are in
// firstRun.test.ts; this pins that the hook uses them with the right person's key. It asks the API
// for nothing: Today reads the account once and hands the answers in (the page's reads are
// covered in pages/Today.test.tsx).
//
// Same technique as components/wiring.test.tsx: the package has no DOM here, and a static render
// does not run effects, so `useEffect` is replaced by a recorder; a test renders a probe component
// (real hooks, real state), then RUNS what was registered. A state update after a static render is
// a no-op, so what is checked is what reached storage.

const recorded = vi.hoisted(() => ({
  effects: [] as { fn: () => void | (() => void); deps: readonly unknown[] | undefined }[],
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useEffect: (fn: () => void | (() => void), deps?: readonly unknown[]) => {
      recorded.effects.push({ fn, deps });
    },
  };
});

function memoryStorage(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
  };
}

let storage = memoryStorage();
let hook: ReturnType<typeof useFirstRun> | null = null;

function Probe({ userId, facts }: { userId: string; facts: FirstRunFacts }) {
  hook = useFirstRun(userId, facts);
  return null;
}

function mount(facts: FirstRunFacts, userId = "user-1"): ReturnType<typeof useFirstRun> {
  renderToStaticMarkup(<Probe userId={userId} facts={facts} />);
  if (hook === null) throw new Error("the hook did not run");
  return hook;
}

function runEffects(): Array<void | (() => void)> {
  return recorded.effects.map((effect) => effect.fn());
}

const ok = <T,>(value: T) => ({ kind: "ok", value }) as const;
const failed = { kind: "failed" } as const;

const FINISHED: FirstRunFacts = {
  profile: ok(true),
  modelKey: ok(true),
  savedSearches: ok(1),
  applications: ok({ count: 1, fromDiscover: 1, withResume: 1, resumeTargetId: "a-1" }),
};

const BRAND_NEW: FirstRunFacts = {
  profile: ok(false),
  modelKey: ok(false),
  savedSearches: ok(0),
  applications: ok({ count: 0, fromDiscover: 0, withResume: 0, resumeTargetId: null }),
};

beforeEach(() => {
  recorded.effects.length = 0;
  storage = memoryStorage();
  hook = null;
  vi.stubGlobal("window", { localStorage: storage });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useFirstRun: before the reads come back", () => {
  it("shows nothing for an account with nothing stored, and writes nothing", () => {
    const { view } = mount(STILL_LOADING);
    expect(view.presentation).toBe("hidden");
    runEffects();
    expect([...storage.data.keys()]).toEqual([]);
  });

  it("shows the one-line ready state for an account this browser saw finished, for that person only", () => {
    storage.setItem(completedKey("user-1"), "1");
    expect(mount(STILL_LOADING, "user-1").view).toMatchObject({ presentation: "collapsed", finished: true });
    recorded.effects.length = 0;
    expect(mount(STILL_LOADING, "user-2").view.presentation).toBe("hidden");
  });

  it("does nothing in its effect while any read is still loading", () => {
    mount({ ...FINISHED, applications: { kind: "loading" } });
    runEffects();
    expect(storage.data.has(completedKey("user-1"))).toBe(false);
  });

  it("re-runs when the reads arrive or the person changes", () => {
    mount(STILL_LOADING, "user-1");
    expect(recorded.effects[0].deps).toEqual([false, STILL_LOADING, "user-1"]);
    recorded.effects.length = 0;
    mount(FINISHED, "user-2");
    expect(recorded.effects[0].deps).toEqual([true, FINISHED, "user-2"]);
  });
});

describe("useFirstRun: hiding", () => {
  it("writes the hidden flag under the person's own key, and no other", () => {
    mount(BRAND_NEW, "user-1").dismiss();
    expect([...storage.data.keys()]).toEqual([dismissedKey("user-1")]);
    expect(storage.data.get(dismissedKey("user-1"))).toBe("1");
  });

  it("does not throw when storage cannot take it", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => null,
        setItem: () => {
          throw new Error("QuotaExceededError");
        },
      },
    });
    expect(() => mount(BRAND_NEW).dismiss()).not.toThrow();
  });
});

describe("useFirstRun: when the reads arrive", () => {
  it("remembers a finished account, under the person's key, once all five steps are done", () => {
    mount(FINISHED, "user-1");
    runEffects();
    expect(storage.data.get(completedKey("user-1"))).toBe("1");
    expect(storage.data.has(completedKey("user-2"))).toBe(false);
  });

  it("does not remember an account that still has something to do", () => {
    mount(BRAND_NEW);
    runEffects();
    expect(storage.data.has(completedKey("user-1"))).toBe(false);
  });

  it("does not remember an account whose reads failed", () => {
    mount({ ...FINISHED, applications: failed, savedSearches: failed });
    runEffects();
    expect(storage.data.has(completedKey("user-1"))).toBe(false);
  });

  it("reads the first-search mark under the person's own key (the Discover page writes it there)", () => {
    // Everything else is done; only the browser's mark can say a search ran.
    const facts: FirstRunFacts = {
      ...FINISHED,
      savedSearches: ok(0),
      applications: ok({ count: 1, fromDiscover: 0, withResume: 1, resumeTargetId: "a-1" }),
    };
    storage.setItem(searchedKey("user-2"), "1");
    mount(facts, "user-1");
    runEffects();
    expect(storage.data.has(completedKey("user-1"))).toBe(false);

    recorded.effects.length = 0;
    storage.setItem(searchedKey("user-1"), "1");
    mount(facts, "user-1");
    runEffects();
    expect(storage.data.get(completedKey("user-1"))).toBe("1");
  });
});
