import { describe, expect, it } from "vitest";
import {
  EMBED_URL_PREFIX,
  savedCardKey,
  searchCardKey,
} from "../lib/hiringSignals";
import {
  SAVED_HEADING_ID,
  SEARCH_BUTTON_ID,
  type PanelState,
  initialPanelState,
} from "../lib/hiringSignalsPanelModel";
import type {
  Freshness,
  HiringSignal,
  SavedPost,
  SearchOutcome,
  SignalCounts,
} from "../lib/hiringSignalsTypes";
import {
  type HostElement,
  expand,
  findAll,
  isHost,
  onlyButton,
  press,
  prop,
  textOf,
} from "../testing/reactTree";
import { HiringSignalsPanelView, type PanelActions } from "./HiringSignalsPanelView";

// What each state of the panel renders, and what pressing each control calls --
// without a browser. The view is a pure function of its props, so the test
// CALLS it and walks the elements it returns (see testing/reactTree.ts). Each
// test here pins something a mutation run showed could be deleted with the old
// suite still green: the freshness pills doing anything, the busy states, the
// switched-off feature rendering nothing. (Ids, names and urls are synthetic.)

const ID = "7000000000000000001";
const NOW = new Date("2026-09-19T12:00:00Z");
const QUERY_LABEL = "Acme -- software engineer -- last 7 days";

function makeSignal(overrides: Partial<HiringSignal> = {}): HiringSignal {
  return {
    activity_id: ID,
    post_url: `https://www.linkedin.com/posts/example-${ID}`,
    embed_url: `${EMBED_URL_PREFIX}${ID}`,
    author_name: "Jane Example",
    posted_at: "2026-09-16T12:00:00Z",
    age_hint: null,
    species: "unclassified",
    comment_count: null,
    role_match: null,
    registry_match: null,
    saved: false,
    ...overrides,
  };
}

function makeSave(overrides: Partial<SavedPost> = {}): SavedPost {
  return {
    id: "save-1",
    activity_id: ID,
    post_url: `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`,
    embed_url: `${EMBED_URL_PREFIX}${ID}`,
    created_at: "2026-09-18T12:00:00Z",
    ...overrides,
  };
}

function makeCounts(overrides: Partial<SignalCounts> = {}): SignalCounts {
  return {
    raw_hits: 0,
    rejected: 0,
    duplicates: 0,
    off_topic_hidden: 0,
    echoes_hidden: 0,
    job_seekers_hidden: 0,
    too_old_hidden: 0,
    shown: 0,
    ...overrides,
  };
}

function makeOutcome(signals: HiringSignal[], freshness: Freshness = "week"): SearchOutcome {
  return {
    response: {
      provider: "firecrawl",
      cached: false,
      freshness,
      query_label: QUERY_LABEL,
      signals,
      counts: makeCounts({ raw_hits: signals.length, shown: signals.length }),
    },
    unreadable: 0,
  };
}

function ready(overrides: Partial<PanelState> = {}): PanelState {
  return initialPanelState({ savesStatus: "ready", ...overrides });
}

// Actions that record what was called, with what.
function recorder(): { actions: PanelActions; calls: [string, ...unknown[]][] } {
  const calls: [string, ...unknown[]][] = [];
  const record =
    (name: string) =>
    (...args: unknown[]) => {
      calls.push([name, ...args]);
    };
  return {
    calls,
    actions: {
      setFreshness: record("setFreshness"),
      search: record("search"),
      toggleEmbed: record("toggleEmbed"),
      save: record("save"),
      unsave: record("unsave"),
      reloadSaves: record("reloadSaves"),
    },
  };
}

function tree(state: PanelState, actions: PanelActions) {
  return expand(HiringSignalsPanelView({ state, now: NOW, actions }));
}

function pills(nodes: ReturnType<typeof tree>): HostElement[] {
  const group = findAll(nodes, (el) => prop(el, "role") === "group")[0];
  return findAll(group.children, (el) => el.type === "button");
}

describe("what renders nothing", () => {
  it("is nothing at all when the feature is switched off -- no heading, no button, no placeholder", () => {
    const { actions } = recorder();
    expect(HiringSignalsPanelView({ state: ready({ disabled: true }), now: NOW, actions })).toBeNull();
    // ... whatever else the state holds
    const busyAndSaved = ready({
      disabled: true,
      search: { kind: "loading" },
      saves: [makeSave()],
    });
    expect(HiringSignalsPanelView({ state: busyAndSaved, now: NOW, actions })).toBeNull();
  });

  it("is nothing until the first saved-posts reply, so a switched-off server never flashes it (F5)", () => {
    const { actions } = recorder();
    expect(
      HiringSignalsPanelView({ state: initialPanelState(), now: NOW, actions }),
    ).toBeNull();
  });

  it("shows once that first reply is a failure, so the person can still search", () => {
    const { actions } = recorder();
    const state = initialPanelState({ savesStatus: "error", savesError: "Could not load your saved posts." });
    expect(HiringSignalsPanelView({ state, now: NOW, actions })).not.toBeNull();
  });
});

describe("the freshness pills", () => {
  it("each choose their own window when pressed", () => {
    const { actions, calls } = recorder();
    const nodes = tree(ready(), actions);
    const labels = pills(nodes).map((el) => textOf(el));
    expect(labels).toEqual(["24 hours", "3 days", "7 days"]);
    for (const el of pills(nodes)) press(el);
    expect(calls).toEqual([
      ["setFreshness", "day"],
      ["setFreshness", "3days"],
      ["setFreshness", "week"],
    ]);
  });

  it("announce the chosen window with aria-pressed", () => {
    const { actions } = recorder();
    const nodes = tree(ready({ freshness: "3days" }), actions);
    expect(pills(nodes).map((el) => prop(el, "aria-pressed"))).toEqual([false, true, false]);
  });

  it("are marked aria-disabled while a search runs -- and never `disabled`, which would drop focus (F4)", () => {
    const { actions } = recorder();
    const busy = pills(tree(ready({ search: { kind: "loading" } }), actions));
    expect(busy.every((el) => prop(el, "aria-disabled") === true)).toBe(true);
    expect(busy.every((el) => prop(el, "disabled") === undefined)).toBe(true);
    const idle = pills(tree(ready(), actions));
    expect(idle.every((el) => prop(el, "aria-disabled") === undefined)).toBe(true);
  });
});

describe("the search button", () => {
  function searchButton(state: PanelState, actions: PanelActions): HostElement {
    return findAll(tree(state, actions), (el) => prop(el, "id") === SEARCH_BUTTON_ID)[0];
  }

  it("says what it will do: find, then search again", () => {
    const { actions } = recorder();
    expect(textOf(searchButton(ready(), actions))).toBe("Find hiring posts");
    expect(
      textOf(searchButton(ready({ search: { kind: "ready", outcome: makeOutcome([]) } }), actions)),
    ).toBe("Search again");
    expect(textOf(searchButton(ready({ search: { kind: "loading" } }), actions))).toBe(
      "Searching...",
    );
  });

  it("searches the chosen window when pressed", () => {
    const { actions, calls } = recorder();
    press(searchButton(ready({ freshness: "day" }), actions));
    expect(calls).toEqual([["search", "day"]]);
  });

  it("is aria-disabled, never `disabled`, while a search runs, and keeps its id for focus", () => {
    const { actions } = recorder();
    const busy = searchButton(ready({ search: { kind: "loading" } }), actions);
    expect(prop(busy, "aria-disabled")).toBe(true);
    expect(prop(busy, "disabled")).toBeUndefined();
    expect(prop(searchButton(ready(), actions), "aria-disabled")).toBeUndefined();
  });
});

describe("what pressing the search button costs", () => {
  // Spelled out in full on purpose: the words are the promise, and a refactor must not change them quietly.
  const SPEND =
    "Pressing Find hiring posts, Search again, Try again after a failed search, or a button that widens the window, makes a real search call on your own search key, then tries each other search key you have saved only if that one fails or finds no posts.";
  const CACHE = "A search repeated soon after is usually answered from a cache, which makes no new call.";
  const INTRO = "Recent public hiring posts about this company, found through the search provider you connected.";

  // The panel's first block is its intro; the controls row follows it directly.
  function introAndControls(state: PanelState): [HostElement, HostElement] {
    const { actions } = recorder();
    const root = tree(state, actions)[0];
    if (!isHost(root)) throw new Error("the panel rendered no element");
    const [intro, controls] = root.children;
    if (!isHost(intro) || !isHost(controls)) throw new Error("expected the intro and the controls row first");
    return [intro, controls];
  }

  it("says in the intro that Find hiring posts, Search again, Try again and the widen buttons make a search call on the person's own key and may try their other keys", () => {
    const [intro, controls] = introAndControls(ready());
    expect(intro.type).toBe("p");
    expect(textOf(intro).replace(/\s+/g, " ").trim()).toBe(`${INTRO} ${SPEND} ${CACHE}`);
    expect(findAll([controls], (el) => prop(el, "id") === SEARCH_BUTTON_ID)).toHaveLength(1);
  });

  it("calls it a search call, never a model call, and uses no dash and none of the banned words", () => {
    const [intro] = introAndControls(ready());
    const words = textOf(intro);
    expect(words).toContain("a real search call on your own search key");
    expect(words).not.toMatch(/model/i);
    expect(words).not.toMatch(/[\u2013\u2014]/);
    expect(words).not.toMatch(/verified|unknown model|linkedin/i);
  });

  it("keeps the same sentence above the button once a search has run, whose button then reads Search again", () => {
    const state = ready({ search: { kind: "ready", outcome: makeOutcome([makeSignal()]) } });
    const [intro, controls] = introAndControls(state);
    expect(textOf(intro)).toContain(SPEND);
    expect(textOf(intro)).toContain(CACHE);
    expect(onlyButton([controls], "Search again")).toBeDefined();
  });

  it("keeps it above Try again after a failed search and above the widen button on an empty answer", () => {
    const failed = ready({
      search: { kind: "failed", failure: { kind: "error", message: "Busy.", retryable: true }, freshness: "week" },
    });
    const [failedIntro] = introAndControls(failed);
    expect(textOf(failedIntro)).toContain(SPEND);
    expect(onlyButton(tree(failed, recorder().actions), "Try again")).toBeDefined();

    const empty = ready({ search: { kind: "ready", outcome: makeOutcome([], "day") } });
    const [emptyIntro] = introAndControls(empty);
    expect(textOf(emptyIntro)).toContain(SPEND);
    expect(onlyButton(tree(empty, recorder().actions), "Search the last 3 days")).toBeDefined();
  });

  it("does not add the sentence to the saved posts section's own Try again, which reads the saved list and searches nothing", () => {
    const state = ready({ savesStatus: "error", savesError: "Could not load saved posts." });
    const nodes = tree(state, recorder().actions);
    const sections = findAll(nodes, (el) => el.type === "section");
    expect(sections).toHaveLength(1);
    expect(textOf(sections[0])).not.toContain("Pressing");
  });
});

describe("the live region", () => {
  it("gives every notice its own key, so the same words twice in a row are announced twice (F4)", () => {
    const { actions } = recorder();
    const region = (state: PanelState) =>
      findAll(tree(state, actions), (el) => prop(el, "role") === "status")[0];
    const first = region(ready({ notice: { id: 1, text: "Saved to this application." } }));
    const second = region(ready({ notice: { id: 2, text: "Saved to this application." } }));
    const keyOf = (el: HostElement) => findAll(el.children, (c) => c.type === "span")[0].key;
    expect(textOf(first)).toContain("Saved to this application.");
    expect(keyOf(first)).toBe("1");
    expect(keyOf(second)).toBe("2");
  });

  it("holds no notice when there is none, and announces the result headline after a search", () => {
    const { actions } = recorder();
    const idle = findAll(tree(ready(), actions), (el) => prop(el, "role") === "status")[0];
    expect(textOf(idle)).toBe("");
    const done = findAll(
      tree(ready({ search: { kind: "ready", outcome: makeOutcome([makeSignal()]) } }), actions),
      (el) => prop(el, "role") === "status",
    )[0];
    expect(textOf(done)).toBe("1 post found");
  });
});

describe("wiring from the results", () => {
  const resultsState = (extra: Partial<PanelState> = {}) =>
    ready({ search: { kind: "ready", outcome: makeOutcome([makeSignal()]) }, ...extra });

  it("Save passes the post AND the label of the search that found it", () => {
    const { actions, calls } = recorder();
    press(onlyButton(tree(resultsState(), actions), "Save"));
    expect(calls).toEqual([["save", makeSignal(), QUERY_LABEL]]);
  });

  it("Unsave on a result card names the RESULT card, so focus can return to its Save button", () => {
    const { actions, calls } = recorder();
    const save = makeSave();
    press(onlyButton(tree(resultsState({ saves: [save] }), actions), "Unsave"));
    expect(calls).toEqual([["unsave", save, searchCardKey(ID)]]);
  });

  it("Remove on a saved card names the SAVED card", () => {
    const { actions, calls } = recorder();
    const save = makeSave({ id: "save-9", activity_id: "7000000000000000009" });
    press(onlyButton(tree(ready({ saves: [save] }), actions), "Remove"));
    expect(calls).toEqual([["unsave", save, savedCardKey("save-9")]]);
  });

  it("Show post toggles that card's embed, in its own namespace", () => {
    const { actions, calls } = recorder();
    press(onlyButton(tree(resultsState(), actions), "Show post"));
    expect(calls).toEqual([["toggleEmbed", searchCardKey(ID)]]);
  });

  it("the empty state's widen button searches the next wider window", () => {
    const { actions, calls } = recorder();
    const state = ready({ search: { kind: "ready", outcome: makeOutcome([], "day") } });
    press(onlyButton(tree(state, actions), "Search the last 3 days"));
    expect(calls).toEqual([["search", "3days"]]);
  });

  it("a failed search offers Try again for the same window it failed on", () => {
    const { actions, calls } = recorder();
    const state = ready({
      freshness: "week",
      search: {
        kind: "failed",
        failure: { kind: "error", message: "Busy.", retryable: true },
        freshness: "3days",
      },
    });
    press(onlyButton(tree(state, actions), "Try again"));
    expect(calls).toEqual([["search", "3days"]]);
  });
});

describe("the saved section", () => {
  it("has a heading focus can be sent to: an id, and focusable by script but not by Tab", () => {
    const { actions } = recorder();
    const heading = findAll(
      tree(ready({ saves: [makeSave()] }), actions),
      (el) => el.type === "h3",
    )[0];
    expect(prop(heading, "id")).toBe(SAVED_HEADING_ID);
    expect(prop(heading, "tabIndex")).toBe(-1);
  });

  it("is absent with nothing saved and no failure", () => {
    const { actions } = recorder();
    expect(findAll(tree(ready(), actions), (el) => el.type === "h3")).toEqual([]);
  });

  it("reloads the list from its own Try again after a failure", () => {
    const { actions, calls } = recorder();
    const state = ready({ savesStatus: "error", savesError: "Could not load your saved posts." });
    const nodes = tree(state, actions);
    expect(nodes.filter(isHost).length).toBeGreaterThan(0);
    press(onlyButton(nodes, "Try again"));
    expect(calls).toEqual([["reloadSaves"]]);
  });
});
