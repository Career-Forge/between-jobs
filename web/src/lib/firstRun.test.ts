import { describe, expect, it } from "vitest";
import {
  STILL_LOADING,
  browserStorage,
  completedKey,
  deriveFirstRun,
  dismissedKey,
  initialFirstRunState,
  loadFirstRunFacts,
  markSearched,
  onFirstRunDismissed,
  onFirstRunFactsLoaded,
  readCompleted,
  readDismissed,
  readSearched,
  searchedKey,
  shouldLoadFirstRun,
  viewOfFirstRun,
  writeCompleted,
  writeDismissed,
  type FirstRunFacts,
  type FirstRunFlags,
  type FirstRunState,
  type StorageLike,
} from "./firstRun";

// The first-run checklist's decisions, none of which needs a browser: which of the five steps
// are done from what the API said, what happens when asking failed, and the dismissed flag
// that lives in storage which may be missing or may throw.

const ok = <T>(value: T) => ({ kind: "ok", value }) as const;
const failed = { kind: "failed" } as const;
const loading = { kind: "loading" } as const;

const BRAND_NEW: FirstRunFacts = {
  profile: ok(false),
  modelKey: ok(false),
  savedSearches: ok(0),
  applications: ok({ count: 0, fromDiscover: 0, withResume: 0, resumeTargetId: null }),
};

const EVERYTHING_DONE: FirstRunFacts = {
  profile: ok(true),
  modelKey: ok(true),
  savedSearches: ok(1),
  applications: ok({ count: 2, fromDiscover: 0, withResume: 1, resumeTargetId: "app-1" }),
};

const FRESH: FirstRunFlags = { dismissed: false, searched: false };

const ALL_FAILED: FirstRunFacts = {
  profile: failed,
  modelKey: failed,
  savedSearches: failed,
  applications: failed,
};

function statuses(facts: FirstRunFacts, flags: FirstRunFlags = FRESH): string[] {
  return deriveFirstRun(facts, flags).steps.map((s) => `${s.id}:${s.status}`);
}

describe("deriveFirstRun", () => {
  it("shows a brand-new account five unticked steps, the profile first", () => {
    const view = deriveFirstRun(BRAND_NEW, FRESH);
    expect(view.visible).toBe(true);
    expect(view.doneCount).toBe(0);
    expect(view.steps.map((s) => s.id)).toEqual([
      "profile",
      "model_key",
      "first_search",
      "track_job",
      "generate_resume",
    ]);
    expect(view.steps.every((s) => s.status === "todo")).toBe(true);
    expect(view.nextStep?.id).toBe("profile");
  });

  it("sends each step to the real page where it is done", () => {
    const view = deriveFirstRun(BRAND_NEW, FRESH);
    expect(Object.fromEntries(view.steps.map((s) => [s.id, s.to]))).toEqual({
      profile: "/profile",
      model_key: "/profile/integrations",
      first_search: "/discover",
      track_job: "/applications",
      generate_resume: "/applications",
    });
    for (const step of view.steps) {
      expect(step.label).not.toBe("");
      expect(step.hint).not.toBe("");
    }
  });

  it("shows nothing once all five steps are done", () => {
    const view = deriveFirstRun(EVERYTHING_DONE, FRESH);
    expect(view.doneCount).toBe(5);
    expect(view.nextStep).toBeNull();
    expect(view.visible).toBe(false);
  });

  it("ticks what the data shows and points at the first step that is not done", () => {
    const facts: FirstRunFacts = {
      profile: ok(true),
      modelKey: ok(true),
      savedSearches: ok(0),
      applications: ok({ count: 0, fromDiscover: 0, withResume: 0, resumeTargetId: null }),
    };
    expect(statuses(facts, { dismissed: false, searched: true })).toEqual([
      "profile:done",
      "model_key:done",
      "first_search:done",
      "track_job:todo",
      "generate_resume:todo",
    ]);
    const view = deriveFirstRun(facts, { dismissed: false, searched: true });
    expect(view.doneCount).toBe(3);
    expect(view.nextStep?.id).toBe("track_job");
    expect(view.visible).toBe(true);
  });

  it("does not tick a step off a click: an account with nothing set up has nothing ticked", () => {
    expect(deriveFirstRun(BRAND_NEW, FRESH).doneCount).toBe(0);
  });

  describe("the model key step", () => {
    it("is done only when a validated model key exists, and todo when none does", () => {
      expect(statuses({ ...BRAND_NEW, modelKey: ok(true) })[1]).toBe("model_key:done");
      expect(statuses({ ...BRAND_NEW, modelKey: ok(false) })[1]).toBe("model_key:todo");
    });
  });

  describe("the first search step", () => {
    // A search leaves no row: /discover persists nothing and is rate limited, so it cannot be
    // probed. It counts as done when this browser remembers a first successful search, or when
    // the account holds what only a search leaves: an application tracked FROM Discover, or a
    // saved search. An application pasted, added by URL or sent over Telegram says nothing.
    it("is done when the browser remembers a search", () => {
      expect(statuses(BRAND_NEW, { dismissed: false, searched: true })[2]).toBe("first_search:done");
    });

    it("is done when a saved search exists", () => {
      expect(statuses({ ...BRAND_NEW, savedSearches: ok(1) })[2]).toBe("first_search:done");
    });

    it("is done when an application was tracked from a Discover search", () => {
      const facts = { ...BRAND_NEW, applications: ok({ count: 1, fromDiscover: 1, withResume: 0, resumeTargetId: "a" }) };
      expect(statuses(facts)[2]).toBe("first_search:done");
    });

    it("is NOT done by an application that came from somewhere else (pasted, a URL, Telegram)", () => {
      const facts = {
        ...BRAND_NEW,
        applications: ok({ count: 3, fromDiscover: 0, withResume: 0, resumeTargetId: "a" }),
      };
      const view = deriveFirstRun(facts, FRESH);
      const byId = Object.fromEntries(view.steps.map((s) => [s.id, s]));
      expect(byId.first_search.status).toBe("todo");
      // The step that those applications DO complete is the next one.
      expect(byId.track_job.status).toBe("done");
    });

    it("points a person who only pasted a posting at Discover, once the earlier steps are done", () => {
      const facts: FirstRunFacts = {
        profile: ok(true),
        modelKey: ok(true),
        savedSearches: ok(0),
        applications: ok({ count: 1, fromDiscover: 0, withResume: 0, resumeTargetId: "a" }),
      };
      const view = deriveFirstRun(facts, FRESH);
      expect(view.nextStep?.id).toBe("first_search");
      expect(view.nextStep?.to).toBe("/discover");
    });

    it("is todo only when everything that could show a search was read, and none did", () => {
      expect(statuses(BRAND_NEW, { dismissed: false, searched: false })[2]).toBe("first_search:todo");
    });

    it("is unknown, not todo, when something that could show a search could not be read", () => {
      expect(statuses({ ...BRAND_NEW, savedSearches: failed })[2]).toBe("first_search:unknown");
      expect(statuses({ ...BRAND_NEW, applications: failed })[2]).toBe("first_search:unknown");
      expect(statuses(BRAND_NEW, { dismissed: false, searched: null })[2]).toBe("first_search:unknown");
    });

    it("stays done when other sources failed but one still shows it", () => {
      expect(statuses(ALL_FAILED, { dismissed: false, searched: true })[2]).toBe("first_search:done");
      expect(statuses({ ...ALL_FAILED, savedSearches: ok(2) })[2]).toBe("first_search:done");
    });
  });

  describe("the track and resume steps", () => {
    it("tracks from any application, and wants a resume on one of them", () => {
      const facts: FirstRunFacts = {
        ...BRAND_NEW,
        applications: ok({ count: 2, fromDiscover: 0, withResume: 0, resumeTargetId: "app-7" }),
      };
      const view = deriveFirstRun(facts, FRESH);
      const byId = Object.fromEntries(view.steps.map((s) => [s.id, s]));
      expect(byId.track_job.status).toBe("done");
      expect(byId.generate_resume.status).toBe("todo");
      // Straight to the Generate panel of an application that has no resume yet.
      expect(byId.generate_resume.to).toBe("/applications/app-7#generate");
    });

    it("is done for the resume when one application has a resume", () => {
      const facts: FirstRunFacts = {
        ...BRAND_NEW,
        applications: ok({ count: 1, fromDiscover: 0, withResume: 1, resumeTargetId: "app-1" }),
      };
      expect(statuses(facts)[4]).toBe("generate_resume:done");
    });

    it("falls back to the applications list when there is no application to open", () => {
      const view = deriveFirstRun(BRAND_NEW, FRESH);
      expect(view.steps[4].to).toBe("/applications");
    });

    it("encodes an id before it goes into a path", () => {
      const facts: FirstRunFacts = {
        ...BRAND_NEW,
        applications: ok({ count: 1, fromDiscover: 0, withResume: 0, resumeTargetId: "a/b?c#d" }),
      };
      expect(deriveFirstRun(facts, FRESH).steps[4].to).toBe("/applications/a%2Fb%3Fc%23d#generate");
    });
  });

  describe("when something could not be loaded", () => {
    it("makes that step unknown -- never done, never todo -- and leaves the others alone", () => {
      expect(statuses({ ...BRAND_NEW, profile: failed })).toEqual([
        "profile:unknown",
        "model_key:todo",
        "first_search:todo",
        "track_job:todo",
        "generate_resume:todo",
      ]);
      expect(statuses({ ...EVERYTHING_DONE, modelKey: failed })[1]).toBe("model_key:unknown");
    });

    it("takes the track and resume steps down together when the applications could not be read", () => {
      const result = statuses({ ...EVERYTHING_DONE, applications: failed });
      expect(result[3]).toBe("track_job:unknown");
      expect(result[4]).toBe("generate_resume:unknown");
    });

    it("shows five unknown steps, none counted, and no card, when every input failed", () => {
      const view = deriveFirstRun(ALL_FAILED, { dismissed: false, searched: null });
      expect(view.steps.every((s) => s.status === "unknown")).toBe(true);
      expect(view.doneCount).toBe(0);
      // Nothing is known to be left to do, so there is nothing to guide.
      expect(view.visible).toBe(false);
      expect(view.nextStep).toBeNull();
    });

    // A finished account must not be told to redo a step because one request failed.
    it.each([
      ["the profile", { profile: failed }],
      ["the model key", { modelKey: failed }],
      ["the applications", { applications: failed }],
    ] as const)("hides the card for a finished account when %s could not be loaded", (_name, failure) => {
      const view = deriveFirstRun({ ...EVERYTHING_DONE, ...failure }, FRESH);
      expect(view.visible).toBe(false);
      expect(view.nextStep).toBeNull();
      expect(view.steps.some((s) => s.status === "unknown")).toBe(true);
    });

    it("hides the card when the only step not done is the first search, and it could not be checked", () => {
      // Profile, key, a tracked job and a resume are all there; nothing could say whether a
      // search ran (the saved searches failed, the browser's flag could not be read).
      const view = deriveFirstRun({ ...EVERYTHING_DONE, savedSearches: failed }, { dismissed: false, searched: null });
      expect(view.steps[2].status).toBe("unknown");
      expect(view.doneCount).toBe(4);
      expect(view.visible).toBe(false);
    });

    it("still shows for an account with something known to be left, whatever else failed", () => {
      const view = deriveFirstRun({ ...BRAND_NEW, profile: failed }, FRESH);
      expect(view.visible).toBe(true);
      expect(view.doneCount).toBe(0);
    });

    it("never names a step that could not be checked as the next one", () => {
      // The profile could not be read, the model key is known to be missing: the guide says
      // "Add a model key", not "Add your profile".
      const view = deriveFirstRun({ ...BRAND_NEW, profile: failed }, FRESH);
      expect(view.steps[0].status).toBe("unknown");
      expect(view.nextStep?.id).toBe("model_key");
    });

    it("counts a missing profile (a 404) as todo, so the card shows", () => {
      const view = deriveFirstRun({ ...EVERYTHING_DONE, profile: ok(false) }, FRESH);
      expect(view.visible).toBe(true);
      expect(view.nextStep?.id).toBe("profile");
      expect(view.doneCount).toBe(4);
    });
  });

  describe("visibility", () => {
    it("is hidden while any input is still loading", () => {
      for (const key of ["profile", "modelKey", "savedSearches", "applications"] as const) {
        expect(deriveFirstRun({ ...BRAND_NEW, [key]: loading }, FRESH).visible).toBe(false);
      }
      const allLoading: FirstRunFacts = {
        profile: loading,
        modelKey: loading,
        savedSearches: loading,
        applications: loading,
      };
      const view = deriveFirstRun(allLoading, FRESH);
      expect(view.visible).toBe(false);
      expect(view.doneCount).toBe(0);
      // A step whose answer has not arrived is not "todo".
      expect(view.steps.every((s) => s.status === "unknown")).toBe(true);
    });

    it("is hidden once dismissed, however much is left", () => {
      const view = deriveFirstRun(BRAND_NEW, { dismissed: true, searched: false });
      expect(view.visible).toBe(false);
      expect(view.steps).toHaveLength(5);
    });

    it("does not come back on its own: dismissed stays hidden whatever the data says", () => {
      expect(deriveFirstRun(ALL_FAILED, { dismissed: true, searched: null }).visible).toBe(false);
      expect(deriveFirstRun(BRAND_NEW, { dismissed: true, searched: false }).visible).toBe(false);
    });
  });
});

// ── loading the facts ──────────────────────────────────────────────────────

class FakeApiError extends Error {
  constructor(
    public readonly status: number,
    message = "x",
  ) {
    super(message);
  }
}

type Reply = unknown | Error;

function fakeFetcher(replies: Record<string, Reply>) {
  const calls: Array<{ path: string; init: unknown }> = [];
  const fetcher = async <T>(path: string, init?: unknown): Promise<T> => {
    calls.push({ path, init });
    const reply = replies[path];
    if (reply instanceof Error) throw reply;
    return reply as T;
  };
  return { fetcher, calls };
}

const GOOD: Record<string, Reply> = {
  "/profile/current": { id: "pv-1", canonical_json: {} },
  "/credentials": [
    { service: "llm", provider: "openrouter", is_validated: true },
    { service: "search", provider: "brave", is_validated: true },
  ],
  "/applications": [
    { id: "app-1", resume_exists: false },
    { id: "app-2", resume_exists: true },
  ],
  "/saved-searches": [{ id: "s-1" }],
};

describe("loadFirstRunFacts", () => {
  it("reads the four existing lists with plain GETs and nothing else -- never the rate-limited search", async () => {
    const { fetcher, calls } = fakeFetcher(GOOD);
    await loadFirstRunFacts(fetcher);
    expect(calls.map((c) => c.path).sort()).toEqual(
      ["/applications", "/credentials", "/profile/current", "/saved-searches"].sort(),
    );
    // No second argument: no method, no body.
    expect(calls.every((c) => c.init === undefined)).toBe(true);
    expect(calls.some((c) => c.path.startsWith("/discover"))).toBe(false);
  });

  it("turns good replies into facts", async () => {
    const { fetcher } = fakeFetcher(GOOD);
    expect(await loadFirstRunFacts(fetcher)).toEqual({
      profile: ok(true),
      modelKey: ok(true),
      savedSearches: ok(1),
      applications: ok({ count: 2, fromDiscover: 0, withResume: 1, resumeTargetId: "app-1" }),
    });
  });

  it("reads a 404 for the current profile as 'no profile yet', not as a failure", async () => {
    const { fetcher } = fakeFetcher({ ...GOOD, "/profile/current": new FakeApiError(404) });
    expect((await loadFirstRunFacts(fetcher)).profile).toEqual(ok(false));
  });

  it("reads any other profile failure -- a 500, a dropped connection -- as a failure", async () => {
    for (const error of [new FakeApiError(500), new Error("Failed to fetch"), new FakeApiError(401)]) {
      const { fetcher } = fakeFetcher({ ...GOOD, "/profile/current": error });
      expect((await loadFirstRunFacts(fetcher)).profile).toEqual(failed);
    }
  });

  it("does not take a profile reply that is not an object for a profile", async () => {
    for (const reply of [null, undefined, "ok", 7]) {
      const { fetcher } = fakeFetcher({ ...GOOD, "/profile/current": reply });
      expect((await loadFirstRunFacts(fetcher)).profile).toEqual(failed);
    }
  });

  it("wants a validated key for the model, and a search key does not count", async () => {
    const cases: Array<[unknown, ReturnType<typeof ok<boolean>>]> = [
      [[{ service: "llm", is_validated: true }], ok(true)],
      [[{ service: "llm", is_validated: false }], ok(false)],
      [[{ service: "search", is_validated: true }], ok(false)],
      [[], ok(false)],
      [[null, "x", 3, { service: "llm" }], ok(false)],
    ];
    for (const [rows, expected] of cases) {
      const { fetcher } = fakeFetcher({ ...GOOD, "/credentials": rows });
      expect((await loadFirstRunFacts(fetcher)).modelKey).toEqual(expected);
    }
  });

  it("counts applications and finds one without a resume to open", async () => {
    const rows = [
      { id: "a", resume_exists: true },
      { id: "b", resume_exists: false },
      { id: "c" },
    ];
    const { fetcher } = fakeFetcher({ ...GOOD, "/applications": rows });
    expect((await loadFirstRunFacts(fetcher)).applications).toEqual(
      ok({ count: 3, fromDiscover: 0, withResume: 1, resumeTargetId: "b" }),
    );
  });

  it("counts only the applications tracked from Discover as showing a search", async () => {
    const rows = [
      { id: "a", source_channel: "web" },
      { id: "b", source_channel: "discover" },
      { id: "c", source_channel: "telegram" },
      { id: "d", source_channel: "discover" },
      { id: "e" },
      { id: "f", source_channel: "Discover" },
    ];
    const { fetcher } = fakeFetcher({ ...GOOD, "/applications": rows });
    const facts = await loadFirstRunFacts(fetcher);
    expect(facts.applications).toMatchObject({ kind: "ok", value: { count: 6, fromDiscover: 2 } });
  });

  it("makes a pasted posting leave the first-search step todo, end to end", async () => {
    const { fetcher } = fakeFetcher({
      ...GOOD,
      "/saved-searches": [],
      "/applications": [{ id: "a", source_channel: "web", resume_exists: false }],
    });
    const facts = await loadFirstRunFacts(fetcher);
    const view = deriveFirstRun(facts, FRESH);
    expect(view.steps.find((s) => s.id === "first_search")?.status).toBe("todo");
    expect(view.nextStep?.id).toBe("first_search");
  });

  it("makes an application tracked from Discover tick the first-search step, end to end", async () => {
    const { fetcher } = fakeFetcher({
      ...GOOD,
      "/saved-searches": [],
      "/applications": [{ id: "a", source_channel: "discover", resume_exists: false }],
    });
    const view = deriveFirstRun(await loadFirstRunFacts(fetcher), FRESH);
    expect(view.steps.find((s) => s.id === "first_search")?.status).toBe("done");
  });

  it("opens the first application when none lacks a known resume state, and none when none has an id", async () => {
    const withIds = fakeFetcher({ ...GOOD, "/applications": [{ id: "a", resume_exists: true }] });
    expect((await loadFirstRunFacts(withIds.fetcher)).applications).toEqual(
      ok({ count: 1, fromDiscover: 0, withResume: 1, resumeTargetId: "a" }),
    );
    const noIds = fakeFetcher({ ...GOOD, "/applications": [{ resume_exists: false }, { id: 5 }, { id: "" }] });
    expect((await loadFirstRunFacts(noIds.fetcher)).applications).toEqual(
      ok({ count: 3, fromDiscover: 0, withResume: 0, resumeTargetId: null }),
    );
  });

  it("never picks a row with no id to open, even when it is the one without a resume", async () => {
    const lacking = fakeFetcher({
      ...GOOD,
      "/applications": [{ resume_exists: false }, { id: "b", resume_exists: false }],
    });
    expect((await loadFirstRunFacts(lacking.fetcher)).applications).toEqual(
      ok({ count: 2, fromDiscover: 0, withResume: 0, resumeTargetId: "b" }),
    );
    const mixed = fakeFetcher({
      ...GOOD,
      "/applications": [{ resume_exists: false }, { id: "b", resume_exists: true }],
    });
    expect((await loadFirstRunFacts(mixed.fetcher)).applications).toEqual(
      ok({ count: 2, fromDiscover: 0, withResume: 1, resumeTargetId: "b" }),
    );
  });

  it("reads a reply that is not a list as a failure for each list", async () => {
    const { fetcher } = fakeFetcher({
      "/profile/current": {},
      "/credentials": { rows: [] },
      "/applications": "nope",
      "/saved-searches": null,
    });
    expect(await loadFirstRunFacts(fetcher)).toEqual({
      profile: ok(true),
      modelKey: failed,
      savedSearches: failed,
      applications: failed,
    });
  });

  it("lets one failing request fail only its own facts", async () => {
    const { fetcher } = fakeFetcher({ ...GOOD, "/applications": new FakeApiError(500) });
    expect(await loadFirstRunFacts(fetcher)).toEqual({
      profile: ok(true),
      modelKey: ok(true),
      savedSearches: ok(1),
      applications: failed,
    });
  });

  it("fails every fact when nothing answers", async () => {
    const down = new Error("Failed to fetch");
    const { fetcher } = fakeFetcher({
      "/profile/current": down,
      "/credentials": down,
      "/applications": down,
      "/saved-searches": down,
    });
    expect(await loadFirstRunFacts(fetcher)).toEqual(ALL_FAILED);
  });
});

// ── storage ────────────────────────────────────────────────────────────────

function memoryStorage(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
  };
}

const throwingStorage: StorageLike = {
  getItem() {
    throw new Error("SecurityError: storage is blocked");
  },
  setItem() {
    throw new Error("QuotaExceededError");
  },
};

describe("the dismissed flag", () => {
  it("is not set on an empty storage", () => {
    expect(readDismissed(memoryStorage(), "user-1")).toBe(false);
  });

  it("is set by dismissing, and stays set", () => {
    const storage = memoryStorage();
    expect(writeDismissed(storage, "user-1")).toBe(true);
    expect(readDismissed(storage, "user-1")).toBe(true);
    expect(readDismissed(storage, "user-1")).toBe(true);
  });

  it("is per person: one account dismissing does not hide it from another on the same browser", () => {
    const storage = memoryStorage();
    writeDismissed(storage, "user-1");
    expect(readDismissed(storage, "user-2")).toBe(false);
    expect(dismissedKey("user-1")).not.toBe(dismissedKey("user-2"));
  });

  it("reads as not dismissed, without throwing, when storage is missing or throws", () => {
    expect(readDismissed(null, "user-1")).toBe(false);
    expect(readDismissed(throwingStorage, "user-1")).toBe(false);
  });

  it("says it could not store the flag, without throwing, when storage is missing or throws", () => {
    expect(writeDismissed(null, "user-1")).toBe(false);
    expect(writeDismissed(throwingStorage, "user-1")).toBe(false);
  });

  it("is not set by anything but its own value", () => {
    const storage = memoryStorage();
    storage.setItem(dismissedKey("user-1"), "0");
    expect(readDismissed(storage, "user-1")).toBe(false);
  });
});

describe("the first-search flag", () => {
  it("is false on an empty storage and true once marked", () => {
    const storage = memoryStorage();
    expect(readSearched(storage, "user-1")).toBe(false);
    markSearched(storage, "user-1");
    expect(readSearched(storage, "user-1")).toBe(true);
  });

  it("is per person, and a different key from the dismissed flag", () => {
    const storage = memoryStorage();
    markSearched(storage, "user-1");
    expect(readSearched(storage, "user-2")).toBe(false);
    expect(searchedKey("user-1")).not.toBe(dismissedKey("user-1"));
    expect(readDismissed(storage, "user-1")).toBe(false);
  });

  it("is unknown (null), not false, when it cannot be read", () => {
    expect(readSearched(null, "user-1")).toBeNull();
    expect(readSearched(throwingStorage, "user-1")).toBeNull();
  });

  it("marking never throws, whatever the storage does", () => {
    expect(() => markSearched(null, "user-1")).not.toThrow();
    expect(() => markSearched(throwingStorage, "user-1")).not.toThrow();
  });
});

describe("the completed flag", () => {
  it("is not set on an empty storage, and set once written", () => {
    const storage = memoryStorage();
    expect(readCompleted(storage, "user-1")).toBe(false);
    expect(writeCompleted(storage, "user-1")).toBe(true);
    expect(readCompleted(storage, "user-1")).toBe(true);
  });

  it("is per person, and a key of its own", () => {
    const storage = memoryStorage();
    writeCompleted(storage, "user-1");
    expect(readCompleted(storage, "user-2")).toBe(false);
    expect(completedKey("user-1")).not.toBe(completedKey("user-2"));
    expect(completedKey("user-1")).not.toBe(dismissedKey("user-1"));
    expect(completedKey("user-1")).not.toBe(searchedKey("user-1"));
    expect(readDismissed(storage, "user-1")).toBe(false);
  });

  it("reads as not completed, without throwing, when storage is missing or throws", () => {
    expect(readCompleted(null, "user-1")).toBe(false);
    expect(readCompleted(throwingStorage, "user-1")).toBe(false);
  });

  it("says it could not store the flag, without throwing, when storage is missing or throws", () => {
    expect(writeCompleted(null, "user-1")).toBe(false);
    expect(writeCompleted(throwingStorage, "user-1")).toBe(false);
  });

  it("is not set by anything but its own value", () => {
    const storage = memoryStorage();
    storage.setItem(completedKey("user-1"), "0");
    expect(readCompleted(storage, "user-1")).toBe(false);
  });
});

// ── the checklist as one small machine ─────────────────────────────────────

describe("the checklist's state, over a storage", () => {
  const USER = "user-1";

  function start(storage: StorageLike | null = memoryStorage(), user = USER): FirstRunState {
    return initialFirstRunState(storage, user);
  }

  it("starts loading, undismissed, uncompleted and not yet searched on an empty storage", () => {
    expect(start()).toEqual({ dismissed: false, completed: false, facts: STILL_LOADING, searched: null });
    expect(viewOfFirstRun(start()).visible).toBe(false); // still loading
  });

  describe("dismissing", () => {
    it("writes the person's own flag and hides the card at once", () => {
      const storage = memoryStorage();
      const loaded = { ...start(storage), ...onFirstRunFactsLoaded(BRAND_NEW, storage, USER) };
      expect(viewOfFirstRun(loaded).visible).toBe(true);

      const dismissed = onFirstRunDismissed(loaded, storage, USER);
      expect(storage.data.get(dismissedKey(USER))).toBe("1");
      expect(dismissed.dismissed).toBe(true);
      expect(viewOfFirstRun(dismissed).visible).toBe(false);
    });

    it("keeps what was loaded, so nothing else about the state is lost", () => {
      const storage = memoryStorage();
      const loaded = { ...start(storage), ...onFirstRunFactsLoaded(BRAND_NEW, storage, USER) };
      expect(onFirstRunDismissed(loaded, storage, USER)).toEqual({ ...loaded, dismissed: true });
    });

    it("stays hidden on a fresh start over the same storage, for that person only", () => {
      const storage = memoryStorage();
      onFirstRunDismissed(start(storage), storage, USER);
      expect(start(storage, USER).dismissed).toBe(true);
      expect(start(storage, "user-2").dismissed).toBe(false);
      // Written under the real id and no other.
      expect([...storage.data.keys()]).toEqual([dismissedKey(USER)]);
    });

    it("hides the card for this visit even when storage cannot take the flag", () => {
      const dismissed = onFirstRunDismissed(start(throwingStorage), throwingStorage, USER);
      expect(viewOfFirstRun({ ...dismissed, ...onFirstRunFactsLoaded(BRAND_NEW, throwingStorage, USER) }).visible).toBe(
        false,
      );
    });
  });

  describe("whether to ask the API at all", () => {
    it("asks for a card that is neither dismissed nor completed", () => {
      expect(shouldLoadFirstRun(start())).toBe(true);
    });

    it("does not ask for a dismissed card -- no requests are made for one", () => {
      const storage = memoryStorage();
      writeDismissed(storage, USER);
      expect(shouldLoadFirstRun(start(storage))).toBe(false);
      expect(shouldLoadFirstRun(start(storage, "user-2"))).toBe(true);
    });

    it("does not ask for an account already seen finished", () => {
      const storage = memoryStorage();
      writeCompleted(storage, USER);
      expect(shouldLoadFirstRun(start(storage))).toBe(false);
      expect(viewOfFirstRun(start(storage)).visible).toBe(false);
    });

    it("keeps a completed account's card hidden whatever a load would say", () => {
      const storage = memoryStorage();
      writeCompleted(storage, USER);
      const state = { ...start(storage), facts: BRAND_NEW, searched: false };
      expect(viewOfFirstRun(state).visible).toBe(false);
    });
  });

  describe("when the answers arrive", () => {
    it("reads the first-search flag at that moment, under the person's own key", () => {
      const storage = memoryStorage();
      expect(onFirstRunFactsLoaded(BRAND_NEW, storage, USER).searched).toBe(false);
      markSearched(storage, USER);
      expect(onFirstRunFactsLoaded(BRAND_NEW, storage, USER).searched).toBe(true);
      expect(onFirstRunFactsLoaded(BRAND_NEW, storage, "user-2").searched).toBe(false);
      expect(onFirstRunFactsLoaded(BRAND_NEW, null, USER).searched).toBeNull();
    });

    it("lets the Discover page's mark tick the step (the same key is read as is written)", () => {
      const storage = memoryStorage();
      markSearched(storage, USER);
      const state = { ...start(storage), ...onFirstRunFactsLoaded(BRAND_NEW, storage, USER) };
      expect(viewOfFirstRun(state).steps.find((s) => s.id === "first_search")?.status).toBe("done");
    });

    it("carries the facts through", () => {
      const storage = memoryStorage();
      expect(onFirstRunFactsLoaded(BRAND_NEW, storage, USER).facts).toBe(BRAND_NEW);
    });

    it("does not mark an account that still has something to do as completed", () => {
      const storage = memoryStorage();
      expect(onFirstRunFactsLoaded(BRAND_NEW, storage, USER).completed).toBe(false);
      expect(readCompleted(storage, USER)).toBe(false);
    });

    it("does not mark an account as completed on the strength of a failed request", () => {
      const storage = memoryStorage();
      // Four of five steps done, the fifth unknown: not seen finished.
      const result = onFirstRunFactsLoaded({ ...EVERYTHING_DONE, applications: failed }, storage, USER);
      expect(result.completed).toBe(false);
      expect(readCompleted(storage, USER)).toBe(false);
    });

    it("writes the completed flag, under the person's key, the first time all five are done", () => {
      const storage = memoryStorage();
      markSearched(storage, USER);
      const result = onFirstRunFactsLoaded(EVERYTHING_DONE, storage, USER);
      expect(result.completed).toBe(true);
      expect(storage.data.get(completedKey(USER))).toBe("1");
      expect(readCompleted(storage, "user-2")).toBe(false);
      // And the next start asks for nothing.
      expect(shouldLoadFirstRun(start(storage))).toBe(false);
    });

    it("hides the card for an account that is finished, and does not bring it back after one request fails", () => {
      const storage = memoryStorage();
      const state = { ...start(storage), ...onFirstRunFactsLoaded(EVERYTHING_DONE, storage, USER) };
      expect(viewOfFirstRun(state).visible).toBe(false);
      // A later visit: remembered, so nothing is loaded and nothing is shown.
      const later = start(storage);
      expect(shouldLoadFirstRun(later)).toBe(false);
      expect(viewOfFirstRun(later).visible).toBe(false);
    });

    it("still finishes the visit when storage cannot take the completed flag", () => {
      const result = onFirstRunFactsLoaded(EVERYTHING_DONE, throwingStorage, USER);
      expect(result.completed).toBe(true);
      expect(viewOfFirstRun({ ...start(null), ...result }).visible).toBe(false);
    });
  });
});

describe("browserStorage", () => {
  it("is the storage when the browser hands it over", () => {
    const storage = memoryStorage();
    expect(browserStorage(() => storage)).toBe(storage);
  });

  it("is null when even reaching for it throws (blocked site data), or there is none", () => {
    expect(
      browserStorage(() => {
        throw new Error("SecurityError");
      }),
    ).toBeNull();
    expect(browserStorage(() => undefined as unknown as StorageLike)).toBeNull();
  });

  it("is null where there is no window at all", () => {
    expect(browserStorage()).toBeNull();
  });
});
