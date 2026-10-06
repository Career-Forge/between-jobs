// Forgot password, and setting a new one.
//
// The flow: the Login page asks Supabase to email a link (`resetPasswordForEmail`, with a
// `redirectTo` back to this app's /update-password). Following the link makes supabase-js
// open a recovery session from the URL and fire its PASSWORD_RECOVERY event; the person
// then sets a new password with `updateUser`. This module is the logic of that, with the
// two Supabase calls injected (`PasswordAuthApi`), so none of it needs a browser or a
// server to be tested; auth.tsx wires the real client and the pages draw it.
//
// TWO THINGS THIS MODULE REFUSES TO DO.
//   - Reveal whether an address has an account. The request answers with the same sentence
//     whatever the address, and never repeats the address. The auth server answers an
//     address with NO account in the same way every time (a plain success, sending nothing),
//     but an address WITH one can draw an error of its own: "you can only request this after
//     N seconds" (a second request inside the project's email frequency limit), "email rate
//     limit exceeded", "Email address ... is not authorized" (the default mail sender). Each
//     of those exists only for registered addresses, so showing its text, or showing any
//     different result for it, tells a stranger who is registered. So the text of an error
//     from the server is never shown here: a refusal the server gave (4xx) reads as the same
//     "sent" sentence, and only a failure to get an answer at all (no network, a 5xx) is an
//     error, in fixed words. That one is the platform failing, not an answer about the
//     address, and calling it "sent" would tell the person a link is coming when none is.
//     (A mail server that is down fails for registered addresses only, so during such an
//     outage a 5xx can differ by address; that is the lesser harm.)
//   - Show text taken from the URL. A dead link's hash carries `error_description`, which
//     is whatever the sender of the link wrote. Only a fixed kind ("expired", "invalid")
//     leaves `parseRecoveryLinkProblem`, and the page words it from fixed sentences, so a
//     crafted link cannot put its own message on this page.

// ── the new password ───────────────────────────────────────────────────────

// The same minimum as registration (the Login page's own field uses this constant). The
// server enforces its own minimum too (supabase/config.toml's minimum_password_length,
// also 6) and its error is shown if that is ever stricter.
export const MIN_PASSWORD_LENGTH = 6;

export type PasswordCheck = { ok: true } | { ok: false; message: string };

// Length first, then the match, so one problem is named at a time. Not trimmed: a password
// may begin or end with a space, and a trailing space is a real difference between two
// entries.
export function validateNewPassword(password: string, confirmation: string): PasswordCheck {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return { ok: false, message: `Use at least ${MIN_PASSWORD_LENGTH} characters.` };
  }
  if (password !== confirmation) {
    return { ok: false, message: "The two passwords do not match." };
  }
  return { ok: true };
}

// ── where the emailed link leads back to ───────────────────────────────────

export const UPDATE_PASSWORD_PATH = "/update-password";

// The redirect for the reset email: this app's own origin plus the update-password route,
// or null when the origin is not a plain http(s) one. Built from the ORIGIN alone (the
// caller passes `window.location.origin`), never from a query parameter or the current
// path, so no link can be made to carry the person somewhere else. The Supabase project's
// redirect allow-list decides whether the address is honoured; one that is not falls back
// to the project's site URL.
export function buildResetRedirectUrl(origin: string): string | null {
  try {
    const parsed = new URL(origin);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
    return `${parsed.origin}${UPDATE_PASSWORD_PATH}`;
  } catch {
    return null;
  }
}

// ── the two Supabase calls ─────────────────────────────────────────────────

// What the two calls resolve to, as much of it as is read. `status` is the HTTP status of
// the auth server's answer; a failure to get one at all (no network) carries 0 or none.
type AuthResult = { error: { message: string; status?: number } | null };

// The slice of `supabase.auth` this module uses.
export interface PasswordAuthApi {
  resetPasswordForEmail(email: string, options: { redirectTo: string }): Promise<AuthResult>;
  updateUser(attributes: { password: string }): Promise<AuthResult>;
}

export const RESET_SENT_MESSAGE = "If an account exists for that address, a reset link is on its way.";
export const GENERIC_RESET_FAILURE =
  "Could not send the reset link. Check your connection and try again.";
export const GENERIC_CHANGE_FAILURE =
  "Could not update your password. Check your connection and try again.";

export type ResetOutcome = { kind: "sent"; message: string } | { kind: "error"; message: string };

// Whether the auth server ANSWERED with a refusal (a 4xx), as opposed to there being no
// answer (no network, status 0) or the server failing (5xx). See the header: a refusal can
// exist only for a registered address, so it is never told apart from success.
function isRefusal(error: { status?: number }): boolean {
  const { status } = error;
  return typeof status === "number" && status >= 400 && status < 500;
}

// Plausible enough to be worth a request: something, an @, something, no spaces. The
// server and the mail provider are the real judges; this only saves a pointless call.
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+$/;

export async function requestPasswordReset(
  api: PasswordAuthApi,
  email: string,
  origin: string,
): Promise<ResetOutcome> {
  const address = email.trim();
  if (!EMAIL_SHAPE.test(address)) {
    return { kind: "error", message: "Enter the email address you signed up with." };
  }
  const redirectTo = buildResetRedirectUrl(origin);
  if (redirectTo === null) return { kind: "error", message: GENERIC_RESET_FAILURE };

  try {
    const { error } = await api.resetPasswordForEmail(address, { redirectTo });
    // Success, and a refusal, are the same sentence; the error's own text is never read.
    if (error === null || isRefusal(error)) return { kind: "sent", message: RESET_SENT_MESSAGE };
    return { kind: "error", message: GENERIC_RESET_FAILURE };
  } catch {
    return { kind: "error", message: GENERIC_RESET_FAILURE };
  }
}

export type ChangeOutcome =
  | { kind: "updated" }
  // The two entries failed the checks; nothing was sent.
  | { kind: "invalid"; message: string }
  // The server (or the network) refused; the message is the server's, shown plainly.
  | { kind: "failed"; message: string };

export async function changePassword(
  api: PasswordAuthApi,
  password: string,
  confirmation: string,
): Promise<ChangeOutcome> {
  const check = validateNewPassword(password, confirmation);
  if (!check.ok) return { kind: "invalid", message: check.message };

  try {
    const { error } = await api.updateUser({ password });
    return error === null ? { kind: "updated" } : { kind: "failed", message: error.message };
  } catch {
    return { kind: "failed", message: GENERIC_CHANGE_FAILURE };
  }
}

// ── a link that did not work ───────────────────────────────────────────────

// What went wrong with an emailed link, as a fixed kind. Nothing from the URL survives
// into it.
export type RecoveryLinkProblem = { kind: "expired" } | { kind: "invalid" };

function paramsOf(raw: string): URLSearchParams {
  return new URLSearchParams(raw.replace(/^[#?]/, ""));
}

// The auth server's error code for an emailed link that is old or was already used (mail
// scanners that open links first use them up). It belongs to emailed links alone.
const EMAIL_LINK_EXPIRED = "otp_expired";

function problemIn(raw: string): { problem: RecoveryLinkProblem; code: string | null } | null {
  const params = paramsOf(raw);
  const code = params.get("error_code");
  const error = params.get("error");
  const description = params.get("error_description");
  if (code === null && error === null && description === null) return null;
  // The code says an emailed link expired; its description says the same in words.
  const expired = code === EMAIL_LINK_EXPIRED || /expired/i.test(description ?? "");
  return { problem: { kind: expired ? "expired" : "invalid" }, code };
}

// The problem with an emailed password-reset link that a URL reports, or null when it
// reports none. The auth server puts it in the hash (`#error=access_denied&
// error_code=otp_expired&error_description=...`); a query string is read the same way. A
// good recovery link carries `access_token` and `type=recovery` instead, and is not a problem.
//
// AN AUTH ERROR IN THE URL IS NOT ALWAYS A DEAD RESET LINK. Cancelling Google's consent
// screen, or a sign-in that failed on the server, comes back to the sign-in page with the
// same `error=...` parameters, and telling that person a reset link failed would be wrong.
// So it counts only where a dead reset link lands: the page the reset email leads to
// (UPDATE_PASSWORD_PATH), or anywhere when the server's code says an emailed link expired
// (the fallback when the project does not allow-list the redirect: the link lands on the
// site's own address). Elsewhere the URL is left for the sign-in page to ignore.
export function parseRecoveryLinkProblem(
  hash: string,
  search: string,
  pathname: string,
): RecoveryLinkProblem | null {
  const found = problemIn(hash) ?? problemIn(search);
  if (found === null) return null;
  if (pathname === UPDATE_PASSWORD_PATH || found.code === EMAIL_LINK_EXPIRED) return found.problem;
  return null;
}

// Whether a URL carries an auth error of ANY kind: a dead reset link, a cancelled Google
// consent screen, a failed sign-in. The shell uses it to keep a signed-out visitor on the
// sign-in page, rather than the public landing page, when the auth server has just sent them
// back to the site's root with one of these in the URL (lib/publicRoutes.ts). It says nothing
// about WHICH error: the sign-in page decides that from `parseRecoveryLinkProblem`, and shows
// the ordinary sign-in for any error that is not a dead reset link.
export function urlCarriesAuthError(hash: string, search: string): boolean {
  return problemIn(hash) !== null || problemIn(search) !== null;
}

export function recoveryProblemMessage(problem: RecoveryLinkProblem): string {
  return problem.kind === "expired"
    ? "That link has expired or was already used. Request a new one."
    : "That link could not be used. Request a new one.";
}

// ── the recovery flag ──────────────────────────────────────────────────────

// What the page that sets the new password reports, in this tab, the moment it has: the
// same effect on the flag as the auth event for it (USER_UPDATED), without waiting for one.
export const RECOVERY_COMPLETED = "RECOVERY_COMPLETED";

// Whether the person is in the middle of a password recovery, as supabase-js reports it:
// the PASSWORD_RECOVERY event sets it; signing out or a completed password update clears
// it. Every other event -- and a recovery fires several, among them INITIAL_SESSION,
// SIGNED_IN and TOKEN_REFRESHED -- leaves it alone.
//
// A COMPLETED UPDATE MUST CLEAR IT IN EVERY TAB. supabase-js relays each auth event to the
// person's other tabs, PASSWORD_RECOVERY included: the tab that was open on the sign-in
// page when the emailed link opened in a new one also gets the recovery session, sets the
// flag, and moves to the update page. When the password is then set in the new tab, the
// only thing the old tab hears is USER_UPDATED. If that did not clear the flag, the old
// tab would stay on the update page for good, sending every navigation back to it. (The
// web app updates nothing but the password, so USER_UPDATED means exactly that here.)
export function recoveryAfterEvent(current: boolean, event: string): boolean {
  if (event === "PASSWORD_RECOVERY") return true;
  if (event === "SIGNED_OUT" || event === "USER_UPDATED" || event === RECOVERY_COMPLETED) return false;
  return current;
}

// Where the shell must send a person in recovery: to the update page, from anywhere else.
export function recoveryRedirect(recovery: boolean, pathname: string): string | null {
  return recovery && pathname !== UPDATE_PASSWORD_PATH ? UPDATE_PASSWORD_PATH : null;
}

// ── form state ─────────────────────────────────────────────────────────────

export interface ResetRequestState {
  email: string;
  busy: boolean;
  outcome: ResetOutcome | null;
}

export type ResetRequestEvent =
  | { type: "typed"; value: string }
  | { type: "submitted" }
  | { type: "finished"; outcome: ResetOutcome };

export const initialResetRequestState: ResetRequestState = { email: "", busy: false, outcome: null };

export function resetRequestReducer(state: ResetRequestState, event: ResetRequestEvent): ResetRequestState {
  switch (event.type) {
    case "typed":
      return { ...state, email: event.value, outcome: null };
    case "submitted":
      return { ...state, busy: true, outcome: null };
    case "finished":
      return { ...state, busy: false, outcome: event.outcome };
  }
}

export interface UpdatePasswordState {
  password: string;
  confirm: string;
  status: "idle" | "submitting" | "error" | "done";
  message: string | null;
}

export type UpdatePasswordEvent =
  | { type: "typed"; field: "password" | "confirm"; value: string }
  | { type: "submitted" }
  | { type: "failed"; message: string }
  | { type: "succeeded" };

export const initialUpdatePasswordState: UpdatePasswordState = {
  password: "",
  confirm: "",
  status: "idle",
  message: null,
};

export function updatePasswordReducer(
  state: UpdatePasswordState,
  event: UpdatePasswordEvent,
): UpdatePasswordState {
  switch (event.type) {
    case "typed":
      return {
        ...state,
        [event.field]: event.value,
        status: state.status === "error" ? "idle" : state.status,
        message: state.status === "error" ? null : state.message,
      };
    case "submitted":
      return { ...state, status: "submitting", message: null };
    // The typed passwords are kept, so a retry does not mean typing both again.
    case "failed":
      return { ...state, status: "error", message: event.message };
    // Done: nothing typed is kept.
    case "succeeded":
      return { password: "", confirm: "", status: "done", message: null };
  }
}

// ── submitting the forms ───────────────────────────────────────────────────

// The guard against a double submit. A ref's shape, not component state: state only changes
// on the next render, and a second click can land before it.
export interface InFlight {
  current: boolean;
}

// What pressing "Update password" does. Nothing is sent while a send is under way. A password
// that was set clears the recovery flag BEFORE it shows success: while the flag is set the
// shell sends the person straight back to the update page, so the redirect that follows the
// success message would be undone. The two typed passwords go to `updatePassword` as two
// separate arguments, so a mismatch is caught where the checks are. A call that throws
// (nothing here does, but the guard must not depend on it) reads as a failed one, and the
// guard is released whatever happens: a failed attempt must leave the button usable.
export async function submitNewPassword(
  deps: {
    updatePassword: (password: string, confirmation: string) => Promise<ChangeOutcome>;
    clearRecovery: () => void;
    dispatch: (event: UpdatePasswordEvent) => void;
    inFlight: InFlight;
  },
  fields: Pick<UpdatePasswordState, "password" | "confirm">,
): Promise<void> {
  if (deps.inFlight.current) return;
  deps.inFlight.current = true;
  try {
    deps.dispatch({ type: "submitted" });
    let outcome: ChangeOutcome;
    try {
      outcome = await deps.updatePassword(fields.password, fields.confirm);
    } catch {
      outcome = { kind: "failed", message: GENERIC_CHANGE_FAILURE };
    }
    if (outcome.kind === "updated") {
      deps.clearRecovery();
      deps.dispatch({ type: "succeeded" });
    } else {
      deps.dispatch({ type: "failed", message: outcome.message });
    }
  } finally {
    deps.inFlight.current = false;
  }
}

// What pressing "Send reset link" (or "Email me a new link", for an address already known)
// does: the same guard, and the same promise that it is released whatever happens, so a
// refusal does not leave the button dead. A null address (a signed-in session with no email)
// sends nothing.
export async function submitResetRequest(
  deps: {
    resetPassword: (email: string) => Promise<ResetOutcome>;
    dispatch: (event: ResetRequestEvent) => void;
    inFlight: InFlight;
  },
  email: string | null,
): Promise<void> {
  if (deps.inFlight.current || email === null) return;
  deps.inFlight.current = true;
  try {
    deps.dispatch({ type: "submitted" });
    let outcome: ResetOutcome;
    try {
      outcome = await deps.resetPassword(email);
    } catch {
      outcome = { kind: "error", message: GENERIC_RESET_FAILURE };
    }
    deps.dispatch({ type: "finished", outcome });
  } finally {
    deps.inFlight.current = false;
  }
}

// What each field of a form hands back, as the view's actions: which typed value goes to
// which field is the part that is easy to cross.
export function updatePasswordActions(
  dispatch: (event: UpdatePasswordEvent) => void,
  submit: () => void,
) {
  return {
    setPassword: (value: string) => dispatch({ type: "typed", field: "password", value }),
    setConfirm: (value: string) => dispatch({ type: "typed", field: "confirm", value }),
    submit,
  };
}

export function resetRequestActions(
  dispatch: (event: ResetRequestEvent) => void,
  submit: () => void,
  back: () => void,
) {
  return {
    setEmail: (value: string) => dispatch({ type: "typed", value }),
    submit,
    back,
  };
}

// ── the Login page's three modes ───────────────────────────────────────────

export type LoginMode = "sign_in" | "register" | "reset";

export type LoginEvent =
  // "Forgot password?"
  | "forgot"
  // "Back to sign in", from the reset form
  | "back"
  // "Need an account? Register" / "Already have an account? Sign in"
  | "toggle"
  // Registered, but the project wants the address confirmed before a first sign-in.
  | "confirmation_required";

// The sentence about a dead emailed link that Login opens with, or null when the URL does
// not carry one (see parseRecoveryLinkProblem: a failed Google sign-in is not one).
export function loginLinkProblem(hash: string, search: string, pathname: string): string | null {
  const problem = parseRecoveryLinkProblem(hash, search, pathname);
  return problem === null ? null : recoveryProblemMessage(problem);
}

// The query string that opens the sign-in page on its "Create account" form. The landing page's
// Create account link carries it; nothing else in the app does. Only this one value is read, so
// a link can never open the page on the reset form or on anything else.
export const LOGIN_REGISTER_QUERY = "?mode=register";

function registerHinted(search: string): boolean {
  return paramsOf(search).get("mode") === "register";
}

// A person who followed a dead link starts on the reset form, to ask for a new one -- that
// wins over a register hint, so a stale link is never hidden behind another form. Otherwise
// the register hint (see LOGIN_REGISTER_QUERY) opens "Create account"; everything else
// starts on sign-in.
export function initialLoginMode(linkProblem: string | null, search = ""): LoginMode {
  if (linkProblem !== null) return "reset";
  return registerHinted(search) ? "register" : "sign_in";
}

export function loginModeAfter(mode: LoginMode, event: LoginEvent): LoginMode {
  switch (event) {
    case "forgot":
      return "reset";
    case "back":
    case "confirmation_required":
      return "sign_in";
    case "toggle":
      return mode === "sign_in" ? "register" : "sign_in";
  }
}

// Whether to take the dead link's error (and the path it arrived on) out of the address bar,
// so a reload, or signing in afterwards, does not replay it. Only for a dead link: any other
// signed-out visit keeps its address, or a deep link would be lost to the sign-in page.
export function shouldClearUrl(linkProblem: string | null): boolean {
  return linkProblem !== null;
}
