import { useEffect, useReducer, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { LinkProblemView, UpdatePasswordFormView } from "../components/UpdatePasswordView";
import {
  initialResetRequestState,
  initialUpdatePasswordState,
  parseRecoveryLinkProblem,
  recoveryProblemMessage,
  resetRequestReducer,
  submitNewPassword,
  submitResetRequest,
  updatePasswordActions,
  updatePasswordReducer,
} from "../lib/passwordReset";

// /update-password -- where an emailed reset link lands (and where App sends anyone in a
// password recovery, wherever they arrived). supabase-js has already turned the link into a
// recovery session by the time this renders; the page sets the new password on it. Thin glue
// over the tested logic in lib/passwordReset.ts.
//
// A link that did not work (expired, already used) carries its reason in the URL hash. A
// signed-out visitor never gets here (they see the Login page, which handles that case); a
// signed-in one sees a fixed sentence about it and can have a new link sent to their own
// address. The recovery flag wins: if a real recovery is under way, the form shows.

const REDIRECT_DELAY_MS = 1500;

export default function UpdatePassword() {
  const { session, recovery, updatePassword, resetPassword, clearRecovery } = useAuth();
  const location = useLocation();
  const problem = recovery
    ? null
    : parseRecoveryLinkProblem(location.hash, location.search, location.pathname);

  if (problem !== null) {
    return (
      <ExpiredLink
        message={recoveryProblemMessage(problem)}
        email={session?.user.email ?? null}
        resetPassword={resetPassword}
      />
    );
  }
  return <NewPasswordForm updatePassword={updatePassword} clearRecovery={clearRecovery} />;
}

function NewPasswordForm({
  updatePassword,
  clearRecovery,
}: {
  updatePassword: ReturnType<typeof useAuth>["updatePassword"];
  clearRecovery: () => void;
}) {
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(updatePasswordReducer, initialUpdatePasswordState);
  const inFlight = useRef(false);

  // After a short success message, on to Today. The recovery flag is cleared at the moment
  // of success, before this runs: while it is set the shell sends the person straight back
  // here.
  useEffect(() => {
    if (state.status !== "done") return;
    const timer = setTimeout(() => navigate("/", { replace: true }), REDIRECT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [state.status, navigate]);

  // What a submit does -- the guard, the order of clearing the recovery flag and showing
  // success, what is sent -- is lib/passwordReset.ts's submitNewPassword.
  const submit = () =>
    void submitNewPassword({ updatePassword, clearRecovery, dispatch, inFlight }, state);

  return <UpdatePasswordFormView state={state} actions={updatePasswordActions(dispatch, submit)} />;
}

function ExpiredLink({
  message,
  email,
  resetPassword,
}: {
  message: string;
  email: string | null;
  resetPassword: ReturnType<typeof useAuth>["resetPassword"];
}) {
  const [resend, dispatch] = useReducer(resetRequestReducer, initialResetRequestState);
  const inFlight = useRef(false);

  const sendNewLink = () => void submitResetRequest({ resetPassword, dispatch, inFlight }, email);

  return <LinkProblemView message={message} email={email} resend={resend} actions={{ resend: sendNewLink }} />;
}
