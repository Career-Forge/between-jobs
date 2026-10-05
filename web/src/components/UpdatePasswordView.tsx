import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  MIN_PASSWORD_LENGTH,
  type ResetRequestState,
  type UpdatePasswordState,
} from "../lib/passwordReset";

// The two things the /update-password page can show, each a pure function of its props (the
// same split as AccountCardView): the form that sets a new password, and the message for an
// emailed link that did not work. The state, the requests and the redirect after success
// live in pages/UpdatePassword.tsx.

export interface UpdatePasswordActions {
  setPassword: (value: string) => void;
  setConfirm: (value: string) => void;
  submit: () => void;
}

export function UpdatePasswordFormView({
  state,
  actions,
}: {
  state: UpdatePasswordState;
  actions: UpdatePasswordActions;
}) {
  const busy = state.status === "submitting";
  const done = state.status === "done";

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    actions.submit();
  }

  return (
    <div>
      <h1>Set a new password</h1>
      <form className="bj-card" onSubmit={onSubmit}>
        <p className="bj-muted bj-small">
          Choose a new password for your account. Use at least {MIN_PASSWORD_LENGTH} characters.
        </p>
        <label className="bj-field">
          <span>New password</span>
          <input
            type="password"
            value={state.password}
            autoComplete="new-password"
            minLength={MIN_PASSWORD_LENGTH}
            disabled={busy || done}
            onChange={(e) => actions.setPassword(e.target.value)}
            required
          />
        </label>
        <label className="bj-field">
          <span>Confirm new password</span>
          <input
            type="password"
            value={state.confirm}
            autoComplete="new-password"
            minLength={MIN_PASSWORD_LENGTH}
            disabled={busy || done}
            onChange={(e) => actions.setConfirm(e.target.value)}
            required
          />
        </label>
        {state.status === "error" && state.message !== null && (
          <div className="bj-error" role="alert">
            {state.message}
          </div>
        )}
        {done && (
          <div className="bj-muted bj-small" role="status">
            Your password has been updated. Taking you to Today.{" "}
            <Link to="/">Go there now</Link>
          </div>
        )}
        <div className="bj-actions">
          <button className="bj-primary" type="submit" disabled={busy || done}>
            {busy ? "Updating..." : "Update password"}
          </button>
        </div>
      </form>
    </div>
  );
}

export interface LinkProblemActions {
  resend: () => void;
}

// An emailed link that did not work, reached while signed in. `email` is the account's own
// address when it is known: with it the person can have a new link sent in one click. The
// message is one of the fixed sentences from lib/passwordReset.ts, never text from the URL.
export function LinkProblemView({
  message,
  email,
  resend,
  actions,
}: {
  message: string;
  email: string | null;
  resend: ResetRequestState;
  actions: LinkProblemActions;
}) {
  const sent = resend.outcome?.kind === "sent";
  return (
    <div>
      <h1>Set a new password</h1>
      <div className="bj-card">
        <div className="bj-error" role="alert">
          {message}
        </div>
        {sent && resend.outcome?.kind === "sent" && (
          <div className="bj-muted bj-small" role="status">
            {resend.outcome.message}
          </div>
        )}
        {resend.outcome?.kind === "error" && (
          <div className="bj-error" role="alert">
            {resend.outcome.message}
          </div>
        )}
        <div className="bj-actions">
          {email !== null && !sent && (
            <button
              className="bj-primary"
              type="button"
              onClick={actions.resend}
              disabled={resend.busy}
            >
              {resend.busy ? "Sending..." : "Email me a new link"}
            </button>
          )}
          <Link to="/">Back to Today</Link>
        </div>
      </div>
    </div>
  );
}
