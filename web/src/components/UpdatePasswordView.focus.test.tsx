// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { initialUpdatePasswordState, type UpdatePasswordState } from "../lib/passwordReset";
import { buttonNamed, query, render } from "../testing/render";
import { UpdatePasswordFormView } from "./UpdatePasswordView";

// Pressing Update password must not take keyboard focus away from the person: the fields are read-only and the
// button aria-disabled while the request is out, never disabled (a disabled control drops focus to the top).

const actions = { setPassword: () => undefined, setConfirm: () => undefined, submit: () => undefined };
const view = (state: UpdatePasswordState) => <UpdatePasswordFormView state={state} actions={actions} />;

describe("the new-password form while it works", () => {
  it("keeps focus on the button, and on a field when Enter in that field submitted it", () => {
    const { container, rerender } = render(view({ ...initialUpdatePasswordState, password: "a-long-password", confirm: "a-long-password" }));
    const button = buttonNamed(container, "Update password");
    button.focus();
    rerender(view({ ...initialUpdatePasswordState, password: "a-long-password", confirm: "a-long-password", status: "submitting" }));
    expect(document.activeElement).toBe(button);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBe("true");

    const field = query<HTMLInputElement>(container, "input[type='password']");
    field.focus();
    expect(field.readOnly).toBe(true);
    expect(field.disabled).toBe(false);
    expect(document.activeElement).toBe(field);
  });
});
