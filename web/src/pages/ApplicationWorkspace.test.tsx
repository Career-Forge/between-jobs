// @vitest-environment jsdom
import { act, useEffect } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CROSS_NAV_HASH } from "../lib/applicationsBoard";
import { SECTION_IDS } from "../lib/workspaceSections";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, calls, callsTo, on, reset } from "../testing/fakeApi";
import { choose, settle, text } from "../testing/forms";
import { buttonNamed, click, installViewport, keydown, query, render } from "../testing/render";
import ApplicationWorkspace from "./ApplicationWorkspace";

// The workspace page, whole: the real page with the API client faked and the panels it does not own
// (Studio, Tailor, research, prepare) replaced by stand-ins that say which application they were given.
// All names, text and numbers are synthetic.

vi.mock("../lib/api", async () => {
  const fake = await import("../testing/fakeApi");
  return {
    ...fake.apiModule,
    apiFetchBlob: async (path: string) => {
      fake.calls.push({ method: "GET", path, body: undefined });
      return new Blob(["pdf"], { type: "application/pdf" });
    },
  };
});

const hiring = vi.hoisted(() => ({ kind: "enabled" as "enabled" | "disabled" | "checking" | "unavailable" }));
vi.mock("../lib/useHiringSignalsStatus", () => ({
  useHiringSignalsStatus: () => ({ kind: hiring.kind }),
  useHiringSignalsEnabled: () => hiring.kind === "enabled",
}));
const gmail = vi.hoisted(() => ({ state: { kind: "checking" } as unknown }));
vi.mock("../lib/useCapabilities", () => ({ useCapabilities: () => gmail.state, refreshCapabilities: () => undefined }));

// How many times each stand-in has mounted: a panel that is rebuilt has asked its own requests again.
const mounts = vi.hoisted(() => ({}) as Record<string, number>);

function standIn(name: string, heading?: string) {
  return ({ applicationId }: { applicationId: string }) => {
    useEffect(() => {
      mounts[name] = (mounts[name] ?? 0) + 1;
    }, []);
    return (
      <div className="bj-card" data-standin={name}>
        {heading !== undefined && <h2>{heading}</h2>}
        <p>{name} for {applicationId}</p>
      </div>
    );
  };
}
vi.mock("../components/HeaderComposer", () => ({ HeaderComposer: standIn("Header composer") }));
vi.mock("../components/SectionOrderEditor", () => ({ SectionOrderEditor: standIn("Section order editor") }));
vi.mock("../components/ShapeSettingsPanel", () => ({ ShapeSettingsPanel: standIn("Shape settings") }));
vi.mock("../components/TailorPanel", () => ({ TailorPanel: standIn("Tailor panel") }));
vi.mock("../components/CompanyIntelPanel", () => ({ CompanyIntelPanel: standIn("Company intel panel", "Company intel") }));
vi.mock("../components/ContactFinderPanel", () => ({ ContactFinderPanel: standIn("Contacts panel", "Contacts") }));
vi.mock("../components/WarmPathEventsPanel", () => ({ WarmPathEventsPanel: standIn("Events panel", "Warm-path events") }));
vi.mock("../components/HiringSignalsPanel", () => ({ HiringSignalsPanel: standIn("Hiring posts panel", "Hiring posts") }));
vi.mock("../components/InterviewPracticePanel", () => ({ InterviewPracticePanel: standIn("Practice panel", "Interview practice") }));
vi.mock("../components/PositioningBriefPanel", () => ({ PositioningBriefPanel: standIn("Your play panel", "Your play") }));

const ID = "app-1";
const APP_PATH = `GET /applications/${ID}`;

function application(overrides: Record<string, unknown> = {}) {
  return {
    id: ID,
    status: "saved",
    source_channel: "discover",
    created_at: "2026-09-22T11:00:00Z",
    date_applied: null,
    resume_exists: false,
    snapshot: {
      id: "s1",
      title: "Data Engineer",
      company_name: "Northwind Labs",
      location_text: "Remote (US)",
      description_text: "Build and run batch pipelines.",
      source_url: "https://jobs.example.com/roles/7",
    },
    ...overrides,
  };
}

const KEY = [{ service: "llm", provider: "openrouter", model: "vendor/model-a", is_validated: true, setup_state: "ready" }];
const PROFILE = { id: "pv1", canonical_json: { personal: { name: "Reader Example" } } };

const BREAKDOWN = { semantic_coverage: 27, experience_quality: 22, hard_req_score: 8, quantification: 6, company_alignment: 4, structure: 5 };
const attempt = (score: number, gaps: string[] = []) => ({ overall_score: score, breakdown: BREAKDOWN, confidence: "medium", rating: "Moderate", gaps });
const CLAIM = 'unsupported claim (unverifiable): "Cut query costs by 40%" -- Nothing picked mentions query costs.';

function drafted(warnings: string[] = [], attempts = [attempt(72)]) {
  return {
    run_id: "0123456789abcdef",
    resume: { artifact_id: "a", version_id: "v", media_type: "application/pdf", sha256: "f00dfeedbabe0123456789" },
    ats_attempts: attempts,
    final_score: 72,
    score_scale: "ats_100",
    gate_outcome: "proceed",
    gate_cautions: [],
    warnings,
  };
}

const FIT = { overall_score: 3.6, dimensions: { skills_match: 4 }, gaps: ["No Kafka in your profile"], strengths: [], recommendation: "Skip", keyword_gaps: ["Kafka"], keyword_hits: ["dbt"], visa_flag: null };
const HELD = { run_id: "fedcba", resume: null, ats_attempts: [], final_score: null, score_scale: "ats_100", fit: FIT, gate_outcome: "SKIP_LOW_SCORE", warnings: [] };

const CHECKLIST = {
  items: [
    { key: "one_page", label: "Fits on one page", status: "pass", detail: "1 page." },
    { key: "no_engine_warnings", label: "No engine warnings", status: "pass", detail: "None." },
    { key: "no_unsupported_claims", label: "No unsupported claims", status: "fail", detail: CLAIM },
    { key: "page_fill", label: "Page is well-filled", status: "pass", detail: "88%." },
    { key: "page_count_within_shape", label: "Page count matches the shape", status: "pass", detail: "1 page." },
    { key: "pins_honored", label: "Pinned entries honored", status: "pass", detail: "Yes." },
    { key: "links_valid", label: "Links work", status: "not_checked", detail: "Open each link yourself." },
    { key: "text_selectable", label: "Text is selectable", status: "not_checked", detail: "Select a line." },
    { key: "fonts_embedded", label: "Fonts are embedded", status: "not_checked", detail: "Check properties." },
    { key: "no_duplicate_bullets", label: "No repeated bullets", status: "not_checked", detail: "Read for repeats." },
  ],
};

interface Account {
  app?: unknown;
  profile?: unknown;
  credentials?: unknown;
  result?: unknown;
}

function describeAccount({ app = application(), profile = PROFILE, credentials = KEY, result = null }: Account = {}) {
  on(APP_PATH, app);
  on("GET /profile/current", profile);
  on("GET /credentials", credentials);
  on(`GET /applications/${ID}/prepare-result`, { result });
}

let scrolled: string[] = [];

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.hash}</output>;
}

function page(entry = `/applications/${ID}`) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/applications/:id" element={<ApplicationWorkspace />} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );
}

const band = (root: ParentNode) => query(root, 'section[aria-label="Next step"]');
const names = (root: ParentNode, selector: string) => [...root.querySelectorAll(selector)].map((node) => text(node));
const cyan = (root: ParentNode) => [...root.querySelectorAll(".bj-primary, .bj-button-link-primary")];

beforeEach(() => {
  reset();
  for (const key of Object.keys(mounts)) delete mounts[key];
  hiring.kind = "enabled";
  gmail.state = { kind: "checking" };
  scrolled = [];
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolled.push(this.id);
  };
  installViewport(1280);
  // A download is a blob saved through a link: jsdom has neither a blob URL nor a navigation.
  URL.createObjectURL = () => "blob:test";
  URL.revokeObjectURL = () => undefined;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  vi.spyOn(window.crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-000000000000");
});

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "matchMedia");
});

describe("the frames", () => {
  it("shows the breadcrumb, a placeholder and only the band's eyebrow while the application is being read", async () => {
    on(APP_PATH, () => new Promise(() => undefined));
    on("GET /profile/current", PROFILE);
    on("GET /credentials", KEY);
    const { container } = page();
    await settle();
    expect(text(query(container, ".bj-breadcrumb"))).toContain("Loading...");
    expect(text(band(container))).toBe("Next stepLoading...");
    expect(container.querySelector(".bj-primary")).toBeNull();
    expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull();
    expect(names(container, "h1")).toEqual(["Application"]);
  });

  it("says an application that is not there is not there, links back, and offers no Try again", async () => {
    describeAccount({ app: new FakeApiError(`no application found for id '${ID}'`, "NOT_FOUND", undefined, 404) });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("This application wasn't found -- it may have been deleted.");
    expect(text(band(container))).toContain(`why: GET /applications/${ID} returned not found`);
    const back = [...band(container).querySelectorAll("a")].find((a) => text(a) === "Go to Applications");
    expect(back?.getAttribute("href")).toBe("/applications");
    expect(buttonNamedOrNull(band(container), "Try again")).toBeNull();
    expect(callsTo(APP_PATH)).toHaveLength(1);
  });

  it("puts the page's own failure in the band with Try again and the failed call and time, and a callout with no Retry in the body", async () => {
    describeAccount({ app: new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500) });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Couldn't load this application. You didn't do anything wrong, and nothing was lost.");
    expect(text(band(container))).toMatch(/why: GET \/applications\/app-1 failed · \d\d:\d\d/);
    const body = query(container, ".bj-state--failure");
    expect(text(body)).toContain("The server hit a problem.");
    expect(body.querySelector("button")).toBeNull();

    on(APP_PATH, application());
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(names(container, "h1")).toEqual(["Data Engineer"]);
  });

  it("says the setup check failed, not the page, when the profile could not be read", async () => {
    describeAccount({ profile: new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500) as unknown as typeof PROFILE });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Couldn't check your setup.");
    expect(text(band(container))).toMatch(/why: GET \/profile\/current failed · \d\d:\d\d/);
    expect(text(container)).toContain("Couldn't read your profile");
  });

  it("tolerates a missing profile: the page renders and each part that needs one says so, with a link", async () => {
    describeAccount({ profile: new FakeApiError("none", "NOT_FOUND", undefined, 404) as unknown as typeof PROFILE });
    const { container } = page();
    await settle();
    expect(names(container, "h1")).toEqual(["Data Engineer"]);
    expect(text(band(container))).toContain("Everything here starts from your profile. Import it first.");
    expect(text(band(container))).toContain("why: no active profile");
    expect(container.querySelector("[data-standin='Header composer']")).toBeNull();
    expect(container.querySelector("[data-standin='Tailor panel']")).toBeNull();
    const missing = [...container.querySelectorAll(".bj-state--setup a")].map((a) => [text(a), a.getAttribute("href")]);
    expect(missing.length).toBeGreaterThanOrEqual(3);
    for (const entry of missing) expect(entry).toEqual(["Add your profile", "/profile"]);
    // The research panels do not read the profile, and still mount.
    expect(container.querySelector("[data-standin='Company intel panel']")).not.toBeNull();
    // Nothing that needs a profile was asked of the server.
    expect(callsTo(`POST /applications/${ID}/prepare`)).toHaveLength(0);
  });
});

describe("the sections", () => {
  it("are the document the jump bar indexes, in order, with one h1 and an h2 for each section", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    expect(names(container, "h1")).toEqual(["Data Engineer"]);
    expect(names(container, ".bj-chapter-title--h2")).toEqual([
      "Tailor",
      "Draft",
      "Generate",
      "Review",
      "Company intel",
      "Contacts",
      "Warm-path events",
      "Hiring posts",
      "Interview practice",
      "Your play",
    ]);
    const links = [...query(container, 'nav[aria-label="Sections on this page"]').querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(links).toEqual(["#ch-sources", "#ch-draft", "#generate", "#ch-check", "#company-intel", "#contacts", "#warm-path-events", "#hiring-posts", "#interview-practice", "#your-play"]);
    // Every link has its anchor, and a heading to take focus.
    for (const link of links) {
      const target = container.querySelector(link!);
      expect(target, link!).not.toBeNull();
      expect(target?.querySelector("h2[tabindex='-1']"), link!).not.toBeNull();
    }
  });

  it("gives every CROSS_NAV_HASH value an element that is rendered", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    for (const hash of Object.values(CROSS_NAV_HASH)) expect(container.querySelector(`#${hash}`), hash).not.toBeNull();
    expect(Object.values(SECTION_IDS).sort()).toEqual(Object.values(CROSS_NAV_HASH).sort());
  });

  it("hides a research panel's own h2 by rule and shows one heading for the section", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    const section = query(container, "#company-intel").parentElement!;
    expect(section.querySelectorAll(".bj-chapter-title").length).toBe(1);
    expect(section.querySelector(".bj-ws-panel .bj-card > h2")).not.toBeNull();
  });

  it("embeds the Studio unchanged, each given this application", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    for (const name of ["Header composer", "Section order editor", "Shape settings"]) {
      expect(text(query(container, `[data-standin='${name}']`))).toBe(`${name} for ${ID}`);
    }
  });

  it("leaves Hiring posts out of the bar and the page while the server is not known to have it, and says it is off only to someone who asked", async () => {
    describeAccount();
    hiring.kind = "checking";
    const { container, unmount } = page();
    await settle();
    expect(container.querySelector("a[href='#hiring-posts']")).toBeNull();
    expect(container.querySelector("#hiring-posts")).not.toBeNull();
    expect(text(query(container, "#hiring-posts"))).toBe("");
    unmount();

    hiring.kind = "disabled";
    const quiet = page();
    await settle();
    expect(text(quiet.container)).not.toContain("switched off");
    quiet.unmount();

    const asked = page(`/applications/${ID}#hiring-posts`);
    await settle();
    expect(text(query(asked.container, "#hiring-posts"))).toContain("Hiring posts is switched off on this server.");
    expect(asked.container.querySelector("a[href='#hiring-posts']")).toBeNull();
  });
});

describe("the header", () => {
  it("shows the job's identity, the posting, and a trail from returned fields only, inline at 1280", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    expect(text(query(container, ".bj-page-subtitle"))).toBe("Northwind Labs · Remote (US)");
    expect(text(query(container, ".bj-ws-meta .bj-trail"))).toBe("● You tracked this · from Discover · Sep 22");
    expect(query(container, ".bj-ws-meta").closest(".bj-doc")).toBeNull();
    const link = query<HTMLAnchorElement>(container, ".bj-ws-facts a");
    expect(link.getAttribute("href")).toBe("https://jobs.example.com/roles/7");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("leaves out what did not come back: no channel, no date, a posting address that is not a web address", async () => {
    describeAccount({ app: application({ source_channel: undefined, created_at: undefined, snapshot: { ...application().snapshot, source_url: "javascript:alert(1)", location_text: null } }) });
    const { container } = page();
    await settle();
    expect(text(query(container, ".bj-ws-meta .bj-trail"))).toBe("● You tracked this");
    expect(container.querySelector(".bj-ws-facts")).toBeNull();
    expect(text(query(container, ".bj-page-subtitle"))).toBe("Northwind Labs");
  });

  it("keeps the trail inline at 960-1279 and on a phone too", async () => {
    describeAccount();
    for (const width of [1100, 375]) {
      installViewport(width);
      const { container, unmount } = page();
      await settle();
      expect(text(query(container, ".bj-ws-meta .bj-trail"))).toContain("You tracked this");
      unmount();
    }
  });

  it("writes a date applied from date_applied", async () => {
    describeAccount({ app: application({ status: "applied", date_applied: "2026-10-02" }) });
    const { container } = page();
    await settle();
    expect(text(query(container, ".bj-ws-facts"))).toContain("Applied Oct 2");
  });
});

describe("the status select", () => {
  it("offers the seven statuses, shows the one the application has, and moves it through the stage endpoint, then re-reads", async () => {
    describeAccount();
    on(`POST /applications/${ID}/stage`, {});
    const { container } = page();
    await settle();
    const select = query<HTMLSelectElement>(container, "select.bj-ws-status-select");
    expect([...select.options].map((option) => option.textContent)).toEqual(["Saved", "Applied", "Screening", "Interviewing", "Offer", "Rejected", "Withdrawn"]);
    expect(select.value).toBe("saved");
    on(APP_PATH, application({ status: "applied", date_applied: "2026-10-19" }));
    choose(select, "applied");
    await settle();
    expect(callsTo(`POST /applications/${ID}/stage`)[0].body).toEqual({ new_status: "applied", idempotency_key: "00000000-0000-4000-8000-000000000000" });
    expect(callsTo(APP_PATH)).toHaveLength(2);
    expect(query<HTMLSelectElement>(container, "select.bj-ws-status-select").value).toBe("applied");
    expect(text(band(container))).toContain("Applied Oct 19. Waiting on them.");
  });

  it("says a failed change failed, keeps the old status, and retries it", async () => {
    describeAccount();
    on(`POST /applications/${ID}/stage`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = page();
    await settle();
    choose(query<HTMLSelectElement>(container, "select.bj-ws-status-select"), "offer");
    await settle();
    expect(query<HTMLSelectElement>(container, "select.bj-ws-status-select").value).toBe("saved");
    const callout = query(container, ".bj-ws-meta .bj-state--failure");
    expect(text(callout)).toContain("The server hit a problem.");
    on(`POST /applications/${ID}/stage`, {});
    on(APP_PATH, application({ status: "offer" }));
    click(buttonNamed(callout, "Retry"));
    await settle();
    expect(query<HTMLSelectElement>(container, "select.bj-ws-status-select").value).toBe("offer");
    expect(container.querySelector(".bj-ws-meta .bj-state--failure")).toBeNull();
  });

  it("retries a failed change under the same idempotency key, and a new choice under a new one", async () => {
    describeAccount();
    const uuid = vi.spyOn(window.crypto, "randomUUID");
    uuid.mockReset();
    uuid.mockReturnValueOnce("11111111-1111-4111-8111-111111111111").mockReturnValueOnce("22222222-2222-4222-8222-222222222222");
    on(`POST /applications/${ID}/stage`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = page();
    await settle();
    choose(query<HTMLSelectElement>(container, "select.bj-ws-status-select"), "applied");
    await settle();
    on(`POST /applications/${ID}/stage`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    click(buttonNamed(query(container, ".bj-ws-meta .bj-state--failure"), "Retry"));
    await settle();
    on(`POST /applications/${ID}/stage`, {});
    on(APP_PATH, application({ status: "applied" }));
    click(buttonNamed(query(container, ".bj-ws-meta .bj-state--failure"), "Retry"));
    await settle();
    const keys = callsTo(`POST /applications/${ID}/stage`).map((call) => (call.body as { idempotency_key: string }).idempotency_key);
    expect(keys).toEqual(["11111111-1111-4111-8111-111111111111", "11111111-1111-4111-8111-111111111111", "11111111-1111-4111-8111-111111111111"]);
    choose(query<HTMLSelectElement>(container, "select.bj-ws-status-select"), "offer");
    await settle();
    expect(callsTo(`POST /applications/${ID}/stage`)[3].body).toMatchObject({ new_status: "offer", idempotency_key: "22222222-2222-4222-8222-222222222222" });
  });

  it("holds still while a change is out: aria-disabled, changes ignored, and the band's buttons wait too", async () => {
    describeAccount();
    let release: (value: unknown) => void = () => undefined;
    on(`POST /applications/${ID}/stage`, () => new Promise((resolve) => (release = resolve)));
    const { container } = page();
    await settle();
    choose(query<HTMLSelectElement>(container, "select.bj-ws-status-select"), "applied");
    await settle(1);
    const select = query<HTMLSelectElement>(container, "select.bj-ws-status-select");
    expect(select.getAttribute("aria-disabled")).toBe("true");
    choose(select, "offer");
    expect(callsTo(`POST /applications/${ID}/stage`)).toHaveLength(1);
    expect(band(container).querySelector("button[aria-disabled='true']")).not.toBeNull();
    on(APP_PATH, application({ status: "applied" }));
    release({});
    await settle();
  });
});

describe("the band, from the first step to the last", () => {
  it("A1: no resume yet -> Generate resume, the page's one cyan, with the cover letter choice in the band and not twice", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Generate a tailored resume for this posting.");
    expect(text(band(container))).toContain("why: status saved · no resume yet · profile active");
    expect(cyan(container)).toHaveLength(1);
    expect(band(container).contains(cyan(container)[0])).toBe(true);
    expect(text(cyan(container)[0])).toBe("Generate resume");
    expect(band(container).querySelector("input[type='checkbox']")).not.toBeNull();
    expect(container.querySelectorAll("input[type='checkbox']")).toHaveLength(1);
    // The body's copy of the action is outlined.
    const body = [...container.querySelectorAll("button")].filter((button) => text(button) === "Generate resume" && !band(container).contains(button));
    expect(body).toHaveLength(1);
    expect(body[0].classList.contains("bj-primary")).toBe(false);
  });

  it("generates from the band: the same request as the body's button, with the cover letter asked for only when ticked", async () => {
    describeAccount();
    on(`POST /applications/${ID}/prepare`, drafted([CLAIM]));
    const { container } = page();
    await settle();
    click(query(band(container), "input[type='checkbox']"));
    click(cyan(container)[0]);
    await settle();
    expect(callsTo(`POST /applications/${ID}/prepare`)[0].body).toEqual({
      idempotency_key: "00000000-0000-4000-8000-000000000000",
      force_generate: false,
      generate_cover_letter: true,
    });
    expect(text(band(container))).toContain("Read the 1 flagged claim, then download.");
  });

  it("shows Generating... on the band's button while the run is out, aria-disabled, and ignores a second press", async () => {
    describeAccount();
    let release: (value: unknown) => void = () => undefined;
    on(`POST /applications/${ID}/prepare`, () => new Promise((resolve) => (release = resolve)));
    const { container } = page();
    await settle();
    click(cyan(container)[0]);
    await settle(1);
    const button = cyan(container)[0];
    expect(text(button)).toBe("Generating...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    click(button);
    expect(callsTo(`POST /applications/${ID}/prepare`)).toHaveLength(1);
    release(drafted());
    await settle();
    expect(text(band(container))).toContain("No claims flagged. Give it one read, then download.");
  });

  it("A1c: a run that failed in this visit -> Generate again in the band, and no Retry of the body's own", async () => {
    describeAccount();
    on(`POST /applications/${ID}/prepare`, new FakeApiError("The engine did not answer.", "INTERNAL", undefined, 500));
    const { container } = page();
    await settle();
    click(cyan(container)[0]);
    await settle();
    expect(text(band(container))).toContain("Generation didn't finish. You didn't do anything wrong, and nothing was lost.");
    expect(text(band(container))).toMatch(/why: POST \/prepare failed · \d\d:\d\d/);
    expect(text(cyan(container)[0])).toBe("Generate again");
    const callout = query(container, ".bj-ws-generate .bj-state--failure");
    expect(text(callout)).toContain("The engine did not answer.");
    expect(callout.querySelector("button")).toBeNull();
    on(`POST /applications/${ID}/prepare`, drafted());
    click(cyan(container)[0]);
    await settle();
    expect(callsTo(`POST /applications/${ID}/prepare`)).toHaveLength(2);
  });

  it("A1b: held back -> Read the fit check in cyan and Generate anyway outlined, no resume, calm honest read", async () => {
    describeAccount({ result: HELD });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("No resume yet: the fit check held this draft back. Read why, then decide -- your call.");
    expect(text(band(container))).toContain("why: latest attempt held back · ceiling 3.6 / 10");
    // A held-back draft is not a failure: the band carries no red cross, as the honest read below has no red.
    expect(band(container).querySelector(".bj-band-glyph")).toBeNull();
    expect(text(cyan(container)[0])).toBe("Read the fit check");
    expect(cyan(container)).toHaveLength(1);
    const secondary = buttonNamed(band(container), "Generate anyway");
    expect(secondary.classList.contains("bj-primary")).toBe(false);
    expect(query(container, "#honest-read-heading").tagName).toBe("H3");
    expect(container.querySelector(".bj-danger-callout, .bj-ws-callout--danger")).toBeNull();
    // Reading the fit check goes to its heading.
    click(cyan(container)[0]);
    expect(document.activeElement?.id).toBe("honest-read-heading");
    // Generate anyway forces the run.
    on(`POST /applications/${ID}/prepare`, drafted());
    click(secondary);
    await settle();
    expect(callsTo(`POST /applications/${ID}/prepare`)[0].body).toMatchObject({ force_generate: true });
  });

  it("A1b with an earlier resume: says the earlier one is still below and that the latest attempt declined", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: HELD });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("The fit check held the latest attempt back. Your earlier resume is still below.");
    expect(text(band(container))).toContain("· earlier resume kept");
    expect(text(container)).toContain("From an earlier successful generation -- the latest attempt above declined.");
    expect(buttonNamed(container, "Download PDF")).toBeTruthy();
  });

  it("arrives at #generate on the honest read's heading when the latest attempt was held back", async () => {
    describeAccount({ result: HELD });
    page(`/applications/${ID}#generate`);
    await settle();
    expect(document.activeElement?.id).toBe("honest-read-heading");
  });

  it("A2 and A3: a drafted resume says what was flagged, as flagged and never as verified", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    const flagged = page();
    await settle();
    expect(text(band(flagged.container))).toContain("Read the 1 flagged claim, then download.");
    expect(text(band(flagged.container))).toContain("why: status saved · resume drafted · 1 claim flagged");
    flagged.unmount();

    describeAccount({ app: application({ resume_exists: true }), result: drafted([]) });
    const clean = page();
    await settle();
    expect(text(band(clean.container))).toContain("No claims flagged. Give it one read, then download.");
    expect(text(band(clean.container))).toContain("why: status saved · resume drafted · 0 flagged, not the same as verified");
  });

  it("A2u: a resume with no stored result says its claim check was not stored", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: null });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("why: status saved · resume drafted · claim check result not stored");
    expect(text(container)).toContain("This page has no stored score or claim check for your resume.");
  });

  it("A4: downloading the PDF from the band moves on to Open the posting, and focus follows the band", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    const { container } = page();
    await settle();
    click(buttonNamed(band(container), "Download PDF"));
    await settle();
    expect(callsTo(`GET /applications/${ID}/resume.pdf`)).toHaveLength(1);
    expect(text(band(container))).toContain("Submit on the employer's site, then mark it applied.");
    expect(text(band(container))).toContain("why: PDF downloaded this session · status saved");
    const open = [...band(container).querySelectorAll("a")].find((a) => text(a) === "Open the posting")!;
    expect(open.getAttribute("href")).toBe("https://jobs.example.com/roles/7");
    expect(open.getAttribute("target")).toBe("_blank");
    expect(document.activeElement).toBe(open);
    expect(cyan(container)).toHaveLength(1);
  });

  it("offers the person's own step under Review: open the posting, and record it as applied through the stage endpoint", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([]) });
    on(`POST /applications/${ID}/stage`, {});
    const { container } = page();
    await settle();
    const submit = query(container, ".bj-ws-submit");
    expect(text(submit)).toContain("Between Jobs never submits an application for you.");
    expect([...submit.querySelectorAll("a")].map((a) => text(a))).toEqual(["Open the posting (opens in a new tab)"]);
    on(APP_PATH, application({ status: "applied", resume_exists: true, date_applied: "2026-10-19" }));
    click(buttonNamed(submit, "I submitted this -- mark as applied"));
    await settle();
    expect(callsTo(`POST /applications/${ID}/stage`)[0].body).toMatchObject({ new_status: "applied" });
    expect(container.querySelector(".bj-ws-submit")).toBeNull();
    expect(text(band(container))).toContain("Applied Oct 19. Waiting on them.");
  });

  it("does not offer it before a resume exists, or once the application has moved past Saved", async () => {
    describeAccount();
    const none = page();
    await settle();
    expect(none.container.querySelector(".bj-ws-submit")).toBeNull();
    none.unmount();
    describeAccount({ app: application({ status: "applied", resume_exists: true }), result: drafted([]) });
    const applied = page();
    await settle();
    expect(applied.container.querySelector(".bj-ws-submit")).toBeNull();
  });

  it.each([
    ["applied", { status: "applied", date_applied: "2026-09-30" }, "Applied Sep 30. Waiting on them.", "status applied", false],
    ["screening", { status: "screening" }, "Practice for this interview.", "status screening", true],
    ["interviewing", { status: "interviewing" }, "Practice for this interview.", "status interviewing", true],
    ["offer", { status: "offer" }, "Offer stage. Nothing for us to draft -- your decision.", "status offer", false],
    ["rejected", { status: "rejected" }, "At rest. Move it back any time.", "status rejected", false],
    ["withdrawn", { status: "withdrawn" }, "At rest. Move it back any time.", "status withdrawn", false],
  ])("%s: the band says where the application stands", async (_name, fields, sentence, why, lit) => {
    describeAccount({ app: application({ ...fields, resume_exists: true }), result: drafted([CLAIM]) });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain(sentence);
    expect(text(band(container))).toContain(why);
    expect(cyan(container).length).toBe(lit ? 1 : 0);
    if (lit) {
      const link = cyan(container)[0] as HTMLAnchorElement;
      expect(text(link)).toBe("Go to Practice");
      expect(link.getAttribute("href")).toBe(`/applications/${ID}#interview-practice`);
    } else {
      // Unlit: the sentence takes focus for the skip link, and nothing else on the page turns cyan.
      expect(band(container).querySelector("#next-step-focus")?.tagName).toBe("P");
    }
  });

  it("says what Gmail does for an applied application only when it knows", async () => {
    describeAccount({ app: application({ status: "applied" }) });
    const unknown = page();
    await settle();
    expect(text(band(unknown.container))).toContain("why: status applied");
    expect(text(band(unknown.container))).not.toContain("Gmail");
    unknown.unmount();

    gmail.state = { kind: "ready", capabilities: { gmail: true, gmailAllowed: true } };
    describeAccount({ app: application({ status: "applied" }), credentials: [...KEY, { service: "oauth", provider: "gmail", is_validated: true, scope: "https://www.googleapis.com/auth/gmail.readonly" }] });
    const connected = page();
    await settle();
    expect(text(band(connected.container))).toContain("a reply may move this automatically, or appear in Today for you to confirm");
    connected.unmount();

    describeAccount({ app: application({ status: "applied" }) });
    const absent = page();
    await settle();
    expect(text(band(absent.container))).toContain("Gmail not connected -- move it yourself when they reply");
  });

  it("holds the band at Loading... until the stored generation has been read, then lights it", async () => {
    describeAccount();
    let release: (value: unknown) => void = () => undefined;
    on(`GET /applications/${ID}/prepare-result`, () => new Promise((resolve) => (release = resolve)));
    const { container } = page();
    await settle();
    expect(text(band(container))).toBe("Next stepLoading...");
    release({ result: null });
    await settle();
    expect(text(band(container))).toContain("Generate a tailored resume for this posting.");
  });
});

describe("setup that is known to be missing", () => {
  it("G3: no usable model key -> the band asks for one, Tailor and Generate say so and fire nothing", async () => {
    describeAccount({ credentials: [] });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Add a model key so Between Jobs can draft and score.");
    expect(container.querySelector("[data-standin='Tailor panel']")).toBeNull();
    const missing = [...container.querySelectorAll(".bj-state--setup a")].map((a) => [text(a), a.getAttribute("href")]);
    expect(missing).toEqual([["Add a model key", "/settings#ai-provider"], ["Add a model key", "/settings#ai-provider"]]);
    expect(buttonNamedOrNull(container, "Generate resume")).toBeNull();
    expect(callsTo(`POST /applications/${ID}/prepare`)).toHaveLength(0);
    expect(cyan(container)).toHaveLength(1);
  });

  it("says Choose a model for a key that is saved without one", async () => {
    describeAccount({ credentials: [{ service: "llm", provider: "openrouter", model: null, is_validated: true, setup_state: "needs_model" }] });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Your model key is saved. Choose a model so Between Jobs can draft and score.");
    expect(text(container)).toContain("Choose a model");
  });

  it("claims nothing when the credentials could not be read: the band says the setup check failed and the parts stay usable", async () => {
    describeAccount({ credentials: new FakeApiError("no", "INTERNAL", undefined, 500) as unknown as typeof KEY });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Couldn't check your setup.");
    expect(text(band(container))).toMatch(/GET \/credentials failed/);
    expect(container.querySelector("[data-standin='Tailor panel']")).not.toBeNull();
    expect(buttonNamed(container, "Generate resume")).toBeTruthy();
    on("GET /credentials", KEY);
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(text(band(container))).toContain("Generate a tailored resume for this posting.");
  });
});

describe("the Generate and Review sections", () => {
  it("restores the stored result and says so, with no date", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    const { container } = page();
    await settle();
    const generate = query(container, "#generate").parentElement!;
    expect(text(generate)).toContain("Resume drafted");
    expect(text(generate)).toContain("Read back from your last stored generation: no time came back with it.");
    expect(text(generate)).not.toMatch(/\b(ago|\d\d:\d\d)\b/);
  });

  it("explains a failed read of the stored generation with a Retry that reads it again, and does not pretend there is a result", async () => {
    describeAccount();
    on(`GET /applications/${ID}/prepare-result`, new FakeApiError("no", "INTERNAL", undefined, 500));
    const { container } = page();
    await settle();
    const callout = query(container, ".bj-ws-generate .bj-state--failure");
    expect(text(callout)).toContain("Couldn't read your last generation.");
    on(`GET /applications/${ID}/prepare-result`, { result: drafted([]) });
    click(buttonNamed(callout, "Retry"));
    await settle();
    expect(container.querySelector(".bj-ws-generate .bj-state--failure")).toBeNull();
    expect(text(container)).toContain("Resume drafted");
  });

  it("shows the ATS score, the claim check and the export checks of a drafted resume, each with its trail", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM], [attempt(64), attempt(72)]) });
    const { container } = page();
    await settle();
    const review = query(container, "#ch-check").parentElement!;
    expect(text(query(review, ".bj-scoreblock-numeral"))).toContain("64 -> 72");
    expect(names(review, ".bj-segbar-label")).toEqual(["Semantic coverage", "Experience quality", "Hard requirements", "Quantified impact", "Company alignment · structure"]);
    expect(names(review, ".bj-segbar-value")).toEqual(["27 / 35 · near max", "22 / 30 · near max", "8 / 15 · has headroom", "6 / 10 · near max", "9 / 10 · near max"]);
    expect(names(review, ".bj-claimrow-claim")).toEqual(["“Cut query costs by 40%”"]);
    const trails = names(container, ".bj-trail");
    expect(trails.some((t) => t.endsWith("Drafted by your model · on your model key · 1 flagged"))).toBe(true);
    expect(trails.some((t) => t.startsWith("engine") && t.endsWith("code-weighted from your model's sub-scores"))).toBe(true);
    expect(trails.some((t) => t.endsWith("Claim check · claims compared to the achievements picked for this draft · 1 flagged"))).toBe(true);
  });

  it("shows no score for a resume that was not scored, and never a number in its place", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: { ...drafted([]), ats_attempts: [], final_score: null } });
    const { container } = page();
    await settle();
    expect(query(container, "#ch-check").parentElement!.querySelector(".bj-scoreblock")).toBeNull();
    expect(text(container)).toContain("This resume wasn't scored");
  });

  it("offers the gap interview only under a score of 70, and nowhere in a held-back state", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([], [attempt(72)]) });
    const high = page();
    await settle();
    expect(high.container.querySelector(".bj-ws-gap")).toBeNull();
    high.unmount();
    describeAccount({ app: application({ resume_exists: true }), result: drafted([], [attempt(61)]) });
    const low = page();
    await settle();
    expect(buttonNamed(low.container, "Close the gaps")).toBeTruthy();
    low.unmount();
    describeAccount({ result: HELD });
    const held = page();
    await settle();
    expect(held.container.querySelector(".bj-ws-gap")).toBeNull();
  });

  it("says there is nothing to review before a resume exists, and offers no export checks", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    expect(text(query(container, "#ch-check").parentElement!)).toContain("Nothing to review yet");
    expect(buttonNamedOrNull(container, "Run the export checks")).toBeNull();
  });

  it("runs the export checks only when asked, once per press, and counts them as 5 pass · 1 fail · 4 not checked", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    on(`GET /applications/${ID}/export-checklist`, CHECKLIST);
    const { container } = page();
    await settle();
    expect(callsTo(`GET /applications/${ID}/export-checklist`)).toHaveLength(0);
    click(buttonNamed(container, "Run the export checks"));
    await settle();
    expect(callsTo(`GET /applications/${ID}/export-checklist`)).toHaveLength(1);
    const export_ = query(container, ".bj-ws-export");
    expect(text(query(export_, ".bj-checkgroup-summary"))).toBe("5 pass · 1 fail · 4 not checked");
    expect(names(export_, ".bj-checkgroup-title")).toEqual(["Checked by Between Jobs (6)", "Yours to check (4)"]);
    expect(names(container, ".bj-trail").some((t) => t.endsWith("Checked by code · no model involved"))).toBe(true);
  });

  it("does not carry an old checklist over to a new generation", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    on(`GET /applications/${ID}/export-checklist`, CHECKLIST);
    on(`POST /applications/${ID}/prepare`, drafted([]));
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Run the export checks"));
    await settle();
    expect(container.querySelector(".bj-checkgroup")).not.toBeNull();
    click(buttonNamed(container, "Generate resume"));
    await settle();
    expect(container.querySelector(".bj-checkgroup")).toBeNull();
  });
});

describe("one cyan fill, however the page is", () => {
  it.each([
    ["no resume", {}],
    ["held back", { result: HELD }],
    ["drafted", { app: application({ resume_exists: true }), result: drafted([CLAIM], [attempt(55, ["Missing dealbreaker: Active clearance"])]) }],
    ["applied", { app: application({ status: "applied" }) }],
    ["no key", { credentials: [] }],
  ] as const)("has at most one in the band and none in the body: %s", async (_name, account) => {
    describeAccount(account as Account);
    const { container } = page();
    await settle();
    const fills = cyan(container);
    expect(fills.length).toBeLessThanOrEqual(1);
    for (const fill of fills) expect(band(container).contains(fill)).toBe(true);
    // Every selectable control is a pressed state or the jump bar, never a second primary.
    expect(container.querySelectorAll("button.bj-primary")).toHaveLength(fills.filter((fill) => fill.tagName === "BUTTON").length);
  });
});

describe("hash navigation and the jump bar", () => {
  it("lands focus on the heading of the section a link points at, for every section", async () => {
    describeAccount();
    for (const id of ["ch-sources", "ch-draft", "generate", "ch-check", "company-intel", "contacts", "warm-path-events", "hiring-posts", "interview-practice", "your-play"]) {
      scrolled = [];
      const { unmount } = page(`/applications/${ID}#${id}`);
      await settle();
      expect(scrolled, id).toContain(id);
      expect(document.activeElement?.tagName, id).toBe("H2");
      expect(query(document.body, `#${id}`).contains(document.activeElement), id).toBe(true);
      unmount();
    }
  });

  it("follows a jump bar link to its hash and focuses the heading", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    click(query(container, "a[href='#ch-check']"));
    await settle();
    expect(text(query(container, "[data-testid='where']"))).toBe(`/applications/${ID}#ch-check`);
    expect(text(document.activeElement!)).toBe("Review");
  });

  it("offers a Jump to select with the bar's own groups and labels on a phone", async () => {
    installViewport(375);
    describeAccount();
    const { container } = page();
    await settle();
    const select = query<HTMLSelectElement>(container, "nav[aria-label='Sections on this page'] select");
    expect([...select.querySelectorAll("optgroup")].map((group) => group.label)).toEqual(["Resume", "Research", "Prepare"]);
    expect([...select.querySelectorAll("option")].map((option) => option.textContent)).toContain("Review");
  });

  it("draws the jump bar's Hiring posts entry only while the feature is on", async () => {
    describeAccount();
    hiring.kind = "disabled";
    const { container } = page();
    await settle();
    expect(container.querySelector("nav[aria-label='Sections on this page'] a[href='#hiring-posts']")).toBeNull();
    expect(container.querySelector("nav[aria-label='Sections on this page'] a[href='#company-intel']")).not.toBeNull();
  });
});

describe("where the trail goes by width", () => {
  async function drafted_(width: number) {
    installViewport(width);
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    const view = page();
    await settle();
    return view;
  }

  it("is in the 280px margin beside its block from 1280px", async () => {
    const { container } = await drafted_(1280);
    expect(query(container, ".bj-doc").getAttribute("data-document-mode")).toBe("margin");
    const cell = [...container.querySelectorAll(".bj-doc-margin")].find((node) => text(node).includes("Drafted by your model"));
    expect(cell).toBeTruthy();
    expect(container.querySelector(".bj-doc-summary")).toBeNull();
  });

  it("is under its block from 960px, with the current state once at the top", async () => {
    const { container } = await drafted_(1100);
    expect(query(container, ".bj-doc").getAttribute("data-document-mode")).toBe("inline");
    expect(container.querySelectorAll(".bj-doc-margin")).toHaveLength(0);
    expect(text(query(container, ".bj-doc-summary"))).toBe("Status saved · resume drafted · ATS 72 / 100 · 1 claim flagged");
    expect(text(query(container, ".bj-doc-main .bj-trail-trigger"))).toContain("·");
  });

  it("is under its block on a phone and opens as a sheet with a Close button", async () => {
    const { container } = await drafted_(375);
    expect(query(container, ".bj-doc").getAttribute("data-document-mode")).toBe("sheet");
    const trigger = [...container.querySelectorAll("button.bj-trail-trigger")].find((node) => text(node).includes("Drafted by your model"))!;
    expect(trigger.getAttribute("aria-haspopup")).toBe("dialog");
    click(trigger);
    const sheet = document.querySelector("[role='dialog']")!;
    expect(sheet).not.toBeNull();
    expect(text(sheet)).toContain("run");
    expect(text(sheet)).toContain("0123456789abcdef".slice(0, 8));
    expect(buttonNamed(sheet, "Close")).toBeTruthy();
  });

  it("opens a receipt built from the returned run, hash and attempts", async () => {
    const { container } = await drafted_(1100);
    const trigger = [...container.querySelectorAll("button.bj-trail-trigger")].find((node) => text(node).includes("Drafted by your model"))!;
    click(trigger);
    const receipt = query(container, ".bj-receipt");
    expect(text(receipt)).toContain("run01234567");
    expect(text(receipt)).toContain("pdff00dfeedbabe");
    expect(text(receipt)).toContain("gateproceed");
    expect(text(receipt)).toContain("Restored from your last stored generation.");
  });
});

describe("keyboard and accessibility", () => {
  it("reaches the band's button by Skip to next step's target and runs it with Enter on the focused button", async () => {
    describeAccount();
    on(`POST /applications/${ID}/prepare`, drafted());
    const { container } = page();
    await settle();
    const target = query(container, "#next-step-focus");
    expect(target.tagName).toBe("BUTTON");
    target.focus();
    expect(keydown("Enter", { target })).toBe(true);
    click(target);
    await settle();
    expect(callsTo(`POST /applications/${ID}/prepare`)).toHaveLength(1);
  });

  it("puts every control of every state in reading order: block, its trail, the next block", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    const { container } = page();
    await settle();
    const blocks = [...container.querySelectorAll(".bj-doc-block")];
    expect(blocks.length).toBeGreaterThan(8);
    for (const block of blocks) {
      const kids = [...block.children].map((child) => child.className);
      expect(kids[0]).toContain("bj-doc-main");
    }
  });

  it.each([
    ["no resume", {}],
    ["held back", { result: HELD }],
    ["drafted with a low score", { app: application({ resume_exists: true }), result: drafted([CLAIM], [attempt(61, ["Missing dealbreaker: Active clearance", "Weak/missing requirement: Kafka"])]) }],
    ["no profile", { profile: new FakeApiError("none", "NOT_FOUND", undefined, 404) }],
  ] as const)("has no serious accessibility violation: %s", async (_name, account) => {
    describeAccount(account as Account);
    on(`GET /applications/${ID}/export-checklist`, CHECKLIST);
    const { container } = page();
    await settle();
    if (container.querySelector("button")) {
      const run = [...container.querySelectorAll("button")].find((button) => text(button) === "Run the export checks");
      if (run) {
        click(run);
        await settle();
      }
    }
    await expectNoSeriousA11yViolations(container);
  });

  it("makes no request it did not make before, except the credentials read", async () => {
    describeAccount();
    page();
    await settle();
    const paths = calls.map((call) => `${call.method} ${call.path}`).sort();
    expect(paths).toEqual(
      [APP_PATH, "GET /profile/current", "GET /credentials", `GET /applications/${ID}/prepare-result`].sort(),
    );
  });
});

describe("asking for the setup again", () => {
  const FAILED = (message = "no") => new FakeApiError(message, "INTERNAL", undefined, 500);

  it("re-reads only the credentials that failed and leaves the ready page mounted: no loading frame, no panel rebuilt, nothing asked twice", async () => {
    describeAccount({ credentials: FAILED() as unknown as typeof KEY, app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    const { container } = page();
    await settle();
    const tailor = query(container, "[data-standin='Tailor panel']");
    const before = { ...mounts };
    expect(Object.keys(before).length).toBeGreaterThanOrEqual(8);
    expect(text(container)).toContain("Resume drafted");

    let release: (value: unknown) => void = () => undefined;
    on("GET /credentials", () => new Promise((resolve) => (release = resolve)));
    click(buttonNamed(band(container), "Try again"));
    await settle(1);
    // While it is out: the page is still the page, and the band says it is trying.
    expect(query(container, "[data-standin='Tailor panel']")).toBe(tailor);
    expect(container.querySelector('[role="status"][aria-busy="true"]')).toBeNull();
    expect(text(band(container))).toContain("Trying again...");
    expect(buttonNamed(band(container), "Trying again...").getAttribute("aria-disabled")).toBe("true");
    release(KEY);
    await settle();

    expect(query(container, "[data-standin='Tailor panel']")).toBe(tailor);
    expect(mounts).toEqual(before);
    expect(text(container)).toContain("Resume drafted");
    expect(callsTo("GET /credentials")).toHaveLength(2);
    expect(callsTo("GET /profile/current")).toHaveLength(1);
    expect(callsTo(`GET /applications/${ID}/prepare-result`)).toHaveLength(1);
    expect(callsTo(APP_PATH)).toHaveLength(1);
    expect(text(band(container))).not.toContain("Couldn't check your setup.");
  });

  it("keeps a result made in this visit through the retry, and does not let a second paid run start", async () => {
    describeAccount({ credentials: FAILED() as unknown as typeof KEY });
    on(`POST /applications/${ID}/prepare`, drafted([CLAIM]));
    const { container } = page();
    await settle();
    const body = [...container.querySelectorAll("button")].find((button) => text(button) === "Generate resume" && !band(container).contains(button))!;
    click(body);
    await settle();
    expect(text(container)).toContain("Resume drafted");
    on("GET /credentials", KEY);
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(text(container)).toContain("Resume drafted");
    expect(callsTo(`POST /applications/${ID}/prepare`)).toHaveLength(1);
    expect(callsTo(`GET /applications/${ID}/prepare-result`)).toHaveLength(1);
  });

  it("asks for a profile that failed again, and the page comes back when it answers", async () => {
    describeAccount({ profile: FAILED() as unknown as typeof PROFILE });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Couldn't check your setup.");
    on("GET /profile/current", PROFILE);
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(names(container, "h1")).toEqual(["Data Engineer"]);
    expect(callsTo("GET /profile/current")).toHaveLength(2);
    expect(callsTo("GET /credentials")).toHaveLength(1);
  });

  it("waits for the credentials before it mounts Tailor or draws Generate's button, and a failed read is an answer that claims nothing", async () => {
    describeAccount();
    let answer: (value: unknown) => void = () => undefined;
    on("GET /credentials", () => new Promise((resolve) => (answer = resolve)));
    const held = page();
    await settle();
    expect(held.container.querySelector("[data-standin='Tailor panel']")).toBeNull();
    expect(mounts["Tailor panel"]).toBeUndefined();
    expect([...held.container.querySelectorAll("button")].some((button) => text(button) === "Generate resume")).toBe(false);
    expect(text(held.container)).toContain("Checking your model setup...");
    answer([]);
    await settle();
    // No usable key: still no Tailor, ever, and the part says so.
    expect(mounts["Tailor panel"]).toBeUndefined();
    expect(text(held.container)).toContain("no usable model key is saved");
    held.unmount();

    let answerKey: (value: unknown) => void = () => undefined;
    on("GET /credentials", () => new Promise((resolve) => (answerKey = resolve)));
    const withKey = page();
    await settle();
    expect(mounts["Tailor panel"]).toBeUndefined();
    answerKey(KEY);
    await settle();
    expect(mounts["Tailor panel"]).toBe(1);
    expect(buttonNamed(withKey.container, "Generate resume")).toBeTruthy();
    withKey.unmount();

    delete mounts["Tailor panel"];
    on("GET /credentials", FAILED());
    page();
    await settle();
    expect(mounts["Tailor panel"]).toBe(1);
  });

  it.each([
    ["no usable model key", { credentials: [] as unknown as typeof KEY }],
    ["no profile", { profile: new FakeApiError("none", "NOT_FOUND", undefined, 404) as unknown as typeof PROFILE }],
    ["the setup check itself failing", { credentials: FAILED() as unknown as typeof KEY }],
  ] as const)("gives the application's failed read a Retry of its own when the band is showing something else: %s", async (_name, account) => {
    describeAccount({ ...account, app: new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500) });
    const { container } = page();
    await settle();
    expect(text(band(container))).not.toContain("Couldn't load this application");
    const body = query(container, ".bj-state--failure");
    expect(text(body)).toContain("The server hit a problem.");
    on(APP_PATH, application());
    click(buttonNamed(body, "Retry"));
    await settle();
    expect(names(container, "h1")).toEqual(["Data Engineer"]);
    expect(callsTo(APP_PATH)).toHaveLength(2);
  });
});

describe("what the page says when it could not show a stored result", () => {
  it("says a failed read of the stored result could not be read, in the band and in Review, not that none is stored", async () => {
    describeAccount({ app: application({ resume_exists: true }) });
    on(`GET /applications/${ID}/prepare-result`, new FakeApiError("no", "INTERNAL", undefined, 500));
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("claim check result couldn't be read");
    expect(text(band(container))).not.toContain("not stored");
    const review = query(container, "#ch-check").parentElement!;
    expect(text(review)).toContain("couldn't show the score or claim check");
    expect(text(review)).not.toContain("no stored score");
  });

  it("says so after a run that did not finish dropped the result that was on screen, and keeps 'none stored' for a read that answered none", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    on(`POST /applications/${ID}/prepare`, new FakeApiError("Add a model key.", "SETUP_REQUIRED"));
    const { container, unmount } = page();
    await settle();
    const body = [...container.querySelectorAll("button")].find((button) => text(button) === "Generate resume" && !band(container).contains(button))!;
    click(body);
    await settle();
    const review = query(container, "#ch-check").parentElement!;
    expect(text(band(container))).toContain("claim check result couldn't be read");
    expect(text(review)).toContain("couldn't show the score or claim check");
    unmount();

    describeAccount({ app: application({ resume_exists: true }), result: null });
    on(`POST /applications/${ID}/prepare`, new FakeApiError("Add a model key.", "SETUP_REQUIRED"));
    const none = page();
    await settle();
    click([...none.container.querySelectorAll("button")].find((button) => text(button) === "Generate resume" && !band(none.container).contains(button))!);
    await settle();
    expect(text(band(none.container))).toContain("claim check result not stored");
    expect(text(none.container)).toContain("This page has no stored score or claim check for your resume.");
  });

  it("marks a restored held-back result as restored, with no date, under the honest read", async () => {
    describeAccount({ result: HELD });
    const { container } = page();
    await settle();
    const generate = query(container, "#generate").parentElement!;
    expect(text(query(generate, ".bj-ws-honest"))).toContain("Honest read");
    expect(text(generate)).toContain("Read back from your last stored generation: no time came back with it.");
  });

  it("says what a run produces before the first one, and not once a resume exists", async () => {
    describeAccount();
    const empty = page();
    await settle();
    expect(text(query(empty.container, ".bj-ws-intro"))).toContain("A run makes a resume PDF with its ATS score and its claim check, and a cover letter if you ask for one.");
    empty.unmount();
    describeAccount({ app: application({ resume_exists: true }), result: drafted([]) });
    const drafted_ = page();
    await settle();
    expect(text(query(drafted_.container, ".bj-ws-intro"))).not.toContain("A run makes");
  });

  it("calls the date applied the first one when the status is back at Saved, and the date applied otherwise", async () => {
    describeAccount({ app: application({ status: "saved", date_applied: "2026-09-22T12:00:00Z" }) });
    const saved = page();
    await settle();
    expect(text(query(saved.container, ".bj-ws-facts"))).toContain("First applied Sep 22");
    saved.unmount();
    describeAccount({ app: application({ status: "interviewing", date_applied: "2026-09-22T12:00:00Z" }) });
    const later = page();
    await settle();
    expect(text(query(later.container, ".bj-ws-facts"))).toContain("Applied Sep 22");
    expect(text(query(later.container, ".bj-ws-facts"))).not.toContain("First applied");
  });
});

describe("a new generation and the person's own marks", () => {
  it("drops 'I checked this' marks and an export check that was still out when the new run began", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    let answerChecks: (value: unknown) => void = () => undefined;
    on(`GET /applications/${ID}/export-checklist`, CHECKLIST);
    on(`POST /applications/${ID}/prepare`, drafted([]));
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Run the export checks"));
    await settle();
    click(query(container, "button.bj-selectable"));
    expect(container.querySelectorAll("button.bj-selectable[aria-pressed='true']")).toHaveLength(1);
    click([...container.querySelectorAll("button")].find((button) => text(button) === "Generate resume" && !band(container).contains(button))!);
    await settle();
    on(`GET /applications/${ID}/export-checklist`, CHECKLIST);
    click(buttonNamed(container, "Run the export checks"));
    await settle();
    expect(container.querySelectorAll("button.bj-selectable")).toHaveLength(4);
    expect(container.querySelectorAll("button.bj-selectable[aria-pressed='true']")).toHaveLength(0);

    // A check that is still out when the next run begins never lands under it.
    on(`GET /applications/${ID}/export-checklist`, () => new Promise((resolve) => (answerChecks = resolve)));
    click(buttonNamed(container, "Check again"));
    await settle(1);
    let finish: (value: unknown) => void = () => undefined;
    on(`POST /applications/${ID}/prepare`, () => new Promise((resolve) => (finish = resolve)));
    click([...container.querySelectorAll("button")].find((button) => text(button) === "Generate resume" && !band(container).contains(button))!);
    await settle(1);
    answerChecks(CHECKLIST);
    await settle();
    finish(drafted([]));
    await settle();
    expect(container.querySelector(".bj-checkgroup")).toBeNull();
  });
});

describe("where keyboard focus goes when the control that had it is replaced", () => {
  it("keeps it on the export checks button from the press to the finished checks", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([CLAIM]) });
    on(`GET /applications/${ID}/export-checklist`, CHECKLIST);
    const { container } = page();
    await settle();
    const run = buttonNamed(container, "Run the export checks");
    run.focus();
    click(run);
    await settle();
    expect(document.activeElement).toBe(run);
    expect(text(run)).toBe("Check again");
    expect(text(query(container, ".bj-ws-export .bj-visually-hidden[role='status']"))).toContain("Export checks finished: 5 pass · 1 fail · 4 not checked.");
  });

  it("moves it from Generate anyway to the Generate button, which says Generating...", async () => {
    describeAccount({ result: HELD });
    let release: (value: unknown) => void = () => undefined;
    on(`POST /applications/${ID}/prepare`, () => new Promise((resolve) => (release = resolve)));
    const { container } = page();
    await settle();
    const anyway = [...container.querySelectorAll(".bj-ws-honest button")].find((button) => text(button) === "Generate anyway") as HTMLElement;
    anyway.focus();
    click(anyway);
    await settle(1);
    expect(container.contains(anyway)).toBe(false);
    expect(document.activeElement?.tagName).toBe("BUTTON");
    expect(text(document.activeElement!)).toBe("Generating...");
    release(drafted());
    await settle();
  });

  // The band's second button is gone as soon as the run starts, and a run can take a minute: focus must not
  // sit on the page for all of it.
  it("moves it from the band's Generate anyway to the band at once, and holds it there while the run is out", async () => {
    describeAccount({ result: HELD });
    let release: (value: unknown) => void = () => undefined;
    on(`POST /applications/${ID}/prepare`, () => new Promise((resolve) => (release = resolve)));
    const { container } = page();
    await settle();
    const anyway = [...container.querySelectorAll(".bj-band button")].find((button) => text(button) === "Generate anyway") as HTMLElement;
    anyway.focus();
    click(anyway);
    await settle(1);
    expect(container.contains(anyway)).toBe(false);
    expect(document.activeElement?.id).toBe("next-step-focus");
    expect(container.contains(document.activeElement)).toBe(true);
    release(drafted());
    await settle();
    expect(document.activeElement?.id).toBe("next-step-focus");
  });

  it("moves it from Park this application to the Honest read heading", async () => {
    describeAccount({ result: HELD });
    on(`POST /applications/${ID}/stage`, {});
    on(APP_PATH, application({ status: "withdrawn" }));
    const { container } = page();
    await settle();
    const park = buttonNamed(query(container, ".bj-ws-honest"), "Park this application");
    park.focus();
    click(park);
    await settle();
    expect(document.activeElement?.id).toBe("honest-read-heading");
    expect(text(document.activeElement!)).toBe("Honest read");
  });

  it("moves it from I submitted this to the band's sentence, which says what is true now", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([]) });
    on(`POST /applications/${ID}/stage`, {});
    const { container } = page();
    await settle();
    const submit = buttonNamed(query(container, ".bj-ws-submit"), "I submitted this -- mark as applied");
    submit.focus();
    on(APP_PATH, application({ status: "applied", resume_exists: true, date_applied: "2026-10-19" }));
    click(submit);
    await settle();
    expect(container.querySelector(".bj-ws-submit")).toBeNull();
    expect(document.activeElement?.id).toBe("next-step-focus");
    expect(text(document.activeElement!)).toContain("Applied Oct 19. Waiting on them.");
  });

  it("leaves it on the button when recording the status failed, and the block stays", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([]) });
    on(`POST /applications/${ID}/stage`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = page();
    await settle();
    const submit = buttonNamed(query(container, ".bj-ws-submit"), "I submitted this -- mark as applied");
    submit.focus();
    click(submit);
    await settle();
    expect(document.activeElement).toBe(submit);
  });

  it("moves it from Close the gaps to the block that holds the interview", async () => {
    describeAccount({ app: application({ resume_exists: true }), result: drafted([], [attempt(61, ["Weak/missing requirement: Kafka"])]) });
    on("GET /resume-documents/mine?application_id=app-1", { id: "doc-1" });
    on("POST /resume-documents/doc-1/gap-interview", { questions: [] });
    const { container } = page();
    await settle();
    const close = buttonNamed(container, "Close the gaps");
    close.focus();
    click(close);
    await settle();
    expect(document.activeElement).toBe(query(container, ".bj-ws-gap"));
    expect(container.contains(close)).toBe(false);
  });
});

describe("the status select with a keyboard", () => {
  const select = (container: ParentNode) => query<HTMLSelectElement>(container, "select.bj-ws-status-select");

  it("holds a keyboard step until Enter, records one change for the status it ended on, and says what Enter will do", async () => {
    describeAccount();
    on(`POST /applications/${ID}/stage`, {});
    const { container } = page();
    await settle();
    const element = select(container);
    element.focus();
    for (const next of ["applied", "screening", "offer"]) {
      keydown("ArrowDown", { target: element });
      choose(element, next);
    }
    expect(callsTo(`POST /applications/${ID}/stage`)).toHaveLength(0);
    expect(element.value).toBe("offer");
    expect(text(query(container, ".bj-ws-status-hint"))).toBe("Press Enter to set Offer. Escape cancels.");
    expect(element.getAttribute("aria-describedby")).toBe(query(container, ".bj-ws-status-hint").id);
    on(APP_PATH, application({ status: "offer" }));
    keydown("Enter", { target: element });
    await settle();
    const posts = callsTo(`POST /applications/${ID}/stage`);
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toMatchObject({ new_status: "offer" });
    expect(container.querySelector(".bj-ws-status-hint")).toBeNull();
  });

  it("drops a held step on Escape, and on leaving the select, and records nothing", async () => {
    describeAccount();
    on(`POST /applications/${ID}/stage`, {});
    const { container } = page();
    await settle();
    const element = select(container);
    element.focus();
    keydown("ArrowDown", { target: element });
    choose(element, "applied");
    keydown("Escape", { target: element });
    expect(element.value).toBe("saved");
    keydown("ArrowDown", { target: element });
    choose(element, "applied");
    act(() => element.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
    expect(element.value).toBe("saved");
    expect(callsTo(`POST /applications/${ID}/stage`)).toHaveLength(0);
  });
});

describe("a deep link while the page above it is still loading", () => {
  it("brings the section back to the top at each change in the page's height until the person takes over", async () => {
    const callbacks: (() => void)[] = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        private readonly callback: () => void;
        constructor(callback: () => void) {
          this.callback = callback;
          callbacks.push(callback);
        }
        observe() {}
        disconnect() {
          const at = callbacks.indexOf(this.callback);
          if (at !== -1) callbacks.splice(at, 1);
        }
      },
    );
    describeAccount();
    page(`/applications/${ID}#company-intel`);
    await settle();
    scrolled = [];
    act(() => callbacks.forEach((callback) => callback()));
    act(() => callbacks.forEach((callback) => callback()));
    expect(scrolled.filter((id) => id === "company-intel").length).toBeGreaterThanOrEqual(1);
    const held = scrolled.length;
    act(() => {
      window.dispatchEvent(new Event("wheel"));
    });
    act(() => callbacks.forEach((callback) => callback()));
    expect(scrolled.length).toBe(held);
    vi.unstubAllGlobals();
  });
});

function buttonNamedOrNull(root: ParentNode, name: string): HTMLButtonElement | null {
  try {
    return buttonNamed(root, name);
  } catch {
    return null;
  }
}
