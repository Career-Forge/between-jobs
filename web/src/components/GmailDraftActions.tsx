import { Link } from "react-router-dom";
import { buildMailto } from "../lib/mailto";
import { type GmailState, mayOfferGmailDraft } from "../lib/gmailAccess";

// What sits under an outreach draft in the contact panel (ContactFinderPanel): the way to put it
// in Gmail when that works, and otherwise the two ways that need no Gmail at all -- open it in the
// person's own mail app, or copy it. A pure function of its props, no hooks, so a test can render
// it directly; the panel owns the state and the requests.
//
// Who sees what:
//   - a draft already in Gmail: just the note saying so;
//   - Gmail connected (or the connection not read yet, in which case the server answers when they
//     click): "Create Gmail draft" once the contact has a work email, otherwise the old prompt to
//     find one;
//   - Gmail not connected, a connection that needs reconnecting, or Gmail not available for this
//     person or server: the mail-app link and the copy button, with a line saying why and, where
//     it helps, the link to Integrations. Nobody is sent to connect a Gmail they cannot have;
//   - it is not known whether Gmail is open to this person (the server's settings could not be
//     read): the same two ways out and a line saying so, and no button or link towards Gmail at
//     all. An offer waits for a definite yes.
//
// Nothing here sends mail. The mail-app link only opens the person's mail app with the draft
// filled in; the recipient is the contact's work email the person looked up, or empty.

export interface GmailDraftActionsProps {
  state: GmailState;
  // Gmail is not for this person or server: the sentence saying so (lib/gmailAccess.ts).
  unavailableMessage: string | null;
  // The draft is already in the person's Gmail.
  alreadyPushed: boolean;
  // The contact's work email, if the person looked it up.
  recipient: string | null;
  subject: string;
  body: string;
  pushing: boolean;
  copied: boolean;
  // Copying failed (the browser refused clipboard access, or has none on this kind of page): say
  // so, since the button would otherwise seem to do nothing.
  copyFailed?: boolean;
  onPush: () => void;
  onCopyAll: () => void;
}

const INTEGRATIONS = "/profile/integrations";

function fallbackExplanation(state: GmailState, unavailableMessage: string | null): string {
  switch (state) {
    case "unavailable":
      return `${unavailableMessage ?? "Gmail isn't available for you."} You can still copy this email or open it in your mail app.`;
    case "reconnect_needed":
      return "Your Gmail connection needs to be reconnected before it can create drafts. Until then, copy this email or open it in your mail app.";
    case "availability_unknown":
      return "We can't tell whether Gmail is available for your account right now. You can still copy this email or open it in your mail app.";
    default:
      return "Connect Gmail to have drafts created for you. Until then, copy this email or open it in your mail app.";
  }
}

export function GmailDraftActions({
  state,
  unavailableMessage,
  alreadyPushed,
  recipient,
  subject,
  body,
  pushing,
  copied,
  copyFailed = false,
  onPush,
  onCopyAll,
}: GmailDraftActionsProps) {
  if (alreadyPushed) {
    return (
      <div className="bj-actions">
        <span className="bj-small">Draft created in your Gmail</span>
      </div>
    );
  }

  if (mayOfferGmailDraft(state)) {
    return (
      <div className="bj-actions">
        {recipient ? (
          <button className="bj-primary" onClick={onPush} disabled={pushing}>
            {pushing ? "Creating draft..." : "Create Gmail draft"}
          </button>
        ) : (
          <span className="bj-muted bj-small">
            Find this contact's work email to create a Gmail draft.
          </span>
        )}
      </div>
    );
  }

  const mail = buildMailto({ to: recipient, subject, body });
  return (
    <div className="bj-gmail-fallback">
      <p className="bj-muted bj-small">{fallbackExplanation(state, unavailableMessage)}</p>
      <div className="bj-actions">
        {state === "not_connected" && (
          <Link className="bj-button-link bj-button-link-primary" to={INTEGRATIONS}>
            Connect Gmail in Integrations
          </Link>
        )}
        {state === "reconnect_needed" && (
          <Link className="bj-button-link bj-button-link-primary" to={INTEGRATIONS}>
            Reconnect Gmail in Integrations
          </Link>
        )}
        {mail.href !== null ? (
          <a className="bj-button-link" href={mail.href}>
            Open in your mail app
          </a>
        ) : (
          <span className="bj-muted bj-small">
            This email is too long to open in a mail app. Copy it instead.
          </span>
        )}
        <button onClick={onCopyAll}>{copied ? "Copied!" : "Copy subject and email"}</button>
      </div>
      {copyFailed && (
        <p className="bj-error" role="status">
          Couldn't copy automatically. Select the email above and copy it by hand.
        </p>
      )}
    </div>
  );
}
