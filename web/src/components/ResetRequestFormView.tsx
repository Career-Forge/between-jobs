import type { FormEvent } from "react";
import type { ResetRequestState } from "../lib/passwordReset";

// The "forgot password" form on the Login page, as a pure function of its props (the same
// split as AccountCardView): what it says for each state, and what its buttons hand back.
// The state, the request and the guard against a double submit live in ResetRequestForm.tsx.
//
// `problem` is the sentence about an emailed link that did not work (expired, already used),
// when the person arrived from one: the form below it is the way to ask for a new one.

export interface ResetRequestActions {
  setEmail: (value: string) => void;
  submit: () => void;
  back: () => void;
}

export function ResetRequestFormView({
  state,
  problem,
  actions,
}: {
  state: ResetRequestState;
  problem: string | null;
  actions: ResetRequestActions;
}) {
  if (state.outcome?.kind === "sent") {
    return (
      <div className="bj-reset-request">
        <div className="bj-muted bj-small" role="status" tabIndex={-1}>
          {state.outcome.message}
        </div>
        <button type="button" className="bj-link-button" onClick={actions.back}>
          Back to sign in
        </button>
      </div>
    );
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!state.busy) actions.submit();
  }

  return (
    <div className="bj-reset-request">
      {problem !== null && (
        <div className="bj-error" role="alert">
          {problem}
        </div>
      )}
      <form onSubmit={onSubmit}>
        <input
          type="email"
          placeholder="Email"
          aria-label="Email"
          value={state.email}
          onChange={(e) => actions.setEmail(e.target.value)}
          autoComplete="email"
          readOnly={state.busy}
          required
        />
        {state.outcome?.kind === "error" && (
          <div className="bj-error" role="alert">
            {state.outcome.message}
          </div>
        )}
        {/* While the request is out the field is read-only and the button aria-disabled, never disabled:
            a disabled control drops keyboard focus to the top of the page. */}
        <button className="bj-primary" type="submit" aria-disabled={state.busy ? true : undefined}>
          {state.busy ? "Sending..." : "Send reset link"}
        </button>
      </form>
      <button type="button" className="bj-link-button" onClick={actions.back}>
        Back to sign in
      </button>
    </div>
  );
}
