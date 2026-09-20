import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { expand } from "../testing/reactTree";
import { ApplicationActionsSelect } from "./ApplicationActionsSelect";
import { HiringPostsSlot } from "./HiringPostsSlot";
import { PrimaryNav } from "./PrimaryNav";

// What each answer to "is Hiring signals on?" actually puts in front of a person,
// rendered rather than reasoned about. `navItemsFor`, `crossNavItemsFor` and the
// status store are tested on their own; what these pin is the JSX that APPLIES
// them -- the nav, the Applications "Actions..." menu, and the workspace's Hiring
// posts slot -- because a component that forgot to apply the filter would pass
// every one of those tests and still show a dead link.
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

function links(markup: string): string[] {
  return [...markup.matchAll(/<a [^>]*href="([^"]+)"[^>]*>([^<]*)<\/a>/g)].map(
    (m) => `${m[2]} ${m[1]}`,
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

describe("the workspace's Hiring posts slot", () => {
  // The panel renders nothing until its first saved-posts reply, so its own markup
  // cannot tell "mounted" from "not mounted". The ELEMENT tree can: `expand` calls
  // the (hook-free) slot and leaves the panel, which uses hooks, as an opaque leaf.
  function slot(enabled: boolean) {
    const [root] = expand(<HiringPostsSlot enabled={enabled} applicationId="app-1" />);
    if (typeof root !== "object" || root.kind !== "host") throw new Error("no wrapper");
    return root;
  }

  it("keeps the hash target but mounts no panel while the feature is off or not yet known", () => {
    const off = slot(false);
    expect(off.type).toBe("div");
    expect(off.props.id).toBe("hiring-posts"); // the target exists ...
    expect(off.children).toEqual([]); // ... and nothing is in it
    expect(renderToStaticMarkup(<HiringPostsSlot enabled={false} applicationId="app-1" />)).toBe(
      '<div id="hiring-posts"></div>',
    );
  });

  it("mounts the panel for the right application once the feature is known to be on", () => {
    const on = slot(true);
    expect(on.props.id).toBe("hiring-posts");
    // exactly one thing inside, and it is a component (the panel), not markup
    expect(on.children).toHaveLength(1);
    expect(typeof on.children[0]).toBe("object");
    expect((on.children[0] as { kind: string }).kind).toBe("component");
    const element = (
      HiringPostsSlot({ enabled: true, applicationId: "app-9" }) as {
        props: { children: { props: { applicationId: string } } };
      }
    ).props.children;
    expect(element.props.applicationId).toBe("app-9");
  });
});
