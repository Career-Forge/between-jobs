// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CapabilitiesState } from "../lib/capabilities";
import type { GmailConnection } from "../lib/gmailAccess";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { NOW, RUN, FakeSetupError, candidate } from "../testing/researchFixtures";
import { buttonNamed, click, query, render } from "../testing/render";
import { ContactFinderPanel } from "./ContactFinderPanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const gmail = vi.hoisted(() => ({
  capabilities: { kind: "ready", capabilities: { gmail: true, gmailAllowed: true } } as unknown as CapabilitiesState,
  connection: { kind: "connected" } as GmailConnection,
  markReconnectNeeded: vi.fn(),
  refreshCapabilities: vi.fn(),
  copy: vi.fn(),
}));

vi.mock("../lib/useCapabilities", () => ({ useCapabilities: () => gmail.capabilities, refreshCapabilities: gmail.refreshCapabilities }));
vi.mock("../lib/useGmailConnection", () => ({ useGmailConnection: () => ({ connection: gmail.connection, markReconnectNeeded: gmail.markReconnectNeeded }) }));
vi.mock("../lib/clipboard", () => ({ copyToClipboard: (value: string) => gmail.copy(value) }));

const PATH = "/applications/a1/contacts";
const stored = (candidates: unknown[], runOverrides: Record<string, unknown> = {}) => ({ run: { ...RUN, ...runOverrides }, candidates });
const DRAFT = {
  id: "d1",
  subject: "Your data platform talk",
  email_body: "Hi Person 1, I enjoyed the talk.",
  linkedin_message: "Hi, loved the talk.",
  follow_up_message: "Following up on my note.",
  rubric_warnings: [] as string[],
  gmail_draft_id: null as string | null,
  created_at: "2026-09-20T12:00:00Z",
};

function panel() {
  return render(
    <MemoryRouter>
      <ContactFinderPanel applicationId="a1" now={NOW} />
    </MemoryRouter>,
  );
}

const row = (container: HTMLElement, index = 0) => container.querySelectorAll<HTMLElement>(".bj-rs-item")[index];

beforeEach(() => {
  reset();
  gmail.capabilities = { kind: "ready", capabilities: { gmail: true, gmailAllowed: true } } as unknown as CapabilitiesState;
  gmail.connection = { kind: "connected" };
  gmail.markReconnectNeeded.mockReset();
  gmail.refreshCapabilities.mockReset();
  gmail.copy.mockReset().mockResolvedValue(true);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => vi.restoreAllMocks());

describe("Contacts: loading, empty and a stored run", () => {
  it("shows a placeholder while the stored run is read", () => {
    on(`GET ${PATH}`, () => new Promise(() => undefined));
    const { container } = panel();
    expect(query(container, "[role='status'][aria-busy='true']").textContent).toContain("Loading contacts...");
  });

  it("teaches what a run finds, that it gives no email or phone number, and what it needs", async () => {
    on(`GET ${PATH}`, { run: null, candidates: [] });
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-empty h3"))).toBe("No contacts found yet");
    const body = text(query(container, ".bj-rs-empty"));
    expect(body).toContain("hiring lead");
    expect(body).toContain("no email address or phone number");
    expect(body).toContain("Firecrawl key");
    expect(query<HTMLAnchorElement>(container, ".bj-rs-empty a").getAttribute("href")).toBe("/settings");
    expect(container.querySelector(".bj-trail")).toBeNull();
    expect(buttonNamed(container, "Find contacts")).toBeTruthy();
  });

  it("draws each person with their role, the order code gave them, their reason and the approach", async () => {
    on(`GET ${PATH}`, stored([candidate(1), candidate(2, { claimed_title: null, claimed_team: null, approach_hint: null })]));
    const { container } = panel();
    await settle();
    const first = row(container);
    expect(text(first.querySelector("h3")!)).toBe("Person 1");
    expect(text(first.querySelector(".bj-rs-sub")!)).toBe("Engineering manager · Data Platform");
    expect(text(first.querySelector(".bj-rs-chip")!)).toBe("Engineering manager");
    expect(text(first.querySelector(".bj-rs-priority")!)).toBe("Priority 71 / 100");
    expect(text(first)).toContain("Manager persona match; 2 independent sources.");
    expect(text(first)).toContain("Approach: Mention the data platform talk.");
    expect(row(container, 1).querySelector(".bj-rs-sub")).toBeNull();
    expect(text(row(container, 1))).not.toContain("Approach:");
  });

  it("shows each source with the model's reading of how clearly it names the person, as gold cells and a word -- never green and never the word verified", async () => {
    on(`GET ${PATH}`, stored([candidate(1)]));
    const { container } = panel();
    await settle();
    const lines = [...row(container).querySelectorAll(".bj-rs-ev")];
    expect(lines.map((line) => [line.querySelectorAll(".bj-segbar-cell--filled").length, line.querySelectorAll(".bj-segbar-cell").length, text(line.querySelector(".bj-rs-conf-word")!)])).toEqual([
      [4, 4, "Confidence: Clear match"],
      [2, 4, "Confidence: Indirect"],
    ]);
    // A source with no title shows its address.
    expect(text(lines[1].querySelector("a")!)).toContain("https://blog.example.com/person-1");
    expect(lines[0].querySelector("a")!.getAttribute("rel")).toBe("noopener noreferrer");
    expect(row(container).innerHTML).not.toContain("bj-badge-emerald");
    expect(text(container)).not.toMatch(/verified/i);
  });

  it("draws the run's trail from its fields and says how the names were checked in its receipt", async () => {
    on(`GET ${PATH}`, stored([candidate(1)], { providers_used: ["you_com", "firecrawl", "github"] }));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-meta"))).toBe("Researched Sep 20 · 1 person");
    expect(text(query(container, ".bj-trail"))).toBe("✦ People named by your model · from You.com, Firecrawl and GitHub results · Sep 20");
    click(query(container, "button.bj-trail-trigger"));
    const receipt = text(query(container, ".bj-receipt"));
    expect(receipt).toContain("Code checked that each name appears in the title or snippet of the source it cites.");
    expect(receipt).toContain("GitHub's public member list needs no key");
  });

  it("names the searches that were skipped for want of a Firecrawl key, and keeps the people found", async () => {
    on(`GET ${PATH}`, stored([candidate(1)], { warnings: ["hiring_lead: needs Firecrawl, not configured -- skipped."] }));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-state-lead"))).toBe("1 of this run's searches failed or was skipped:");
    expect(text(query(container, ".bj-state--partial li"))).toBe("hiring_lead: needs Firecrawl, not configured -- skipped.");
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
    expect(text(query(container, ".bj-trail"))).toContain("✕ 1 search failed or skipped");
  });

  it("says when a run found no one named, and keeps its trail", async () => {
    on(`GET ${PATH}`, stored([]));
    const { container } = panel();
    await settle();
    expect(text(container)).toContain("No named people turned up in that search pass.");
    expect(container.querySelector(".bj-trail")).not.toBeNull();
  });

  it("does not say a model named anyone for a run that found no one", async () => {
    const warnings = Array.from({ length: 7 }, (_, index) => `search ${index + 1} failed`);
    on(`GET ${PATH}`, stored([], { warnings }));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-trail"))).toBe("○ Searched with You.com · on your keys · Sep 20 · ✕ 7 searches failed or skipped");
    expect(text(container)).not.toContain("by your model");
  });
});

describe("Contacts: the lookups, one person at a time, on the person's own credit", () => {
  const UPDATED = { id: "p1", enriched_email: "person.one@northwind.example", enriched_email_status: "valid", enrichment_provider: "hunter", enriched_at: "2026-09-22T12:00:00Z" };

  it("asks first, names the credit, and sends nothing when the person says no", async () => {
    on(`GET ${PATH}`, stored([candidate(1)]));
    on(`POST ${PATH}/p1/enrich`, UPDATED);
    vi.mocked(window.confirm).mockReturnValue(false);
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Find work email"));
    await settle();
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("your own Apollo and/or Hunter credit for one lookup"));
    expect(callsTo(`POST ${PATH}/p1/enrich`)).toHaveLength(0);
  });

  it("merges the answer onto the person: the email shows and their sources stay", async () => {
    on(`GET ${PATH}`, stored([candidate(1)]));
    on(`POST ${PATH}/p1/enrich`, UPDATED);
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Find work email"));
    await settle();
    expect(text(query(row(container), ".bj-rs-fact"))).toBe("Work email person.one@northwind.example (valid) via hunter");
    expect(row(container).querySelectorAll(".bj-rs-ev")).toHaveLength(2);
    expect([...row(container).querySelectorAll("button")].some((button) => button.textContent === "Find work email")).toBe(false);
  });

  it("says a lookup that ran and found nothing, and does not invite another without a Retry the person chooses", async () => {
    on(`GET ${PATH}`, stored([candidate(1, { enriched_at: "2026-09-21T12:00:00Z", linkedin_discovered_at: "2026-09-21T12:00:00Z" })]));
    const { container } = panel();
    await settle();
    const facts = [...row(container).querySelectorAll(".bj-rs-fact")].map((node) => text(node));
    expect(facts).toEqual(["No confirmed email found. Retry", "No LinkedIn profile found. Retry"]);
    expect([...row(container).querySelectorAll(".bj-actions button")].map((button) => button.textContent)).toEqual(["Draft outreach"]);
  });

  it("busies only the pressed button while a lookup is out, and sends one request for two presses", async () => {
    on(`GET ${PATH}`, stored([candidate(1)]));
    let finish: (value: unknown) => void = () => undefined;
    on(`POST ${PATH}/p1/enrich`, () => new Promise((resolve) => (finish = resolve)));
    const { container } = panel();
    await settle();
    const button = buttonNamed(container, "Find work email");
    button.focus();
    click(button);
    await settle(1);
    expect(button.textContent).toBe("Looking up...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.hasAttribute("disabled")).toBe(false);
    expect(document.activeElement).toBe(button);
    expect(buttonNamed(container, "Find LinkedIn").getAttribute("aria-disabled")).toBeNull();
    click(button);
    expect(callsTo(`POST ${PATH}/p1/enrich`)).toHaveLength(1);
    finish(UPDATED);
    await settle();
  });

  it("puts a failed lookup on that person's row, not on the others, and Retry asks again", async () => {
    on(`GET ${PATH}`, stored([candidate(1), candidate(2)]));
    on(`POST ${PATH}/p1/enrich`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    click(buttonNamed(row(container, 0), "Find work email"));
    await settle();
    expect(text(query(row(container, 0), "[role='alert']"))).toContain("The server hit a problem.");
    expect(row(container, 1).querySelector("[role='alert']")).toBeNull();
    on(`POST ${PATH}/p1/enrich`, UPDATED);
    click(buttonNamed(row(container, 0), "Retry"));
    await settle();
    expect(callsTo(`POST ${PATH}/p1/enrich`)).toHaveLength(2);
    expect(row(container, 0).querySelector("[role='alert']")).toBeNull();
    expect(text(query(row(container, 0), ".bj-rs-fact"))).toContain("person.one@northwind.example");
  });

  it("links to Settings for a missing lookup key, with the name of the key and no Retry", async () => {
    on(`GET ${PATH}`, stored([candidate(1)]));
    on(`POST ${PATH}/p1/enrich`, new FakeSetupError("No Apollo or Hunter key is saved.", "/profile/integrations", "contact_enrichment", ["apollo_credential"]));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Find work email"));
    await settle();
    const link = query<HTMLAnchorElement>(row(container), ".bj-setup-notice a");
    expect(link.getAttribute("href")).toBe("/settings");
    expect(text(link)).toBe("Add an Apollo or Hunter key in Settings");
    expect([...row(container).querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(false);
  });

  it("links a found LinkedIn profile that opens in a new tab, says how it was found and to check it is the right person unless the match is strong", async () => {
    on(`GET ${PATH}`, stored([candidate(1, { discovered_linkedin_url: "https://www.linkedin.com/in/person-one", linkedin_discovery_confidence: "inferred", linkedin_discovery_provider: "exa", linkedin_discovered_at: "2026-09-21T12:00:00Z" }), candidate(2, { discovered_linkedin_url: "https://www.linkedin.com/in/person-two", linkedin_discovery_confidence: "strong", linkedin_discovery_provider: "exa" })]));
    const { container } = panel();
    await settle();
    const first = row(container, 0).querySelector<HTMLAnchorElement>(".bj-rs-fact a")!;
    expect(first.getAttribute("href")).toBe("https://www.linkedin.com/in/person-one");
    expect(first.getAttribute("target")).toBe("_blank");
    expect(text(first.parentElement!)).toContain("inferred match via exa. A name search can surface someone else: check it is the right person.");
    expect(text(row(container, 1).querySelector(".bj-rs-fact")!)).not.toContain("check it is the right person");
  });

  it("never makes a link of an unsafe LinkedIn address", async () => {
    on(`GET ${PATH}`, stored([candidate(1, { discovered_linkedin_url: "javascript:alert(1)", linkedin_discovery_confidence: "inferred", linkedin_discovered_at: "2026-09-21T12:00:00Z" })]));
    const { container } = panel();
    await settle();
    expect(container.querySelector("a[href^='javascript']")).toBeNull();
    expect(text(row(container))).toContain("No LinkedIn profile found.");
  });
});

describe("Contacts: an outreach draft stays a draft", () => {
  async function withDraft(extra: Partial<typeof DRAFT> = {}) {
    on(`GET ${PATH}`, stored([candidate(1, { enriched_email: "person.one@northwind.example", enriched_at: "2026-09-21T12:00:00Z" })]));
    on(`POST ${PATH}/p1/draft-outreach`, { draft: { ...DRAFT, ...extra } });
    const view = panel();
    await settle();
    click(buttonNamed(view.container, "Draft outreach"));
    await settle();
    return view;
  }

  it("shows the four messages as a draft that is not sent, with a Copy for each, and says nothing is sent from here", async () => {
    const { container } = await withDraft();
    const draft = query(container, ".bj-rs-draft");
    expect(draft.getAttribute("role")).toBe("group");
    expect(draft.getAttribute("aria-label")).toBe("Outreach draft for Person 1");
    expect(text(query(draft, ".bj-rs-draft-state"))).toBe("Draft -- not sent");
    expect([...draft.querySelectorAll(".bj-rs-field")].map((field) => [text(field.querySelector("h4")!), text(field.querySelector(".bj-rs-well")!)])).toEqual([
      ["Subject", DRAFT.subject],
      ["Email", DRAFT.email_body],
      ["LinkedIn message", DRAFT.linkedin_message],
      ["Follow-up", DRAFT.follow_up_message],
    ]);
    expect([...draft.querySelectorAll(".bj-rs-field button")].map((button) => button.getAttribute("aria-label"))).toEqual(["Copy subject", "Copy email", "Copy linkedin message", "Copy follow-up"]);
    expect(text(draft)).toContain("Nothing is sent from here.");
    expect(buttonNamed(container, "Redraft outreach")).toBeTruthy();
  });

  it("carries its own trail: your model, on your key, and the time the draft row came back with", async () => {
    const { container } = await withDraft();
    const trail = query(query(container, ".bj-rs-draft"), ".bj-trail");
    expect(text(trail)).toBe("✦ Drafted by your model · on your model key · Sep 20");
    click(trail);
    const receipt = text(query(container, ".bj-rs-draft .bj-receipt"));
    expect(receipt).toContain("Nothing is sent.");
    expect(receipt).not.toContain("No time came back");
  });

  it("leaves the time out, and says nothing about it, when the draft row carries none", async () => {
    const { container } = await withDraft({ created_at: undefined as unknown as string });
    const trail = query(query(container, ".bj-rs-draft"), ".bj-trail");
    expect(text(trail)).toBe("✦ Drafted by your model · on your model key");
    click(trail);
    expect(text(query(container, ".bj-rs-draft .bj-receipt"))).not.toContain("No time came back");
  });

  it("flags what a code check found, as a neutral callout above the draft", async () => {
    const { container } = await withDraft({ rubric_warnings: ["email_body opens with a generic, banned phrase"] });
    const flagged = query(container, ".bj-rs-draft .bj-state--partial");
    expect(text(flagged)).toContain("A code check flagged this draft:");
    expect(text(flagged)).toContain("email_body opens with a generic, banned phrase");
  });

  it("copies a field and says so, and says when the browser refused", async () => {
    const { container } = await withDraft();
    const subject = buttonNamed(container, "Copy subject");
    click(subject);
    await settle();
    expect(gmail.copy).toHaveBeenCalledWith(DRAFT.subject);
    expect(subject.textContent).toBe("Copied!");
    gmail.copy.mockResolvedValue(false);
    const email = buttonNamed(container, "Copy email");
    click(email);
    await settle();
    expect(email.textContent).toBe("Couldn't copy");
  });

  it("explains the server declining to draft for want of evidence as a gold note, not a failure, with no Retry", async () => {
    on(`GET ${PATH}`, stored([candidate(1)]));
    on(`POST ${PATH}/p1/draft-outreach`, new FakeApiError("There is nothing in this contact's sources to ground a draft in.", "INSUFFICIENT_EVIDENCE", undefined, 422));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Draft outreach"));
    await settle();
    const note = query(row(container), ".bj-rs-note");
    expect(note.getAttribute("role")).toBe("status");
    expect(text(note)).toBe("◆ There is nothing in this contact's sources to ground a draft in.");
    expect(row(container).querySelector("[role='alert']")).toBeNull();
    expect([...row(container).querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(false);
  });

  it("with Gmail connected and a work email, offers Create Gmail draft outlined -- optional, and never sent", async () => {
    const { container } = await withDraft();
    const create = buttonNamed(container, "Create Gmail draft");
    expect(create.classList.contains("bj-primary")).toBe(false);
    on(`POST ${PATH}/p1/push-to-gmail`, { ...DRAFT, gmail_draft_id: "g1" });
    click(create);
    await settle();
    expect(text(query(container, ".bj-rs-draft"))).toContain("Draft created in your Gmail");
  });

  it("without Gmail, offers the mail app and the clipboard and sends nobody to connect a Gmail that is not theirs", async () => {
    gmail.capabilities = { kind: "ready", capabilities: { gmail: true, gmailAllowed: false } } as unknown as CapabilitiesState;
    const { container } = await withDraft();
    const draft = query(container, ".bj-rs-draft");
    expect(text(draft)).toContain("Gmail isn't available for your account yet.");
    expect(draft.querySelector("a[href^='mailto:']")).not.toBeNull();
    expect(text(draft)).not.toContain("Create Gmail draft");
    expect(draft.querySelector("a[href='/settings']")).toBeNull();
  });

  it("learns from a refused push first: a reconnect answer is remembered and shown with its link on that person's row", async () => {
    const { container } = await withDraft();
    const refusal = new FakeSetupError("Google stopped accepting your Gmail connection.", "/profile/integrations", "gmail", ["gmail_oauth"]);
    Object.assign(refusal, { reason: "gmail_reconnect_required" });
    on(`POST ${PATH}/p1/push-to-gmail`, refusal);
    click(buttonNamed(container, "Create Gmail draft"));
    await settle();
    expect(gmail.markReconnectNeeded).toHaveBeenCalledTimes(1);
    expect(text(query(row(container), ".bj-setup-notice"))).toContain("Google stopped accepting your Gmail connection.");
    expect(query<HTMLAnchorElement>(row(container), ".bj-setup-notice a").getAttribute("href")).toBe("/settings");
  });

  it("refreshes what is known about Gmail when the server says it is not available", async () => {
    const { container } = await withDraft();
    on(`POST ${PATH}/p1/push-to-gmail`, new FakeApiError("Gmail is not available.", "FEATURE_DISABLED", undefined, 404));
    click(buttonNamed(container, "Create Gmail draft"));
    await settle();
    expect(gmail.refreshCapabilities).toHaveBeenCalledTimes(1);
    expect(text(query(row(container), "[role='alert']"))).toContain("Gmail is not available.");
  });
});

describe("Contacts: running", () => {
  it("keeps the earlier people when a run fails, and Retry runs again", async () => {
    on(`GET ${PATH}`, stored([candidate(1)]));
    on(`POST ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Refresh"));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    on(`POST ${PATH}`, stored([candidate(1), candidate(2)]));
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(2);
  });

  it("links to Settings when a run needs a key, with no Retry", async () => {
    on(`GET ${PATH}`, { run: null, candidates: [] });
    on(`POST ${PATH}`, new FakeSetupError("ContactFinder needs a You.com or Firecrawl key configured.", "/profile/integrations", "contact_research", ["you_com_or_firecrawl_credential"]));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Find contacts"));
    await settle();
    expect(query<HTMLAnchorElement>(container, ".bj-setup-notice a").getAttribute("href")).toBe("/settings");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(false);
  });

  it("names a read that failed, with a Retry that reads again", async () => {
    on(`GET ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-trail"))).toBe("✕ Contacts couldn't be read");
    on(`GET ${PATH}`, stored([candidate(1)]));
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
  });
});

describe("Contacts: the page's one cyan fill is the band's, and the markup is sound", () => {
  it("draws no primary button or link in any of its states", async () => {
    on(`GET ${PATH}`, stored([candidate(1, { enriched_email: "p@x.example" })], { warnings: ["x"] }));
    on(`POST ${PATH}/p1/draft-outreach`, { draft: DRAFT });
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Draft outreach"));
    await settle();
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
  });

  it.each([
    ["people with sources", () => on(`GET ${PATH}`, stored([candidate(1), candidate(2, { enriched_at: "2026-09-21T12:00:00Z", linkedin_discovered_at: "2026-09-21T12:00:00Z" })], { warnings: ["x"] }))],
    ["empty", () => on(`GET ${PATH}`, { run: null, candidates: [] })],
  ])("has no serious accessibility violation (%s)", async (_name, describeReads) => {
    describeReads();
    const { container } = panel();
    await settle();
    await expectNoSeriousA11yViolations(container);
  });

  it("has none with a draft open and a Gmail fallback showing", async () => {
    gmail.connection = { kind: "none" };
    on(`GET ${PATH}`, stored([candidate(1, { enriched_email: "p@x.example", enriched_at: "2026-09-21T12:00:00Z" })]));
    on(`POST ${PATH}/p1/draft-outreach`, { draft: { ...DRAFT, rubric_warnings: ["a flag"] } });
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Draft outreach"));
    await settle();
    expect(text(query(container, ".bj-rs-draft"))).toContain("Connect Gmail to have drafts created for you.");
    await expectNoSeriousA11yViolations(container);
  });
});
