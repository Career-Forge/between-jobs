import { describe, expect, it } from "vitest";
import {
  WORK_AUTHORIZATION_INDIA_NOTE,
  WORK_AUTHORIZATION_REGION_EXAMPLES,
} from "../lib/workAuthorization";
import { expand, findAll, press, prop, textOf, type HostElement } from "../testing/reactTree";
import { PersonalDetailsCardView, type PersonalDetailsActions } from "./PersonalDetailsCardView";

// The view is a pure function of its props (no hooks), so these tests call
// it directly and walk what it returns -- see src/testing/reactTree.ts and
// its sibling HiringSignalsPanelView.test.tsx for the same idiom. All
// interactive state (the draft text, whether Examples is open) lives one
// level up in PersonalDetailsCard.tsx; this file only checks what each
// combination of props renders and what pressing each control calls.

function recorder(): { actions: PersonalDetailsActions; calls: [string, ...unknown[]][] } {
  const calls: [string, ...unknown[]][] = [];
  const record =
    (name: string) =>
    (...args: unknown[]) => {
      calls.push([name, ...args]);
    };
  return {
    calls,
    actions: {
      setDraft: record("setDraft"),
      toggleExamples: record("toggleExamples"),
      save: record("save"),
    },
  };
}

function tree(
  props: Omit<Parameters<typeof PersonalDetailsCardView>[0], "actions">,
  actions: PersonalDetailsActions,
) {
  return expand(PersonalDetailsCardView({ ...props, actions }));
}

function base() {
  return { draft: "", dirty: false, examplesOpen: false, saving: false, error: null };
}

function textarea(nodes: ReturnType<typeof tree>): HostElement {
  return findAll(nodes, (el) => el.type === "textarea")[0];
}

function toggleButton(nodes: ReturnType<typeof tree>): HostElement {
  return findAll(nodes, (el) => el.type === "button" && prop(el, "aria-expanded") !== undefined)[0];
}

function saveButton(nodes: ReturnType<typeof tree>): HostElement {
  return findAll(nodes, (el) => el.type === "button" && el.props.className === "bj-primary")[0];
}

describe("the field", () => {
  it("has a real label and shows the current draft value", () => {
    const { actions } = recorder();
    const nodes = tree({ ...base(), draft: "I'm a citizen, no sponsorship needed." }, actions);
    const label = findAll(nodes, (el) => el.type === "label")[0];
    expect(textOf(label)).toContain("Work authorization");
    expect(prop(textarea(nodes), "value")).toBe("I'm a citizen, no sponsorship needed.");
  });

  it("calls setDraft with the new text when edited", () => {
    const { actions, calls } = recorder();
    const nodes = tree(base(), actions);
    const onChange = prop(textarea(nodes), "onChange") as (e: unknown) => void;
    onChange({ target: { value: "a new self-report" } });
    expect(calls).toEqual([["setDraft", "a new self-report"]]);
  });

  it("shows an error when one is present, and nothing when there isn't", () => {
    const { actions } = recorder();
    const withError = findAll(tree({ ...base(), error: "Save failed." }, actions), (el) =>
      el.props.className === "bj-error",
    );
    expect(withError).toHaveLength(1);
    expect(textOf(withError[0])).toBe("Save failed.");
    const withoutError = findAll(tree(base(), actions), (el) => el.props.className === "bj-error");
    expect(withoutError).toHaveLength(0);
  });
});

describe("the Save button", () => {
  it("is disabled when nothing changed, and enabled once it has", () => {
    const { actions } = recorder();
    expect(prop(saveButton(tree({ ...base(), dirty: false }, actions)), "disabled")).toBe(true);
    expect(prop(saveButton(tree({ ...base(), dirty: true }, actions)), "disabled")).toBe(false);
  });

  it("is disabled while saving, regardless of dirty, and says so", () => {
    const { actions } = recorder();
    const nodes = tree({ ...base(), dirty: true, saving: true }, actions);
    expect(prop(saveButton(nodes), "disabled")).toBe(true);
    expect(textOf(saveButton(nodes))).toBe("Saving...");
  });

  it("calls actions.save when pressed", () => {
    const { actions, calls } = recorder();
    press(saveButton(tree({ ...base(), dirty: true }, actions)));
    expect(calls).toEqual([["save"]]);
  });
});

describe("the Examples disclosure", () => {
  it("is closed by default and announces its state via aria-expanded", () => {
    const { actions } = recorder();
    const closed = toggleButton(tree({ ...base(), examplesOpen: false }, actions));
    expect(prop(closed, "aria-expanded")).toBe(false);
    expect(textOf(closed)).toBe("Show examples by region");

    const open = toggleButton(tree({ ...base(), examplesOpen: true }, actions));
    expect(prop(open, "aria-expanded")).toBe(true);
    expect(textOf(open)).toBe("Hide examples by region");
  });

  it("is a real, always-focusable <button type=\"button\">, closed or open", () => {
    const { actions } = recorder();
    for (const examplesOpen of [false, true]) {
      const button = toggleButton(tree({ ...base(), examplesOpen }, actions));
      expect(button.type).toBe("button");
      expect(prop(button, "type")).toBe("button");
      expect(prop(button, "disabled")).toBeUndefined();
      expect(prop(button, "tabIndex")).toBeUndefined();
    }
  });

  it("calls actions.toggleExamples when pressed", () => {
    const { actions, calls } = recorder();
    press(toggleButton(tree({ ...base(), examplesOpen: false }, actions)));
    expect(calls).toEqual([["toggleExamples"]]);
  });

  it("shows no example text at all while closed", () => {
    const { actions } = recorder();
    const nodes = tree({ ...base(), examplesOpen: false }, actions);
    const wholeText = nodes.map(textOf).join("");
    for (const region of WORK_AUTHORIZATION_REGION_EXAMPLES) {
      for (const sentence of region.sentences) {
        expect(wholeText).not.toContain(sentence);
      }
    }
  });

  it("shows every region and every example sentence, verbatim, once open", () => {
    const { actions } = recorder();
    const nodes = tree({ ...base(), examplesOpen: true }, actions);
    const wholeText = nodes.map(textOf).join("");
    expect(wholeText).toContain("United States");
    expect(wholeText).toContain("Canada");
    expect(wholeText).toContain("EU / EEA");
    expect(wholeText).toContain("India");
    for (const region of WORK_AUTHORIZATION_REGION_EXAMPLES) {
      for (const sentence of region.sentences) {
        expect(wholeText).toContain(sentence);
      }
    }
    expect(wholeText).toContain(WORK_AUTHORIZATION_INDIA_NOTE);
  });

  it("shows both real India situations, not just one", () => {
    const { actions } = recorder();
    const nodes = tree({ ...base(), examplesOpen: true }, actions);
    const wholeText = nodes.map(textOf).join("");
    expect(wholeText).toContain("no work authorization is needed for Indian roles");
    expect(wholeText).toContain("I hold an OCI card");
  });
});
