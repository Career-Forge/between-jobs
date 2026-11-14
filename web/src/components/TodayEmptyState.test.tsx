import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { KIND_LABELS } from "../lib/todayFeed";
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
    expect(html.indexOf('href="/profile"')).toBeLessThan(html.indexOf('href="/settings#ai-provider"'));
    expect(html.indexOf('href="/settings#ai-provider"')).toBeLessThan(html.indexOf('href="/discover"'));
  });

  it("links to the profile, then Settings, then Discover, by their real routes", () => {
    expect(html).toContain('href="/profile"');
    expect(html).toContain('href="/settings#ai-provider"');
    expect(html).toContain('href="/discover"');
  });

  // The kinds that are listed are pinned, exactly, where the empty state is drawn inside the page
  // (TodayView.test.tsx). What this pins is what is left out.
  it("names no saved search and no high-fit kind", () => {
    const text = visibleText(html);
    expect(text).not.toMatch(/saved search/i);
    expect(text).not.toContain(KIND_LABELS.high_fit_job);
  });

  it("still says what Today will show, and what it does not yet", () => {
    const text = visibleText(html);
    expect(text).toContain("Today shows what actually happened");
    expect(text).toContain("doesn't yet cover interview prep");
  });
});
