import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  GENERIC_RESET_FAILURE,
  RESET_SENT_MESSAGE,
  initialResetRequestState,
  type ResetRequestState,
} from "../lib/passwordReset";
import { expand, findAll, press, prop, textOf } from "../testing/reactTree";
import { ResetRequestFormView, type ResetRequestActions } from "./ResetRequestFormView";

// The "forgot password" form as a person meets it, and what its controls hand back.

function recorder() {
  const calls: [string, ...unknown[]][] = [];
  const actions: ResetRequestActions = {
    setEmail: (value) => calls.push(["setEmail", value]),
    submit: () => calls.push(["submit"]),
    back: () => calls.push(["back"]),
  };
  return { calls, actions };
}

function view(state: Partial<ResetRequestState> = {}, problem: string | null = null, actions?: ResetRequestActions) {
  return ResetRequestFormView({
    state: { ...initialResetRequestState, ...state },
    problem,
    actions: actions ?? recorder().actions,
  });
}

function html(state: Partial<ResetRequestState> = {}, problem: string | null = null): string {
  return renderToStaticMarkup(
    <ResetRequestFormView
      state={{ ...initialResetRequestState, ...state }}
      problem={problem}
      actions={recorder().actions}
    />,
  );
}

describe("ResetRequestFormView", () => {
  it("asks for an email and offers Send reset link", () => {
    const markup = html();
    expect(markup).toContain('type="email"');
    expect(markup).toMatch(/autoComplete="email"|autocomplete="email"/i);
    expect(markup).toContain('aria-label="Email"');
    expect(markup).toContain("required");
    expect(markup).toContain(">Send reset link</button>");
    expect(markup).toContain(">Back to sign in</button>");
    expect(markup).not.toContain('role="alert"');
  });

  it("says Sending... and blocks a second send and edits while busy", () => {
    const nodes = expand(view({ busy: true, email: "pat@example.com" }));
    const submit = findAll(nodes, (el) => el.type === "button" && prop(el, "type") === "submit")[0];
    expect(textOf(submit)).toBe("Sending...");
    expect(prop(submit, "disabled")).toBe(true);
    const input = findAll(nodes, (el) => el.type === "input")[0];
    expect(prop(input, "disabled")).toBe(true);
  });

  it("shows only the confirmation once sent, without the address, and no form to send again", () => {
    const markup = html({
      email: "pat@example.com",
      outcome: { kind: "sent", message: RESET_SENT_MESSAGE },
    });
    expect(markup).toContain('role="status"');
    expect(markup).toContain("If an account exists for that address, a reset link is on its way.");
    expect(markup).not.toContain("pat@example.com");
    expect(markup).not.toContain("<form");
    expect(markup).toContain("Back to sign in");
  });

  it("shows an error plainly, as an alert, and keeps the form so it can be tried again", () => {
    const markup = html({
      email: "pat@example.com",
      outcome: { kind: "error", message: GENERIC_RESET_FAILURE },
    });
    expect(markup).toContain('role="alert"');
    expect(markup).toContain(GENERIC_RESET_FAILURE);
    expect(markup).toContain("<form");
  });

  it("goes back from the confirmation, and does not send another email by doing so", () => {
    const { calls, actions } = recorder();
    const nodes = expand(
      view({ email: "pat@example.com", outcome: { kind: "sent", message: RESET_SENT_MESSAGE } }, null, actions),
    );
    const buttons = findAll(nodes, (el) => el.type === "button");
    expect(buttons).toHaveLength(1);
    expect(textOf(buttons[0])).toBe("Back to sign in");
    press(buttons[0]);
    expect(calls).toEqual([["back"]]);
  });

  it("shows the dead-link sentence above the form when the person arrived from one", () => {
    const markup = html({}, "That link has expired or was already used. Request a new one.");
    expect(markup).toContain("That link has expired or was already used. Request a new one.");
    expect(markup.indexOf("expired")).toBeLessThan(markup.indexOf("<form"));
  });

  it("renders a message with markup in it as text", () => {
    const markup = html({ outcome: { kind: "error", message: "<img src=x onerror=alert(1)>" } });
    expect(markup).not.toContain("<img");
  });

  it("reports typing, the form submit (without a page reload), and Back", () => {
    const { calls, actions } = recorder();
    const nodes = expand(view({}, null, actions));

    const input = findAll(nodes, (el) => el.type === "input")[0];
    (prop(input, "onChange") as (e: unknown) => void)({ target: { value: "pat@example.com" } });

    const form = findAll(nodes, (el) => el.type === "form")[0];
    let prevented = false;
    (prop(form, "onSubmit") as (e: unknown) => void)({ preventDefault: () => (prevented = true) });

    const back = findAll(nodes, (el) => el.type === "button" && textOf(el) === "Back to sign in")[0];
    press(back);

    expect(prevented).toBe(true);
    expect(calls).toEqual([["setEmail", "pat@example.com"], ["submit"], ["back"]]);
  });
});
