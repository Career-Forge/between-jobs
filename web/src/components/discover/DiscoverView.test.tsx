// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { job, response, score } from "../../testing/discoverFixtures";
import { buttonNamed, click, installViewport, query, render } from "../../testing/render";
import { EMPTY_REQUEST, resultsFactOf } from "../../lib/discoverFacts";
import { findForbiddenWord } from "../../lib/trail";
import { nextStep, type NextStepInput, type NextStepOutcome } from "../../lib/nextStep";
import type { DiscoverFacts } from "../../lib/nextStep/pages/discover";
import type { SavedSearchesState } from "../../lib/savedSearches";
import { DiscoverView, SETUP_BLOCKED_NOTE, type DiscoverViewProps, type SearchView } from "./DiscoverView";

// Every state of the Discover page, drawn from props: what each shows, what it leaves out, and that
// whatever the state, the page holds at most one cyan fill and it is the band's. The reading and the
// requests are pages/Discover.tsx (Discover.test.tsx).

const NOW = new Date("2026-09-22T12:00:00Z");
const READY = { state: "ready", value: { active: true, importWaiting: false } } as const;
const CREDS = { state: "ready", value: {} } as const;

function band(facts: Partial<DiscoverFacts>, extra: Partial<NextStepInput> = {}): NextStepOutcome {
  return nextStep({
    page: "discover",
    signedIn: true,
    profile: READY,
    credentials: CREDS,
    facts: { phase: "idle", key: "ready", searchProviders: 2, results: null, ...facts },
    ...extra,
  } as NextStepInput);
}

const noSaved: SavedSearchesState = { kind: "ready", searches: [], busyId: null, error: null };

function props(overrides: Partial<DiscoverViewProps> = {}): DiscoverViewProps {
  return {
    now: NOW,
    outcome: band({}),
    onBandAction: vi.fn(),
    announcement: "",
    values: EMPTY_REQUEST,
    onValuesChange: vi.fn(),
    onSearch: vi.fn(),
    onSave: vi.fn(),
    saving: false,
    search: { kind: "idle" },
    setup: null,
    tracking: new Set(),
    tracked: {},
    trackErrors: {},
    onTrack: vi.fn(),
    saved: { state: noSaved, actions: { toggle: vi.fn(), remove: vi.fn(), retry: vi.fn() }, createError: null },
    timeZone: "UTC",
    ...overrides,
  };
}

function draw(overrides: Partial<DiscoverViewProps> = {}) {
  installViewport(1000);
  return render(
    <MemoryRouter>
      <DiscoverView {...props(overrides)} />
    </MemoryRouter>,
  );
}

const words = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();
const bandOf = (container: HTMLElement) => query(container, 'section[aria-label="Next step"]');
const cyanFills = (container: HTMLElement) => container.querySelectorAll("button.bj-primary, a.bj-button-link-primary").length;

function ready(result = response(), request = EMPTY_REQUEST) {
  const search: SearchView = { kind: "ready", result };
  return { search, outcome: band({ phase: "results", results: resultsFactOf(result, request) }) };
}

describe("the header and the band", () => {
  it("has one page heading, Discover, and names where the page's jobs come from", () => {
    const { container } = draw();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(words(query(container, "h1"))).toBe("Discover");
    expect(words(query(container, ".bj-page-subtitle"))).toContain("Company job boards and your search providers");
  });

  it("draws the band directly under the header, ahead of the form", () => {
    const { container } = draw();
    const order = [...container.querySelectorAll("h1, section[aria-label='Next step'], form")].map((element) => element.tagName);
    expect(order).toEqual(["H1", "SECTION", "FORM"]);
  });

  it("passes a band action to the page by its intent, and the band's Search submits the form from outside", () => {
    const onBandAction = vi.fn();
    const { container } = draw({ onBandAction });
    const search = query<HTMLButtonElement>(bandOf(container), "button.bj-primary");
    expect(words(search)).toBe("Search");
    expect(search.getAttribute("form")).toBe("discover-form");
    expect(search.getAttribute("type")).toBe("submit");
  });
});

describe("idle: nothing has been searched", () => {
  it("says what will appear here and that nothing runs until a search is asked for, with no example rows", () => {
    const { container } = draw();
    const body = words(container);
    expect(body).toContain("Find a job");
    expect(body).toContain("Nothing is searched until you ask");
    expect(container.querySelectorAll("li.bj-disc-row")).toHaveLength(0);
  });

  it("is lit by one cyan Search, the band's, and the form's own Search is outlined", () => {
    const { container } = draw();
    expect(cyanFills(container)).toBe(1);
    expect(query(container, "form").querySelector("button.bj-primary")).toBeNull();
    expect(words(buttonNamed(query(container, "form"), "Search"))).toBe("Search");
  });

  it("has the filter fields, remote only and Save this search, each labelled", () => {
    const { container } = draw();
    const form = query(container, "form");
    expect(form.getAttribute("aria-label")).toBe("Search filters");
    expect(words(form)).toContain("Search");
    expect(words(form)).toContain("Location");
    expect(words(form)).toContain("Companies");
    expect(words(form)).toContain("Remote only");
    expect(form.querySelectorAll("input[type='text']")).toHaveLength(3);
    expect(form.querySelector("input[type='checkbox']")).not.toBeNull();
    expect(words(buttonNamed(form, "Save this search"))).toBe("Save this search");
  });

  it("shows plain examples in the companies field and rotating role suggestions in the search box", () => {
    const { container } = draw();
    const inputs = [...query(container, "form").querySelectorAll<HTMLInputElement>("input[type='text']")];
    expect(inputs[0].placeholder).toBe("e.g. Data Analyst");
    expect(inputs[1].placeholder).toBe("e.g. New York, NY");
    expect(inputs[2].placeholder).toBe("e.g. Anthropic, Stripe");
  });
});

describe("the role suggestions", () => {
  it("change every few seconds while the box is empty, and stop for a person who prefers reduced motion", () => {
    vi.useFakeTimers();
    try {
      installViewport(1000);
      window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;
      const { container } = render(
        <MemoryRouter>
          <DiscoverView {...props()} />
        </MemoryRouter>,
      );
      const box = () => query<HTMLInputElement>(container, "form input[type='text']");
      expect(box().placeholder).toBe("e.g. Data Analyst");
      act(() => void vi.advanceTimersByTime(4000));
      expect(box().placeholder).toBe("e.g. Data Engineer");
      act(() => void vi.advanceTimersByTime(4000));
      expect(box().placeholder).toBe("e.g. Software Development Engineer");
    } finally {
      vi.useRealTimers();
    }
  });

  it("do not change when the person prefers reduced motion", () => {
    vi.useFakeTimers();
    try {
      window.matchMedia = ((query: string) => ({ matches: query.includes("reduce"), media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;
      const { container } = render(
        <MemoryRouter>
          <DiscoverView {...props()} />
        </MemoryRouter>,
      );
      act(() => void vi.advanceTimersByTime(20000));
      expect(query<HTMLInputElement>(container, "form input[type='text']").placeholder).toBe("e.g. Data Analyst");
    } finally {
      vi.useRealTimers();
    }
  });

  it("stop once the box holds something", () => {
    vi.useFakeTimers();
    try {
      window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;
      const { container } = render(
        <MemoryRouter>
          <DiscoverView {...props({ values: { ...EMPTY_REQUEST, q: "analyst" } })} />
        </MemoryRouter>,
      );
      act(() => void vi.advanceTimersByTime(20000));
      expect(query<HTMLInputElement>(container, "form input[type='text']").placeholder).toBe("e.g. Data Analyst");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("loading: a search is running", () => {
  const loading = () => ({ search: { kind: "loading", startedAt: NOW.getTime() } as SearchView, outcome: band({ phase: "searching" }) });

  it("draws static placeholder rows, and the form stays usable", () => {
    const { container } = draw(loading());
    expect(container.querySelectorAll(".bj-skeleton-row")).toHaveLength(3);
    expect(words(query(container, ".bj-state--loading"))).toBe("Searching...");
    expect(container.querySelectorAll("form input").length).toBeGreaterThan(0);
    expect(query(container, "form input[type='text']").hasAttribute("disabled")).toBe(false);
  });

  it("says Searching... in the band with the honest sentence about link checks, and no button", () => {
    const { container } = draw(loading());
    expect(words(bandOf(container))).toContain("Searching...");
    expect(words(bandOf(container))).toContain("why: link checks can take about 20 seconds");
    expect(bandOf(container).querySelector("button, a")).toBeNull();
    expect(cyanFills(container)).toBe(0);
  });

  it("shows a live elapsed counter as role=timer with its live region off, ticking each second", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(NOW);
      installViewport(1000);
      const { container } = render(
        <MemoryRouter>
          <DiscoverView {...props({ ...loading(), search: { kind: "loading", startedAt: Date.now() } })} />
        </MemoryRouter>,
      );
      const timer = () => query(container, "[role='timer']");
      expect(timer().getAttribute("aria-live")).toBe("off");
      expect(words(timer())).toBe("0s");
      act(() => void vi.advanceTimersByTime(7000));
      expect(words(timer())).toBe("7s");
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads Searching... on the form's Search, aria-disabled and not disabled, and shows no trail", () => {
    const { container } = draw(loading());
    const form = query(container, "form");
    const search = buttonNamed(form, "Searching...");
    expect(search.getAttribute("aria-disabled")).toBe("true");
    expect(search.hasAttribute("disabled")).toBe(false);
    expect(container.querySelector(".bj-trail")).toBeNull();
  });

  it("does not search again from the form while one is running", () => {
    const onSearch = vi.fn();
    const { container } = draw({ ...loading(), onSearch });
    act(() => query<HTMLFormElement>(container, "form").requestSubmit());
    expect(onSearch).not.toHaveBeenCalled();
  });
});

describe("results", () => {
  it("is unlit with 'Track the jobs worth a draft.', the counts in the why line, and no cyan fill on the page", () => {
    const { container } = draw(ready(response({ scored: [job()], more: [job({ apply_url: "https://jobs.example/acme/2", score: null })], dead_removed: 3 })));
    const text = words(bandOf(container));
    expect(text).toContain("Track the jobs worth a draft.");
    expect(text).toContain("why: 1 scored · 1 more, unscored · 3 dropped -- no longer live · serper 2");
    expect(cyanFills(container)).toBe(0);
  });

  it("turns the form's submit into 'Search again', outlined", () => {
    const { container } = draw(ready());
    const submit = buttonNamed(query(container, "form"), "Search again");
    expect(submit.classList.contains("bj-primary")).toBe(false);
  });

  it("lists scored rows under Results and rows past the batch under More results, as lists", () => {
    const { container } = draw(ready(response({ scored: [job()], more: [job({ apply_url: "https://jobs.example/acme/2", title: "Analyst", score: null })] })));
    const headings = [...container.querySelectorAll("h2")].map((heading) => words(heading));
    expect(headings).toEqual(expect.arrayContaining(["Results", "More results"]));
    expect(container.querySelectorAll("ul.bj-disc-list")).toHaveLength(2);
    expect(container.querySelectorAll("li.bj-disc-row")).toHaveLength(2);
  });

  it("shows no 'More results' heading when every row was scored", () => {
    const { container } = draw(ready());
    expect([...container.querySelectorAll("h2")].map((heading) => words(heading))).not.toContain("More results");
  });
});

describe("a row's fit block", () => {
  it("draws the ladder with the bin and number, engine mark, the one line and a bar for each applied part only", () => {
    const { container } = draw(ready());
    const fit = query(container, ".bj-fit");
    expect(query(fit, "[role='img']").getAttribute("aria-label")).toBe("Fit: Good -- 61 / 100");
    expect(fit.querySelectorAll(".bj-ladder-cell--filled")).toHaveLength(3);
    expect(words(fit)).toContain("engine");
    expect(words(fit)).toContain("Strong overlap on data pipelines.");
    const meters = [...fit.querySelectorAll("[role='meter']")].map((meter) => meter.getAttribute("aria-valuetext"));
    expect(meters).toEqual(["78 / 100", "52 / 100", "60 / 100", "100 / 100"]);
    const labels = [...fit.querySelectorAll(".bj-segbar-label")].map((label) => words(label));
    expect(labels).toEqual(["Skills", "Experience", "Work Authorization", "Location"]);
  });

  it("names what was not applied and the lowest part, and never draws a dropped part as 0", () => {
    const { container } = draw(ready());
    const fit = words(query(container, ".bj-fit-notes"));
    expect(fit).toContain("Not applied: Company Health -- company isn't in the tier list");
    expect(fit).toContain("Not applied: Compensation -- no salary listed");
    expect(fit).toContain("Lowest part: Experience");
    expect(words(query(container, ".bj-fit-bars"))).not.toContain("Company Health");
    expect(words(query(container, ".bj-fit-bars"))).not.toMatch(/(^|\D)0 \/ 100/);
  });

  it("says what Compensation is when it counts: a constant, not compared to expectations", () => {
    const priced = job({ salary_min: 120000, salary_max: 150000, salary_currency: "USD", score: score({ inapplicable_dims: ["company_health"] }) });
    const { container } = draw(ready(response({ scored: [priced] })));
    expect(words(query(container, ".bj-fit-bars"))).toContain("Salary listed; not compared to your expectations");
    expect(words(container)).toContain("USD 120,000-150,000");
  });

  it("says the location in three states, with where the model read the job to be", () => {
    for (const [match, expected] of [
      ["match", "Location matches -- the model read it as Austin, TX"],
      ["mismatch", "Location doesn't match -- the model read it as Austin, TX"],
      ["unknown", "Location: can't tell -- the model read it as Austin, TX"],
    ] as const) {
      const { container, unmount } = draw(ready(response({ scored: [job({ score: score({ location_match: match, detected_location: "Austin, TX" }) })] })));
      expect(words(query(container, ".bj-fit-notes"))).toContain(expected);
      unmount();
    }
  });

  it("leaves the lowest-part line out when none was named, and a dropped-part line out when none was dropped", () => {
    const whole = job({ score: score({ bottleneck: null, inapplicable_dims: [] }) });
    const { container } = draw(ready(response({ scored: [whole] })));
    const notes = words(query(container, ".bj-fit-notes"));
    expect(notes).not.toContain("Lowest part");
    expect(notes).not.toContain("Not applied");
    expect(container.querySelectorAll("[role='meter']")).toHaveLength(6);
  });

  it("says a sponsorship signal only as a text match, with the tag", () => {
    const yes = job({ sponsorship_signal: "explicit_yes" });
    const no = job({ sponsorship_signal: "explicit_no", apply_url: "https://jobs.example/acme/2" });
    const { container } = draw(ready(response({ scored: [yes, no] })));
    const lines = [...container.querySelectorAll(".bj-disc-signal")].map((line) => words(line));
    expect(lines).toEqual([
      "The posting text mentions visa sponsorship ○ text match, no model",
      "The posting text rules out visa sponsorship ○ text match, no model",
    ]);
  });

  it("says nothing of sponsorship in the row when the text said nothing", () => {
    const { container } = draw(ready());
    expect(container.querySelector(".bj-disc-signal")).toBeNull();
  });

  it("gives a row past the scoring batch no score block at all, only 'Not scored'", () => {
    const { container } = draw(ready(response({ scored: [], more: [job({ score: null })] })));
    expect(container.querySelector(".bj-ladder")).toBeNull();
    expect(container.querySelector("[role='meter']")).toBeNull();
    expect(words(query(container, ".bj-fit"))).toBe("Not scored");
  });

  it("draws a placeholder, no rung and no number, for every row when the scoring fell back, with one gold notice above all", () => {
    const fallback = response({ scoring_strategy: "neutral_fallback", scored: [job(), job({ apply_url: "https://jobs.example/acme/2" })] });
    const { container } = draw({ ...ready(fallback), outcome: band({ phase: "results", results: resultsFactOf(fallback, EMPTY_REQUEST) }) });
    const ladders = [...container.querySelectorAll(".bj-ladder")];
    expect(ladders).toHaveLength(2);
    for (const ladder of ladders) {
      expect(ladder.querySelectorAll(".bj-ladder-cell--filled")).toHaveLength(0);
      expect(query(ladder, "[role='img']").getAttribute("aria-label")).toBe("Fit: Placeholder");
      expect(words(ladder)).toBe("Placeholder");
    }
    expect(container.querySelector("[role='meter']")).toBeNull();
    expect(words(container)).not.toContain("61 / 100");
    expect(container.querySelectorAll(".bj-disc-fallback")).toHaveLength(1);
    expect(words(query(container, ".bj-disc-fallback"))).toContain("these scores are placeholders");
  });

  it("lights 'Search again' as the page's one cyan, in the band, when the scoring fell back", () => {
    const fallback = response({ scoring_strategy: "neutral_fallback" });
    const { container } = draw({ ...ready(fallback), outcome: band({ phase: "results", results: resultsFactOf(fallback, EMPTY_REQUEST) }) });
    expect(cyanFills(container)).toBe(1);
    const lit = query<HTMLButtonElement>(bandOf(container), "button.bj-primary");
    expect(words(lit)).toBe("Search again");
    expect(lit.getAttribute("form")).toBe("discover-form");
  });
});

describe("a row's trail, built from returned fields only", () => {
  const trails = (container: HTMLElement) => [...container.querySelectorAll("li.bj-disc-row .bj-trail-trigger")].map((trail) => words(trail));

  it("says a registry row came from the registry, on the company's job board at the last poll, and never that its link was verified", () => {
    const { container } = draw(ready(response({ scored: [job({ provider: "registry" })] })));
    expect(trails(container)).toEqual(["○ via registry · on the company's job board at last poll · Posted Sep 20"]);
  });

  it("does not say a registry row the API calls stale was on the company's board at the last poll", () => {
    const { container } = draw(ready(response({ scored: [job({ provider: "registry", link_checked: false })] })));
    expect(trails(container)).toEqual(["○ via registry · not confirmed on the company's board recently · Posted Sep 20"]);
  });

  it("words a USAJobs sponsorship as the provider's category fact, with no text-match tag", () => {
    const { container } = draw(ready(response({ scored: [job({ provider: "usajobs", sponsorship_signal: "explicit_no", snippet: "Federal analyst role." })] })));
    const signal = words(query(container, ".bj-disc-signal"));
    expect(signal).toContain("Federal roles generally require US work authorization");
    expect(signal).toContain("by provider, no text read");
    expect(signal).not.toContain("text match");
    expect(signal).not.toContain("rules out");
  });

  it("says a kept live-search row had its link checked, with a green mark, and by whom it was found", () => {
    const { container } = draw(ready());
    expect(trails(container)).toEqual(["✓ Link checked · via serper · Posted Sep 20"]);
    expect(query(container, ".bj-trail-glyph--emerald")).not.toBeNull();
  });

  it("says a row that never reached the check was not checked yet, in the muted code glyph", () => {
    const { container } = draw(ready(response({ scored: [job({ link_checked: false })] })));
    expect(trails(container)).toEqual(["○ Link not checked yet · via serper · Posted Sep 20"]);
  });

  it("leaves out the date, and its separator, when the posting returned none", () => {
    const { container } = draw(ready(response({ scored: [job({ posted_at: null })] })));
    expect(trails(container)).toEqual(["✓ Link checked · via serper"]);
  });

  it("is the last element of the row, inline", () => {
    const { container } = draw(ready());
    const row = query(container, "li.bj-disc-row");
    expect(row.lastElementChild?.classList.contains("bj-disc-trail")).toBe(true);
  });

  it("never says 'verified' anywhere on a page of results, in any row shape", () => {
    const rows = [job(), job({ provider: "registry", apply_url: "https://jobs.example/r" }), job({ link_checked: false, apply_url: "https://jobs.example/n" })];
    const { container } = draw(ready(response({ scored: rows })));
    expect(findForbiddenWord(words(container))).toBeNull();
    expect(words(container).toLowerCase()).not.toContain("verified");
  });

  it("opens a receipt with the fields as returned: provider, source in words, link check, location, sponsorship", () => {
    const { container } = draw(ready());
    click(query(container, "li.bj-disc-row .bj-trail-trigger"));
    const receipt = Object.fromEntries([...container.querySelectorAll(".bj-receipt .bj-receipt-row")].map((row) => [words(query(row, "dt")), words(query(row, "dd"))]));
    expect(receipt.provider).toBe("serper");
    expect(receipt.source).toBe("tier 1 -- a company's job board");
    expect(receipt["link check"]).toContain("probed and not found closed");
    expect(receipt.location).toContain("New York, NY");
    expect(receipt.sponsorship).toBe("unknown (text match, no model)");
    expect(receipt.scoring).toBeUndefined();
  });

  it("names the neutral fallback in the receipt only when it happened", () => {
    const fallback = response({ scoring_strategy: "neutral_fallback" });
    const { container } = draw({ ...ready(fallback) });
    click(query(container, "li.bj-disc-row .bj-trail-trigger"));
    expect(words(query(container, ".bj-receipt"))).toContain("neutral_fallback");
  });
});

describe("a row's links and its Track action", () => {
  it("opens the posting in a new tab, with a name of its own, only for an http(s) address", () => {
    const rows = [job(), job({ apply_url: "javascript:alert(1)", title: "Hostile" }), job({ apply_url: "data:text/html,x", title: "Data" })];
    const { container } = draw(ready(response({ scored: rows })));
    const links = [...container.querySelectorAll<HTMLAnchorElement>("li.bj-disc-row a[target='_blank']")];
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://jobs.example/acme/1");
    expect(links[0].getAttribute("rel")).toBe("noopener noreferrer");
    expect(links[0].getAttribute("aria-label")).toBe("View posting for Data Engineer at Acme (opens in a new tab)");
    expect(container.querySelector("a[href^='javascript:']")).toBeNull();
  });

  it("names Track for its job, leads with the visible word, and calls the page with that job", () => {
    const onTrack = vi.fn();
    const { container } = draw({ ...ready(), onTrack });
    const track = buttonNamed(container, /^Track /);
    expect(track.getAttribute("aria-label")).toBe("Track Data Engineer at Acme");
    expect(words(track)).toBe("Track");
    click(track);
    expect(onTrack).toHaveBeenCalledTimes(1);
    expect(onTrack.mock.calls[0][0].apply_url).toBe("https://jobs.example/acme/1");
  });

  it("is outlined, never cyan, and a row's Track is not a form submit", () => {
    const { container } = draw(ready());
    const track = buttonNamed(container, /^Track /);
    expect(track.classList.contains("bj-primary")).toBe(false);
    expect(track.getAttribute("type")).toBe("button");
  });

  it("reads Tracking... aria-disabled while the request is out, keeps its focus and ignores presses", () => {
    const onTrack = vi.fn();
    const { container } = draw({ ...ready(), tracking: new Set(["https://jobs.example/acme/1"]), onTrack });
    const busy = buttonNamed(container, /^Tracking/);
    expect(words(busy)).toBe("Tracking...");
    expect(busy.getAttribute("aria-disabled")).toBe("true");
    expect(busy.hasAttribute("disabled")).toBe(false);
    click(busy);
    expect(onTrack).not.toHaveBeenCalled();
  });

  it("swaps Track for an Open workspace link to the application once it is tracked", () => {
    const { container } = draw({ ...ready(), tracked: { "https://jobs.example/acme/1": "app-9" } });
    const open = [...container.querySelectorAll<HTMLAnchorElement>("li.bj-disc-row a")].find((link) => words(link) === "Open workspace");
    expect(open?.getAttribute("href")).toBe("/applications/app-9");
    expect(container.querySelector("li.bj-disc-row button[aria-label^='Track']")).toBeNull();
  });

  it("shows a failed Track as a callout on that row only, with its own Retry that tracks again", () => {
    const onTrack = vi.fn();
    const rows = [job(), job({ apply_url: "https://jobs.example/acme/2", title: "Analyst" })];
    const { container } = draw({ ...ready(response({ scored: rows })), trackErrors: { "https://jobs.example/acme/2": "That didn't go through." }, onTrack });
    const rowsOut = container.querySelectorAll("li.bj-disc-row");
    expect(rowsOut[0].querySelector("[role='alert']")).toBeNull();
    const alert = query(rowsOut[1], "[role='alert']");
    expect(words(alert)).toContain("Couldn't track Analyst at Acme: That didn't go through.");
    click(buttonNamed(alert, "Retry"));
    expect(onTrack.mock.calls[0][0].title).toBe("Analyst");
  });

  it("shows the company as Unknown company when none came back, and 'Location unknown' for a location nothing gave", () => {
    const { container } = draw(ready(response({ scored: [job({ company: null, location: null, remote: null })] })));
    expect(words(query(container, ".bj-disc-meta"))).toBe("Unknown company -- Location unknown");
  });
});

describe("partial: what arrived renders, what did not is named", () => {
  it("names each warning on its own line, in its own words, with a red mark, above the rows", () => {
    const partial = response({ warnings: ["brave: request failed (429)", "The job registry could not be searched just now."] });
    const { container } = draw({ ...ready(partial), outcome: band({ phase: "results", results: resultsFactOf(partial, EMPTY_REQUEST) }) });
    const lines = [...container.querySelectorAll(".bj-disc-warnings li")].map((line) => words(line));
    expect(lines).toEqual(["✕ Brave Search couldn't complete that search · request failed (429)", "✕ The job registry could not be searched just now."]);
    expect(container.querySelectorAll(".bj-disc-warnings .bj-trail-glyph--danger")).toHaveLength(2);
    expect(container.querySelectorAll("li.bj-disc-row")).toHaveLength(1);
    const body = query(container, ".bj-disc-results");
    expect(body.querySelector(".bj-disc-warnings")!.compareDocumentPosition(body.querySelector("ul.bj-disc-list")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("puts the gap in the band's why line, and the band stays unlit", () => {
    const partial = response({ warnings: ["brave: down"] });
    const { container } = draw({ ...ready(partial), outcome: band({ phase: "results", results: resultsFactOf(partial, EMPTY_REQUEST) }) });
    expect(words(bandOf(container))).toContain("brave failed");
    expect(cyanFills(container)).toBe(0);
  });

  it("says how many listings were dropped as no longer live", () => {
    const { container } = draw(ready(response({ dead_removed: 3 })));
    expect(words(container)).toContain("3 dropped -- no longer live");
  });

  it("says nothing about dropped listings when none were", () => {
    const { container } = draw(ready());
    expect(words(container)).not.toContain("dropped");
  });
});

describe("empty: nothing matched", () => {
  const empty = (extra: Partial<ReturnType<typeof response>> = {}) => response({ scored: [], more: [], ...extra });

  it("says 'No results' with no example rows, and the band's step is Clear filters when a filter was set", () => {
    const request = { ...EMPTY_REQUEST, q: "zzz", companies: "Nowhere" };
    const result = empty();
    const { container } = draw({ search: { kind: "ready", result }, outcome: band({ phase: "results", results: resultsFactOf(result, request) }) });
    expect(words(query(container, ".bj-state--empty"))).toContain("No results");
    expect(words(query(container, ".bj-state--empty"))).toContain("Nothing matched this search.");
    expect(container.querySelectorAll("li.bj-disc-row")).toHaveLength(0);
    expect(words(bandOf(container))).toContain("No results. Widen your filters or clear Companies.");
    expect(words(query(bandOf(container), "button.bj-primary"))).toBe("Clear filters");
    expect(cyanFills(container)).toBe(1);
  });

  it("carries the warnings and the dropped count into the empty state", () => {
    const { container } = draw({ ...ready(empty({ warnings: ["serper: unexpected failure"], dead_removed: 4 })) });
    expect(words(container)).toContain("✕ Serper couldn't complete that search · unexpected failure");
    expect(words(container)).toContain("4 dropped -- no longer live");
  });

  it("does not claim nothing matched when listings were dropped or a provider failed, and never says 'these filters'", () => {
    for (const result of [empty({ dead_removed: 3 }), empty({ warnings: ["brave: request failed (429)"] })]) {
      const { container } = draw({ ...ready(result) });
      const body = words(query(container, ".bj-state--empty"));
      expect(body).toContain("No listings are left to show.");
      expect(body).not.toContain("Nothing matched");
      expect(body).not.toContain("these filters");
    }
  });

  it("is unlit with no filter to clear when none was set", () => {
    const result = empty();
    const { container } = draw({ search: { kind: "ready", result }, outcome: band({ phase: "results", results: resultsFactOf(result, EMPTY_REQUEST) }) });
    expect(words(bandOf(container))).toContain("No results, and no filters were set.");
    expect(cyanFills(container)).toBe(0);
  });
});

describe("setup-required: no search is made", () => {
  const missing = { message: "Discover scores jobs against your profile with your model.", missing: [{ label: "Add your profile", to: "/profile" }, { label: "Add a model key", to: "/settings#ai-provider" }] as const };

  it("names what is missing, each with a link to where it is fixed, instead of the search's body", () => {
    const { container } = draw({ setup: { message: missing.message, missing: [...missing.missing] as unknown as [typeof missing.missing[0], ...typeof missing.missing[number][]] } });
    const state = query(container, ".bj-state--setup");
    expect(words(state)).toContain("Discover scores jobs against your profile with your model.");
    const links = [...state.querySelectorAll("a")].map((link) => [words(link), link.getAttribute("href")]);
    expect(links).toEqual([
      ["Add your profile", "/profile"],
      ["Add a model key", "/settings#ai-provider"],
    ]);
    expect(container.querySelector(".bj-state--empty")).toBeNull();
  });

  it("says on the form that Search is waiting on the setup, and keeps it aria-disabled", () => {
    const { container } = draw({ setup: { message: "x", missing: [{ label: "Add a model key", to: "/settings#ai-provider" }] } });
    expect(words(query(container, "form"))).toContain(SETUP_BLOCKED_NOTE);
    const search = buttonNamed(query(container, "form"), "Search");
    expect(search.getAttribute("aria-describedby")).toBe("bj-discover-blocked");
  });

  it("is lit by the band's own link, which is the page's one cyan, and G2 wins when there is no profile", () => {
    const g2 = band({}, { profile: { state: "ready", value: { active: false, importWaiting: false } } });
    const { container } = draw({ outcome: g2, setup: { message: "x", missing: [{ label: "Add your profile", to: "/profile" }] } });
    expect(words(bandOf(container))).toContain("Everything here starts from your profile. Import it first.");
    expect(cyanFills(container)).toBe(1);
    const d1 = band({ key: "none" });
    const second = draw({ outcome: d1, setup: { message: "x", missing: [{ label: "Add a model key", to: "/settings#ai-provider" }] } });
    expect(words(bandOf(second.container))).toContain("Discover scores jobs with your model. Add a model key first.");
    expect(second.container.querySelectorAll("a.bj-button-link-primary")).toHaveLength(1);
  });
});

describe("failure: the page's own request failed", () => {
  const failed = () => ({
    search: { kind: "error", message: "The service is down." } as SearchView,
    outcome: band({}, { pageFailure: { what: "Couldn't finish the search", call: "GET /discover", at: "09:14", intent: "retry" } }),
  });

  it("carries the failure in the band: a red mark, the plain sentence, Try again, and the call and the time", () => {
    const { container } = draw(failed());
    const text = words(bandOf(container));
    expect(text).toContain("Couldn't finish the search. You didn't do anything wrong, and nothing was lost.");
    expect(text).toContain("why: GET /discover failed · 09:14");
    expect(bandOf(container).querySelector(".bj-band-glyph")?.textContent).toBe("✕");
    expect(words(query(bandOf(container), "button.bj-primary"))).toBe("Try again");
  });

  it("shows a danger callout naming what could not be read, with no Retry of its own", () => {
    const { container } = draw(failed());
    const callout = query(container, ".bj-state--failure");
    expect(callout.getAttribute("role")).toBe("alert");
    expect(words(callout)).toBe("✕The search couldn't be read: The service is down.");
    expect(callout.querySelector("button")).toBeNull();
    expect(container.querySelector("li.bj-disc-row")).toBeNull();
  });

  it("keeps the form usable, and Search again outlined: the band holds the one cyan", () => {
    const { container } = draw(failed());
    expect(cyanFills(container)).toBe(1);
    expect(query(container, "form").querySelector("button.bj-primary")).toBeNull();
    expect(words(buttonNamed(query(container, "form"), "Search again"))).toBe("Search again");
  });
});

describe("saved searches on the page", () => {
  const search = (overrides = {}) => ({
    id: "s-1",
    query: "data analyst",
    location: null,
    companies: [],
    remote_only: false,
    is_active: true,
    created_at: "2026-09-01T00:00:00Z",
    last_matched_at: null,
    ...overrides,
  });
  const withSearches = (searches: ReturnType<typeof search>[], extra: Partial<Extract<SavedSearchesState, { kind: "ready" }>> = {}, createError: string | null = null) => ({
    saved: { state: { kind: "ready", searches, busyId: null, error: null, ...extra } as SavedSearchesState, actions: { toggle: vi.fn(), remove: vi.fn(), retry: vi.fn() }, createError },
  });

  it("shows no card for a person with none, and the card above the results for one with some", () => {
    expect(draw().container.querySelector(".bj-ss")).toBeNull();
    const { container } = draw({ ...ready(), ...withSearches([search()]) });
    const card = query(container, ".bj-ss");
    expect(card.compareDocumentPosition(query(container, ".bj-disc-results")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(words(card)).toContain('"data analyst"');
  });

  it("reads 'Checked' with the real time when the matcher has run since the search was made, and 'Not checked yet' otherwise", () => {
    const { container } = draw(withSearches([search({ id: "a", last_matched_at: "2026-09-22T06:00:00Z" }), search({ id: "b", last_matched_at: "2026-09-01T00:00:00Z" })]));
    const lines = [...container.querySelectorAll(".bj-ss-row .bj-trail")].map((line) => words(line));
    expect(lines).toEqual(["Checked 06:00", "○ Not checked yet"]);
  });

  it("reads Paused for a paused search, and offers Resume", () => {
    const { container } = draw(withSearches([search({ is_active: false })]));
    expect(words(query(container, ".bj-ss-row .bj-trail"))).toBe("○ Not checked yet · Paused");
    expect(words(buttonNamed(container, /^Resume/))).toBe("Resume");
  });

  it("passes Pause, Resume and Delete to the page by search", () => {
    const handle = withSearches([search()]);
    const { container } = draw(handle);
    click(buttonNamed(container, /^Pause the saved search/));
    click(buttonNamed(container, /^Delete the saved search/));
    expect(handle.saved.actions.toggle).toHaveBeenCalledWith("s-1", false);
    expect(handle.saved.actions.remove).toHaveBeenCalledWith("s-1");
  });

  it("shows a failed read as a callout with Retry inside the card, while the search itself works", () => {
    const retry = vi.fn();
    const { container } = draw({ ...ready(), saved: { state: { kind: "failed", message: "We could not load your saved searches." }, actions: { toggle: vi.fn(), remove: vi.fn(), retry }, createError: null } });
    const alert = query(container, ".bj-ss[role='alert']");
    expect(words(alert)).toContain("We could not load your saved searches.");
    click(buttonNamed(alert, "Try again"));
    expect(retry).toHaveBeenCalled();
    expect(container.querySelectorAll("li.bj-disc-row")).toHaveLength(1);
  });

  it("shows a failed save in the card, in the server's words", () => {
    const { container } = draw(withSearches([], {}, "You can keep at most 25 saved searches."));
    expect(words(query(container, ".bj-ss [role='alert']"))).toBe("You can keep at most 25 saved searches.");
  });

  it("reads Saving... on the form's Save this search while it is out, aria-disabled", () => {
    const onSave = vi.fn();
    const { container } = draw({ saving: true, onSave });
    const saving = buttonNamed(query(container, "form"), "Saving...");
    expect(saving.getAttribute("aria-disabled")).toBe("true");
    click(saving);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("saves with the filters the form holds when Save this search is pressed", () => {
    const onSave = vi.fn();
    const { container } = draw({ onSave });
    click(buttonNamed(query(container, "form"), "Save this search"));
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});

describe("the one cyan fill", () => {
  const states: Array<[string, Partial<DiscoverViewProps>, number]> = [
    ["idle and ready", {}, 1],
    ["searching", { search: { kind: "loading", startedAt: 0 }, outcome: band({ phase: "searching" }) }, 0],
    ["results", ready(), 0],
    ["results scored by the fallback", ready(response({ scoring_strategy: "neutral_fallback" })), 0],
    ["nothing matched", ready(response({ scored: [], more: [] })), 0],
    ["failed", { search: { kind: "error", message: "x" }, outcome: band({}, { pageFailure: { what: "Couldn't finish the search", call: "GET /discover", at: "09:14" } }) }, 1],
    ["no key", { outcome: band({ key: "none" }), setup: { message: "x", missing: [{ label: "Add a model key", to: "/settings#ai-provider" }] } }, 1],
    ["signed-out safe: loading setup", { outcome: nextStep({ page: "discover", signedIn: true, profile: { state: "loading" }, facts: undefined } as NextStepInput) }, 0],
  ];

  it.each(states)("%s holds at most one cyan fill, and it is the band's", (_name, overrides, expected) => {
    // The result states that fall back or hold nothing need the fact-based band to match.
    const merged = _name === "results scored by the fallback" ? { ...ready(response({ scoring_strategy: "neutral_fallback" })), outcome: band({ phase: "results", results: resultsFactOf(response({ scoring_strategy: "neutral_fallback" }), EMPTY_REQUEST) }) } : overrides;
    const { container } = draw(merged);
    const fills = [...container.querySelectorAll("button.bj-primary, a.bj-button-link-primary")];
    const total = _name === "results scored by the fallback" ? 1 : expected;
    expect(fills.length).toBe(total);
    for (const fill of fills) expect(bandOf(container).contains(fill)).toBe(true);
    expect(container.querySelectorAll("li.bj-disc-row button.bj-primary")).toHaveLength(0);
  });
});

describe("keyboard operation", () => {
  it("makes every control a native button, link or field, reachable in reading order, with no positive tab index", () => {
    const { container } = draw(ready(response({ scored: [job(), job({ apply_url: "https://jobs.example/acme/2" })] })));
    const controls = [...container.querySelectorAll<HTMLElement>("button, a[href], input, select, textarea, [tabindex]")];
    expect(controls.length).toBeGreaterThan(8);
    for (const control of controls) {
      const index = control.getAttribute("tabindex");
      expect(index === null || Number(index) <= 0).toBe(true);
      expect(["BUTTON", "A", "INPUT", "H2", "P", "DIV"]).toContain(control.tagName);
    }
    // The order of the page is the order of the document: the form, then the rows.
    const first = (selector: string) => [...container.querySelectorAll("button, a[href], input")].indexOf(query(container, selector));
    expect(first("form input[type='text']")).toBeLessThan(first("form button[type='submit']"));
    expect(first("form button[type='submit']")).toBeLessThan(first("li.bj-disc-row a"));
  });

  it("submits the form on Enter in a field: the form is a real form, so the browser does it", () => {
    const onSearch = vi.fn();
    const { container } = draw({ onSearch });
    const form = query<HTMLFormElement>(container, "form");
    expect(form.getAttribute("novalidate")).toBe("");
    act(() => form.requestSubmit());
    expect(onSearch).toHaveBeenCalledTimes(1);
  });

  it("keeps the trail line a real button that opens its receipt, and Escape closes it back to the line", () => {
    const { container } = draw(ready());
    const trigger = query<HTMLButtonElement>(container, "li.bj-disc-row .bj-trail-trigger");
    expect(trigger.tagName).toBe("BUTTON");
    click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  });

  it("does not hijack any key: nothing on the page listens for a global shortcut", () => {
    const { container } = draw(ready());
    const before = container.innerHTML;
    act(() => {
      for (const key of ["s", "t", "/", "Enter", " "]) document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    });
    expect(container.innerHTML).toBe(before);
  });
});

describe("accessibility", () => {
  const cases: Array<[string, Partial<DiscoverViewProps>]> = [
    ["idle", {}],
    ["loading", { search: { kind: "loading", startedAt: Date.now() }, outcome: band({ phase: "searching" }) }],
    ["results with a fit block, a warning and unscored rows", ready(response({ warnings: ["brave: down"], more: [job({ apply_url: "https://jobs.example/acme/2", score: null })], dead_removed: 2 }))],
    ["a fallback", { ...ready(response({ scoring_strategy: "neutral_fallback" })) }],
    ["empty", ready(response({ scored: [], more: [] }))],
    ["setup", { setup: { message: "x", missing: [{ label: "Add a model key", to: "/settings#ai-provider" }] } }],
    ["failure", { search: { kind: "error", message: "x" }, outcome: band({}, { pageFailure: { what: "Couldn't finish the search", call: "GET /discover", at: "09:14" } }) }],
  ];

  it.each(cases)("%s has no serious or critical axe violation", async (_name, overrides) => {
    const { container } = draw(overrides);
    await expectNoSeriousA11yViolations(container);
  });
});
