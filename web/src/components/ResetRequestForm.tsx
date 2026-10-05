import { useReducer, useRef } from "react";
import { useAuth } from "../auth";
import {
  initialResetRequestState,
  resetRequestActions,
  resetRequestReducer,
  submitResetRequest,
} from "../lib/passwordReset";
import { ResetRequestFormView } from "./ResetRequestFormView";

// Connected wrapper for the "forgot password" form: thin glue over the tested reducer and
// `resetPassword` (lib/passwordReset.ts, wired to Supabase in auth.tsx). What a submit does,
// including the guard against a double submit (a ref, not the busy state: state only updates
// on the next render, and a second click can land before it), is `submitResetRequest`.
export function ResetRequestForm({
  problem,
  onBack,
}: {
  problem: string | null;
  onBack: () => void;
}) {
  const { resetPassword } = useAuth();
  const [state, dispatch] = useReducer(resetRequestReducer, initialResetRequestState);
  const inFlight = useRef(false);

  const submit = () => void submitResetRequest({ resetPassword, dispatch, inFlight }, state.email);

  return (
    <ResetRequestFormView
      state={state}
      problem={problem}
      actions={resetRequestActions(dispatch, submit, onBack)}
    />
  );
}
