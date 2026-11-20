// @vitest-environment jsdom
import { StrictMode, act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NEXT_STEP_FOCUS_ID } from "../components/paper/NextStepBand";
import { ACTIVITY_HEADING_ID } from "../components/today/TodayView";
import { PROPOSAL_STEPS } from "../lib/nextStep/pages/today";
import { SEVERAL_MODEL_CALLS, spendNotice } from "../lib/spendNotice";
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
// What a screen reader is told about the request just made: the page's polite status.
const status = (container: HTMLElement) => words(query(container, ".bj-today > .bj-visually-hidden[role='status']"));
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

// The first day: an active profile, a usable model key, no application tracked, nothing in the feed
// and no saved search. Nothing this page can read says a saved search will find anything, so nothing
// on that screen may say a match will arrive. The band, the empty state and the trail are written in
// three files, and the trail has two placements; this reads the whole page as a person sees it at a
// width of each, so a promise reintroduced in any of them is caught here.
//
// It is a tripwire for the phrasings listed below, not proof that no paraphrase promises anything:
// the band's sentence, the trail's line and the empty state's list of kinds are pinned exactly in
// the tests of those three files. Remove it when the page can read when the listings the matcher
// reads were last refreshed and the copy is written against that date.
describe("the first day makes no promise of matches", () => {
  // Every piece of text on the page with a space between pieces, so a word that opens an element
  // (a list item, a line) is still a word to the patterns below.
  function everythingSaid(root: Element): string {
    const pieces: string[] = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) pieces.push(node.textContent ?? "");
    return pieces.join(" ").replace(/\s+/g, " ").trim();
  }

  const PROMISES: readonly RegExp[] = [
    // The word itself, in any form: the first day has no match to speak of.
    /\bmatch/i,
    // Something arriving, said with a modal ("can land") or in the present tense ("show up here").
    /\b(?:can|could|may|might|will|would|should|'ll)\s+(?:land|appear|show up|arrive|turn up|come in)\b/i,
    /\b(?:lands?|appears?|shows? up|arrives?|turns? up)\s+here\b/i,
  ];

  // Wide, the trail is the page's margin; narrow, it is one line that opens.
  for (const [width, inline] of [
    [1400, false],
    [1000, true],
  ] as const) {
    it(`says it nowhere on the page at ${width}px`, async () => {
      answer({ "/saved-searches": [], "/applications": [] });
      const { container } = await draw(width);
      // Each part has to be on the screen, or a scan of nothing would pass: the band is the Discover
      // step, the empty state is drawn, and the trail is in the placement this width calls for.
      expect(query(band(container), "a.bj-button-link-primary").getAttribute("href")).toBe("/discover");
      const toggle = container.querySelector("button.bj-bg-toggle");
      expect(toggle === null).toBe(!inline);
      if (toggle !== null) {
        expect(words(toggle)).toContain("no saved searches");
        click(toggle);
      }
      const text = everythingSaid(container);
      expect(text).toContain("Nothing here yet");
      expect(text).toContain("No saved searches.");
      for (const promise of PROMISES) expect(text).not.toMatch(promise);
    });
  }
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

  it("sends a person with no saved search and nothing in the feed to find and track a job on Discover", async () => {
    answer({ "/saved-searches": [] });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Find a job on Discover and track it.");
    expect(words(query(band(container), ".bj-band-why"))).toBe("why: 0 items · 0 saved searches");
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

  it("sends a person with an active profile and no usable model key to add one, ahead of any item or saved search", async () => {
    answer({ "/credentials": [], "/saved-searches": [] });
    const empty = await draw();
    expect(words(query(band(empty.container), ".bj-band-sentence"))).toBe("Add a model key so Between Jobs can draft and score.");
    expect(words(query(band(empty.container), ".bj-band-why"))).toBe("why: profile active · no model key saved");
    expect(query(band(empty.container), "a.bj-button-link-primary").getAttribute("href")).toBe("/settings#ai-provider");
    empty.unmount();

    // Nothing is lifted into the band, so an item waiting keeps its own controls in the list.
    answer({ "/credentials": [], "/today": [matchItem] });
    const waiting = await draw();
    expect(words(query(band(waiting.container), ".bj-band-sentence"))).toBe("Add a model key so Between Jobs can draft and score.");
    expect(rows(waiting.container)).toEqual(["high_fit_job"]);
  });

  it("does not count a key that is saved with no model as a usable one: the band asks for the model, and says why", async () => {
    answer({ "/credentials": [{ service: "llm", provider: "openai", is_validated: true, model: null, setup_state: "needs_model" }] });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Your model key is saved. Choose a model so Between Jobs can draft and score.");
    expect(words(query(band(container), ".bj-band-why"))).toBe("why: profile active · a key is saved, no model chosen");
    expect(words(query(band(container), "a.bj-button-link-primary"))).toBe("Choose a model");
    // The checklist under it says the same thing and links to the same section of Settings.
    const step = query(container, ".bj-setup-step:nth-child(2)");
    expect(words(query(step, "a"))).toBe("Choose a model");
    expect(query(step, "a").getAttribute("href")).toBe("/settings#ai-provider");
  });

  it("does not ask for a key when the credentials list has one, or when it could not be read", async () => {
    const present = await draw();
    expect(words(query(band(present.container), ".bj-band-sentence"))).not.toContain("model key");
    present.unmount();
    answer({ "/credentials": new FakeApiError(500, "boom") });
    const failed = await draw();
    expect(words(query(band(failed.container), ".bj-band-sentence"))).toContain("Couldn't check your setup");
  });

  it("names a key that is not set in the checklist, with a link, when the profile is there", async () => {
    answer({ "/credentials": [] });
    const { container } = await draw();
    const step = query(container, ".bj-setup-step:nth-child(2)");
    expect(words(query(step, ".bj-setup-state"))).toBe("◆To do");
    expect(query(step, "a").getAttribute("href")).toBe("/settings#ai-provider");
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

  it("accepts a proposal from the band's one gold button, removes it, and says where the application went", async () => {
    answer({ "/today": [proposalItem], "POST /today/p1/accept-proposal": {} });
    const { container } = await draw();
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
    expect(calls()).toContain("POST /today/p1/accept-proposal");
    expect(band(container).className).toContain("bj-band--unlit");
    // The card is gone from the band and from the list, not just the band's lit styling.
    expect(container.querySelector("[data-kind='status_proposal']")).toBeNull();
    expect(container.textContent).not.toContain("Accept: move to");
    expect(container.textContent).not.toContain("Are you free Thursday?");
    const confirmation = query(container, ".bj-today-confirmation");
    expect(words(confirmation)).toBe("Data Engineer at Acme moved to Interviewing. Open workspace");
    expect(query(confirmation, "a").getAttribute("href")).toBe("/applications/app-1");
  });

  it("ignores a second press while the first request is out, and the band shows the request while it is", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "/today": [proposalItem], "POST /today/p1/accept-proposal": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    const accept = buttonNamed(band(container), "Accept: move to Interviewing");
    click(accept);
    click(accept);
    await flush();
    expect(calls().filter((call) => call.includes("accept-proposal"))).toHaveLength(1);
    // The pressed button changes its words and waits; so does the quiet Dismiss beside it.
    expect(words(accept)).toBe("Moving...");
    expect(accept.getAttribute("aria-disabled")).toBe("true");
    expect(buttonNamed(band(container), "Dismiss").getAttribute("aria-disabled")).toBe("true");
    await act(async () => release({}));
    expect(container.contains(accept)).toBe(false);
  });

  it("shows a forced generation in the band for as long as the request takes", async () => {
    let release: (value: unknown) => void = () => {};
    answer({
      "/today": [heldItem],
      "/applications/app-1/prepare-result": { result: { resume: null, gate_outcome: "REJECT_MISMATCH" } },
      "POST /applications/app-1/prepare": () => new Promise((resolve) => (release = resolve)),
      "POST /today/f1/dismiss": {},
    });
    const { container } = await draw();
    const generate = buttonNamed(band(container), "Generate anyway");
    click(generate);
    await flush();
    expect(words(generate)).toBe("Generating...");
    expect(generate.getAttribute("aria-disabled")).toBe("true");
    // The link beside it is not a handler and stays a link.
    expect(query(band(container), "a.bj-button-link-primary").getAttribute("aria-disabled")).toBeNull();
    expect(status(container)).toBe("Generating your resume.");
    await act(async () => release({ run_id: "r", resume: { artifact_id: "a" } }));
  });

  it("shows a track, and a dismiss, in the band while they are out", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "/today": [matchItem], "POST /discover/track": () => new Promise((resolve) => (release = resolve)), "POST /today/m1/dismiss": () => new Promise(() => {}) });
    const tracking = await draw();
    const track = buttonNamed(band(tracking.container), "Track");
    click(track);
    await flush();
    expect(words(track)).toBe("Tracking...");
    expect(track.getAttribute("aria-disabled")).toBe("true");
    await act(async () => release({ id: "app-9" }));
    tracking.unmount();

    const dismissing = await draw();
    const dismiss = buttonNamed(band(dismissing.container), "Dismiss");
    click(dismiss);
    await flush();
    expect(words(dismiss)).toBe("Dismissing...");
    // The band's own button waits too, though it was not the one pressed.
    expect(buttonNamed(band(dismissing.container), "Track").getAttribute("aria-disabled")).toBe("true");
    expect(words(buttonNamed(band(dismissing.container), "Track"))).toBe("Track");
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

  it("says what Generate anyway costs, beside it in the band and beside it in a list row", async () => {
    const second = { ...heldItem, id: "f2", application_id: "app-2", created_at: "2026-09-22T07:30:00Z" };
    answer({ "/today": [heldItem, second], "/applications/app-1/prepare-result": prepareResult, "/applications/app-2/prepare-result": prepareResult });
    const { container } = await draw();
    const sentence = spendNotice("Generate anyway", SEVERAL_MODEL_CALLS);
    const pressing = (root: ParentNode) => [...root.querySelectorAll("p")].map((line) => words(line)).filter((line) => line.startsWith("Pressing "));
    expect(buttonNamed(band(container), "Generate anyway")).toBeTruthy();
    expect(pressing(query(band(container), ".bj-band-item"))).toEqual([sentence]);
    const row = container.querySelector("li[data-kind='resume_failed']") as HTMLElement;
    expect(buttonNamed(row, "Generate anyway")).toBeTruthy();
    expect(pressing(row)).toEqual([sentence]);
  });

  it("keeps that sentence with the item, not with Retry, when generating anyway fails", async () => {
    answer({
      "/today": [heldItem],
      "/applications/app-1/prepare-result": prepareResult,
      "POST /applications/app-1/prepare": new FakeApiError(500, "The engine did not answer."),
    });
    const { container } = await draw();
    click(buttonNamed(band(container), "Generate anyway"));
    await flush();
    const callout = query(band(container), ".bj-today-problem");
    expect(words(callout)).toContain("The engine did not answer.");
    expect(words(buttonNamed(callout, "Retry"))).toBe("Retry");
    expect(words(callout)).not.toContain("Pressing");
    const notes = [...band(container).querySelectorAll("p")].filter((line) => words(line) === spendNotice("Generate anyway", SEVERAL_MODEL_CALLS));
    expect(notes).toHaveLength(1);
    expect(callout.contains(notes[0])).toBe(false);
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
    // The API still names the old address; the link goes to Settings.
    expect(query(setup, "a").getAttribute("href")).toBe("/settings");
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
  it("opens for a new account, from the same reads, and has no gold fill of its own", async () => {
    answer({ "/profile/current": new FakeApiError(404, "none"), "/credentials": [], "/saved-searches": [], "/applications": [] });
    const { container } = await draw();
    const list = query(container, "section.bj-setup");
    expect(words(query(list, ".bj-setup-count"))).toBe("0 of 5 done");
    expect(list.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
    // Only the band's button is gold.
    expect(container.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(1);
    expect(calls().filter((call) => call === "GET /applications")).toHaveLength(1);
  });

  it("is one 'Setup ready' line for a finished account, and on the next visit only says it is checking until the reads confirm it", async () => {
    answer({ "/today": [], "/saved-searches": SEARCHES, "/applications": APPLICATIONS });
    const first = await draw();
    expect(words(query(first.container, "button.bj-setup-line"))).toBe("✓Setup ready›");
    expect(storage.getItem("between-jobs:first-run:completed:user-1")).toBe("1");
    first.unmount();
    // The next visit keeps a place for the line, and claims nothing until the reads have answered.
    let release: (value: unknown) => void = () => {};
    answer({ "/applications": () => new Promise((resolve) => (release = resolve)) });
    const second = await draw();
    expect(words(query(second.container, "p.bj-setup-line"))).toBe("Setup · checking");
    expect(second.container.textContent).not.toContain("Setup ready");
    await act(async () => release(APPLICATIONS));
    expect(words(query(second.container, "button.bj-setup-line"))).toBe("✓Setup ready›");
  });

  it("does not call an account ready that no longer is: the remembered flag is not a claim, and it is dropped", async () => {
    storage.setItem("between-jobs:first-run:completed:user-1", "1");
    let release: (value: unknown) => void = () => {};
    answer({ "/applications": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    expect(container.textContent).not.toContain("Setup ready");
    // The one tracked application was deleted since.
    await act(async () => release([]));
    expect(container.textContent).not.toContain("Setup ready");
    expect(words(query(container, ".bj-setup-count"))).toMatch(/of 5 done$/);
    expect(storage.getItem("between-jobs:first-run:completed:user-1")).toBe("0");
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

describe("the right column, from 1280px", () => {
  const STAGED = [
    { id: "app-1", status: "applied", source_channel: "discover", resume_exists: true, updated_at: "2026-09-01T00:00:00Z", snapshot: { title: "Data Engineer", company_name: "Acme" } },
    { id: "app-2", status: "saved", source_channel: "manual_paste", resume_exists: false, updated_at: "2026-09-21T00:00:00Z", snapshot: { title: "ML Engineer", company_name: "Orbit" } },
    { id: "app-3", status: "applied", source_channel: "discover", resume_exists: false, updated_at: "2026-08-01T00:00:00Z", snapshot: { title: "Analyst", company_name: "Initech" } },
    { id: "app-4", status: "paused", source_channel: "discover", resume_exists: false, updated_at: "2026-08-01T00:00:00Z", snapshot: { title: "Data Scientist", company_name: "Hooli" } },
  ];
  const pipeline = (container: HTMLElement) => query(container, "section.bj-pipeline");
  const pipelineLines = (container: HTMLElement) => [...pipeline(container).querySelectorAll("li")].map((line) => words(line));

  it("counts the stages from the applications the page already reads, and asks for nothing more", async () => {
    answer({ "/applications": STAGED });
    const { container } = await draw(1280);
    expect(pipelineLines(container)).toEqual(["Saved 1 application", "Applied 2 applications", "Other 1 application"]);
    expect(pipeline(container).closest(".bj-today-side")).not.toBeNull();
    expect([...new Set(calls())].sort()).toEqual(["GET /applications", "GET /credentials", "GET /profile/current", "GET /saved-searches", "GET /today"]);
    expect(calls().filter((call) => call === "GET /applications")).toHaveLength(1);
  });

  it("says nothing about how long ago an application changed: the row's updated_at is not when it was last worked on", async () => {
    answer({ "/applications": STAGED });
    const { container } = await draw(1280);
    expect(words(pipeline(container))).not.toMatch(/touched|days|stale/i);
  });

  it("puts the checklist in the column, above Pipeline, for an account still setting up", async () => {
    answer({ "/profile/current": new FakeApiError(404, "none"), "/credentials": [], "/saved-searches": [], "/applications": [] });
    const { container } = await draw(1280);
    const side = query(container, ".bj-today-side");
    expect([...side.children].map((child) => words(child.querySelector("h2")))).toEqual(["Setup", "Pipeline", "✦ In the background"]);
    expect(query(container, ".bj-today-main").querySelector(".bj-setup")).toBeNull();
    expect(words(query(pipeline(container), ".bj-pipeline-note"))).toBe("No applications are tracked yet: find a job in Discover.");
  });

  it("puts a finished account's one setup line there too", async () => {
    answer({ "/today": [], "/saved-searches": SEARCHES, "/applications": APPLICATIONS });
    const { container } = await draw(1280);
    expect(query(container, "button.bj-setup-line").parentElement).toBe(query(container, ".bj-today-side"));
  });

  it("says a failed applications read plainly in Pipeline, with no count, and reads the account again on Retry", async () => {
    answer({ "/applications": new FakeApiError(500, "boom") });
    const { container } = await draw(1280);
    const callout = query(pipeline(container), ".bj-state--failure");
    expect(words(callout)).toContain("Your applications couldn't be read, so there are no counts to show.");
    expect(words(pipeline(container))).not.toMatch(/\d/);
    api.apiFetch.mockClear();
    answer({ "/applications": STAGED });
    click(buttonNamed(callout, "Retry"));
    await flush();
    expect(calls()).toContain("GET /applications");
    expect(pipelineLines(container)).toEqual(["Saved 1 application", "Applied 2 applications", "Other 1 application"]);
  });

  it("shows a placeholder, not a zero, while the applications are still being read", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "/applications": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw(1280);
    expect(words(query(pipeline(container), ".bj-state--loading"))).toBe("Loading your applications...");
    expect(words(pipeline(container))).not.toMatch(/\d/);
    await act(async () => release(STAGED));
    expect(pipelineLines(container)).toEqual(["Saved 1 application", "Applied 2 applications", "Other 1 application"]);
  });

  it("moves between the two layouts with the window, keeping one checklist and one Pipeline card", async () => {
    answer({ "/profile/current": new FakeApiError(404, "none"), "/credentials": [], "/saved-searches": [], "/applications": STAGED });
    const { container, viewport } = await draw(1279);
    expect(container.querySelector(".bj-today-side, .bj-today-grid")).toBeNull();
    expect(query(container, "section.bj-setup").closest(".bj-today-main")).not.toBeNull();
    expect(query(container, ".bj-today").lastElementChild).toBe(pipeline(container));
    viewport.setWidth(1280);
    expect(query(container, "section.bj-setup").parentElement).toBe(query(container, ".bj-today-side"));
    expect(container.querySelectorAll("section.bj-setup")).toHaveLength(1);
    expect(container.querySelectorAll("section.bj-pipeline")).toHaveLength(1);
    expect(calls().filter((call) => call === "GET /applications")).toHaveLength(1);
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

  it("has exactly one gold fill with a lit item and none without", async () => {
    answer({ "/today": [proposalItem, readyItem] });
    const lit = await draw();
    expect(lit.container.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(1);
    lit.unmount();
    answer({ "/today": [trackedItem] });
    const unlit = await draw();
    expect(unlit.container.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(0);
  });
});

describe("telling a screen reader what happened", () => {
  it("says what was done once it is done, in the page's polite status", async () => {
    answer({ "/today": [proposalItem, trackedItem], "POST /today/p1/accept-proposal": {}, "POST /today/t1/dismiss": {} });
    const { container } = await draw();
    expect(status(container)).toBe("");
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
    // The same sentence the page shows, so a screen reader hears what a sighted person reads.
    expect(status(container)).toBe("Data Engineer at Acme moved to Interviewing.");
    expect(words(query(container, ".bj-today-confirmation"))).toBe(`${status(container)} Open workspace`);
    click(buttonNamed(container, "Dismiss"));
    await flush();
    expect(status(container)).toBe("Dismissed.");
  });

  it("says a track was done even though the control only changed into a link", async () => {
    answer({ "/today": [matchItem], "POST /discover/track": { id: "app-9" } });
    const { container } = await draw();
    click(buttonNamed(band(container), "Track"));
    await flush();
    expect(status(container)).toBe("Tracked.");
  });

  it("leaves a failure to its own alert and says nothing stale", async () => {
    answer({ "/today": [trackedItem], "POST /today/t1/dismiss": new FakeApiError(500, "Request failed (500)") });
    const { container } = await draw();
    click(buttonNamed(container, "Dismiss"));
    await flush();
    expect(status(container)).toBe("");
    expect(container.querySelector(".bj-state--failure")?.getAttribute("role")).toBe("alert");
  });
});

describe("where keyboard focus goes when its control is gone", () => {
  const focusTarget = () => document.getElementById("next-step-focus");

  it("goes to the band's next step after Accept replaces its own button", async () => {
    answer({ "/today": [proposalItem, readyItem], "POST /today/p1/accept-proposal": {} });
    const { container } = await draw();
    const accept = buttonNamed(band(container), "Accept: move to Interviewing");
    accept.focus();
    click(accept);
    await flush();
    // The next lit item is a link, a new element: focus is on it and not dropped to the page.
    expect(document.activeElement).toBe(focusTarget());
    expect(document.activeElement?.tagName).toBe("A");
    expect(words(document.activeElement)).toBe("Open workspace");
    // The confirmation has an Open workspace link of its own now; focus is on the band's, which is
    // the person's next step, and not on the confirmation's.
    const confirmationLink = query(container, ".bj-today-confirmation a");
    expect(confirmationLink.getAttribute("href")).toBe("/applications/app-1");
    expect(document.activeElement).not.toBe(confirmationLink);
    expect(band(container).contains(document.activeElement)).toBe(true);
    expect(document.activeElement?.getAttribute("href")).toBe("/applications/app-2");
  });

  it("goes to the new Open workspace link after Track turns its button into one", async () => {
    answer({ "/today": [matchItem], "POST /discover/track": { id: "app-9" } });
    const { container } = await draw();
    const track = buttonNamed(band(container), "Track");
    track.focus();
    click(track);
    await flush();
    expect(document.activeElement).toBe(focusTarget());
    expect(words(document.activeElement)).toBe("Open workspace");
  });

  it("goes to the band's sentence when nothing is lit any more", async () => {
    answer({ "/today": [heldItem, trackedItem], "/applications/app-1/prepare-result": { result: { resume: null, gate_outcome: "REJECT_MISMATCH" } }, "POST /today/f1/dismiss": {} });
    const { container } = await draw();
    const dismiss = buttonNamed(band(container), "Dismiss");
    dismiss.focus();
    click(dismiss);
    await flush();
    expect(band(container).className).toContain("bj-band--unlit");
    expect(document.activeElement).toBe(query(band(container), ".bj-band-sentence"));
  });

  it("goes to the list's heading when a row it was on is removed", async () => {
    answer({ "/today": [readyItem, trackedItem, { ...trackedItem, id: "t2" }], "POST /today/t1/dismiss": {} });
    const { container } = await draw();
    const row = container.querySelector("li.bj-today-row") as HTMLElement;
    const dismiss = buttonNamed(row, "Dismiss");
    dismiss.focus();
    click(dismiss);
    await flush();
    expect(rows(container)).toEqual(["job_tracked"]);
    expect(document.activeElement).toBe(query(container, "#bj-today-activity"));
  });

  it("goes back to the band after Try again, once the page has read again", async () => {
    answer({ "/today": new FakeApiError(500, "Request failed (500)") });
    const { container } = await draw();
    const retry = buttonNamed(band(container), "Try again");
    retry.focus();
    answer({ "/today": [trackedItem] });
    click(retry);
    await flush();
    expect(document.activeElement).toBe(focusTarget());
    expect(band(container).contains(document.activeElement)).toBe(true);
  });

  it("does not take focus from where the person has put it", async () => {
    answer({ "/today": [matchItem], "POST /discover/track": { id: "app-9" } });
    const { container } = await draw();
    const outside = document.createElement("button");
    document.body.append(outside);
    click(buttonNamed(band(container), "Track"));
    outside.focus();
    await flush();
    expect(document.activeElement).toBe(outside);
  });
});

describe("a forced generation", () => {
  const prepareResult = { result: { resume: null, gate_outcome: "REJECT_MISMATCH", fit: { overall_score: 3.64 } } };

  it("says the resume was made and links to its workspace, instead of the card just vanishing", async () => {
    answer({
      "/today": [heldItem, trackedItem],
      "/applications/app-1/prepare-result": prepareResult,
      "POST /applications/app-1/prepare": { run_id: "r", resume: { artifact_id: "a" } },
      "POST /today/f1/dismiss": {},
    });
    const { container } = await draw();
    click(buttonNamed(band(container), "Generate anyway"));
    await flush();
    const confirmation = query(container, ".bj-today-confirmation");
    expect(words(confirmation)).toBe("Your resume for Data Engineer at Acme was generated. Open workspace");
    expect(query(confirmation, "a").getAttribute("href")).toBe("/applications/app-1");
    expect(status(container)).toBe("Your resume for Data Engineer at Acme was generated.");
  });

  it("still takes the card out and confirms when retiring it fails, so the page never goes on saying it is held back", async () => {
    answer({
      "/today": [heldItem, trackedItem],
      "/applications/app-1/prepare-result": prepareResult,
      "POST /applications/app-1/prepare": { run_id: "r", resume: { artifact_id: "a" } },
      "POST /today/f1/dismiss": new FakeApiError(500, "boom"),
    });
    const { container } = await draw();
    click(buttonNamed(band(container), "Generate anyway"));
    await flush();
    expect(container.querySelector("[data-kind='resume_failed']")).toBeNull();
    expect(container.textContent).not.toContain("held back your draft");
    expect(container.querySelector(".bj-state--failure")).toBeNull();
    expect(container.querySelector(".bj-today-confirmation")).not.toBeNull();
  });

  it("clears the confirmation when the next action starts", async () => {
    answer({
      "/today": [heldItem, trackedItem],
      "/applications/app-1/prepare-result": prepareResult,
      "POST /applications/app-1/prepare": { run_id: "r", resume: { artifact_id: "a" } },
      "POST /today/f1/dismiss": {},
      "POST /today/t1/dismiss": {},
    });
    const { container } = await draw();
    click(buttonNamed(band(container), "Generate anyway"));
    await flush();
    expect(container.querySelector(".bj-today-confirmation")).not.toBeNull();
    click(buttonNamed(container, "Dismiss"));
    await flush();
    expect(container.querySelector(".bj-today-confirmation")).toBeNull();
  });

  it("announces a setup failure that needs a key, so the person who pressed the button hears it", async () => {
    answer({
      "/today": [heldItem],
      "POST /applications/app-1/prepare": new FakeApiError(409, "Add a model key first.", { code: "SETUP_REQUIRED", settingsPath: "/profile/integrations", capability: "prepare_application" }),
    });
    const { container } = await draw();
    click(buttonNamed(band(container), "Generate anyway"));
    await flush();
    expect(query(container, ".bj-today-problem").getAttribute("role")).toBe("alert");
  });
});

// A reply for another application, older than the lit one, so the band holds the first and this
// one stays in the list with its own Accept.
const listedProposal = {
  ...proposalItem,
  id: "p2",
  application_id: "app-2",
  created_at: "2026-09-22T08:30:00Z",
  status_proposal: { ...proposalItem.status_proposal, id: "sp2" },
};

describe("an accepted proposal", () => {
  const confirmationOf = (container: HTMLElement) => words(container.querySelector(".bj-today-confirmation"));
  const listRow = (container: HTMLElement) => container.querySelectorAll("li.bj-today-row")[0] as HTMLElement;

  it("names the application that was pressed from a list row, not the one the band holds", async () => {
    answer({ "/today": [proposalItem, listedProposal, trackedItem], "POST /today/p2/accept-proposal": {} });
    const { container } = await draw();
    // The band holds the Data Engineer reply; the Orbit reply is a row of the list.
    expect(words(query(band(container), ".bj-band-sentence"))).toContain("Acme");
    const row = listRow(container);
    expect(row.getAttribute("data-kind")).toBe("status_proposal");
    click(buttonNamed(row, "Accept: move to Interviewing"));
    await flush();
    expect(calls().filter((call) => call.includes("accept-proposal"))).toEqual(["POST /today/p2/accept-proposal"]);
    expect(confirmationOf(container)).toBe("ML Engineer at Orbit moved to Interviewing. Open workspace");
    expect(query(container, ".bj-today-confirmation a").getAttribute("href")).toBe("/applications/app-2");
    expect(status(container)).toBe("ML Engineer at Orbit moved to Interviewing.");
    // The band's own item is untouched.
    expect(buttonNamed(band(container), "Accept: move to Interviewing")).toBeTruthy();
  });

  // The five stages Accept can move an application to, with the type that leads to each. The words
  // are written out here on purpose, so a changed stage word fails the rows below; the next test
  // fails when a type is added or removed without a row being added or removed with it.
  const STAGES: [string, string][] = [
    ["assessment.received", "Screening"],
    ["interview.requested", "Interviewing"],
    ["interview.scheduled", "Interviewing"],
    ["offer.received", "Offer"],
    ["application.rejected", "Rejected"],
  ];

  it("has a row below for every type Accept can move an application for", () => {
    expect(Object.keys(PROPOSAL_STEPS).sort()).toEqual(STAGES.map(([proposedType]) => proposedType).sort());
  });

  it.each(STAGES)("says %s moved the application to %s, in the line and in the polite status", async (proposedType, stage) => {
    const item = { ...proposalItem, status_proposal: { ...proposalItem.status_proposal, proposed_type: proposedType } };
    answer({ "/today": [item], "POST /today/p1/accept-proposal": {} });
    const { container } = await draw();
    click(buttonNamed(band(container), `Accept: move to ${stage}`));
    await flush();
    expect(confirmationOf(container)).toBe(`Data Engineer at Acme moved to ${stage}. Open workspace`);
    expect(status(container)).toBe(`Data Engineer at Acme moved to ${stage}.`);
  });

  it("says 'The application' when the applications list could not be read, and still links to its workspace", async () => {
    answer({ "/today": [proposalItem], "/applications": new FakeApiError(500, "boom"), "POST /today/p1/accept-proposal": {} });
    const { container } = await draw();
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
    expect(confirmationOf(container)).toBe("The application moved to Interviewing. Open workspace");
    expect(query(container, ".bj-today-confirmation a").getAttribute("href")).toBe("/applications/app-1");
    expect(status(container)).toBe("The application moved to Interviewing.");
  });

  it("says 'this application', not 'this job', when other replies lost their Accept and no job could be named", async () => {
    // The sentence before it has no job in it, so "this job" would have nothing to point at.
    const older = { ...proposalItem, id: "s1", created_at: "2026-09-22T09:00:00Z", status_proposal: { ...proposalItem.status_proposal, id: "sp-s1", proposed_type: "offer.received" } };
    answer({ "/today": [proposalItem, older], "/applications": new FakeApiError(500, "boom"), "POST /today/p1/accept-proposal": {} });
    const { container } = await draw();
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
    const sentence = "The application moved to Interviewing. 1 other reply about this application can no longer be accepted.";
    expect(confirmationOf(container)).toBe(`${sentence} Open workspace`);
    expect(status(container)).toBe(sentence);
  });

  it("shows no confirmation for a failed accept, keeps the card with its Retry, and confirms only once Retry succeeds", async () => {
    answer({ "/today": [proposalItem], "POST /today/p1/accept-proposal": new FakeApiError(409, "this proposal was already dismissed, not pending anymore") });
    const { container } = await draw();
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
    expect(container.querySelector(".bj-today-confirmation")).toBeNull();
    expect(status(container)).toBe("");
    expect(words(query(band(container), ".bj-state--failure"))).toContain("already dismissed");
    expect(buttonNamed(band(container), "Accept: move to Interviewing")).toBeTruthy();

    answer({ "/today": [proposalItem], "POST /today/p1/accept-proposal": {} });
    click(buttonNamed(band(container), "Retry"));
    await flush();
    expect(calls().filter((call) => call === "POST /today/p1/accept-proposal")).toHaveLength(2);
    expect(container.querySelector(".bj-state--failure")).toBeNull();
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. Open workspace");
  });

  it("clears the confirmation when the next action starts", async () => {
    answer({ "/today": [proposalItem, trackedItem], "POST /today/p1/accept-proposal": {}, "POST /today/t1/dismiss": () => new Promise(() => {}) });
    const { container } = await draw();
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
    expect(container.querySelector(".bj-today-confirmation")).not.toBeNull();
    click(buttonNamed(container, "Dismiss"));
    await flush();
    expect(container.querySelector(".bj-today-confirmation")).toBeNull();
  });

  it("keeps focus on the list's heading when Accept is pressed on a row and other rows remain", async () => {
    answer({ "/today": [proposalItem, listedProposal, trackedItem], "POST /today/p2/accept-proposal": {} });
    const { container } = await draw();
    const accept = buttonNamed(listRow(container), "Accept: move to Interviewing");
    accept.focus();
    click(accept);
    await flush();
    expect(container.contains(accept)).toBe(false);
    expect(document.activeElement).toBe(query(container, `#${ACTIVITY_HEADING_ID}`));
    expect(document.activeElement).not.toBe(query(container, ".bj-today-confirmation a"));
  });

  it("falls back to the band's next step when Accept was pressed on the list's last row", async () => {
    answer({ "/today": [proposalItem, listedProposal], "POST /today/p2/accept-proposal": {} });
    const { container } = await draw();
    const accept = buttonNamed(listRow(container), "Accept: move to Interviewing");
    accept.focus();
    click(accept);
    await flush();
    // The list is gone with its last row: focus lands in the band, not on the page.
    expect(container.querySelector(`#${ACTIVITY_HEADING_ID}`)).toBeNull();
    expect(document.activeElement).toBe(document.getElementById(NEXT_STEP_FOCUS_ID));
    expect(band(container).contains(document.activeElement)).toBe(true);
  });
});

// The server settles the other pending replies for an application when one is accepted, and leaves
// their items in the feed. The page marks them decided too, so none offers an Accept that would be
// refused, and says how many lost theirs.
describe("the other replies about an application once one is accepted", () => {
  // A reply for app-1 older than the lit one (10:00), so the band holds `proposalItem` and these are rows.
  const sibling = (id: string, overrides: { type?: string; status?: string; at?: string; application?: string } = {}) => ({
    ...proposalItem,
    id,
    application_id: overrides.application ?? "app-1",
    created_at: overrides.at ?? "2026-09-22T09:00:00Z",
    status_proposal: { ...proposalItem.status_proposal, id: `sp-${id}`, proposed_type: overrides.type ?? "offer.received", status: overrides.status ?? "pending" },
  });
  const rowNamed = (container: HTMLElement, name: string) => [...container.querySelectorAll("li.bj-today-row")].find((row) => words(row).includes(name)) as HTMLElement;
  const confirmationOf = (container: HTMLElement) => words(container.querySelector(".bj-today-confirmation"));
  const acceptLit = async (container: HTMLElement) => {
    click(buttonNamed(band(container), "Accept: move to Interviewing"));
    await flush();
  };

  it("takes Accept and 'Needs your decision' off a reply that could be accepted, and its Dismiss then uses the plain route", async () => {
    answer({ "/today": [proposalItem, sibling("s1")], "POST /today/p1/accept-proposal": {}, "POST /today/s1/dismiss": {} });
    const { container } = await draw();
    const before = rowNamed(container, "Offer");
    expect(words(before)).toContain("Needs your decision");
    expect(buttonNamed(before, "Accept: move to Offer")).toBeTruthy();

    await acceptLit(container);
    const row = rowNamed(container, "Possible update");
    expect(row.getAttribute("data-kind")).toBe("status_proposal");
    expect(container.textContent).not.toContain("Accept: move to");
    expect(container.textContent).not.toContain("Needs your decision");
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. 1 other reply about this job can no longer be accepted. Open workspace");
    expect(status(container)).toBe("Data Engineer at Acme moved to Interviewing. 1 other reply about this job can no longer be accepted.");
    // The band has nothing to hold: the sibling is not offered to it either.
    expect(band(container).className).toContain("bj-band--unlit");

    click(buttonNamed(row, "Dismiss"));
    await flush();
    expect(calls()).toContain("POST /today/s1/dismiss");
    expect(calls().filter((call) => call.includes("dismiss-proposal"))).toEqual([]);
    expect(calls().filter((call) => call.includes("accept-proposal"))).toEqual(["POST /today/p1/accept-proposal"]);
    expect(rows(container)).toEqual([]);
  });

  it("counts the replies that lost an Accept, in the plural, and not the ones that never had one", async () => {
    answer({
      "/today": [proposalItem, sibling("s1"), sibling("s2", { type: "assessment.received", at: "2026-09-22T08:00:00Z" }), sibling("s3", { type: "recruiter.replied", at: "2026-09-22T07:00:00Z" })],
      "POST /today/p1/accept-proposal": {},
    });
    const { container } = await draw();
    await acceptLit(container);
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. 2 other replies about this job can no longer be accepted. Open workspace");
    expect(status(container)).not.toMatch(/dismiss|pending/i);
    expect(confirmationOf(container)).not.toMatch(/dismiss|pending/i);
  });

  it("sends the Dismiss of a reply with no stage to move to the same plain route, and says nothing of it", async () => {
    answer({
      "/today": [proposalItem, sibling("s2", { type: "recruiter.replied" }), sibling("s3", { type: "acknowledged", at: "2026-09-22T08:00:00Z" })],
      "POST /today/p1/accept-proposal": {},
      "POST /today/s2/dismiss": {},
      "POST /today/s3/dismiss": {},
    });
    const { container } = await draw();
    // Before: these were only ever offered Dismiss, through the proposal route.
    expect(rows(container)).toEqual(["status_proposal", "status_proposal"]);
    await acceptLit(container);
    // Neither had an Accept, so neither is counted.
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. Open workspace");
    for (const row of container.querySelectorAll("li.bj-today-row")) click(buttonNamed(row, "Dismiss"));
    await flush();
    expect(calls()).toEqual(expect.arrayContaining(["POST /today/s2/dismiss", "POST /today/s3/dismiss"]));
    expect(calls().filter((call) => call.includes("dismiss-proposal"))).toEqual([]);
    expect(rows(container)).toEqual([]);
  });

  it("takes a sibling's failed Dismiss and its Retry away with the Accept it lost, so the next Dismiss goes the plain way", async () => {
    answer({
      "/today": [proposalItem, sibling("s1")],
      "POST /today/s1/dismiss-proposal": new FakeApiError(500, "Request failed (500)"),
      "POST /today/p1/accept-proposal": {},
      "POST /today/s1/dismiss": {},
    });
    const { container } = await draw();
    const row = rowNamed(container, "Offer");
    click(buttonNamed(row, "Dismiss"));
    await flush();
    expect(words(query(row, ".bj-state--failure"))).toContain("Request failed (500)");
    expect(buttonNamed(row, "Retry")).toBeTruthy();

    await acceptLit(container);
    expect(container.querySelector(".bj-state--failure")).toBeNull();
    expect(() => buttonNamed(container, "Retry")).toThrow();
    // Dismiss again: the one request that goes out is the plain one.
    click(buttonNamed(container.querySelector("li.bj-today-row") as HTMLElement, "Dismiss"));
    await flush();
    expect(calls().filter((call) => call === "POST /today/s1/dismiss-proposal")).toHaveLength(1);
    expect(calls()).toContain("POST /today/s1/dismiss");
    expect(rows(container)).toEqual([]);
  });

  it("takes the failure and Retry off a reply that never had an Accept, too", async () => {
    answer({
      "/today": [proposalItem, sibling("s2", { type: "recruiter.replied" })],
      "POST /today/s2/dismiss-proposal": new FakeApiError(500, "Request failed (500)"),
      "POST /today/p1/accept-proposal": {},
    });
    const { container } = await draw();
    const row = container.querySelector("li.bj-today-row") as HTMLElement;
    click(buttonNamed(row, "Dismiss"));
    await flush();
    expect(buttonNamed(row, "Retry")).toBeTruthy();

    await acceptLit(container);
    // Nothing is said of it, but it was settled all the same: its Retry would be refused too.
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. Open workspace");
    expect(container.querySelector(".bj-state--failure")).toBeNull();
    expect(() => buttonNamed(container, "Retry")).toThrow();
  });

  it("keeps an unrelated reply's failure and its Retry, while a sibling's goes", async () => {
    // x1 is another application's reply with no stage, so it stays a row with its failure on it.
    answer({
      "/today": [proposalItem, sibling("s1"), sibling("x1", { type: "recruiter.replied", application: "app-2", at: "2026-09-22T08:00:00Z" })],
      "POST /today/s1/dismiss-proposal": new FakeApiError(500, "Request failed (500)"),
      "POST /today/x1/dismiss-proposal": new FakeApiError(500, "Request failed (500)"),
      "POST /today/p1/accept-proposal": {},
    });
    const { container } = await draw();
    const [offer, other] = [...container.querySelectorAll("li.bj-today-row")] as HTMLElement[];
    click(buttonNamed(offer, "Dismiss"));
    click(buttonNamed(other, "Dismiss"));
    await flush();
    expect(container.querySelectorAll(".bj-state--failure")).toHaveLength(2);

    await acceptLit(container);
    const kept = container.querySelectorAll("li.bj-today-row")[1] as HTMLElement;
    expect(container.querySelectorAll(".bj-state--failure")).toHaveLength(1);
    expect(kept.querySelector(".bj-state--failure")).not.toBeNull();
    // Its Retry is still the request it was for.
    click(buttonNamed(kept, "Retry"));
    await flush();
    expect(calls().filter((call) => call === "POST /today/x1/dismiss-proposal")).toHaveLength(2);
    expect(calls().filter((call) => call === "POST /today/s1/dismiss-proposal")).toHaveLength(1);
  });

  it("leaves the other replies offering Accept, and says nothing of them, when the Accept itself failed", async () => {
    answer({ "/today": [proposalItem, sibling("s1")], "POST /today/p1/accept-proposal": new FakeApiError(500, "Request failed (500)") });
    const { container } = await draw();
    await acceptLit(container);
    expect(container.querySelector(".bj-today-confirmation")).toBeNull();
    expect(status(container)).toBe("");
    expect(query(band(container), ".bj-state--failure")).toBeTruthy();
    const older = rowNamed(container, "Offer");
    expect(buttonNamed(older, "Accept: move to Offer")).toBeTruthy();
    expect(words(older)).toContain("Needs your decision");
  });

  it("leaves a reply whose own request is out as it is, with its Accept, and does not count it", async () => {
    answer({ "/today": [proposalItem, sibling("s1")], "POST /today/p1/accept-proposal": {}, "POST /today/s1/dismiss-proposal": () => new Promise(() => {}) });
    const { container } = await draw();
    const row = rowNamed(container, "Offer");
    click(buttonNamed(row, "Dismiss"));
    await flush();
    expect(words(buttonNamed(row, "Dismissing..."))).toBe("Dismissing...");
    await acceptLit(container);
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. Open workspace");
    // It is still a reply that can be accepted, so the band holds it now, with its request still out.
    expect(words(band(container))).toContain("Needs your decision");
    expect(buttonNamed(band(container), "Accept: move to Offer")).toBeTruthy();
    expect(words(buttonNamed(band(container), "Dismissing..."))).toBe("Dismissing...");
  });

  it("does not count a reply that another action has just taken out of the feed, even in the same batch of updates", async () => {
    let finishDismiss: (value: unknown) => void = () => {};
    let finishAccept: (value: unknown) => void = () => {};
    answer({
      "/today": [proposalItem, sibling("s1")],
      "POST /today/s1/dismiss-proposal": () => new Promise((resolve) => (finishDismiss = resolve)),
      "POST /today/p1/accept-proposal": () => new Promise((resolve) => (finishAccept = resolve)),
    });
    const { container } = await draw();
    click(buttonNamed(rowNamed(container, "Offer"), "Dismiss"));
    await acceptLit(container);
    // Both answers arrive before the page has drawn either. The order is what makes this test bite:
    // the dismiss is answered first, so its continuation has taken the reply out of the feed by the
    // time the accept's reads the feed. Answered the other way round, the reply would still have a
    // request out and be left alone, and nothing here would be put to the test.
    await act(async () => {
      finishDismiss({});
      finishAccept({});
    });
    expect(rows(container)).toEqual([]);
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. Open workspace");
  });

  // A smoke pin that the page settles the same way when React runs its renders and state updaters
  // twice. Other tests also fail for a count taken in the wrong place, so this is not the only guard.
  it("counts each reply once, and leaves the same cards, under StrictMode", async () => {
    answer({
      "/today": [proposalItem, sibling("s1"), sibling("s2", { type: "recruiter.replied", at: "2026-09-22T08:00:00Z" })],
      "POST /today/p1/accept-proposal": {},
    });
    installViewport(1000);
    const { container } = render(
      <StrictMode>
        <MemoryRouter>
          <Today />
        </MemoryRouter>
      </StrictMode>,
    );
    await flush();
    await acceptLit(container);
    expect(calls().filter((call) => call.includes("accept-proposal"))).toEqual(["POST /today/p1/accept-proposal"]);
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. 1 other reply about this job can no longer be accepted. Open workspace");
    expect(container.textContent).not.toContain("Accept: move to");
    expect(rows(container)).toEqual(["status_proposal", "status_proposal"]);
  });

  it("leaves another application's reply with its Accept, and does not count it", async () => {
    answer({ "/today": [proposalItem, listedProposal], "POST /today/p1/accept-proposal": {} });
    const { container } = await draw();
    await acceptLit(container);
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. Open workspace");
    // It is the next thing for the band to hold, with its own Accept.
    expect(buttonNamed(band(container), "Accept: move to Interviewing")).toBeTruthy();
    expect(words(query(band(container), ".bj-band-sentence"))).toContain("Orbit");
  });

  it("does not count a reply that was already decided", async () => {
    answer({
      "/today": [proposalItem, sibling("s1"), sibling("done", { status: "dismissed", at: "2026-09-22T08:00:00Z" }), sibling("taken", { status: "accepted", at: "2026-09-22T07:00:00Z" })],
      "POST /today/p1/accept-proposal": {},
    });
    const { container } = await draw();
    await acceptLit(container);
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Interviewing. 1 other reply about this job can no longer be accepted. Open workspace");
  });

  it("stops the band holding a reply the server has closed when Accept was pressed on another one of the same application's rows", async () => {
    // The band holds the newest reply (p1). The older one in the list is the one accepted.
    answer({ "/today": [proposalItem, sibling("s1")], "POST /today/s1/accept-proposal": {} });
    const { container } = await draw();
    const accept = buttonNamed(rowNamed(container, "Offer"), "Accept: move to Offer");
    accept.focus();
    click(accept);
    await flush();
    expect(calls().filter((call) => call.includes("accept-proposal"))).toEqual(["POST /today/s1/accept-proposal"]);
    expect(confirmationOf(container)).toBe("Data Engineer at Acme moved to Offer. 1 other reply about this job can no longer be accepted. Open workspace");
    expect(band(container).className).toContain("bj-band--unlit");
    expect(container.textContent).not.toContain("Accept: move to");
    // The band's reply went into the list as a record, and the pressed row went: focus is on the heading.
    expect(rows(container)).toEqual(["status_proposal"]);
    expect(document.activeElement).toBe(query(container, `#${ACTIVITY_HEADING_ID}`));
  });
});

describe("when every read fails", () => {
  it("reads the account and the feed again with the one Try again the band holds", async () => {
    const down = new FakeApiError(500, "boom");
    answer({ "/today": down, "/profile/current": down, "/credentials": down, "/saved-searches": down, "/applications": down });
    const { container } = await draw();
    expect(words(query(band(container), ".bj-band-sentence"))).toContain("Couldn't check your setup");
    const failures = [...container.querySelectorAll(".bj-today-main .bj-state--failure")].map(words);
    expect(failures.some((text) => text.includes("Today's items couldn't be read"))).toBe(true);

    answer({ "/today": [trackedItem] });
    api.apiFetch.mockClear();
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(calls().sort()).toEqual(["GET /applications", "GET /credentials", "GET /profile/current", "GET /saved-searches", "GET /today"].sort());
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Nothing needs a decision right now. 1 update below.");
    expect(container.querySelector(".bj-today-main .bj-state--failure")).toBeNull();
    expect(rows(container)).toEqual(["job_tracked"]);
  });

  it("reads only the account when the feed is fine", async () => {
    answer({ "/profile/current": new FakeApiError(500, "boom"), "/today": [trackedItem] });
    const { container } = await draw();
    answer({ "/today": [trackedItem] });
    api.apiFetch.mockClear();
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(calls()).not.toContain("GET /today");
  });
});

describe("the list while the band is still working it out", () => {
  it("holds the list back, so an item is never drawn with live buttons and then moved into the band", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "/today": [proposalItem, trackedItem], "/applications": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    expect(band(container).getAttribute("aria-busy")).toBe("true");
    expect(rows(container)).toEqual([]);
    expect(container.textContent).not.toContain("Accept: move to Interviewing");
    expect(container.querySelector(".bj-today-main .bj-state--loading")).not.toBeNull();

    await act(async () => release(APPLICATIONS));
    expect(container.querySelector(".bj-today-main .bj-state--loading")).toBeNull();
    expect(rows(container)).toEqual(["job_tracked"]);
    expect(buttonNamed(band(container), "Accept: move to Interviewing")).toBeTruthy();
  });

  it("holds it again when Try again reads the account again, and settles the same way", async () => {
    answer({ "/profile/current": new FakeApiError(500, "boom"), "/today": [proposalItem, trackedItem] });
    const { container } = await draw();
    expect(rows(container)).toEqual(["status_proposal", "job_tracked"]);
    let release: (value: unknown) => void = () => {};
    answer({ "/today": [proposalItem, trackedItem], "/applications": () => new Promise((resolve) => (release = resolve)) });
    click(buttonNamed(band(container), "Try again"));
    await flush();
    expect(rows(container)).toEqual([]);
    await act(async () => release(APPLICATIONS));
    expect(rows(container)).toEqual(["job_tracked"]);
  });
});
