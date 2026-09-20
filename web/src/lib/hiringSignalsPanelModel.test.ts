import { describe, expect, it } from "vitest";
import { savedCardKey, searchCardKey } from "./hiringSignals";
import type { Fetcher } from "./hiringSignalsClient";
import {
  HiringPanelModel,
  SAVED_HEADING_ID,
  SEARCH_BUTTON_ID,
  focusTargetId,
  initialPanelState,
  saveButtonId,
} from "./hiringSignalsPanelModel";
import type { HiringSignal } from "./hiringSignalsTypes";

// The panel's state machine, driven against a fetcher the TEST controls: a
// request stays pending until the test resolves it, so a test can fire a
// second click while the first is in flight, or let a stale reply arrive after
// a newer result. Each of these is a guard that lived in the component with
// nothing testing it; a mutation run found six of them deletable with the old
// suite green. (Ids, names and urls are synthetic; nothing is fetched.)

const APP = "6b1f0c1e-0000-4000-8000-0000000000aa";
const ID = "7000000000000000001";
const ID_2 = "7000000000000000002";
const LABEL = "Acme -- software engineer -- last 7 days";

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
  settled: boolean;
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
        settled: false,
      });
    });

  // The calls made so far with this method (and, optionally, a path ending).
  made(method: string, pathEnd = ""): PendingCall[] {
    return this.calls.filter((c) => c.method === method && c.path.endsWith(pathEnd));
  }

  answer(call: PendingCall, value: unknown): void {
    call.settled = true;
    call.resolve(value);
  }

  fail(call: PendingCall, error: unknown): void {
    call.settled = true;
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
    role_match: null,
    registry_match: null,
    saved: false,
    ...overrides,
  };
}

function searchBody(signals: Record<string, unknown>[] = [rawSignal()]): Record<string, unknown> {
  return {
    provider: "firecrawl",
    cached: false,
    freshness: "week",
    query_label: LABEL,
    signals,
    counts: {
      raw_hits: signals.length,
      rejected: 0,
      duplicates: 0,
      off_topic_hidden: 0,
      echoes_hidden: 0,
      job_seekers_hidden: 0,
      too_old_hidden: 0,
      shown: signals.length,
    },
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

function signalOf(id = ID): HiringSignal {
  return {
    activity_id: id,
    post_url: "",
    embed_url: "",
    author_name: null,
    posted_at: null,
    age_hint: null,
    species: "unclassified",
    comment_count: null,
    role_match: null,
    registry_match: null,
    saved: false,
  };
}

function setup() {
  const wire = new Wire();
  const model = new HiringPanelModel(wire.fetcher, APP);
  return { wire, model };
}

// A model whose first saved-posts request has been answered.
async function loaded(saves: Record<string, unknown>[] = []) {
  const { wire, model } = setup();
  const first = model.loadSaves();
  wire.answer(wire.made("GET")[0], { saves });
  await first;
  return { wire, model };
}

describe("the initial state and subscriptions", () => {
  it("starts idle, with the default window, waiting for the first saved-posts reply", () => {
    const { model } = setup();
    const state = model.getState();
    expect(state).toEqual(initialPanelState());
    expect(state.freshness).toBe("week");
    expect(state.search).toEqual({ kind: "idle" });
    expect(state.savesStatus).toBe("loading");
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
    expect(before.freshness).toBe("week"); // snapshots are immutable
    unsubscribe();
    model.setFreshness("3days");
    expect(notified).toBe(1);
  });

  it("toggles an embed open and closed", () => {
    const { model } = setup();
    model.toggleEmbed("search:1");
    expect(model.getState().openKeys.has("search:1")).toBe(true);
    model.toggleEmbed("search:1");
    expect(model.getState().openKeys.has("search:1")).toBe(false);
  });
});

describe("the freshness window", () => {
  it("changes when chosen", () => {
    const { model } = setup();
    model.setFreshness("day");
    expect(model.getState().freshness).toBe("day");
    model.setFreshness("3days");
    expect(model.getState().freshness).toBe("3days");
  });

  it("cannot be changed while a search is running -- the pills stay focusable, so the handler is the guard", async () => {
    const { wire, model } = await loaded();
    model.setFreshness("day");
    const search = model.runSearch();
    expect(model.getState().search.kind).toBe("loading");
    model.setFreshness("3days");
    expect(model.getState().freshness).toBe("day");
    wire.answer(wire.made("POST", "/search")[0], searchBody());
    await search;
    model.setFreshness("3days");
    expect(model.getState().freshness).toBe("3days");
  });
});

describe("searching", () => {
  it("sends the chosen window, then shows the parsed results", async () => {
    const { wire, model } = await loaded();
    model.setFreshness("3days");
    const search = model.runSearch();
    const [call] = wire.made("POST", "/search");
    expect(call.path).toBe(`/applications/${APP}/hiring-signals/search`);
    expect(call.body).toEqual({ freshness: "3days" });
    expect(model.getState().search).toEqual({ kind: "loading" });
    wire.answer(call, searchBody());
    await search;
    const state = model.getState();
    expect(state.search.kind).toBe("ready");
    if (state.search.kind === "ready") {
      expect(state.search.outcome.response.signals.map((s) => s.activity_id)).toEqual([ID]);
    }
  });

  it("sends ONE request when asked twice in the same tick (F3: the in-flight guard)", async () => {
    const { wire, model } = await loaded();
    const first = model.runSearch();
    const second = model.runSearch();
    const third = model.runSearch("day");
    expect(wire.made("POST", "/search")).toHaveLength(1);
    // the ignored calls change nothing either: still the first call's window
    expect(model.getState().freshness).toBe("week");
    wire.answer(wire.made("POST", "/search")[0], searchBody());
    await Promise.all([first, second, third]);
    expect(wire.made("POST", "/search")).toHaveLength(1);
  });

  it("can search again once the last search has finished", async () => {
    const { wire, model } = await loaded();
    const first = model.runSearch();
    wire.answer(wire.made("POST", "/search")[0], searchBody());
    await first;
    const second = model.runSearch("day");
    expect(wire.made("POST", "/search")).toHaveLength(2);
    expect(wire.made("POST", "/search")[1].body).toEqual({ freshness: "day" });
    wire.answer(wire.made("POST", "/search")[1], searchBody([]));
    await second;
  });

  it("refreshes the saved list after a successful search (F3)", async () => {
    const { wire, model } = await loaded();
    expect(wire.made("GET")).toHaveLength(1);
    const search = model.runSearch();
    wire.answer(wire.made("POST", "/search")[0], searchBody());
    await search;
    await settle();
    expect(wire.made("GET")).toHaveLength(2);
    expect(wire.made("GET")[1].path).toBe(`/applications/${APP}/hiring-signals/saves`);
  });

  it("does not refresh the saved list after a failed search", async () => {
    const { wire, model } = await loaded();
    const search = model.runSearch();
    wire.fail(wire.made("POST", "/search")[0], new FakeApiError(503, "Busy.", "PROVIDER_UNAVAILABLE", true));
    await search;
    await settle();
    expect(wire.made("GET")).toHaveLength(1);
  });

  it("closes the previous results' embeds, errors and notice -- but not the saved list's embeds", async () => {
    const { wire, model } = await loaded([rawSave()]);
    model.toggleEmbed(searchCardKey(ID));
    model.toggleEmbed(savedCardKey("save-1"));
    const search = model.runSearch();
    const state = model.getState();
    expect([...state.openKeys]).toEqual([savedCardKey("save-1")]);
    expect(state.cardErrors).toEqual({});
    expect(state.notice).toBeNull();
    wire.answer(wire.made("POST", "/search")[0], searchBody());
    await search;
  });

  it("shows a failure with the window it failed on, and can be retried (the guard is released)", async () => {
    const { wire, model } = await loaded();
    model.setFreshness("day");
    const search = model.runSearch();
    wire.fail(
      wire.made("POST", "/search")[0],
      new FakeApiError(503, "Your search provider could not be reached.", "PROVIDER_UNAVAILABLE", true),
    );
    await search;
    expect(model.getState().search).toEqual({
      kind: "failed",
      failure: {
        kind: "error",
        message: "Your search provider could not be reached.",
        retryable: true,
      },
      freshness: "day",
    });
    const retry = model.runSearch("day");
    expect(wire.made("POST", "/search")).toHaveLength(2);
    wire.answer(wire.made("POST", "/search")[1], searchBody());
    await retry;
    expect(model.getState().search.kind).toBe("ready");
  });

  it("carries a setup-required failure through, for the panel to link to Integrations", async () => {
    const { wire, model } = await loaded();
    const search = model.runSearch();
    wire.fail(
      wire.made("POST", "/search")[0],
      new FakeApiError(409, "Hiring signals need a search provider.", "SETUP_REQUIRED"),
    );
    await search;
    const state = model.getState();
    expect(state.search.kind).toBe("failed");
    if (state.search.kind === "failed") {
      expect(state.search.failure.kind).toBe("setup_required");
    }
  });
});

describe("a switched-off feature (F3: FEATURE_DISABLED is one-way)", () => {
  it("from the first saved-posts request", async () => {
    const { wire, model } = setup();
    const load = model.loadSaves();
    wire.fail(wire.made("GET")[0], new FakeApiError(404, "off", "FEATURE_DISABLED"));
    await load;
    expect(model.getState().disabled).toBe(true);
  });

  it("from a search, a save or a remove -- and it stays off", async () => {
    for (const trigger of ["search", "save", "remove"] as const) {
      const { wire, model } = await loaded([rawSave(ID_2, "save-2")]);
      const disabled = new FakeApiError(404, "off", "FEATURE_DISABLED");
      if (trigger === "search") {
        const run = model.runSearch();
        wire.fail(wire.made("POST", "/search")[0], disabled);
        await run;
      } else if (trigger === "save") {
        const run = model.saveSignal(signalOf(), LABEL);
        wire.fail(wire.made("POST", "/saves")[0], disabled);
        await run;
      } else {
        const run = model.removeSaved(
          { id: "save-2", activity_id: ID_2, post_url: "", embed_url: "", created_at: "" },
          savedCardKey("save-2"),
        );
        wire.fail(wire.made("DELETE")[0], disabled);
        await run;
      }
      expect(model.getState().disabled, trigger).toBe(true);
      // a later successful-looking event cannot bring it back
      model.setFreshness("day");
      model.toggleEmbed("x");
      expect(model.getState().disabled, trigger).toBe(true);
    }
  });
});

describe("saving a post", () => {
  it("sends the numeric id and the label of the search -- and nothing else (F3)", async () => {
    const { wire, model } = await loaded();
    const save = model.saveSignal(signalOf(), LABEL);
    const [call] = wire.made("POST", "/saves");
    expect(call.path).toBe(`/applications/${APP}/hiring-signals/saves`);
    expect(call.body).toEqual({ activity_id: ID, query_label: LABEL });
    wire.answer(call, rawSave());
    await save;
  });

  it("sends ONE request when the same post is saved twice in the same tick (F3: the saving guard)", async () => {
    const { wire, model } = await loaded();
    const first = model.saveSignal(signalOf(), LABEL);
    const second = model.saveSignal(signalOf(), LABEL);
    expect(wire.made("POST", "/saves")).toHaveLength(1);
    expect(model.getState().savingIds.has(ID)).toBe(true);
    wire.answer(wire.made("POST", "/saves")[0], rawSave());
    await Promise.all([first, second]);
    expect(wire.made("POST", "/saves")).toHaveLength(1);
    expect(model.getState().savingIds.size).toBe(0);
  });

  it("can save two DIFFERENT posts at once, each with its own busy flag", async () => {
    const { wire, model } = await loaded();
    const a = model.saveSignal(signalOf(ID), LABEL);
    const b = model.saveSignal(signalOf(ID_2), LABEL);
    expect(wire.made("POST", "/saves")).toHaveLength(2);
    expect([...model.getState().savingIds].sort()).toEqual([ID, ID_2]);
    // finishing the first must not clear the second's flag
    wire.answer(wire.made("POST", "/saves")[0], rawSave(ID, "save-1"));
    await a;
    expect([...model.getState().savingIds]).toEqual([ID_2]);
    wire.answer(wire.made("POST", "/saves")[1], rawSave(ID_2, "save-2"));
    await b;
    expect(model.getState().savingIds.size).toBe(0);
    expect(model.getState().saves.map((s) => s.id).sort()).toEqual(["save-1", "save-2"]);
  });

  it("does not save a post that is already saved", async () => {
    const { wire, model } = await loaded([rawSave()]);
    await model.saveSignal(signalOf(), LABEL);
    expect(wire.made("POST", "/saves")).toHaveLength(0);
  });

  it("adds the saved post on top and announces it", async () => {
    const { wire, model } = await loaded([rawSave(ID_2, "save-2")]);
    const save = model.saveSignal(signalOf(ID), LABEL);
    wire.answer(wire.made("POST", "/saves")[0], rawSave(ID, "save-1"));
    await save;
    const state = model.getState();
    expect(state.saves.map((s) => s.id)).toEqual(["save-1", "save-2"]);
    expect(state.notice?.text).toBe("Saved to this application.");
  });

  it("gives an identical announcement a new id each time, so a live region announces it again (F4)", async () => {
    const { wire, model } = await loaded();
    const first = model.saveSignal(signalOf(ID), LABEL);
    wire.answer(wire.made("POST", "/saves")[0], rawSave(ID, "save-1"));
    await first;
    const firstNotice = model.getState().notice;
    const second = model.saveSignal(signalOf(ID_2), LABEL);
    // starting a new action clears the old notice ...
    expect(model.getState().notice).toBeNull();
    wire.answer(wire.made("POST", "/saves")[1], rawSave(ID_2, "save-2"));
    await second;
    const secondNotice = model.getState().notice;
    // ... and the same words come back under a different id
    expect(secondNotice?.text).toBe(firstNotice?.text);
    expect(secondNotice?.id).not.toBe(firstNotice?.id);
  });

  it("shows a failure under the card it belongs to, and can be tried again", async () => {
    const { wire, model } = await loaded();
    const save = model.saveSignal(signalOf(), LABEL);
    wire.fail(
      wire.made("POST", "/saves")[0],
      new FakeApiError(503, "Could not save that post.", "PROVIDER_UNAVAILABLE", true),
    );
    await save;
    expect(model.getState().cardErrors).toEqual({ [searchCardKey(ID)]: "Could not save that post." });
    expect(model.getState().savingIds.size).toBe(0);
    const again = model.saveSignal(signalOf(), LABEL);
    expect(model.getState().cardErrors).toEqual({}); // cleared as the retry starts
    wire.answer(wire.made("POST", "/saves")[1], rawSave());
    await again;
  });
});

describe("the saved list versus a save or remove that lands while it loads (F3: the epoch guard)", () => {
  it("discards a snapshot older than a save that finished meanwhile, and takes a fresh one", async () => {
    const { wire, model } = setup();
    const load = model.loadSaves(); // request 1: the list is EMPTY as of now
    const staleRequest = wire.made("GET")[0];

    // while it is in flight, a post is saved
    const save = model.saveSignal(signalOf(), LABEL);
    wire.answer(wire.made("POST", "/saves")[0], rawSave());
    await save;
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-1"]);

    // now the stale (empty) list arrives: it must NOT overwrite the save
    wire.answer(staleRequest, { saves: [] });
    await load;
    await settle();
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-1"]);

    // the model took a fresh snapshot instead of dropping the reply, so the
    // list cannot stay stuck on "loading" behind a discarded one
    expect(wire.made("GET")).toHaveLength(2);
    wire.answer(wire.made("GET")[1], { saves: [rawSave()] });
    await settle();
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-1"]);
    expect(model.getState().savesStatus).toBe("ready");
  });

  it("does the same for a remove that finished meanwhile", async () => {
    const { wire, model } = await loaded([rawSave()]);
    const load = model.loadSaves(); // request 2: still lists save-1
    const staleRequest = wire.made("GET")[1];

    const remove = model.removeSaved(
      { id: "save-1", activity_id: ID, post_url: "", embed_url: "", created_at: "" },
      savedCardKey("save-1"),
    );
    wire.answer(wire.made("DELETE")[0], undefined);
    await remove;
    expect(model.getState().saves).toEqual([]);

    wire.answer(staleRequest, { saves: [rawSave()] }); // stale: it resurrects save-1
    await load;
    await settle();
    expect(model.getState().saves).toEqual([]);
    wire.answer(wire.made("GET")[2], { saves: [] });
    await settle();
    expect(model.getState().saves).toEqual([]);
  });

  it("applies a snapshot when nothing landed in between", async () => {
    const { wire, model } = setup();
    const load = model.loadSaves();
    wire.answer(wire.made("GET")[0], { saves: [rawSave()] });
    await load;
    expect(wire.made("GET")).toHaveLength(1);
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-1"]);
    expect(model.getState().savesStatus).toBe("ready");
  });

  it("reports a failed first load, and recovers on a retry", async () => {
    const { wire, model } = setup();
    const load = model.loadSaves();
    wire.fail(wire.made("GET")[0], new FakeApiError(500, "Boom.", "INTERNAL_ERROR", false));
    await load;
    expect(model.getState().savesStatus).toBe("error");
    expect(model.getState().savesError).toBe("Boom.");
    const retry = model.loadSaves();
    wire.answer(wire.made("GET")[1], { saves: [rawSave()] });
    await retry;
    expect(model.getState().savesStatus).toBe("ready");
    expect(model.getState().savesError).toBeNull();
  });
});

describe("removing a saved post", () => {
  const SAVE = { id: "save-1", activity_id: ID, post_url: "", embed_url: "", created_at: "" };
  const OTHER = { id: "save-2", activity_id: ID_2, post_url: "", embed_url: "", created_at: "" };

  it("deletes by the row id, once, however many times it is pressed", async () => {
    const { wire, model } = await loaded([rawSave()]);
    const first = model.removeSaved(SAVE, savedCardKey("save-1"));
    const second = model.removeSaved(SAVE, savedCardKey("save-1"));
    expect(wire.made("DELETE")).toHaveLength(1);
    expect(wire.made("DELETE")[0].path).toBe("/hiring-signals/saves/save-1");
    expect(model.getState().removingIds.has("save-1")).toBe(true);
    wire.answer(wire.made("DELETE")[0], undefined);
    await Promise.all([first, second]);
    expect(model.getState().removingIds.size).toBe(0);
    expect(wire.made("DELETE")).toHaveLength(1);
  });

  it("drops the post from the list and announces it", async () => {
    const { wire, model } = await loaded([rawSave(ID, "save-1"), rawSave(ID_2, "save-2")]);
    const remove = model.removeSaved(SAVE, savedCardKey("save-1"));
    wire.answer(wire.made("DELETE")[0], undefined);
    await remove;
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-2"]);
    expect(model.getState().notice?.text).toBe("Removed from saved posts.");
  });

  it("treats 'already gone' as done -- without announcing something this click did not do", async () => {
    const { wire, model } = await loaded([rawSave()]);
    const remove = model.removeSaved(SAVE, savedCardKey("save-1"));
    wire.fail(wire.made("DELETE")[0], new FakeApiError(404, "no save found", "NOT_FOUND"));
    await remove;
    expect(model.getState().saves).toEqual([]);
    expect(model.getState().notice).toBeNull();
  });

  it("keeps the post and shows the error when the removal fails", async () => {
    const { wire, model } = await loaded([rawSave()]);
    const remove = model.removeSaved(SAVE, savedCardKey("save-1"));
    wire.fail(wire.made("DELETE")[0], new FakeApiError(503, "Try later.", "PROVIDER_UNAVAILABLE", true));
    await remove;
    expect(model.getState().saves.map((s) => s.id)).toEqual(["save-1"]);
    expect(model.getState().cardErrors).toEqual({ [savedCardKey("save-1")]: "Try later." });
    expect(model.getState().focusRequest).toBeNull();
  });

  describe("where focus goes when the removed post's button disappears (F4)", () => {
    it("back to that result card's own Save button, after an Unsave from the results", async () => {
      const { wire, model } = await loaded([rawSave()]);
      const remove = model.removeSaved(SAVE, searchCardKey(ID));
      wire.answer(wire.made("DELETE")[0], undefined);
      await remove;
      const request = model.getState().focusRequest;
      expect(request?.target).toEqual({ kind: "save_button", activityId: ID });
      expect(focusTargetId(request!.target)).toBe(saveButtonId(ID));
    });

    it("to the saved list's heading, after a Remove from a list that still has posts", async () => {
      const { wire, model } = await loaded([rawSave(ID, "save-1"), rawSave(ID_2, "save-2")]);
      const remove = model.removeSaved(SAVE, savedCardKey("save-1"));
      wire.answer(wire.made("DELETE")[0], undefined);
      await remove;
      const request = model.getState().focusRequest;
      expect(request?.target).toEqual({ kind: "saved_heading" });
      expect(focusTargetId(request!.target)).toBe(SAVED_HEADING_ID);
    });

    it("to the search button, after the last saved post is removed (the list is gone)", async () => {
      const { wire, model } = await loaded([rawSave(ID_2, "save-2")]);
      const remove = model.removeSaved(OTHER, savedCardKey("save-2"));
      wire.answer(wire.made("DELETE")[0], undefined);
      await remove;
      const request = model.getState().focusRequest;
      expect(request?.target).toEqual({ kind: "search_button" });
      expect(focusTargetId(request!.target)).toBe(SEARCH_BUTTON_ID);
    });

    it("is asked for once: consuming it clears it, and a stale id changes nothing", async () => {
      const { wire, model } = await loaded([rawSave()]);
      const remove = model.removeSaved(SAVE, savedCardKey("save-1"));
      wire.answer(wire.made("DELETE")[0], undefined);
      await remove;
      const request = model.getState().focusRequest!;
      model.consumeFocusRequest(request.id + 99);
      expect(model.getState().focusRequest).not.toBeNull();
      model.consumeFocusRequest(request.id);
      expect(model.getState().focusRequest).toBeNull();
    });
  });
});
