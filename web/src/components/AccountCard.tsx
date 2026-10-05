import { useRef, useState } from "react";
import { useAuth } from "../auth";
import { apiFetch } from "../lib/api";
import { CONFIRMATION_PHRASE, deletionErrorMessage } from "../lib/accountDeletion";
import { AccountCardView } from "./AccountCardView";

// Connected wrapper for the "Delete my account" card (launch plan P4.5) --
// wiring only. The server deletes the account as its last step, so success
// ends the session (signOut sends the app back to the login screen) and any
// error leaves a still-existing account and a re-enabled button. A ref, not
// the busy state, guards the double submit: state only updates on the next
// render, a second click can land before it.

export function AccountCard() {
  const { signOut } = useAuth();
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  async function confirm() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/account/delete", {
        method: "POST",
        body: JSON.stringify({ confirm: CONFIRMATION_PHRASE }),
      });
    } catch (e) {
      setError(deletionErrorMessage(e));
      setBusy(false);
      inFlight.current = false;
      return;
    }
    // The account is gone; the local session is all that is left to clear.
    // Stay busy so the card cannot be submitted again while the app
    // swaps to the login screen.
    try {
      await signOut();
    } catch {
      // Nothing useful to show: the next request 401s and the app re-authenticates.
    }
  }

  return (
    <AccountCardView
      typed={typed}
      busy={busy}
      error={error}
      actions={{ setTyped, confirm: () => void confirm() }}
    />
  );
}
