// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { CROSS_NAV_ITEMS } from "./lib/applicationsBoard";
import publicCss from "./styles/public.css?raw";
import { withoutMedia } from "./testing/css";
import { response as discoverResponse } from "./testing/discoverFixtures";
import { FakeApiError, on, reset } from "./testing/fakeApi";
import { expectNoSeriousA11yViolations } from "./testing/a11y";
import { settle, text, typeInto } from "./testing/forms";
import { buttonNamed, click, installViewport, query, render } from "./testing/render";

// The signed-in app as one piece: the real App with the sign-in and the API client faked, every page in
// every account state that changes what its next-step band says. Each page and its rows have tests of
// their own; this is the check that they hold together, so that a change to one page or to a shared
// piece cannot quietly break another:
//
//   - every signed-in page has exactly one next-step band, and one main heading
//   - at most one cyan fill on a page (the band's action, or nothing when the step is unlit)
//   - no duplicate ids, and every link to a place on the same page lands on something
//   - the places other pages link to (Settings sections, the profile's import, each workspace section)
//     exist where the link says they do
//
// All names, companies and numbers are made up.

const who = vi.hoisted(() => ({ n: 1, signedOut: false }));
vi.mock("./auth", () => ({
  useAuth: () => ({
    session: who.signedOut ? null : { user: { id: `user-${who.n}`, email: "someone@example.test" } },
    loading: false,
    recovery: false,
    signOut: () => Promise.resolve(),
  }),
}));
vi.mock("./lib/supabase", () => ({ supabase: {} }));

// What a test adds on top of the described routes: a function that answers a request itself (a path with
// a query string, say), or undefined to leave it to the table.
const answers = vi.hoisted(() => ({ extra: undefined as undefined | ((path: string, init?: RequestInit) => unknown) }));
vi.mock("./lib/api", async () => {
  const fake = await import("./testing/fakeApi");
  return {
    apiFetch: async (path: string, init?: RequestInit) => {
      const handled = answers.extra?.(path, init);
      if (handled instanceof Error) throw handled;
      if (handled !== undefined) return handled;
      try {
        return await fake.apiFetch(path, init);
      } catch (e) {
        // A request nobody described is a missing page of the API here, which every panel must survive.
        if (e instanceof Error && e.message.startsWith("fakeApi: nothing described")) throw new fake.FakeApiError("Not described.", "NOT_FOUND", undefined, 404);
        throw e;
      }
    },
    ApiError: fake.FakeApiError,
    apiFetchBlob: async () => new Blob(["pdf"], { type: "application/pdf" }),
    apiFetchBytes: async () => ({}),
    setEnrollmentRefusalListener: () => undefined,
  };
});

// ── the account ────────────────────────────────────────────────────────────

const NOT_FOUND = new FakeApiError("no active profile yet", "NOT_FOUND", undefined, 404);
const VERSION_ID = "3f2a9c1e-aaaa-bbbb-cccc-1234567890ab";
const PROFILE = {
  id: VERSION_ID,
  canonical_json: {
    personal: { name: "Asha Verma", headline: "Data engineer", links: {}, work_authorization: "US citizen." },
    experience: [{ title: "Senior Data Engineer", company: "Acme Analytics", start_date: "2022-06", end_date: "present", is_current: true, bullets: ["Cut refresh time."] }],
    education: [{ degree: "MS", institution: "State U" }],
    skills: { programming: ["Python"] },
    achievements: ["Team award."],
  },
  activated_at: "2026-10-12T10:15:00Z",
  created_at: "2026-10-12T09:58:00Z",
  source_kind: "web_json_paste",
  version_count: 4,
};
const llm = (model: string | null, setup_state: string) => [{ id: "c1", service: "llm", provider: "openrouter", model, is_validated: true, setup_state, updated_at: "2026-10-12T10:00:00Z" }];
const SEARCH_KEY = { id: "c2", service: "search", provider: "firecrawl", model: null, is_validated: true, setup_state: "ready", updated_at: "2026-10-12T10:00:00Z" };

const snapshot = (title: string, company: string) => ({ id: `snap-${title}`, title, company_name: company, location_text: "Remote", source_url: "https://jobs.example/x", description_text: "Build pipelines." });
const APPLICATIONS = [
  { id: "app-1", status: "saved", source_channel: "discover", created_at: "2026-09-22T12:00:00Z", updated_at: "2026-09-22T12:00:00Z", date_applied: null, snapshot: snapshot("Data Engineer", "Acme"), resume_exists: true },
  { id: "app-2", status: "applied", source_channel: "web", created_at: "2026-09-20T12:00:00Z", updated_at: "2026-09-21T12:00:00Z", date_applied: "2026-09-21", snapshot: snapshot("ML Engineer", "Orbit"), resume_exists: false },
  { id: "app-3", status: "interviewing", source_channel: "telegram", created_at: "2026-09-18T12:00:00Z", updated_at: "2026-09-19T12:00:00Z", date_applied: "2026-09-19", snapshot: snapshot("Analytics Engineer", "Initech"), resume_exists: true },
];
const SEARCHES = [{ id: "ss-1", query: "analytics engineer", location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" }];
const TODAY = [
  { id: "p1", kind: "status_proposal", headline: "Possible update: interview requested", detail: "72% confidence", created_at: "2026-09-22T10:00:00Z", application_id: "app-1", job_match: null, status_proposal: { id: "sp1", proposed_type: "interview.requested", confidence: 0.72, evidence_spans: ["Are you free Thursday?"], status: "pending" } },
  { id: "r1", kind: "resume_ready", headline: "Resume ready for ML Engineer @ Orbit", detail: "ATS score 72/100", created_at: "2026-09-22T09:00:00Z", application_id: "app-2", job_match: null, status_proposal: null },
  { id: "m1", kind: "high_fit_job", headline: "High-fit match: Analytics Engineer @ Initech", detail: "Strong on SQL", created_at: "2026-09-22T06:00:00Z", application_id: null, job_match: { today_item_id: "m1", saved_search_id: "ss-1", apply_url: "https://jobs.example/a", title: "Analytics Engineer", company: "Initech", location: "Remote", score100: 74, bin: "Strong", snippet: "s", provider: "registry" }, status_proposal: null },
];

type Account = "no-profile" | "profile-no-key" | "key-no-model" | "empty" | "data";
const ACCOUNTS: Account[] = ["no-profile", "profile-no-key", "key-no-model", "empty", "data"];

function describeAccount(account: Account, options: { hiring?: boolean; searchKey?: boolean } = {}) {
  reset();
  answers.extra = undefined;
  on("GET /capabilities", { telegram: false, gmail: true, gmail_allowed: true });
  on("GET /hiring-signals/status", { enabled: options.hiring ?? true });
  on("GET /profile/current", account === "no-profile" ? NOT_FOUND : PROFILE);
  const credentials = account === "no-profile" || account === "profile-no-key" ? [] : account === "key-no-model" ? llm(null, "needs_model") : llm("vendor/model-a", "ready");
  on("GET /credentials", options.searchKey ? [...credentials, SEARCH_KEY] : credentials);
  const data = account === "data";
  on("GET /applications", data ? APPLICATIONS : []);
  on("GET /saved-searches", data ? SEARCHES : []);
  on("GET /today", data ? TODAY : []);
  on("GET /hiring-signals/saves", { saves: [] });
  on("GET /hiring-signals/searches", { searches: [] });
  on("GET /resume-documents/mine", { id: "doc-1", user_id: "u", application_id: null, profile_version_id: VERSION_ID, job_snapshot_id: null, section_order: ["summary", "experience"], section_visibility: {}, header_layout: { chips: [], separator: "pipe" }, shape_overrides: {}, assertions: [] });
}

// ── looking at a page ──────────────────────────────────────────────────────

async function open(path: string, width: number) {
  installViewport(width);
  const rendered = render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
  await settle(6);
  return rendered.container;
}

const cyanFills = (root: ParentNode) => [...root.querySelectorAll("button.bj-primary, a.bj-button-link-primary")].map((element) => text(element));
const bands = (root: ParentNode) => root.querySelectorAll('section[aria-label="Next step"]');

function duplicateIds(root: ParentNode): string[] {
  const seen = new Map<string, number>();
  for (const element of root.querySelectorAll("[id]")) seen.set(element.id, (seen.get(element.id) ?? 0) + 1);
  return [...seen].filter(([, count]) => count > 1).map(([id]) => id);
}

function brokenLocalLinks(root: ParentNode): string[] {
  return [...root.querySelectorAll("a[href^='#']")]
    .map((link) => link.getAttribute("href") ?? "")
    .filter((href) => href.length > 1 && root.querySelector(`[id="${href.slice(1)}"]`) === null);
}

// What every signed-in page promises, whatever state it is in.
function expectWholePage(container: HTMLElement, what: string) {
  expect(bands(container), `${what}: next-step bands`).toHaveLength(1);
  expect(cyanFills(container).length, `${what}: cyan fills ${JSON.stringify(cyanFills(container))}`).toBeLessThanOrEqual(1);
  expect(container.querySelectorAll("main h1"), `${what}: main headings`).toHaveLength(1);
  expect(duplicateIds(container), `${what}: duplicate ids`).toEqual([]);
  expect(brokenLocalLinks(container), `${what}: links to nowhere on the page`).toEqual([]);
}

// The signed-in pages are fetched the first time they are opened (App.tsx), so the first test to open one would race
// the import of its file. They are all loaded up front: what is under test here is each page, not the loading.
beforeAll(async () => {
  await Promise.all([
    import("./pages/Today"),
    import("./pages/Discover"),
    import("./pages/HiringSignals"),
    import("./pages/Applications"),
    import("./pages/ApplicationWorkspace"),
    import("./pages/Practice"),
    import("./pages/Profile"),
    import("./pages/Settings"),
  ]);
});

beforeEach(() => {
  who.n += 1;
  who.signedOut = false;
  window.scrollTo = (() => undefined) as typeof window.scrollTo;
  Element.prototype.scrollIntoView = () => undefined;
});

// ── every page, every account state, a phone and a wide screen ─────────────

const ROUTES = ["/", "/discover", "/applications", "/practice", "/profile", "/settings", "/hiring-signals"];

describe("each signed-in page, in each account state", () => {
  for (const account of ACCOUNTS) {
    for (const route of ROUTES) {
      for (const width of [375, 1400]) {
        it(`${account}, ${route}, ${width}px`, async () => {
          describeAccount(account, { searchKey: account === "data" });
          expectWholePage(await open(route, width), `${account} ${route} ${width}px`);
        });
      }
    }
  }

  it("says what is missing on a page that needs the profile, with the step that fixes it as the one fill", async () => {
    describeAccount("no-profile");
    const container = await open("/discover", 1000);
    expect(cyanFills(container)).toEqual(["Set up your profile"]);
  });
});

describe("landmarks", () => {
  // axe's `region` rule does not run on a fragment in jsdom, so the shell's own claim is checked
  // directly: every link, button and line of text sits inside a landmark, except the two skip links,
  // which are meant to be the first things on the page, and the closed More sheet (a <dialog>, not drawn).
  const LANDMARK = "main, nav, aside, header, footer, section[aria-label], [role='region'], dialog";

  it("leaves no content of a signed-in page outside a landmark, the rail included", async () => {
    for (const route of [...ROUTES, "/applications/app-1"]) {
      describeAccount("data");
      const container = await open(route, 1280);
      const outside = [...container.querySelectorAll("a, button, p")]
        .filter((element) => !element.classList.contains("bj-skip-link") && element.closest(LANDMARK) === null)
        .map((element) => `${element.tagName.toLowerCase()}.${element.className} ${text(element).slice(0, 30)}`);
      expect(outside, route).toEqual([]);
    }
  });

  it("names the rail", async () => {
    describeAccount("data");
    const container = await open("/", 1280);
    expect(query(container, ".bj-rail").tagName).toBe("ASIDE");
    expect(query(container, ".bj-rail").getAttribute("aria-label")).toBe("Sidebar");
  });
});

describe("an address the shell has no page for", () => {
  it.each(["/no-such-page", "/applications/app-1/extra"])("draws a heading and a way out, with no band, at %s", async (path) => {
    describeAccount("data");
    const container = await open(path, 1000);
    expect([...container.querySelectorAll("main h1")].map((h) => text(h))).toEqual(["Page not found"]);
    // the tab says what the page is, like every other signed-in page
    expect(document.title).toBe("Page not found -- Between Jobs");
    expect(bands(container)).toHaveLength(0);
    expect(cyanFills(container)).toEqual([]);
    expect(text(query(container, "main a"))).toBe("Go to Today");
    expect(query(container, "main a").getAttribute("href")).toBe("/");
    expect([...container.querySelectorAll(".bj-skip-link")].map((link) => text(link))).toEqual(["Skip to content"]);
    expect(duplicateIds(container)).toEqual([]);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("the tester-programme gate standing in for a page", () => {
  const NOT_JOINED = { enrolled: false, role_cohort: null, seniority: null, needs_sponsorship: null, consent_version: null, consented_at: null, withdrawn_at: null, current_version: "2026-10-01", needs_reconsent: false };
  const skipLinks = (root: ParentNode) => [...root.querySelectorAll(".bj-skip-link")].map((link) => text(link));

  // The shell offers "Skip to next step" at every address that has a band. The page the gate draws in
  // its place has none, so the link would point at nothing: a keyboard user would press it and nothing
  // would move.
  it("offers only 'Skip to content' while the enrollment page replaces Today", async () => {
    describeAccount("data");
    on("GET /capabilities", { telegram: false, tester_program_required: true });
    on("GET /tester/enrollment", NOT_JOINED);
    const container = await open("/", 1000);
    expect(text(query(container, "main h1"))).toBe("Tester programme");
    expect(skipLinks(container)).toEqual(["Skip to content"]);
    expect(brokenLocalLinks(container)).toEqual([]);
  });

  it("offers it again on the page once the person has joined", async () => {
    describeAccount("data");
    on("GET /capabilities", { telegram: false, tester_program_required: true });
    on("GET /tester/enrollment", { ...NOT_JOINED, enrolled: true });
    const container = await open("/", 1000);
    expect(skipLinks(container)).toEqual(["Skip to next step", "Skip to content"]);
    expect(brokenLocalLinks(container)).toEqual([]);
  });

  it("has a main heading and no skip link to a band when the lookup of the person's enrollment failed", async () => {
    describeAccount("data");
    on("GET /capabilities", { telegram: false, tester_program_required: true });
    on("GET /tester/enrollment", new FakeApiError("down", "INTERNAL", undefined, 500));
    const container = await open("/", 1000);
    expect(container.querySelectorAll("main h1")).toHaveLength(1);
    expect(text(query(container, "main h1"))).toBe("Tester programme");
    expect(skipLinks(container)).toEqual(["Skip to content"]);
  });
});

describe("the document title of each signed-in page", () => {
  it("is the page's own, and no two pages share one", async () => {
    const titles = new Map<string, string>();
    for (const route of [...ROUTES, "/applications/app-1"]) {
      describeAccount("data");
      await open(route, 1000);
      titles.set(route, document.title);
    }
    expect([...titles.values()].sort()).toEqual(
      [
        "Today -- Between Jobs",
        "Discover -- Between Jobs",
        "Applications -- Between Jobs",
        "Application -- Between Jobs",
        "Practice -- Between Jobs",
        "Profile -- Between Jobs",
        "Settings -- Between Jobs",
        "Hiring signals -- Between Jobs",
      ].sort(),
    );
    expect(new Set(titles.values()).size).toBe(titles.size);
  });
});

describe("each signed-in page while its setup reads are still out", () => {
  // The skip link to the next step is on these pages from the first render, so the loading band has to
  // hold what it points at.
  for (const route of [...ROUTES, "/applications/app-1"]) {
    for (const width of [375, 1400]) {
      it(`${route}, ${width}px`, async () => {
        describeAccount("data");
        answers.extra = (path) => (path === "/profile/current" || path === "/credentials" ? new Promise(() => undefined) : undefined);
        const container = await open(route, width);
        expectWholePage(container, `loading ${route} ${width}px`);
        expect(container.querySelectorAll("#next-step-focus"), `loading ${route}: the skip link's target`).toHaveLength(1);
      });
    }
  }
});

describe("Hiring signals", () => {
  it("is an unlit note when the server has the feature off", async () => {
    describeAccount("empty", { hiring: false });
    const container = await open("/hiring-signals", 1000);
    expectWholePage(container, "hiring signals off");
    expect(cyanFills(container)).toEqual([]);
  });

  it("keeps to one fill with a search key, after a search, and after a search that failed", async () => {
    describeAccount("data", { searchKey: true });
    const signal = {
      activity_id: "7000000000000000001",
      post_url: "https://www.linkedin.com/posts/example-7000000000000000001",
      embed_url: "https://www.linkedin.com/embed/feed/update/urn:li:activity:7000000000000000001",
      author_name: "Jane Example",
      posted_at: "2026-09-18T12:00:00Z",
      age_hint: null,
      species: "unclassified",
      comment_count: null,
      registry_match: null,
      aggregator: false,
      saved: false,
    };
    const result = { provider: "firecrawl", cached: false, freshness: "3days", locale: "global", query_label: "software engineer -- last 3 days", signals: [signal], counts: { raw_hits: 5, rejected: 0, duplicates: 1, role_mismatch_hidden: 2, echoes_hidden: 0, job_seekers_hidden: 0, too_old_hidden: 0, shown: 1 } };
    const container = await open("/hiring-signals", 1000);
    expectWholePage(container, "hiring signals with a key");
    expect(cyanFills(container)).toEqual(["Search"]);

    answers.extra = (path, init) => (path === "/hiring-signals/search" && init?.method === "POST" ? result : undefined);
    typeInto(query<HTMLInputElement>(container, "#hst-query"), "software engineer");
    await act(async () => query<HTMLFormElement>(container, "form").requestSubmit());
    await settle(6);
    expectWholePage(container, "hiring signals with results");

    answers.extra = (path, init) => (path === "/hiring-signals/search" && init?.method === "POST" ? new FakeApiError("down", "INTERNAL", undefined, 500) : undefined);
    await act(async () => query<HTMLFormElement>(container, "form").requestSubmit());
    await settle(6);
    expectWholePage(container, "hiring signals after a failed search");
  });
});

describe("Discover, after the search", () => {
  const afterSearch = async (answer: unknown) => {
    describeAccount("data");
    answers.extra = (path) => (path.startsWith("/discover?") ? answer : undefined);
    const container = await open("/discover", 1000);
    click(query(container, "button.bj-primary"));
    await settle(6);
    return container;
  };

  it("with results the step is unlit and each row's Track is outlined", async () => {
    const container = await afterSearch(discoverResponse());
    expectWholePage(container, "discover results");
    expect(cyanFills(container)).toEqual([]);
  });

  it("with placeholder scores the one fill is Search again", async () => {
    const container = await afterSearch(discoverResponse({ scoring_strategy: "neutral_fallback" }));
    expectWholePage(container, "discover placeholder scores");
    expect(cyanFills(container)).toEqual(["Search again"]);
  });

  it("with nothing found, with a failure, and with setup missing", async () => {
    expectWholePage(await afterSearch(discoverResponse({ scored: [], more: [] })), "discover empty");
    expectWholePage(await afterSearch(new FakeApiError("down", "INTERNAL", undefined, 500)), "discover failed");
    expectWholePage(await afterSearch(new FakeApiError("Missing key", "SETUP_REQUIRED", undefined, 409)), "discover setup required");
  });

  it("while the search is out the step says so and holds no fill", async () => {
    describeAccount("data");
    answers.extra = (path) => (path.startsWith("/discover?") ? new Promise(() => undefined) : undefined);
    const container = await open("/discover", 1000);
    click(query(container, "button.bj-primary"));
    await settle(4);
    expectWholePage(container, "discover searching");
    expect(cyanFills(container)).toEqual([]);
  });
});

describe("Applications, at the widths that change its layout", () => {
  for (const width of [375, 800, 1400]) {
    it(`with tracked jobs, ${width}px`, async () => {
      describeAccount("data");
      expectWholePage(await open("/applications", width), `applications ${width}px`);
    });
  }
});

describe("Profile, with an import waiting", () => {
  for (const account of ["no-profile", "empty"] as Account[]) {
    it(`for ${account === "no-profile" ? "a person with no profile" : "a person with a profile"}, the band's button is the fill and the review is on the page`, async () => {
      describeAccount(account);
      on("POST /profile/versions", { id: "pending-1", canonical_json: { personal: { name: "Asha Verma" } }, activated_at: null, created_at: "2026-10-19T11:55:00Z", source_kind: "web_json_paste", stats: { experience: 2 }, warnings: [], version_count: 5 });
      const container = await open("/profile", 1000);
      typeInto(query<HTMLTextAreaElement>(query(container, "#json-import"), "textarea"), '{"personal":{"name":"Asha Verma"}}');
      click(buttonNamed(query(container, "#json-import"), "Import"));
      await settle(6);
      expectWholePage(container, `profile import waiting (${account})`);
      expect(cyanFills(container)).toEqual(["Confirm and make active"]);
      expect(container.querySelector("#import-review")).not.toBeNull();
    });
  }

  it("has the import section a person with no profile is sent to, and not the review", async () => {
    describeAccount("no-profile");
    const container = await open("/profile", 1000);
    expect(container.querySelector("#set-up")).not.toBeNull();
    expect(container.querySelector("#import-review")).toBeNull();
  });
});

// ── the workspace ──────────────────────────────────────────────────────────

const ID = "app-1";
const BREAKDOWN = { semantic_coverage: 27, experience_quality: 22, hard_req_score: 8, quantification: 6, company_alignment: 4, structure: 5 };
const ATTEMPT = { overall_score: 72, breakdown: BREAKDOWN, confidence: "medium", rating: "Moderate", gaps: [] as string[] };
const CLAIM = 'unsupported claim (unverifiable): "Cut query costs by 40%" -- Nothing picked mentions query costs.';
const drafted = (warnings: string[] = []) => ({
  run_id: "0123456789abcdef",
  resume: { artifact_id: "a", version_id: "v", media_type: "application/pdf", sha256: "f00dfeedbabe0123456789" },
  ats_attempts: [ATTEMPT],
  final_score: 72,
  score_scale: "ats_100",
  gate_outcome: "proceed",
  gate_cautions: [],
  warnings,
});
const FIT = { overall_score: 3.6, dimensions: { skills_match: 4 }, gaps: ["No Kafka in your profile"], strengths: [], recommendation: "Skip", keyword_gaps: ["Kafka"], keyword_hits: ["dbt"], visa_flag: null };
const HELD = { run_id: "fedcba", resume: null, ats_attempts: [], final_score: null, score_scale: "ats_100", fit: FIT, gate_outcome: "SKIP_LOW_SCORE", warnings: [] };
const CHECKLIST = { items: [{ key: "one_page", label: "Fits on one page", status: "pass", detail: "1 page." }, { key: "links_valid", label: "Links work", status: "not_checked", detail: "Open each link yourself." }] };

const workspaceApp = (overrides: Record<string, unknown> = {}) => ({ id: ID, status: "saved", source_channel: "discover", created_at: "2026-09-22T14:00:00Z", date_applied: null, resume_exists: false, snapshot: snapshot("Data Engineer", "Northwind Labs"), ...overrides });

function describeWorkspace(account: Account, app: Record<string, unknown>, result: unknown) {
  describeAccount(account);
  on(`GET /applications/${ID}`, workspaceApp(app));
  on(`GET /applications/${ID}/prepare-result`, { result });
  on(`GET /applications/${ID}/export-checklist`, CHECKLIST);
}

describe("the application workspace", () => {
  const STATES: [string, Account, Record<string, unknown>, unknown][] = [
    ["no profile", "no-profile", {}, null],
    ["no model key", "profile-no-key", {}, null],
    ["a key with no model", "key-no-model", {}, null],
    ["no resume yet", "empty", {}, null],
    ["a resume with no claim flagged", "empty", { resume_exists: true }, drafted()],
    ["a resume with a flagged claim", "empty", { resume_exists: true }, drafted([CLAIM])],
    ["a draft held back by the fit check", "empty", {}, HELD],
    ["applied", "empty", { resume_exists: true, status: "applied", date_applied: "2026-09-25" }, drafted()],
    ["interviewing", "empty", { resume_exists: true, status: "interviewing", date_applied: "2026-09-25" }, drafted()],
    ["rejected", "empty", { resume_exists: true, status: "rejected" }, drafted()],
  ];
  for (const [label, account, app, result] of STATES) {
    for (const width of [375, 1400]) {
      it(`${label}, ${width}px`, async () => {
        describeWorkspace(account, app, result);
        expectWholePage(await open(`/applications/${ID}`, width), `workspace ${label} ${width}px`);
      });
    }
  }

  it("has no serious accessibility violation with the Studio controls, the review and the research sections all on the page", async () => {
    describeWorkspace("empty", { resume_exists: true }, drafted([CLAIM]));
    await expectNoSeriousA11yViolations(await open(`/applications/${ID}`, 1400));
  });

  it("while a resume is being generated, and when generating failed", async () => {
    describeWorkspace("empty", {}, null);
    on(`POST /applications/${ID}/prepare`, () => new Promise(() => undefined));
    let container = await open(`/applications/${ID}`, 1000);
    click(query(container, "button.bj-primary"));
    await settle(4);
    expectWholePage(container, "workspace generating");

    describeWorkspace("empty", {}, null);
    on(`POST /applications/${ID}/prepare`, new FakeApiError("The engine failed.", "INTERNAL", undefined, 500));
    container = await open(`/applications/${ID}`, 1000);
    click(query(container, "button.bj-primary"));
    await settle(6);
    expectWholePage(container, "workspace failed");
  });

  // The loading frame and the page are different component trees, so the breadcrumb's link is a new element
  // when the page arrives. A keyboard that had reached the old one must not drop to the top of the document.
  describe("when the page replaces its loading frame", () => {
    const loadingWorkspace = async () => {
      describeWorkspace("empty", {}, null);
      let arrive: (value: unknown) => void = () => undefined;
      answers.extra = (path) => (path === `/applications/${ID}` ? new Promise((resolve) => (arrive = resolve)) : undefined);
      const container = await open(`/applications/${ID}`, 1000);
      return { container, arrive: async () => { await act(async () => arrive(workspaceApp())); await settle(6); } };
    };

    it("keeps keyboard focus on the breadcrumb link it was on", async () => {
      const { container, arrive } = await loadingWorkspace();
      const loadingLink = query<HTMLAnchorElement>(container, ".bj-breadcrumb a");
      loadingLink.focus();
      expect(document.activeElement).toBe(loadingLink);
      await arrive();
      const pageLink = query<HTMLAnchorElement>(container, ".bj-breadcrumb a");
      expect(pageLink).not.toBe(loadingLink);
      expect(document.activeElement).toBe(pageLink);
    });

    it("leaves focus alone when it was somewhere else, or when the person had moved on", async () => {
      const { container, arrive } = await loadingWorkspace();
      query<HTMLAnchorElement>(container, ".bj-breadcrumb a").focus();
      const skip = query<HTMLAnchorElement>(container, ".bj-skip-link");
      skip.focus();
      await arrive();
      expect(document.activeElement).toBe(skip);

      const second = await loadingWorkspace();
      second.container.querySelector<HTMLAnchorElement>(".bj-breadcrumb a")?.focus();
      (document.activeElement as HTMLElement).blur();
      await second.arrive();
      expect(document.activeElement).toBe(document.body);
    });
  });

  it("when the application is not found and when it could not be read", async () => {
    describeAccount("empty");
    on(`GET /applications/${ID}`, new FakeApiError("not found", "NOT_FOUND", undefined, 404));
    expectWholePage(await open(`/applications/${ID}`, 1000), "workspace not found");
    on(`GET /applications/${ID}`, new FakeApiError("boom", "INTERNAL", undefined, 500));
    expectWholePage(await open(`/applications/${ID}`, 1000), "workspace read failed");
  });
});

// ── the places other pages link to ─────────────────────────────────────────

describe("the places other pages link to", () => {
  it("has every workspace section the Applications menu and the band can name", async () => {
    describeWorkspace("empty", { resume_exists: true }, drafted());
    const container = await open(`/applications/${ID}`, 1000);
    for (const item of CROSS_NAV_ITEMS) {
      expect(container.querySelector(`[id="${item.hash}"]`), `#${item.hash} (${item.label})`).not.toBeNull();
    }
    for (const id of ["ch-sources", "ch-draft", "ch-check", "interview-practice", "your-play"]) {
      expect(container.querySelector(`[id="${id}"]`), `#${id}`).not.toBeNull();
    }
  });

  it("has the reading of a held draft where the band's link to it lands", async () => {
    describeWorkspace("empty", {}, HELD);
    const container = await open(`/applications/${ID}`, 1000);
    expect(container.querySelector("[id='honest-read-heading']")).not.toBeNull();
  });

  it("has the Settings sections the other pages link to", async () => {
    describeAccount("empty");
    const container = await open("/settings", 1000);
    for (const id of ["ai-provider", "search-keys"]) expect(container.querySelector(`[id="${id}"]`), `#${id}`).not.toBeNull();
  });

  it("links only to places the shell has a page for", async () => {
    describeAccount("data");
    const container = await open("/", 375);
    const hrefs = [...container.querySelectorAll(".bj-rail-nav a, .bj-nav-footer a, .bj-tabbar a, .bj-more-sheet a")].map((a) => a.getAttribute("href"));
    expect(new Set(hrefs)).toEqual(new Set(["/", "/discover", "/hiring-signals", "/applications", "/practice", "/profile", "/settings", "/privacy", "/terms"]));
  });
});

// ── the public pages, signed out ───────────────────────────────────────────

// The signed-out site is the same App with no session. Its pages hold to the same promises as the signed-in
// ones, with the one difference the design makes on purpose: the home page has a band (its hero's, which holds
// the page's one cyan fill) and every other public page has none and lets the header's Create account be the
// one fill. A new public page goes in this list, and it fails here until it has one primary action at most.

const PUBLIC_ROUTES: readonly [route: string, title: string, band: boolean][] = [
  ["/", "Between Jobs", true],
  ["/how-it-works", "How it works -- Between Jobs", false],
  ["/open-source", "Open source -- Between Jobs", false],
  ["/privacy", "Privacy Policy -- Between Jobs", false],
  ["/terms", "Terms of Service -- Between Jobs", false],
];

// A link to a place on the page it is on, written with the path (the router draws "/#tour" for a hash link).
function brokenSamePageLinks(root: ParentNode, path: string): string[] {
  return [...root.querySelectorAll("a[href*='#']")]
    .map((link) => link.getAttribute("href") ?? "")
    .filter((href) => href.startsWith(`${path}#`) || href.startsWith("#"))
    .filter((href) => href.slice(href.indexOf("#") + 1).length > 0 && root.querySelector(`[id="${href.slice(href.indexOf("#") + 1)}"]`) === null);
}

describe("each public page, signed out", () => {
  beforeEach(() => {
    who.signedOut = true;
  });

  for (const [route, , band] of PUBLIC_ROUTES) {
    for (const width of [375, 768, 1400]) {
      it(`${route}, ${width}px: one main heading, exactly one primary action, ${band ? "one band" : "no band"}, no repeated ids, no links to nowhere`, async () => {
        const container = await open(route, width);
        expect(container.querySelectorAll("main h1"), "main headings").toHaveLength(1);
        expect(container.querySelectorAll("h1"), "all headings of level one").toHaveLength(1);
        // The one cyan fill: the hero band's button on the home page, the header's Create account elsewhere.
        expect(cyanFills(container), `cyan fills ${JSON.stringify(cyanFills(container))}`).toEqual([band ? "Create an account" : "Create account"]);
        expect(bands(container)).toHaveLength(band ? 1 : 0);
        expect(duplicateIds(container), "duplicate ids").toEqual([]);
        expect(brokenLocalLinks(container), "links to nowhere on the page").toEqual([]);
        expect(brokenSamePageLinks(container, route), "same-page links to nowhere").toEqual([]);
        expect([...container.querySelectorAll(".bj-skip-link")].map((link) => text(link))).toEqual(band ? ["Skip to next step", "Skip to content"] : ["Skip to content"]);
      });
    }
  }

  it("titles each public page by its own name, and no two share one", async () => {
    const titles: string[] = [];
    for (const [route, title] of PUBLIC_ROUTES) {
      await open(route, 1000);
      expect(document.title, route).toBe(title);
      titles.push(document.title);
    }
    expect(new Set(titles).size).toBe(titles.length);
  });

  it("has no serious or critical axe violation on any of them, with the menu closed and open", async () => {
    for (const [route] of PUBLIC_ROUTES) {
      const container = await open(route, 375);
      await expectNoSeriousA11yViolations(container);
      click(query(container, "button.bj-public-menu-button"));
      await expectNoSeriousA11yViolations(container);
    }
  });

  it("shows the way in on a phone, where the tabs fold: Create account is drawn beside the Menu button on every page, and Sign in waits in the menu", async () => {
    // The package has no layout engine, but the header's base rules (the ones a phone gets, before any
    // min-width query) are plain `display` declarations, which jsdom's cascade does read.
    const sheet = document.createElement("style");
    sheet.textContent = withoutMedia(publicCss);
    document.head.append(sheet);
    try {
      for (const [route, , band] of PUBLIC_ROUTES) {
        const container = await open(route, 375);
        const header = query(container, "header.bj-public-header");
        const drawn = (element: Element) => {
          for (let node: Element | null = element; node !== null; node = node.parentElement) {
            if (getComputedStyle(node).display === "none") return false;
          }
          return true;
        };
        const links = [...header.querySelectorAll("a")].filter(drawn).map((link) => text(link));
        expect(links, route).toEqual(["Between Jobs", "Create account"]);
        expect(drawn(query(header, "button.bj-public-menu-button")), route).toBe(true);
        // On a page with no hero of its own this is the page's one cyan fill, so it must be seen.
        if (!band) expect([...header.querySelectorAll("a.bj-button-link-primary")].filter(drawn), route).toHaveLength(1);
        click(query(container, "button.bj-public-menu-button"));
        expect([...query(container, "nav.bj-public-menu").querySelectorAll("a")].filter(drawn).map((link) => text(link)), route).toEqual([
          "Product",
          "How it works",
          "Open source",
          "Privacy",
          "Sign in",
        ]);
      }
    } finally {
      sheet.remove();
    }
  });

  it("draws the home page's header Create account outlined and every other page's filled", async () => {
    for (const [route, , band] of PUBLIC_ROUTES) {
      const container = await open(route, 1400);
      const header = query(container, "header.bj-public-header");
      expect(header.querySelectorAll("a.bj-button-link-primary").length, route).toBe(band ? 0 : 1);
    }
  });
});

describe("the explainer pages for a signed-in person who follows a shared link", () => {
  it.each([
    ["/how-it-works", "How it works", "How it works -- Between Jobs"],
    ["/open-source", "Open source", "Open source -- Between Jobs"],
  ])("draw %s inside the shell, not as a page that is not found, with no account link to offer", async (route, _name, title) => {
    describeAccount("data");
    const container = await open(route, 1280);
    expect(container.querySelectorAll("main h1")).toHaveLength(1);
    expect(text(query(container, "main h1"))).not.toBe("Page not found");
    expect(document.title).toBe(title);
    expect(query(container, ".bj-rail")).not.toBeNull();
    expect([...container.querySelectorAll("main a")].some((link) => /Create account|Sign in/.test(text(link)))).toBe(false);
    expect(cyanFills(container)).toEqual([]);
    expect(duplicateIds(container)).toEqual([]);
  });
});
