import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  AGGREGATOR_BADGE,
  AGGREGATOR_LEGEND,
  EMBED_URL_PREFIX,
  UNCLASSIFIED_LEGEND,
  savedCardKey,
  searchCardKey,
} from "../lib/hiringSignals";
import type { HiringSignal, SavedPost } from "../lib/hiringSignalsTypes";
import {
  ALREADY_SAVED_REASON,
  HINT_DROP_LOCATION,
  HINT_SHORTER_ROLE,
  INITIAL_FORM,
  LOOSE_ROLE_NOTE,
  LOCATION_TOO_LONG_MESSAGE,
  QUERY_REQUIRED_MESSAGE,
  SAVE_NEEDS_ROLE_REASON,
  TAB_NOT_FOUND_MESSAGE,
  TAB_NOTHING_SHOWN_NOTE,
  TAB_FORM_LABEL,
  TAB_LOCATION_HINT,
  TAB_NO_POSTS_SEEN_NOTE,
  TAB_TRUNCATED_NOTE,
  TAB_YOU_COM_NO_LINKEDIN_NOTE,
  type TabForm,
  type TabSearchRequest,
} from "../lib/hiringSignalsTab";
import {
  TAB_LOCATION_ERROR_ID,
  TAB_LOCATION_INPUT_ID,
  TAB_QUERY_ERROR_ID,
  TAB_QUERY_INPUT_ID,
  TAB_SAVED_POSTS_HEADING_ID,
  TAB_SAVED_SEARCHES_HEADING_ID,
  TAB_SAVE_SEARCH_BUTTON_ID,
  TAB_SAVE_SEARCH_REASON_ID,
  TAB_SEARCH_BUTTON_ID,
  type TabState,
  initialTabState,
  tabFocusTargetId,
} from "../lib/hiringSignalsTabModel";
import type {
  Freshness,
  SearchProvider,
} from "../lib/hiringSignalsTypes";
import type {
  SavedHiringSearch,
  TabCounts,
  TabSearchOutcome,
  TabSignal,
} from "../lib/hiringSignalsTabTypes";
import {
  type HostElement,
  expand,
  findAll,
  onlyButton,
  press,
  prop,
  textOf,
} from "../testing/reactTree";
import { SignalCard } from "./HiringPostCard";
import {
  HiringSignalsPageShell,
  HiringSignalsTabView,
  SwitchedOffNote,
  type TabActions,
} from "./HiringSignalsTabView";

// What each state of the "Hiring signals" tab renders, and what pressing each
// control calls -- without a browser. The view is a pure function of its props, so
// the test CALLS it and walks the elements it returns (see testing/reactTree.ts),
// and renders it to static markup for what a reader would see and what a browser
// might load. (Ids, names and urls are synthetic; nothing is fetched.)

const ID = "7000000000000000001";
const ID_2 = "7000000000000000002";
const ID_3 = "7000000000000000003";
const NOW = new Date("2026-09-19T12:00:00Z");
const QUERY_LABEL = "software engineer -- Pune -- last 3 days";

const REQUEST: TabSearchRequest = {
  query: "software engineer",
  location: "Pune",
  freshness: "3days",
  locale: null,
};

function makeSignal(overrides: Partial<TabSignal> = {}): TabSignal {
  return {
    activity_id: ID,
    post_url: `https://www.linkedin.com/posts/example-${ID}`,
    embed_url: `${EMBED_URL_PREFIX}${ID}`,
    author_name: "Jane Example",
    posted_at: "2026-09-18T12:00:00Z",
    age_hint: null,
    species: "unclassified",
    comment_count: null,
    registry_match: null,
    aggregator: false,
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

function makeSearch(overrides: Partial<SavedHiringSearch> = {}): SavedHiringSearch {
  return {
    id: "search-1",
    query: "software engineer",
    location: "Pune",
    created_at: "2026-09-18T12:00:00Z",
    ...overrides,
  };
}

function makeCounts(overrides: Partial<TabCounts> = {}): TabCounts {
  return {
    raw_hits: 0,
    rejected: 0,
    duplicates: 0,
    role_mismatch_hidden: 0,
    echoes_hidden: 0,
    job_seekers_hidden: 0,
    too_old_hidden: 0,
    shown: 0,
    ...overrides,
  };
}

function makeOutcome(
  signals: TabSignal[],
  extra: {
    freshness?: Freshness;
    provider?: SearchProvider | null;
    counts?: TabCounts | null;
    cached?: boolean;
    unreadable?: number;
    locale?: "india" | "global" | null;
  } = {},
): TabSearchOutcome {
  return {
    response: {
      provider: extra.provider === undefined ? "firecrawl" : extra.provider,
      cached: extra.cached ?? false,
      freshness: extra.freshness ?? "3days",
      locale: extra.locale === undefined ? "global" : extra.locale,
      query_label: QUERY_LABEL,
      signals,
      counts:
        extra.counts === undefined
          ? makeCounts({ raw_hits: signals.length, shown: signals.length })
          : extra.counts,
    },
    unreadable: extra.unreadable ?? 0,
  };
}

function ready(overrides: Partial<TabState> = {}): TabState {
  return initialTabState({ savesStatus: "ready", searchesStatus: "ready", ...overrides });
}

function withForm(form: Partial<TabForm>, overrides: Partial<TabState> = {}): TabState {
  return ready({ form: { ...INITIAL_FORM, ...form }, ...overrides });
}

function results(outcome: TabSearchOutcome, overrides: Partial<TabState> = {}): TabState {
  return ready({
    form: { ...INITIAL_FORM, query: "software engineer", location: "Pune" },
    search: { kind: "ready", outcome, request: REQUEST },
    ...overrides,
  });
}

// Actions that record what was called, with what.
function recorder(): { actions: TabActions; calls: [string, ...unknown[]][] } {
  const calls: [string, ...unknown[]][] = [];
  const record =
    (name: string) =>
    (...args: unknown[]) => {
      calls.push([name, ...args]);
    };
  return {
    calls,
    actions: {
      setQuery: record("setQuery"),
      setLocation: record("setLocation"),
      setFreshness: record("setFreshness"),
      setLocale: record("setLocale"),
      search: record("search"),
      saveSearch: record("saveSearch"),
      runSavedSearch: record("runSavedSearch"),
      deleteSearch: record("deleteSearch"),
      reloadSearches: record("reloadSearches"),
      widen: record("widen"),
      retry: record("retry"),
      toggleEmbed: record("toggleEmbed"),
      save: record("save"),
      unsave: record("unsave"),
      reloadSaves: record("reloadSaves"),
    },
  };
}

const NO_ACTIONS = recorder().actions;

function tree(state: TabState, actions: TabActions) {
  return expand(HiringSignalsTabView({ state, now: NOW, actions }));
}

function html(state: TabState, actions: TabActions = NO_ACTIONS): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <HiringSignalsTabView state={state} now={NOW} actions={actions} />
    </MemoryRouter>,
  );
}

// The words a reader would see: tags stripped, entities decoded.
function visibleText(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ");
}

function iframeCount(markup: string): number {
  return (markup.match(/<iframe/g) ?? []).length;
}

function byId(nodes: ReturnType<typeof tree>, id: string): HostElement {
  const found = findAll(nodes, (el) => prop(el, "id") === id);
  if (found.length !== 1) throw new Error(`expected exactly one #${id}, found ${found.length}`);
  return found[0];
}

function maybeById(nodes: ReturnType<typeof tree>, id: string): HostElement | undefined {
  return findAll(nodes, (el) => prop(el, "id") === id)[0];
}

function change(el: HostElement, value: string): void {
  (el.props.onChange as (event: unknown) => void)({ target: { value } });
}

function submit(form: HostElement): void {
  (form.props.onSubmit as (event: unknown) => void)({ preventDefault() {} });
}

function pills(nodes: ReturnType<typeof tree>): HostElement[] {
  const group = findAll(nodes, (el) => prop(el, "role") === "group")[0];
  return findAll(group.children, (el) => el.type === "button");
}

function liveRegion(nodes: ReturnType<typeof tree>): HostElement {
  return findAll(nodes, (el) => prop(el, "role") === "status")[0];
}

describe("the idle tab", () => {
  const idle = html(ready());

  it("explains itself in one line, naming the user's own provider, and does not call every result a hiring post", () => {
    const text = visibleText(idle);
    expect(text).toContain(
      "Recent public posts that mention a role, found through the search provider you connected. Some are hiring calls and others are not.",
    );
    expect(text).not.toContain("hiring posts for a role");
  });

  it("says the location is a search word: only its first comma-separated part, and never checked in a post", () => {
    const text = visibleText(idle);
    expect(text).toContain(TAB_LOCATION_HINT);
    expect(TAB_LOCATION_HINT).toContain("before the first comma");
    expect(TAB_LOCATION_HINT).toContain("no post is checked against it");
  });

  it("never uses the source site's name or marks in anything a person can see or a browser can load", () => {
    expect(idle).not.toMatch(/linkedin/i);
    expect(idle).not.toMatch(/<img|<svg|<iframe|<a /i);
    // ... and never the product name it must not borrow.
    expect(idle).not.toMatch(/LinkedIn Hiring Posts/i);
  });

  it("offers a role box (required, 200 max) and an optional location box (100 max), each labelled", () => {
    expect(idle).toMatch(/<label[^>]*><span>Role<\/span><input[^>]*id="hst-query"[^>]*>/);
    expect(idle).toContain('maxLength="200"');
    expect(idle).toContain('aria-required="true"');
    expect(idle).toMatch(/<label[^>]*><span>Location \(optional\)<\/span><input[^>]*id="hst-location"[^>]*>/);
    expect(idle).toContain('maxLength="100"');
  });

  it("offers 24 hours, 3 days and 7 days as a labelled group, defaulting to 3 days", () => {
    expect(idle).toContain('role="group" aria-label="How recent"');
    expect(idle).toMatch(/aria-pressed="false"[^>]*>24 hours</);
    expect(idle).toMatch(/class="bj-toggle-active"[^>]*aria-pressed="true"[^>]*>3 days</);
    expect(idle).toMatch(/aria-pressed="false"[^>]*>7 days</);
  });

  it("offers Auto, Global and India wording, defaulting to Auto", () => {
    expect(idle).toMatch(/<select[^>]*>/);
    const options = [...idle.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)];
    expect(options.map((m) => m[1])).toEqual(["auto", "global", "india"]);
    expect(options.map((m) => m[2])).toEqual(["Auto (from the location)", "Global", "India"]);
    expect(idle).toMatch(/<option value="auto" selected/);
  });

  it("has exactly one primary action, Search, and it is enabled in both senses", () => {
    expect((idle.match(/class="bj-primary"/g) ?? []).length).toBe(1);
    expect(idle).toMatch(/<button[^>]*type="submit"[^>]*class="bj-primary"[^>]*>Search<\/button>/);
    expect(idle).not.toMatch(/<button[^>]*bj-primary[^>]*(disabled|aria-disabled)/);
  });

  it("has an always-mounted polite live region, so later results are announced", () => {
    expect(idle).toMatch(/role="status" aria-live="polite"/);
  });

  it("says that searches run only when asked, and that a saved search keeps just a role and location", () => {
    const text = visibleText(idle);
    expect(text).toContain("Searches run only when you ask.");
    expect(text).toContain("A saved search keeps just the role and location");
    expect(text).toContain("it does not run or watch anything");
  });

  it("shows no results, no saved lists and no embed before anything has happened", () => {
    expect(idle).not.toContain("<li");
    expect(idle).not.toContain("bj-error");
    expect(idle).not.toContain("Saved posts");
    expect(idle).not.toContain("Saved searches");
    expect(idle).not.toContain("Searching");
    expect(idle).not.toContain("aria-label=\"Results\"");
  });

  it("never invents an 'actively hiring' claim or a connection badge", () => {
    expect(idle).not.toMatch(/actively hiring|1st|2nd|3rd\+|connection|mutual/i);
  });
});

describe("the search form", () => {
  it("passes each box's text to its own handler as it is typed", () => {
    const { actions, calls } = recorder();
    const nodes = tree(ready(), actions);
    change(byId(nodes, TAB_QUERY_INPUT_ID), "data scientist");
    change(byId(nodes, TAB_LOCATION_INPUT_ID), "Pune");
    expect(calls).toEqual([
      ["setQuery", "data scientist"],
      ["setLocation", "Pune"],
    ]);
  });

  it("shows what the model holds in the boxes (controlled inputs)", () => {
    const { actions } = recorder();
    const nodes = tree(withForm({ query: "designer", location: "Delhi" }), actions);
    expect(prop(byId(nodes, TAB_QUERY_INPUT_ID), "value")).toBe("designer");
    expect(prop(byId(nodes, TAB_LOCATION_INPUT_ID), "value")).toBe("Delhi");
  });

  it("the window pills each choose their own window, announce the chosen one, and never disable", () => {
    const { actions, calls } = recorder();
    const nodes = tree(withForm({ freshness: "week" }), actions);
    expect(pills(nodes).map((el) => textOf(el))).toEqual(["24 hours", "3 days", "7 days"]);
    for (const el of pills(nodes)) press(el);
    expect(calls).toEqual([
      ["setFreshness", "day"],
      ["setFreshness", "3days"],
      ["setFreshness", "week"],
    ]);
    expect(pills(nodes).map((el) => prop(el, "aria-pressed"))).toEqual([false, false, true]);
    // A person may still change the window while a search runs (a different
    // request is a different search), so the pills are never inert.
    const busy = tree(
      ready({ search: { kind: "loading", request: REQUEST } }),
      actions,
    );
    expect(pills(busy).every((el) => prop(el, "aria-disabled") === undefined)).toBe(true);
    expect(pills(busy).every((el) => prop(el, "disabled") === undefined)).toBe(true);
  });

  it("the wording select chooses a locale, and anything it could not have produced is Auto", () => {
    const { actions, calls } = recorder();
    const nodes = tree(ready(), actions);
    const select = findAll(nodes, (el) => el.type === "select")[0];
    change(select, "india");
    change(select, "global");
    change(select, "auto");
    change(select, "klingon");
    expect(calls).toEqual([
      ["setLocale", "india"],
      ["setLocale", "global"],
      ["setLocale", "auto"],
      ["setLocale", "auto"],
    ]);
    expect(prop(select, "value")).toBe("auto");
    expect(prop(findAll(tree(withForm({ locale: "india" }), actions), (el) => el.type === "select")[0], "value")).toBe("india");
  });

  it("is a real form: submitting it (Enter in a box, or the Search button) searches, and the page does not reload", () => {
    const { actions, calls } = recorder();
    const nodes = tree(withForm({ query: "designer" }), actions);
    const form = findAll(nodes, (el) => el.type === "form")[0];
    expect(prop(form, "aria-label")).toBe(TAB_FORM_LABEL);
    expect(TAB_FORM_LABEL).toBe("Search posts");
    let prevented = false;
    (form.props.onSubmit as (event: unknown) => void)({
      preventDefault() {
        prevented = true;
      },
    });
    expect(prevented).toBe(true);
    expect(calls).toEqual([["search"]]);
    submit(form);
    expect(calls).toEqual([["search"], ["search"]]);
  });

  it("Search reads as busy only while the request in flight is exactly what the boxes would send", () => {
    const { actions } = recorder();
    const busySame = tree(
      withForm(
        { query: "software engineer", location: "Pune" },
        { search: { kind: "loading", request: REQUEST } },
      ),
      actions,
    );
    const same = byId(busySame, TAB_SEARCH_BUTTON_ID);
    expect(textOf(same)).toBe("Searching...");
    expect(prop(same, "aria-disabled")).toBe(true);
    expect(prop(same, "disabled")).toBeUndefined();

    // Edit the role, and it is a different search again: pressable, and says so.
    const busyEdited = tree(
      withForm(
        { query: "product designer", location: "Pune" },
        { search: { kind: "loading", request: REQUEST } },
      ),
      actions,
    );
    const edited = byId(busyEdited, TAB_SEARCH_BUTTON_ID);
    expect(textOf(edited)).toBe("Search");
    expect(prop(edited, "aria-disabled")).toBeUndefined();

    // Same words, different window: also a different search.
    const busyOtherWindow = tree(
      withForm(
        { query: "software engineer", location: "Pune", freshness: "week" },
        { search: { kind: "loading", request: REQUEST } },
      ),
      actions,
    );
    expect(prop(byId(busyOtherWindow, TAB_SEARCH_BUTTON_ID), "aria-disabled")).toBeUndefined();
  });

  it("is idle-enabled with an empty role: pressing it shows the field error instead of being dead", () => {
    const { actions } = recorder();
    const button = byId(tree(ready(), actions), TAB_SEARCH_BUTTON_ID);
    expect(prop(button, "aria-disabled")).toBeUndefined();
    expect(prop(button, "type")).toBe("submit");
  });

  it("puts a field's error next to it, marks the box invalid and points the box at the message", () => {
    const { actions } = recorder();
    const nodes = tree(
      ready({ fieldErrors: { query: QUERY_REQUIRED_MESSAGE, location: LOCATION_TOO_LONG_MESSAGE } }),
      actions,
    );
    const query = byId(nodes, TAB_QUERY_INPUT_ID);
    expect(prop(query, "aria-invalid")).toBe(true);
    expect(prop(query, "aria-describedby")).toBe(TAB_QUERY_ERROR_ID);
    const queryError = byId(nodes, TAB_QUERY_ERROR_ID);
    expect(textOf(queryError)).toBe(QUERY_REQUIRED_MESSAGE);
    expect(prop(queryError, "role")).toBe("alert");

    const location = byId(nodes, TAB_LOCATION_INPUT_ID);
    expect(prop(location, "aria-invalid")).toBe(true);
    expect(String(prop(location, "aria-describedby")).split(" ")).toContain(TAB_LOCATION_ERROR_ID);
    expect(textOf(byId(nodes, TAB_LOCATION_ERROR_ID))).toBe(LOCATION_TOO_LONG_MESSAGE);
  });

  it("shows no error and marks nothing invalid when the boxes are fine", () => {
    const { actions } = recorder();
    const nodes = tree(ready(), actions);
    expect(prop(byId(nodes, TAB_QUERY_INPUT_ID), "aria-invalid")).toBeUndefined();
    expect(prop(byId(nodes, TAB_QUERY_INPUT_ID), "aria-describedby")).toBeUndefined();
    expect(maybeById(nodes, TAB_QUERY_ERROR_ID)).toBeUndefined();
    expect(maybeById(nodes, TAB_LOCATION_ERROR_ID)).toBeUndefined();
  });
});

describe("Save this search", () => {
  function saveButton(state: TabState, actions: TabActions): HostElement {
    return byId(tree(state, actions), TAB_SAVE_SEARCH_BUTTON_ID);
  }

  it("is available for a role that is not saved yet, and pressing it calls saveSearch", () => {
    const { actions, calls } = recorder();
    const state = withForm({ query: "designer" });
    const button = saveButton(state, actions);
    expect(prop(button, "aria-disabled")).toBeUndefined();
    expect(prop(button, "aria-describedby")).toBeUndefined();
    expect(maybeById(tree(state, actions), TAB_SAVE_SEARCH_REASON_ID)).toBeUndefined();
    press(button);
    expect(calls).toEqual([["saveSearch"]]);
  });

  it("is unavailable, WITH VISIBLE TEXT saying why, when there is no role -- and never `disabled`", () => {
    const { actions } = recorder();
    const nodes = tree(ready(), actions);
    const button = byId(nodes, TAB_SAVE_SEARCH_BUTTON_ID);
    expect(prop(button, "aria-disabled")).toBe(true);
    expect(prop(button, "disabled")).toBeUndefined();
    expect(prop(button, "aria-describedby")).toBe(TAB_SAVE_SEARCH_REASON_ID);
    expect(textOf(byId(nodes, TAB_SAVE_SEARCH_REASON_ID))).toBe(SAVE_NEEDS_ROLE_REASON);
  });

  it("is unavailable, with the reason, when an equivalent search is already saved (case and spacing ignored)", () => {
    const { actions } = recorder();
    const state = withForm(
      { query: "  SOFTWARE   engineer ", location: "pune" },
      { searches: [makeSearch()] },
    );
    const nodes = tree(state, actions);
    expect(prop(byId(nodes, TAB_SAVE_SEARCH_BUTTON_ID), "aria-disabled")).toBe(true);
    expect(textOf(byId(nodes, TAB_SAVE_SEARCH_REASON_ID))).toBe(ALREADY_SAVED_REASON);
  });

  it("is open for the same role in a different place", () => {
    const { actions } = recorder();
    const state = withForm(
      { query: "software engineer", location: "Delhi" },
      { searches: [makeSearch()] },
    );
    expect(prop(saveButton(state, actions), "aria-disabled")).toBeUndefined();
  });

  it("still answers a press on the unavailable button: the model shows the error or announces the duplicate", () => {
    const { actions, calls } = recorder();
    press(saveButton(ready(), actions));
    expect(calls).toEqual([["saveSearch"]]);
  });

  it("reads 'Saving...' and is aria-disabled while the request is in flight, with no reason text", () => {
    const { actions } = recorder();
    const state = withForm({ query: "designer" }, { savingSearch: true });
    const nodes = tree(state, actions);
    const button = byId(nodes, TAB_SAVE_SEARCH_BUTTON_ID);
    expect(textOf(button)).toBe("Saving...");
    expect(prop(button, "aria-disabled")).toBe(true);
    expect(maybeById(nodes, TAB_SAVE_SEARCH_REASON_ID)).toBeUndefined();
  });

  it("shows the server's refusal (the 25-search cap) verbatim, as an alert, next to the button", () => {
    const { actions } = recorder();
    const cap = "You can keep at most 25 saved searches. Delete one to save another.";
    const state = withForm({ query: "designer" }, { saveSearchError: cap });
    const markup = html(state);
    expect(visibleText(markup)).toContain(cap);
    const alerts = findAll(tree(state, actions), (el) => prop(el, "role") === "alert");
    expect(alerts.map((el) => textOf(el))).toContain(cap);
    // A refusal is not something Try again would fix.
    expect(visibleText(markup)).not.toContain("Try again");
  });
});

describe("the saved searches list", () => {
  it("is absent with nothing saved and no failure", () => {
    const { actions } = recorder();
    expect(findAll(tree(ready(), actions), (el) => el.type === "h2")).toEqual([]);
    expect(html(ready())).not.toContain("Saved searches");
  });

  it("has a heading that focus can be sent to: an id, and focusable by script but not by Tab", () => {
    const { actions } = recorder();
    const heading = byId(tree(ready({ searches: [makeSearch()] }), actions), TAB_SAVED_SEARCHES_HEADING_ID);
    expect(heading.type).toBe("h2");
    expect(prop(heading, "tabIndex")).toBe(-1);
    expect(textOf(heading)).toBe("Saved searches");
  });

  it("lists each search as the person typed it, with Run and Delete named by that search", () => {
    const { actions } = recorder();
    const state = ready({
      searches: [
        makeSearch({ id: "a", query: "software engineer", location: "Pune" }),
        makeSearch({ id: "b", query: "data scientist", location: null }),
      ],
    });
    const nodes = tree(state, actions);
    const rows = findAll(nodes, (el) => el.type === "li");
    expect(rows.map((row) => textOf(row).replace(/(Run|Delete)/g, "").trim())).toEqual([
      "software engineer -- Pune",
      "data scientist",
    ]);
    // Only the buttons inside the list's rows (the form has its own).
    const rowButtons = rows.flatMap((row) => findAll(row.children, (el) => el.type === "button"));
    const labels = rowButtons.map((el) => prop(el, "aria-label"));
    expect(labels).toEqual([
      "Run saved search: software engineer -- Pune",
      "Delete saved search: software engineer -- Pune",
      "Run saved search: data scientist",
      "Delete saved search: data scientist",
    ]);
    expect(new Set(labels).size).toBe(labels.length);
    // The visible word leads each accessible name.
    for (const button of rowButtons) {
      expect(String(prop(button, "aria-label")).startsWith(textOf(button))).toBe(true);
    }
  });

  it("Run passes that search to the handler; Delete passes it to the delete handler", () => {
    const { actions, calls } = recorder();
    const first = makeSearch({ id: "a", query: "one" });
    const second = makeSearch({ id: "b", query: "two" });
    const nodes = tree(ready({ searches: [first, second] }), actions);
    const runs = findAll(nodes, (el) => textOf(el) === "Run");
    const deletes = findAll(nodes, (el) => textOf(el) === "Delete");
    press(runs[1]);
    press(deletes[0]);
    expect(calls).toEqual([
      ["runSavedSearch", second],
      ["deleteSearch", first],
    ]);
  });

  it("marks a search being deleted aria-disabled, says so, and ignores a press", () => {
    const { actions, calls } = recorder();
    const search = makeSearch({ id: "a" });
    const nodes = tree(
      ready({ searches: [search, makeSearch({ id: "b", query: "two" })], deletingSearchIds: new Set(["a"]) }),
      actions,
    );
    const busy = onlyButton(nodes, "Deleting...");
    expect(prop(busy, "aria-disabled")).toBe(true);
    expect(prop(busy, "disabled")).toBeUndefined();
    press(busy);
    expect(calls).toEqual([]);
    // The other row is untouched.
    expect(findAll(nodes, (el) => textOf(el) === "Delete")).toHaveLength(1);
  });

  it("reports a load failure with Try again, and still shows the heading", () => {
    const { actions, calls } = recorder();
    const nodes = tree(
      ready({ searchesStatus: "error", searchesError: "Could not load your saved searches." }),
      actions,
    );
    expect(textOf(byId(nodes, TAB_SAVED_SEARCHES_HEADING_ID))).toBe("Saved searches");
    press(onlyButton(nodes, "Try again"));
    expect(calls).toEqual([["reloadSearches"]]);
  });

  it("shows a failed delete in the list, in the server's words", () => {
    const { actions } = recorder();
    const state = ready({ searches: [makeSearch()], deleteSearchError: "Could not delete that search." });
    const alerts = findAll(tree(state, actions), (el) => prop(el, "role") === "alert");
    expect(alerts.map((el) => textOf(el))).toContain("Could not delete that search.");
  });

  it("wraps long saved text inside the row instead of widening the page (class hooks the stylesheet targets)", () => {
    const markup = html(ready({ searches: [makeSearch({ query: "x".repeat(150) })] }));
    expect(markup).toContain("bj-hs-tab-search-row");
    expect(markup).toContain("bj-hs-tab-search-label");
  });
});

describe("the live region", () => {
  it("gives every notice its own key, so the same words twice in a row are announced twice", () => {
    const { actions } = recorder();
    const region = (state: TabState) => liveRegion(tree(state, actions));
    const first = region(ready({ notice: { id: 1, text: "Search saved." } }));
    const second = region(ready({ notice: { id: 2, text: "Search saved." } }));
    const keyOf = (el: HostElement) => findAll(el.children, (c) => c.type === "span")[0].key;
    expect(textOf(first)).toContain("Search saved.");
    expect(keyOf(first)).toBe("1");
    expect(keyOf(second)).toBe("2");
  });

  it("is empty when there is nothing to say", () => {
    const { actions } = recorder();
    expect(textOf(liveRegion(tree(ready(), actions))).trim()).toBe("");
  });

  it("says a search is running, and announces the result headline afterwards", () => {
    const { actions } = recorder();
    const loading = liveRegion(tree(ready({ search: { kind: "loading", request: REQUEST } }), actions));
    expect(textOf(loading)).toBe("Searching for recent hiring posts...");
    const one = liveRegion(tree(results(makeOutcome([makeSignal()])), actions));
    expect(textOf(one)).toBe("1 post found");
    const many = liveRegion(tree(results(makeOutcome([makeSignal(), makeSignal({ activity_id: ID_2 })])), actions));
    expect(textOf(many)).toBe("2 posts found");
    const none = liveRegion(tree(results(makeOutcome([])), actions));
    expect(textOf(none)).toBe("No posts to show for this search");
  });

  it("owns up in the headline to posts this page could not display", () => {
    const { actions } = recorder();
    const region = liveRegion(tree(results(makeOutcome([], { unreadable: 2 })), actions));
    expect(textOf(region)).toBe("This page could not display the posts the search found");
  });
});

describe("the results", () => {
  const twoPosts = (): TabSearchOutcome =>
    makeOutcome([makeSignal(), makeSignal({ activity_id: ID_2, author_name: "John Example" })], {
      counts: makeCounts({ raw_hits: 5, shown: 2, role_mismatch_hidden: 2, duplicates: 1 }),
    });

  it("says what was searched, through which provider, in which wording, and how the search went", () => {
    const text = visibleText(html(results(twoPosts())));
    expect(text).toContain("software engineer -- Pune -- last 3 days -- via Firecrawl -- Global wording");
    expect(text).toContain(
      "The search returned 5 results; 2 shown. Not shown: 2 that did not contain all the words of your role and 1 duplicate.",
    );
  });

  it("never words the role filter as being about a company", () => {
    const text = visibleText(html(results(twoPosts())));
    expect(text).not.toMatch(/not about this company|this company/i);
  });

  it("notes a cached answer, and says nothing about the cache otherwise", () => {
    expect(visibleText(html(results(makeOutcome([makeSignal()], { cached: true }))))).toContain(
      "recent cached search",
    );
    expect(visibleText(html(results(makeOutcome([makeSignal()]))))).not.toContain("cached");
  });

  it("shows no counts sentence when the counts are unknown -- better none than a partial breakdown", () => {
    const text = visibleText(html(results(makeOutcome([makeSignal()], { counts: null }))));
    expect(text).not.toContain("The search returned");
  });

  it("renders one card per post, in the server's order, each with its author and posted time", () => {
    const text = visibleText(html(results(twoPosts())));
    expect(text.indexOf("Jane Example")).toBeGreaterThan(-1);
    expect(text.indexOf("Jane Example")).toBeLessThan(text.indexOf("John Example"));
    expect(text).toContain("Posted 1 day ago");
  });

  it("explains Unclassified once, and each other tag on screen in visible text", () => {
    const outcome = makeOutcome([
      makeSignal({ species: "unclassified" }),
      makeSignal({ activity_id: ID_2, species: "hiring_drive" }),
      makeSignal({ activity_id: ID_3, species: "hiring_drive" }),
    ]);
    const text = visibleText(html(results(outcome)));
    expect(text).toContain(UNCLASSIFIED_LEGEND);
    expect(text.match(/Hiring drive: /g)).toHaveLength(1);
    expect(text).not.toContain("Referral offer:");
  });

  it("shows the aggregator badge only on an account flagged true, with a visible legend -- never on false or unknown", () => {
    const flagged = html(
      results(
        makeOutcome([
          makeSignal({ aggregator: true }),
          makeSignal({ activity_id: ID_2, aggregator: false }),
          makeSignal({ activity_id: ID_3, aggregator: null }),
        ]),
      ),
    );
    expect(flagged.match(new RegExp(`>${AGGREGATOR_BADGE}<`, "g"))).toHaveLength(1);
    expect(visibleText(flagged)).toContain(AGGREGATOR_LEGEND);

    const none = html(
      results(
        makeOutcome([
          makeSignal({ aggregator: false }),
          makeSignal({ activity_id: ID_2, aggregator: null }),
        ]),
      ),
    );
    expect(none).not.toContain(AGGREGATOR_BADGE);
    expect(visibleText(none)).not.toContain(AGGREGATOR_LEGEND);
  });

  it("words the aggregator tag as a fact, never a verdict", () => {
    const text = visibleText(html(results(makeOutcome([makeSignal({ aggregator: true })]))));
    expect(text).toContain("Posts often");
    expect(text).toContain("may be a job-alert or reposting account");
    expect(text).not.toMatch(/\bspam\b|\bbot\b|fake/i);
  });

  it("shows a registry note only for an echo that is unmatched or possibly matched", () => {
    const text = visibleText(
      html(
        results(
          makeOutcome([
            makeSignal({ species: "ats_echo", registry_match: "unmatched" }),
            makeSignal({ activity_id: ID_2, species: "ats_echo", registry_match: "possible" }),
            makeSignal({ activity_id: ID_3, registry_match: null }),
          ]),
        ),
      ),
    );
    // the tab has no company of its own: the listing's page is the one tracked
    expect(text).toContain("We track the company that shared this, but not this listing yet.");
    expect(text).not.toContain("We track this company");
    expect(text).toContain("May match a job listing we already track.");
    expect(text.match(/listing we already track|not this listing yet/g)).toHaveLength(2);
  });

  it("does not use the per-application panel's 'Role words found' tag: the role filter hides mismatches instead", () => {
    expect(html(results(twoPosts()))).not.toContain("Role words found");
  });

  it("mounts no iframe until a card is opened, then exactly one hardened one from the validated address", () => {
    expect(iframeCount(html(results(twoPosts())))).toBe(0);
    const open = html(results(twoPosts(), { openKeys: new Set([searchCardKey(ID)]) }));
    expect(iframeCount(open)).toBe(1);
    expect(open).toContain(`src="${EMBED_URL_PREFIX}${ID}"`);
    expect(open).toContain('sandbox="allow-scripts allow-same-origin allow-popups"');
    expect(open).toMatch(/referrerpolicy="no-referrer"/i);
    const bad = html(
      results(makeOutcome([makeSignal({ embed_url: "https://evil.example/x" })]), {
        openKeys: new Set([searchCardKey(ID)]),
      }),
    );
    expect(iframeCount(bad)).toBe(0);
    expect(bad).not.toContain("evil.example");
  });

  it("Save passes the post AND the label of the search that found it", () => {
    const { actions, calls } = recorder();
    const signal = makeSignal();
    press(onlyButton(tree(results(makeOutcome([signal])), actions), "Save"));
    expect(calls).toEqual([["save", signal, QUERY_LABEL]]);
  });

  it("Show post toggles that card's embed, in its own namespace", () => {
    const { actions, calls } = recorder();
    press(onlyButton(tree(results(makeOutcome([makeSignal()])), actions), "Show post"));
    expect(calls).toEqual([["toggleEmbed", searchCardKey(ID)]]);
  });

  it("Unsave on a result card names the RESULT card, so focus can return to its Save button", () => {
    const { actions, calls } = recorder();
    const save = makeSave();
    const state = results(makeOutcome([makeSignal()]), { saves: [save] });
    press(onlyButton(tree(state, actions), "Unsave"));
    expect(calls).toEqual([["unsave", save, searchCardKey(ID)]]);
  });

  it("marks a post saved from the loaded list, and trusts the server's flag only until that list has loaded", () => {
    const flagged = makeOutcome([makeSignal({ saved: true })]);
    // Saves not loaded yet: the server's per-post flag is the best information there is.
    expect(visibleText(html(results(flagged, { savesStatus: "loading" })))).toContain("Saved");
    // Loaded and the post is not in the list: it was removed, whatever the flag says.
    const loadedWithout = html(results(flagged, { savesStatus: "ready", saves: [] }));
    expect(loadedWithout).not.toMatch(/>Saved</);
    expect(loadedWithout).toMatch(/>Save</);
    // Loaded and it is in the list.
    expect(html(results(makeOutcome([makeSignal()]), { saves: [makeSave()] }))).toMatch(/>Saved</);
  });

  it("shows a card's own error under that card", () => {
    const { actions } = recorder();
    const state = results(makeOutcome([makeSignal()]), {
      cardErrors: { [searchCardKey(ID)]: "Could not save that post." },
    });
    const alerts = findAll(tree(state, actions), (el) => prop(el, "role") === "alert");
    expect(alerts.map((el) => textOf(el))).toContain("Could not save that post.");
  });
});

describe("notes that are true of the search whatever is on screen", () => {
  const manyHidden = (signals: TabSignal[] = [makeSignal(), makeSignal({ activity_id: ID_2 })]) =>
    makeOutcome(signals, {
      counts: makeCounts({ raw_hits: 10, shown: signals.length, role_mismatch_hidden: 8 }),
    });

  it("suggests a shorter role WITH cards on screen when the role filter hid at least half", () => {
    const text = visibleText(html(results(manyHidden())));
    expect(text).toContain(HINT_SHORTER_ROLE);
    expect(text).toContain("Not shown: 8 that did not contain all the words of your role");
  });

  it("does not suggest it when few were hidden for the role", () => {
    const few = makeOutcome([makeSignal()], {
      counts: makeCounts({ raw_hits: 10, shown: 7, role_mismatch_hidden: 3 }),
    });
    expect(visibleText(html(results(few)))).not.toContain(HINT_SHORTER_ROLE);
  });

  it("says once when the search stopped at the provider's cap, with cards or without", () => {
    const capped = makeOutcome([makeSignal()], {
      counts: makeCounts({ raw_hits: 20, shown: 1, role_mismatch_hidden: 19 }),
    });
    const text = visibleText(html(results(capped)));
    expect(text).toContain(TAB_TRUNCATED_NOTE);
    expect(text.split(TAB_TRUNCATED_NOTE)).toHaveLength(2);
    const emptyCapped = makeOutcome([], {
      counts: makeCounts({ raw_hits: 20, role_mismatch_hidden: 20 }),
    });
    expect(visibleText(html(results(emptyCapped)))).toContain(TAB_TRUNCATED_NOTE);
    // ... and never below the cap
    const under = makeOutcome([makeSignal()], {
      counts: makeCounts({ raw_hits: 19, shown: 1, role_mismatch_hidden: 18 }),
    });
    expect(visibleText(html(results(under)))).not.toContain(TAB_TRUNCATED_NOTE);
  });

  it("warns about a very short role, and only for the search that used it", () => {
    const outcome = makeOutcome([makeSignal()]);
    const short = ready({
      search: { kind: "ready", outcome, request: { ...REQUEST, query: "PM" } },
    });
    expect(visibleText(html(short))).toContain(LOOSE_ROLE_NOTE);
    const ordinary = ready({
      search: { kind: "ready", outcome, request: { ...REQUEST, query: "product manager" } },
    });
    expect(visibleText(html(ordinary))).not.toContain(LOOSE_ROLE_NOTE);
    // the warning follows the request the RESULT came from, not what is now in the box
    const editedAfter = ready({
      form: { ...INITIAL_FORM, query: "PM" },
      search: { kind: "ready", outcome, request: REQUEST },
    });
    expect(visibleText(html(editedAfter))).not.toContain(LOOSE_ROLE_NOTE);
  });

  it("gives a result card's Save button the id a focus request for it resolves to", () => {
    const markup = html(results(makeOutcome([makeSignal()])));
    const id = tabFocusTargetId({ kind: "save_button", activityId: ID });
    expect(markup).toContain(`id="${id}"`);
  });
});

describe("an empty result", () => {
  const emptyState = (
    extra: Parameters<typeof makeOutcome>[1] = {},
    overrides: Partial<TabState> = {},
  ) => results(makeOutcome([], extra), overrides);

  it("is an answer, not an error: no red, no alert, and it says what the index returned", () => {
    const markup = html(emptyState({ counts: makeCounts({ raw_hits: 4, role_mismatch_hidden: 4 }) }));
    expect(markup).not.toContain("bj-error");
    expect(markup).not.toContain('role="alert"');
    const text = visibleText(markup);
    expect(text).toContain("The search returned 4 results; 0 shown. Not shown: 4 that did not contain all the words of your role.");
    expect(text).toContain(TAB_NOTHING_SHOWN_NOTE);
  });

  it("never says nobody is hiring", () => {
    const text = visibleText(html(emptyState()));
    expect(text).not.toMatch(/nobody is hiring(?! --)/i);
    expect(text).toContain("says little about whether anyone is hiring for this role");
    expect(text).not.toMatch(/no one is hiring|no jobs|not hiring/i);
  });

  it("says a search with no posts at all says little about the role", () => {
    expect(visibleText(html(emptyState()))).toContain(TAB_NO_POSTS_SEEN_NOTE);
  });

  it("blames the index, not the role, for a provider known to hold no such posts, and links to Integrations", () => {
    const text = visibleText(html(emptyState({ provider: "you_com" })));
    expect(text).toContain(TAB_YOU_COM_NO_LINKEDIN_NOTE);
    expect(html(emptyState({ provider: "you_com" }))).toContain('href="/profile/integrations"');
    // ... and only then.
    expect(html(emptyState({ provider: "firecrawl" }))).not.toContain("/profile/integrations");
  });

  it("offers hints only where they are true of the search: a shorter role, and dropping the location", () => {
    const withBoth = visibleText(
      html(emptyState({ counts: makeCounts({ raw_hits: 3, role_mismatch_hidden: 3 }) })),
    );
    expect(withBoth).toContain(HINT_SHORTER_ROLE);
    expect(withBoth).toContain(HINT_DROP_LOCATION);
    // No mismatches, and no location on the search that produced this result.
    const state = ready({
      search: {
        kind: "ready",
        outcome: makeOutcome([]),
        request: { ...REQUEST, location: null },
      },
    });
    const bare = visibleText(html(state));
    expect(bare).not.toContain(HINT_SHORTER_ROLE);
    expect(bare).not.toContain(HINT_DROP_LOCATION);
  });

  it("offers to widen the window one step, and pressing it asks for that window", () => {
    const { actions, calls } = recorder();
    press(onlyButton(tree(emptyState({ freshness: "day" }), actions), "Search the last 3 days"));
    press(onlyButton(tree(emptyState({ freshness: "3days" }), actions), "Search the last 7 days"));
    expect(calls).toEqual([
      ["widen", "3days"],
      ["widen", "week"],
    ]);
  });

  it("offers no widening at the widest window", () => {
    const { actions } = recorder();
    const nodes = tree(emptyState({ freshness: "week" }), actions);
    expect(findAll(nodes, (el) => el.type === "button" && textOf(el).startsWith("Search the"))).toEqual([]);
  });

  it("offers no widening, and no 'nothing returned' note, when the result has cards", () => {
    const text = visibleText(html(results(makeOutcome([makeSignal()], { freshness: "day" }))));
    expect(text).not.toContain("Search the last");
    expect(text).not.toContain(TAB_NOTHING_SHOWN_NOTE);
  });

  it("says nothing about the index when entries came back that this page could not display", () => {
    const text = visibleText(
      html(
        emptyState({
          unreadable: 2,
          counts: makeCounts({ raw_hits: 2, shown: 2 }),
        }),
      ),
    );
    expect(text).toContain("Not shown: 2 that this page could not display.");
    expect(text).not.toContain(TAB_NOTHING_SHOWN_NOTE);
    expect(text).not.toContain(TAB_NO_POSTS_SEEN_NOTE);
  });
});

describe("a failed search", () => {
  const failed = (failure: Parameters<typeof makeFailed>[0]) => makeFailed(failure);
  function makeFailed(
    failure:
      | { kind: "setup_required"; message: string }
      | { kind: "not_found"; message: string }
      | { kind: "error"; message: string; retryable: boolean },
  ): TabState {
    return ready({ search: { kind: "failed", failure, request: REQUEST } });
  }

  it("sends setup-required to Integrations as a to-do (gold, not red), with no pointless retry", () => {
    const markup = html(failed({ kind: "setup_required", message: "Connect a search provider first." }));
    expect(visibleText(markup)).toContain("Connect a search provider first.");
    expect(markup).toContain('href="/profile/integrations"');
    expect(markup).toContain("bj-hs-callout");
    expect(markup).not.toContain("bj-error");
    expect(visibleText(markup)).not.toContain("Try again");
  });

  it("offers Try again when the server says trying again can help, and pressing it retries", () => {
    const { actions, calls } = recorder();
    const state = failed({ kind: "error", message: "The provider is busy.", retryable: true });
    expect(visibleText(html(state))).toContain("The provider is busy.");
    press(onlyButton(tree(state, actions), "Try again"));
    expect(calls).toEqual([["retry"]]);
  });

  it("offers no Try again when it cannot help, but still shows the server's message", () => {
    const { actions } = recorder();
    const state = failed({ kind: "error", message: "The role is too long.", retryable: false });
    expect(visibleText(html(state))).toContain("The role is too long.");
    expect(findAll(tree(state, actions), (el) => textOf(el) === "Try again")).toEqual([]);
  });

  it("rewords a missing thing for the tab -- it must not say 'application' or offer Back to Applications", () => {
    const text = visibleText(html(failed({ kind: "not_found", message: "Application 6b1f-... not found" })));
    expect(text).toContain(TAB_NOT_FOUND_MESSAGE);
    expect(text).not.toMatch(/application/i);
  });

  it("shows no results while a search has failed", () => {
    const markup = html(failed({ kind: "error", message: "x", retryable: true }));
    expect(markup).not.toContain("<li");
    expect(markup).not.toContain('aria-label="Results"');
  });
});

describe("the saved posts list", () => {
  it("is absent with nothing saved and no failure", () => {
    expect(html(ready())).not.toContain("Saved posts");
  });

  it("has a heading focus can be sent to, and lists each saved post with Show post and Remove", () => {
    const { actions } = recorder();
    const state = ready({ saves: [makeSave({ id: "save-9", activity_id: ID_2 })] });
    const nodes = tree(state, actions);
    const heading = byId(nodes, TAB_SAVED_POSTS_HEADING_ID);
    expect(prop(heading, "tabIndex")).toBe(-1);
    expect(textOf(heading)).toBe("Saved posts");
    expect(findAll(nodes, (el) => el.type === "li")).toHaveLength(1);
    expect(iframeCount(html(state))).toBe(0);
  });

  it("Remove names the SAVED card, and Show post toggles it in the saved namespace", () => {
    const { actions, calls } = recorder();
    const save = makeSave({ id: "save-9", activity_id: ID_2 });
    const nodes = tree(ready({ saves: [save] }), actions);
    press(onlyButton(nodes, "Remove"));
    press(onlyButton(nodes, "Show post"));
    expect(calls).toEqual([
      ["unsave", save, savedCardKey("save-9")],
      ["toggleEmbed", savedCardKey("save-9")],
    ]);
  });

  it("re-embeds a saved post from its validated address only, when opened", () => {
    const save = makeSave({ id: "save-9" });
    const open = html(ready({ saves: [save], openKeys: new Set([savedCardKey("save-9")]) }));
    expect(iframeCount(open)).toBe(1);
    expect(open).toContain(`src="${EMBED_URL_PREFIX}${ID}"`);
    const bad = html(
      ready({
        saves: [makeSave({ id: "save-9", embed_url: "https://evil.example/x" })],
        openKeys: new Set([savedCardKey("save-9")]),
      }),
    );
    expect(iframeCount(bad)).toBe(0);
  });

  it("reloads the list from its own Try again after a failure, and shows the heading with the failure", () => {
    const { actions, calls } = recorder();
    const state = ready({ savesStatus: "error", savesError: "Could not load your saved posts." });
    const nodes = tree(state, actions);
    expect(maybeById(nodes, TAB_SAVED_POSTS_HEADING_ID)).toBeDefined();
    press(onlyButton(nodes, "Try again"));
    expect(calls).toEqual([["reloadSaves"]]);
  });

  it("keeps a saved post and a result card for the same post independent", () => {
    const { actions } = recorder();
    const state = results(makeOutcome([makeSignal()]), {
      saves: [makeSave()],
      openKeys: new Set([searchCardKey(ID)]),
    });
    // Opening the result must not open the saved one.
    expect(iframeCount(html(state))).toBe(1);
    expect(tree(state, actions).length).toBeGreaterThan(0);
  });
});

describe("the switched-off feature", () => {
  it("renders a calm note -- no form, no inputs, no buttons, and nothing red", () => {
    const { actions } = recorder();
    const state = ready({ disabled: true, search: { kind: "loading", request: REQUEST }, saves: [makeSave()] });
    const markup = html(state);
    expect(visibleText(markup)).toContain("Hiring signals is switched off");
    expect(visibleText(markup)).toContain("This server has the feature turned off");
    expect(markup).not.toMatch(/<form|<input|<button|<select/);
    expect(markup).not.toContain("bj-error");
    expect(markup).not.toContain('role="alert"');
    const nodes = tree(state, actions);
    expect(findAll(nodes, (el) => el.type === "button" || el.type === "input")).toEqual([]);
  });

  it("is the same note the page shell shows", () => {
    const shell = renderToStaticMarkup(<SwitchedOffNote />);
    expect(html(ready({ disabled: true }))).toBe(shell);
  });
});

describe("the page shell", () => {
  const CHILD = <div id="the-connected-tab">connected tab</div>;
  const noop = () => {};
  const shell = (status: Parameters<typeof HiringSignalsPageShell>[0]["status"], onRetryStatus = noop) =>
    renderToStaticMarkup(
      <HiringSignalsPageShell status={status} onRetryStatus={onRetryStatus}>
        {CHILD}
      </HiringSignalsPageShell>,
    );

  it("titles the page 'Hiring signals' in every state", () => {
    for (const kind of ["checking", "enabled", "disabled", "unavailable"] as const) {
      expect(shell({ kind })).toContain("<h1>Hiring signals</h1>");
    }
  });

  it("mounts the tab only once the server has said the feature is on", () => {
    expect(shell({ kind: "enabled" })).toContain("connected tab");
    for (const kind of ["checking", "disabled", "unavailable"] as const) {
      expect(shell({ kind })).not.toContain("connected tab");
    }
  });

  it("shows only the heading while it is still finding out -- no placeholder, no flash of a form", () => {
    const markup = shell({ kind: "checking" });
    expect(markup).toBe("<div><h1>Hiring signals</h1></div>");
  });

  it("shows the calm note when the server has the feature off", () => {
    const markup = shell({ kind: "disabled" });
    expect(visibleText(markup)).toContain("Hiring signals is switched off");
    expect(markup).not.toContain("bj-error");
  });

  it("offers a retry when the status request itself failed, and pressing it calls onRetryStatus", () => {
    const calls: string[] = [];
    const nodes = expand(
      HiringSignalsPageShell({
        status: { kind: "unavailable" },
        onRetryStatus: () => calls.push("retry"),
        children: CHILD,
      }),
    );
    const markup = shell({ kind: "unavailable" });
    expect(visibleText(markup)).toContain("Could not check whether hiring signals are available on this server.");
    expect(markup).toContain('role="alert"');
    press(onlyButton(nodes, "Try again"));
    expect(calls).toEqual(["retry"]);
  });

  it("never mentions the source site anywhere on the shell", () => {
    for (const kind of ["checking", "disabled", "unavailable"] as const) {
      expect(shell({ kind })).not.toMatch(/linkedin/i);
    }
  });
});

describe("SignalCard is shared with the per-application panel, and stays the same for it", () => {
  const NOW_ = NOW;
  function renderCard(signal: Parameters<typeof SignalCard>[0]["signal"]): string {
    return renderToStaticMarkup(
      <ul>
        <SignalCard
          signal={signal}
          now={NOW_}
          saved={false}
          saving={false}
          embedOpen={false}
          error={null}
          onToggleEmbed={() => {}}
          onSave={() => {}}
          onUnsave={null}
          unsaving={false}
        />
      </ul>,
    );
  }

  const panelSignal: HiringSignal = {
    activity_id: ID,
    post_url: `https://www.linkedin.com/posts/example-${ID}`,
    embed_url: `${EMBED_URL_PREFIX}${ID}`,
    author_name: "Jane Example",
    posted_at: "2026-09-18T12:00:00Z",
    age_hint: null,
    species: "unclassified",
    comment_count: null,
    role_match: true,
    registry_match: null,
    saved: false,
  };

  it("the per-application panel's signal still shows 'Role words found' and never an aggregator badge", () => {
    const markup = renderCard(panelSignal);
    expect(markup).toContain("Role words found");
    expect(markup).not.toContain(AGGREGATOR_BADGE);
  });

  it("the tab's signal shows the aggregator badge, and never 'Role words found'", () => {
    const markup = renderCard(makeSignal({ aggregator: true }));
    expect(markup).toContain(AGGREGATOR_BADGE);
    expect(markup).toContain("bj-badge-muted");
    expect(markup).not.toContain("Role words found");
  });

  it("gives the badge a title with the legend, but the legend is also visible text elsewhere", () => {
    const markup = renderCard(makeSignal({ aggregator: true }));
    expect(markup).toContain(`title="${AGGREGATOR_LEGEND}"`);
  });
});

describe("form defaults", () => {
  it("the form the tab starts with is the one the model's constant says (no drift between page and model)", () => {
    expect(initialTabState().form).toEqual(INITIAL_FORM);
    const nodes = tree(initialTabState({ savesStatus: "ready" }), NO_ACTIONS);
    expect(pills(nodes).map((el) => prop(el, "aria-pressed"))).toEqual([false, true, false]);
  });
});
