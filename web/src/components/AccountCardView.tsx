import type { Ref } from "react";
import { CONFIRMATION_PHRASE, isConfirmed } from "../lib/accountDeletion";

// "Delete my account" card (launch plan P4.5). A pure function of its props,
// same shape as PersonalDetailsCardView: no hooks, so a test can call it
// directly. The typed text, busy flag and error live in AccountCard.tsx,
// which also hands in errorRef so it can move focus to the error alert.

export interface AccountCardActions {
  setTyped: (value: string) => void;
  confirm: () => void;
}

const INPUT_ID = "bj-account-delete-confirm";
const HINT_ID = "bj-account-delete-hint";

export function AccountCardView({
  typed,
  busy,
  error,
  errorRef,
  actions,
}: {
  typed: string;
  busy: boolean;
  error: string | null;
  errorRef?: Ref<HTMLDivElement>;
  actions: AccountCardActions;
}) {
  const confirmed = isConfirmed(typed);
  const disabled = busy || !confirmed;
  return (
    <div className="bj-card bj-account-delete">
      <h2>Delete my account</h2>
      <p>This permanently removes:</p>
      <ul>
        <li>your profile and every version of it</li>
        <li>your applications and their notes</li>
        <li>generated resumes and cover letters, and the files stored for them</li>
        <li>saved searches</li>
        <li>saved provider keys</li>
        <li>
          your connected Gmail access: we ask Google to revoke the connection, and you can also
          remove it yourself on Google&rsquo;s{" "}
          <a
            href="https://myaccount.google.com/permissions"
            target="_blank"
            rel="noopener noreferrer"
          >
            account permissions page
          </a>
        </li>
        <li>your browser-extension sign-in</li>
      </ul>
      <p>
        <strong>This cannot be undone.</strong> If you also use the Telegram bot or the Discord app,
        using it again starts a new, empty account.
      </p>
      <p>Drafts we already created in your Gmail stay in your mailbox.</p>
      <p className="bj-muted bj-small">
        Want a copy first? Use &ldquo;Export JSON&rdquo; on this page to keep your profile.
      </p>
      <label className="bj-field" htmlFor={INPUT_ID}>
        <span>Type &ldquo;{CONFIRMATION_PHRASE}&rdquo; to confirm</span>
        <input
          id={INPUT_ID}
          type="text"
          value={typed}
          autoComplete="off"
          disabled={busy}
          onChange={(e) => actions.setTyped(e.target.value)}
        />
      </label>
      <p id={HINT_ID} className="bj-muted bj-small" aria-live="polite">
        {busy
          ? "Deleting your account."
          : confirmed
            ? "Ready. This is the last step."
            : `The button stays off until you type "${CONFIRMATION_PHRASE}".`}
      </p>
      {error && (
        <div className="bj-error" role="alert" ref={errorRef} tabIndex={-1}>
          {error}
        </div>
      )}
      <div className="bj-actions">
        <button
          type="button"
          className="bj-danger"
          onClick={() => actions.confirm()}
          disabled={disabled}
          aria-describedby={HINT_ID}
        >
          {busy ? "Deleting..." : "Delete my account"}
        </button>
      </div>
    </div>
  );
}
