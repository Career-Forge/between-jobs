// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { NOW, RUN, FakeSetupError, event } from "../testing/researchFixtures";
import { buttonNamed, click, query, render } from "../testing/render";
import { WarmPathEventsPanel } from "./WarmPathEventsPanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const PATH = "/applications/a1/warm-path-events";

function panel() {
  return render(
    <MemoryRouter>
      <WarmPathEventsPanel applicationId="a1" now={NOW} />
    </MemoryRouter>,
  );
}

const stored = (events: unknown[], runOverrides: Record<string, unknown> = {}) => ({ run: { ...RUN, ...runOverrides }, events });

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("Events", () => {
  it("shows a placeholder while the stored run is read", () => {
    on(`GET ${PATH}`, () => new Promise(() => undefined));
    const { container } = panel();
    expect(query(container, "[role='status'][aria-busy='true']").textContent).toContain("Loading events...");
  });

  it("says before any run that it uses the person's own keys, what it finds and what it needs, and teaches the three certainties", async () => {
    on(`GET ${PATH}`, { run: null, events: [] });
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-ws-intro"))).toContain("uses your own keys each time");
    expect(text(query(container, ".bj-rs-empty h3"))).toBe("No events found yet");
    const body = text(query(container, ".bj-rs-empty"));
    expect(body).toContain("a confirmed speaker");
    expect(body).toContain("a sponsor booth that is likely staffed");
    expect(body).toContain("company");
    expect(query<HTMLAnchorElement>(container, ".bj-rs-empty a").getAttribute("href")).toBe("/settings");
    expect(container.querySelector(".bj-trail")).toBeNull();
    expect(buttonNamed(container, "Find events")).toBeTruthy();
  });

  it("draws a certainty it does not know as its own word with no mark, not as a plain-code circle", async () => {
    on(`GET ${PATH}`, stored([event(1, "mystery_reading")]));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-chip"))).toBe("mystery_reading");
  });

  it("labels each event by how certain the tie is, with a mark and the word, firmest first, and never merges them into one list of equals", async () => {
    on(`GET ${PATH}`, stored([event(1, "company_adjacent"), event(2, "likely_staffed_sponsor"), event(3, "confirmed_speaker")]));
    const { container } = panel();
    await settle();
    const items = [...container.querySelectorAll(".bj-rs-item")];
    expect(items.map((item) => text(item.querySelector("h3")!))).toEqual(["Event 3", "Event 2", "Event 1"]);
    expect(items.map((item) => text(item.querySelector(".bj-rs-chip")!))).toEqual(["◆ Confirmed speaker", "◆ Likely-staffed sponsor", "Company-adjacent"]);
    expect(items.map((item) => [...item.querySelector(".bj-rs-chip")!.classList].find((name) => name.startsWith("bj-rs-chip--")))).toEqual(["bj-rs-chip--solid", "bj-rs-chip--dashed", "bj-rs-chip--muted"]);
    expect(container.innerHTML).not.toContain("bj-badge-emerald");
  });

  it("names a speaker only where the server did, and shows the date, the place and the source link", async () => {
    on(`GET ${PATH}`, stored([event(1, "confirmed_speaker"), event(2, "company_adjacent", { event_date: null })]));
    const { container } = panel();
    await settle();
    const [first, second] = [...container.querySelectorAll(".bj-rs-item")];
    expect(text(first.querySelector(".bj-rs-text")!)).toBe('Dana Example -- Staff engineer: "Batch pipelines"');
    expect(text(first.querySelector(".bj-rs-sub")!)).toBe("2026-10-14 · Jersey City, NJ");
    expect(second.querySelector(".bj-rs-text")).toBeNull();
    expect(text(second.querySelector(".bj-rs-sub")!)).toBe("Jersey City, NJ");
    const link = first.querySelector<HTMLAnchorElement>("a")!;
    expect(link.getAttribute("href")).toBe("https://events.example.com/e1");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("draws the run's trail from its own fields, and the stale line from its date", async () => {
    on(`GET ${PATH}`, stored([event(1, "confirmed_speaker")]));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-meta"))).toBe("Researched Sep 20 · 1 event");
    expect(text(query(container, ".bj-trail"))).toBe("✦ Events picked out by your model · from You.com results, on your keys · Sep 20");
    click(query(container, "button.bj-trail-trigger"));
    expect(text(query(container, ".bj-receipt"))).toContain("No one's attendance is guessed.");
  });

  it("says how many searches did not complete, and keeps the trail's count", async () => {
    on(`GET ${PATH}`, stored([], { warnings: ["confirmed_speaker: You.com timed out."] }));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-state-lead"))).toBe("1 of this run's searches failed or was skipped:");
    expect(text(container)).toContain("No nearby events turned up in that search pass.");
    expect(text(query(container, ".bj-trail"))).toBe("○ Searched with You.com · on your keys · Sep 20 · ✕ 1 search failed or skipped");
  });

  it("keeps the earlier events when a run fails and retries the run from the callout", async () => {
    on(`GET ${PATH}`, stored([event(1, "confirmed_speaker")]));
    on(`POST ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Refresh"));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    on(`POST ${PATH}`, stored([event(1, "confirmed_speaker"), event(2, "company_adjacent")]));
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(2);
  });

  it("links to Settings for a missing key, with no Retry and no trail", async () => {
    on(`GET ${PATH}`, { run: null, events: [] });
    on(`POST ${PATH}`, new FakeSetupError("Events need a You.com or Firecrawl key.", "/profile/integrations", "warm_path_events", ["you_com_or_firecrawl_credential"]));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Find events"));
    await settle();
    expect(query<HTMLAnchorElement>(container, ".bj-setup-notice a").getAttribute("href")).toBe("/settings#search-keys");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(false);
    expect(container.querySelector(".bj-trail")).toBeNull();
  });

  it("busies only its button while a run is out, and sends one request for two presses", async () => {
    on(`GET ${PATH}`, { run: null, events: [] });
    let finish: (value: unknown) => void = () => undefined;
    on(`POST ${PATH}`, () => new Promise((resolve) => (finish = resolve)));
    const { container } = panel();
    await settle();
    const button = buttonNamed(container, "Find events");
    click(button);
    await settle(1);
    expect(button.textContent).toBe("Searching...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    click(button);
    expect(callsTo(`POST ${PATH}`)).toHaveLength(1);
    finish(stored([event(1, "confirmed_speaker")]));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
  });

  it("draws no cyan, and has no serious accessibility violation", async () => {
    on(`GET ${PATH}`, stored([event(1, "confirmed_speaker"), event(2, "likely_staffed_sponsor"), event(3, "company_adjacent")], { warnings: ["x"] }));
    const { container } = panel();
    await settle();
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
    await expectNoSeriousA11yViolations(container);
  });

  it("names a read that failed with a Retry that reads again", async () => {
    on(`GET ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-trail"))).toBe("✕ Events couldn't be read");
    on(`GET ${PATH}`, stored([event(1, "confirmed_speaker")]));
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
  });
});
