// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Fetched } from "../../lib/firstRun";
import { nextStepWithRow, type NextStepInput } from "../../lib/nextStep";
import { todayLit, type TodayRowId } from "../../lib/nextStep/pages/today";
import { SETTINGS_PATH } from "../../lib/settingsPaths";
import {
  applicationNames,
  backgroundView,
  searchesRead,
  todayFacts,
  type FitRead,
  type JobMatch,
  type TodayItem,
} from "../../lib/todayFeed";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { buttonNamed, click, query, render } from "../../testing/render";
import { TodayView, type AccountNotes, type FeedState, type TodayViewProps } from "./TodayView";

// Today as a person meets it, one state at a time. The view is a function of props, so each state is
// built the way the page builds it (the rule table's answer, the lit item it chose) and drawn here.

const NOW = new Date("2026-09-22T15:00:00Z");
const ok = <T,>(value: T) => ({ kind: "ok", value }) as const;
const failed = { kind: "failed" } as const;
const loading = { kind: "loading" } as const;
const CYAN_FILLS = ".bj-primary, .bj-button-link-primary";

function item(overrides: Partial<TodayItem> & Pick<TodayItem, "id" | "kind">): TodayItem {
  return { headline: "A headline", detail: null, created_at: "2026-09-22T10:00:00Z", application_id: null, job_match: null, status_proposal: null, ...overrides };
}

const MATCH: JobMatch = {
  today_item_id: "m1",
  saved_search_id: "ss-1",
  apply_url: "https://jobs.example/a",
  title: "Analytics Engineer",
  company: "Initech",
  location: "Remote",
  score100: 74,
  bin: "Strong",
  snippet: "s",
  provider: "registry",
};

const APPLICATIONS = ok([
  { id: "app-1", snapshot: { title: "Data Engineer", company_name: "Acme" } },
  { id: "app-2", snapshot: { title: "ML Engineer", company_name: "Orbit" } },
]);
const SEARCH_ROWS = ok([
  { id: "ss-1", query: "analytics engineer", location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" },
]);
const NO_NOTES: AccountNotes = { setupFailed: false, profileMissing: false, modelKeyMissing: false, applicationsFailed: false };

const proposal = item({
  id: "p1",
  kind: "status_proposal",
  application_id: "app-1",
  headline: "✉️ Possible update: interview requested",
  detail: "72% confidence -- review and confirm",
  status_proposal: { id: "sp1", proposed_type: "interview.requested", confidence: 0.72, evidence_spans: ["Are you free Thursday?"], status: "pending" },
});
const ready = item({ id: "r1", kind: "resume_ready", application_id: "app-2", headline: "📄 Resume ready for ML Engineer @ Orbit", detail: "ATS score 72/100" });
const held = item({ id: "f1", kind: "resume_failed", application_id: "app-1", headline: "⚠️ Resume generation needs attention for Data Engineer @ Acme", detail: "Skipped: too far from your profile" });
const match = item({ id: "m1", kind: "high_fit_job", headline: "🎯 High-fit match: Analytics Engineer @ Initech", detail: "Strong on SQL", job_match: MATCH });
const tracked = item({ id: "t1", kind: "job_tracked", application_id: "app-1", headline: "🆕 Tracking Data Engineer @ Acme" });
const staged = item({ id: "s1", kind: "stage_changed", application_id: "app-1", headline: "📌 Data Engineer @ Acme moved to applied", created_at: "2026-09-21T10:00:00Z" });

type Rows = Fetched<readonly Record<string, unknown>[]>;

interface Scenario {
  items?: TodayItem[] | null;
  feedFailure?: string;
  wide?: boolean;
  searches?: Rows;
  applications?: Rows;
  credentials?: Rows;
  profile?: NextStepInput["profile"];
  notes?: Partial<AccountNotes>;
  fits?: Record<string, FitRead>;
  busy?: TodayViewProps["busy"];
  problems?: TodayViewProps["problems"];
  trackedUrls?: Record<string, string>;
}

// Builds the props the way pages/Today.tsx does, then lets a test override what it is about.
function props(scenario: Scenario = {}, handlers: Partial<TodayViewProps> = {}): TodayViewProps {
  const searches = searchesRead(scenario.searches ?? SEARCH_ROWS);
  const applications: Rows = scenario.applications ?? APPLICATIONS;
  const credentials: Rows = scenario.credentials ?? ok([]);
  const items = scenario.items === undefined ? [] : scenario.items;
  const feed: FeedState =
    scenario.feedFailure !== undefined ? { state: "failed", message: scenario.feedFailure } : items === null ? { state: "loading" } : { state: "ready", items };
  const facts = todayFacts({ items: feed.state === "ready" ? feed.items : null, applications, searches, tracked: scenario.trackedUrls ?? {} });
  const input: NextStepInput = {
    page: "today",
    signedIn: true,
    profile: scenario.profile ?? { state: "ready", value: { active: true, importWaiting: false } },
    credentials: { state: "ready", value: {} as Record<string, never> },
    pageFailure: scenario.feedFailure === undefined ? null : { what: "Couldn't load Today", call: "GET /today", at: "09:14", intent: "retry" },
    facts,
  };
  const { outcome, rowId } = nextStepWithRow(input);
  const choice = feed.state === "ready" && rowId !== null && ["T1", "T2", "T3", "T4"].includes(rowId) ? todayLit(facts.feed.state === "ready" ? facts.feed.items : []) : null;
  const litItem = choice === null || feed.state !== "ready" ? null : (feed.items.find((candidate) => candidate.id === choice.item.id) ?? null);
  return {
    now: NOW,
    wide: scenario.wide ?? false,
    outcome,
    litRow: (choice?.row as TodayRowId | undefined) ?? null,
    litItem,
    feed,
    context: {
      now: NOW,
      timeZone: "UTC",
      names: applications.kind === "ok" ? applicationNames(applications.value) : new Map(),
      searchLabels: new Map(searches.state === "ready" ? searches.searches.map((search) => [search.id, search.label] as const) : []),
      fits: scenario.fits ?? {},
    },
    tracked: scenario.trackedUrls ?? {},
    busy: scenario.busy ?? {},
    problems: scenario.problems ?? {},
    background: backgroundView(searches, credentials),
    account: { ...NO_NOTES, ...scenario.notes },
    checklist: null,
    onBandAction: vi.fn(),
    onItemAction: vi.fn(),
    onRetryProblem: vi.fn(),
    onRetryAccount: vi.fn(),
    ...handlers,
  };
}

function draw(scenario: Scenario = {}, handlers: Partial<TodayViewProps> = {}) {
  const view = props(scenario, handlers);
  const rendered = render(
    <MemoryRouter>
      <TodayView {...view} />
    </MemoryRouter>,
  );
  return { ...rendered, view };
}

const words = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();
const band = (container: HTMLElement) => query(container, 'section[aria-label="Next step"]');
const rows = (container: HTMLElement) => [...container.querySelectorAll("li.bj-today-row")];

describe("the page's frame", () => {
  it("has one h1 (the serif Today), a plain subtitle, and the band directly under the header", () => {
    const { container } = draw({ items: [] });
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(words(query(container, "h1"))).toBe("Today");
    const header = query(container, "header");
    expect(header.nextElementSibling).toBe(band(container));
  });
});

describe("loading", () => {
  it("shows the band's eyebrow and Loading... with no button, and placeholder rows in the feed's shape", () => {
    const { container } = draw({ items: null, searches: loading, applications: loading });
    const section = band(container);
    expect(section.getAttribute("aria-busy")).toBe("true");
    expect(words(section)).toBe("Next stepLoading...");
    expect(section.querySelector("button, a")).toBeNull();
    const status = query(container, '.bj-state--loading[role="status"]');
    expect(words(status)).toBe("Loading Today...");
    expect(status.querySelectorAll(".bj-skeleton-row")).toHaveLength(3);
    expect(container.querySelector(CYAN_FILLS)).toBeNull();
    expect(container.querySelector("li.bj-today-row")).toBeNull();
  });

  it("waits for the names a sentence uses rather than lighting a sentence and rewriting it", () => {
    const { container } = draw({ items: [proposal], applications: loading });
    expect(band(container).getAttribute("aria-busy")).toBe("true");
  });

  it("shows the background trail's own placeholder while the saved searches load", () => {
    const { container } = draw({ items: [], searches: loading, wide: true });
    expect(words(query(container, ".bj-today-margin .bj-state--loading"))).toBe("Loading saved searches...");
  });
});

describe("the band holds the lit item, and the list does not", () => {
  it("lifts a status proposal into the band with its words, its trail and a quiet Dismiss, and drops it from the list", () => {
    const { container } = draw({ items: [tracked, proposal, ready] });
    const section = band(container);
    expect(words(query(section, ".bj-band-eyebrow"))).toBe("Next step · Status proposal");
    expect(words(query(section, ".bj-band-sentence"))).toBe("Acme may have requested an interview. Read their words, then decide.");
    expect(words(query(section, ".bj-band-why"))).toBe("why: status proposal · 72% · under the 85% auto-move line");
    expect(words(query(section, ".bj-band-item .bj-today-quote"))).toBe("“Are you free Thursday?”");
    expect(words(query(section, ".bj-band-item .bj-trail"))).toBe("✦ Read by your model · from a Gmail reply · Sep 22 · 72% · under the 85% auto-move line");
    const accept = buttonNamed(section, "Accept: move to Interviewing");
    expect(accept.className).toContain("bj-primary");
    const dismiss = buttonNamed(section, "Dismiss");
    expect(dismiss.className).toContain("bj-quiet");
    // Not in the list: no action shows twice.
    expect(rows(container).map((row) => row.getAttribute("data-kind"))).toEqual(["job_tracked", "resume_ready"]);
    expect(container.querySelectorAll("[data-kind='status_proposal']")).toHaveLength(0);
    expect(container.querySelectorAll("button.bj-primary")).toHaveLength(1);
  });

  it("puts the next item in the band when the lit one is dismissed away, and none twice", () => {
    const first = draw({ items: [proposal, ready, tracked] });
    expect(words(query(band(first.container), ".bj-band-sentence"))).toContain("Acme may have requested an interview");
    first.unmount();
    const second = draw({ items: [ready, tracked] });
    expect(words(query(band(second.container), ".bj-band-sentence"))).toBe("Your resume for ML Engineer at Orbit is ready for review.");
    expect(rows(second.container).map((row) => row.getAttribute("data-kind"))).toEqual(["job_tracked"]);
  });

  it("lights a ready resume (T3) with an Open workspace link and the gold chip", () => {
    const { container } = draw({ items: [ready] });
    const section = band(container);
    expect(words(query(section, ".bj-band-eyebrow"))).toBe("Next step · Resume ready");
    const link = query<HTMLAnchorElement>(section, "a.bj-button-link-primary");
    expect(link.getAttribute("href")).toBe("/applications/app-2#ch-check");
    expect(words(link)).toBe("Open workspace");
    expect(words(query(section, ".bj-today-chip"))).toBe("◆Ready for your review");
    expect(words(query(section, ".bj-band-why"))).toBe("why: resume ready · ATS score 72 / 100");
  });

  it("lights a high fit job (T4) with Track, and leaves the other items in the list", () => {
    const { container } = draw({ items: [match, tracked] });
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("A Strong match landed: Analytics Engineer at Initech.");
    expect(words(query(band(container), ".bj-band-why"))).toBe('why: high fit job · 74 / 100 · saved search "analytics engineer"');
    buttonNamed(band(container), "Track");
    expect(rows(container)).toHaveLength(1);
  });

  it("turns Track into Open workspace in place once the job is tracked", () => {
    const { container } = draw({ items: [match], trackedUrls: { [MATCH.apply_url]: "app-9" } });
    const link = query<HTMLAnchorElement>(band(container), "a.bj-button-link-primary");
    expect(link.getAttribute("href")).toBe("/applications/app-9");
  });

  it("holds nothing when no item has a step: the band is unlit and every item is in the list", () => {
    const { container } = draw({ items: [tracked, staged] });
    const section = band(container);
    expect(section.className).toContain("bj-band--unlit");
    expect(words(query(section, ".bj-band-sentence"))).toBe("Nothing needs a decision right now. 2 updates below.");
    expect(words(query(section, ".bj-band-eyebrow"))).toBe("Next step");
    expect(section.querySelector(".bj-band-item")).toBeNull();
    expect(rows(container)).toHaveLength(2);
    expect(container.querySelector(CYAN_FILLS)).toBeNull();
  });
});

describe("a resume the fit check held back", () => {
  it("is a calm card in the band: a red cross in the eyebrow, a gold chip with the score, the reason, and three controls", () => {
    const { container } = draw({ items: [held], fits: { "app-1": { state: "held", score: 3.6 } } });
    const section = band(container);
    const eyebrow = query(section, ".bj-band-eyebrow");
    expect(words(eyebrow)).toBe("✕Resume not generated");
    expect(query(eyebrow, ".bj-band-eyebrow-glyph").getAttribute("aria-hidden")).toBe("true");
    expect(words(query(section, ".bj-band-sentence"))).toBe("The fit check held back your draft for Data Engineer at Acme. Read why, then decide.");
    expect(words(query(section, ".bj-band-why"))).toBe("why: held back by the fit check · no resume generated");
    expect(words(query(section, ".bj-today-chip"))).toBe("◆Fit check held it back · 3.6 / 10");
    expect(words(query(section, ".bj-today-detail"))).toBe("Skipped: too far from your profile");
    const seeWhy = query<HTMLAnchorElement>(section, "a.bj-button-link-primary");
    expect(seeWhy.getAttribute("href")).toBe("/applications/app-1#generate");
    expect(words(seeWhy)).toBe("See why");
    const anyway = buttonNamed(section, "Generate anyway");
    expect(anyway.className).not.toContain("bj-primary");
    expect(buttonNamed(section, "Dismiss").className).toContain("bj-quiet");
    // No sentence in it says the draft failed or needs attention, and there is no Retry.
    expect(words(section)).not.toMatch(/needs attention|Retry|failed/i);
  });

  it("never turns red anywhere but the cross: no danger class, error line or red badge on the card", () => {
    const { container } = draw({ items: [held, { ...held, id: "f2", application_id: "app-2" }] });
    // In the band there is no red class at all (the cross in the eyebrow has its own).
    expect(band(container).innerHTML).not.toMatch(/danger|bj-error|bj-badge-danger/);
    // The second one is in the list: its cross in the kind line and its marker are the only red.
    const row = rows(container)[0];
    expect(words(query(row, ".bj-today-kind"))).toBe("✕Resume not generated");
    expect(query(row, ".bj-today-marker").className).toContain("bj-today-marker--danger");
    const red = [...row.querySelectorAll("[class*='danger'], [class*='error']")];
    expect(red.map((element) => words(element))).toEqual(["✕"]);
  });

  it("names the job from the application, not from the server's headline", () => {
    const { container } = draw({ items: [held, { ...held, id: "f2", application_id: "app-2" }] });
    const row = rows(container)[0];
    expect(words(query(row, ".bj-today-title"))).toBe("ML Engineer at Orbit");
    expect(row.textContent).not.toContain("needs attention");
  });

  it("says only that the fit check held it back when no score could be read", () => {
    const cases: Record<string, FitRead>[] = [{}, { "app-1": { state: "loading" } }, { "app-1": { state: "unavailable" } }];
    for (const fits of cases) {
      const { container, unmount } = draw({ items: [held], fits });
      expect(words(query(band(container), ".bj-today-chip"))).toBe("◆Fit check held it back");
      unmount();
    }
  });

  it("with no application keeps Dismiss only, and falls through to nothing lit", () => {
    const { container } = draw({ items: [{ ...held, application_id: null }] });
    expect(band(container).className).toContain("bj-band--unlit");
    const row = rows(container)[0];
    expect([...row.querySelectorAll("button, a")].map(words).filter((name) => name !== "")).toContain("Dismiss");
    expect(row.querySelector("a[href]")).toBeNull();
    expect(row.querySelectorAll("button.bj-today-action")).toHaveLength(1);
  });

  it("calls the handlers with the item when its buttons are pressed", () => {
    const onItemAction = vi.fn();
    const onBandAction = vi.fn();
    const { container } = draw({ items: [held] }, { onItemAction, onBandAction });
    click(buttonNamed(container, "Dismiss"));
    // The band's buttons run the rule table's intents; the item's own Dismiss is the page's.
    click(buttonNamed(container, "Generate anyway"));
    expect(onItemAction.mock.calls.map(([action, target]) => [action, target.id])).toEqual([["dismiss", "f1"]]);
    expect(onBandAction).toHaveBeenCalledWith("generate-anyway:f1");
  });
});

describe("a row", () => {
  it("is a list row with its kind, headline, detail, a trail line last, and secondary controls", () => {
    const { container } = draw({ items: [proposal, ready, match, tracked, staged] });
    expect(rows(container)).toHaveLength(4);
    const byKind = (kind: string) => rows(container).find((row) => row.getAttribute("data-kind") === kind) as HTMLElement;
    const resume = byKind("resume_ready");
    expect(words(query(resume, ".bj-today-kind"))).toBe("Resume ready");
    expect(words(query(resume, ".bj-today-title"))).toBe("📄 Resume ready for ML Engineer @ Orbit");
    expect(words(query(resume, ".bj-today-detail"))).toBe("ATS score 72/100");
    expect(words(query(resume, ".bj-trail"))).toBe("✦ Drafted by your model · on your key · Sep 22");
    // The trail is the row's last line, before the controls.
    const body = query(resume, ".bj-today-row-body");
    expect(body.lastElementChild?.className).toContain("bj-trail");
    expect(words(query(byKind("job_tracked"), ".bj-trail"))).toBe("○ Logged by code · no model involved · Sep 22");
    expect(words(query(byKind("stage_changed"), ".bj-trail"))).toBe("○ Logged by code · no model involved · Sep 21");
    expect(words(query(byKind("high_fit_job"), ".bj-trail"))).toBe('engine ✦ · Found by saved search "analytics engineer" · via registry · Sep 22');
  });

  it("offers each row only controls that are real for it, none of them cyan", () => {
    const { container } = draw({ items: [match, ready, tracked] });
    // `match` is lit (T4 comes after T3: the ready resume holds the band).
    const names = (kind: string) => [...(rows(container).find((row) => row.getAttribute("data-kind") === kind) as HTMLElement).querySelectorAll("a, button.bj-today-action")].map(words);
    expect(names("high_fit_job")).toEqual(["Track", "Dismiss"]);
    expect(names("job_tracked")).toEqual(["Open workspace", "Dismiss"]);
    for (const control of container.querySelectorAll("li.bj-today-row a, li.bj-today-row button")) {
      expect(control.className).not.toMatch(/primary/);
    }
  });

  it("marks a held-back resume with a red cross, a waiting one gold and a logged one with a muted circle", () => {
    const { container } = draw({ items: [held, ready, tracked], fits: {} });
    // held (T2) wins the band; ready and tracked are rows.
    const markers = rows(container).map((row) => [row.getAttribute("data-kind"), words(query(row, ".bj-today-marker")), query(row, ".bj-today-marker").className]);
    expect(markers).toEqual([
      ["resume_ready", "◆", "bj-today-marker bj-today-marker--gold"],
      ["job_tracked", "○", "bj-today-marker bj-today-marker--muted"],
    ]);
  });

  it("shows a busy control as aria-disabled, with its own words, and ignores a press", () => {
    const onItemAction = vi.fn();
    const { container } = draw({ items: [tracked], busy: { t1: "dismiss" } }, { onItemAction });
    const button = buttonNamed(container, "Dismissing...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.disabled).toBe(false);
    click(button);
    expect(onItemAction).not.toHaveBeenCalled();
  });

  it("opens its receipt, built from the item's own fields, in a button with aria-expanded", () => {
    const { container } = draw({ items: [tracked] });
    const trigger = query<HTMLButtonElement>(container, "li.bj-today-row button.bj-trail-trigger");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const receipt = query(container, ".bj-receipt");
    expect(words(receipt)).toContain("kindjob_tracked");
    expect(words(receipt)).toContain("logged2026-09-22T10:00:00Z");
  });
});

describe("what a failed action looks like", () => {
  it("is a callout under that item with its own Retry, and leaves the band and the other rows alone", () => {
    const onRetryProblem = vi.fn();
    const { container } = draw(
      { items: [ready, tracked], problems: { t1: { kind: "error", message: "Request failed (500)" } } },
      { onRetryProblem },
    );
    const row = rows(container).find((candidate) => candidate.getAttribute("data-kind") === "job_tracked") as HTMLElement;
    const callout = query(row, '.bj-state--failure[role="alert"]');
    expect(words(callout)).toContain("Request failed (500)");
    click(buttonNamed(callout, "Retry"));
    expect(onRetryProblem).toHaveBeenCalledWith("t1");
    expect(band(container).className).toContain("bj-band--lit");
    expect(container.querySelectorAll(".bj-state--failure")).toHaveLength(1);
  });

  it("shows the failure of the lit item inside the band", () => {
    const { container } = draw({ items: [proposal], problems: { p1: { kind: "error", message: "Failed to accept" } } });
    expect(words(query(band(container), ".bj-state--failure"))).toContain("Failed to accept");
  });

  it("names what is missing, with a link, when generating needs a profile or a key", () => {
    const notice = { message: "Add a model key first.", linkTo: "/profile/integrations", linkLabel: "Add a model key in Integrations" };
    const { container } = draw({ items: [held], problems: { f1: { kind: "setup", notice } } });
    const setup = query(band(container), ".bj-state--setup");
    expect(words(setup)).toContain("Add a model key first.");
    expect(query(setup, "a").getAttribute("href")).toBe("/profile/integrations");
    expect(words(query(setup, "a"))).toBe("Add a model key in Integrations");
  });

  it("falls back to the settings page, so a setup failure always has a link", () => {
    const notice = { message: "Something needs to be set up.", linkTo: null, linkLabel: null };
    const { container } = draw({ items: [held], problems: { f1: { kind: "setup", notice } } });
    expect(query(band(container), ".bj-state--setup a").getAttribute("href")).toBe(SETTINGS_PATH);
  });
});

describe("empty", () => {
  it("teaches what will appear, by its real kinds and what each needs, with no example row", () => {
    const { container } = draw({ items: [], searches: ok([]) });
    const empty = query(container, ".bj-state--empty");
    expect(words(query(empty, "h2"))).toBe("Nothing here yet");
    const kinds = [...empty.querySelectorAll("li")].map(words);
    expect(kinds).toEqual([
      "High fit job -- needs a saved search in Discover",
      "Status proposal -- needs Gmail connected",
      "Resume ready",
      "Resume not generated",
      "Job tracked",
      "Stage changed",
    ]);
    expect(container.querySelector("li.bj-today-row")).toBeNull();
    // And the band says the next step: T5a, a Discover link as the only cyan.
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Save a search in Discover so Strong matches can land here.");
    expect(container.querySelectorAll(CYAN_FILLS)).toHaveLength(1);
  });

  it("is unlit, with no cyan on the page, when there are saved searches and nothing in the feed", () => {
    const { container } = draw({ items: [] });
    expect(band(container).className).toContain("bj-band--unlit");
    expect(words(query(band(container), ".bj-band-why"))).toBe("why: 0 items need a decision · 1 saved search active");
    expect(container.querySelector(CYAN_FILLS)).toBeNull();
    // The sentence takes focus for the skip link, since there is no button.
    expect(query(band(container), ".bj-band-sentence").getAttribute("tabindex")).toBe("-1");
  });
});

describe("partial", () => {
  it("names what could not be read, and still draws what arrived", () => {
    const { container } = draw({ items: [tracked], applications: failed, notes: { applicationsFailed: true } });
    const partial = query(container, ".bj-state--partial");
    expect(words(partial)).toBe("Some of this page couldn't be read:Your applications couldn't be read, so these items appear without their job names.");
    expect(rows(container)).toHaveLength(1);
  });

  it("says nothing about names when no item has an application to name", () => {
    const { container } = draw({ items: [match], applications: failed, notes: { applicationsFailed: true } });
    expect(container.querySelector(".bj-state--partial")).toBeNull();
  });

  it("names a sentence without a job when the lookup failed, not with a guessed one", () => {
    const { container } = draw({ items: [ready], applications: failed });
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Your resume is ready for review.");
  });

  it("names a saved-searches read that failed, in the background trail, with its own Retry", () => {
    const onRetryAccount = vi.fn();
    const { container } = draw({ items: [], searches: failed, wide: true }, { onRetryAccount });
    const callout = query(container, ".bj-today-margin .bj-state--failure");
    expect(words(callout)).toContain("Your saved searches couldn't be read.");
    click(buttonNamed(callout, "Retry"));
    expect(onRetryAccount).toHaveBeenCalledTimes(1);
    // The empty feed's band still says what it knows, without the count it could not read.
    expect(words(query(band(container), ".bj-band-why"))).toBe("why: 0 items need a decision");
  });
});

describe("stale", () => {
  it("says a saved search has not been checked yet, rather than a time it never had", () => {
    const never = ok([{ id: "ss-1", query: "ml", location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-01T00:00:00Z" }]);
    const { container } = draw({ items: [], searches: never, wide: true });
    expect(words(query(container, ".bj-today-margin .bj-state--stale"))).toBe("These haven't been checked yet.");
    expect(words(query(container, ".bj-today-margin .bj-trail"))).toContain("not checked yet");
  });

  it("carries the real time of the last check in the trail, from a returned field", () => {
    const { container } = draw({ items: [], wide: true });
    expect(words(query(container, ".bj-today-margin .bj-trail"))).toBe("✦ 1 saved search active · scored by your model on your key · checked 06:00");
    expect(container.querySelector(".bj-today-margin .bj-state--stale")).toBeNull();
  });
});

describe("setup required", () => {
  it("names what is missing, each with a link to where it is fixed", () => {
    const profile = { state: "ready", value: { active: false, importWaiting: false } } as const;
    const { container } = draw({ items: [], profile, notes: { profileMissing: true, modelKeyMissing: true } });
    const setup = query(container, ".bj-state--setup");
    expect(words(setup)).toBe("Resumes, match scores and reply reading all run on your profile and your own model key.Missing:Add your profileAdd a model key");
    expect([...setup.querySelectorAll("a")].map((a) => [words(a), a.getAttribute("href")])).toEqual([
      ["Add your profile", "/profile"],
      ["Add a model key", SETTINGS_PATH],
    ]);
    // The band carries the same fix (G2).
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("Everything here starts from your profile. Import it first.");
  });

  it("names only the key when the profile is there", () => {
    const { container } = draw({ items: [], notes: { modelKeyMissing: true } });
    expect([...query(container, ".bj-state--setup").querySelectorAll("a")].map(words)).toEqual(["Add a model key"]);
  });

  it("draws no setup block when nothing is known to be missing", () => {
    const { container } = draw({ items: [] });
    expect(container.querySelector(".bj-state--setup")).toBeNull();
  });
});

describe("failure", () => {
  it("is carried by the band (a red cross, Try again, the failed call and the time) and the body names it with no Retry of its own", () => {
    const onBandAction = vi.fn();
    const { container } = draw({ feedFailure: "Request failed (500)" }, { onBandAction });
    const section = band(container);
    expect(words(query(section, ".bj-band-sentence"))).toBe("✕Couldn't load Today. You didn't do anything wrong, and nothing was lost.");
    expect(words(query(section, ".bj-band-why"))).toBe("why: GET /today failed · 09:14");
    click(buttonNamed(section, "Try again"));
    expect(onBandAction).toHaveBeenCalledWith("retry");
    const callout = query(container, '.bj-today-main .bj-state--failure[role="alert"]');
    expect(words(callout)).toBe("✕Today's items couldn't be read: Request failed (500)");
    expect(callout.querySelector("button")).toBeNull();
    expect(container.querySelector("li.bj-today-row")).toBeNull();
  });

  it("names a setup read that failed in the body, and leaves Try again to the band", () => {
    const profile = { state: "failed", call: "GET /profile/current", at: "09:14" } as const;
    const { container } = draw({ items: [tracked], profile, notes: { setupFailed: true } });
    expect(words(query(band(container), ".bj-band-sentence"))).toBe("✕Couldn't check your setup. You didn't do anything wrong, and nothing was lost.");
    expect(words(query(band(container), ".bj-band-why"))).toBe("why: GET /profile/current failed · 09:14");
    expect(words(query(container, ".bj-today-main .bj-state--failure"))).toContain("Your profile or your credentials couldn't be read");
    expect(container.querySelector(".bj-today-main .bj-state--failure button")).toBeNull();
  });
});

describe("the margin and the inline trail", () => {
  it("is a right margin at 1280px and up: a labelled aside with the lines, beside the feed", () => {
    const { container } = draw({ items: [tracked], wide: true });
    const grid = query(container, ".bj-today-grid");
    expect(grid.children[0].className).toContain("bj-today-main");
    const aside = query(container, "aside.bj-today-margin");
    expect(aside.parentElement).toBe(grid);
    expect(words(query(aside, "h2"))).toBe("✦ In the background");
    expect(aside.getAttribute("aria-labelledby")).toBe(query(aside, "h2").id);
    expect(container.querySelector("button.bj-bg-toggle")).toBeNull();
  });

  it("is one disclosure line under the band below 1280px, closed until it is pressed", () => {
    const { container } = draw({ items: [tracked], wide: false });
    expect(container.querySelector("aside, .bj-today-grid")).toBeNull();
    const toggle = query<HTMLButtonElement>(container, "button.bj-bg-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(words(toggle)).toBe("✦ In the background · 1 saved search active · checked 06:00›");
    // It sits between the band and the feed.
    expect(band(container).nextElementSibling).toBe(toggle.parentElement);
    expect(container.querySelector(".bj-bg-body")).toBeNull();
    click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-controls")).toBe(query(container, ".bj-bg-body").id);
    expect(words(query(container, ".bj-bg-body .bj-trail"))).toBe("✦ 1 saved search active · scored by your model on your key · checked 06:00");
  });

  it("adds Gmail to the line only when replies are really being read", () => {
    const connected = ok([{ service: "oauth", provider: "gmail", is_validated: true, scope: "https://www.googleapis.com/auth/gmail.readonly" }]);
    const { container } = draw({ items: [], credentials: connected, wide: false });
    expect(words(query(container, "button.bj-bg-toggle"))).toContain("Gmail replies read on your key");
    click(query(container, "button.bj-bg-toggle"));
    expect(words(query(container, ".bj-bg-body"))).toContain("✦ Gmail replies read by your model · on your key");
  });

  it("says Not connected, in words with no glyph, and does not say replies are read", () => {
    const { container } = draw({ items: [], wide: true });
    const line = [...container.querySelectorAll(".bj-today-margin .bj-bg-line")].map(words).find((text) => text.startsWith("Gmail replies"));
    expect(line).toBe("Gmail replies · Not connected");
    expect(words(query(container, ".bj-today-margin"))).not.toContain("Gmail replies read");
  });

  it("asks for a reconnect when Gmail's access ended, in gold, with a link to the settings page", () => {
    const ended = ok([{ service: "oauth", provider: "gmail", is_validated: false, scope: "https://www.googleapis.com/auth/gmail.readonly" }]);
    const { container } = draw({ items: [], credentials: ended, wide: true });
    expect(words(query(container, ".bj-today-margin"))).toContain("◆ Gmail needs reconnecting · replies aren't being read");
    expect(query(container, '.bj-today-margin a[href="' + SETTINGS_PATH + '"]')).toBeTruthy();
  });

  it("says nothing runs yet, with a link to Discover, when there is no saved search", () => {
    const { container } = draw({ items: [], searches: ok([]), wide: true });
    const text = words(query(container, ".bj-today-margin"));
    expect(text).toContain("No saved searches, so nothing is being matched.");
    expect(query(container, '.bj-today-margin a[href="/discover"]')).toBeTruthy();
  });

  it("opens a saved-search receipt with each search's own status", () => {
    const { container } = draw({ items: [], wide: true });
    click(query(container, ".bj-today-margin button.bj-trail-trigger"));
    expect(words(query(container, ".bj-today-margin .bj-receipt"))).toBe('"analytics engineer"Checked 06:00');
  });
});

describe("the one cyan fill", () => {
  const scenarios: [string, Scenario, number][] = [
    ["a lit proposal", { items: [proposal, ready, match, held] }, 1],
    ["a held-back resume", { items: [held, ready] }, 1],
    ["an unlit band", { items: [tracked] }, 0],
    ["the empty feed with no saved search", { items: [], searches: ok([]) }, 1],
    ["loading", { items: null }, 0],
    ["a failed feed", { feedFailure: "x" }, 1],
    ["a missing profile", { items: [proposal], profile: { state: "ready", value: { active: false, importWaiting: false } } }, 1],
  ];
  for (const [name, scenario, fills] of scenarios) {
    it(`has ${fills} on ${name}, always the band's`, () => {
      const { container } = draw(scenario);
      const found = [...container.querySelectorAll(CYAN_FILLS)];
      expect(found).toHaveLength(fills);
      for (const fill of found) expect(band(container).contains(fill)).toBe(true);
    });
  }
});

describe("keyboard and accessibility", () => {
  it("makes the lit item's Dismiss, the band's button, the receipts and a row's controls real, focusable controls", () => {
    const { container } = draw({ items: [proposal, ready, tracked] });
    const controls = [...container.querySelectorAll("button, a[href]")];
    expect(controls.length).toBeGreaterThan(5);
    for (const control of controls) {
      expect(control.getAttribute("tabindex")).not.toBe("-1");
      expect(control.hasAttribute("disabled")).toBe(false);
    }
    const dismiss = buttonNamed(band(container), "Dismiss");
    dismiss.focus();
    expect(document.activeElement).toBe(dismiss);
    expect(dismiss.type).toBe("button");
  });

  it("keeps tab order in reading order: the band, then the feed, with the background line before the feed on a narrow screen", () => {
    const { container } = draw({ items: [proposal, tracked] });
    const order = [...container.querySelectorAll("button, a[href]")];
    const indexOf = (element: Element) => order.indexOf(element);
    expect(indexOf(buttonNamed(band(container), "Accept: move to Interviewing"))).toBeLessThan(indexOf(query(container, "button.bj-bg-toggle")));
    expect(indexOf(query(container, "button.bj-bg-toggle"))).toBeLessThan(indexOf(query(container, "li.bj-today-row a")));
  });

  const states: [string, Scenario][] = [
    ["loading", { items: null }],
    ["a lit proposal", { items: [proposal, ready, match, tracked, staged] }],
    ["a held-back resume", { items: [held, ready], fits: { "app-1": { state: "held", score: 3.6 } } }],
    ["empty", { items: [], searches: ok([]) }],
    ["a failed feed", { feedFailure: "Request failed (500)" }],
    ["a failed action", { items: [tracked], problems: { t1: { kind: "error", message: "Failed" } } }],
    ["setup required", { items: [], notes: { profileMissing: true, modelKeyMissing: true }, profile: { state: "ready", value: { active: false, importWaiting: false } } }],
    ["partial", { items: [tracked], applications: failed, notes: { applicationsFailed: true } }],
    ["wide, with the margin", { items: [proposal, tracked], wide: true }],
  ];
  for (const [name, scenario] of states) {
    it(`has no serious accessibility violation: ${name}`, async () => {
      const { container } = draw(scenario);
      await expectNoSeriousA11yViolations(container);
    });
  }

  it("has no heading out of order: one h1, then h2s", () => {
    const { container } = draw({ items: [proposal, tracked], wide: true });
    const levels = [...container.querySelectorAll("h1, h2, h3, h4")].map((heading) => heading.tagName);
    expect(levels[0]).toBe("H1");
    expect(levels.filter((level) => level === "H1")).toHaveLength(1);
    expect(levels.every((level) => level === "H1" || level === "H2")).toBe(true);
  });
});
