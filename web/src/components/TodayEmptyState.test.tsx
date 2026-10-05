import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { TodayEmptyState } from "./TodayEmptyState";

function visibleText(html: string): string {
  return html
    .replace(/<[^>]*>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .replace(/ ([:,.])/g, "$1");
}

describe("the Today empty state", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter>
      <TodayEmptyState />
    </MemoryRouter>,
  );

  it("starts with the profile, the first step", () => {
    const text = visibleText(html);
    const start = text.indexOf("Start with your profile");
    expect(start).toBeGreaterThan(-1);
    // Nothing about tracking, generating or saving comes before it.
    expect(text.slice(0, start)).not.toMatch(/track|generate|save/i);
    expect(html.indexOf('href="/profile"')).toBeLessThan(html.indexOf('href="/profile/integrations"'));
    expect(html.indexOf('href="/profile/integrations"')).toBeLessThan(html.indexOf('href="/discover"'));
  });

  it("links to the profile, then Integrations, then Discover, by their real routes", () => {
    expect(html).toContain('href="/profile"');
    expect(html).toContain('href="/profile/integrations"');
    expect(html).toContain('href="/discover"');
  });

  it("still says what Today will show, and what it does not yet", () => {
    const text = visibleText(html);
    expect(text).toContain("Today shows what actually happened");
    expect(text).toContain("doesn't yet cover interview prep");
  });
});
