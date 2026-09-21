import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { CanonicalProfile } from "../lib/profileTypes";
import { ActiveProfile } from "./Profile";

// Regression coverage for the cross-card error-bleed finding: ActiveProfile
// used to pass the SAME `useProfileEditor.error` instance into both
// PersonalDetailsCard (always visible on the page) and SectionedProfile (its
// error only ever shown inside a deliberately-opened, already-cleared edit
// modal). A save failure anywhere in SectionedProfile's modal would then
// also surface, with no relation to work authorization, under the always-
// visible Personal Details card -- and PersonalDetailsCard's own failure
// could vanish the moment an unrelated SectionedProfile modal opened, since
// that modal calls `clearError()` on open.
//
// A separate file from Profile.test.tsx (rather than adding a mock there)
// because Profile.test.tsx deliberately renders the REAL PersonalDetailsCard
// to check its own text content -- mocking it out here would break that.
// This file's only job is the wiring fact: does the shared editor's error
// still reach this card at all.

const captured = vi.hoisted(() => ({
  personalDetailsCard: [] as unknown[],
}));

vi.mock("../lib/api", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {},
}));
vi.mock("../components/HeaderComposer", () => ({ HeaderComposer: () => null }));
vi.mock("../components/SectionOrderEditor", () => ({ SectionOrderEditor: () => null }));
vi.mock("../components/ShapeSettingsPanel", () => ({ ShapeSettingsPanel: () => null }));
vi.mock("../components/SectionedProfile", () => ({ SectionedProfile: () => null }));
vi.mock("../components/PersonalDetailsCard", () => ({
  PersonalDetailsCard: (props: unknown) => {
    captured.personalDetailsCard.push(props);
    return null;
  },
}));

function profile(): CanonicalProfile {
  return {
    personal: { name: "Asha Verma", headline: "Backend Engineer" },
    experience: [
      { title: "Engineer", company: "Example Corp", start_date: "2022-01", end_date: "present" },
    ],
  };
}

function render() {
  captured.personalDetailsCard.length = 0;
  renderToStaticMarkup(
    <ActiveProfile
      version={{
        id: "v1",
        canonical_json: profile(),
        activated_at: "2026-09-01T00:00:00Z",
        version_count: 3,
      }}
      onEdited={() => Promise.resolve()}
    />,
  );
  if (captured.personalDetailsCard.length !== 1) {
    throw new Error("PersonalDetailsCard was not rendered exactly once");
  }
  return captured.personalDetailsCard[0] as Record<string, unknown>;
}

describe("PersonalDetailsCard's isolation from the shared editor's error", () => {
  it("passes onSave and saving, exactly as before", () => {
    const props = render();
    expect(typeof props.onSave).toBe("function");
    expect(props.saving).toBe(false);
  });

  it("does NOT receive the shared useProfileEditor error at all -- the root cause of the bleed", () => {
    const props = render();
    // Before the fix, ActiveProfile passed `error={editor.error}` here
    // explicitly, so this key would be present (as `null` in this default
    // case, but present -- a save failure elsewhere on the page would then
    // update the SAME shared value and surface under this always-visible
    // card). The fix removes that binding entirely: PersonalDetailsCard no
    // longer accepts, and ActiveProfile no longer forwards, any `error` prop.
    expect("error" in props).toBe(false);
  });
});
