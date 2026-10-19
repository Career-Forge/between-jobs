// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { buttonNamed, click, installViewport, query, render } from "../testing/render";

// The Hiring signals page against a fake API: the status gate, what the band says as the reads, the
// status and the searches answer, a missing search key, a search that fails, saving a search from the
// band, and a refused save. What each block looks like is drawn from props in
// components/HiringSignalsTabView.test.tsx, and the rows are lib/nextStep/pages/hiring-signals.test.ts.

const api = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("../lib/api", () => ({ apiFetch: api.apiFetch }));
vi.mock("../auth", () => ({ useAuth: () => ({ session: { user: { id: "user-1" } }, loading: false }) }));

type Reply = unknown | Error | (() => unknown);

class FakeApiError extends Error {
  code?: string;
  retryable?: boolean;
  constructor(
    public readonly status: number,
    message: string,
    extra: { code?: string; retryable?: boolean } = {},
  ) {
    super(message);
    Object.assign(this, extra);
  }
}

const ID = "7000000000000000001";
const ID_2 = "7000000000000000002";
const SIGNAL = (id: string, extra: Record<string, unknown> = {}) => ({
  activity_id: id,
  post_url: `https://www.linkedin.com/posts/example-${id}`,
  embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${id}`,
  author_name: "Jane Example",
  posted_at: "2026-09-18T12:00:00Z",
  age_hint: null,
  species: "unclassified",
  comment_count: null,
  registry_match: null,
  aggregator: false,
  saved: false,
  ...extra,
});
const COUNTS = { raw_hits: 5, rejected: 0, duplicates: 1, role_mismatch_hidden: 2, echoes_hidden: 0, job_seekers_hidden: 0, too_old_hidden: 0, shown: 2 };
const RESULT = (extra: Record<string, unknown> = {}) => ({
  provider: "firecrawl",
  cached: false,
  freshness: "3days",
  locale: "global",
  query_label: "software engineer -- last 3 days",
  signals: [SIGNAL(ID), SIGNAL(ID_2)],
  counts: COUNTS,
  ...extra,
});
const SAVED_SEARCH = { id: "hs-1", query: "data scientist", location: null, created_at: "2026-09-10T00:00:00Z" };
const KEYS = [
  { service: "llm", provider: "openrouter", is_validated: true, model: "vendor/model" },
  { service: "search", provider: "firecrawl", is_validated: true },
  { service: "search", provider: "brave", is_validated: true },
];

let replies: Record<string, Reply> = {};
const calls = () => api.apiFetch.mock.calls.map(([path, init]) => `${init?.method ?? "GET"} ${path}`);
const callsTo = (prefix: string) => calls().filter((call) => call.startsWith(prefix));

function reply(path: string, method = "GET"): Reply {
  const key = `${method} ${path}`;
  return key in replies ? replies[key] : replies[path];
}

function answer(extra: Record<string, Reply> = {}) {
  replies = {
    "/hiring-signals/status": { enabled: true },
    "/profile/current": { id: "pv-1", activated_at: "2026-09-12T10:00:00Z" },
    "/credentials": KEYS,
    "/hiring-signals/saves": { saves: [] },
    "/hiring-signals/searches": { searches: [SAVED_SEARCH] },
    ...extra,
  };
}

const flush = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));

// The status store is module-level (one answer for the whole app), so each test loads a fresh page.
async function draw() {
  vi.resetModules();
  const { default: HiringSignals } = await import("./HiringSignals");
  installViewport(1000);
  const rendered = render(
    <MemoryRouter>
      <HiringSignals />
    </MemoryRouter>,
  );
  await flush();
  return rendered;
}

const words = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();
const band = (container: HTMLElement) => query(container, 'section[aria-label="Next step"]');
const form = (container: HTMLElement) => query<HTMLFormElement>(container, "form");
const cyan = (container: HTMLElement) => container.querySelectorAll("button.bj-primary, a.bj-button-link-primary");

function type(input: HTMLInputElement, value: string) {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function search(container: HTMLElement, role = "software engineer") {
  type(query(form(container), "#hst-query"), role);
  act(() => form(container).requestSubmit());
  await flush();
}

beforeEach(() => {
  api.apiFetch.mockReset();
  api.apiFetch.mockImplementation(async (path: string, init?: { method?: string }) => {
    const found = reply(path, init?.method ?? "GET");
    if (found === undefined) throw new Error(`no reply for ${init?.method ?? "GET"} ${path}`);
    if (found instanceof Error) throw found;
    return typeof found === "function" ? (found as () => unknown)() : found;
  });
  answer();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the status gate", () => {
  it("shows only the header and the band's Loading... while the status is being asked, and asks for nothing else of the feature", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/hiring-signals/status": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    expect(words(band(container))).toBe("Next stepLoading...");
    expect(container.querySelector("form")).toBeNull();
    expect(words(query(container, "h1"))).toBe("Hiring signals");
    expect(callsTo("GET /hiring-signals/saves")).toEqual([]);
    expect(callsTo("GET /hiring-signals/searches")).toEqual([]);
    await act(async () => release({ enabled: true }));
    await flush();
    expect(container.querySelector("form")).not.toBeNull();
  });

  it("asks for both saved lists once the feature is on, and makes no search", async () => {
    await draw();
    expect(callsTo("GET /hiring-signals/saves").length).toBeGreaterThanOrEqual(1);
    expect(callsTo("GET /hiring-signals/searches").length).toBeGreaterThanOrEqual(1);
    expect(callsTo("POST /hiring-signals/search")).toEqual([]);
  });

  it("says the feature is switched off, unlit, and makes no request of the feature but the status read", async () => {
    answer({ "/hiring-signals/status": { enabled: false } });
    const { container } = await draw();
    const text = words(band(container));
    expect(text).toContain("Hiring signals is switched off on this server. Nothing else is affected.");
    expect(text).toContain("why: switched off on this server");
    expect(band(container).querySelector("button, a")).toBeNull();
    expect(words(query(container, ".bj-state--empty"))).toContain("Hiring signals is switched off");
    expect(container.querySelector("form")).toBeNull();
    expect(cyan(container)).toHaveLength(0);
    expect(calls().filter((call) => call.includes("hiring-signals") && !call.endsWith("/status"))).toEqual([]);
  });

  it("reads a 404 on the status as the feature being off, not as a failure", async () => {
    answer({ "/hiring-signals/status": new FakeApiError(404, "not found", { code: "FEATURE_DISABLED" }) });
    const { container } = await draw();
    expect(words(band(container))).toContain("switched off on this server");
    expect(container.querySelector(".bj-state--failure")).toBeNull();
  });

  it("names a failed status read in the band, with the call and the time, and Try again re-reads it", async () => {
    let attempt = 0;
    answer({
      "/hiring-signals/status": () => {
        if (++attempt === 1) throw new FakeApiError(503, "down");
        return { enabled: true };
      },
    });
    const { container } = await draw();
    const text = words(band(container));
    expect(text).toContain("Couldn't check whether hiring signals are available. You didn't do anything wrong, and nothing was lost.");
    expect(text).toMatch(/why: GET \/hiring-signals\/status failed · \d\d:\d\d/);
    expect(words(query(container, ".bj-state--failure"))).toContain("Could not check whether hiring signals are available on this server.");
    expect(query(container, ".bj-state--failure").querySelector("button")).toBeNull();
    expect(cyan(container)).toHaveLength(1);
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(callsTo("GET /hiring-signals/status")).toHaveLength(2);
    expect(container.querySelector("form")).not.toBeNull();
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
  });
});

describe("ready, before any search", () => {
  it("lights Search in the band with the keys and saved searches it read, and the form's own Search is outlined", async () => {
    const { container } = await draw();
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
    expect(words(band(container))).toContain("why: 2 search keys connected · 1 saved search");
    const lit = query<HTMLButtonElement>(band(container), "button.bj-primary");
    expect([words(lit), lit.getAttribute("form"), lit.getAttribute("type")]).toEqual(["Search", "hiring-signals-form", "submit"]);
    expect(cyan(container)).toHaveLength(1);
    expect(form(container).querySelector("button.bj-primary")).toBeNull();
    expect(words(buttonNamed(form(container), "Search"))).toBe("Search");
  });

  it("counts only the four providers a search can use", async () => {
    answer({ "/credentials": [...KEYS, { service: "search", provider: "apollo" }] });
    const { container } = await draw();
    expect(words(band(container))).toContain("2 search keys connected");
  });

  it("leaves the saved-searches clause out when that list could not be read, and says nothing is lit by it", async () => {
    answer({ "/hiring-signals/searches": new FakeApiError(500, "down") });
    const { container } = await draw();
    expect(words(band(container))).toContain("why: 2 search keys connected");
    expect(words(band(container))).not.toContain("saved search");
    expect(words(query(container, ".bj-state--failure"))).toContain("down");
  });

  it("is also lit by the band's Search, which submits the same form: one request", async () => {
    answer({ "/hiring-signals/search": RESULT() });
    const { container } = await draw();
    type(query(form(container), "#hst-query"), "software engineer");
    click(query(band(container), "button.bj-primary"));
    await flush();
    expect(callsTo("POST /hiring-signals/search")).toHaveLength(1);
    expect(container.querySelectorAll("li.bj-hs-card")).toHaveLength(2);
  });

  it("answers an empty role with the field's own error and sends nothing", async () => {
    const { container } = await draw();
    click(query(band(container), "button.bj-primary"));
    await flush();
    expect(callsTo("POST /hiring-signals/search")).toEqual([]);
    expect(words(query(container, "#hst-query-error"))).toBe("Enter a role to search for.");
  });
});

describe("a missing search key", () => {
  it("says so in the band with a link to the search keys, and in the results slot, before any search", async () => {
    answer({ "/credentials": [KEYS[0]] });
    const { container } = await draw();
    expect(words(band(container))).toContain("Hiring signals searches with your own search provider. Add a Brave, Serper, Firecrawl or You.com key first.");
    const link = query<HTMLAnchorElement>(band(container), "a.bj-button-link-primary");
    expect([words(link), link.getAttribute("href")]).toEqual(["Add a search key", "/settings#search-keys"]);
    expect(words(band(container))).toContain("why: no Brave, Serper, Firecrawl or You.com key");
    expect(words(query(container, ".bj-state--setup"))).toContain("Missing:Add a search key");
    expect(cyan(container)).toHaveLength(1);
  });

  it("does not stop a search: the server stays the authority, and its answer is shown in the results slot", async () => {
    answer({
      "/credentials": [KEYS[0]],
      "/hiring-signals/search": new FakeApiError(409, "Add a search key first.", { code: "SETUP_REQUIRED" }),
    });
    const { container } = await draw();
    await search(container);
    expect(callsTo("POST /hiring-signals/search")).toHaveLength(1);
    expect(words(container)).toContain("Add a search key first.");
    expect(container.querySelectorAll(".bj-state--setup")).toHaveLength(1);
    expect(words(band(container))).toContain("Add a Brave, Serper, Firecrawl or You.com key first.");
  });

  it("lights the same row when the credentials held a key but the search answered setup-required", async () => {
    answer({ "/hiring-signals/search": new FakeApiError(409, "No usable search key.", { code: "SETUP_REQUIRED" }) });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("Hiring signals searches with your own search provider.");
    expect(query<HTMLAnchorElement>(container, ".bj-state--setup a").getAttribute("href")).toBe("/settings#search-keys");
  });
});

describe("what the global rows decide here", () => {
  it("never reads a profile and never sends anyone to set one up: this page needs none", async () => {
    answer({ "/profile/current": new FakeApiError(404, "no profile", { code: "NOT_FOUND" }) });
    const { container } = await draw();
    expect(callsTo("GET /profile/current")).toEqual([]);
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
    expect(words(band(container))).not.toContain("profile");
    expect(container.querySelector("form")).not.toBeNull();
    expect(cyan(container)).toHaveLength(1);
  });

  it("never asks for a model key: a person with no model key is not sent to add one", async () => {
    answer({ "/credentials": [KEYS[1]] });
    const { container } = await draw();
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
  });

  it("says the setup check failed when the credentials could not be read, and Try again re-reads", async () => {
    let attempt = 0;
    answer({
      "/credentials": () => {
        if (++attempt === 1) throw new FakeApiError(500, "boom");
        return KEYS;
      },
    });
    const { container } = await draw();
    expect(words(band(container))).toContain("Couldn't check your setup.");
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
  });
});

describe("a failed status read together with a failed setup check", () => {
  it("lets one Try again re-ask both, so neither is left without a way back", async () => {
    let statusAttempts = 0;
    let keyAttempts = 0;
    answer({
      "/hiring-signals/status": () => {
        if (++statusAttempts === 1) throw new FakeApiError(503, "down");
        return { enabled: true };
      },
      "/credentials": () => {
        if (++keyAttempts === 1) throw new FakeApiError(500, "boom");
        return KEYS;
      },
    });
    const { container } = await draw();
    expect(words(band(container))).toContain("Couldn't check your setup.");
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(callsTo("GET /hiring-signals/status")).toHaveLength(2);
    expect(callsTo("GET /credentials")).toHaveLength(2);
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
    expect(container.querySelector("form")).not.toBeNull();
  });
});

describe("a search", () => {
  it("goes out with the role, location, window and no wording for Auto, once", async () => {
    answer({ "/hiring-signals/search": RESULT() });
    const { container } = await draw();
    type(query(form(container), "#hst-query"), "software engineer");
    type(query(form(container), "#hst-location"), "Pune");
    act(() => form(container).requestSubmit());
    await flush();
    const post = api.apiFetch.mock.calls.find(([path]) => path === "/hiring-signals/search");
    expect(JSON.parse(post![1].body)).toEqual({ query: "software engineer", location: "Pune", freshness: "3days" });
    expect(callsTo("POST /hiring-signals/search")).toHaveLength(1);
  });

  it("says Searching... in the band with no button, and the form's Search reads Searching... aria-disabled, while it is out", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/hiring-signals/search": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("Searching for recent hiring posts...");
    expect(words(band(container))).toContain("why: searching with your own search keys");
    expect(band(container).querySelector("button, a")).toBeNull();
    const busy = buttonNamed(form(container), "Searching...");
    expect(busy.getAttribute("aria-disabled")).toBe("true");
    expect(busy.hasAttribute("disabled")).toBe(false);
    expect(cyan(container)).toHaveLength(0);
    await act(async () => release(RESULT()));
    await flush();
    expect(container.querySelectorAll("li.bj-hs-card")).toHaveLength(2);
  });

  it("answers with results, and lights Save this search with the counts when it is not saved", async () => {
    answer({ "/hiring-signals/search": RESULT() });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("Save this search to run it again later. It won't run on its own.");
    expect(words(band(container))).toContain("why: 2 shown · 3 hidden · search not saved");
    expect(words(query(band(container), "button.bj-primary"))).toBe("Save this search");
    expect(cyan(container)).toHaveLength(1);
    expect(form(container).querySelector("button.bj-primary")).toBeNull();
  });

  it("adds 'from a recent cached search' to the why line for a cached answer", async () => {
    answer({ "/hiring-signals/search": RESULT({ cached: true }) });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("search not saved · from a recent cached search");
  });

  it("leaves the hidden clause out when the server's counts could not be read", async () => {
    answer({ "/hiring-signals/search": RESULT({ counts: { raw_hits: 1 } }) });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("why: 2 shown · search not saved");
  });

  it("is lit by Save this search for a result with no posts: an empty search can be saved", async () => {
    answer({ "/hiring-signals/search": RESULT({ signals: [], counts: { ...COUNTS, raw_hits: 0, duplicates: 0, role_mismatch_hidden: 0, shown: 0 } }) });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("why: 0 shown · 0 hidden · search not saved");
  });

  it("goes back to Search when the role, location, window or wording is changed after the results", async () => {
    answer({ "/hiring-signals/search": RESULT() });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("Save this search");
    type(query(form(container), "#hst-location"), "Pune");
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
    expect(words(band(container))).not.toContain("hidden");
    type(query(form(container), "#hst-location"), "");
    expect(words(band(container))).toContain("Save this search to run it again later.");
    click(buttonNamed(form(container), "7 days"));
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
  });
});

describe("saving the search from the band", () => {
  it("is the same request as the form's Save this search: a POST of the role and location, and then the band is unlit", async () => {
    answer({
      "/hiring-signals/search": RESULT(),
      "POST /hiring-signals/searches": { id: "hs-2", query: "software engineer", location: null, created_at: "2026-09-22T00:00:00Z" },
    });
    const { container } = await draw();
    await search(container);
    click(buttonNamed(band(container), "Save this search"));
    await flush();
    const post = api.apiFetch.mock.calls.find(([path, init]) => path === "/hiring-signals/searches" && init?.method === "POST");
    expect(JSON.parse(post![1].body)).toEqual({ query: "software engineer", location: null });
    const text = words(band(container));
    expect(text).toContain("Open the posts worth a closer look.");
    expect(text).toContain("why: 2 shown · 3 hidden · search saved");
    expect(band(container).querySelector("button, a")).toBeNull();
    expect(cyan(container)).toHaveLength(0);
    expect(words(container)).toContain("Search saved.");
  });

  it("is also what the form's own Save this search does", async () => {
    answer({
      "/hiring-signals/search": RESULT(),
      "POST /hiring-signals/searches": { id: "hs-2", query: "software engineer", location: null, created_at: "2026-09-22T00:00:00Z" },
    });
    const { container } = await draw();
    await search(container);
    click(buttonNamed(form(container), "Save this search"));
    await flush();
    expect(callsTo("POST /hiring-signals/searches")).toHaveLength(1);
    expect(words(band(container))).toContain("search saved");
  });

  it("reads Saving... on the band's button while the request is out, and ignores a second press", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/hiring-signals/search": RESULT(), "POST /hiring-signals/searches": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    await search(container);
    click(buttonNamed(band(container), "Save this search"));
    await flush();
    const busy = buttonNamed(band(container), "Saving...");
    expect(busy.getAttribute("aria-disabled")).toBe("true");
    click(busy);
    expect(callsTo("POST /hiring-signals/searches")).toHaveLength(1);
    await act(async () => release({ id: "hs-2", query: "software engineer", location: null, created_at: "2026-09-22T00:00:00Z" }));
    await flush();
  });

  it("shows a refusal (the 25-search cap) in the server's words directly under the band, and keeps the band's step", async () => {
    answer({
      "/hiring-signals/search": RESULT(),
      "POST /hiring-signals/searches": new FakeApiError(422, "You can keep at most 25 saved searches. Delete one to save another.", { code: "INVALID_INPUT" }),
    });
    const { container } = await draw();
    await search(container);
    click(buttonNamed(band(container), "Save this search"));
    await flush();
    const alert = query(container, ".bj-hs-save-error [role='alert']");
    expect(words(alert)).toContain("You can keep at most 25 saved searches. Delete one to save another.");
    expect(band(container).compareDocumentPosition(alert) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(words(band(container))).toContain("Save this search to run it again later.");
    expect(container.querySelectorAll("[role='alert']")).toHaveLength(1);
  });

  it("is already saved when an equivalent search is on the list: case and spacing ignored", async () => {
    answer({ "/hiring-signals/search": RESULT({ query_label: "x" }), "/hiring-signals/searches": { searches: [{ id: "hs-9", query: "Software  Engineer", location: null, created_at: "2026-09-01T00:00:00Z" }] } });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("Open the posts worth a closer look.");
    expect(words(band(container))).toContain("search saved");
  });

  it("cannot tell, and says so unlit, when the saved searches could not be loaded", async () => {
    answer({ "/hiring-signals/search": RESULT(), "/hiring-signals/searches": new FakeApiError(500, "down") });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("why: 2 shown · 3 hidden · saved searches couldn't load");
    expect(band(container).querySelector("button, a")).toBeNull();
  });
});

describe("a failed search", () => {
  it("is the page's own data failing when trying again can help: the band names the call and Try again re-runs the same search", async () => {
    let attempt = 0;
    answer({
      "/hiring-signals/search": () => {
        if (++attempt === 1) throw new FakeApiError(503, "Every search provider failed.", { code: "PROVIDER_UNAVAILABLE", retryable: true });
        return RESULT();
      },
    });
    const { container } = await draw();
    await search(container);
    const text = words(band(container));
    expect(text).toContain("Couldn't finish the search. You didn't do anything wrong, and nothing was lost.");
    expect(text).toMatch(/why: POST \/hiring-signals\/search failed · \d\d:\d\d/);
    expect(words(query(container, ".bj-state--failure"))).toContain("Every search provider failed.");
    expect(query(container, ".bj-state--failure").querySelector("button")).toBeNull();
    click(buttonNamed(band(container), "Try again"));
    await flush();
    const posts = api.apiFetch.mock.calls.filter(([path]) => path === "/hiring-signals/search").map(([, init]) => init.body);
    expect(posts).toHaveLength(2);
    expect(posts[0]).toBe(posts[1]);
    expect(container.querySelectorAll("li.bj-hs-card")).toHaveLength(2);
  });

  it("keeps its message in the results block, with no Try again, and the band at Search, when trying again cannot help", async () => {
    answer({ "/hiring-signals/search": new FakeApiError(422, "The role is too long.", { code: "INVALID_INPUT" }) });
    const { container } = await draw();
    await search(container);
    expect(words(query(container, ".bj-state--failure"))).toContain("The role is too long.");
    expect([...container.querySelectorAll("button")].filter((button) => words(button) === "Try again")).toEqual([]);
    expect(words(band(container))).toContain("Search recent public hiring posts for a role.");
    expect(words(query(band(container), "button.bj-primary"))).toBe("Search");
  });

  it("is a switched-off note when any request answers FEATURE_DISABLED while the page is open", async () => {
    answer({ "/hiring-signals/search": new FakeApiError(404, "off", { code: "FEATURE_DISABLED" }) });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("Hiring signals is switched off on this server.");
    expect(container.querySelector("form")).toBeNull();
  });

  it("is a failure callout on a saved post that could not be saved, and the band is not changed", async () => {
    answer({ "/hiring-signals/search": RESULT(), "POST /hiring-signals/saves": new FakeApiError(500, "Could not save that post.") });
    const { container } = await draw();
    await search(container);
    const before = words(band(container));
    click(buttonNamed(container, /^Save post/));
    await flush();
    expect(words(container)).toContain("Could not save that post.");
    expect(words(band(container))).toBe(before);
  });
});

describe("the saved lists, once the feature is on", () => {
  it("lists a saved search with its trail, and a saved post with its trail", async () => {
    answer({
      "/hiring-signals/saves": { saves: [{ id: "sv-1", activity_id: ID, post_url: `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`, embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`, created_at: "2026-09-12T10:00:00Z" }] },
    });
    const { container } = await draw();
    expect([...container.querySelectorAll(".bj-trail--static")].map((line) => words(line))).toEqual(["● You saved this · Sep 10", "● You saved this · Sep 12"]);
  });

  it("re-runs a saved search from its Run button, with its role and location in the boxes", async () => {
    answer({ "/hiring-signals/search": RESULT() });
    const { container } = await draw();
    click(buttonNamed(container, /^Run saved search/));
    await flush();
    expect(query<HTMLInputElement>(form(container), "#hst-query").value).toBe("data scientist");
    expect(callsTo("POST /hiring-signals/search")).toHaveLength(1);
  });
});

describe("focus and accessibility", () => {
  it("holds focus on the band's sentence when the band's Search was pressed and its button went away", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/hiring-signals/search": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    type(query(form(container), "#hst-query"), "software engineer");
    const lit = query<HTMLButtonElement>(band(container), "button.bj-primary");
    act(() => lit.focus());
    click(lit);
    await flush();
    expect(document.activeElement?.id).toBe("next-step-focus");
    await act(async () => release(RESULT()));
    await flush();
    // The band's step changed to Save this search: focus goes to its button, not to the top of the page.
    expect(document.activeElement?.id).toBe("next-step-focus");
    expect(document.activeElement?.tagName).toBe("BUTTON");
    expect(words(document.activeElement)).toBe("Save this search");
  });

  it("holds focus on the band across Try again on a failed setup check, and lands on the next step's button", async () => {
    let attempt = 0;
    answer({
      "/credentials": () => {
        if (++attempt === 1) throw new FakeApiError(500, "boom");
        return KEYS;
      },
    });
    const { container } = await draw();
    const retry = query<HTMLButtonElement>(band(container), "button.bj-primary");
    act(() => retry.focus());
    click(retry);
    await flush();
    expect(document.activeElement?.id).toBe("next-step-focus");
    expect(words(document.activeElement)).toBe("Search");
  });

  it("leaves focus where it was when the search was started from a field", async () => {
    answer({ "/hiring-signals/search": RESULT() });
    const { container } = await draw();
    const box = query<HTMLInputElement>(form(container), "#hst-query");
    act(() => box.focus());
    await search(container);
    expect(document.activeElement).toBe(box);
  });

  it("holds exactly one page heading and, when ready, one cyan fill", async () => {
    const { container } = await draw();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(cyan(container)).toHaveLength(1);
  });

  it("has no serious or critical axe violation before a search and with results", async () => {
    answer({ "/hiring-signals/search": RESULT() });
    const { container } = await draw();
    await expectNoSeriousA11yViolations(container);
    await search(container);
    await expectNoSeriousA11yViolations(container);
  });
});
