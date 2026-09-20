import { describe, expect, it } from "vitest";
import { UNREACHABLE_MESSAGE, UNREADABLE_MESSAGE } from "./hiringSignals";
import type { Fetcher } from "./hiringSignalsClient";
import type { TabSearchRequest } from "./hiringSignalsTab";
import {
  SAVES_PATH,
  SEARCHES_PATH,
  SEARCH_PATH,
  createSavedSearch,
  loadSavedSearches,
  loadStandaloneSaves,
  removeSavedSearch,
  saveStandalonePost,
  savedSearchPath,
  searchHiringTab,
} from "./hiringSignalsTabClient";

// Everything here is synthetic. The fetcher is a recording fake: nothing in this
// file touches a network, and the linkedin.com strings are only ever parsed,
// never fetched.

const ID = "7000000000000000001";
const EMBED = `https://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`;

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

interface Call {
  path: string;
  init: RequestInit | undefined;
}

// A fetcher that records each call and answers with `reply` (a value to return or
// an error to throw).
function fakeFetcher(reply: unknown): { fetcher: Fetcher; calls: Call[] } {
  const calls: Call[] = [];
  const fetcher = (async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (reply instanceof Error) throw reply;
    return reply;
  }) as Fetcher;
  return { fetcher, calls };
}

function body(call: Call): Record<string, unknown> {
  return JSON.parse(String(call.init?.body)) as Record<string, unknown>;
}

const REQUEST: TabSearchRequest = {
  query: "software engineer",
  location: "Pune",
  freshness: "3days",
  locale: null,
};

const RAW_COUNTS = {
  raw_hits: 3,
  rejected: 0,
  duplicates: 0,
  role_mismatch_hidden: 1,
  echoes_hidden: 0,
  job_seekers_hidden: 0,
  too_old_hidden: 0,
  shown: 2,
};

function rawSignal(activityId: string): Record<string, unknown> {
  return {
    activity_id: activityId,
    post_url: `https://www.linkedin.com/posts/example-${activityId}`,
    embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${activityId}`,
    author_name: "Jane Example",
    posted_at: "2026-09-17T10:00:00Z",
    age_hint: null,
    species: "hiring_drive",
    comment_count: 4,
    registry_match: null,
    aggregator: false,
    saved: false,
  };
}

const RAW_SEARCH = {
  provider: "brave",
  cached: true,
  freshness: "3days",
  locale: "india",
  query_label: "software engineer -- Pune -- last 3 days",
  signals: [rawSignal(ID), rawSignal("7000000000000000002")],
  counts: RAW_COUNTS,
};

const RAW_SAVE = {
  id: "5c0d0000-0000-4000-8000-000000000001",
  activity_id: ID,
  post_url: `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`,
  embed_url: EMBED,
  created_at: "2026-09-18T12:00:00Z",
};

const RAW_SAVED_SEARCH = {
  id: "5c0d0000-0000-4000-8000-0000000000aa",
  query: "software engineer",
  location: "Pune",
  created_at: "2026-09-18T12:00:00Z",
};

describe("paths", () => {
  it("are the routes the server exposes", () => {
    expect(SEARCH_PATH).toBe("/hiring-signals/search");
    expect(SAVES_PATH).toBe("/hiring-signals/saves");
    expect(SEARCHES_PATH).toBe("/hiring-signals/searches");
    expect(savedSearchPath("abc")).toBe("/hiring-signals/searches/abc");
  });

  it("encode an id as one path segment, so it can never add path parts", () => {
    expect(savedSearchPath("../../x")).toBe("/hiring-signals/searches/..%2F..%2Fx");
    expect(savedSearchPath("a/b?c")).toBe("/hiring-signals/searches/a%2Fb%3Fc");
  });
});

describe("searchHiringTab", () => {
  it("POSTs the role, location and window -- and no locale when the person left it on Auto", async () => {
    const { fetcher, calls } = fakeFetcher(RAW_SEARCH);
    await searchHiringTab(fetcher, REQUEST);
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe("/hiring-signals/search");
    expect(calls[0].init?.method).toBe("POST");
    expect(body(calls[0])).toEqual({
      query: "software engineer",
      location: "Pune",
      freshness: "3days",
    });
    expect(Object.keys(body(calls[0])).sort()).toEqual(["freshness", "location", "query"]);
  });

  it("sends a chosen locale and a null location explicitly", async () => {
    const { fetcher, calls } = fakeFetcher(RAW_SEARCH);
    await searchHiringTab(fetcher, { ...REQUEST, location: null, locale: "india" });
    expect(body(calls[0])).toEqual({
      query: "software engineer",
      location: null,
      freshness: "3days",
      locale: "india",
    });
  });

  it("returns the parsed outcome", async () => {
    const { fetcher } = fakeFetcher(RAW_SEARCH);
    const result = await searchHiringTab(fetcher, REQUEST);
    expect(result.kind).toBe("ok");
    if (result.kind !== "ok") return;
    expect(result.value.unreadable).toBe(0);
    expect(result.value.response.provider).toBe("brave");
    expect(result.value.response.locale).toBe("india");
    expect(result.value.response.cached).toBe(true);
    expect(result.value.response.signals.map((s) => s.activity_id)).toEqual([
      ID,
      "7000000000000000002",
    ]);
    expect(result.value.response.counts).toEqual(RAW_COUNTS);
  });

  it("falls back to the requested window when the reply does not echo one", async () => {
    const { fetcher } = fakeFetcher({ ...RAW_SEARCH, freshness: undefined });
    const result = await searchHiringTab(fetcher, { ...REQUEST, freshness: "week" });
    expect(result.kind === "ok" && result.value.response.freshness).toBe("week");
  });

  it("is a failure with fixed wording -- not a crash -- for a reply it cannot read", async () => {
    for (const reply of [null, undefined, "x", [], { signals: "no" }]) {
      const result = await searchHiringTab(fakeFetcher(reply).fetcher, REQUEST);
      expect(result).toEqual({
        kind: "failed",
        failure: { kind: "error", message: UNREADABLE_MESSAGE, retryable: true },
      });
    }
  });

  it("answers 'disabled' for FEATURE_DISABLED, so the page can show its switched-off note", async () => {
    const { fetcher } = fakeFetcher(new FakeApiError(404, "off", "FEATURE_DISABLED"));
    expect(await searchHiringTab(fetcher, REQUEST)).toEqual({ kind: "disabled" });
  });

  it("classifies SETUP_REQUIRED with the server's own message", async () => {
    const { fetcher } = fakeFetcher(new FakeApiError(409, "Connect a search provider.", "SETUP_REQUIRED"));
    expect(await searchHiringTab(fetcher, REQUEST)).toEqual({
      kind: "failed",
      failure: { kind: "setup_required", message: "Connect a search provider." },
    });
  });

  it("respects the server's retryable flag, and falls back to the status when there is none", async () => {
    const retryable = await searchHiringTab(
      fakeFetcher(new FakeApiError(503, "Provider busy.", "PROVIDER_UNAVAILABLE", true)).fetcher,
      REQUEST,
    );
    expect(retryable).toEqual({
      kind: "failed",
      failure: { kind: "error", message: "Provider busy.", retryable: true },
    });
    const notRetryable = await searchHiringTab(
      fakeFetcher(new FakeApiError(503, "Key revoked.", "PROVIDER_UNAVAILABLE", false)).fetcher,
      REQUEST,
    );
    expect(notRetryable).toMatchObject({ failure: { retryable: false } });
    const invalid = await searchHiringTab(
      fakeFetcher(new FakeApiError(422, "query too long", "INVALID_INPUT")).fetcher,
      REQUEST,
    );
    expect(invalid).toMatchObject({ failure: { retryable: false } });
  });

  it("words a network failure with fixed text and says it can be retried", async () => {
    const { fetcher } = fakeFetcher(new TypeError("Failed to fetch"));
    expect(await searchHiringTab(fetcher, REQUEST)).toEqual({
      kind: "failed",
      failure: { kind: "error", message: UNREACHABLE_MESSAGE, retryable: true },
    });
  });

  it("never throws, even for something that is not an Error", async () => {
    const fetcher = (async () => {
      throw "boom";
    }) as unknown as Fetcher;
    const result = await searchHiringTab(fetcher, REQUEST);
    expect(result.kind).toBe("failed");
  });
});

describe("standalone saves", () => {
  it("loads from the standalone list route, not the per-application one", async () => {
    const { fetcher, calls } = fakeFetcher({ saves: [RAW_SAVE] });
    const result = await loadStandaloneSaves(fetcher);
    expect(calls[0].path).toBe("/hiring-signals/saves");
    expect(calls[0].init).toBeUndefined();
    expect(result.kind === "ok" && result.value.map((s) => s.activity_id)).toEqual([ID]);
  });

  it("is a failure for a body that is not { saves: [...] }", async () => {
    expect((await loadStandaloneSaves(fakeFetcher({}).fetcher)).kind).toBe("failed");
    expect((await loadStandaloneSaves(fakeFetcher(null).fetcher)).kind).toBe("failed");
  });

  it("words a network failure for the list in its own terms", async () => {
    const result = await loadStandaloneSaves(fakeFetcher(new TypeError("Failed to fetch")).fetcher);
    expect(result).toEqual({
      kind: "failed",
      failure: { kind: "error", message: "Could not load your saved posts.", retryable: true },
    });
  });

  it("saves by activity id and label and NOTHING else -- no url, author, title or text ever leaves", async () => {
    const { fetcher, calls } = fakeFetcher(RAW_SAVE);
    const result = await saveStandalonePost(fetcher, ID, "software engineer -- Pune -- last 3 days");
    expect(calls[0].path).toBe("/hiring-signals/saves");
    expect(calls[0].init?.method).toBe("POST");
    expect(body(calls[0])).toEqual({
      activity_id: ID,
      query_label: "software engineer -- Pune -- last 3 days",
    });
    expect(Object.keys(body(calls[0])).sort()).toEqual(["activity_id", "query_label"]);
    expect(result.kind === "ok" && result.value.id).toBe(RAW_SAVE.id);
  });

  it("sends a null label when there is none", async () => {
    const { fetcher, calls } = fakeFetcher(RAW_SAVE);
    await saveStandalonePost(fetcher, ID, "");
    expect(body(calls[0])).toEqual({ activity_id: ID, query_label: null });
  });

  it("is a failure for a save reply it cannot read", async () => {
    expect((await saveStandalonePost(fakeFetcher({}).fetcher, ID, "")).kind).toBe("failed");
  });

  it("answers 'disabled' for FEATURE_DISABLED on either call", async () => {
    const off = new FakeApiError(404, "off", "FEATURE_DISABLED");
    expect(await loadStandaloneSaves(fakeFetcher(off).fetcher)).toEqual({ kind: "disabled" });
    expect(await saveStandalonePost(fakeFetcher(off).fetcher, ID, "")).toEqual({ kind: "disabled" });
  });
});

describe("saved searches", () => {
  it("loads the list", async () => {
    const { fetcher, calls } = fakeFetcher({ searches: [RAW_SAVED_SEARCH] });
    const result = await loadSavedSearches(fetcher);
    expect(calls[0].path).toBe("/hiring-signals/searches");
    expect(result.kind === "ok" && result.value).toEqual([RAW_SAVED_SEARCH]);
  });

  it("is a failure for a body that is not { searches: [...] }, and words a network failure in its own terms", async () => {
    expect((await loadSavedSearches(fakeFetcher({ saves: [] }).fetcher)).kind).toBe("failed");
    expect(await loadSavedSearches(fakeFetcher(new TypeError("Failed to fetch")).fetcher)).toEqual({
      kind: "failed",
      failure: { kind: "error", message: "Could not load your saved searches.", retryable: true },
    });
  });

  it("creates a search from the person's own role and location, and NOTHING else", async () => {
    const { fetcher, calls } = fakeFetcher(RAW_SAVED_SEARCH);
    const result = await createSavedSearch(fetcher, { ...REQUEST, freshness: "week", locale: "india" });
    expect(calls[0].path).toBe("/hiring-signals/searches");
    expect(calls[0].init?.method).toBe("POST");
    expect(body(calls[0])).toEqual({ query: "software engineer", location: "Pune" });
    expect(Object.keys(body(calls[0])).sort()).toEqual(["location", "query"]);
    expect(result.kind === "ok" && result.value.id).toBe(RAW_SAVED_SEARCH.id);
  });

  it("sends a null location when there is none", async () => {
    const { fetcher, calls } = fakeFetcher({ ...RAW_SAVED_SEARCH, location: null });
    await createSavedSearch(fetcher, { ...REQUEST, location: null });
    expect(body(calls[0])).toEqual({ query: "software engineer", location: null });
  });

  it("passes a refusal (the 25-search cap) through with the server's own message", async () => {
    const cap = "You can keep at most 25 saved searches. Delete one to save another.";
    const result = await createSavedSearch(
      fakeFetcher(new FakeApiError(422, cap, "INVALID_INPUT")).fetcher,
      REQUEST,
    );
    expect(result).toEqual({
      kind: "failed",
      failure: { kind: "error", message: cap, retryable: false },
    });
  });

  it("is a failure for a created row it cannot read", async () => {
    expect((await createSavedSearch(fakeFetcher({ id: "x" }).fetcher, REQUEST)).kind).toBe("failed");
  });

  it("deletes by id, and reports 'removed'", async () => {
    const { fetcher, calls } = fakeFetcher(undefined);
    const result = await removeSavedSearch(fetcher, "abc");
    expect(calls[0].path).toBe("/hiring-signals/searches/abc");
    expect(calls[0].init?.method).toBe("DELETE");
    expect(result).toEqual({ kind: "ok", value: "removed" });
  });

  it("treats a search that is already gone as done -- the state the person asked for", async () => {
    const { fetcher } = fakeFetcher(new FakeApiError(404, "not found", "NOT_FOUND"));
    expect(await removeSavedSearch(fetcher, "abc")).toEqual({ kind: "ok", value: "already_gone" });
  });

  it("still reports every other delete failure, and FEATURE_DISABLED as disabled", async () => {
    expect(
      await removeSavedSearch(fakeFetcher(new FakeApiError(500, "boom", "INTERNAL")).fetcher, "abc"),
    ).toMatchObject({ kind: "failed", failure: { kind: "error", message: "boom" } });
    expect(
      await removeSavedSearch(
        fakeFetcher(new FakeApiError(404, "off", "FEATURE_DISABLED")).fetcher,
        "abc",
      ),
    ).toEqual({ kind: "disabled" });
  });
});
