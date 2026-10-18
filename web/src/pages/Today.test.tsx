// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { buttonNamed, click, installViewport, query, render } from "../testing/render";
import Today from "./Today";

// The page itself, against a fake API: which requests it makes and when, what the band says as the
// answers arrive and as items are dismissed, accepted and tracked, what a failed action leaves
// behind, and the margin and the inline trail switching with the width. The states of the view are
// drawn from props in components/today/TodayView.test.tsx; this is the reading and the requests.

const api = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("../lib/api", () => ({ apiFetch: api.apiFetch }));
vi.mock("../auth", () => ({ useAuth: () => ({ session: { user: { id: "user-1" } } }) }));
// What the shell learned from GET /capabilities. It is asked once by the gate above the pages, not
// by Today, so it is a mocked state here and not one of the page's own requests.
const recorded = vi.hoisted(() => ({ caps: { kind: "checking" } as unknown }));
vi.mock("../lib/useCapabilities", () => ({ useCapabilities: () => recorded.caps }));
const GMAIL_OPEN = { kind: "ready", capabilities: { telegram: false, testerProgramRequired: false, telegramBotUsername: null, gmail: true, gmailAllowed: true } };

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
const GOOD_CREDENTIALS = [{ service: "llm", provider: "openrouter", is_validated: true, model: "vendor/model" }];
const APPLICATIONS = [
  { id: "app-1", source_channel: "discover", resume_exists: true, snapshot: { title: "Data Engineer", company_name: "Acme" } },
  { id: "app-2", source_channel: "web", resume_exists: false, snapshot: { title: "ML Engineer", company_name: "Orbit" } },
];
const SEARCHES = [
  { id: "ss-1", query: "analytics engineer", location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" },
];

const proposalItem = {
  id: "p1",
  kind: "status_proposal",
  headline: "✉️ Possible update: interview requested",
  detail: "72% confidence -- review and confirm",
  created_at: "2026-09-22T10:00:00Z",
  application_id: "app-1",
  job_match: null,
  status_proposal: { id: "sp1", proposed_type: "interview.requested", confidence: 0.72, evidence_spans: ["Are you free Thursday?"], status: "pending" },
};
const readyItem = { id: "r1", kind: "resume_ready", headline: "📄 Resume ready for ML Engineer @ Orbit", detail: "ATS score 72/100", created_at: "2026-09-22T09:00:00Z", application_id: "app-2", job_match: null, status_proposal: null };
const heldItem = { id: "f1", kind: "resume_failed", headline: "⚠️ Resume generation needs attention for Data Engineer @ Acme", detail: "Fit too low", created_at: "2026-09-22T08:00:00Z", application_id: "app-1", job_match: null, status_proposal: null };
const trackedItem = { id: "t1", kind: "job_tracked", headline: "🆕 Tracking Data Engineer @ Acme", detail: null, created_at: "2026-09-22T07:00:00Z", application_id: "app-1", job_match: null, status_proposal: null };
const matchItem = {
  id: "m1",
  kind: "high_fit_job",
  headline: "🎯 High-fit match: Analytics Engineer @ Initech",
  detail: "Strong on SQL",
  created_at: "2026-09-22T06:00:00Z",
  application_id: null,
  job_match: { today_item_id: "m1", saved_search_id: "ss-1", apply_url: "https://jobs.example/a", title: "Analytics Engineer", company: "Initech", location: "Remote", score100: 74, bin: "Strong", snippet: "s", provider: "registry" },
  status_proposal: null,
};

// This environment has no usable localStorage, so the page's flags go to a plain map.
const memory = new Map<string, string>();
const storage = {
  getItem: (key: string) => memory.get(key) ?? null,
  setItem: (key: string, value: string) => void memory.set(key, value),
};

let replies: Record<string, Reply> = {};
const calls = () => api.apiFetch.mock.calls.map(([path, init]) => `${init?.method ?? "GET"} ${path}`);

function reply(path: string, method = "GET"): Reply {
  return replies[`${method} ${path}`] ?? replies[path];
}

function answer(extra: Record<string, Reply> = {}) {
  replies = {
    "/today": [],
    "/profile/current": GOOD_PROFILE,
    "/credentials": GOOD_CREDENTIALS,
    "/saved-searches": SEARCHES,
    "/applications": APPLICATIONS,
    ...extra,
  };
}

const flush = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));

async function draw(width = 1000) {
  const viewport = installViewport(width);
  const rendered = render(
    <MemoryRouter>
      <Today />
    </MemoryRouter>,
  );
  await flush();
  return { ...rendered, viewport };
}

const words = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();
const band = (container: HTMLElement) => query(container, 'section[aria-label="Next step"]');
const rows = (container: HTMLElement) => [...container.querySelectorAll("li.bj-today-row")].map((row) => row.getAttribute("data-kind"));

beforeEach(() => {
  api.apiFetch.mockReset();
  api.apiFetch.mockImplementation(async (path: string, init?: { method?: string }) => {
    const found = reply(path, init?.method ?? "GET");
    if (found === undefined) throw new Error(`no reply for ${init?.method ?? "GET"} ${path}`);
    if (found instanceof Error) throw found;
    return typeof found === "function" ? (found as () => unknown)() : found;
  });
  answer();
  recorded.caps = GMAIL_OPEN;
  memory.clear();
  Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
  if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function") {
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000000" });
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("what the page asks for", () => {
  it("reads the feed and the four account lists once each, all plain GETs, and nothing that spends a search", async () => {
    await draw();
    expect(calls().sort()).toEqual(["GET /applications", "GET /credentials", "GET /profile/current", "GET /saved-searches", "GET /today"].sort());
    expect(calls().some((call) => call.includes("/discover"))).toBe(false);
  });

  it("asks once for the fit check's score of each held-back application, and not for any other kind", async () => {
    answer({
      "/today": [heldItem, { ...heldItem, id: "f2" }, readyItem, trackedItem],
      "/applications/app-1/prepare-result": { result: { resume: null, gate_outcome: "REJECT_MISMATCH", fit: { overall_score: 3.64 } } },
    });
    await draw();
    expect(calls().filter((call) => call.includes("prepare-result"))).toEqual(["GET /applications/app-1/prepare-result"]);
  });
});

describe("the band as the answers arrive", () => {
  it("shows only Loading... until the feed, the profile and the lists have all answered", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "/applications": () => new Promise((resolve) => (release = resolve)), "/today": [proposalItem] });
    const { container } = await draw();
    expect(band(container).getAttribute("aria-busy")).toBe("true");
    expect(words(band(container))).toBe("Next stepLoading...");
    await act(async () => release(APPLICATIONS));
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Acme may have requested an interview. Read their words, then decide.");
  });

  it("holds the most urgent item, and lists the others", async () => {
    answer({ "/today": [trackedItem, readyItem, proposalItem, matchItem] });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-eyebrow"))).toBe("Next step · Status proposal");
    expect(rows(container)).toEqual(["job_tracked", "resume_ready", "high_fit_job"]);
  });

  it("says nothing needs a decision for an empty feed with saved searches, and offers no button", async () => {
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Nothing needs a decision right now.");
    expect(words(query(band(container), ".bj-band-why"))).toBe("why: 0 items need a decision · 1 saved search active");
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
  });

  it("asks for a saved search when there is none and nothing in the feed", async () => {
    answer({ "/saved-searches": [] });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Save a search in Discover so Strong matches can land here.");
    expect(query(band(container), "a.bj-button-link-primary").getAttribute("href")).toBe("/discover");
  });

  it("asks for the profile first when there is no active profile, over any item", async () => {
    answer({ "/today": [proposalItem], "/profile/current": new FakeApiError(404, "no profile") });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Everything here starts from your profile. Import it first.");
    expect(query(band(container), "a.bj-button-link-primary").getAttribute("href")).toBe("/profile");
    // Nothing is lifted into the band, so the item stays in the list with its own controls.
    expect(rows(container)).toEqual(["status_proposal"]);
    // The checklist says it is to do, with the link to where it is done. No second box repeats it.
    const step = query(container, ".bj-setup-step:nth-child(1)");
    expect(words(query(step, ".bj-setup-state"))).toBe("◆To do");
    expect(query(step, "a").getAttribute("href")).toBe("/profile");
    expect(container.querySelector(".bj-state--setup")).toBeNull();
  });

  it("names a key that is not set in the checklist, with a link, when the profile is there", async () => {
    answer({ "/credentials": [] });
    const { container } = await draw();
    const step = query(container, ".bj-setup-step:nth-child(2)");
    expect(words(query(step, ".bj-setup-state"))).toBe("◆To do");
    expect(query(step, "a").getAttribute("href")).toBe("/profile/integrations");
    expect(words(query(step, "a"))).toBe("Add a model key");
  });
});

describe("failure and retry", () => {
  it("says the page's own read failed, in the band, and Try again reads it again", async () => {
    answer({ "/today": new FakeApiError(500, "Request failed (500)") });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("✕Couldn't load Today. You didn't do anything wrong, and nothing was lost.");
    expect(words(query(band(container), ".bj-band-why"))).toMatch(/^why: GET \/today failed · \d\d:\d\d$/);
    expect(words(query(container, ".bj-today-main .bj-state--failure"))).toBe("✕Today's items couldn't be read: Request failed (500)");
    expect(container.querySelector(".bj-today-main .bj-state--failure button")).toBeNull();

    answer({ "/today": [trackedItem] });
    api.apiFetch.mockClear();
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(calls()).toEqual(["GET /today"]);
    expect(rows(container)).toEqual(["job_tracked"]);
    expect(container.querySelector(".bj-today-main .bj-state--failure")).toBeNull();
  });

  it("says the setup check failed when the profile cannot be read, and Try again reads the account again", async () => {
    answer({ "/profile/current": new FakeApiError(500, "boom"), "/today": [trackedItem] });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("✕Couldn't check your setup. You didn't do anything wrong, and nothing was lost.");
    expect(words(query(band(container), ".bj-band-why"))).toMatch(/^why: GET \/profile\/current failed · \d\d:\d\d$/);

    answer({ "/today": [trackedItem] });
    api.apiFetch.mockClear();
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(calls().sort()).toEqual(["GET /applications", "GET /credentials", "GET /profile/current", "GET /saved-searches"].sort());
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Nothing needs a decision right now. 1 update below.");
  });

  it("leaves the sentence off a name it could not read: a failed applications list names no job", async () => {
    answer({ "/today": [readyItem], "/applications": new FakeApiError(500, "boom") });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Your resume is ready for review.");
    expect(words(query(container, ".bj-state--partial"))).toContain("Your applications couldn't be read");
  });
});

describe("dismissing, accepting and tracking", () => {
  it("dismisses a lit proposal through the proposal route, takes it out, and moves the band on", async () => {
    answer({ "/today": [proposalItem, readyItem], "POST /today/p1/dismiss-proposal": {} });
    const { container } = await draw();
    click(buttonNamed(band(container), "Dismiss"));
    await flush();
    expect(calls()).toContain("POST /today/p1/dismiss-proposal");
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Your resume for ML Engineer at Orbit is ready for review.");
    expect(rows(container)).toEqual([]);
    expect(container.querySelector("[data-kind='status_proposal']")).toBeNull();
  });

  it("dismisses any other item through the plain route", async () => {
    answer({ "/today": [trackedItem], "POST /today/t1/dismiss": {} });
    const { container } = await draw();
    click(buttonNamed(container, "Dismiss"));
    await flush();
    expect(calls()).toContain("POST /today/t1/dismiss");
    expect(rows(container)).toEqual([]);
    expect(container.querySelector(".bj-state--empty")).not.toBeNull();
  });

  it("does not light a proposal that was already decided, and dismisses it through the plain route", async () => {
    // The server resolves a sibling proposal on its own and leaves its item in the feed, where
    // Accept and the proposal Dismiss would both be refused with a 409.
    const decided = { ...proposalItem, status_proposal: { ...proposalItem.status_proposal, status: "dismissed" } };
    answer({ "/today": [decided, readyItem], "POST /today/p1/dismiss": {} });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Your resume for ML Engineer at Orbit is ready for review.");
    expect(container.textContent).not.toContain("Accept: move to");
    expect(container.textContent).not.toContain("Needs your decision");
    const row = container.querySelector("li.bj-today-row") as HTMLElement;
    expect(row.getAttribute("data-kind")).toBe("status_proposal");
    click(buttonNamed(row, "Dismiss"));
    await flush();
    expect(calls()).toContain("POST /today/p1/dismiss");
    expect(calls()).not.toContain("POST /today/p1/dismiss-proposal");
    expect(rows(container)).toEqual([]);
  });

  it("accepts a proposal from the band's one cyan button, and removes it", async () => {
    answer({ "/today": [proposalItem], "POST /today/p1/accept-proposal": {} });
    const { container } = await draw();
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
    expect(calls()).toContain("POST /today/p1/accept-proposal");
    expect(band(container).className).toContain("bj-band--unlit");
  });

  it("ignores a second press while the first request is out", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "/today": [proposalItem], "POST /today/p1/accept-proposal": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
    expect(calls().filter((call) => call.includes("accept-proposal"))).toHaveLength(1);
    await act(async () => release({}));
  });

  it("tracks a high fit job with the request Discover makes, then turns Track into Open workspace", async () => {
    answer({ "/today": [matchItem], "POST /discover/track": { id: "app-9" } });
    const { container } = await draw();
    click(buttonNamed(band(container), "Track"));
    await flush();
    const track = api.apiFetch.mock.calls.find(([path]) => path === "/discover/track");
    expect(JSON.parse(track?.[1].body)).toEqual({
      apply_url: "https://jobs.example/a",
      title: "Analytics Engineer",
      company: "Initech",
      location: "Remote",
      snippet: "s",
      provider: "registry",
    });
    const link = query<HTMLAnchorElement>(band(container), "a.bj-button-link-primary");
    expect(link.getAttribute("href")).toBe("/applications/app-9");
    expect(words(link)).toBe("Open workspace");
    // The item is still the one the band holds, and is not in the list.
    expect(rows(container)).toEqual([]);
  });

  it("leaves a failed dismiss under its item, in the error's own words, with the rest of the page as it was", async () => {
    answer({ "/today": [readyItem, trackedItem], "POST /today/t1/dismiss": new FakeApiError(500, "Request failed (500)") });
    const { container } = await draw();
    const row = container.querySelector("li.bj-today-row") as HTMLElement;
    click(buttonNamed(row, "Dismiss"));
    await flush();
    expect(words(query(row, ".bj-state--failure"))).toContain("Request failed (500)");
    expect(rows(container)).toEqual(["job_tracked"]);
    expect(band(container).className).toContain("bj-band--lit");
    // The band says nothing about it: only the page's own read does that.
    expect(words(band(container))).not.toContain("Couldn't load");

    // Retry sends the same request again, and a success takes the item out and the callout with it.
    answer({ "/today": [readyItem, trackedItem], "POST /today/t1/dismiss": {} });
    click(buttonNamed(row, "Retry"));
    await flush();
    expect(calls().filter((call) => call === "POST /today/t1/dismiss")).toHaveLength(2);
    expect(rows(container)).toEqual([]);
    expect(container.querySelector(".bj-state--failure")).toBeNull();
  });

  it("shows a failed track in the friendly words a rate limit gets", async () => {
    const limited = new FakeApiError(429, "slow down", { code: "RATE_LIMITED" });
    answer({ "/today": [matchItem], "POST /discover/track": limited });
    const { container } = await draw();
    click(buttonNamed(band(container), "Track"));
    await flush();
    expect(words(query(band(container), ".bj-state--failure"))).toMatch(/try again/i);
  });
});

describe("a resume the fit check held back", () => {
  const prepareResult = { result: { resume: null, gate_outcome: "REJECT_MISMATCH", fit: { overall_score: 3.64 } } };

  it("shows the fit check's score once its result has been read", async () => {
    answer({ "/today": [heldItem], "/applications/app-1/prepare-result": prepareResult });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-today-chip"))).toBe("◆Fit check held it back · 3.6 / 10");
  });

  it("shows the chip with no number, and does not claim no resume exists, when that read fails", async () => {
    answer({ "/today": [heldItem], "/applications/app-1/prepare-result": new FakeApiError(500, "boom") });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-today-chip"))).toBe("◆Fit check held it back");
    expect(words(query(band(container), ".bj-band-why"))).toBe("why: held back by the fit check");
  });

  it("does not light a held-back card once a resume exists for its application, and says why it is quiet", async () => {
    answer({
      "/today": [heldItem, trackedItem],
      "/applications/app-1/prepare-result": { result: { resume: { artifact_id: "a" }, gate_outcome: "proceed", fit: { overall_score: 8 } } },
    });
    const { container } = await draw();
    expect(band(container).className).toContain("bj-band--unlit");
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Nothing needs a decision right now. 2 updates below.");
    const row = container.querySelector("li[data-kind='resume_failed']") as HTMLElement;
    expect(words(query(row, ".bj-today-kind"))).toBe("Held back earlier");
    expect(row.textContent).not.toContain("Generate anyway");
  });

  it("keeps the band on Loading... until the stored result of a held-back card has answered, then lights the right thing", async () => {
    let release: (value: unknown) => void = () => {};
    answer({
      "/today": [heldItem, readyItem],
      "/applications/app-1/prepare-result": () => new Promise((resolve) => (release = resolve)),
    });
    const { container } = await draw();
    expect(band(container).getAttribute("aria-busy")).toBe("true");
    await act(async () => release({ result: { resume: { artifact_id: "a" }, gate_outcome: "proceed" } }));
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Your resume for ML Engineer at Orbit is ready for review.");
  });

  it("asks for the stored result of the newest few held-back cards only, however many there are", async () => {
    const many = Array.from({ length: 25 }, (_, index) => ({ ...heldItem, id: `f${index}`, application_id: `app-${index}`, created_at: `2026-09-${String(1 + index).padStart(2, "0")}T08:00:00Z` }));
    answer({ "/today": many });
    await draw();
    expect(calls().filter((call) => call.includes("prepare-result"))).toHaveLength(5);
  });

  it("generates anyway with the gate bypassed, then retires the card", async () => {
    answer({
      "/today": [heldItem, trackedItem],
      "/applications/app-1/prepare-result": prepareResult,
      "POST /applications/app-1/prepare": { run_id: "r", resume: { artifact_id: "a" } },
      "POST /today/f1/dismiss": {},
    });
    const { container } = await draw();
    click(buttonNamed(band(container), "Generate anyway"));
    await flush();
    const prepare = api.apiFetch.mock.calls.find(([path]) => path === "/applications/app-1/prepare");
    expect(JSON.parse(prepare?.[1].body)).toMatchObject({ force_generate: true, generate_cover_letter: false });
    expect(typeof JSON.parse(prepare?.[1].body).idempotency_key).toBe("string");
    expect(calls()).toContain("POST /today/f1/dismiss");
    expect(container.querySelector("[data-kind='resume_failed']")).toBeNull();
    expect(words(query(band(container), ".bj-band-eyebrow"))).not.toContain("Resume not generated");
  });

  it("keeps the card, with the reason, when no resume came back", async () => {
    answer({ "/today": [heldItem], "POST /applications/app-1/prepare": { run_id: "r", resume: null } });
    const { container } = await draw();
    click(buttonNamed(band(container), "Generate anyway"));
    await flush();
    expect(words(query(band(container), ".bj-state--failure"))).toContain("No resume was produced.");
    expect(calls()).not.toContain("POST /today/f1/dismiss");
    expect(container.querySelector(".bj-band-sentence")?.textContent).toContain("held back your draft");
  });

  it("names what is missing, with its link, when generating needs a key", async () => {
    answer({
      "/today": [heldItem],
      "POST /applications/app-1/prepare": new FakeApiError(409, "Add a model key first.", { code: "SETUP_REQUIRED", settingsPath: "/profile/integrations", capability: "prepare_application" }),
    });
    const { container } = await draw();
    click(buttonNamed(band(container), "Generate anyway"));
    await flush();
    const setup = query(band(container), ".bj-state--setup");
    expect(words(setup)).toContain("Add a model key first.");
    expect(query(setup, "a").getAttribute("href")).toBe("/profile/integrations");
  });
});

describe("what the trail says about Gmail", () => {
  const CONNECTED = { service: "oauth", provider: "gmail", is_validated: true, scope: "https://www.googleapis.com/auth/gmail.readonly" };
  const gmailLines = (container: HTMLElement) => words(query(container, "aside.bj-today-margin"));

  it("says replies are read on the person's key once Gmail is open to them and connected", async () => {
    answer({ "/credentials": [...GOOD_CREDENTIALS, CONNECTED] });
    const { container } = await draw(1400);
    expect(gmailLines(container)).toContain("Gmail replies read by your model");
  });

  it("claims nothing while it is not known whether Gmail is open to this person", async () => {
    answer({ "/credentials": [...GOOD_CREDENTIALS, CONNECTED] });
    for (const caps of [{ kind: "checking" }, { kind: "unavailable" }]) {
      recorded.caps = caps;
      const wide = await draw(1400);
      expect(gmailLines(wide.container)).not.toContain("Gmail");
      wide.unmount();
      const narrow = await draw(1000);
      expect(words(query(narrow.container, "button.bj-bg-toggle"))).not.toContain("Gmail");
      narrow.unmount();
    }
  });

  it("says nothing about reads or reconnecting for a person Gmail is not for, whatever they once connected", async () => {
    recorded.caps = { kind: "ready", capabilities: { ...GMAIL_OPEN.capabilities, gmailAllowed: false } };
    answer({ "/credentials": [...GOOD_CREDENTIALS, CONNECTED] });
    const connected = await draw(1400);
    expect(gmailLines(connected.container)).not.toContain("Gmail");
    connected.unmount();
    answer({ "/credentials": [...GOOD_CREDENTIALS, { ...CONNECTED, is_validated: false }] });
    const ended = await draw(1400);
    expect(gmailLines(ended.container)).not.toContain("Gmail");
    expect(ended.container.textContent).not.toContain("Reconnect Gmail");
  });

  it("asks for nothing to learn it: the shell has already asked", async () => {
    await draw();
    expect(calls()).not.toContain("GET /capabilities");
  });
});

describe("the margin and the inline trail follow the width", () => {
  it("is a right margin at 1280px, and one disclosure line under the band below it, switching as the window changes", async () => {
    const { container, viewport } = await draw(1400);
    expect(container.querySelector("aside.bj-today-margin")).not.toBeNull();
    expect(container.querySelector("button.bj-bg-toggle")).toBeNull();

    viewport.setWidth(1100);
    expect(container.querySelector("aside.bj-today-margin")).toBeNull();
    expect(container.querySelector("button.bj-bg-toggle")).not.toBeNull();

    viewport.setWidth(375);
    expect(container.querySelector("button.bj-bg-toggle")).not.toBeNull();
    expect(container.querySelector(".bj-today-grid")).toBeNull();

    viewport.setWidth(1280);
    expect(container.querySelector("aside.bj-today-margin")).not.toBeNull();
  });

  it("holds the same words in both: the saved searches and when they were last checked", async () => {
    const wide = await draw(1400);
    const margin = words(query(wide.container, "aside.bj-today-margin .bj-trail"));
    wide.unmount();
    const narrow = await draw(1000);
    click(query(narrow.container, "button.bj-bg-toggle"));
    expect(words(query(narrow.container, ".bj-bg-body .bj-trail"))).toBe(margin);
  });
});

describe("the setup checklist", () => {
  it("opens for a new account, from the same reads, and has no cyan fill of its own", async () => {
    answer({ "/profile/current": new FakeApiError(404, "none"), "/credentials": [], "/saved-searches": [], "/applications": [] });
    const { container } = await draw();
    const list = query(container, "section.bj-setup");
    expect(words(query(list, ".bj-setup-count"))).toBe("0 of 5 done");
    expect(list.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
    // Only the band's button is cyan.
    expect(container.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(1);
    expect(calls().filter((call) => call === "GET /applications")).toHaveLength(1);
  });

  it("is one 'Setup ready' line for a finished account, and remembers that for the next visit", async () => {
    answer({ "/today": [], "/saved-searches": SEARCHES, "/applications": APPLICATIONS });
    const first = await draw();
    expect(words(query(first.container, "button.bj-setup-line"))).toBe("✓Setup ready›");
    expect(storage.getItem("between-jobs:first-run:completed:user-1")).toBe("1");
    first.unmount();
    // The next visit shows it before the reads have come back.
    answer({ "/applications": () => new Promise(() => {}) });
    const second = await draw();
    expect(words(query(second.container, "button.bj-setup-line"))).toBe("✓Setup ready›");
  });

  it("keeps a hidden checklist hidden, as one counting line", async () => {
    answer({ "/profile/current": new FakeApiError(404, "none"), "/credentials": [], "/saved-searches": [], "/applications": [] });
    const first = await draw();
    click(buttonNamed(first.container, "Hide the setup checklist"));
    expect(storage.getItem("between-jobs:first-run:dismissed:user-1")).toBe("1");
    first.unmount();
    const second = await draw();
    expect(second.container.querySelector("section.bj-setup")).toBeNull();
    expect(words(query(second.container, "button.bj-setup-line"))).toBe("Setup · 5 to do›");
  });

  it("offers Try again on a step whose read failed, which reads the account again", async () => {
    answer({ "/profile/current": new FakeApiError(404, "none"), "/credentials": [], "/applications": new FakeApiError(500, "boom"), "/saved-searches": [] });
    const { container } = await draw();
    expect(words(query(container, ".bj-setup-step:nth-child(4) .bj-setup-state"))).toBe("○Couldn't check");
    api.apiFetch.mockClear();
    answer({ "/profile/current": new FakeApiError(404, "none"), "/credentials": [], "/applications": [], "/saved-searches": [] });
    click(buttonNamed(container, "Try again: Track a job"));
    await flush();
    expect(calls()).toContain("GET /applications");
    expect(words(query(container, ".bj-setup-step:nth-child(4) .bj-setup-state"))).toBe("◆To do");
  });
});

describe("the page as a whole", () => {
  it("has no serious accessibility violation with a lit item, a list, the checklist and the margin", async () => {
    answer({ "/today": [proposalItem, readyItem, heldItem, matchItem, trackedItem], "/applications/app-1/prepare-result": { result: { resume: null, fit: { overall_score: 3 } } } });
    const { container } = await draw(1400);
    await expectNoSeriousA11yViolations(container);
  });

  it("has no serious accessibility violation on a phone", async () => {
    answer({ "/today": [proposalItem, readyItem] });
    const { container } = await draw(375);
    await expectNoSeriousA11yViolations(container);
  });

  it("has exactly one cyan fill with a lit item and none without", async () => {
    answer({ "/today": [proposalItem, readyItem] });
    const lit = await draw();
    expect(lit.container.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(1);
    lit.unmount();
    answer({ "/today": [trackedItem] });
    const unlit = await draw();
    expect(unlit.container.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(0);
  });
});
