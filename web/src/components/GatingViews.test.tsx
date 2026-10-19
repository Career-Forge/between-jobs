import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApplicationActionsSelect } from "./ApplicationActionsSelect";
import { PrimaryNav } from "./PrimaryNav";

// What each answer to "is Hiring signals on?" actually puts in front of a person,
// rendered rather than reasoned about. `navItemsFor`, `crossNavItemsFor` and the
// status store are tested on their own; what these pin is the JSX that APPLIES
// them -- the nav and the Applications "Actions..." menu -- because a component that forgot to apply
// the filter would pass every one of those tests and still show a dead link. The workspace's Hiring
// posts section follows the same answer in components/workspace/PanelSections.test.tsx.
//
// The status hook is replaced by a variable the test sets (a static render has no
// network); the real client is never reached (nothing here may fetch).

const status = vi.hoisted(() => ({ enabled: false }));
vi.mock("../lib/useHiringSignalsStatus", () => ({
  useHiringSignalsEnabled: () => status.enabled,
}));
vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));

function nav(enabled: boolean): string {
  status.enabled = enabled;
  return renderToStaticMarkup(
    <MemoryRouter>
      <PrimaryNav />
    </MemoryRouter>,
  );
}

function menu(enabled: boolean): string {
  status.enabled = enabled;
  return renderToStaticMarkup(
    <MemoryRouter>
      <ApplicationActionsSelect applicationId="app-1" title="Data Engineer" />
    </MemoryRouter>,
  );
}

// Each link as "label href". The nav links carry an icon (a hidden SVG), so the label is the
// text left once the tags are dropped.
function links(markup: string): string[] {
  return [...markup.matchAll(/<a [^>]*href="([^"]+)"[^>]*>(.*?)<\/a>/g)].map(
    (m) => `${m[2].replace(/<[^>]+>/g, "")} ${m[1]}`,
  );
}

describe("the primary navigation", () => {
  it("has no Hiring signals link while the feature is off or not yet known", () => {
    const off = links(nav(false));
    expect(off).toEqual([
      "Today /",
      "Discover /discover",
      "Applications /applications",
      "Practice /practice",
      "Profile /profile",
    ]);
    expect(nav(false)).not.toContain("hiring-signals");
  });

  it("has it, between Discover and Applications, once the feature is known to be on", () => {
    expect(links(nav(true))).toEqual([
      "Today /",
      "Discover /discover",
      "Hiring signals /hiring-signals",
      "Applications /applications",
      "Practice /practice",
      "Profile /profile",
    ]);
  });
});

describe("the Applications 'Actions...' menu", () => {
  const options = (markup: string) =>
    [...markup.matchAll(/<option[^>]*>([^<]*)<\/option>/g)].map((m) => m[1]);

  it("offers no jump to Hiring posts while the feature is off or not yet known", () => {
    const off = options(menu(false));
    expect(off[0]).toBe("Actions...");
    expect(off).not.toContain("Find Hiring Posts");
    expect(off).toContain("Generate Docs"); // ... and everything else stays
    expect(menu(false)).not.toContain("hiring-posts");
  });

  it("offers it once the feature is known to be on", () => {
    const on = options(menu(true));
    expect(on[0]).toBe("Actions...");
    expect(on).toContain("Find Hiring Posts");
    expect(menu(true)).toContain('value="hiring-posts"');
  });

  it("is named for the application it belongs to", () => {
    expect(menu(true)).toContain('aria-label="Actions for &quot;Data Engineer&quot;"');
    status.enabled = true;
    expect(
      renderToStaticMarkup(
        <MemoryRouter>
          <ApplicationActionsSelect applicationId="app-1" title={null} />
        </MemoryRouter>,
      ),
    ).toContain("Actions for &quot;Untitled&quot;");
  });
});
