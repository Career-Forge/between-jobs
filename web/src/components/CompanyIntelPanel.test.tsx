// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { NOW, RUN, FakeSetupError, claim } from "../testing/researchFixtures";
import { buttonNamed, click, query, render } from "../testing/render";
import { CompanyIntelPanel } from "./CompanyIntelPanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const PATH = "/applications/a1/company-intel";

function panel() {
  return render(
    <MemoryRouter>
      <CompanyIntelPanel applicationId="a1" now={NOW} />
    </MemoryRouter>,
  );
}

const stored = (claims: unknown[], runOverrides: Record<string, unknown> = {}) => ({ run: { ...RUN, ...runOverrides }, claims });

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("Company intel: loading, empty and a stored dossier", () => {
  it("shows a placeholder in the dossier's shape while the stored run is read, and no trail yet", () => {
    on(`GET ${PATH}`, () => new Promise(() => undefined));
    const { container } = panel();
    expect(query(container, "[role='status'][aria-busy='true']").textContent).toContain("Loading company intel...");
    expect(container.querySelector(".bj-trail")).toBeNull();
  });

  it("teaches what a run finds and what it needs, with a link to Settings, and shows no example row and no trail", async () => {
    on(`GET ${PATH}`, { run: null, claims: [] });
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-empty h3"))).toBe("No dossier yet");
    const body = text(query(container, ".bj-rs-empty"));
    for (const kind of ["product and mission", "funding", "hiring activity", "layoffs and risk", "interview process", "news"]) expect(body).toContain(kind);
    expect(body).toContain("It needs a model key and a You.com or Firecrawl key.");
    expect(query<HTMLAnchorElement>(container, ".bj-rs-empty a").getAttribute("href")).toBe("/settings");
    expect(container.querySelector(".bj-rs-item")).toBeNull();
    expect(container.querySelector(".bj-trail")).toBeNull();
    expect(buttonNamed(container, "Research this company")).toBeTruthy();
  });

  it("draws each claim with its category, its words, a source link that opens in a new tab and the model's confidence as gold cells and a word", async () => {
    on(`GET ${PATH}`, stored([claim(1), claim(2), claim(3)]));
    const { container } = panel();
    await settle();
    const items = [...container.querySelectorAll(".bj-rs-item")];
    expect(items).toHaveLength(3);
    expect(text(items[0].querySelector(".bj-rs-eyebrow")!)).toBe("Product & mission");
    expect(text(items[0].querySelector(".bj-rs-text")!)).toBe("Claim number 1 about Northwind.");
    const link = items[0].querySelector<HTMLAnchorElement>("a")!;
    expect(link.getAttribute("href")).toBe("https://news.example.com/story-1");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(text(link)).toContain("Story 1");
    expect(text(link)).toContain("(opens in a new tab)");
    // 3, 2 and 1 filled cells of 3, with the word; never an emerald badge.
    const marks = items.map((item) => ({ filled: item.querySelectorAll(".bj-segbar-cell--filled").length, of: item.querySelectorAll(".bj-segbar-cell").length, word: text(item.querySelector(".bj-rs-conf-word")!) }));
    expect(marks).toEqual([
      { filled: 3, of: 3, word: "Confidence: High" },
      { filled: 2, of: 3, word: "Confidence: Medium" },
      { filled: 1, of: 3, word: "Confidence: Low" },
    ]);
    expect(container.innerHTML).not.toContain("bj-badge-emerald");
  });

  it("says how old the run is from its own date, and draws a trail from the returned fields only", async () => {
    on(`GET ${PATH}`, stored([claim(1), claim(2)]));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-meta"))).toBe("Researched Sep 20 · 2 claims");
    expect(text(query(container, ".bj-trail"))).toBe("✦ Claims written by your model · from You.com results, on your keys · Sep 20");
    // The glyph is decoration; the words carry the meaning.
    expect(query(container, ".bj-trail-glyph").getAttribute("aria-hidden")).toBe("true");
  });

  it("leaves a slot out when its field did not come back: no provider, no readable date", async () => {
    on(`GET ${PATH}`, stored([claim(1)], { providers_used: [], created_at: "not a date" }));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-trail"))).toBe("✦ Claims written by your model · on your keys");
    expect(text(query(container, ".bj-rs-meta"))).toBe("1 claim");
    expect(container.textContent).not.toMatch(/unknown/i);
  });

  it("opens a receipt built from the run's fields", async () => {
    on(`GET ${PATH}`, stored([claim(1), claim(2)], { providers_used: ["you_com", "firecrawl"] }));
    const { container } = panel();
    await settle();
    const trigger = query<HTMLButtonElement>(container, "button.bj-trail-trigger");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const rows = [...container.querySelectorAll(".bj-receipt-row")].map((row) => [text(row.querySelector("dt")!), text(row.querySelector("dd")!)]);
    expect(rows).toEqual([
      ["company", "Northwind Labs"],
      ["searched with", "You.com and Firecrawl"],
      ["claims", "2"],
      ["run", "01234567"],
    ]);
    expect(text(query(container, ".bj-receipt-note"))).toContain("your model's own reading, not a check");
  });

  it("does not say a run makes one call to the model: a run with interview-process claims makes a second", async () => {
    on(`GET ${PATH}`, { run: null, claims: [] });
    const { container } = panel();
    await settle();
    const intro = text(query(container, ".bj-ws-intro"));
    expect(intro).not.toContain("one call");
    expect(intro).toContain("calls your model once more");
  });

  it("names a run with no claims, keeps its trail (it did run) and offers Refresh", async () => {
    on(`GET ${PATH}`, stored([]));
    const { container } = panel();
    await settle();
    expect(text(container)).toContain("No claims came back from that research pass.");
    expect(container.querySelector(".bj-trail")).not.toBeNull();
    expect(buttonNamed(container, "Refresh")).toBeTruthy();
  });

  it("does not say a model wrote claims, or that they came from results, for a run with none", async () => {
    const warnings = Array.from({ length: 7 }, (_, index) => `search ${index + 1} failed`);
    on(`GET ${PATH}`, stored([], { warnings }));
    const { container } = panel();
    await settle();
    const line = text(query(container, ".bj-trail"));
    expect(line).toBe("○ Searched with You.com · on your keys · Sep 20 · ✕ 7 searches failed or skipped");
    expect(line).not.toContain("by your model");
    expect(line).not.toContain("results");
  });

  it("never makes a link of an address that is not http or https", async () => {
    on(`GET ${PATH}`, stored([claim(1, { source_url: "javascript:alert(1)", source_title: "A page" }), claim(2, { source_url: "data:text/html,x", source_title: null })]));
    const { container } = panel();
    await settle();
    expect(container.querySelector("a[href^='javascript']")).toBeNull();
    expect(container.querySelector(".bj-rs-item a")).toBeNull();
    expect(text(container)).toContain("A page");
  });
});

describe("Company intel: a run that did not complete every search", () => {
  it("says how many searches failed or were skipped, in the server's own words, and carries the count on the trail", async () => {
    on(`GET ${PATH}`, stored([claim(1)], { warnings: ["Product: You.com didn't respond.", "News: skipped"] }));
    const { container } = panel();
    await settle();
    const partial = query(container, ".bj-state--partial");
    expect(text(query(partial, ".bj-state-lead"))).toBe("2 of this run's searches failed or were skipped:");
    expect([...partial.querySelectorAll("li")].map((item) => text(item))).toEqual(["Product: You.com didn't respond.", "News: skipped"]);
    expect(text(query(container, ".bj-trail"))).toContain("✕ 2 searches failed or skipped");
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
  });

  it("says it in the singular too", async () => {
    on(`GET ${PATH}`, stored([claim(1)], { warnings: ["Product: slow"] }));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-state-lead"))).toBe("1 of this run's searches failed or was skipped:");
    expect(text(query(container, ".bj-trail"))).toContain("1 search failed or skipped");
  });
});

describe("Company intel: a long dossier", () => {
  const eight = Array.from({ length: 8 }, (_, index) => claim(index + 1));

  it("opens on its first claims and says how many, with a disclosure that shows the rest and folds again", async () => {
    on(`GET ${PATH}`, stored(eight));
    const { container } = panel();
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(3);
    expect(text(query(container, ".bj-rs-more .bj-rs-meta"))).toBe("3 of 8 claims");
    // The status region says the run's own count whether the list is folded or not, so the disclosure
    // never rewrites it (a rewrite would be read out as if a run had finished).
    const live = query(container, ".bj-rs-live");
    expect(text(live)).toBe("Researched Sep 20 · 8 claims");
    const toggle = buttonNamed(container, "Show all 8");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBe(query(container, "ul.bj-rs-list").id);
    click(toggle);
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(8);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.textContent).toBe("Show fewer");
    expect(text(live)).toBe("Researched Sep 20 · 8 claims");
    click(toggle);
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(3);
    expect(text(live)).toBe("Researched Sep 20 · 8 claims");
  });

  it("does not collapse a short one, and the section itself never collapses", async () => {
    on(`GET ${PATH}`, stored(eight.slice(0, 5)));
    const { container } = panel();
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(5);
    expect(container.querySelector(".bj-rs-more")).toBeNull();
  });
});

describe("Company intel: running", () => {
  it("runs on press, busies only its button (aria-disabled, so focus stays), ignores a second press and keeps the earlier dossier up meanwhile", async () => {
    on(`GET ${PATH}`, stored([claim(1)]));
    let finish: (value: unknown) => void = () => undefined;
    on(`POST ${PATH}`, () => new Promise((resolve) => (finish = resolve)));
    const { container } = panel();
    await settle();
    const button = buttonNamed(container, "Refresh");
    button.focus();
    click(button);
    await settle(1);
    expect(button.textContent).toBe("Researching...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.hasAttribute("disabled")).toBe(false);
    expect(document.activeElement).toBe(button);
    click(button);
    expect(callsTo(`POST ${PATH}`)).toHaveLength(1);
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
    finish(stored([claim(1), claim(2), claim(3)], { created_at: "2026-09-22T11:00:00Z" }));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(3);
    expect(text(query(container, ".bj-rs-meta"))).toBe("Researched Sep 22 · 3 claims");
    expect(button.textContent).toBe("Refresh");
  });

  it("keeps the earlier dossier and its trail when a run fails, names the failure with a Retry that runs again", async () => {
    on(`GET ${PATH}`, stored([claim(1)]));
    on(`POST ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Refresh"));
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    expect(text(query(container, ".bj-trail"))).toContain("Claims written by your model");
    on(`POST ${PATH}`, stored([claim(1), claim(2)]));
    const retry = buttonNamed(container, "Retry");
    retry.focus();
    click(retry);
    await settle();
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(2);
    expect(callsTo(`POST ${PATH}`)).toHaveLength(2);
    // The Retry went with its callout: keyboard focus is on the run button, not dropped on the page body.
    expect(document.activeElement).toBe(buttonNamed(container, "Refresh"));
  });

  it("says what is missing with the link that fixes it, and offers no Retry, when a run needs a key", async () => {
    on(`GET ${PATH}`, { run: null, claims: [] });
    on(`POST ${PATH}`, new FakeSetupError("Company intel needs a You.com or Firecrawl key.", "/profile/integrations", "company_intel", ["you_com_or_firecrawl_credential"]));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Research this company"));
    await settle();
    const notice = query(container, ".bj-setup-notice");
    expect(text(notice)).toContain("Company intel needs a You.com or Firecrawl key.");
    const link = query<HTMLAnchorElement>(notice, "a");
    expect(link.getAttribute("href")).toBe("/settings");
    expect(text(link)).toBe("Add a You.com or Firecrawl key in Settings");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(false);
    // Setup is not a failure and produced nothing, so there is no trail.
    expect(container.querySelector(".bj-trail")).toBeNull();
  });

  it("puts ✕ on the block's own trail when a first run fails with nothing earlier to show", async () => {
    on(`GET ${PATH}`, { run: null, claims: [] });
    on(`POST ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Research this company"));
    await settle();
    expect(text(query(container, ".bj-trail"))).toBe("✕ That research run didn't finish");
  });
});

describe("Company intel: the stored run could not be read", () => {
  it("names it with a Retry that reads again, marks the block's trail and leaves the run button usable", async () => {
    on(`GET ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    expect(text(query(container, ".bj-trail"))).toBe("✕ Company intel couldn't be read");
    expect(buttonNamed(container, "Research this company").getAttribute("aria-disabled")).toBeNull();
    on(`GET ${PATH}`, stored([claim(1)]));
    const retry = buttonNamed(container, "Retry");
    retry.focus();
    click(retry);
    await settle();
    expect(container.querySelectorAll(".bj-rs-item")).toHaveLength(1);
    expect(callsTo(`GET ${PATH}`)).toHaveLength(2);
    expect(document.activeElement).toBe(buttonNamed(container, "Refresh"));
  });
});

describe("Company intel: the page's one cyan fill is the band's", () => {
  it("draws no primary button and no filled cyan in any state", async () => {
    on(`GET ${PATH}`, stored([claim(1), claim(2)], { warnings: ["x"] }));
    const { container } = panel();
    await settle();
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
  });
});

describe("Company intel: accessibility", () => {
  it.each([
    ["empty", { run: null, claims: [] }],
    ["a stored run", stored([claim(1), claim(2), claim(3), claim(4), claim(5), claim(6)], { warnings: ["Product: slow"] })],
  ])("has no serious violations (%s)", async (_name, answer) => {
    on(`GET ${PATH}`, answer);
    const { container } = panel();
    await settle();
    await expectNoSeriousA11yViolations(container);
  });

  it("is reachable by keyboard: every control is a native button or link in reading order", async () => {
    on(`GET ${PATH}`, stored(Array.from({ length: 6 }, (_, index) => claim(index + 1))));
    const { container } = panel();
    await settle();
    const controls = [...container.querySelectorAll<HTMLElement>("button, a[href]")];
    expect(controls.map((control) => control.tagName)).toEqual(expect.arrayContaining(["BUTTON", "A"]));
    expect(controls.every((control) => control.tabIndex >= 0)).toBe(true);
    expect(controls[0].textContent).toBe("Refresh");
  });
});
