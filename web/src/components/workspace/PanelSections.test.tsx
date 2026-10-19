// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CapabilitiesState } from "../../lib/capabilities";
import { SECTION_IDS } from "../../lib/workspaceSections";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { callsTo, on, reset } from "../../testing/fakeApi";
import { settle, text } from "../../testing/forms";
import { BRIEF, RUN, SESSION, answered, candidate, claim, event, question } from "../../testing/researchFixtures";
import { buttonNamed, click, installViewport, keydown, query, render } from "../../testing/render";
import { DocumentLayout } from "../paper/DocumentLayout";
import { PanelSections } from "./PanelSections";
import type { HiringStatus } from "../../lib/hiringSignalsStatus";

vi.mock("../../lib/api", async () => (await import("../../testing/fakeApi")).apiModule);
vi.mock("../../lib/useCapabilities", () => ({
  useCapabilities: () => ({ kind: "ready", capabilities: { gmail: false } }) as unknown as CapabilitiesState,
  refreshCapabilities: () => undefined,
}));
vi.mock("../../lib/useGmailConnection", () => ({ useGmailConnection: () => ({ connection: { kind: "unknown" }, markReconnectNeeded: () => undefined }) }));

// The research and prepare part of the workspace, as the page builds it: each section is a chapter (its
// heading and its anchor) and a block, inside the document layout whose width decides where each block's
// trail goes. The panels themselves are the real ones, over a stand-in for the API.

const ENABLED: HiringStatus = { kind: "enabled" };
const SAVED_POST = { id: "save-1", activity_id: "7000000000000000001", post_url: "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001", embed_url: "https://www.linkedin.com/embed/feed/update/urn:li:activity:7000000000000000001", created_at: "2026-09-18T12:00:00+00:00" };

function describeEverything() {
  on("GET /applications/a1/company-intel", { run: RUN, claims: [claim(1), claim(2)] });
  on("GET /applications/a1/contacts", { run: RUN, candidates: [candidate(1)] });
  on("GET /applications/a1/warm-path-events", { run: RUN, events: [event(1, "confirmed_speaker")] });
  on("GET /applications/a1/hiring-signals/saves", { saves: [SAVED_POST] });
  on("GET /applications/a1/interview-practice/sessions", { sessions: [SESSION] });
  on(`GET /applications/a1/interview-practice/sessions/${SESSION.id}`, { session: SESSION, questions: [answered(1, 7), ...[2, 3, 4, 5].map((n) => question(n))] });
  on("GET /applications/a1/positioning-brief", { brief: BRIEF });
}

function sections(hiring: HiringStatus = ENABLED, hiringRequested = false) {
  return render(
    <MemoryRouter>
      <DocumentLayout>
        <PanelSections applicationId="a1" hiring={hiring} hiringRequested={hiringRequested} />
      </DocumentLayout>
    </MemoryRouter>,
  );
}

const TITLES = [
  [SECTION_IDS.companyIntel, "Company intel"],
  [SECTION_IDS.contacts, "Contacts"],
  [SECTION_IDS.events, "Warm-path events"],
  [SECTION_IDS.hiringPosts, "Hiring posts"],
  [SECTION_IDS.practice, "Interview practice"],
  [SECTION_IDS.yourPlay, "Your play"],
] as const;

beforeEach(() => {
  reset();
  describeEverything();
});
afterEach(() => vi.restoreAllMocks());

describe("the sections' structure", () => {
  it("makes each section a chapter on its anchor: one h2, its title, and nothing else that is an h2", async () => {
    installViewport(1280);
    const { container } = sections();
    await settle();
    for (const [id, title] of TITLES) {
      const chapter = query(container, `#${id}`);
      expect(text(chapter.querySelector("h2")!)).toBe(title);
      expect(chapter.querySelector("h2")!.getAttribute("tabindex")).toBe("-1");
    }
    expect([...container.querySelectorAll("h2")].map((heading) => text(heading))).toEqual(TITLES.map(([, title]) => title));
  });

  it("names the group on the first section of each group only", async () => {
    installViewport(1280);
    const { container } = sections();
    await settle();
    expect([...container.querySelectorAll(".bj-chapter-eyebrow")].map((node) => text(node))).toEqual(["Research", "Prepare"]);
  });

  it("draws no primary button or link anywhere in the six sections, so the next-step band keeps the page's one cyan fill", async () => {
    installViewport(1280);
    const { container } = sections();
    await settle();
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
  });

  it("starts every request each section makes, once", async () => {
    installViewport(1280);
    sections();
    await settle();
    for (const route of [
      "GET /applications/a1/company-intel",
      "GET /applications/a1/contacts",
      "GET /applications/a1/warm-path-events",
      "GET /applications/a1/hiring-signals/saves",
      "GET /applications/a1/interview-practice/sessions",
      "GET /applications/a1/positioning-brief",
    ]) {
      expect(callsTo(route), route).toHaveLength(1);
    }
  });
});

describe("hiring posts follows what the server says", () => {
  it("shows its section while the feature is on, with the person's saved posts and a trail that says they are theirs", async () => {
    installViewport(1280);
    const { container } = sections(ENABLED);
    await settle();
    const section = query(container, `#${SECTION_IDS.hiringPosts}`).parentElement!;
    expect(text(section.querySelector(".bj-hs-saved-section h3")!)).toBe("Saved posts");
    expect(text(section.querySelector(".bj-trail")!)).toBe("● You saved these · 1 post");
  });

  it("has nothing for it while the answer is not known: an empty anchor, no heading, no request", async () => {
    installViewport(1280);
    const { container } = sections({ kind: "checking" } as HiringStatus);
    await settle();
    const anchor = query(container, `#${SECTION_IDS.hiringPosts}`);
    expect(anchor.textContent).toBe("");
    expect(anchor.querySelector("h2")).toBeNull();
    expect(callsTo("GET /applications/a1/hiring-signals/saves")).toHaveLength(0);
  });

  it("says it is switched off on this server, but only to a person who followed a link to it", async () => {
    installViewport(1280);
    const asked = sections({ kind: "disabled" } as HiringStatus, true);
    await settle();
    expect(text(query(asked.container, `#${SECTION_IDS.hiringPosts}`))).toContain("Hiring posts is switched off on this server.");
    asked.unmount();
    const quiet = sections({ kind: "disabled" } as HiringStatus, false);
    await settle();
    expect(query(quiet.container, `#${SECTION_IDS.hiringPosts}`).textContent).toBe("");
  });
});

describe("hiring posts: a search's results", () => {
  const ID = "7000000000000000002";
  const RESPONSE = {
    provider: "firecrawl",
    cached: true,
    freshness: "week",
    query_label: "Northwind -- data engineer -- last 7 days",
    signals: [{ activity_id: ID, post_url: `https://www.linkedin.com/posts/example-${ID}`, embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`, author_name: "Jane Example", posted_at: "2026-09-17T10:00:00+00:00", age_hint: null, species: "unclassified", comment_count: null, role_match: null, registry_match: null, saved: false }],
    counts: { raw_hits: 1, rejected: 0, duplicates: 0, off_topic_hidden: 0, echoes_hidden: 0, job_seekers_hidden: 0, too_old_hidden: 0, shown: 1 },
  };

  it("draws a trail for the results -- the person's provider, tagged by code, no model, and that they were cached -- with the Find button outlined", async () => {
    installViewport(1280);
    on("GET /applications/a1/hiring-signals/saves", { saves: [] });
    on("POST /applications/a1/hiring-signals/search", RESPONSE);
    const { container } = sections();
    await settle();
    const section = query(container, `#${SECTION_IDS.hiringPosts}`).parentElement!;
    expect(section.querySelector(".bj-trail")).toBeNull();
    const find = buttonNamed(section, "Find hiring posts");
    expect(find.classList.contains("bj-primary")).toBe(false);
    click(find);
    await settle();
    expect(text(section.querySelector(".bj-doc-margin .bj-trail")!)).toBe("○ via Firecrawl · tagged by code, no model involved · cached result");
    expect(text(section)).toContain("Northwind -- data engineer -- last 7 days");
  });
});

describe("where each block's trail goes", () => {
  it("sits in the right margin from 1280px, one line per block, each a button that opens its receipt there", async () => {
    installViewport(1280);
    const { container } = sections();
    await settle();
    const margins = [...container.querySelectorAll(".bj-doc-margin")];
    expect(margins).toHaveLength(6);
    const lines = margins.map((margin) => [...margin.querySelectorAll(".bj-trail")].map((line) => text(line)));
    expect(lines[0]).toEqual(["✦ Claims written by your model · from You.com results, on your keys · Sep 20"]);
    expect(lines[1]).toEqual(["✦ People named by your model · from You.com results, on your keys · Sep 20"]);
    expect(lines[2]).toEqual(["✦ Events picked out by your model · from You.com results, on your keys · Sep 20"]);
    expect(lines[3]).toEqual(["● You saved these · 1 post"]);
    expect(lines[4]).toEqual(["✦ Questions asked by your model · from your resume evidence · Sep 21"]);
    expect(lines[5]).toEqual(["✦ Written by your model · on your model key · Sep 20"]);
    const trigger = query<HTMLButtonElement>(margins[0], "button.bj-trail-trigger");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(text(query(margins[0], ".bj-receipt"))).toContain("Northwind Labs");
  });

  it("opens one receipt at a time, and Esc closes it and returns focus to its line", async () => {
    installViewport(1280);
    const { container } = sections();
    await settle();
    const [first, second] = [...container.querySelectorAll<HTMLButtonElement>(".bj-doc-margin button.bj-trail-trigger")];
    click(first);
    click(second);
    expect(first.getAttribute("aria-expanded")).toBe("false");
    expect(second.getAttribute("aria-expanded")).toBe("true");
    keydown("Escape", { target: second });
    expect(second.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(second);
  });

  it("sits under its block between 960 and 1279px, with the receipt inline as a disclosure", async () => {
    installViewport(1024);
    const { container } = sections();
    await settle();
    expect(container.querySelector(".bj-doc-margin")).toBeNull();
    const main = query(container, `#${SECTION_IDS.companyIntel}`).parentElement!.querySelector(".bj-doc-main")!;
    const trigger = query<HTMLButtonElement>(main, "button.bj-trail-trigger");
    click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(text(query(main, ".bj-trail-receipt"))).toContain("01234567");
  });

  it("opens as a focus-trapped bottom sheet on a phone, with a Close button, and Esc closes it", async () => {
    installViewport(375);
    const { container } = sections();
    await settle();
    const trigger = query<HTMLButtonElement>(query(container, `#${SECTION_IDS.companyIntel}`).parentElement!, "button.bj-trail-trigger");
    expect(trigger.getAttribute("aria-haspopup")).toBe("dialog");
    click(trigger);
    const dialog = query(document.body, "[role='dialog']");
    expect(text(dialog)).toContain("Northwind Labs");
    expect(buttonNamed(dialog, "Close")).toBeTruthy();
    keydown("Escape", { target: dialog });
    expect(document.body.querySelector("[role='dialog']")).toBeNull();
  });
});

describe("a failure of one section never touches another", () => {
  it("lets Company intel fail while the other five keep their content and their trails", async () => {
    installViewport(1280);
    on("GET /applications/a1/company-intel", new Error("The server hit a problem."));
    const { container } = sections();
    await settle();
    expect(text(query(container, `#${SECTION_IDS.companyIntel}`).parentElement!.querySelector("[role='alert']")!)).toContain("The server hit a problem.");
    expect(container.querySelectorAll(".bj-doc-margin .bj-trail")).toHaveLength(6);
    expect(text(container.querySelectorAll(".bj-doc-margin")[0].querySelector(".bj-trail")!)).toBe("✕ Company intel couldn't be read");
    expect(container.querySelectorAll(".bj-rs-item").length).toBeGreaterThan(0);
  });
});

describe("accessibility", () => {
  it.each([375, 1024, 1280])("has no serious violation at %ipx", async (width) => {
    installViewport(width);
    const { container } = sections();
    await settle();
    await expectNoSeriousA11yViolations(container);
  });

  it("has none in the empty states", async () => {
    installViewport(1280);
    on("GET /applications/a1/company-intel", { run: null, claims: [] });
    on("GET /applications/a1/contacts", { run: null, candidates: [] });
    on("GET /applications/a1/warm-path-events", { run: null, events: [] });
    on("GET /applications/a1/hiring-signals/saves", { saves: [] });
    on("GET /applications/a1/interview-practice/sessions", { sessions: [] });
    on("GET /applications/a1/positioning-brief", { brief: null });
    const { container } = sections();
    await settle();
    expect(container.querySelectorAll(".bj-rs-empty")).toHaveLength(5);
    expect(container.querySelectorAll(".bj-trail")).toHaveLength(0);
    await expectNoSeriousA11yViolations(container);
  });
});
