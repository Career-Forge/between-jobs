import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { completedKey, dismissedKey, searchedKey, type StorageLike } from "./firstRun";
import { useFirstRun } from "./useFirstRun";

// The first-run hook's own decisions, which only exist where React and the browser meet:
// whether it asks the API at all, under whose key it reads and writes, what it writes when
// the card is dismissed or the account is seen finished. The pure rules are in
// firstRun.test.ts; this pins that the hook uses them with the right person's key.
//
// Same technique as components/wiring.test.tsx: the package has no DOM, and a static render
// does not run effects, so `useEffect` is replaced by a recorder; a test renders a probe
// component (real hooks, real state), then RUNS what was registered. A state update after
// a static render is a no-op, so what is checked is what reached storage and the API.

const recorded = vi.hoisted(() => ({
  effects: [] as { fn: () => void | (() => void); deps: readonly unknown[] | undefined }[],
  apiFetch: vi.fn(),
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

vi.mock("./api", () => ({ apiFetch: recorded.apiFetch }));

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

function Probe({ userId }: { userId: string }) {
  hook = useFirstRun(userId);
  return null;
}

function mount(userId = "user-1"): ReturnType<typeof useFirstRun> {
  renderToStaticMarkup(<Probe userId={userId} />);
  if (hook === null) throw new Error("the hook did not run");
  return hook;
}

function runEffects(): Array<void | (() => void)> {
  return recorded.effects.map((effect) => effect.fn());
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const FINISHED: Record<string, unknown> = {
  "/profile/current": { id: "pv-1" },
  "/credentials": [{ service: "llm", is_validated: true }],
  "/saved-searches": [{ id: "s-1" }],
  "/applications": [{ id: "a-1", resume_exists: true, source_channel: "discover" }],
};

const BRAND_NEW: Record<string, unknown> = {
  "/profile/current": new Error("x"),
  "/credentials": [],
  "/saved-searches": [],
  "/applications": [],
};

function answerWith(replies: Record<string, unknown>) {
  recorded.apiFetch.mockImplementation(async (path: string) => {
    const reply = replies[path];
    if (reply instanceof Error) throw reply;
    return reply;
  });
}

beforeEach(() => {
  recorded.effects.length = 0;
  recorded.apiFetch.mockReset();
  answerWith(BRAND_NEW);
  storage = memoryStorage();
  hook = null;
  vi.stubGlobal("window", { localStorage: storage });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useFirstRun: whether it asks the API", () => {
  it("asks for the four plain lists, once each, for an account with nothing stored", () => {
    mount();
    runEffects();
    expect(recorded.apiFetch.mock.calls.map(([path]) => path).sort()).toEqual(
      ["/applications", "/credentials", "/profile/current", "/saved-searches"].sort(),
    );
  });

  it("asks for nothing for a card this person dismissed", () => {
    storage.setItem(dismissedKey("user-1"), "1");
    mount("user-1");
    runEffects();
    expect(recorded.apiFetch).not.toHaveBeenCalled();
  });

  it("asks for nothing for an account already seen finished", () => {
    storage.setItem(completedKey("user-1"), "1");
    mount("user-1");
    runEffects();
    expect(recorded.apiFetch).not.toHaveBeenCalled();
  });

  it("is not stopped by another person's dismissal or completion on the same browser", () => {
    storage.setItem(dismissedKey("user-2"), "1");
    storage.setItem(completedKey("user-2"), "1");
    mount("user-1");
    runEffects();
    expect(recorded.apiFetch).toHaveBeenCalledTimes(4);
  });

  it("re-runs when the person changes, or when asking becomes unnecessary", () => {
    mount("user-1");
    expect(recorded.effects[0].deps).toEqual([true, "user-1"]);
    recorded.effects.length = 0;
    storage.setItem(dismissedKey("user-2"), "1");
    mount("user-2");
    expect(recorded.effects[0].deps).toEqual([false, "user-2"]);
  });

  it("shows nothing while it is still loading", () => {
    expect(mount().view.visible).toBe(false);
  });
});

describe("useFirstRun: dismissing", () => {
  it("writes the dismissed flag under the person's own key, and no other", () => {
    mount("user-1").dismiss();
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
    expect(() => mount().dismiss()).not.toThrow();
  });
});

describe("useFirstRun: when the answers arrive", () => {
  it("remembers a finished account, under the person's key, once all five steps are done", async () => {
    answerWith(FINISHED);
    mount("user-1");
    runEffects();
    await flush();
    expect(storage.data.get(completedKey("user-1"))).toBe("1");
    expect(storage.data.has(completedKey("user-2"))).toBe(false);
  });

  it("does not remember an account that still has something to do", async () => {
    answerWith(BRAND_NEW);
    mount();
    runEffects();
    await flush();
    expect(storage.data.has(completedKey("user-1"))).toBe(false);
  });

  it("does not remember an account whose requests failed", async () => {
    answerWith({
      ...FINISHED,
      "/applications": new Error("500"),
      "/saved-searches": new Error("500"),
    });
    mount();
    runEffects();
    await flush();
    expect(storage.data.has(completedKey("user-1"))).toBe(false);
  });

  it("reads the first-search mark under the person's own key (the Discover page writes it there)", async () => {
    // Everything else is done; only the browser's mark can say a search ran.
    answerWith({
      ...FINISHED,
      "/saved-searches": [],
      "/applications": [{ id: "a-1", resume_exists: true, source_channel: "web" }],
    });
    storage.setItem(searchedKey("user-2"), "1");
    mount("user-1");
    runEffects();
    await flush();
    expect(storage.data.has(completedKey("user-1"))).toBe(false);

    recorded.effects.length = 0;
    storage.setItem(searchedKey("user-1"), "1");
    mount("user-1");
    runEffects();
    await flush();
    expect(storage.data.get(completedKey("user-1"))).toBe("1");
  });

  it("drops an answer that arrives after the card was torn down or dismissed (the cleanup cancels it)", async () => {
    answerWith(FINISHED);
    mount();
    const [cleanup] = runEffects();
    expect(cleanup).toBeTypeOf("function");
    (cleanup as () => void)();
    await flush();
    expect(storage.data.has(completedKey("user-1"))).toBe(false);
  });
});
