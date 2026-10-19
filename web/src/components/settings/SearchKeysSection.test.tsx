// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../../testing/fakeApi";
import { settle, submit, text, typeInto } from "../../testing/forms";
import { click, query, render } from "../../testing/render";
import { SEARCH_PROVIDER_CARDS } from "../../lib/providerStatus";
import type { CredentialSummary } from "../../lib/settings/credentials";
import { SearchKeysSection } from "./SearchKeysSection";

vi.mock("../../lib/api", async () => (await import("../../testing/fakeApi")).apiModule);

const NOW = new Date("2026-10-20T12:00:00Z");
const KEY = "bk-" + "q".repeat(30);

function saved(provider: string, extra: Partial<CredentialSummary> = {}): CredentialSummary {
  return { id: `id-${provider}`, service: "search", provider, model: null, is_validated: true, updated_at: "2026-09-22T11:00:00Z", scope: null, ...extra };
}

function mount(credentials: CredentialSummary[], hiringSignalsEnabled = false, onChanged = vi.fn(async () => undefined)) {
  return { ...render(<SearchKeysSection credentials={credentials} hiringSignalsEnabled={hiringSignalsEnabled} now={NOW} onChanged={onChanged} />), onChanged };
}

const row = (root: ParentNode, provider: string) => query(root, `#search-${provider}`);
const button = (root: ParentNode, label: string) => [...root.querySelectorAll("button")].find((b) => text(b) === label) as HTMLButtonElement;

beforeEach(() => {
  reset();
});

describe("the rows", () => {
  it("count what is connected, out of the rows the page has, and nothing else", () => {
    const { container } = mount([saved("brave"), saved("firecrawl"), saved("jsearch"), { ...saved("openai"), service: "llm" }]);
    // jsearch has no row on this page and openai is a model key: neither is counted
    expect(text(query(container, ".bj-set-count"))).toBe(`2 of ${SEARCH_PROVIDER_CARDS.length} connected`);
    expect(container.querySelectorAll(".bj-set-item")).toHaveLength(SEARCH_PROVIDER_CARDS.length);
  });

  it("draws a row per provider in the order the registry has them", () => {
    const { container } = mount([]);
    expect([...container.querySelectorAll(".bj-set-row-title h3")].map((h) => h.textContent)).toEqual(SEARCH_PROVIDER_CARDS.map((card) => card.title));
  });

  it("says Connected for a saved key and the words Not set, with no glyph and no trail, for one that is not", () => {
    const { container } = mount([saved("brave")]);
    const set = row(container, "brave");
    expect(text(query(set, ".bj-badge-emerald"))).toBe("✓ Connected");
    const notSet = row(container, "serper");
    expect(text(query(notSet, ".bj-badge-muted"))).toBe("Not set");
    expect(notSet.querySelector(".bj-trail")).toBeNull();
    expect(text(notSet)).not.toMatch(/[○✓◆]/);
  });

  it("gives a connected key one trail line built from what was returned: who checked, how, and when", () => {
    const first = mount([saved("firecrawl")]);
    expect(text(query(row(first.container, "firecrawl"), ".bj-trail"))).toBe("○ Checked by code on save · a free credit-usage read · Sep 22");
    first.unmount();
    const second = mount([saved("exa")]);
    expect(text(query(row(second.container, "exa"), ".bj-trail"))).toBe("○ Checked by code on save · one small search call · Sep 22");
  });

  it("leaves the date out of the trail when the row's date cannot be read, rather than guess one", () => {
    const { container } = mount([saved("brave", { updated_at: "" })]);
    expect(text(query(row(container, "brave"), ".bj-trail"))).toBe("○ Checked by code on save · one tiny search call");
  });

  it("says what each key is for and what saving it costs, and which integrations have not been seen working", () => {
    const { container } = mount([]);
    expect(text(row(container, "firecrawl"))).toContain("doesn't spend a search/scrape credit");
    expect(text(row(container, "firecrawl"))).not.toContain("Not yet verified live");
    expect(text(row(container, "brave"))).toContain("Not yet verified live");
    expect(text(row(container, "apollo"))).toContain("no run with a valid key is recorded");
  });

  it("mentions hiring posts on Brave and Serper only for a person Hiring signals is on for", () => {
    expect(text(mount([], false).container).toLowerCase()).not.toContain("hiring");
    expect(text(mount([], true).container).toLowerCase()).toContain("hiring posts");
  });

  it("links to each provider's own page for a key, opening in a new tab without handing over the opener", () => {
    const { container } = mount([saved("serper")]);
    const get = [...row(container, "brave").querySelectorAll("a")][0];
    expect(get.href).toBe("https://api-dashboard.search.brave.com/documentation/guides/authentication");
    expect(get.target).toBe("_blank");
    expect(get.rel).toBe("noopener noreferrer");
    expect(text(get)).toContain("Get a key");
    expect(text([...row(container, "serper").querySelectorAll("a")][0])).toContain("Open key page");
  });
});

describe("saving a key", () => {
  it("opens a masked field from Add key, sends the key to the API, empties and closes the field, and re-reads", async () => {
    on("POST /credentials", saved("brave"));
    const { container, onChanged } = mount([]);
    click(button(row(container, "brave"), "Add key"));
    const form = query<HTMLFormElement>(row(container, "brave"), "form");
    const input = query<HTMLInputElement>(form, 'input[autocomplete="off"]');
    expect(input.type).toBe("password");
    typeInto(input, KEY);
    submit(form);
    await settle();
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "search", provider: "brave", secret: KEY });
    expect(onChanged).toHaveBeenCalled();
    expect(row(container, "brave").querySelector("form")).toBeNull();
    expect(container.innerHTML).not.toContain(KEY);
  });

  it("says in the provider's name why a key was refused, and keeps the field", async () => {
    on("POST /credentials", new FakeApiError("Brave rejected that key.", "PROVIDER_REJECTED", undefined, 400));
    const { container } = mount([]);
    click(button(row(container, "brave"), "Add key"));
    const form = query<HTMLFormElement>(row(container, "brave"), "form");
    typeInto(query<HTMLInputElement>(form, 'input[autocomplete="off"]'), KEY);
    submit(form);
    await settle();
    expect(text(query(row(container, "brave"), '[role="alert"]'))).toBe("Brave rejected that key.");
    expect(query<HTMLInputElement>(form, 'input[autocomplete="off"]').value).toBe(KEY);
  });

  it("keeps focus in the key field and on the save button while the key is checked: read-only and aria-disabled, never disabled", async () => {
    let calls = 0;
    on("POST /credentials", () => {
      calls += 1;
      return new Promise(() => undefined);
    });
    const { container } = mount([]);
    click(button(row(container, "brave"), "Add key"));
    const form = query<HTMLFormElement>(row(container, "brave"), "form");
    const input = query<HTMLInputElement>(form, 'input[autocomplete="off"]');
    typeInto(input, KEY);
    input.focus();
    submit(form);
    await settle(1);
    expect(input.readOnly).toBe(true);
    expect(input.disabled).toBe(false);
    expect(document.activeElement).toBe(input);
    const save = button(form, "Validating...");
    expect(save.disabled).toBe(false);
    expect(save.getAttribute("aria-disabled")).toBe("true");
    submit(form);
    expect(calls).toBe(1);
  });

  it("will not send an empty key, and Cancel clears what was typed", async () => {
    const { container } = mount([]);
    click(button(row(container, "brave"), "Add key"));
    const form = query<HTMLFormElement>(row(container, "brave"), "form");
    expect(button(form, "Validate and save").disabled).toBe(true);
    typeInto(query<HTMLInputElement>(form, 'input[autocomplete="off"]'), KEY);
    click(button(form, "Cancel"));
    expect(row(container, "brave").querySelector("form")).toBeNull();
    click(button(row(container, "brave"), "Add key"));
    expect(query<HTMLInputElement>(row(container, "brave"), 'input[autocomplete="off"]').value).toBe("");
  });

  it("has no cyan button anywhere in the section", () => {
    const { container } = mount([saved("brave")]);
    click(button(row(container, "serper"), "Add key"));
    expect(container.querySelector(".bj-primary")).toBeNull();
  });
});

describe("removing a key", () => {
  it("asks first, then deletes it by its provider and re-reads", async () => {
    on("DELETE /credentials/search/brave", undefined);
    const { container, onChanged } = mount([saved("brave")]);
    click(button(row(container, "brave"), "Remove key"));
    expect(text(row(container, "brave"))).toContain("It stays valid at Brave Search until you delete it there.");
    expect(callsTo("DELETE /credentials/search/brave")).toHaveLength(0);
    click(button(row(container, "brave"), "Remove key"));
    await settle();
    expect(callsTo("DELETE /credentials/search/brave")).toHaveLength(1);
    expect(onChanged).toHaveBeenCalled();
  });

  it("is offered for a saved key only", () => {
    const { container } = mount([saved("brave")]);
    expect(button(row(container, "brave"), "Remove key")).toBeDefined();
    expect(button(row(container, "serper"), "Remove key")).toBeUndefined();
  });
});

describe("accessibility", () => {
  it("has no serious violation, closed or with a form open", async () => {
    const { container } = mount([saved("brave")]);
    await expectNoSeriousA11yViolations(container);
    click(button(row(container, "serper"), "Add key"));
    await expectNoSeriousA11yViolations(container);
  });
});
