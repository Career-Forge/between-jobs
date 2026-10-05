import { describe, expect, it } from "vitest";
import { expand, findAll, press, prop, textOf, type HostElement } from "../testing/reactTree";
import { AccountCardView, type AccountCardActions } from "./AccountCardView";

function recorder() {
  const calls: [string, ...unknown[]][] = [];
  const actions: AccountCardActions = {
    setTyped: (v) => calls.push(["setTyped", v]),
    confirm: () => calls.push(["confirm"]),
  };
  return { calls, actions };
}

function tree(props: { typed?: string; busy?: boolean; error?: string | null }, actions: AccountCardActions) {
  return expand(AccountCardView({ typed: "", busy: false, error: null, ...props, actions }));
}

function button(nodes: ReturnType<typeof tree>): HostElement {
  return findAll(nodes, (el) => el.type === "button")[0];
}

describe("the confirm button", () => {
  it("is disabled for empty and wrong input, enabled for the phrase", () => {
    const { actions } = recorder();
    expect(prop(button(tree({ typed: "" }, actions)), "disabled")).toBe(true);
    expect(prop(button(tree({ typed: "delete my acount" }, actions)), "disabled")).toBe(true);
    expect(prop(button(tree({ typed: "  Delete My Account " }, actions)), "disabled")).toBe(false);
  });

  it("calls confirm when pressed", () => {
    const { actions, calls } = recorder();
    press(button(tree({ typed: "delete my account" }, actions)));
    expect(calls).toEqual([["confirm"]]);
  });

  it("shows Deleting... and stays disabled while busy, even with the phrase typed", () => {
    const { actions } = recorder();
    const b = button(tree({ typed: "delete my account", busy: true }, actions));
    expect(textOf(b)).toBe("Deleting...");
    expect(prop(b, "disabled")).toBe(true);
  });

  it("explains its disabled state through aria-describedby", () => {
    const { actions } = recorder();
    const nodes = tree({ typed: "nope" }, actions);
    const id = prop(button(nodes), "aria-describedby");
    const hint = findAll(nodes, (el) => el.props.id === id)[0];
    expect(textOf(hint)).toContain("stays off");
  });
});

describe("the input", () => {
  it("has a label tied to it and reports typing", () => {
    const { actions, calls } = recorder();
    const nodes = tree({ typed: "abc" }, actions);
    const input = findAll(nodes, (el) => el.type === "input")[0];
    const label = findAll(nodes, (el) => el.type === "label")[0];
    expect(prop(label, "htmlFor")).toBe(prop(input, "id"));
    expect(textOf(label)).toContain("delete my account");
    expect(prop(input, "value")).toBe("abc");
    (prop(input, "onChange") as (e: unknown) => void)({ target: { value: "abcd" } });
    expect(calls).toEqual([["setTyped", "abcd"]]);
  });

  it("is disabled while busy", () => {
    const { actions } = recorder();
    const input = findAll(tree({ busy: true }, actions), (el) => el.type === "input")[0];
    expect(prop(input, "disabled")).toBe(true);
  });
});

describe("the error", () => {
  it("renders in a role=alert region only when there is one", () => {
    const { actions } = recorder();
    expect(findAll(tree({}, actions), (el) => prop(el, "role") === "alert")).toHaveLength(0);
    const alerts = findAll(tree({ error: "Nothing was deleted." }, actions), (el) => prop(el, "role") === "alert");
    expect(alerts).toHaveLength(1);
    expect(textOf(alerts[0])).toBe("Nothing was deleted.");
  });
});

describe("the copy", () => {
  it("states what is removed, that it is permanent, the Telegram note and the export pointer", () => {
    const { actions } = recorder();
    const text = tree({}, actions).map(textOf).join(" ");
    for (const part of [
      "every version of it",
      "applications and their notes",
      "resumes and cover letters",
      "saved searches",
      "saved provider keys",
      "revoked at Google",
      "browser-extension sign-in",
      "cannot be undone",
      "starts a new, empty account",
      "Export JSON",
    ]) {
      expect(text).toContain(part);
    }
  });
});
