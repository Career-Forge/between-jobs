import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  MIN_PASSWORD_LENGTH,
  RESET_SENT_MESSAGE,
  initialResetRequestState,
  initialUpdatePasswordState,
  type ResetRequestState,
  type UpdatePasswordState,
} from "../lib/passwordReset";
import { expand, findAll, press, prop, textOf } from "../testing/reactTree";
import {
  LinkProblemView,
  UpdatePasswordFormView,
  type LinkProblemActions,
  type UpdatePasswordActions,
} from "./UpdatePasswordView";

// The two things /update-password shows, and what their controls hand back.

function formActions() {
  const calls: [string, ...unknown[]][] = [];
  const actions: UpdatePasswordActions = {
    setPassword: (value) => calls.push(["setPassword", value]),
    setConfirm: (value) => calls.push(["setConfirm", value]),
    submit: () => calls.push(["submit"]),
  };
  return { calls, actions };
}

function formHtml(state: Partial<UpdatePasswordState> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <UpdatePasswordFormView
        state={{ ...initialUpdatePasswordState, ...state }}
        actions={formActions().actions}
      />
    </MemoryRouter>,
  );
}

describe("UpdatePasswordFormView", () => {
  it("has two labelled new-password fields with the registration minimum, and one submit", () => {
    const markup = formHtml();
    expect((markup.match(/type="password"/g) ?? []).length).toBe(2);
    expect((markup.match(/autoComplete="new-password"|autocomplete="new-password"/gi) ?? []).length).toBe(2);
    expect((markup.match(new RegExp(`minLength="${MIN_PASSWORD_LENGTH}"`, "gi")) ?? []).length).toBe(2);
    expect(markup).toContain("<span>New password</span>");
    expect(markup).toContain("<span>Confirm new password</span>");
    expect(markup).toContain(">Update password</button>");
    expect(markup).not.toContain('role="alert"');
    expect(markup).not.toContain('role="status"');
  });

  it("shows the server's (or the check's) message plainly, as an alert", () => {
    const markup = formHtml({ status: "error", message: "New password should be different from the old password." });
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("New password should be different from the old password.");
  });

  it("disables everything while updating, and says so", () => {
    const nodes = expand(
      UpdatePasswordFormView({
        state: { ...initialUpdatePasswordState, status: "submitting" },
        actions: formActions().actions,
      }),
    );
    const submit = findAll(nodes, (el) => el.type === "button")[0];
    expect(textOf(submit)).toBe("Updating...");
    expect(prop(submit, "disabled")).toBe(true);
    for (const input of findAll(nodes, (el) => el.type === "input")) {
      expect(prop(input, "disabled")).toBe(true);
    }
  });

  it("confirms the update with a status message and a way on, and cannot be submitted again", () => {
    const markup = formHtml({ status: "done" });
    expect(markup).toContain('role="status"');
    expect(markup).toContain("Your password has been updated.");
    expect(markup).toMatch(/<a href="\/"[^>]*>Go there now<\/a>/);
    const nodes = expand(
      UpdatePasswordFormView({ state: { ...initialUpdatePasswordState, status: "done" }, actions: formActions().actions }),
    );
    expect(prop(findAll(nodes, (el) => el.type === "button")[0], "disabled")).toBe(true);
  });

  it("shows each field its own text: the password in the first, the confirmation in the second", () => {
    const nodes = expand(
      UpdatePasswordFormView({
        state: { ...initialUpdatePasswordState, password: "the-password", confirm: "the-confirmation" },
        actions: formActions().actions,
      }),
    );
    const [password, confirm] = findAll(nodes, (el) => el.type === "input");
    expect(prop(password, "value")).toBe("the-password");
    expect(prop(confirm, "value")).toBe("the-confirmation");
  });

  it("reports typing in each field and the form submit, without a page reload", () => {
    const { calls, actions } = formActions();
    const nodes = expand(UpdatePasswordFormView({ state: initialUpdatePasswordState, actions }));
    const [password, confirm] = findAll(nodes, (el) => el.type === "input");
    (prop(password, "onChange") as (e: unknown) => void)({ target: { value: "abcdef" } });
    (prop(confirm, "onChange") as (e: unknown) => void)({ target: { value: "abcdeg" } });
    let prevented = false;
    (prop(findAll(nodes, (el) => el.type === "form")[0], "onSubmit") as (e: unknown) => void)({
      preventDefault: () => (prevented = true),
    });
    expect(prevented).toBe(true);
    expect(calls).toEqual([["setPassword", "abcdef"], ["setConfirm", "abcdeg"], ["submit"]]);
  });
});

describe("LinkProblemView", () => {
  const MESSAGE = "That link has expired or was already used. Request a new one.";

  function problemActions() {
    const calls: string[] = [];
    const actions: LinkProblemActions = { resend: () => calls.push("resend") };
    return { calls, actions };
  }

  function problemHtml(email: string | null, resend: Partial<ResetRequestState> = {}): string {
    return renderToStaticMarkup(
      <MemoryRouter>
        <LinkProblemView
          message={MESSAGE}
          email={email}
          resend={{ ...initialResetRequestState, ...resend }}
          actions={problemActions().actions}
        />
      </MemoryRouter>,
    );
  }

  it("says the link is dead, in fixed words, and offers a new one for the account's own address", () => {
    const markup = problemHtml("pat@example.com");
    expect(markup).toContain('role="alert"');
    expect(markup).toContain(MESSAGE);
    expect(markup).toContain(">Email me a new link</button>");
    expect(markup).not.toContain("pat@example.com");
    expect(markup).toMatch(/<a href="\/"[^>]*>Back to Today<\/a>/);
  });

  it("offers no button when the account has no address to send to", () => {
    expect(problemHtml(null)).not.toContain("Email me a new link");
  });

  it("shows the confirmation, not the button, once a link was sent", () => {
    const markup = problemHtml("pat@example.com", { outcome: { kind: "sent", message: RESET_SENT_MESSAGE } });
    expect(markup).toContain(RESET_SENT_MESSAGE);
    expect(markup).not.toContain("Email me a new link");
  });

  it("shows an error from sending plainly and keeps the button", () => {
    const markup = problemHtml("pat@example.com", { outcome: { kind: "error", message: "Too many requests." } });
    expect(markup).toContain("Too many requests.");
    expect(markup).toContain("Email me a new link");
  });

  it("calls resend when the button is pressed, and disables it while sending", () => {
    const { calls, actions } = problemActions();
    const nodes = expand(
      LinkProblemView({ message: MESSAGE, email: "pat@example.com", resend: initialResetRequestState, actions }),
    );
    press(findAll(nodes, (el) => el.type === "button")[0]);
    expect(calls).toEqual(["resend"]);

    const busy = expand(
      LinkProblemView({ message: MESSAGE, email: "pat@example.com", resend: { ...initialResetRequestState, busy: true }, actions }),
    );
    const button = findAll(busy, (el) => el.type === "button")[0];
    expect(textOf(button)).toBe("Sending...");
    expect(prop(button, "disabled")).toBe(true);
  });
});
