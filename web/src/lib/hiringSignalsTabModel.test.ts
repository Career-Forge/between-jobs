import { describe, expect, it } from "vitest";
import { savedCardKey, searchCardKey } from "./hiringSignals";
import type { Fetcher } from "./hiringSignalsClient";
import { saveButtonId } from "./hiringSignalsPanelModel";
import {
  ALREADY_SAVED_REASON,
  INITIAL_FORM,
  NOTICE_POST_REMOVED,
  NOTICE_POST_SAVED,
  NOTICE_SEARCH_ALREADY_SAVED,
  NOTICE_SEARCH_REMOVED,
  NOTICE_SEARCH_SAVED,
  QUERY_REQUIRED_MESSAGE,
  TAB_NOT_FOUND_MESSAGE,
  type TabForm,
  saveSearchGate,
} from "./hiringSignalsTab";
import {
  HiringTabModel,
  TAB_LOCATION_INPUT_ID,
  TAB_QUERY_INPUT_ID,
  TAB_SAVED_POSTS_HEADING_ID,
  TAB_SAVED_SEARCHES_HEADING_ID,
  TAB_SEARCH_BUTTON_ID,
  initialTabState,
  tabFocusTargetId,
  tabSaveButtonId,
} from "./hiringSignalsTabModel";
import type { SavedHiringSearch, TabSignal } from "./hiringSignalsTabTypes";

// The tab's state machine, driven against a fetcher the TEST controls: a request
// stays pending until the test resolves it, so a test can fire a second search
// while the first is in flight, or let a stale reply arrive after a newer result.
// Each of these is a guard a component test could never reach (the web package has
// no DOM). Ids, names and urls are synthetic; nothing is fetched.

const ID = "7000000000000000001";
const ID_2 = "7000000000000000002";
const LABEL = "software engineer -- Pune -- last 3 days";

const SEARCH = "/hiring-signals/search";
const SAVES = "/hiring-signals/saves";
const SEARCHES = "/hiring-signals/searches";

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

interface PendingCall {
  method: string;
  path: string;
  body: unknown;
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
}

// A fetcher whose every call waits for the test.
class Wire {
  readonly calls: PendingCall[] = [];

  readonly fetcher: Fetcher = <T>(path: string, init?: RequestInit): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      this.calls.push({
        method: init?.method ?? "GET",
        path,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
        resolve: (value) => resolve(value as T),
        reject,
      });
    });

  // The calls made so far with this method and this exact path.
  made(method: string, path: string): PendingCall[] {
    return this.calls.filter((c) => c.method === method && c.path === path);
  }

  answer(call: PendingCall, value: unknown): void {
    call.resolve(value);
  }

  fail(call: PendingCall, error: unknown): void {
    call.reject(error);
  }
}

// Lets every promise continuation the model has queued run.
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await new Promise((r) => setTimeout(r, 0));
}

function rawSignal(id = ID, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    activity_id: id,
    post_url: `https://www.linkedin.com/posts/example-${id}`,
    embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${id}`,
    author_name: "Jane Example",
    posted_at: "2026-09-17T10:00:00+00:00",
    age_hint: null,
    species: "unclassified",
    comment_count: null,
    registry_match: null,
    aggregator: false,
    saved: false,
    ...overrides,
  };
}

function searchBody(
  signals: Record<string, unknown>[] = [rawSignal()],
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    provider: "firecrawl",
    cached: false,
    freshness: "3days",
    locale: "global",
    query_label: LABEL,
    signals,
    counts: {
      raw_hits: signals.length,
      rejected: 0,
      duplicates: 0,
      role_mismatch_hidden: 0,
      echoes_hidden: 0,
      job_seekers_hidden: 0,
      too_old_hidden: 0,
      shown: signals.length,
    },
    ...overrides,
  };
}

function rawSave(id = ID, saveId = "save-1"): Record<string, unknown> {
  return {
    id: saveId,
    activity_id: id,
    post_url: `https://www.linkedin.com/feed/update/urn:li:activity:${id}`,
    embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${id}`,
    created_at: "2026-09-18T12:00:00+00:00",
  };
}

function rawSearchRow(
  query = "software engineer",
  location: string | null = "Pune",
  id = "search-1",
): Record<string, unknown> {
  return { id, query, location, created_at: "2026-09-18T12:00:00+00:00" };
}

function signalOf(id = ID): TabSignal {
  return {
    activity_id: id,
    post_url: "",
    embed_url: "",
    author_name: null,
    posted_at: null,
    age_hint: null,
    species: "unclassified",
    comment_count: null,
    registry_match: null,
    aggregator: null,
    saved: false,
  };
}

function setup() {
  const wire = new Wire();
  const model = new HiringTabModel(wire.fetcher);
  return { wire, model };
}

// A model whose two list requests have been answered.
async function loaded(
  saves: Record<string, unknown>[] = [],
  searches: Record<string, unknown>[] = [],
) {
  const { wire, model } = setup();
  const savesLoad = model.loadSaves();
  const searchesLoad = model.loadSearches();
  wire.answer(wire.made("GET", SAVES)[0], { saves });
  wire.answer(wire.made("GET", SEARCHES)[0], { searches });
  await Promise.all([savesLoad, searchesLoad]);
  return { wire, model };
}

function fill(model: HiringTabModel, form: Partial<TabForm>): void {
  if (form.query !== undefined) model.setQuery(form.query);
  if (form.location !== undefined) model.setLocation(form.location);
  if (form.freshness !== undefined) model.setFreshness(form.freshness);
  if (form.locale !== undefined) model.setLocale(form.locale);
}

describe("the initial state and subscriptions", () => {
  it("starts idle on the 3-day window with Auto wording, waiting for both lists", () => {
    const { model } = setup();
    const state = model.getState();
    expect(state).toEqual(initialTabState());
    expect(state.form).toEqual(INITIAL_FORM);
    expect(state.form.freshness).toBe("3days");
    expect(state.form.locale).toBe("auto");
    expect(state.search).toEqual({ kind: "idle" });
    expect(state.savesStatus).toBe("loading");
    expect(state.searchesStatus).toBe("loading");
    expect(state.disabled).toBe(false);
  });

  it("notifies a subscriber on every change, hands out a new snapshot each time, and stops on unsubscribe", () => {
    const { model } = setup();
    let notified = 0;
    const unsubscribe = model.subscribe(() => {
      notified += 1;
    });
    const before = model.getState();
    model.setFreshness("day");
    expect(notified).toBe(1);
    expect(model.getState()).not.toBe(before);
    expect(before.form.freshness).toBe("3days"); // the old snapshot is never mutated
    unsubscribe();
    model.setFreshness("week");
    expect(notified).toBe(1);
  });
});

describe("the form", () => {
  it("holds what is typed, and choosing a window or wording never searches by itself", () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "Pune", freshness: "week", locale: "india" });
    expect(model.getState().form).toEqual({
      query: "designer",
      location: "Pune",
      freshness: "week",
      locale: "india",
    });
    expect(wire.calls).toHaveLength(0);
  });

  it("clears a field's own error when it is edited, and only that one", async () => {
    const { model } = setup();
    fill(model, { query: "", location: "p".repeat(101) });
    await model.search();
    expect(model.getState().fieldErrors.query).not.toBeNull();
    expect(model.getState().fieldErrors.location).not.toBeNull();
    model.setQuery("designer");
    expect(model.getState().fieldErrors.query).toBeNull();
    expect(model.getState().fieldErrors.location).not.toBeNull();
    model.setLocation("Pune");
    expect(model.getState().fieldErrors.location).toBeNull();
  });
});

describe("searching", () => {
  it("sends nothing for an empty role: the error shows next to the field and focus goes there", async () => {
    const { wire, model } = setup();
    fill(model, { query: "   ", location: "Pune" });
    await model.search();
    expect(wire.calls).toHaveLength(0);
    expect(model.getState().search).toEqual({ kind: "idle" });
    expect(model.getState().fieldErrors).toEqual({ query: QUERY_REQUIRED_MESSAGE, location: null });
    const request = model.getState().focusRequest;
    expect(request && tabFocusTargetId(request.target)).toBe(TAB_QUERY_INPUT_ID);
  });

  it("sends nothing for an over-long location, and sends focus to that box", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "p".repeat(101) });
    await model.search();
    expect(wire.calls).toHaveLength(0);
    const request = model.getState().focusRequest;
    expect(request && tabFocusTargetId(request.target)).toBe(TAB_LOCATION_INPUT_ID);
  });

  it("POSTs the normalized role, location and window -- and no locale on Auto", async () => {
    const { wire, model } = setup();
    fill(model, { query: "  software   engineer ", location: " Pune ", freshness: "week" });
    const searching = model.search();
    const [call] = wire.made("POST", SEARCH);
    expect(wire.made("POST", SEARCH)).toHaveLength(1);
    expect(call.body).toEqual({ query: "software engineer", location: "Pune", freshness: "week" });
    expect("locale" in (call.body as object)).toBe(false);
    wire.answer(call, searchBody());
    await searching;
  });

  it("sends a chosen wording, and null for a blank location", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "", locale: "india" });
    const searching = model.search();
    expect(wire.made("POST", SEARCH)[0].body).toEqual({
      query: "designer",
      location: null,
      freshness: "3days",
      locale: "india",
    });
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await searching;
  });

  it("goes loading with the request, then ready with the outcome and the request that produced it", async () => {
    const { wire, model } = setup();
    fill(model, { query: "software engineer", location: "Pune" });
    const searching = model.search();
    expect(model.getState().search).toEqual({
      kind: "loading",
      request: { query: "software engineer", location: "Pune", freshness: "3days", locale: null },
    });
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([rawSignal(), rawSignal(ID_2)]));
    await searching;
    const state = model.getState();
    expect(state.search.kind).toBe("ready");
    if (state.search.kind !== "ready") return;
    expect(state.search.outcome.response.signals.map((s) => s.activity_id)).toEqual([ID, ID_2]);
    expect(state.search.request).toEqual({
      query: "software engineer",
      location: "Pune",
      freshness: "3days",
      locale: null,
    });
  });

  it("writes the normalized request back into the boxes, so they show what was searched", async () => {
    const { wire, model } = setup();
    fill(model, { query: "  data    scientist ", location: "  ", locale: "global" });
    const searching = model.search();
    expect(model.getState().form).toEqual({
      query: "data scientist",
      location: "",
      freshness: "3days",
      locale: "global",
    });
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await searching;
  });

  it("reloads the standalone saves after a successful search, so a save made elsewhere shows up", async () => {
    const { wire, model } = await loaded();
    fill(model, { query: "designer" });
    const searching = model.search();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await searching;
    expect(wire.made("GET", SAVES)).toHaveLength(2);
  });

  it("keeps the server's reply for a search that was empty: an empty result is a result, not a failure", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const searching = model.search();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([]));
    await searching;
    expect(model.getState().search.kind).toBe("ready");
  });
});

describe("overlapping searches", () => {
  it("sends ONE request for a double press of the same words", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "Pune" });
    const first = model.search();
    const second = model.search(); // Enter, then a click, in the same breath
    expect(wire.made("POST", SEARCH)).toHaveLength(1);
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await Promise.all([first, second]);
    expect(wire.made("POST", SEARCH)).toHaveLength(1);
  });

  it("treats words that differ only by spacing as the same search", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const first = model.search();
    model.setQuery("  designer  ");
    const second = model.search();
    expect(wire.made("POST", SEARCH)).toHaveLength(1);
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await Promise.all([first, second]);
  });

  it("allows the same search again once the first has finished (Search again is a real refresh)", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const first = model.search();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await first;
    const second = model.search();
    expect(wire.made("POST", SEARCH)).toHaveLength(2);
    wire.answer(wire.made("POST", SEARCH)[1], searchBody());
    await second;
  });

  it("sends a DIFFERENT search while one is in flight, and the newer one wins even when the slow first answers last", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const slow = model.search();
    fill(model, { query: "engineer" });
    const fast = model.search();
    const [slowCall, fastCall] = wire.made("POST", SEARCH);
    expect(wire.made("POST", SEARCH)).toHaveLength(2);

    wire.answer(fastCall, searchBody([rawSignal(ID_2)], { query_label: "engineer" }));
    await fast;
    expect(model.getState().search.kind).toBe("ready");

    // The slow first search finally answers: it must not overwrite the newer result.
    wire.answer(slowCall, searchBody([rawSignal(ID)], { query_label: "designer" }));
    await slow;
    const state = model.getState();
    expect(state.search.kind).toBe("ready");
    if (state.search.kind !== "ready") return;
    expect(state.search.outcome.response.query_label).toBe("engineer");
    expect(state.search.outcome.response.signals.map((s) => s.activity_id)).toEqual([ID_2]);
    expect(state.search.request.query).toBe("engineer");
    expect(state.form.query).toBe("engineer");
  });

  it("does not let a stale reply that arrives FIRST stand in for the newer search: the page stays loading", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const slow = model.search();
    fill(model, { query: "engineer" });
    const fast = model.search();
    const [slowCall, fastCall] = wire.made("POST", SEARCH);

    wire.answer(slowCall, searchBody([rawSignal(ID)], { query_label: "designer" }));
    await slow;
    // Still waiting for the search the person actually wants.
    expect(model.getState().search).toMatchObject({ kind: "loading", request: { query: "engineer" } });

    wire.answer(fastCall, searchBody([rawSignal(ID_2)], { query_label: "engineer" }));
    await fast;
    expect(model.getState().search).toMatchObject({ kind: "ready" });
  });

  it("discards a stale FAILURE too: an old error never replaces a newer result", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const slow = model.search();
    fill(model, { query: "engineer" });
    const fast = model.search();
    const [slowCall, fastCall] = wire.made("POST", SEARCH);
    wire.answer(fastCall, searchBody());
    await fast;
    wire.fail(slowCall, new FakeApiError(503, "Provider busy.", "PROVIDER_UNAVAILABLE", true));
    await slow;
    expect(model.getState().search.kind).toBe("ready");
  });

  it("keeps recognising a double press of the NEWEST search after an older reply has come back and gone", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const older = model.search();
    fill(model, { query: "engineer" });
    const newest = model.search();
    const [olderCall, newestCall] = wire.made("POST", SEARCH);

    // The older search answers (stale) and finishes; the newest is still in flight.
    wire.answer(olderCall, searchBody([rawSignal(ID)]));
    await older;

    // A second press of the newest words is still a double press, not a new search.
    const doublePress = model.search();
    expect(wire.made("POST", SEARCH)).toHaveLength(2);

    wire.answer(newestCall, searchBody([rawSignal(ID_2)]));
    await Promise.all([newest, doublePress]);
    expect(wire.made("POST", SEARCH)).toHaveLength(2);
  });

  it("lets the superseded search be run again afterwards: only the NEWEST in-flight request counts as a duplicate", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const a1 = model.search();
    fill(model, { query: "engineer" });
    const b = model.search();
    fill(model, { query: "designer" });
    const a2 = model.search(); // "designer" again -- the in-flight one is "engineer"
    expect(wire.made("POST", SEARCH)).toHaveLength(3);
    const [a1Call, bCall, a2Call] = wire.made("POST", SEARCH);
    wire.answer(a2Call, searchBody([rawSignal(ID)], { query_label: "designer (latest)" }));
    await a2;
    wire.answer(bCall, searchBody([rawSignal(ID_2)], { query_label: "engineer" }));
    wire.answer(a1Call, searchBody([rawSignal(ID_2)], { query_label: "designer (old)" }));
    await Promise.all([a1, b]);
    const state = model.getState();
    expect(state.search.kind === "ready" && state.search.outcome.response.query_label).toBe(
      "designer (latest)",
    );
  });

  it("still switches the page off for FEATURE_DISABLED from a superseded search: that is about the server, not the search", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const slow = model.search();
    fill(model, { query: "engineer" });
    void model.search();
    wire.fail(wire.made("POST", SEARCH)[0], new FakeApiError(404, "off", "FEATURE_DISABLED"));
    await slow;
    expect(model.getState().disabled).toBe(true);
  });

  it("clears the previous results' open embeds and errors, and leaves the saved posts' alone", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1")]);
    model.toggleEmbed(searchCardKey(ID));
    model.toggleEmbed(savedCardKey("save-1"));
    fill(model, { query: "designer" });
    const searching = model.search();
    expect([...model.getState().openKeys]).toEqual([savedCardKey("save-1")]);
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await searching;
  });
});

describe("card errors across searches", () => {
  it("clears the previous RESULTS' card errors when a new search replaces them, but keeps a saved post's own error", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1")]);
    // A failed removal on the saved card, and a failed save on a result card.
    const removing = model.removeSaved(model.getState().saves[0], savedCardKey("save-1"));
    wire.fail(wire.made("DELETE", `${SAVES}/save-1`)[0], new FakeApiError(500, "Could not remove.", "INTERNAL"));
    await removing;
    const saving = model.saveSignal(signalOf(ID_2), LABEL);
    wire.fail(wire.made("POST", SAVES)[0], new FakeApiError(500, "Could not save.", "INTERNAL"));
    await saving;
    expect(model.getState().cardErrors).toEqual({
      [savedCardKey("save-1")]: "Could not remove.",
      [searchCardKey(ID_2)]: "Could not save.",
    });

    fill(model, { query: "designer" });
    const searching = model.search();
    // The results are about to be replaced, so their errors go; the saved list is not.
    expect(model.getState().cardErrors).toEqual({ [savedCardKey("save-1")]: "Could not remove." });
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await searching;
  });
});

describe("failures and retry", () => {
  it("holds the failure and the search that failed", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "Pune" });
    const searching = model.search();
    wire.fail(
      wire.made("POST", SEARCH)[0],
      new FakeApiError(503, "Provider busy.", "PROVIDER_UNAVAILABLE", true),
    );
    await searching;
    expect(model.getState().search).toEqual({
      kind: "failed",
      failure: { kind: "error", message: "Provider busy.", retryable: true },
      request: { query: "designer", location: "Pune", freshness: "3days", locale: null },
    });
  });

  it("carries a setup-needed failure, so the view can link to Integrations", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const searching = model.search();
    wire.fail(wire.made("POST", SEARCH)[0], new FakeApiError(409, "Connect a provider.", "SETUP_REQUIRED"));
    await searching;
    expect(model.getState().search).toMatchObject({
      kind: "failed",
      failure: { kind: "setup_required", message: "Connect a provider." },
    });
  });

  it("retries the SAME search that failed, exactly", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "Pune", locale: "india", freshness: "week" });
    const first = model.search();
    wire.fail(wire.made("POST", SEARCH)[0], new FakeApiError(503, "busy", "PROVIDER_UNAVAILABLE", true));
    await first;

    // The boxes have since been edited; a retry is still the search that failed.
    fill(model, { query: "something else", location: "", locale: "global", freshness: "day" });
    const retrying = model.retry();
    const [, second] = wire.made("POST", SEARCH);
    expect(second.body).toEqual({
      query: "designer",
      location: "Pune",
      freshness: "week",
      locale: "india",
    });
    wire.answer(second, searchBody());
    await retrying;
    expect(model.getState().search.kind).toBe("ready");
    expect(model.getState().form.query).toBe("designer");
  });

  it("sends focus to the Search button on a retry, since the Try again button is replaced while it runs", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const first = model.search();
    wire.fail(wire.made("POST", SEARCH)[0], new FakeApiError(503, "busy", "PROVIDER_UNAVAILABLE", true));
    await first;
    expect(model.getState().focusRequest).toBeNull();
    const retrying = model.retry();
    expect(model.getState().focusRequest?.target).toEqual({ kind: "search_button" });
    wire.answer(wire.made("POST", SEARCH)[1], searchBody());
    await retrying;
  });

  it("does not move focus when a retry is ignored as a duplicate of a search already running", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const first = model.search();
    wire.fail(wire.made("POST", SEARCH)[0], new FakeApiError(503, "busy", "PROVIDER_UNAVAILABLE", true));
    await first;
    const retrying = model.retry();
    const focusAfterFirstRetry = model.getState().focusRequest;
    model.consumeFocusRequest(focusAfterFirstRetry!.id);
    // The state is loading now, so a second retry does nothing at all.
    await model.retry();
    expect(model.getState().focusRequest).toBeNull();
    expect(wire.made("POST", SEARCH)).toHaveLength(2);
    wire.answer(wire.made("POST", SEARCH)[1], searchBody());
    await retrying;
  });

  it("ignores a retry when nothing has failed", async () => {
    const { wire, model } = setup();
    await model.retry();
    expect(wire.calls).toHaveLength(0);
    fill(model, { query: "designer" });
    const searching = model.search();
    await model.retry(); // loading, not failed
    expect(wire.made("POST", SEARCH)).toHaveLength(1);
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await searching;
    await model.retry(); // ready, not failed
    expect(wire.made("POST", SEARCH)).toHaveLength(1);
  });

  it("goes to the switched-off state when the server says the feature is off, and stays there", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const searching = model.search();
    wire.fail(wire.made("POST", SEARCH)[0], new FakeApiError(404, "off", "FEATURE_DISABLED"));
    await searching;
    expect(model.getState().disabled).toBe(true);
  });
});

describe("widening the window", () => {
  it("re-runs the search the RESULTS came from with the wider window, whatever the boxes now say", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "Pune", locale: "india", freshness: "day" });
    const first = model.search();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([], { freshness: "day" }));
    await first;

    fill(model, { query: "typed afterwards", location: "Delhi", locale: "global" });
    const widening = model.widen("3days");
    const [, second] = wire.made("POST", SEARCH);
    expect(second.body).toEqual({
      query: "designer",
      location: "Pune",
      freshness: "3days",
      locale: "india",
    });
    // The boxes now show what was searched.
    expect(model.getState().form).toEqual({
      query: "designer",
      location: "Pune",
      freshness: "3days",
      locale: "india",
    });
    wire.answer(second, searchBody([rawSignal()]));
    await widening;
    expect(model.getState().search.kind).toBe("ready");
  });

  it("sends focus to the Search button, since the button that was pressed is replaced while the search runs", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", freshness: "day" });
    const first = model.search();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([], { freshness: "day" }));
    await first;
    // A plain search from the boxes does not move focus: the person is typing there.
    expect(model.getState().focusRequest).toBeNull();
    const widening = model.widen("3days");
    const request = model.getState().focusRequest;
    expect(request?.target).toEqual({ kind: "search_button" });
    expect(request && tabFocusTargetId(request.target)).toBe(TAB_SEARCH_BUTTON_ID);
    wire.answer(wire.made("POST", SEARCH)[1], searchBody());
    await widening;
  });

  it("does nothing when there are no results to widen", async () => {
    const { wire, model } = setup();
    await model.widen("week");
    expect(wire.calls).toHaveLength(0);
  });
});

describe("saved searches: running one", () => {
  it("fills the boxes with its role and location, resets the wording to Auto, keeps the window, and searches", async () => {
    const { wire, model } = setup();
    fill(model, { query: "old", location: "Old place", freshness: "week", locale: "india" });
    const saved: SavedHiringSearch = {
      id: "search-1",
      query: "product designer",
      location: "Berlin",
      created_at: "2026-09-18T12:00:00Z",
    };
    const running = model.runSavedSearch(saved);
    expect(wire.made("POST", SEARCH)).toHaveLength(1);
    expect(wire.made("POST", SEARCH)[0].body).toEqual({
      query: "product designer",
      location: "Berlin",
      freshness: "week",
    });
    expect(model.getState().form).toEqual({
      query: "product designer",
      location: "Berlin",
      freshness: "week",
      locale: "auto",
    });
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await running;
  });

  it("clears the location box for a saved search that has none", async () => {
    const { wire, model } = setup();
    fill(model, { query: "old", location: "Old place" });
    const running = model.runSavedSearch({
      id: "s",
      query: "designer",
      location: null,
      created_at: "",
    });
    expect(wire.made("POST", SEARCH)[0].body).toMatchObject({ query: "designer", location: null });
    wire.answer(wire.made("POST", SEARCH)[0], searchBody());
    await running;
    expect(model.getState().form.location).toBe("");
  });

  it("supersedes a search already in flight, like any other different search", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const slow = model.search();
    const running = model.runSavedSearch({ id: "s", query: "engineer", location: null, created_at: "" });
    expect(wire.made("POST", SEARCH)).toHaveLength(2);
    wire.answer(wire.made("POST", SEARCH)[1], searchBody([rawSignal(ID_2)], { query_label: "engineer" }));
    await running;
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([rawSignal(ID)], { query_label: "designer" }));
    await slow;
    const state = model.getState();
    expect(state.search.kind === "ready" && state.search.outcome.response.query_label).toBe("engineer");
  });
});

describe("saved posts (standalone)", () => {
  it("loads them, and reports a failure without losing the page", async () => {
    const { wire, model } = setup();
    const loading = model.loadSaves();
    wire.answer(wire.made("GET", SAVES)[0], { saves: [rawSave()] });
    await loading;
    expect(model.getState().savesStatus).toBe("ready");
    expect(model.getState().saves.map((s) => s.activity_id)).toEqual([ID]);

    const failing = model.loadSaves();
    wire.fail(wire.made("GET", SAVES)[1], new TypeError("Failed to fetch"));
    await failing;
    expect(model.getState().savesStatus).toBe("error");
    expect(model.getState().savesError).toBe("Could not load your saved posts.");
    // The last good list is still there.
    expect(model.getState().saves).toHaveLength(1);
  });

  it("disables the page when the load says the feature is off", async () => {
    const { wire, model } = setup();
    const loading = model.loadSaves();
    wire.fail(wire.made("GET", SAVES)[0], new FakeApiError(404, "off", "FEATURE_DISABLED"));
    await loading;
    expect(model.getState().disabled).toBe(true);
  });

  it("discards a list that was requested BEFORE a save landed, and takes it again (F-stale)", async () => {
    const { wire, model } = setup();
    const refresh = model.loadSaves(); // GET #1, in flight
    const saving = model.saveSignal(signalOf(ID), LABEL);
    wire.answer(wire.made("POST", SAVES)[0], rawSave(ID, "save-1")); // the save lands
    await saving;
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-1"]);

    // The older list (which does not know about the save) finally answers.
    wire.answer(wire.made("GET", SAVES)[0], { saves: [] });
    await refresh;
    // It did not overwrite the newer state; a fresh one was requested instead.
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-1"]);
    expect(wire.made("GET", SAVES)).toHaveLength(2);
    wire.answer(wire.made("GET", SAVES)[1], { saves: [rawSave(ID, "save-1")] });
    await settle();
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-1"]);
    expect(model.getState().savesStatus).toBe("ready");
  });

  it("saves by activity id and label ONLY, marks the post saving meanwhile, and announces it", async () => {
    const { wire, model } = await loaded();
    const saving = model.saveSignal(signalOf(ID), LABEL);
    expect(model.getState().savingIds.has(ID)).toBe(true);
    const [call] = wire.made("POST", SAVES);
    expect(call.body).toEqual({ activity_id: ID, query_label: LABEL });
    expect(Object.keys(call.body as object).sort()).toEqual(["activity_id", "query_label"]);
    wire.answer(call, rawSave(ID, "save-1"));
    await saving;
    const state = model.getState();
    expect(state.savingIds.size).toBe(0);
    expect(state.saves.map((s) => s.id)).toEqual(["save-1"]);
    expect(state.notice?.text).toBe(NOTICE_POST_SAVED);
  });

  it("sends ONE request for a double click on Save", async () => {
    const { wire, model } = await loaded();
    const first = model.saveSignal(signalOf(ID), LABEL);
    const second = model.saveSignal(signalOf(ID), LABEL);
    expect(wire.made("POST", SAVES)).toHaveLength(1);
    wire.answer(wire.made("POST", SAVES)[0], rawSave(ID));
    await Promise.all([first, second]);
    expect(model.getState().saves).toHaveLength(1);
  });

  it("does not save a post that is already saved", async () => {
    const { wire, model } = await loaded([rawSave(ID)]);
    await model.saveSignal(signalOf(ID), LABEL);
    expect(wire.made("POST", SAVES)).toHaveLength(0);
  });

  it("shows a failed save on that card, in the server's words, and lets the person try again", async () => {
    const { wire, model } = await loaded();
    const saving = model.saveSignal(signalOf(ID), LABEL);
    wire.fail(wire.made("POST", SAVES)[0], new FakeApiError(503, "Save failed upstream.", "INTERNAL"));
    await saving;
    expect(model.getState().cardErrors[searchCardKey(ID)]).toBe("Save failed upstream.");
    expect(model.getState().savingIds.size).toBe(0);
    expect(model.getState().saves).toHaveLength(0);

    const again = model.saveSignal(signalOf(ID), LABEL);
    expect(model.getState().cardErrors[searchCardKey(ID)]).toBeUndefined();
    wire.answer(wire.made("POST", SAVES)[1], rawSave(ID));
    await again;
    expect(model.getState().saves).toHaveLength(1);
  });

  it("removes a save, announces it, and hands focus back to the result card's own Save button", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1")]);
    const save = model.getState().saves[0];
    const removing = model.removeSaved(save, searchCardKey(ID));
    expect(model.getState().removingIds.has("save-1")).toBe(true);
    const [call] = wire.made("DELETE", `${SAVES}/save-1`);
    wire.answer(call, undefined);
    await removing;
    const state = model.getState();
    expect(state.saves).toEqual([]);
    expect(state.notice?.text).toBe(NOTICE_POST_REMOVED);
    expect(state.removingIds.size).toBe(0);
    expect(state.focusRequest?.target).toEqual({ kind: "save_button", activityId: ID });
    expect(tabFocusTargetId(state.focusRequest!.target)).toBe(tabSaveButtonId(ID));
  });

  it("sends focus to the saved-posts heading when a saved card is removed and others remain", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1"), rawSave(ID_2, "save-2")]);
    const removing = model.removeSaved(model.getState().saves[0], savedCardKey("save-1"));
    wire.answer(wire.made("DELETE", `${SAVES}/save-1`)[0], undefined);
    await removing;
    const target = model.getState().focusRequest?.target;
    expect(target).toEqual({ kind: "saved_posts_heading" });
    expect(target && tabFocusTargetId(target)).toBe(TAB_SAVED_POSTS_HEADING_ID);
  });

  it("sends focus to the search button when the last saved card is removed", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1")]);
    const removing = model.removeSaved(model.getState().saves[0], savedCardKey("save-1"));
    wire.answer(wire.made("DELETE", `${SAVES}/save-1`)[0], undefined);
    await removing;
    const target = model.getState().focusRequest?.target;
    expect(target).toEqual({ kind: "search_button" });
    expect(target && tabFocusTargetId(target)).toBe(TAB_SEARCH_BUTTON_ID);
  });

  it("treats a save that is already gone as removed -- without announcing something this click did not do", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1")]);
    const removing = model.removeSaved(model.getState().saves[0], savedCardKey("save-1"));
    wire.fail(wire.made("DELETE", `${SAVES}/save-1`)[0], new FakeApiError(404, "gone", "NOT_FOUND"));
    await removing;
    expect(model.getState().saves).toEqual([]);
    expect(model.getState().notice).toBeNull();
    expect(model.getState().cardErrors).toEqual({});
  });

  it("shows a failed removal on its card and keeps the save", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1")]);
    const removing = model.removeSaved(model.getState().saves[0], savedCardKey("save-1"));
    wire.fail(wire.made("DELETE", `${SAVES}/save-1`)[0], new FakeApiError(500, "Could not remove.", "INTERNAL"));
    await removing;
    expect(model.getState().saves).toHaveLength(1);
    expect(model.getState().cardErrors[savedCardKey("save-1")]).toBe("Could not remove.");
  });

  it("rewords a NOT_FOUND on a save action for the tab -- never 'application'", async () => {
    const { wire, model } = await loaded();
    const saving = model.saveSignal(signalOf(ID), LABEL);
    wire.fail(wire.made("POST", SAVES)[0], new FakeApiError(404, "Application 6b1f not found", "NOT_FOUND"));
    await saving;
    expect(model.getState().cardErrors[searchCardKey(ID)]).toBe(TAB_NOT_FOUND_MESSAGE);
  });

  it("sends ONE delete for a double click on Remove", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1")]);
    const save = model.getState().saves[0];
    const first = model.removeSaved(save, savedCardKey("save-1"));
    const second = model.removeSaved(save, savedCardKey("save-1"));
    expect(wire.made("DELETE", `${SAVES}/save-1`)).toHaveLength(1);
    wire.answer(wire.made("DELETE", `${SAVES}/save-1`)[0], undefined);
    await Promise.all([first, second]);
  });

  it("toggles an embed open and shut", () => {
    const { model } = setup();
    model.toggleEmbed("search:1");
    expect(model.getState().openKeys.has("search:1")).toBe(true);
    model.toggleEmbed("search:1");
    expect(model.getState().openKeys.has("search:1")).toBe(false);
  });

  it("clears a focus request once it has been consumed, and ignores a stale id", async () => {
    const { model } = setup();
    fill(model, { query: "" });
    await model.search();
    const request = model.getState().focusRequest!;
    model.consumeFocusRequest(request.id + 99);
    expect(model.getState().focusRequest).not.toBeNull();
    model.consumeFocusRequest(request.id);
    expect(model.getState().focusRequest).toBeNull();
  });
});

describe("saved searches: the list", () => {
  it("loads them, and reports a failure without losing the page", async () => {
    const { wire, model } = setup();
    const loading = model.loadSearches();
    wire.answer(wire.made("GET", SEARCHES)[0], { searches: [rawSearchRow()] });
    await loading;
    expect(model.getState().searchesStatus).toBe("ready");
    expect(model.getState().searches.map((s) => s.query)).toEqual(["software engineer"]);

    const failing = model.loadSearches();
    wire.fail(wire.made("GET", SEARCHES)[1], new TypeError("Failed to fetch"));
    await failing;
    expect(model.getState().searchesStatus).toBe("error");
    expect(model.getState().searchesError).toBe("Could not load your saved searches.");
    expect(model.getState().searches).toHaveLength(1);
  });

  it("disables the page when the load says the feature is off", async () => {
    const { wire, model } = setup();
    const loading = model.loadSearches();
    wire.fail(wire.made("GET", SEARCHES)[0], new FakeApiError(404, "off", "FEATURE_DISABLED"));
    await loading;
    expect(model.getState().disabled).toBe(true);
  });

  it("discards a list requested BEFORE a save landed, and takes it again", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "Pune" });
    const refresh = model.loadSearches();
    const saving = model.saveCurrentSearch();
    wire.answer(wire.made("POST", SEARCHES)[0], rawSearchRow("designer", "Pune", "search-9"));
    await saving;
    wire.answer(wire.made("GET", SEARCHES)[0], { searches: [] }); // stale
    await refresh;
    expect(model.getState().searches.map((s) => s.id)).toEqual(["search-9"]);
    expect(wire.made("GET", SEARCHES)).toHaveLength(2);
    wire.answer(wire.made("GET", SEARCHES)[1], { searches: [rawSearchRow("designer", "Pune", "search-9")] });
    await settle();
    expect(model.getState().searchesStatus).toBe("ready");
    expect(model.getState().searches.map((s) => s.id)).toEqual(["search-9"]);
  });
});

describe("saved searches: saving what is in the boxes", () => {
  it("sends only the role and location -- normalized -- and puts the new search on top", async () => {
    const { wire, model } = await loaded([], [rawSearchRow("older", null, "search-0")]);
    fill(model, {
      query: "  software   engineer ",
      location: " Pune ",
      freshness: "week",
      locale: "india",
    });
    const saving = model.saveCurrentSearch();
    expect(model.getState().savingSearch).toBe(true);
    const [call] = wire.made("POST", SEARCHES);
    expect(call.body).toEqual({ query: "software engineer", location: "Pune" });
    expect(Object.keys(call.body as object).sort()).toEqual(["location", "query"]);
    wire.answer(call, rawSearchRow("software engineer", "Pune", "search-1"));
    await saving;
    const state = model.getState();
    expect(state.savingSearch).toBe(false);
    expect(state.searches.map((s) => s.id)).toEqual(["search-1", "search-0"]);
    expect(state.notice?.text).toBe(NOTICE_SEARCH_SAVED);
    expect(state.saveSearchError).toBeNull();
  });

  it("sends a null location for a blank box", async () => {
    const { wire, model } = await loaded();
    fill(model, { query: "designer", location: "  " });
    const saving = model.saveCurrentSearch();
    expect(wire.made("POST", SEARCHES)[0].body).toEqual({ query: "designer", location: null });
    wire.answer(wire.made("POST", SEARCHES)[0], rawSearchRow("designer", null));
    await saving;
  });

  it("does not run, schedule or search anything by saving: no search request goes out", async () => {
    const { wire, model } = await loaded();
    fill(model, { query: "designer" });
    const saving = model.saveCurrentSearch();
    wire.answer(wire.made("POST", SEARCHES)[0], rawSearchRow("designer", null));
    await saving;
    expect(wire.made("POST", SEARCH)).toHaveLength(0);
    expect(model.getState().search).toEqual({ kind: "idle" });
  });

  it("answers a press on the unavailable button instead of ignoring it: an empty role shows its error and takes focus", async () => {
    const { wire, model } = await loaded();
    await model.saveCurrentSearch();
    expect(wire.made("POST", SEARCHES)).toHaveLength(0);
    expect(model.getState().fieldErrors.query).toBe(QUERY_REQUIRED_MESSAGE);
    expect(model.getState().focusRequest?.target).toEqual({ kind: "query_input" });
  });

  it("answers a press on an already-saved search by saying so, and sends nothing", async () => {
    const { wire, model } = await loaded([], [rawSearchRow("Software Engineer", "Pune")]);
    fill(model, { query: "  software   ENGINEER", location: "pune" });
    await model.saveCurrentSearch();
    expect(wire.made("POST", SEARCHES)).toHaveLength(0);
    expect(model.getState().notice?.text).toBe(ALREADY_SAVED_REASON);
  });

  it("sends ONE request for a double click on Save this search", async () => {
    const { wire, model } = await loaded();
    fill(model, { query: "designer" });
    const first = model.saveCurrentSearch();
    const second = model.saveCurrentSearch();
    expect(wire.made("POST", SEARCHES)).toHaveLength(1);
    wire.answer(wire.made("POST", SEARCHES)[0], rawSearchRow("designer", null));
    await Promise.all([first, second]);
    expect(model.getState().searches).toHaveLength(1);
  });

  it("puts exactly one entry in the list when the server answers with a search that is already there (200, not 201)", async () => {
    // The list has not loaded yet, so the page cannot know it is a repeat.
    const { wire, model } = setup();
    fill(model, { query: "designer", location: "Pune" });
    const listing = model.loadSearches();
    const saving = model.saveCurrentSearch();
    wire.answer(wire.made("GET", SEARCHES)[0], { searches: [rawSearchRow("designer", "Pune", "search-7")] });
    await listing;
    wire.answer(wire.made("POST", SEARCHES)[0], rawSearchRow("designer", "Pune", "search-7"));
    await saving;
    const state = model.getState();
    expect(state.searches.map((s) => s.id)).toEqual(["search-7"]);
    expect(state.notice?.text).toBe(NOTICE_SEARCH_ALREADY_SAVED);
  });

  it("shows the server's own words when it refuses (the 25-search cap), keeps the list, and offers no retry", async () => {
    const cap = "You can keep at most 25 saved searches. Delete one to save another.";
    const { wire, model } = await loaded(
      [],
      Array.from({ length: 25 }, (_, i) => rawSearchRow(`role ${i}`, null, `search-${i}`)),
    );
    fill(model, { query: "the twenty-sixth" });
    // The page does NOT pre-empt the cap: the button is open, and the server decides.
    expect(saveSearchGate(model.getState().form, model.getState().searches)).toEqual({ kind: "ok" });
    const saving = model.saveCurrentSearch();
    wire.fail(wire.made("POST", SEARCHES)[0], new FakeApiError(422, cap, "INVALID_INPUT"));
    await saving;
    const state = model.getState();
    expect(state.saveSearchError).toBe(cap);
    expect(state.searches).toHaveLength(25);
    expect(state.savingSearch).toBe(false);
  });

  it("clears the refusal at the next attempt", async () => {
    const { wire, model } = await loaded();
    fill(model, { query: "designer" });
    const first = model.saveCurrentSearch();
    wire.fail(wire.made("POST", SEARCHES)[0], new FakeApiError(422, "Too many.", "INVALID_INPUT"));
    await first;
    expect(model.getState().saveSearchError).toBe("Too many.");
    const second = model.saveCurrentSearch();
    expect(model.getState().saveSearchError).toBeNull();
    wire.answer(wire.made("POST", SEARCHES)[1], rawSearchRow("designer", null));
    await second;
  });

  it("switches the page off when the server says the feature is off", async () => {
    const { wire, model } = await loaded();
    fill(model, { query: "designer" });
    const saving = model.saveCurrentSearch();
    wire.fail(wire.made("POST", SEARCHES)[0], new FakeApiError(404, "off", "FEATURE_DISABLED"));
    await saving;
    expect(model.getState().disabled).toBe(true);
    expect(model.getState().savingSearch).toBe(false);
  });

  it("agrees with the button: the model sends a request exactly when the gate is open", async () => {
    const saved = [rawSearchRow("Software Engineer", "Pune", "search-1")];
    const forms: Partial<TabForm>[] = [
      { query: "" },
      { query: "   " },
      { query: "a".repeat(201) },
      { query: "designer", location: "p".repeat(101) },
      { query: "software engineer", location: "pune" },
      { query: "  SOFTWARE   engineer ", location: " Pune" },
      { query: "software engineer", location: "" },
      { query: "software engineer", location: "Delhi" },
      { query: "designer" },
    ];
    for (const form of forms) {
      const { wire, model } = await loaded([], saved);
      fill(model, form);
      const open = saveSearchGate(model.getState().form, model.getState().searches).kind === "ok";
      const saving = model.saveCurrentSearch();
      const sent = wire.made("POST", SEARCHES).length;
      expect(sent).toBe(open ? 1 : 0);
      if (open) wire.answer(wire.made("POST", SEARCHES)[0], rawSearchRow("x", null, "new"));
      await saving;
    }
  });
});

describe("saved searches: deleting one", () => {
  it("deletes it, announces it, and sends focus to the list's heading while others remain", async () => {
    const { wire, model } = await loaded(
      [],
      [rawSearchRow("a", null, "search-a"), rawSearchRow("b", null, "search-b")],
    );
    const deleting = model.deleteSearch(model.getState().searches[0]);
    expect(model.getState().deletingSearchIds.has("search-a")).toBe(true);
    wire.answer(wire.made("DELETE", `${SEARCHES}/search-a`)[0], undefined);
    await deleting;
    const state = model.getState();
    expect(state.searches.map((s) => s.id)).toEqual(["search-b"]);
    expect(state.notice?.text).toBe(NOTICE_SEARCH_REMOVED);
    expect(state.deletingSearchIds.size).toBe(0);
    expect(state.focusRequest?.target).toEqual({ kind: "saved_searches_heading" });
    expect(tabFocusTargetId(state.focusRequest!.target)).toBe(TAB_SAVED_SEARCHES_HEADING_ID);
  });

  it("sends focus to the role box when the last one is deleted, since the list is gone", async () => {
    const { wire, model } = await loaded([], [rawSearchRow("a", null, "search-a")]);
    const deleting = model.deleteSearch(model.getState().searches[0]);
    wire.answer(wire.made("DELETE", `${SEARCHES}/search-a`)[0], undefined);
    await deleting;
    expect(model.getState().searches).toEqual([]);
    expect(model.getState().focusRequest?.target).toEqual({ kind: "query_input" });
  });

  it("treats one that is already gone as deleted, without announcing something this click did not do", async () => {
    const { wire, model } = await loaded([], [rawSearchRow("a", null, "search-a")]);
    const deleting = model.deleteSearch(model.getState().searches[0]);
    wire.fail(wire.made("DELETE", `${SEARCHES}/search-a`)[0], new FakeApiError(404, "gone", "NOT_FOUND"));
    await deleting;
    expect(model.getState().searches).toEqual([]);
    expect(model.getState().notice).toBeNull();
    expect(model.getState().deleteSearchError).toBeNull();
  });

  it("shows a failed delete in the list, in the server's words, and keeps the search", async () => {
    const { wire, model } = await loaded([], [rawSearchRow("a", null, "search-a")]);
    const deleting = model.deleteSearch(model.getState().searches[0]);
    wire.fail(wire.made("DELETE", `${SEARCHES}/search-a`)[0], new FakeApiError(500, "Could not delete.", "INTERNAL"));
    await deleting;
    expect(model.getState().deleteSearchError).toBe("Could not delete.");
    expect(model.getState().searches).toHaveLength(1);
    expect(model.getState().deletingSearchIds.size).toBe(0);
  });

  it("sends ONE delete for a double click", async () => {
    const { wire, model } = await loaded([], [rawSearchRow("a", null, "search-a")]);
    const search = model.getState().searches[0];
    const first = model.deleteSearch(search);
    const second = model.deleteSearch(search);
    expect(wire.made("DELETE", `${SEARCHES}/search-a`)).toHaveLength(1);
    wire.answer(wire.made("DELETE", `${SEARCHES}/search-a`)[0], undefined);
    await Promise.all([first, second]);
  });

  it("discards a list requested before the delete landed, so a deleted search does not reappear", async () => {
    const { wire, model } = await loaded([], [rawSearchRow("a", null, "search-a")]);
    const refresh = model.loadSearches();
    const deleting = model.deleteSearch(model.getState().searches[0]);
    wire.answer(wire.made("DELETE", `${SEARCHES}/search-a`)[0], undefined);
    await deleting;
    wire.answer(wire.made("GET", SEARCHES)[1], { searches: [rawSearchRow("a", null, "search-a")] }); // stale
    await refresh;
    expect(model.getState().searches).toEqual([]);
    expect(wire.made("GET", SEARCHES)).toHaveLength(3);
  });

  it("switches the page off when the server says the feature is off", async () => {
    const { wire, model } = await loaded([], [rawSearchRow("a", null, "search-a")]);
    const deleting = model.deleteSearch(model.getState().searches[0]);
    wire.fail(wire.made("DELETE", `${SEARCHES}/search-a`)[0], new FakeApiError(404, "off", "FEATURE_DISABLED"));
    await deleting;
    expect(model.getState().disabled).toBe(true);
  });
});

describe("isolation from the job-search saved searches", () => {
  it("only ever talks to /hiring-signals routes -- never /saved-searches, and nothing that publishes", async () => {
    const { wire, model } = await loaded([rawSave()], [rawSearchRow()]);
    fill(model, { query: "designer", location: "Pune" });
    const searching = model.search();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([rawSignal()]));
    await searching;
    const saving = model.saveCurrentSearch();
    wire.answer(wire.made("POST", SEARCHES)[0], rawSearchRow("designer", "Pune", "s2"));
    await saving;
    const saved = model.saveSignal(signalOf(ID_2), LABEL);
    wire.answer(wire.made("POST", SAVES)[0], rawSave(ID_2, "save-2"));
    await saved;
    await settle();
    for (const call of wire.calls) {
      expect(call.path.startsWith("/hiring-signals/")).toBe(true);
      expect(call.path).not.toContain("saved-searches");
      expect(call.path).not.toContain("discover");
    }
  });
});

describe("form type sanity", () => {
  it("the form the page starts with is the one the constant says", () => {
    const { model } = setup();
    expect(model.getState().form).toBe(INITIAL_FORM);
  });
});


// A refused "Save this search" (the 25-search cap, a role the server would not
// take) is about what was in the boxes and what the list held. It must not outlive
// either: the person deletes a search as the message told them to, or edits the
// box, and the instruction they just followed stays on screen.
describe("a refused 'Save this search' does not outlive its cause", () => {
  const CAP = "You can keep at most 25 saved searches. Delete one to save another.";

  async function refused() {
    const { wire, model } = await loaded(
      [],
      Array.from({ length: 25 }, (_, i) => rawSearchRow(`role ${i}`, null, `search-${i}`)),
    );
    fill(model, { query: "the twenty-sixth" });
    const saving = model.saveCurrentSearch();
    wire.fail(wire.made("POST", SEARCHES)[0], new FakeApiError(409, CAP, "CONFLICT"));
    await saving;
    expect(model.getState().saveSearchError).toBe(CAP);
    return { wire, model };
  }

  it("is cleared when a saved search is deleted", async () => {
    const { wire, model } = await refused();
    const deleting = model.deleteSearch(model.getState().searches[0]);
    wire.answer(wire.made("DELETE", `${SEARCHES}/search-0`)[0], undefined);
    await deleting;
    expect(model.getState().saveSearchError).toBeNull();
    expect(model.getState().searches).toHaveLength(24);
  });

  it("stays when the delete itself fails (nothing has changed)", async () => {
    const { wire, model } = await refused();
    const deleting = model.deleteSearch(model.getState().searches[0]);
    wire.fail(wire.made("DELETE", `${SEARCHES}/search-0`)[0], new FakeApiError(500, "down"));
    await deleting;
    expect(model.getState().saveSearchError).toBe(CAP);
  });

  it("is cleared when the role is edited", async () => {
    const { model } = await refused();
    model.setQuery("the twenty-sixth, reworded");
    expect(model.getState().saveSearchError).toBeNull();
  });

  it("is cleared when the location is edited", async () => {
    const { model } = await refused();
    model.setLocation("Pune");
    expect(model.getState().saveSearchError).toBeNull();
  });

  it("is cleared when a search is run", async () => {
    const { wire, model } = await refused();
    const searching = model.search();
    expect(model.getState().saveSearchError).toBeNull();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([]));
    await searching;
  });

  it("is cleared when a saved search is run", async () => {
    const { wire, model } = await refused();
    const running = model.runSavedSearch(model.getState().searches[3]);
    expect(model.getState().saveSearchError).toBeNull();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([]));
    await running;
  });

  it("is not cleared by changing the window or the wording, which it is not about", async () => {
    const { model } = await refused();
    model.setFreshness("week");
    model.setLocale("india");
    expect(model.getState().saveSearchError).toBe(CAP);
  });

  it("a server-side refusal of the role (INVALID_INPUT) is cleared by the next edit too", async () => {
    const { wire, model } = await loaded();
    fill(model, { query: "!!" });
    const saving = model.saveCurrentSearch();
    wire.fail(wire.made("POST", SEARCHES)[0], new FakeApiError(422, "Type a role.", "INVALID_INPUT"));
    await saving;
    expect(model.getState().saveSearchError).toBe("Type a role.");
    model.setQuery("designer");
    expect(model.getState().saveSearchError).toBeNull();
  });
});

describe("the guards the model keeps around removing a saved post and editing the form", () => {
  it("a saves list requested BEFORE a remove finished cannot bring the removed post back", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1"), rawSave(ID_2, "save-2")]);
    const refresh = model.loadSaves(); // GET, in flight, and it still lists save-1
    const removing = model.removeSaved(model.getState().saves[0], savedCardKey("save-1"));
    wire.answer(wire.made("DELETE", `${SAVES}/save-1`)[0], undefined);
    await removing;
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-2"]);

    // the older list finally answers, and it does not know about the removal
    wire.answer(wire.made("GET", SAVES)[1], { saves: [rawSave(ID, "save-1"), rawSave(ID_2, "save-2")] });
    await refresh;
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-2"]); // not resurrected
    expect(wire.made("GET", SAVES)).toHaveLength(3); // a fresh one was asked for instead
  });

  it("editing the location clears the location error and leaves a role error alone", () => {
    const { model } = setup();
    model.setQuery("");
    void model.search(); // an empty role: a query error
    expect(model.getState().fieldErrors.query).toBe(QUERY_REQUIRED_MESSAGE);
    model.setLocation("Pune");
    expect(model.getState().fieldErrors.query).toBe(QUERY_REQUIRED_MESSAGE); // still there
    expect(model.getState().fieldErrors.location).toBeNull();
  });

  it("a widen launched after an invalid re-press carries no stale field error and restores the boxes", async () => {
    // a result is on screen; the person empties the role box and presses Search
    // (invalid: the error shows), then presses "Search the last 7 days" instead --
    // that re-runs the search the RESULTS came from, so the boxes and the error
    // must describe that search, not the abandoned edit.
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const first = model.search();
    wire.answer(wire.made("POST", SEARCH)[0], searchBody([]));
    await first;
    model.setQuery("");
    void model.search();
    expect(model.getState().fieldErrors.query).toBe(QUERY_REQUIRED_MESSAGE);
    expect(wire.made("POST", SEARCH)).toHaveLength(1); // the invalid press sent nothing

    const widening = model.widen("week");
    const state = model.getState();
    expect(state.fieldErrors).toEqual({ query: null, location: null });
    expect(state.form.query).toBe("designer");
    wire.answer(wire.made("POST", SEARCH)[1], searchBody([]));
    await widening;
  });

  it("a retry launched after a failure and an invalid re-press carries no stale field error either", async () => {
    const { wire, model } = setup();
    fill(model, { query: "designer" });
    const first = model.search();
    wire.fail(wire.made("POST", SEARCH)[0], new FakeApiError(503, "down", "PROVIDER_UNAVAILABLE", true));
    await first;
    expect(model.getState().search.kind).toBe("failed");
    model.setQuery("");
    void model.search();
    expect(model.getState().fieldErrors.query).toBe(QUERY_REQUIRED_MESSAGE);

    const retrying = model.retry();
    expect(model.getState().fieldErrors).toEqual({ query: null, location: null });
    wire.answer(wire.made("POST", SEARCH)[1], searchBody([]));
    await retrying;
  });
});

describe("the Save button's id is the card's own", () => {
  it("is the id the per-application panel model builds, not a second spelling of it", () => {
    expect(tabSaveButtonId(ID)).toBe(saveButtonId(ID));
    expect(tabFocusTargetId({ kind: "save_button", activityId: ID })).toBe(saveButtonId(ID));
  });
});
