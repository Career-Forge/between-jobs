import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { navItemsFor } from "../lib/nav";
import { tagsOf, textOfMarkup } from "../testing/markup";
import { AppShell } from "./AppShell";
import { AssistantDrawerSlot, CommandPaletteSlot } from "./ReservedSlots";
import { MAIN_CONTENT_ID, SkipLink } from "./SkipLink";

// The signed-in frame, rendered to markup: the rail and the phone's tab bar must offer the same
// destinations and mark the page you are on, the skip link must be the first stop and point at the
// one <main>, and the places reserved for the assistant must be inert. CSS decides which of rail
// and tab bar a screen sees (shellStyles.test.ts); both are always in the markup.

const status = vi.hoisted(() => ({ enabled: false }));
vi.mock("../lib/useHiringSignalsStatus", () => ({ useHiringSignalsEnabled: () => status.enabled }));
vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));

const EMAIL = "someone@example.test";

function shell(path: string, hiringSignals = false): string {
  status.enabled = hiringSignals;
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <AppShell email={EMAIL} onSignOut={() => undefined}>
        <h1>Page</h1>
      </AppShell>
    </MemoryRouter>,
  );
}

// One element's markup, by an attribute it carries: the rail nav, the tab bar, the More sheet.
function section(markup: string, tag: string, marker: string): string {
  const match = new RegExp(`<${tag}\\b[^>]*${marker}[^>]*>[\\s\\S]*?</${tag}>`).exec(markup);
  if (match === null) throw new Error(`no <${tag}> with ${marker}`);
  return match[0];
}
const railNav = (markup: string): string => section(markup, "nav", 'class="bj-rail-nav"');
const tabBar = (markup: string): string => section(markup, "nav", 'class="bj-tabbar"');
const moreSheet = (markup: string): string => section(markup, "dialog", 'class="bj-more-sheet"');
const railFooter = (markup: string): string => markup.slice(markup.indexOf('<div class="bj-nav-footer">'), markup.indexOf("</main>"));

// Each link as { label, href, current }, the label being the visible text (the icon is a hidden SVG).
function linksOf(markup: string): { label: string; href: string; current: string | undefined }[] {
  return [...markup.matchAll(/<a ([^>]*)>([\s\S]*?)<\/a>/g)].map((match) => ({
    label: textOfMarkup(match[2]),
    href: /href="([^"]*)"/.exec(match[1])?.[1] ?? "",
    current: /aria-current="([^"]*)"/.exec(match[1])?.[1],
  }));
}

describe.each([false, true])("the shell, with Hiring signals on = %s", (hiringSignals) => {
  const markup = shell("/discover", hiringSignals);
  const expected = navItemsFor(hiringSignals);

  it("shows the rail every destination, in the order of the nav data, as links", () => {
    expect(linksOf(railNav(markup)).map(({ label, href }) => [label, href])).toEqual(expected.map((item) => [item.label, item.to]));
    expect(expected.some((item) => item.label === "Hiring signals")).toBe(hiringSignals);
  });

  it("gives the phone the same destinations: four tabs and a More sheet that holds the rest and Settings", () => {
    const tabs = linksOf(tabBar(markup));
    expect(tabs.map((tab) => tab.label)).toEqual(["Today", "Discover", "Applications", "Profile"]);
    const more = linksOf(moreSheet(markup));
    expect(more.map((link) => link.label)).toEqual([...(hiringSignals ? ["Hiring signals"] : []), "Practice", "Settings", "Privacy Policy", "Terms of Service"]);

    const everyPhoneDestination = [...tabs, ...more.filter((link) => link.label !== "Privacy Policy" && link.label !== "Terms of Service")]
      .map((link) => link.href)
      .filter((href) => href !== "/profile/integrations");
    expect([...everyPhoneDestination].sort()).toEqual(expected.map((item) => item.to).sort());
  });

  it("labels the tab bar and the rail's navigation 'Main' (CSS shows one), and labels the rest", () => {
    const labels = tagsOf(markup, "nav").map((tag) => tag["aria-label"]);
    expect(labels.filter((label) => label === "Main")).toHaveLength(2);
    expect(labels.every((label) => typeof label === "string" && label.length > 0)).toBe(true);
    expect(labels).toEqual(expect.arrayContaining(["Main", "More", "Legal"]));
  });
});

describe("the page you are on", () => {
  const current = (markup: string): string[] => linksOf(markup).filter((link) => link.current === "page").map((link) => link.label);

  it.each([
    ["/", "Today"],
    ["/discover", "Discover"],
    ["/applications", "Applications"],
    ["/applications/abc-123", "Applications"],
    ["/practice", "Practice"],
    ["/profile", "Profile"],
  ])("%s marks %s, and only that, in the rail", (path, label) => {
    expect(current(railNav(shell(path)))).toEqual([label]);
    expect(current(railFooter(shell(path)))).toEqual([]);
  });

  it("marks the same destination in the tab bar, and leaves More unmarked", () => {
    expect(current(tabBar(shell("/applications")))).toEqual(["Applications"]);
    expect(current(tabBar(shell("/")))).toEqual(["Today"]);
    expect(tabBar(shell("/practice"))).not.toContain("aria-current");
    const more = /<button[^>]*>(?:(?!<\/button>)[\s\S])*More<\/span><\/button>/.exec(tabBar(shell("/")))?.[0] ?? "";
    expect(more).toContain('aria-haspopup="dialog"');
    expect(more).not.toContain("aria-current");
  });

  it("marks Hiring signals when it is on and the page is open", () => {
    expect(current(railNav(shell("/hiring-signals", true)))).toEqual(["Hiring signals"]);
  });

  it("marks Settings, not Profile, on the integrations page, which Settings links to", () => {
    const markup = shell("/profile/integrations");
    expect(current(railNav(markup))).toEqual([]);
    expect(current(railFooter(markup))).toEqual(["Settings"]);
    expect(linksOf(railFooter(markup)).find((link) => link.label === "Settings")?.href).toBe("/profile/integrations");
    expect(current(moreSheet(markup))).toEqual(["Settings"]);
  });

  it("uses the selection class on every navigation link, so the fill comes from one rule", () => {
    for (const nav of [railNav(shell("/")), tabBar(shell("/")), moreSheet(shell("/"))]) {
      const anchors = [...nav.matchAll(/<a [^>]*>/g)].map((m) => m[0]).filter((a) => !a.includes("/privacy") && !a.includes("/terms"));
      expect(anchors.length).toBeGreaterThan(0);
      for (const a of anchors) expect(a).toContain("bj-selectable");
    }
  });
});

describe("landmarks and the way in", () => {
  const markup = shell("/");

  it("has one <main>, which the skip link targets, and the skip link comes first", () => {
    expect(tagsOf(markup, "main")).toHaveLength(1);
    expect(tagsOf(markup, "main")[0]).toMatchObject({ id: MAIN_CONTENT_ID, tabindex: "-1" });
    const skip = /<a [^>]*class="bj-skip-link"[^>]*>([^<]*)<\/a>/.exec(markup);
    expect(skip?.[1]).toBe("Skip to content");
    expect(skip?.[0]).toContain(`href="#${MAIN_CONTENT_ID}"`);
    expect(markup.indexOf("bj-skip-link")).toBeLessThan(markup.indexOf("<header"));
    expect(markup.indexOf("bj-skip-link")).toBeLessThan(markup.indexOf("<a ", markup.indexOf("bj-skip-link") + 10));
  });

  it("renders the page inside <main> and nothing else of the page outside it", () => {
    const main = markup.slice(markup.indexOf("<main"), markup.indexOf("</main>"));
    expect(main).toContain("<h1>Page</h1>");
    expect(markup.replace(main, "")).not.toContain("<h1>");
  });

  it("names the wordmark in the rail and on the phone's top bar, as a link to Today", () => {
    const brands = [...markup.matchAll(/<a [^>]*class="(?:bj-rail-brand|bj-topbar-brand)"[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g)];
    expect(brands).toHaveLength(2);
    for (const [, href, inner] of brands) {
      expect(href).toBe("/");
      expect(textOfMarkup(inner)).toBe("Between Jobs");
    }
  });

  it("hides every icon from assistive technology: each sits beside a visible label", () => {
    const svgs = [...markup.matchAll(/<svg [^>]*>/g)].map((m) => m[0]);
    expect(svgs.length).toBeGreaterThan(8);
    for (const svg of svgs) {
      expect(svg).toContain('aria-hidden="true"');
      expect(svg).toContain('focusable="false"');
    }
  });

  it("keeps the signed-in address, Settings, Sign out and the legal links in the rail's footer, in that order", () => {
    const footer = railFooter(markup);
    const order = [EMAIL, "Settings", "Sign out", "Privacy Policy"].map((text) => footer.indexOf(text));
    expect(order.every((i) => i > -1)).toBe(true);
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it("offers Sign out and a Close button (labelled) in the More sheet, which is a dialog named 'More'", () => {
    const sheet = moreSheet(markup);
    expect(sheet).toContain('aria-labelledby="bj-more-title"');
    expect(sheet).toMatch(/<h2 id="bj-more-title">More<\/h2>/);
    expect(sheet).toMatch(/<button[^>]*aria-label="Close"/);
    expect(sheet).toContain("Sign out");
    expect(sheet).toContain(EMAIL);
  });
});

describe("the reserved assistant slots", () => {
  it("draw the palette row dashed, labelled, hidden from assistive technology and inert", () => {
    const markup = renderToStaticMarkup(<CommandPaletteSlot />);
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).toContain("inert");
    expect(markup).toContain("Command palette -- reserved");
    expect(markup).not.toMatch(/<(a|button|input|select|textarea)\b|tabindex|onclick|accesskey/i);
  });

  it("render the drawer as an empty, hidden, inert element", () => {
    const markup = renderToStaticMarkup(<AssistantDrawerSlot />);
    expect(markup).toMatch(/^<div [^>]*\bhidden\b/);
    expect(markup).toContain("inert");
    expect(markup).toContain('aria-hidden="true"');
    expect(textOfMarkup(markup)).toBe("");
  });

  it("sit in the shell: the row between the wordmark and the navigation, the drawer after the page", () => {
    const markup = shell("/");
    expect(markup.indexOf("bj-rail-brand")).toBeLessThan(markup.indexOf("bj-palette-slot"));
    expect(markup.indexOf("bj-palette-slot")).toBeLessThan(markup.indexOf("bj-rail-nav"));
    expect(markup.indexOf("</main>")).toBeLessThan(markup.indexOf("bj-assistant-slot"));
  });
});

describe("SkipLink", () => {
  it("is a plain link to the main landmark, so it works before any script has run", () => {
    expect(renderToStaticMarkup(<SkipLink />)).toBe(`<a class="bj-skip-link" href="#${MAIN_CONTENT_ID}">Skip to content</a>`);
  });
});
