import { useEffect, useReducer, useRef } from "react";
import { useAuth } from "../auth";
import { apiFetch } from "../lib/api";
import {
  CONFIRMATION_PHRASE,
  accountCardReducer,
  initialAccountCardState,
  runAccountDeletion,
} from "../lib/accountDeletion";
import { AccountCardView } from "./AccountCardView";

// Connected wrapper for the "Delete my account" card (launch plan P4.5) --
// thin glue over the tested reducer and runAccountDeletion in
// lib/accountDeletion.ts. A ref, not the busy state, guards the double
// submit: state only updates on the next render, a second click can land
// before it. After a success the card stays busy while signOut swaps the app
// to the login screen; after a failure focus moves to the error message.

export function AccountCard() {
  const { signOut } = useAuth();
  const [state, dispatch] = useReducer(accountCardReducer, initialAccountCardState);
  const inFlight = useRef(false);
  const errorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (state.error) errorRef.current?.focus();
  }, [state.error]);

  async function confirm() {
    if (inFlight.current) return;
    inFlight.current = true;
    dispatch({ type: "submitted" });
    const result = await runAccountDeletion({
      post: () =>
        apiFetch("/account/delete", {
          method: "POST",
          body: JSON.stringify({ confirm: CONFIRMATION_PHRASE }),
        }),
      signOut,
    });
    if (!result.ok) {
      dispatch({ type: "failed", message: result.message });
      inFlight.current = false;
    }
  }

  return (
    <AccountCardView
      typed={state.typed}
      busy={state.busy}
      error={state.error}
      errorRef={errorRef}
      actions={{
        setTyped: (value) => dispatch({ type: "typed", value }),
        confirm: () => void confirm(),
      }}
    />
  );
}
