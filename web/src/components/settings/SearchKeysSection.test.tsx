// @vitest-environment jsdom
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../../testing/fakeApi";
import { settle, submit, text, typeInto, visibleText } from "../../testing/forms";
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

function Harness({ open: startOpen = true, ...props }: Omit<Parameters<typeof SearchKeysSection>[0], "open" | "onOpenChange"> & { open?: boolean }) {
  const [open, setOpen] = useState(startOpen);
  return <SearchKeysSection {...props} open={open} onOpenChange={setOpen} />;
}

function mount(credentials: CredentialSummary[], hiringSignalsEnabled = false, onChanged = vi.fn(async () => undefined), open = true) {
  return { ...render(<Harness credentials={credentials} hiringSignalsEnabled={hiringSignalsEnabled} now={NOW} onChanged={onChanged} open={open} />), onChanged };
}

const row = (root: ParentNode, provider: string) => query(root, `#search-${provider}`);
const button = (root: ParentNode, label: string) => [...root.querySelectorAll("button")].find((b) => visibleText(b) === label || text(b) === label) as HTMLButtonElement;
const connect = (root: ParentNode, provider: string, title: string) => click(button(row(root, provider), `Connect ${title}`));

beforeEach(() => {
  reset();
});

describe("the disclosure", () => {
  it("is titled Optional extras, closed by default, with one line saying what they are", () => {
    const { container } = mount([], false, undefined, false);
    const heading = query(container, "#search-keys-h");
    expect(text(heading)).toBe("Optional extras›");
    expect(heading.tagName).toBe("H2");
    const toggle = query<HTMLButtonElement>(heading, "button");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(query(container, `#${CSS.escape(toggle.getAttribute("aria-controls") ?? "")}`).hasAttribute("hidden")).toBe(true);
    expect(text(query(container, ".bj-set-lede"))).toBe("Discover works without these. They add live job search, company and event research, and people lookup.");
  });

  it("is a real button inside the heading, so Enter and Space open it from the keyboard, and it opens and closes", () => {
    const { container } = mount([], false, undefined, false);
    const toggle = query<HTMLButtonElement>(container, "#search-keys-h button");
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.type).toBe("button");
    click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(query(container, `#${CSS.escape(toggle.getAttribute("aria-controls") ?? "")}`).hasAttribute("hidden")).toBe(false);
    click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps the section and heading ids the other pages link to, and the heading takes focus", () => {
    const { container } = mount([], false, undefined, false);
    expect(query(container, "section#search-keys").getAttribute("aria-labelledby")).toBe("search-keys-h");
    expect(query(container, "#search-keys-h").getAttribute("tabindex")).toBe("-1");
  });

  it("says nothing of hiring posts to a person who does not have Hiring signals, and does to one who does", () => {
    expect(text(mount([], false).container).toLowerCase()).not.toContain("hiring");
    const on = mount([], true);
    expect(text(query(on.container, ".bj-set-lede"))).toBe("Discover works without these. They add live job search, company and event research, people lookup and hiring posts.");
  });

  it("has no count of connected keys anywhere, and no setup score", () => {
    const { container } = mount([saved("brave"), saved("firecrawl")]);
    expect(container.querySelector(".bj-set-count")).toBeNull();
    expect(text(container)).not.toMatch(/\d+ of \d+|of \d+ connected/);
  });
});

describe("the grouping", () => {
  const titles = (container: ParentNode) => [...container.querySelectorAll(".bj-set-group-title")].map((h) => h.textContent);
  const inGroup = (container: ParentNode, title: string) =>
    [...(([...container.querySelectorAll(".bj-set-group")].find((g) => g.querySelector(".bj-set-group-title")?.textContent === title) as Element).querySelectorAll(".bj-set-extra-name"))].map((h) => h.textContent);

  it("groups every key by what it is for: live job search, then finding people", () => {
    const { container } = mount([]);
    expect(titles(container)).toEqual(["Live job search", "Find people"]);
    expect(inGroup(container, "Live job search")).toEqual(["You.com", "Firecrawl", "Brave Search", "Serper"]);
    expect(inGroup(container, "Find people")).toEqual(["Apollo", "Hunter", "Exa"]);
  });

  it("draws every provider the page has a card for exactly once", () => {
    const { container } = mount([]);
    expect([...container.querySelectorAll(".bj-set-extra")].map((li) => li.id).sort()).toEqual(SEARCH_PROVIDER_CARDS.map((card) => `search-${card.provider}`).sort());
  });

  it("adds a Hiring posts line, naming the keys it uses, only when Hiring signals is on for the person", () => {
    expect(titles(mount([], false).container)).not.toContain("Hiring posts");
    const { container } = mount([], true);
    expect(titles(container)).toEqual(["Live job search", "Find people", "Hiring posts"]);
    const group = [...container.querySelectorAll(".bj-set-group")][2];
    expect(text(group)).toContain("any one of Brave Search, Serper, Firecrawl or You.com is enough");
    expect(group.querySelector("input, button")).toBeNull();
  });
});

describe("the rows", () => {
  it("draw a row per provider in the order of the cards, by group", () => {
    const { container } = mount([]);
    const names = [...container.querySelectorAll(".bj-set-extra-name")].map((h) => h.textContent);
    expect(names.sort()).toEqual(SEARCH_PROVIDER_CARDS.map((card) => card.title).sort());
    expect(container.querySelectorAll(".bj-set-extra")).toHaveLength(SEARCH_PROVIDER_CARDS.length);
  });

  it("make a connected key one quiet line: the name, Connected, Replace key and Remove key", () => {
    const { container } = mount([saved("brave")]);
    const set = row(container, "brave");
    expect(text(query(set, ".bj-set-extra-line"))).toBe("Brave Search✓ ConnectedReplace key for Brave SearchRemove key for Brave Search");
    expect(button(set, "Replace key").classList.contains("bj-quiet")).toBe(true);
    expect(query(set, ".bj-set-extra-glyph").getAttribute("aria-hidden")).toBe("true");
  });

  it("tell two connected keys' buttons apart by name: Replace key and Remove key each end with the provider", () => {
    const { container } = mount([saved("brave"), saved("serper")]);
    const names = [...container.querySelectorAll("button")]
      .map((b) => text(b))
      .filter((name) => /^(Replace|Remove) key/.test(name))
      .sort();
    expect(names).toEqual(["Remove key for Brave Search", "Remove key for Serper", "Replace key for Brave Search", "Replace key for Serper"]);
    // the visible words are unchanged: the provider is said to a screen reader only
    const visible = [...row(container, "brave").querySelectorAll(".bj-set-extra-actions > button")].map((b) => visibleText(b));
    expect(visible).toEqual(["Replace key", "Remove key"]);
  });

  it("make a key that is not connected its name and a Connect text action, with no glyph, no trail and no form until it is pressed", () => {
    const { container } = mount([saved("brave")]);
    const notSet = row(container, "serper");
    expect(text(query(notSet, ".bj-set-extra-line"))).toBe("SerperConnect Serper");
    expect(button(notSet, "Connect Serper").classList.contains("bj-quiet")).toBe(true);
    expect(notSet.querySelector(".bj-trail, form")).toBeNull();
    expect(text(notSet)).not.toMatch(/[○✓◆]/);
    expect(text(notSet)).not.toContain("Connected");
  });

  it("give a connected key one trail line built from what was returned: who checked, how, and when", () => {
    const first = mount([saved("firecrawl")]);
    expect(text(query(row(first.container, "firecrawl"), ".bj-trail"))).toBe("○ Checked by code on save · a free credit-usage read · Sep 22");
    first.unmount();
    const second = mount([saved("exa")]);
    expect(text(query(row(second.container, "exa"), ".bj-trail"))).toBe("○ Checked by code on save · one small search call · Sep 22");
  });

  it("leave the date out of the trail when the row's date cannot be read, rather than guess one", () => {
    const { container } = mount([saved("brave", { updated_at: "" })]);
    expect(text(query(row(container, "brave"), ".bj-trail"))).toBe("○ Checked by code on save · one tiny search call");
  });

  it("show a connected key's not-yet-verified-live notice on the row, and an unconnected one's in its form", () => {
    const { container } = mount([saved("brave")]);
    expect(text(row(container, "brave"))).toContain("Not yet verified live");
    expect(text(row(container, "serper"))).not.toContain("Not yet verified live");
    connect(container, "serper", "Serper");
    expect(text(row(container, "serper"))).toContain("Not yet verified live");
  });

  it("say, in the form, what each key is for and what saving it costs", () => {
    const { container } = mount([]);
    connect(container, "firecrawl", "Firecrawl");
    expect(text(row(container, "firecrawl"))).toContain("doesn't spend a search/scrape credit");
    expect(text(row(container, "firecrawl"))).not.toContain("Not yet verified live");
    connect(container, "apollo", "Apollo");
    expect(text(row(container, "apollo"))).toContain("no run with a valid key is recorded");
    connect(container, "exa", "Exa");
    expect(text(row(container, "exa"))).toContain("small real charge");
  });

  it("mention hiring posts in the form only for a person Hiring signals is on for", () => {
    const off = mount([], false);
    connect(off.container, "brave", "Brave Search");
    expect(text(row(off.container, "brave")).toLowerCase()).not.toContain("hiring");
    off.unmount();
    const on = mount([], true);
    connect(on.container, "brave", "Brave Search");
    expect(text(row(on.container, "brave")).toLowerCase()).toContain("hiring posts");
  });

  it("link, in the form, to each provider's own page for a key, opening in a new tab without handing over the opener", () => {
    const { container } = mount([saved("serper")]);
    connect(container, "brave", "Brave Search");
    const get = query<HTMLAnchorElement>(row(container, "brave"), "a");
    expect(get.href).toBe("https://api-dashboard.search.brave.com/documentation/guides/authentication");
    expect(get.target).toBe("_blank");
    expect(get.rel).toBe("noopener noreferrer");
    expect(text(get)).toContain("Get a key at Brave Search");
    click(button(row(container, "serper"), "Replace key"));
    expect(text(query(row(container, "serper"), "form a"))).toContain("Get a key at Serper");
  });
});

describe("saving a key", () => {
  it("opens a masked field from Connect, sends the key to the API, empties and closes the field, and re-reads", async () => {
    on("POST /credentials", saved("brave"));
    const { container, onChanged } = mount([]);
    connect(container, "brave", "Brave Search");
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
    connect(container, "brave", "Brave Search");
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
    connect(container, "brave", "Brave Search");
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
    connect(container, "brave", "Brave Search");
    const form = query<HTMLFormElement>(row(container, "brave"), "form");
    expect(button(form, "Validate and save").disabled).toBe(true);
    typeInto(query<HTMLInputElement>(form, 'input[autocomplete="off"]'), KEY);
    click(button(form, "Cancel"));
    expect(row(container, "brave").querySelector("form")).toBeNull();
    connect(container, "brave", "Brave Search");
    expect(query<HTMLInputElement>(row(container, "brave"), 'input[autocomplete="off"]').value).toBe("");
  });

  it("has no cyan button anywhere in the section", () => {
    const { container } = mount([saved("brave")]);
    connect(container, "serper", "Serper");
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

  it("keeps the keyboard in the row after the key is removed and the list is re-read, on the row's own button", async () => {
    on("DELETE /credentials/search/brave", undefined);
    // The page re-reads the keys after a removal, and the confirmation that had focus unmounts with it.
    function Page() {
      const [credentials, setCredentials] = useState([saved("brave")]);
      return <Harness credentials={credentials} hiringSignalsEnabled={false} now={NOW} onChanged={async () => setCredentials([])} />;
    }
    const { container } = render(<Page />);
    click(button(row(container, "brave"), "Remove key"));
    click(button(row(container, "brave"), "Remove key"));
    await settle();
    expect(callsTo("DELETE /credentials/search/brave")).toHaveLength(1);
    expect(button(row(container, "brave"), "Connect Brave Search")).toBeDefined();
    expect(document.activeElement).toBe(button(row(container, "brave"), "Connect Brave Search"));
  });

  it("is offered for a saved key only", () => {
    const { container } = mount([saved("brave")]);
    expect(button(row(container, "brave"), "Remove key")).toBeDefined();
    expect(button(row(container, "serper"), "Remove key")).toBeUndefined();
  });
});

describe("accessibility", () => {
  it("has no serious violation closed, open, or with a form open", async () => {
    const closed = mount([saved("brave")], true, undefined, false);
    await expectNoSeriousA11yViolations(closed.container);
    closed.unmount();
    const { container } = mount([saved("brave")], true);
    await expectNoSeriousA11yViolations(container);
    connect(container, "serper", "Serper");
    await expectNoSeriousA11yViolations(container);
    click(button(row(container, "brave"), "Replace key"));
    await expectNoSeriousA11yViolations(container);
  });
});
