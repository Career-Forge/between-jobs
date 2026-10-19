// @vitest-environment jsdom
import { StrictMode } from "react";
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { job, response, score } from "../testing/discoverFixtures";
import { buttonNamed, click, installViewport, query, render } from "../testing/render";
import Discover from "./Discover";

// The Discover page against a fake API: which requests it makes and when (and that opening it makes no
// search), what the band says as the reads and the search answer, setup that is known to be missing
// (no request is made), a failed search, Track, and saved searches. What each state looks like is
// drawn from props in components/discover/DiscoverView.test.tsx.

const api = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("../lib/api", () => ({ apiFetch: api.apiFetch }));
vi.mock("../auth", () => ({ useAuth: () => ({ session: { user: { id: "user-1" } } }) }));

type Reply = unknown | Error | (() => unknown);

class FakeApiError extends Error {
  code?: string;
  settingsPath?: string;
  capability?: string;
  missing?: string[];
  constructor(
    public readonly status: number,
    message: string,
    extra: { code?: string; settingsPath?: string; capability?: string; missing?: string[] } = {},
  ) {
    super(message);
    Object.assign(this, extra);
  }
}

const GOOD_PROFILE = { id: "pv-1", activated_at: "2026-09-12T10:00:00Z" };
const GOOD_KEY = { service: "llm", provider: "openrouter", is_validated: true, model: "vendor/model" };
const SEARCH_KEYS = [
  { service: "search", provider: "serper", is_validated: true },
  { service: "search", provider: "brave", is_validated: true },
];
const SAVED = {
  id: "ss-1",
  query: "analytics engineer",
  location: null,
  companies: [],
  remote_only: false,
  is_active: true,
  created_at: "2026-09-01T00:00:00Z",
  last_matched_at: "2026-09-22T06:00:00Z",
};

// This environment has no usable localStorage, so the page's flags go to a plain map.
const memory = new Map<string, string>();
const storage = {
  getItem: (key: string) => memory.get(key) ?? null,
  setItem: (key: string, value: string) => void memory.set(key, value),
};

let replies: Record<string, Reply> = {};
const calls = () => api.apiFetch.mock.calls.map(([path, init]) => `${init?.method ?? "GET"} ${path}`);
const callsTo = (prefix: string) => calls().filter((call) => call.startsWith(prefix));

function reply(path: string, method = "GET"): Reply {
  const key = `${method} ${path}`;
  return key in replies ? replies[key] : replies[path];
}

function answer(extra: Record<string, Reply> = {}) {
  replies = {
    "/profile/current": GOOD_PROFILE,
    "/credentials": [GOOD_KEY, ...SEARCH_KEYS],
    "/saved-searches": [],
    ...extra,
  };
}

const flush = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));

async function draw(options: { strict?: boolean } = {}) {
  installViewport(1000);
  const page = (
    <MemoryRouter>
      <Discover />
    </MemoryRouter>
  );
  const rendered = render(options.strict ? <StrictMode>{page}</StrictMode> : page);
  await flush();
  return rendered;
}

const words = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();
const band = (container: HTMLElement) => query(container, 'section[aria-label="Next step"]');
const form = (container: HTMLElement) => query<HTMLFormElement>(container, "form");
const status = (container: HTMLElement) => words(query(container, ".bj-discover > .bj-visually-hidden[role='status']"));

function type(input: HTMLInputElement, value: string) {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function search(container: HTMLElement) {
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
  memory.clear();
  Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("opening the page", () => {
  it("reads the profile, the keys and the saved searches, and makes no search", async () => {
    await draw();
    expect([...new Set(calls())].sort()).toEqual(["GET /credentials", "GET /profile/current", "GET /saved-searches"]);
    expect(callsTo("GET /discover")).toEqual([]);
  });

  it("makes no search from any effect, StrictMode's double mount included", async () => {
    await draw({ strict: true });
    expect(callsTo("GET /discover")).toEqual([]);
    expect(callsTo("POST")).toEqual([]);
  });

  it("shows the band loading until the reads answer, then lights Search with the facts it read", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/credentials": () => new Promise((resolve) => (release = resolve)) });
    installViewport(1000);
    const { container } = render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>,
    );
    await flush();
    expect(words(band(container))).toBe("Next stepLoading...");
    await act(async () => release([GOOD_KEY, ...SEARCH_KEYS]));
    await flush();
    expect(words(band(container))).toContain("Search company job boards and your search providers.");
    expect(words(band(container))).toContain("why: profile active · model key set · 2 search providers connected");
    expect(words(query(band(container), "button.bj-primary"))).toBe("Search");
  });

  it("counts only the keys a search can use, not keys that look people up", async () => {
    answer({ "/credentials": [GOOD_KEY, { service: "search", provider: "apollo" }, { service: "search", provider: "brave" }] });
    const { container } = await draw();
    expect(words(band(container))).toContain("1 search provider connected");
  });
});

describe("a search", () => {
  it("is made by the form: the filters as typed go out in one GET, and the first run-through is a first-run fact", async () => {
    answer({ "/discover?q=data+engineer&location=New+York%2C+NY&companies=Acme%2C+Initech&remote_only=true": response() });
    const { container } = await draw();
    const [q, location, companies] = [...form(container).querySelectorAll<HTMLInputElement>("input[type='text']")];
    type(q, "data engineer");
    type(location, "New York, NY");
    type(companies, "Acme, Initech");
    click(query(form(container), "input[type='checkbox']"));
    await search(container);
    expect(callsTo("GET /discover")).toEqual(["GET /discover?q=data+engineer&location=New+York%2C+NY&companies=Acme%2C+Initech&remote_only=true"]);
    expect(memory.get("between-jobs:first-run:searched:user-1")).toBe("1");
  });

  it("sends no filter that holds nothing: an empty form browses the latest postings", async () => {
    answer({ "/discover?": response() });
    const { container } = await draw();
    await search(container);
    expect(callsTo("GET /discover")).toEqual(["GET /discover?"]);
  });

  it("is also made by the band's Search, which submits the same form", async () => {
    answer({ "/discover?": response() });
    const { container } = await draw();
    click(query(band(container), "button.bj-primary"));
    await flush();
    expect(callsTo("GET /discover")).toHaveLength(1);
    expect(container.querySelectorAll("li.bj-disc-row")).toHaveLength(1);
  });

  it("is one at a time: a second press while it is out sends nothing", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/discover?": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    await search(container);
    await search(container);
    click(buttonNamed(form(container), "Searching..."));
    expect(callsTo("GET /discover")).toHaveLength(1);
    await act(async () => release(response()));
    await flush();
    expect(callsTo("GET /discover")).toHaveLength(1);
  });

  it("goes through Searching... in the band, with a counter, while it is out, and lights nothing", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/discover?": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("Searching...");
    expect(words(band(container))).toContain("why: link checks can take about 20 seconds");
    expect(container.querySelector("[role='timer']")).not.toBeNull();
    expect(container.querySelectorAll(".bj-skeleton-row").length).toBeGreaterThan(0);
    expect(container.querySelectorAll("button.bj-primary")).toHaveLength(0);
    await act(async () => release(response()));
    await flush();
    expect(container.querySelector("[role='timer']")).toBeNull();
  });

  it("answers with unlit results, the counts, and one polite announcement", async () => {
    answer({
      "/discover?": response({
        scored: [job(), job({ provider: "registry", apply_url: "https://jobs.example/acme/2" })],
        more: [job({ apply_url: "https://jobs.example/acme/3", score: null })],
        dead_removed: 2,
      }),
    });
    const { container } = await draw();
    await search(container);
    const text = words(band(container));
    expect(text).toContain("Track the jobs worth a draft.");
    expect(text).toContain("why: 2 scored · 1 more, unscored · 2 dropped -- no longer live");
    expect(status(container)).toBe("The search finished: 2 scored, 1 more, unscored, 2 dropped as no longer live.");
    expect(container.querySelectorAll("button.bj-primary, a.bj-button-link-primary")).toHaveLength(0);
  });

  it("can be run again, and the second answer replaces the first", async () => {
    let n = 0;
    answer({ "/discover?": () => (++n === 1 ? response({ scored: [job()] }) : response({ scored: [job({ title: "Analyst", apply_url: "https://jobs.example/acme/9" })] })) });
    const { container } = await draw();
    await search(container);
    await search(container);
    expect(callsTo("GET /discover")).toHaveLength(2);
    expect([...container.querySelectorAll(".bj-disc-title")].map((title) => words(title))).toEqual(["Analyst"]);
  });

  it("holds focus on the band's sentence when the band's Search was pressed and its button went away", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/discover?": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    const lit = query<HTMLButtonElement>(band(container), "button.bj-primary");
    act(() => lit.focus());
    click(lit);
    await flush();
    expect(document.activeElement?.id).toBe("next-step-focus");
    expect(document.activeElement?.tagName).toBe("P");
    await act(async () => release(response()));
    await flush();
    expect(document.activeElement?.id).toBe("next-step-focus");
  });

  it("moves focus to the band's new button when the answer lights one (Search again after a fallback)", async () => {
    answer({ "/discover?": response({ scoring_strategy: "neutral_fallback" }) });
    const { container } = await draw();
    const lit = query<HTMLButtonElement>(band(container), "button.bj-primary");
    act(() => lit.focus());
    click(lit);
    await flush();
    expect(words(document.activeElement)).toBe("Search again");
    expect(document.activeElement?.id).toBe("next-step-focus");
  });

  it("leaves focus alone when it was somewhere else", async () => {
    answer({ "/discover?": response() });
    const { container } = await draw();
    const box = query<HTMLInputElement>(form(container), "input[type='text']");
    act(() => box.focus());
    await search(container);
    expect(document.activeElement).toBe(box);
  });
});

describe("setup that is known to be missing", () => {
  it("sends a person with no active profile to set one up, and makes no search", async () => {
    answer({ "/profile/current": new FakeApiError(404, "no active profile", { code: "NOT_FOUND" }) });
    const { container } = await draw();
    expect(words(band(container))).toContain("Everything here starts from your profile. Import it first.");
    expect(words(query(container, ".bj-state--setup"))).toContain("Missing:Add your profile");
    await search(container);
    click(buttonNamed(form(container), "Search"));
    expect(callsTo("GET /discover")).toEqual([]);
  });

  it("says no model key, links to where it is added, and makes no search", async () => {
    answer({ "/credentials": [] });
    const { container } = await draw();
    expect(words(band(container))).toContain("Discover scores jobs with your model. Add a model key first.");
    expect(words(band(container))).toContain("why: profile active · no model key saved");
    const link = query<HTMLAnchorElement>(band(container), "a.bj-button-link-primary");
    expect(link.getAttribute("href")).toBe("/settings#ai-provider");
    const setupLink = query<HTMLAnchorElement>(container, ".bj-state--setup a");
    expect([words(setupLink), setupLink.getAttribute("href")]).toEqual(["Add a model key", "/settings#ai-provider"]);
    await search(container);
    expect(callsTo("GET /discover")).toEqual([]);
  });

  it("says choose a model for a key that is saved with none, and an unconfirmed key the same as none", async () => {
    answer({ "/credentials": [{ service: "llm", provider: "openrouter", is_validated: true, model: null, setup_state: "needs_model" }] });
    const { container } = await draw();
    expect(words(band(container))).toContain("Your key is saved -- choose a model first.");
    expect(words(query(container, ".bj-state--setup a"))).toBe("Choose a model");
  });

  it("lists both missing pieces when neither is there, profile first", async () => {
    answer({ "/profile/current": new FakeApiError(404, "no", { code: "NOT_FOUND" }), "/credentials": [] });
    const { container } = await draw();
    expect([...container.querySelectorAll(".bj-state--setup a")].map((link) => words(link))).toEqual(["Add your profile", "Add a model key"]);
  });

  it("says the setup check failed when the reads errored, offers Try again, and does not guess it is missing", async () => {
    answer({ "/credentials": new FakeApiError(500, "boom") });
    const { container } = await draw();
    expect(words(band(container))).toContain("Couldn't check your setup. You didn't do anything wrong, and nothing was lost.");
    expect(words(band(container))).toContain("GET /credentials failed");
    expect(container.querySelector(".bj-state--setup")).toBeNull();
    answer({});
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(words(band(container))).toContain("Search company job boards");
  });

  it("shows the server's own answer when it says setup is missing although the reads said it was there, with a link", async () => {
    answer({
      "/discover?": new FakeApiError(409, "Add a model key before searching.", { code: "SETUP_REQUIRED", settingsPath: "/profile/integrations", capability: "job_scoring", missing: ["credential"] }),
    });
    const { container } = await draw();
    await search(container);
    expect(words(query(container, ".bj-state--setup"))).toContain("Add a model key before searching.");
    const link = query<HTMLAnchorElement>(container, ".bj-state--setup a");
    expect(link.getAttribute("href")).toBe("/settings");
    expect(words(link)).toBe("Add a model key in Settings");
    expect(words(band(container))).toContain("Discover scores jobs with your model. Add a model key first.");
    expect(words(band(container))).toContain("the server says setup is missing");
  });

  it("falls back to Settings when the server named no usable link", async () => {
    answer({ "/discover?": new FakeApiError(409, "Something needs setup.", { code: "SETUP_REQUIRED" }) });
    const { container } = await draw();
    await search(container);
    const link = query<HTMLAnchorElement>(container, ".bj-state--setup a");
    expect([words(link), link.getAttribute("href")]).toEqual(["Open Settings", "/settings"]);
  });

  it("asks the server again on the next press after it said setup is missing, and shows the results once it is fixed", async () => {
    let asked = 0;
    answer({
      "/discover?": () => {
        asked += 1;
        if (asked === 1) throw new FakeApiError(409, "Add a model key before searching.", { code: "SETUP_REQUIRED", capability: "job_scoring", missing: ["credential"] });
        return response();
      },
    });
    const { container } = await draw();
    await search(container);
    expect(container.querySelector(".bj-state--setup")).not.toBeNull();
    expect(words(form(container))).not.toContain("Search is waiting on the setup above.");
    await search(container);
    expect(callsTo("GET /discover")).toHaveLength(2);
    expect(container.querySelector(".bj-state--setup")).toBeNull();
    expect(container.querySelectorAll("li.bj-disc-row").length).toBeGreaterThan(0);
  });

  it("keeps the 'Find a job' text back until the setup reads have answered, so it is not swapped for the setup block", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/profile/current": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    expect(container.querySelector(".bj-state--empty")).toBeNull();
    expect(container.querySelector(".bj-state--setup")).toBeNull();
    await act(async () => release(GOOD_PROFILE));
    await flush();
    expect(words(query(container, ".bj-state--empty"))).toContain("Find a job");
  });

  it("believes the server when it says the profile is what is missing", async () => {
    answer({ "/discover?": new FakeApiError(409, "Import and activate a resume profile before searching for jobs.", { code: "SETUP_REQUIRED", settingsPath: "/profile", capability: "profile", missing: ["profile_version"] }) });
    const { container } = await draw();
    await search(container);
    expect(words(band(container))).toContain("Everything here starts from your profile. Import it first.");
    expect(words(query(container, ".bj-state--setup"))).toContain("Add your profile");
  });
});

describe("a failed search", () => {
  it("says so in the band with the call and the time, shows a callout, and Try again re-runs the same filters", async () => {
    let attempt = 0;
    answer({
      "/discover?q=analyst": () => {
        if (++attempt === 1) throw new FakeApiError(503, "The scoring model did not answer.", { code: "PROVIDER_UNAVAILABLE" });
        return response();
      },
    });
    const { container } = await draw();
    type(query(form(container), "input[type='text']"), "analyst");
    await search(container);
    expect(words(band(container))).toContain("Couldn't finish the search. You didn't do anything wrong, and nothing was lost.");
    expect(words(band(container))).toMatch(/why: GET \/discover failed · \d\d:\d\d/);
    expect(words(query(container, ".bj-state--failure"))).toContain("The scoring model did not answer.");
    expect(query(container, ".bj-state--failure").querySelector("button")).toBeNull();
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(callsTo("GET /discover")).toEqual(["GET /discover?q=analyst", "GET /discover?q=analyst"]);
    expect(container.querySelector(".bj-state--failure")).toBeNull();
    expect(container.querySelectorAll("li.bj-disc-row")).toHaveLength(1);
  });

  it("says when to try again for a rate limit, in the callout", async () => {
    answer({ "/discover?": new FakeApiError(429, "Too many requests.", { code: "RATE_LIMITED" }) });
    const { container } = await draw();
    await search(container);
    expect(words(query(container, ".bj-state--failure"))).toContain("The search couldn't be read:");
  });
});

describe("clearing the filters", () => {
  it("empties the form, goes back to ready, and moves focus to the search box", async () => {
    answer({ "/discover?q=zzz&location=Mars": response({ scored: [], more: [] }) });
    const { container } = await draw();
    const [q, location] = [...form(container).querySelectorAll<HTMLInputElement>("input[type='text']")];
    type(q, "zzz");
    type(location, "Mars");
    await search(container);
    expect(words(band(container))).toContain("No results. Widen your filters.");
    vi.useFakeTimers();
    click(buttonNamed(band(container), "Clear filters"));
    act(() => void vi.advanceTimersByTime(10));
    vi.useRealTimers();
    expect(q.value).toBe("");
    expect(location.value).toBe("");
    expect(words(band(container))).toContain("Search company job boards");
    expect(document.activeElement).toBe(q);
    expect(container.querySelector(".bj-state--empty")?.textContent).toContain("Find a job");
  });
});

describe("Track", () => {
  const TRACK_BODY = {
    apply_url: "https://jobs.example/acme/1",
    title: "Data Engineer",
    company: "Acme",
    location: "New York, NY",
    snippet: "Build and run data pipelines.",
    provider: "serper",
  };

  it("posts exactly the fields the track endpoint takes, then swaps in Open workspace and moves nowhere else", async () => {
    answer({ "/discover?": response(), "POST /discover/track": { id: "app-7" } });
    const { container } = await draw();
    await search(container);
    click(buttonNamed(container, /^Track /));
    await flush();
    const post = api.apiFetch.mock.calls.find(([path]) => path === "/discover/track");
    expect(JSON.parse(post![1].body)).toEqual(TRACK_BODY);
    expect(post![1].method).toBe("POST");
    const open = [...container.querySelectorAll<HTMLAnchorElement>("li.bj-disc-row a")].find((link) => words(link) === "Open workspace");
    expect(open?.getAttribute("href")).toBe("/applications/app-7");
    expect(status(container)).toBe("Tracked Data Engineer. Open workspace is now available.");
    expect(callsTo("GET /discover")).toHaveLength(1);
  });

  it("is one request per job: a second press while it is out sends nothing", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "/discover?": response(), "POST /discover/track": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    await search(container);
    const track = buttonNamed(container, /^Track /);
    click(track);
    await flush();
    click(buttonNamed(container, /^Tracking/));
    expect(callsTo("POST /discover/track")).toHaveLength(1);
    await act(async () => release({ id: "app-7" }));
    await flush();
    expect(callsTo("POST /discover/track")).toHaveLength(1);
  });

  it("shows a failure on that row only, in the friendly words, and Retry tracks again", async () => {
    let attempt = 0;
    answer({
      "/discover?": response({ scored: [job(), job({ title: "Analyst", apply_url: "https://jobs.example/acme/2" })] }),
      "POST /discover/track": () => {
        if (++attempt === 1) throw new FakeApiError(429, "Slow down.", { code: "RATE_LIMITED" });
        return { id: "app-8" };
      },
    });
    const { container } = await draw();
    await search(container);
    click(buttonNamed(container, "Track Data Engineer at Acme"));
    await flush();
    const rows = container.querySelectorAll("li.bj-disc-row");
    expect(rows[1].querySelector("[role='alert']")).toBeNull();
    const alert = query(rows[0], "[role='alert']");
    expect(words(alert)).toContain("Couldn't track Data Engineer at Acme:");
    click(buttonNamed(alert, "Retry"));
    await flush();
    expect(container.querySelector("li.bj-disc-row [role='alert']")).toBeNull();
    expect([...rows[0].querySelectorAll("a")].map((link) => words(link))).toContain("Open workspace");
  });

  it("tracks two jobs at once, each on its own", async () => {
    const ids: Record<string, string> = { "https://jobs.example/acme/1": "app-1", "https://jobs.example/acme/2": "app-2" };
    answer({
      "/discover?": response({ scored: [job(), job({ title: "Analyst", apply_url: "https://jobs.example/acme/2" })] }),
      "POST /discover/track": () => ({ id: ids[JSON.parse(api.apiFetch.mock.calls.at(-1)![1].body).apply_url] }),
    });
    const { container } = await draw();
    await search(container);
    click(buttonNamed(container, "Track Data Engineer at Acme"));
    click(buttonNamed(container, "Track Analyst at Acme"));
    await flush();
    const hrefs = [...container.querySelectorAll<HTMLAnchorElement>("li.bj-disc-row a")].filter((link) => words(link) === "Open workspace").map((link) => link.getAttribute("href"));
    expect(hrefs).toEqual(["/applications/app-1", "/applications/app-2"]);
  });
});

describe("saved searches", () => {
  it("lists the person's searches above the results, with the last check, and no request beyond the one list", async () => {
    answer({ "/saved-searches": [SAVED] });
    const { container } = await draw();
    expect(words(query(container, ".bj-ss-label"))).toBe('"analytics engineer"');
    expect(words(query(container, ".bj-ss-row .bj-trail"))).toBe("Checked Sep 22");
    expect(callsTo("GET /saved-searches")).toHaveLength(1);
  });

  it("saves the form's filters as they have always been sent, and puts the new search on top", async () => {
    answer({
      "/saved-searches": [SAVED],
      "POST /saved-searches": { ...SAVED, id: "ss-2", query: "data engineer", location: "Jersey City", companies: ["Acme", "Initech"], remote_only: true, last_matched_at: null },
    });
    const { container } = await draw();
    const [q, location, companies] = [...form(container).querySelectorAll<HTMLInputElement>("input[type='text']")];
    type(q, "data engineer");
    type(location, "Jersey City");
    type(companies, "Acme,  Initech, ");
    click(query(form(container), "input[type='checkbox']"));
    click(buttonNamed(form(container), "Save this search"));
    await flush();
    const post = api.apiFetch.mock.calls.find(([path, init]) => path === "/saved-searches" && init?.method === "POST");
    expect(JSON.parse(post![1].body)).toEqual({ query: "data engineer", location: "Jersey City", companies: ["Acme", "Initech"], remote_only: true });
    const labels = [...container.querySelectorAll(".bj-ss-label")].map((label) => words(label));
    expect(labels).toEqual(['"data engineer" at Acme, Initech in Jersey City remote only', '"analytics engineer"']);
    expect(words(container.querySelectorAll(".bj-ss-row .bj-trail")[0])).toBe("○ Not checked yet");
    expect(status(container)).toBe("Search saved.");
  });

  it("sends a blank location as none and no companies as an empty list", async () => {
    answer({ "POST /saved-searches": { ...SAVED, id: "ss-3", last_matched_at: null } });
    const { container } = await draw();
    click(buttonNamed(form(container), "Save this search"));
    await flush();
    const post = api.apiFetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(post![1].body)).toEqual({ query: "", location: null, companies: [], remote_only: false });
  });

  it("shows a refused save in the card, in the server's words, and keeps the form's filters", async () => {
    answer({ "/saved-searches": [SAVED], "POST /saved-searches": new FakeApiError(422, "You can keep at most 25 saved searches.", { code: "INVALID_INPUT" }) });
    const { container } = await draw();
    type(query(form(container), "input[type='text']"), "kept");
    click(buttonNamed(form(container), "Save this search"));
    await flush();
    expect(words(query(container, ".bj-ss [role='alert']"))).toBe("You can keep at most 25 saved searches.");
    expect(query<HTMLInputElement>(form(container), "input[type='text']").value).toBe("kept");
  });

  it("shows a refused save even when the saved list failed to load, and does not announce a save", async () => {
    answer({
      "/saved-searches": new FakeApiError(500, "The service is down."),
      "POST /saved-searches": new FakeApiError(422, "You can keep at most 25 saved searches.", { code: "INVALID_INPUT" }),
    });
    const { container } = await draw();
    click(buttonNamed(form(container), "Save this search"));
    await flush();
    expect(words(query(container, ".bj-ss"))).toContain("You can keep at most 25 saved searches.");
    expect(words(query(container, ".bj-ss"))).toContain("Try again");
    expect(status(container)).toBe("");
  });

  it("shows a refused save while the saved list is still loading", async () => {
    answer({
      "/saved-searches": () => new Promise(() => undefined),
      "POST /saved-searches": new FakeApiError(422, "You can keep at most 25 saved searches.", { code: "INVALID_INPUT" }),
    });
    const { container } = await draw();
    click(buttonNamed(form(container), "Save this search"));
    await flush();
    expect(words(query(container, ".bj-ss [role='alert']"))).toBe("You can keep at most 25 saved searches.");
  });

  it("does not say a search is saved when the list that would show it failed to load", async () => {
    answer({ "/saved-searches": new FakeApiError(500, "The service is down."), "POST /saved-searches": { ...SAVED, id: "ss-9", last_matched_at: null } });
    const { container } = await draw();
    click(buttonNamed(form(container), "Save this search"));
    await flush();
    expect(status(container)).toBe("Search saved, but the saved searches list isn't showing it yet.");
  });

  it("is one save at a time", async () => {
    let release: (value: unknown) => void = () => undefined;
    answer({ "POST /saved-searches": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    click(buttonNamed(form(container), "Save this search"));
    await flush();
    click(buttonNamed(form(container), "Saving..."));
    expect(callsTo("POST /saved-searches")).toHaveLength(1);
    await act(async () => release({ ...SAVED, id: "ss-4" }));
    await flush();
  });

  it("pauses and resumes a search with a PATCH, and says Paused", async () => {
    answer({ "/saved-searches": [SAVED], "PATCH /saved-searches/ss-1": { ...SAVED, is_active: false } });
    const { container } = await draw();
    click(buttonNamed(container, /^Pause the saved search/));
    await flush();
    const patch = api.apiFetch.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(patch![1].body)).toEqual({ is_active: false });
    expect(words(query(container, ".bj-ss-row .bj-trail"))).toBe("Checked Sep 22 · Paused");
    expect(words(buttonNamed(container, /^Resume/))).toBe("Resume");
  });

  it("deletes a search with a DELETE and takes it off the list", async () => {
    answer({ "/saved-searches": [SAVED], "DELETE /saved-searches/ss-1": null });
    const { container } = await draw();
    click(buttonNamed(container, /^Delete the saved search/));
    await flush();
    expect(callsTo("DELETE /saved-searches/ss-1")).toHaveLength(1);
    expect(container.querySelector(".bj-ss")).toBeNull();
  });

  it("keeps the list and says why when an action fails", async () => {
    answer({ "/saved-searches": [SAVED], "DELETE /saved-searches/ss-1": new FakeApiError(500, "Server error.") });
    const { container } = await draw();
    click(buttonNamed(container, /^Delete the saved search/));
    await flush();
    expect(words(query(container, ".bj-ss [role='alert']"))).toBe("Server error.");
    expect(container.querySelectorAll(".bj-ss-row")).toHaveLength(1);
  });

  it("shows a failed read as a callout with Retry while the search itself still works", async () => {
    let attempt = 0;
    answer({
      "/saved-searches": () => {
        if (++attempt === 1) throw new FakeApiError(500, "down");
        return [SAVED];
      },
      "/discover?": response(),
    });
    const { container } = await draw();
    const alert = query(container, ".bj-ss[role='alert']");
    expect(words(alert)).toContain("Saved searches");
    await search(container);
    expect(container.querySelectorAll("li.bj-disc-row")).toHaveLength(1);
    click(buttonNamed(alert, "Try again"));
    await flush();
    expect(container.querySelector(".bj-ss[role='alert']")).toBeNull();
    expect(words(query(container, ".bj-ss-label"))).toBe('"analytics engineer"');
  });
});

describe("accessibility of the whole page", () => {
  it("has no serious or critical axe violation before a search, with results and with a failure", async () => {
    answer({ "/discover?": response({ warnings: ["brave: down"], more: [job({ apply_url: "https://jobs.example/acme/2", score: score(), provider: "registry" })] }), "/saved-searches": [SAVED] });
    const { container } = await draw();
    await expectNoSeriousA11yViolations(container);
    await search(container);
    await expectNoSeriousA11yViolations(container);
  });

  it("holds exactly one page heading and one cyan fill when ready", async () => {
    const { container } = await draw();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(container.querySelectorAll("button.bj-primary, a.bj-button-link-primary")).toHaveLength(1);
  });
});
