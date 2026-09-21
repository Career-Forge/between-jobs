import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { CanonicalProfile } from "../lib/profileTypes";
import { ActiveProfile } from "./Profile";

// The real API client pulls in the Supabase client, which throws at import
// time without env vars (same reasoning as HiringSignalsPanel.test.tsx) --
// nothing in a static render should reach a network anyway.
vi.mock("../lib/api", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

// Everything ActiveProfile composes besides the new "Personal details" card
// is its own, separately-tested surface (HeaderComposer, SectionOrderEditor,
// ShapeSettingsPanel, SectionedProfile each have their own tests elsewhere
// in this codebase, or predate this session's testing conventions). This
// file's job is narrower: does adding PersonalDetailsCard leave everything
// else on the page exactly as it was, wired exactly as it was.
const captured = vi.hoisted(() => ({
  headerComposer: [] as unknown[],
  sectionOrderEditor: [] as unknown[],
  shapeSettingsPanel: [] as unknown[],
  sectionedProfile: [] as unknown[],
}));

vi.mock("../components/HeaderComposer", () => ({
  HeaderComposer: (props: unknown) => {
    captured.headerComposer.push(props);
    return null;
  },
}));
vi.mock("../components/SectionOrderEditor", () => ({
  SectionOrderEditor: (props: unknown) => {
    captured.sectionOrderEditor.push(props);
    return null;
  },
}));
vi.mock("../components/ShapeSettingsPanel", () => ({
  ShapeSettingsPanel: (props: unknown) => {
    captured.shapeSettingsPanel.push(props);
    return null;
  },
}));
vi.mock("../components/SectionedProfile", () => ({
  SectionedProfile: (props: unknown) => {
    captured.sectionedProfile.push(props);
    return null;
  },
}));

function profile(overrides: Partial<CanonicalProfile["personal"]> = {}): CanonicalProfile {
  return {
    personal: { name: "Asha Verma", headline: "Backend Engineer", ...overrides },
    experience: [
      { title: "Engineer", company: "Example Corp", start_date: "2022-01", end_date: "present" },
    ],
  };
}

function render(canonical_json: CanonicalProfile) {
  return renderToStaticMarkup(
    <ActiveProfile
      version={{ id: "v1", canonical_json, activated_at: "2026-09-01T00:00:00Z", version_count: 3 }}
      onEdited={() => Promise.resolve()}
    />,
  );
}

describe("ActiveProfile with the new Personal details card", () => {
  it("renders the new Work authorization field alongside the existing page", () => {
    const html = render(profile({ work_authorization: "citizen, no sponsorship needed" }));
    expect(html).toContain("Personal details");
    expect(html).toContain("Work authorization");
    expect(html).toContain("citizen, no sponsorship needed");
  });

  it("does not crash, and shows an empty field, when work_authorization is unset", () => {
    expect(() => render(profile())).not.toThrow();
    const html = render(profile());
    expect(html).toContain("Work authorization");
  });

  it("leaves the rest of the page's headings and controls exactly where they were", () => {
    const html = render(profile());
    expect(html).toContain("Resume structure");
    expect(html).toContain("Resume settings");
    expect(html).toContain(">Sections<");
    expect(html).toContain(">Raw JSON<");
    expect(html).toContain(">Export JSON<");
    expect(html).toContain("Active");
  });

  it("still shows the name, headline and the Google Scholar chip exactly as before", () => {
    const html = render(
      profile({ headline: "Backend Engineer", links: { scholar: "scholar.google.com/citations?x" } }),
    );
    expect(html).toContain("Asha Verma");
    expect(html).toContain("Backend Engineer");
    expect(html).toContain("scholar.google.com/citations?x");
  });

  it("omits the Scholar chip when there is none, same as before", () => {
    const html = render(profile());
    expect(html).not.toContain("bj-contact-chip");
  });

  it("still threads the shared editor's save/saving/error into SectionedProfile, unchanged", () => {
    captured.sectionedProfile.length = 0;
    render(profile());
    expect(captured.sectionedProfile).toHaveLength(1);
    const props = captured.sectionedProfile[0] as {
      profile: CanonicalProfile;
      versionCount: number;
      onSave: unknown;
      saving: boolean;
      error: string | null;
      clearError: unknown;
    };
    expect(props.versionCount).toBe(3);
    expect(typeof props.onSave).toBe("function");
    expect(props.saving).toBe(false);
    expect(props.error).toBeNull();
    expect(typeof props.clearError).toBe("function");
  });

  it("still renders HeaderComposer, SectionOrderEditor and ShapeSettingsPanel exactly once each", () => {
    captured.headerComposer.length = 0;
    captured.sectionOrderEditor.length = 0;
    captured.shapeSettingsPanel.length = 0;
    render(profile());
    expect(captured.headerComposer).toHaveLength(1);
    expect(captured.sectionOrderEditor).toHaveLength(1);
    expect(captured.shapeSettingsPanel).toHaveLength(1);
  });

  it("never renders or references work_authorization_status anywhere on the page", () => {
    const html = render(profile({ work_authorization: "a self-report" }));
    expect(html).not.toContain("work_authorization_status");
  });
});
