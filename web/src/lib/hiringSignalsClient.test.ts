import { describe, expect, it } from "vitest";
import { UNREACHABLE_MESSAGE, UNREADABLE_MESSAGE } from "./hiringSignals";
import {
  type Fetcher,
  applicationBase,
  loadSavedPosts,
  removeSavedPost,
  saveRequestBody,
  savePost,
  savedPostPath,
  searchHiringPosts,
} from "./hiringSignalsClient";

// Everything here is synthetic. The fetcher is a recording fake: nothing in
// this file touches a network, and the linkedin.com strings are only ever
// parsed, never fetched.

const APP = "6b1f0c1e-0000-4000-8000-0000000000aa";
const ID = "7000000000000000001";
const EMBED = `https://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`;

class FakeApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
  ) {
    super(message);
  }
}

interface Call {
  path: string;
  init: RequestInit | undefined;
}

// A fetcher that records each call and answers with `reply` (a value to return
// or an error to throw).
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

const RAW_COUNTS = {
  raw_hits: 3,
  rejected: 0,
  duplicates: 0,
  off_topic_hidden: 1,
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
    role_match: true,
    registry_match: null,
    saved: false,
  };
}

const RAW_SEARCH = {
  provider: "brave",
  cached: true,
  freshness: "3days",
  query_label: "Acme -- software engineer -- last 3 days",
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

describe("paths", () => {
  it("builds the routes the server exposes", () => {
    expect(applicationBase(APP)).toBe(`/applications/${APP}/hiring-signals`);
    expect(savedPostPath("abc")).toBe("/hiring-signals/saves/abc");
  });

  it("encodes an id as one path segment, so it can never add path parts", () => {
    expect(applicationBase("a/b?c")).toBe("/applications/a%2Fb%3Fc/hiring-signals");
    expect(savedPostPath("../../x")).toBe("/hiring-signals/saves/..%2F..%2Fx");
  });
});

describe("searchHiringPosts", () => {
  it("POSTs the requested window, and only that", async () => {
    const { fetcher, calls } = fakeFetcher(RAW_SEARCH);
    await searchHiringPosts(fetcher, APP, "3days");
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe(`/applications/${APP}/hiring-signals/search`);
    expect(calls[0].init?.method).toBe("POST");
    expect(body(calls[0])).toEqual({ freshness: "3days" });
  });

  it("returns the parsed outcome", async () => {
    const { fetcher } = fakeFetcher(RAW_SEARCH);
    const result = await searchHiringPosts(fetcher, APP, "3days");
    expect(result.kind).toBe("ok");
    if (result.kind !== "ok") return;
    expect(result.value.response.provider).toBe("brave");
    expect(result.value.response.cached).toBe(true);
    expect(result.value.response.signals.map((s) => s.activity_id)).toEqual([
      ID,
      "7000000000000000002",
    ]);
    expect(result.value.unreadable).toBe(0);
  });

  it("falls back to the requested window when the reply does not echo one", async () => {
    const { fetcher } = fakeFetcher({ ...RAW_SEARCH, freshness: undefined });
    const result = await searchHiringPosts(fetcher, APP, "day");
    expect(result.kind === "ok" && result.value.response.freshness).toBe("day");
  });

  it("treats a reply it cannot read as a retryable failure with fixed wording, not a crash", async () => {
    for (const reply of [null, "nope", { signals: "x" }, [], undefined]) {
      const { fetcher } = fakeFetcher(reply);
      expect(await searchHiringPosts(fetcher, APP, "week")).toEqual({
        kind: "failed",
        failure: { kind: "error", message: UNREADABLE_MESSAGE, retryable: true },
      });
    }
  });

  it("reports a switched-off feature as 'disabled', so the panel renders nothing", async () => {
    const { fetcher } = fakeFetcher(new FakeApiError(404, "off", "FEATURE_DISABLED"));
    expect(await searchHiringPosts(fetcher, APP, "week")).toEqual({ kind: "disabled" });
  });

  it("carries setup-required through with the server's message", async () => {
    const { fetcher } = fakeFetcher(
      new FakeApiError(409, "Hiring signals need a search provider.", "SETUP_REQUIRED"),
    );
    expect(await searchHiringPosts(fetcher, APP, "week")).toEqual({
      kind: "failed",
      failure: { kind: "setup_required", message: "Hiring signals need a search provider." },
    });
  });

  it("marks a provider outage retryable and an invalid request not", async () => {
    const outage = fakeFetcher(new FakeApiError(503, "Try again in a moment.", "PROVIDER_UNAVAILABLE"));
    expect(await searchHiringPosts(outage.fetcher, APP, "week")).toEqual({
      kind: "failed",
      failure: { kind: "error", message: "Try again in a moment.", retryable: true },
    });
    const invalid = fakeFetcher(
      new FakeApiError(422, "This application has no company name to search for.", "INVALID_INPUT"),
    );
    expect(await searchHiringPosts(invalid.fetcher, APP, "week")).toEqual({
      kind: "failed",
      failure: {
        kind: "error",
        message: "This application has no company name to search for.",
        retryable: false,
      },
    });
  });

  it("turns a network failure into the friendly message", async () => {
    const { fetcher } = fakeFetcher(new TypeError("Failed to fetch"));
    expect(await searchHiringPosts(fetcher, APP, "week")).toEqual({
      kind: "failed",
      failure: { kind: "error", message: UNREACHABLE_MESSAGE, retryable: true },
    });
  });
});

describe("loadSavedPosts", () => {
  it("GETs this application's saves and parses them", async () => {
    const { fetcher, calls } = fakeFetcher({ saves: [RAW_SAVE] });
    const result = await loadSavedPosts(fetcher, APP);
    expect(calls[0].path).toBe(`/applications/${APP}/hiring-signals/saves`);
    expect(calls[0].init?.method).toBeUndefined();
    expect(calls[0].init?.body).toBeUndefined();
    expect(result).toEqual({ kind: "ok", value: [RAW_SAVE] });
  });

  it("is an empty list, not a failure, when nothing is saved", async () => {
    const { fetcher } = fakeFetcher({ saves: [] });
    expect(await loadSavedPosts(fetcher, APP)).toEqual({ kind: "ok", value: [] });
  });

  it("fails with fixed wording on a reply of the wrong shape", async () => {
    const { fetcher } = fakeFetcher({ items: [] });
    expect(await loadSavedPosts(fetcher, APP)).toEqual({
      kind: "failed",
      failure: { kind: "error", message: UNREADABLE_MESSAGE, retryable: true },
    });
  });

  it("says the saved posts could not be loaded on a network failure", async () => {
    const { fetcher } = fakeFetcher(new TypeError("Failed to fetch"));
    expect(await loadSavedPosts(fetcher, APP)).toEqual({
      kind: "failed",
      failure: { kind: "error", message: "Could not load your saved posts.", retryable: true },
    });
  });

  it("reports the switched-off feature as disabled", async () => {
    const { fetcher } = fakeFetcher(new FakeApiError(404, "off", "FEATURE_DISABLED"));
    expect(await loadSavedPosts(fetcher, APP)).toEqual({ kind: "disabled" });
  });
});

describe("savePost", () => {
  it("sends the numeric activity id and the search label -- and nothing else", async () => {
    const { fetcher, calls } = fakeFetcher(RAW_SAVE);
    await savePost(fetcher, APP, ID, "Acme -- software engineer -- last 7 days");
    expect(calls[0].path).toBe(`/applications/${APP}/hiring-signals/saves`);
    expect(calls[0].init?.method).toBe("POST");
    // The privacy contract: no url, author, title or post text ever leaves the
    // browser for the server to store. The key set is the whole assertion.
    expect(Object.keys(body(calls[0])).sort()).toEqual(["activity_id", "query_label"]);
    expect(body(calls[0])).toEqual({
      activity_id: ID,
      query_label: "Acme -- software engineer -- last 7 days",
    });
  });

  it("sends a null label, not an empty string, when there is none", () => {
    expect(saveRequestBody(ID, "")).toEqual({ activity_id: ID, query_label: null });
    expect(saveRequestBody(ID, "Acme")).toEqual({ activity_id: ID, query_label: "Acme" });
  });

  it("returns the saved row", async () => {
    const { fetcher } = fakeFetcher(RAW_SAVE);
    expect(await savePost(fetcher, APP, ID, "Acme")).toEqual({ kind: "ok", value: RAW_SAVE });
  });

  it("is the same result when repeated -- the server answers a repeat with the existing row", async () => {
    const { fetcher, calls } = fakeFetcher(RAW_SAVE);
    const first = await savePost(fetcher, APP, ID, "Acme");
    const second = await savePost(fetcher, APP, ID, "Acme");
    expect(second).toEqual(first);
    expect(calls).toHaveLength(2);
  });

  it("fails with fixed wording when the reply is not a saved post", async () => {
    for (const reply of [null, {}, { id: "", activity_id: ID }, { id: "x", activity_id: "abc" }]) {
      const { fetcher } = fakeFetcher(reply);
      expect(await savePost(fetcher, APP, ID, "Acme")).toEqual({
        kind: "failed",
        failure: { kind: "error", message: UNREADABLE_MESSAGE, retryable: true },
      });
    }
  });

  it("classifies failures like the search does", async () => {
    expect(
      await savePost(fakeFetcher(new FakeApiError(404, "off", "FEATURE_DISABLED")).fetcher, APP, ID, ""),
    ).toEqual({ kind: "disabled" });
    expect(
      await savePost(
        fakeFetcher(new FakeApiError(404, "no application found for id 'x'", "NOT_FOUND")).fetcher,
        APP,
        ID,
        "",
      ),
    ).toEqual({
      kind: "failed",
      failure: { kind: "not_found", message: "no application found for id 'x'" },
    });
    expect(await savePost(fakeFetcher(new TypeError("Failed to fetch")).fetcher, APP, ID, "")).toEqual({
      kind: "failed",
      failure: { kind: "error", message: UNREACHABLE_MESSAGE, retryable: true },
    });
  });
});

describe("removeSavedPost", () => {
  it("DELETEs the save by its row id", async () => {
    const { fetcher, calls } = fakeFetcher(undefined);
    expect(await removeSavedPost(fetcher, "save-1")).toEqual({ kind: "ok", value: "removed" });
    expect(calls[0].path).toBe("/hiring-signals/saves/save-1");
    expect(calls[0].init?.method).toBe("DELETE");
    expect(calls[0].init?.body).toBeUndefined();
  });

  it("counts an already-deleted save as done, without claiming the click did it", async () => {
    const { fetcher } = fakeFetcher(new FakeApiError(404, "no save found", "NOT_FOUND"));
    expect(await removeSavedPost(fetcher, "save-1")).toEqual({ kind: "ok", value: "already_gone" });
  });

  it("reports any other failure, and the switched-off feature", async () => {
    expect(
      await removeSavedPost(fakeFetcher(new FakeApiError(500, "boom", "INTERNAL_ERROR")).fetcher, "s"),
    ).toEqual({ kind: "failed", failure: { kind: "error", message: "boom", retryable: true } });
    expect(
      await removeSavedPost(fakeFetcher(new FakeApiError(404, "off", "FEATURE_DISABLED")).fetcher, "s"),
    ).toEqual({ kind: "disabled" });
    expect(await removeSavedPost(fakeFetcher(new TypeError("Failed to fetch")).fetcher, "s")).toEqual({
      kind: "failed",
      failure: { kind: "error", message: UNREACHABLE_MESSAGE, retryable: true },
    });
  });
});
