import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { CanonicalProfile, Personal } from "../lib/profileTypes";
import { PersonalDetailsCard } from "./PersonalDetailsCard";
import type { PersonalDetailsActions } from "./PersonalDetailsCardView";

// Wiring only -- what the view is called with, and what pressing its
// actions actually does. What each prop combination renders is
// PersonalDetailsCardView.test.tsx's job (a pure function, tested by
// calling it directly); this file mocks that view to capture what the
// connected wrapper hands it, mirroring HiringSignalsPanel.test.tsx /
// wiring.test.tsx's own split between a component's wiring and its view.

const captured = vi.hoisted(() => ({
  props: null as null | {
    draft: string;
    dirty: boolean;
    examplesOpen: boolean;
    saving: boolean;
    error: string | null;
    actions: PersonalDetailsActions;
  },
}));

vi.mock("./PersonalDetailsCardView", () => ({
  PersonalDetailsCardView: (props: typeof captured.props) => {
    captured.props = props;
    return null;
  },
}));

function samplePersonal(overrides: Partial<Personal> = {}): Personal {
  return { name: "Asha Verma", headline: "Backend Engineer", ...overrides };
}

function sampleProfile(personal: Personal): CanonicalProfile {
  return {
    personal,
    experience: [
      { title: "Engineer", company: "Example Corp", start_date: "2022-01", end_date: "present" },
    ],
  };
}

function mount(
  personal: Personal,
  onSave: (mutate: (draft: CanonicalProfile) => CanonicalProfile) => Promise<void>,
  saving = false,
) {
  renderToStaticMarkup(<PersonalDetailsCard personal={personal} onSave={onSave} saving={saving} />);
  if (captured.props === null) throw new Error("the view was not rendered");
  return captured.props;
}

describe("PersonalDetailsCard wiring", () => {
  it("hands the view the current work_authorization as the initial draft, not dirty", () => {
    const props = mount(samplePersonal({ work_authorization: "citizen, no sponsorship needed" }), vi.fn());
    expect(props.draft).toBe("citizen, no sponsorship needed");
    expect(props.dirty).toBe(false);
    expect(props.examplesOpen).toBe(false);
  });

  it("starts the draft as an empty string when work_authorization is unset -- no crash", () => {
    expect(() => mount(samplePersonal(), vi.fn())).not.toThrow();
    const props = mount(samplePersonal(), vi.fn());
    expect(props.draft).toBe("");
    expect(props.dirty).toBe(false);
  });

  it("passes saving straight through from the shared editor, unchanged", () => {
    const props = mount(samplePersonal(), vi.fn(), true);
    expect(props.saving).toBe(true);
  });

  it("starts with no error, regardless of the shared editor's own state elsewhere on the page", () => {
    const props = mount(samplePersonal(), vi.fn());
    expect(props.error).toBeNull();
  });

  it("save() calls onSave (the shared editor.save) with a mutation that sets ONLY work_authorization", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const personal = samplePersonal({ work_authorization: "an existing self-report" });
    const props = mount(personal, onSave);

    props.actions.save();
    expect(onSave).toHaveBeenCalledTimes(1);

    const mutate = onSave.mock.calls[0][0] as (p: CanonicalProfile) => CanonicalProfile;
    const before = sampleProfile(personal);
    const after = mutate(before);

    expect(after.personal.work_authorization).toBe("an existing self-report");
    expect(after.personal.name).toBe(before.personal.name);
    expect(after.personal.headline).toBe(before.personal.headline);
    // Nothing outside `personal` moved -- same object identity.
    expect(after.experience).toBe(before.experience);
    expect(after).not.toBe(before);
  });

  it("save() swallows a rejected onSave instead of throwing back into the click handler", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("network"));
    const props = mount(samplePersonal(), onSave);
    expect(() => props.actions.save()).not.toThrow();
    // The rejection is asynchronous; let it settle so the test doesn't leak
    // an unhandled rejection into a later test.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  // The cross-card error-bleed fix itself (this card used to render the
  // SAME `useProfileEditor.error` instance SectionedProfile also renders,
  // so a save failure anywhere on the page could surface under this
  // always-visible card, and vice versa) is a dynamic state transition --
  // `renderToStaticMarkup` performs one synchronous render with no live
  // fiber tree behind it, so a `setState` call after that render returns
  // is a confirmed no-op here (verified directly: a setter called after
  // `renderToStaticMarkup` never triggers a second call of a mocked child,
  // in this package or any other component test in this codebase -- there
  // is no DOM/act() environment to observe it with). The decoupling this
  // fixes is a static, wiring-level fact instead: see
  // "does not accept, and therefore cannot render, a foreign `error` prop
  // from the shared editor" in Profile.test.tsx, which captures exactly
  // that at the point ActiveProfile constructs this component.

  it("exposes setDraft and toggleExamples as callable actions", () => {
    const props = mount(samplePersonal(), vi.fn());
    expect(() => props.actions.setDraft("something else")).not.toThrow();
    expect(() => props.actions.toggleExamples()).not.toThrow();
  });
});
