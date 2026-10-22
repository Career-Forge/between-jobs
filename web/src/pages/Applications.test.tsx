// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { choose, settle, text, typeInto } from "../testing/forms";
import { buttonNamed, click, installViewport, keydown, query, render } from "../testing/render";
import Applications from "./Applications";

// The page itself, against a fake API: which requests it makes, what the band says as the answers
// arrive, the list, the board and the phone's one-status-at-a-time list, a status change from the
// first choice to the re-read after it, and what each failure leaves behind. Every state is reached
// here through the page's own requests, since the view that draws them takes only props.

const api = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("../lib/api", () => ({ apiFetch: api.apiFetch }));
const hiring = vi.hoisted(() => ({ enabled: true }));
vi.mock("../lib/useHiringSignalsStatus", () => ({ useHiringSignalsEnabled: () => hiring.enabled }));

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

function snapshot(title: string, company: string, location: string | null = null) {
  return { id: `snap-${title}`, title, company_name: company, location_text: location, source_url: `https://jobs.example/${title.replace(/\s/g, "-")}` };
}

const SAVED = {
  id: "a1",
  status: "saved",
  source_channel: "discover",
  created_at: "2026-09-22T12:00:00Z",
  updated_at: "2026-09-22T12:00:00Z",
  date_applied: null,
  snapshot: snapshot("Data Engineer", "Acme", "Remote"),
  resume_exists: true,
};
const APPLIED = {
  id: "a2",
  status: "applied",
  source_channel: "web",
  created_at: "2026-09-20T12:00:00Z",
  updated_at: "2026-09-21T12:00:00Z",
  date_applied: "2026-09-21",
  snapshot: snapshot("ML Engineer", "Orbit"),
  resume_exists: false,
};
const INTERVIEW = {
  id: "a3",
  status: "interviewing",
  source_channel: "telegram",
  created_at: "2026-09-18T12:00:00Z",
  updated_at: "2026-09-19T12:00:00Z",
  date_applied: "2026-09-19",
  snapshot: snapshot("Analytics Engineer", "Initech", "New York"),
  resume_exists: true,
};
const APPLICATIONS = [SAVED, APPLIED, INTERVIEW];

const GOOD_PROFILE = { id: "pv-1", activated_at: "2026-09-12T10:00:00Z" };
const GOOD_CREDENTIALS = [{ service: "llm", provider: "openrouter", is_validated: true, model: "vendor/model" }];

// This environment has no usable localStorage, so the remembered status goes to a plain map.
const memory = new Map<string, string>();
const storage = {
  getItem: (key: string) => memory.get(key) ?? null,
  setItem: (key: string, value: string) => void memory.set(key, value),
};

let replies: Record<string, Reply> = {};
const calls = () => api.apiFetch.mock.calls.map(([path, init]) => `${init?.method ?? "GET"} ${path}`);
const bodyOf = (call: string) => {
  const found = api.apiFetch.mock.calls.find(([path, init]) => `${init?.method ?? "GET"} ${path}` === call);
  return found?.[1]?.body === undefined ? undefined : JSON.parse(found[1].body);
};

function answer(extra: Record<string, Reply> = {}) {
  replies = {
    "/applications": APPLICATIONS,
    "/profile/current": GOOD_PROFILE,
    "/credentials": GOOD_CREDENTIALS,
    "POST /applications/a1/stage": {},
    "POST /applications/a2/stage": {},
    "POST /applications/a3/stage": {},
    ...extra,
  };
}

const LOCATION = "bj-test-location";
function Where() {
  const location = useLocation();
  return <p id={LOCATION}>{`${location.pathname}${location.hash}`}</p>;
}

async function draw(width = 1000, at = "/applications") {
  const viewport = installViewport(width);
  const rendered = render(
    <MemoryRouter initialEntries={[at]}>
      <Applications />
      <Where />
    </MemoryRouter>,
  );
  await settle();
  return { ...rendered, viewport };
}

const band = (container: HTMLElement) => query(container, 'section[aria-label="Next step"]');
const sentence = (container: HTMLElement) => text(query(band(container), ".bj-band-sentence"));
const why = (container: HTMLElement) => text(query(band(container), ".bj-band-why"));
const cyanFills = (container: HTMLElement) => container.querySelectorAll(".bj-primary, .bj-button-link-primary").length;
const status = (container: HTMLElement) => text(query(container, ".bj-apps > .bj-visually-hidden[role='status']"));
const card = (container: HTMLElement, id: string) => query(container, `[data-app-id="${id}"]`);
const titles = (container: HTMLElement) => [...container.querySelectorAll(".bj-app-title")].map((title) => text(title));

beforeEach(() => {
  api.apiFetch.mockReset();
  api.apiFetch.mockImplementation(async (path: string, init?: { method?: string }) => {
    const method = init?.method ?? "GET";
    const found = replies[`${method} ${path}`] ?? replies[path];
    if (found === undefined) throw new Error(`no reply for ${method} ${path}`);
    if (found instanceof Error) throw found;
    const value = typeof found === "function" ? (found as () => unknown)() : found;
    if (value instanceof Error) throw value;
    return value;
  });
  answer();
  memory.clear();
  hiring.enabled = true;
  Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
  Element.prototype.scrollIntoView = vi.fn();
  if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function") {
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000000" });
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("what the page asks for", () => {
  it("reads the applications and the two setup facts the band needs, and nothing else", async () => {
    await draw();
    expect(calls().sort()).toEqual(["GET /applications", "GET /credentials", "GET /profile/current"]);
  });
});

describe("the band as the answers arrive", () => {
  it("shows only Loading... until the list and the setup reads have answered, with placeholder cards below", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "/applications": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    expect(band(container).getAttribute("aria-busy")).toBe("true");
    expect(text(band(container))).toBe("Next stepLoading...");
    expect(cyanFills(container)).toBe(0);
    expect(container.querySelector(".bj-state--loading")).not.toBeNull();
    expect(text(query(container, ".bj-state--loading"))).toBe("Loading your applications...");
    expect(container.querySelector(".bj-app-card")).toBeNull();
    // The tracking forms are usable meanwhile.
    expect(container.querySelectorAll(".bj-app-track-card")).toHaveLength(2);
    await act(async () => release(APPLICATIONS));
    expect(sentence(container)).toBe("Your move on 2 · their move on 1.");
  });

  it("is unlit when something is tracked: the choice is per card, and nothing on the page is cyan", async () => {
    const { container } = await draw();
    expect(sentence(container)).toBe("Your move on 2 · their move on 1.");
    expect(why(container)).toBe("why: 2 saved, screening, interviewing, offer · 1 applied");
    expect(band(container).classList.contains("bj-band--unlit")).toBe(true);
    expect(cyanFills(container)).toBe(0);
  });

  it("asks to start tracking when nothing is tracked, and that button is the page's one cyan fill", async () => {
    answer({ "/applications": [] });
    const { container } = await draw();
    expect(sentence(container)).toBe("Track your first job -- paste a posting or fetch it by URL.");
    expect(why(container)).toBe("why: 0 applications");
    expect(cyanFills(container)).toBe(1);
    expect(query(band(container), ".bj-primary").textContent).toBe("Start tracking");
    // The body's own copy of that action is an outlined button.
    expect(query(container, ".bj-app-track-card:last-child button[type='submit']").className).not.toContain("bj-primary");
  });

  it("sends the person to the paste form when they press Start tracking, and focuses its heading", async () => {
    answer({ "/applications": [] });
    const { container } = await draw();
    click(buttonNamed(band(container), "Start tracking"));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(query(container, "#bj-app-paste-heading"));
    expect(query(container, "#start-tracking").contains(document.activeElement)).toBe(true);
  });

  it("lands on the paste form for a link to /applications#start-tracking", async () => {
    const { container } = await draw(1000, "/applications#start-tracking");
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(document.activeElement).toBe(query(container, "#bj-app-paste-heading"));
  });

  it("scrolls to the same form from the header's Track a job", async () => {
    const { container } = await draw();
    click(buttonNamed(query(container, ".bj-page-header"), "Track a job"));
    expect(document.activeElement).toBe(query(container, "#bj-app-paste-heading"));
  });

  it("asks for the profile first when there is no active profile, over the list's own row, and still shows the list", async () => {
    answer({ "/profile/current": new FakeApiError(404, "no profile") });
    const { container } = await draw();
    expect(sentence(container)).toBe("Everything here starts from your profile. Import it first.");
    expect(query(band(container), "a.bj-button-link-primary").getAttribute("href")).toBe("/profile");
    expect(cyanFills(container)).toBe(1);
    expect(titles(container)).toEqual(["Data Engineer", "ML Engineer", "Analytics Engineer"]);
  });

  it("asks for a model key when the credentials say there is none", async () => {
    answer({ "/credentials": [] });
    const { container } = await draw();
    expect(sentence(container)).toBe("Add a model key so Between Jobs can draft and score.");
    expect(why(container)).toBe("why: profile active · no model key saved");
  });

  it("says the setup check failed, names the call, and Try again reads the two facts again", async () => {
    answer({ "/credentials": new FakeApiError(500, "boom") });
    const { container } = await draw();
    expect(sentence(container)).toBe("✕Couldn't check your setup. You didn't do anything wrong, and nothing was lost.");
    expect(why(container)).toMatch(/^why: GET \/credentials failed · \d\d:\d\d$/);
    answer({});
    api.apiFetch.mockClear();
    buttonNamed(band(container), "Try again").focus();
    click(buttonNamed(band(container), "Try again"));
    // The old failure is gone in the render the press made, not a render later.
    expect(text(band(container))).not.toContain("Couldn't check your setup");
    await settle();
    expect(calls().sort()).toEqual(["GET /credentials", "GET /profile/current"]);
    expect(sentence(container)).toBe("Your move on 2 · their move on 1.");
    expect(document.activeElement).toBe(query(band(container), ".bj-band-sentence"));
  });
});

describe("the list", () => {
  it("draws one card per application, each with its identity, status chip and status select", async () => {
    const { container } = await draw();
    expect(titles(container)).toEqual(["Data Engineer", "ML Engineer", "Analytics Engineer"]);
    const first = card(container, "a1");
    expect(text(query(first, ".bj-app-company"))).toBe("Acme -- Remote");
    expect(text(query(first, ".bj-app-chip"))).toBe("◇Saved");
    const select = query<HTMLSelectElement>(first, "select[data-role='status']");
    expect(select.value).toBe("saved");
    expect(select.getAttribute("aria-label")).toBe('Status of "Data Engineer" at Acme');
    expect([...select.options].map((option) => option.textContent)).toEqual([
      "Saved",
      "Applied",
      "Screening",
      "Interviewing",
      "Offer",
      "Rejected",
      "Withdrawn",
    ]);
    expect(text(query(card(container, "a3"), ".bj-app-chip"))).toBe("◑Interviewing");
  });

  it("says a draft exists only for the application that has one, as Resume drafted, never as a pass", async () => {
    const { container } = await draw();
    expect(card(container, "a1").querySelector(".bj-app-badge")).not.toBeNull();
    expect(text(query(card(container, "a1"), ".bj-app-badge"))).toBe("✦Resume drafted");
    expect(card(container, "a2").querySelector(".bj-app-badge")).toBeNull();
    expect(text(container)).not.toContain("Resume ✓");
    expect(container.querySelector(".bj-badge-emerald")).toBeNull();
  });

  it("shows the day it was applied only where one was returned", async () => {
    const { container } = await draw();
    expect(text(query(card(container, "a2"), ".bj-app-applied"))).toMatch(/^Applied Sep 21/);
    expect(card(container, "a1").querySelector(".bj-app-applied")).toBeNull();
  });

  it("words the day an application was moved back to Saved as First applied, as its workspace does", async () => {
    answer({ "/applications": [{ ...SAVED, date_applied: "2026-09-21" }, APPLIED, INTERVIEW] });
    const { container } = await draw();
    expect(text(query(card(container, "a1"), ".bj-app-applied"))).toMatch(/^First applied Sep 21/);
    expect(text(query(card(container, "a2"), ".bj-app-applied"))).toMatch(/^Applied Sep 21/);
  });

  it("ends each card with its trail, built from the channel and the date the list returned", async () => {
    const { container } = await draw();
    const trail = (id: string) => text(query(card(container, id), ".bj-app-card > .bj-trail"));
    expect(trail("a1")).toBe("● You tracked this · from Discover · Sep 22");
    expect(trail("a2")).toBe("● You tracked this · added on the web · Sep 20");
    expect(trail("a3")).toBe("● You tracked this · from Telegram · Sep 18");
    // It is the last line of the card.
    expect(card(container, "a1").lastElementChild?.classList.contains("bj-trail")).toBe(true);
  });

  it("leaves out what the list did not return: an unknown channel, an unreadable date", async () => {
    answer({ "/applications": [{ ...SAVED, source_channel: "carrier-pigeon", created_at: "sometime" }] });
    const { container } = await draw();
    expect(text(query(card(container, "a1"), ".bj-app-card > .bj-trail"))).toBe("● You tracked this");
  });

  it("falls back to Untitled and Unknown company for a row with no snapshot, and says nothing else about it", async () => {
    answer({ "/applications": [{ ...SAVED, snapshot: null }] });
    const { container } = await draw();
    expect(titles(container)).toEqual(["Untitled"]);
    expect(text(query(container, ".bj-app-company"))).toBe("Unknown company");
    expect(container.querySelector("a[href^='https://']")).toBeNull();
  });

  it("links to the posting and the workspace", async () => {
    const { container } = await draw();
    const first = card(container, "a1");
    const posting = query<HTMLAnchorElement>(first, "a[target='_blank']");
    expect(posting.textContent).toBe("View posting");
    expect(posting.getAttribute("href")).toBe("https://jobs.example/Data-Engineer");
    expect(posting.getAttribute("rel")).toContain("noreferrer");
    expect(query(first, "a[href='/applications/a1']").textContent).toBe("Open workspace");
  });

  it("offers the actions menu with Find Hiring Posts only while the feature is on", async () => {
    const on = await draw();
    const options = (container: HTMLElement) => [...query<HTMLSelectElement>(card(container, "a1"), "select[aria-label^='Actions']").options].map((option) => option.textContent);
    expect(options(on.container)).toContain("Find Hiring Posts");
    on.unmount();
    hiring.enabled = false;
    const off = await draw();
    expect(options(off.container)).not.toContain("Find Hiring Posts");
    expect(options(off.container)).toEqual(["Actions...", "Generate Docs", "Research Company", "Find Contacts", "Find Events", "Practice Interview", "Your Play"]);
  });

  it("jumps to the workspace panel the actions menu names", async () => {
    const { container } = await draw();
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[aria-label^='Actions']"), "interview-practice");
    expect(text(query(container, `#${LOCATION}`))).toBe("/applications/a1#interview-practice");
  });

  it("has no serious accessibility violation", async () => {
    const { container } = await draw();
    await expectNoSeriousA11yViolations(container);
  });
});

describe("changing a status", () => {
  it("sends the move with an idempotency key, reads the list again, and says what moved", async () => {
    let applications: unknown[] = APPLICATIONS;
    answer({
      "/applications": () => applications,
      "POST /applications/a1/stage": () => {
        applications = [{ ...SAVED, status: "applied", date_applied: "2026-10-05" }, APPLIED, INTERVIEW];
        return {};
      },
    });
    const { container } = await draw();
    api.apiFetch.mockClear();
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "applied");
    await settle();
    expect(calls()).toEqual(["POST /applications/a1/stage", "GET /applications"]);
    const body = bodyOf("POST /applications/a1/stage");
    expect(body.new_status).toBe("applied");
    expect(typeof body.idempotency_key).toBe("string");
    expect(body.idempotency_key.length).toBeGreaterThan(0);
    expect(text(query(card(container, "a1"), ".bj-app-chip"))).toBe("●Applied");
    expect(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']").value).toBe("applied");
    expect(status(container)).toBe('Moved "Data Engineer" at Acme to Applied.');
    expect(sentence(container)).toBe("Your move on 1 · their move on 2.");
  });

  it("holds the card still while its request is out: aria-disabled, Moving..., and a second change is ignored", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "POST /applications/a1/stage": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    const select = query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']");
    choose(select, "applied");
    await settle(1);
    expect(select.getAttribute("aria-disabled")).toBe("true");
    expect(select.disabled).toBe(false);
    expect(card(container, "a1").getAttribute("aria-busy")).toBe("true");
    expect(text(query(card(container, "a1"), ".bj-app-moving"))).toBe("Moving...");
    choose(select, "offer");
    await settle(1);
    expect(calls().filter((call) => call === "POST /applications/a1/stage")).toHaveLength(1);
    // Another card is not held.
    expect(query(card(container, "a2"), "select[data-role='status']").getAttribute("aria-disabled")).toBeNull();
    await act(async () => release({}));
    await settle();
    expect(card(container, "a1").getAttribute("aria-busy")).toBeNull();
  });

  it("keeps a failed move on its own card, still on its old status, with the reason and a Retry that sends it again", async () => {
    answer({ "POST /applications/a1/stage": new FakeApiError(500, "Server unavailable") });
    const { container } = await draw();
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "offer");
    await settle();
    const problem = query(card(container, "a1"), ".bj-app-problem");
    expect(text(problem)).toBe("✕The move to Offer didn't go through. Server unavailableRetry");
    expect(text(query(card(container, "a1"), ".bj-app-chip"))).toBe("◇Saved");
    // Nothing else on the page was touched: no other card has a problem, no page-level failure, no re-read.
    expect(container.querySelectorAll(".bj-app-problem")).toHaveLength(1);
    expect(sentence(container)).toBe("Your move on 2 · their move on 1.");
    expect(calls().filter((call) => call === "GET /applications")).toHaveLength(1);

    answer({ "POST /applications/a1/stage": {} });
    api.apiFetch.mockClear();
    click(buttonNamed(problem, "Retry"));
    await settle();
    expect(calls()).toEqual(["POST /applications/a1/stage", "GET /applications"]);
    expect(bodyOf("POST /applications/a1/stage").new_status).toBe("offer");
    expect(container.querySelector(".bj-app-problem")).toBeNull();
  });

  it("does not claim where the card is: a failed move says only that the move did not go through", async () => {
    answer({ "POST /applications/a1/stage": new TypeError("Failed to fetch") });
    const { container } = await draw();
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "applied");
    await settle();
    const problem = text(query(card(container, "a1"), ".bj-app-problem"));
    expect(problem).toContain("The move to Applied didn't go through. Failed to fetch");
    expect(problem).not.toContain("still where it was");
  });

  it("keeps a failed move's callout while a read still shows the card where it was, and drops it once a read shows it elsewhere", async () => {
    answer({ "POST /applications/a1/stage": new TypeError("Failed to fetch") });
    const { container } = await draw();
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "applied");
    await settle();

    // Another card's move re-reads the list; a1 is still saved there, so its callout stands.
    answer({ "POST /applications/a1/stage": new TypeError("Failed to fetch"), "POST /applications/a2/stage": {} });
    choose(query<HTMLSelectElement>(card(container, "a2"), "select[data-role='status']"), "offer");
    await settle();
    expect(container.querySelectorAll(".bj-app-problem")).toHaveLength(1);

    // The lost request had gone through after all: the next read shows a1 applied, and the card no
    // longer says its move did not go through while it shows Applied.
    answer({ "/applications": [{ ...SAVED, status: "applied", date_applied: "2026-10-05" }, APPLIED, INTERVIEW], "POST /applications/a2/stage": {} });
    choose(query<HTMLSelectElement>(card(container, "a2"), "select[data-role='status']"), "offer");
    await settle();
    expect(card(container, "a1").querySelector(".bj-app-problem")).toBeNull();
    expect(text(query(card(container, "a1"), ".bj-app-chip"))).toBe("●Applied");
    expect(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']").value).toBe("applied");
  });

  it("sends a Retry under the same idempotency key as the move it repeats, and a new move under a new one", async () => {
    let next = 0;
    const spy = vi
      .spyOn(globalThis.crypto, "randomUUID")
      .mockImplementation(() => `00000000-0000-4000-8000-${String(++next).padStart(12, "0")}` as ReturnType<Crypto["randomUUID"]>);
    try {
      answer({ "POST /applications/a1/stage": new FakeApiError(500, "Server unavailable") });
      const { container } = await draw();
      choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "offer");
      await settle();
      answer({ "POST /applications/a1/stage": new FakeApiError(500, "Server unavailable") });
      click(buttonNamed(query(card(container, "a1"), ".bj-app-problem"), "Retry"));
      await settle();
      answer({ "POST /applications/a1/stage": {} });
      click(buttonNamed(query(card(container, "a1"), ".bj-app-problem"), "Retry"));
      await settle();
      choose(query<HTMLSelectElement>(card(container, "a2"), "select[data-role='status']"), "offer");
      await settle();
      const keys = api.apiFetch.mock.calls
        .filter(([path]) => String(path).endsWith("/stage"))
        .map(([path, init]) => [path, JSON.parse(init.body).idempotency_key]);
      expect(keys).toEqual([
        ["/applications/a1/stage", "00000000-0000-4000-8000-000000000001"],
        ["/applications/a1/stage", "00000000-0000-4000-8000-000000000001"],
        ["/applications/a1/stage", "00000000-0000-4000-8000-000000000001"],
        ["/applications/a2/stage", "00000000-0000-4000-8000-000000000002"],
      ]);
    } finally {
      spy.mockRestore();
    }
  });

  it("keeps keyboard focus in the card when a Retry fails again, on the new Retry", async () => {
    answer({ "POST /applications/a1/stage": new FakeApiError(500, "Server unavailable") });
    const { container } = await draw();
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "offer");
    await settle();
    const retry = buttonNamed(query(card(container, "a1"), ".bj-app-problem"), "Retry");
    retry.focus();
    click(retry);
    // The pressed Retry is replaced while the request is out, and focus goes with it.
    (document.activeElement as HTMLElement | null)?.blur();
    await settle();
    expect(container.querySelectorAll(".bj-app-problem")).toHaveLength(1);
    expect(document.activeElement).toBe(buttonNamed(query(card(container, "a1"), ".bj-app-problem"), "Retry"));
  });

  it("names the company in the announcement and the card's controls, and says a repeated move again", async () => {
    const first = { ...SAVED, id: "b1", snapshot: snapshot("Software Engineer", "Acme") };
    const second = { ...SAVED, id: "b2", snapshot: snapshot("Software Engineer", "Initech") };
    answer({ "/applications": [first, second], "POST /applications/b1/stage": {}, "POST /applications/b2/stage": {} });
    const { container } = await draw();
    const names = (id: string) => [...card(container, id).querySelectorAll("select")].map((select) => select.getAttribute("aria-label"));
    expect(names("b1")).toEqual(['Status of "Software Engineer" at Acme', 'Actions for "Software Engineer" at Acme']);
    expect(names("b2")).toEqual(['Status of "Software Engineer" at Initech', 'Actions for "Software Engineer" at Initech']);

    const region = () => query(container, ".bj-apps > .bj-visually-hidden[role='status']");
    choose(query<HTMLSelectElement>(card(container, "b1"), "select[data-role='status']"), "applied");
    await settle();
    expect(status(container)).toBe('Moved "Software Engineer" at Acme to Applied.');
    const once = region().textContent;
    // The list still reads saved, so the same move is made again and says the same words; the region
    // must still change, or a screen reader stays silent.
    choose(query<HTMLSelectElement>(card(container, "b1"), "select[data-role='status']"), "applied");
    await settle();
    expect(status(container)).toBe('Moved "Software Engineer" at Acme to Applied.');
    expect(region().textContent).not.toBe(once);
  });

  it("says the page's read failed when the re-read after a move fails, keeps the cards it has, and Try again recovers", async () => {
    let fail = false;
    answer({ "/applications": () => (fail ? new FakeApiError(500, "Request failed (500)") : APPLICATIONS), "POST /applications/a1/stage": () => ({}) });
    const { container } = await draw();
    fail = true;
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "applied");
    await settle();
    expect(sentence(container)).toBe("✕Couldn't load your applications. You didn't do anything wrong, and nothing was lost.");
    expect(why(container)).toMatch(/^why: GET \/applications failed · \d\d:\d\d$/);
    expect(titles(container)).toHaveLength(3);
    expect(text(query(container, ".bj-app-section .bj-state--failure"))).toContain("so what is shown may be out of date: Request failed (500)");
    expect(query(container, ".bj-app-section .bj-state--failure").querySelector("button")).toBeNull();
    expect(status(container)).toBe("");

    fail = false;
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(container.querySelector(".bj-app-section .bj-state--failure")).toBeNull();
    expect(sentence(container)).toBe("Your move on 2 · their move on 1.");
  });

  it("returns focus to the card's status control when the card was replaced by the re-read", async () => {
    let applications: unknown[] = APPLICATIONS;
    answer({
      "/applications": () => applications,
      "POST /applications/a2/stage": () => {
        applications = [SAVED, { ...APPLIED, status: "offer" }, INTERVIEW];
        return {};
      },
    });
    const { container } = await draw();
    query<HTMLSelectElement>(card(container, "a2"), "select[data-role='status']").focus();
    choose(query<HTMLSelectElement>(card(container, "a2"), "select[data-role='status']"), "offer");
    // The control that had focus is gone with the old card.
    (document.activeElement as HTMLElement | null)?.blur();
    await settle();
    expect(document.activeElement).toBe(query(card(container, "a2"), "select[data-role='status']"));
  });
});

describe("failure of the list itself", () => {
  it("says so in the band with Try again, names the read in the body without a Retry of its own, and keeps the forms usable", async () => {
    answer({ "/applications": new FakeApiError(500, "Request failed (500)") });
    const { container } = await draw();
    expect(sentence(container)).toBe("✕Couldn't load your applications. You didn't do anything wrong, and nothing was lost.");
    expect(why(container)).toMatch(/^why: GET \/applications failed · \d\d:\d\d$/);
    const callout = query(container, ".bj-state--failure");
    expect(text(callout)).toBe("✕Your applications couldn't be read: Request failed (500)");
    expect(callout.querySelector("button")).toBeNull();
    expect(container.querySelector(".bj-app-card")).toBeNull();
    expect(container.querySelector(".bj-view-toggle")).toBeNull();
    expect(container.querySelectorAll(".bj-app-track-card")).toHaveLength(2);
    expect(cyanFills(container)).toBe(1);

    answer({});
    api.apiFetch.mockClear();
    buttonNamed(band(container), "Try again").focus();
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(calls()).toEqual(["GET /applications"]);
    expect(titles(container)).toHaveLength(3);
    expect(container.querySelector(".bj-state--failure")).toBeNull();
    // The button that was pressed went with the failure: focus is on the band's sentence, not lost.
    expect(document.activeElement).toBe(query(band(container), ".bj-band-sentence"));
  });

  it("gives the body callout its own Retry when a setup row holds the band, since the band then has no Try again", async () => {
    for (const setup of [{ "/profile/current": new FakeApiError(404, "no profile") }, { "/credentials": [] }]) {
      answer({ "/applications": new FakeApiError(500, "down"), ...setup });
      const { container, unmount } = await draw();
      expect(() => buttonNamed(band(container), "Try again")).toThrow();
      const callout = query(container, ".bj-state--failure");
      expect(text(callout)).toBe("✕Your applications couldn't be read: downRetry");
      answer({ ...setup });
      api.apiFetch.mockClear();
      click(buttonNamed(callout, "Retry"));
      await settle();
      expect(calls()).toEqual(["GET /applications"]);
      expect(titles(container)).toHaveLength(3);
      expect(container.querySelector(".bj-state--failure")).toBeNull();
      unmount();
    }
  });

  it("goes back to loading for a retry of the first read, not on to a stale screen", async () => {
    answer({ "/applications": new FakeApiError(500, "down") });
    const { container } = await draw();
    let release: (value: unknown) => void = () => {};
    answer({ "/applications": () => new Promise((resolve) => (release = resolve)) });
    click(buttonNamed(band(container), "Try again"));
    await settle(1);
    expect(text(band(container))).toBe("Next stepLoading...");
    expect(container.querySelector(".bj-state--loading")).not.toBeNull();
    await act(async () => release(APPLICATIONS));
    await settle();
    expect(titles(container)).toHaveLength(3);
  });

  it("has no serious accessibility violation", async () => {
    answer({ "/applications": new FakeApiError(500, "down") });
    const { container } = await draw();
    await expectNoSeriousA11yViolations(container);
  });
});

describe("an empty list", () => {
  it("teaches what lands here and the two ways in, with no List/Board toggle and no example rows", async () => {
    answer({ "/applications": [] });
    const { container } = await draw();
    const empty = query(container, ".bj-state--empty");
    expect(text(query(empty, "h2"))).toBe("Nothing tracked yet");
    expect(text(empty)).toContain("one card with its status");
    expect(text(empty)).toContain("Resume drafted");
    expect(text(empty)).toContain("paste a posting, or fetch one by its URL");
    expect(container.querySelector(".bj-view-toggle")).toBeNull();
    expect(container.querySelector(".bj-app-card")).toBeNull();
    // The band's button leads to the paste form, so the header does not offer a second way to it.
    expect(() => buttonNamed(query(container, ".bj-page-header"), "Track a job")).toThrow();
    // The band holds the one button; the teaching repeats none.
    expect(empty.querySelector("button, a")).toBeNull();
  });

  it("does not teach 'Nothing tracked yet' when a read after saving a posting failed: the list may be out of date", async () => {
    let failing = false;
    answer({
      "/applications": () => (failing ? new FakeApiError(500, "down") : []),
      "POST /applications": () => {
        failing = true;
        return {};
      },
    });
    const { container } = await draw();
    const form = query<HTMLFormElement>(container, "#start-tracking");
    const [title, company] = [...form.querySelectorAll<HTMLInputElement>("input")];
    typeInto(title, "Data Engineer");
    typeInto(company, "Acme");
    typeInto(query<HTMLTextAreaElement>(form, "textarea"), "Build pipelines.");
    click(buttonNamed(form, "Start tracking"));
    await settle();
    expect(container.querySelector(".bj-state--empty")).toBeNull();
    expect(container.textContent).not.toContain("Nothing tracked yet");
    expect(text(query(container, ".bj-state--failure"))).toBe("✕Your applications couldn't be read again, so the list may be out of date: down");
  });

  it("has no serious accessibility violation", async () => {
    answer({ "/applications": [] });
    const { container } = await draw();
    await expectNoSeriousA11yViolations(container);
  });
});

describe("tracking a job", () => {
  it("fetches a posting by URL, reads the list again and clears the box", async () => {
    answer({ "POST /applications/from-url": {} });
    const { container } = await draw();
    api.apiFetch.mockClear();
    const form = query<HTMLFormElement>(container, "form.bj-app-track-card");
    const box = query<HTMLInputElement>(form, "input");
    typeInto(box, "  https://jobs.example/a  ");
    click(buttonNamed(form, "Fetch and start tracking"));
    await settle();
    expect(calls()).toEqual(["POST /applications/from-url", "GET /applications"]);
    expect(bodyOf("POST /applications/from-url")).toEqual({ url: "https://jobs.example/a" });
    expect(box.value).toBe("");
  });

  it("ignores a press while the box is empty or the request is out", async () => {
    let release: (value: unknown) => void = () => {};
    answer({ "POST /applications/from-url": () => new Promise((resolve) => (release = resolve)) });
    const { container } = await draw();
    api.apiFetch.mockClear();
    const form = query<HTMLFormElement>(container, "form.bj-app-track-card");
    const button = buttonNamed(form, "Fetch and start tracking");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    click(button);
    await settle(1);
    expect(calls()).toEqual([]);
    typeInto(query<HTMLInputElement>(form, "input"), "https://jobs.example/a");
    click(button);
    await settle(1);
    expect(button.textContent).toBe("Fetching...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    click(button);
    await settle(1);
    expect(calls()).toEqual(["POST /applications/from-url"]);
    await act(async () => release({}));
  });

  it("names a missing Firecrawl key with a link to where it is fixed, and says the paste form needs no key", async () => {
    answer({
      "POST /applications/from-url": new FakeApiError(409, "Add a Firecrawl key to fetch a job posting from a URL not already in the registry.", {
        code: "SETUP_REQUIRED",
        settingsPath: "/profile/integrations",
        capability: "job_url_ingest",
        missing: ["firecrawl_credential"],
      }),
    });
    const { container } = await draw();
    const form = query<HTMLFormElement>(container, "form.bj-app-track-card");
    typeInto(query<HTMLInputElement>(form, "input"), "https://jobs.example/a");
    click(buttonNamed(form, "Fetch and start tracking"));
    await settle();
    const notice = query(form, ".bj-state--setup");
    expect(text(notice)).toBe(
      "Add a Firecrawl key to fetch a job posting from a URL not already in the registry. You can also paste the posting instead.Missing:Add a Firecrawl key in Settings",
    );
    expect(query(notice, "a").getAttribute("href")).toBe("/settings#search-firecrawl");
    // Announced on arrival, like the failure beside it: the callout sits in an alert.
    expect(notice.closest("[role='alert']")).not.toBeNull();
    expect(form.querySelector(".bj-state--failure")).toBeNull();
    // The list is not re-read, and the forms stay usable.
    expect(calls().filter((call) => call === "GET /applications")).toHaveLength(1);
  });

  it("keeps any other fetch failure on its own form, in the server's words", async () => {
    answer({ "POST /applications/from-url": new FakeApiError(422, "Couldn't find any real content at that URL -- try pasting instead.", { code: "INVALID_INPUT" }) });
    const { container } = await draw();
    const form = query<HTMLFormElement>(container, "form.bj-app-track-card");
    typeInto(query<HTMLInputElement>(form, "input"), "https://jobs.example/a");
    click(buttonNamed(form, "Fetch and start tracking"));
    await settle();
    expect(text(query(form, ".bj-state--failure"))).toContain("Couldn't find any real content at that URL -- try pasting instead.");
    expect(container.querySelectorAll(".bj-state--failure")).toHaveLength(1);
  });

  function fillPaste(container: HTMLElement) {
    const form = query<HTMLFormElement>(container, "#start-tracking");
    const [title, company, location, url] = [...form.querySelectorAll<HTMLInputElement>("input")];
    typeInto(title, "Data Engineer");
    typeInto(company, "Acme");
    typeInto(location, " Remote ");
    typeInto(url, "");
    typeInto(query<HTMLTextAreaElement>(form, "textarea"), "Build pipelines.");
    return form;
  }

  it("saves a pasted posting with the same body as ever, reads the list again and clears the form", async () => {
    answer({ "POST /applications": {} });
    const { container } = await draw();
    api.apiFetch.mockClear();
    const form = fillPaste(container);
    click(buttonNamed(form, "Start tracking"));
    await settle();
    expect(calls()).toEqual(["POST /applications", "GET /applications"]);
    expect(bodyOf("POST /applications")).toEqual({
      title: "Data Engineer",
      company_name: "Acme",
      description_text: "Build pipelines.",
      canonical_url: null,
      location_text: "Remote",
    });
    expect([...form.querySelectorAll<HTMLInputElement>("input")].every((input) => input.value === "")).toBe(true);
  });

  it("needs a title, a company and a description before it sends anything", async () => {
    const { container } = await draw();
    api.apiFetch.mockClear();
    const form = query<HTMLFormElement>(container, "#start-tracking");
    typeInto(query<HTMLInputElement>(form, "input"), "Data Engineer");
    click(buttonNamed(form, "Start tracking"));
    await settle(1);
    expect(calls()).toEqual([]);
    expect(buttonNamed(form, "Start tracking").getAttribute("aria-disabled")).toBe("true");
  });

  it("keeps a failed save on the paste form, with what was typed", async () => {
    answer({ "POST /applications": new FakeApiError(500, "Failed to save the posting") });
    const { container } = await draw();
    const form = fillPaste(container);
    click(buttonNamed(form, "Start tracking"));
    await settle();
    expect(text(query(form, ".bj-state--failure"))).toBe("✕Failed to save the posting");
    expect(query<HTMLInputElement>(form, "input").value).toBe("Data Engineer");
  });
});

describe("List and Board", () => {
  it("opens on the list, with a toggle whose chosen view is announced and drawn as the shared selection", async () => {
    const { container } = await draw();
    const toggle = query(container, ".bj-view-toggle");
    expect(toggle.getAttribute("role")).toBe("group");
    expect(toggle.getAttribute("aria-label")).toBe("View");
    expect(buttonNamed(toggle, "List").getAttribute("aria-pressed")).toBe("true");
    expect(buttonNamed(toggle, "Board").getAttribute("aria-pressed")).toBe("false");
    expect(container.querySelector(".bj-app-list")).not.toBeNull();
    expect(container.querySelector(".bj-app-board")).toBeNull();
  });

  it("switches to a board of seven columns with their counts, and back, without a request", async () => {
    const { container } = await draw();
    api.apiFetch.mockClear();
    click(buttonNamed(container, "Board"));
    expect(buttonNamed(container, "Board").getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector(".bj-app-list")).toBeNull();
    const columns = [...container.querySelectorAll(".bj-app-column")];
    expect(columns.map((column) => text(query(column, ".bj-app-column-head")))).toEqual([
      "Saved1",
      "Applied1",
      "Screening0",
      "Interviewing1",
      "Offer0",
      "Rejected0",
      "Withdrawn0",
    ]);
    expect(text(query(query(container, "[data-status='screening']"), ".bj-app-column-empty"))).toBe("No applications");
    expect(titles(query(container, "[data-status='saved']"))).toEqual(["Data Engineer"]);
    // A column is a section named by its heading, and its cards are list items.
    expect(query(container, "[data-status='saved']").getAttribute("aria-labelledby")).toBe("bj-app-column-saved");
    expect(query(container, "[data-status='saved'] ul > li")).toBe(card(container, "a1"));
    expect(calls()).toEqual([]);
    click(buttonNamed(container, "List"));
    expect(container.querySelector(".bj-app-board")).toBeNull();
    expect(titles(container)).toHaveLength(3);
  });

  it("draws a board card with its Move to... select, its trail and the badge, and no status chip (the column says it)", async () => {
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    const first = card(container, "a1");
    expect(first.querySelector(".bj-app-chip")).toBeNull();
    expect(text(query(first, ".bj-app-badge"))).toBe("✦Resume drafted");
    expect(text(query(first, ".bj-app-card > .bj-trail"))).toBe("● You tracked this · from Discover · Sep 22");
    const move = query<HTMLSelectElement>(first, "select[data-role='status']");
    expect(move.getAttribute("aria-label")).toBe('Move "Data Engineer" at Acme to a different stage');
    expect([...move.options].map((option) => option.textContent)).toEqual(["Move to...", "Applied", "Screening", "Interviewing", "Offer", "Rejected", "Withdrawn"]);
    expect(text(query(first, "a[href='/applications/a1']"))).toBe("Open");
  });

  it("moves a card from the keyboard with its Move to... select, the same request as the list's select", async () => {
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    api.apiFetch.mockClear();
    const move = query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']");
    move.focus();
    expect(document.activeElement).toBe(move);
    choose(move, "screening");
    await settle();
    expect(calls()).toEqual(["POST /applications/a1/stage", "GET /applications"]);
    expect(bodyOf("POST /applications/a1/stage").new_status).toBe("screening");
  });

  it("makes the board region reachable by keyboard so it can be scrolled inside itself", async () => {
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    const region = query(container, ".bj-app-board");
    expect(region.getAttribute("role")).toBe("region");
    expect(region.getAttribute("aria-label")).toBe("Applications board");
    expect(region.getAttribute("tabindex")).toBe("0");
  });

  function drag(source: Element, target: Element) {
    act(() => void source.dispatchEvent(new Event("dragstart", { bubbles: true })));
    act(() => void target.dispatchEvent(new Event("dragenter", { bubbles: true })));
    act(() => void target.dispatchEvent(new Event("dragover", { bubbles: true, cancelable: true })));
    act(() => void target.dispatchEvent(new Event("drop", { bubbles: true, cancelable: true })));
  }

  it("moves a card dropped on another column, and does nothing for a drop on its own", async () => {
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    api.apiFetch.mockClear();
    drag(card(container, "a1"), query(container, "[data-status='offer']"));
    await settle();
    expect(calls()).toEqual(["POST /applications/a1/stage", "GET /applications"]);
    expect(bodyOf("POST /applications/a1/stage").new_status).toBe("offer");

    api.apiFetch.mockClear();
    drag(card(container, "a2"), query(container, "[data-status='applied']"));
    await settle();
    expect(calls()).toEqual([]);
  });

  it("marks the column a card is over, with an outline and no color of its own", async () => {
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    const target = query(container, "[data-status='offer']");
    act(() => void card(container, "a1").dispatchEvent(new Event("dragstart", { bubbles: true })));
    act(() => void target.dispatchEvent(new Event("dragenter", { bubbles: true })));
    expect(target.classList.contains("bj-app-column--dragover")).toBe(true);
    act(() => void target.dispatchEvent(new Event("dragleave", { bubbles: true })));
    expect(target.classList.contains("bj-app-column--dragover")).toBe(false);
    expect(card(container, "a1").classList.contains("bj-app-card--dragging")).toBe(true);
    act(() => void card(container, "a1").dispatchEvent(new Event("dragend", { bubbles: true })));
    expect(card(container, "a1").classList.contains("bj-app-card--dragging")).toBe(false);
  });

  it("does not let a card that is moving be picked up again", async () => {
    answer({ "POST /applications/a1/stage": () => new Promise(() => undefined) });
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    expect(card(container, "a1").getAttribute("draggable")).toBe("true");
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "applied");
    await settle(1);
    expect(card(container, "a1").getAttribute("draggable")).toBe("false");
    expect(text(query(card(container, "a1"), ".bj-app-moving"))).toBe("Moving...");
    api.apiFetch.mockClear();
    drag(card(container, "a1"), query(container, "[data-status='offer']"));
    await settle(1);
    expect(calls()).toEqual([]);
  });

  it("keeps a failed move on its card in the board too, with the same Retry", async () => {
    answer({ "POST /applications/a1/stage": new FakeApiError(500, "Server unavailable") });
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "offer");
    await settle();
    expect(titles(query(container, "[data-status='saved']"))).toEqual(["Data Engineer"]);
    expect(text(query(card(container, "a1"), ".bj-app-problem"))).toContain("The move to Offer didn't go through. Server unavailable");
    expect(() => buttonNamed(card(container, "a1"), "Retry")).not.toThrow();
  });

  it("has no serious accessibility violation as a board", async () => {
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    await expectNoSeriousA11yViolations(container);
  });
});

describe("a phone", () => {
  it("shows one status at a time, with no List/Board toggle and no board", async () => {
    const { container } = await draw(375);
    expect(container.querySelector(".bj-view-toggle")).toBeNull();
    expect(container.querySelector(".bj-app-board")).toBeNull();
    expect(container.querySelector(".bj-app-list")).toBeNull();
    const picker = query<HTMLSelectElement>(container, "select[data-role='picker']");
    expect(text(query(container, ".bj-app-picker label"))).toBe("Showing status");
    expect(query(container, ".bj-app-picker label").getAttribute("for")).toBe(picker.id);
    expect([...picker.options].map((option) => option.textContent)).toEqual([
      "Saved (1)",
      "Applied (1)",
      "Screening (0)",
      "Interviewing (1)",
      "Offer (0)",
      "Rejected (0)",
      "Withdrawn (0)",
    ]);
  });

  it("opens on the first status that has an application, and shows only that status's cards, each with Move to...", async () => {
    answer({ "/applications": [APPLIED, INTERVIEW] });
    const { container } = await draw(375);
    expect(query<HTMLSelectElement>(container, "select[data-role='picker']").value).toBe("applied");
    expect(titles(container)).toEqual(["ML Engineer"]);
    expect(query(container, "select[aria-label^='Move']")).not.toBeNull();
    expect(container.querySelector(".bj-app-chip")).toBeNull();
  });

  it("switches status, says when one is empty, and remembers the choice for next time", async () => {
    const first = await draw(375);
    choose(query<HTMLSelectElement>(first.container, "select[data-role='picker']"), "interviewing");
    expect(titles(first.container)).toEqual(["Analytics Engineer"]);
    expect(memory.get("between-jobs:applications:phone-status")).toBe("interviewing");
    choose(query<HTMLSelectElement>(first.container, "select[data-role='picker']"), "offer");
    expect(text(query(first.container, ".bj-app-column-empty"))).toBe("Nothing is under Offer right now.");
    first.unmount();

    const again = await draw(375);
    expect(query<HTMLSelectElement>(again.container, "select[data-role='picker']").value).toBe("offer");
  });

  it("ignores a remembered value that is not a status", async () => {
    memory.set("between-jobs:applications:phone-status", "ghosted");
    const { container } = await draw(375);
    expect(query<HTMLSelectElement>(container, "select[data-role='picker']").value).toBe("saved");
  });

  it("works when the browser's storage throws", async () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("blocked");
      },
    });
    const { container } = await draw(375);
    choose(query<HTMLSelectElement>(container, "select[data-role='picker']"), "applied");
    expect(titles(container)).toEqual(["ML Engineer"]);
  });

  it("moves a card from the phone's list and follows the picker back when its status's card leaves", async () => {
    let applications: unknown[] = APPLICATIONS;
    answer({
      "/applications": () => applications,
      "POST /applications/a1/stage": () => {
        applications = [{ ...SAVED, status: "applied" }, APPLIED, INTERVIEW];
        return {};
      },
    });
    const { container } = await draw(375);
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "applied");
    await settle();
    expect(calls()).toContain("POST /applications/a1/stage");
    expect(status(container)).toBe('Moved "Data Engineer" at Acme to Applied.');
    expect(query<HTMLSelectElement>(container, "select[data-role='picker']").options[0].textContent).toBe("Saved (0)");
  });

  it("is one column wide: the cards and the forms stack", async () => {
    const { container } = await draw(375);
    expect(container.querySelectorAll("table").length).toBe(0);
    expect(query(container, ".bj-app-track-grid")).not.toBeNull();
  });

  it("has no serious accessibility violation", async () => {
    const { container } = await draw(375);
    await expectNoSeriousA11yViolations(container);
  });
});

// Two of the six states do not occur on this page, and the page must not invent them: one reply
// carries every row, so the list arrives whole or fails whole (no partial), and nothing returned says
// when the list was read, so it never claims an age (no stale).
describe("the states that do not occur", () => {
  it("draws a whole list or a failure, never a partial notice", async () => {
    const { container } = await draw();
    expect(container.querySelector(".bj-state--partial")).toBeNull();
    expect(titles(container)).toHaveLength(3);
  });

  it("never says how old the list is: no 'updated', 'ago' or 'as of' anywhere", async () => {
    const { container } = await draw();
    expect(text(query(container, ".bj-apps"))).not.toMatch(/\bupdated\b|\bago\b|\bas of\b|\bstale\b/i);
    expect(container.querySelector(".bj-state--stale")).toBeNull();
  });
});

describe("one cyan fill", () => {
  it("is never more than the band's own button, in every state the page can be in", async () => {
    const states: Array<[string, Record<string, Reply>, number]> = [
      ["tracked", {}, 0],
      ["empty", { "/applications": [] }, 1],
      ["no profile", { "/profile/current": new FakeApiError(404, "no profile") }, 1],
      ["no key", { "/credentials": [] }, 1],
      ["list failed", { "/applications": new FakeApiError(500, "down") }, 1],
      ["setup failed", { "/profile/current": new FakeApiError(500, "down") }, 1],
    ];
    for (const [name, replies, expected] of states) {
      answer(replies);
      const view = await draw();
      expect(cyanFills(view.container), name).toBe(expected);
      view.unmount();
    }
  });

  it("never draws a control with the primary class outside the band", async () => {
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    expect(container.querySelectorAll(".bj-primary")).toHaveLength(0);
    expect(query(container, ".bj-app-track")).not.toBeNull();
    expect(query(container, ".bj-app-track").querySelector(".bj-primary")).toBeNull();
  });
});

describe("keyboard", () => {
  it("does not fire a status change from a key: only a choice in the select does", async () => {
    const { container } = await draw();
    api.apiFetch.mockClear();
    const select = query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']");
    select.focus();
    keydown("ArrowDown", { target: select });
    keydown("Enter", { target: select });
    await settle(1);
    expect(calls()).toEqual([]);
  });

  // Windows and Linux Chromium and Firefox fire `change` on a closed select for every Arrow press.
  function step(select: HTMLSelectElement, key: string, value: string) {
    select.focus();
    keydown(key, { target: select });
    choose(select, value);
  }

  it("holds a status stepped to with the arrow keys until Enter, and drops it on Escape or leaving", async () => {
    const { container } = await draw();
    api.apiFetch.mockClear();
    const select = query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']");
    step(select, "ArrowDown", "applied");
    step(select, "ArrowDown", "screening");
    await settle(1);
    expect(calls()).toEqual([]);
    expect(select.value).toBe("screening");

    keydown("Escape", { target: select });
    expect(select.value).toBe("saved");
    step(select, "ArrowDown", "applied");
    act(() => select.blur());
    expect(select.value).toBe("saved");
    await settle(1);
    expect(calls()).toEqual([]);

    step(select, "ArrowDown", "applied");
    step(select, "ArrowDown", "screening");
    keydown("Enter", { target: select });
    await settle();
    expect(calls().filter((call) => call.startsWith("POST"))).toEqual(["POST /applications/a1/stage"]);
    expect(bodyOf("POST /applications/a1/stage").new_status).toBe("screening");
  });

  it("holds a Move to... choice made with the arrow keys the same way, on a board card", async () => {
    const { container } = await draw();
    click(buttonNamed(container, "Board"));
    api.apiFetch.mockClear();
    const select = query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']");
    step(select, "ArrowDown", "applied");
    await settle(1);
    expect(calls()).toEqual([]);
    keydown("Enter", { target: select });
    await settle();
    expect(bodyOf("POST /applications/a1/stage").new_status).toBe("applied");
  });

  it("still sends at once for a choice made with the pointer or from the open list", async () => {
    const { container } = await draw();
    api.apiFetch.mockClear();
    choose(query<HTMLSelectElement>(card(container, "a1"), "select[data-role='status']"), "applied");
    await settle();
    expect(calls()[0]).toBe("POST /applications/a1/stage");
  });
});
