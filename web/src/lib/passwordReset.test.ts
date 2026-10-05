import { describe, expect, it } from "vitest";
import loginSource from "../pages/Login.tsx?raw";
import {
  GENERIC_CHANGE_FAILURE,
  GENERIC_RESET_FAILURE,
  MIN_PASSWORD_LENGTH,
  RECOVERY_COMPLETED,
  RESET_SENT_MESSAGE,
  UPDATE_PASSWORD_PATH,
  buildResetRedirectUrl,
  changePassword,
  initialLoginMode,
  initialResetRequestState,
  initialUpdatePasswordState,
  loginLinkProblem,
  loginModeAfter,
  parseRecoveryLinkProblem,
  recoveryAfterEvent,
  recoveryProblemMessage,
  recoveryRedirect,
  requestPasswordReset,
  resetRequestActions,
  resetRequestReducer,
  shouldClearUrl,
  submitNewPassword,
  submitResetRequest,
  updatePasswordActions,
  updatePasswordReducer,
  validateNewPassword,
  type ChangeOutcome,
  type InFlight,
  type LoginEvent,
  type LoginMode,
  type PasswordAuthApi,
  type ResetOutcome,
  type ResetRequestEvent,
  type UpdatePasswordEvent,
  type UpdatePasswordState,
} from "./passwordReset";

// The logic of "forgot password" and "set a new password", none of which needs a browser:
// what a new password must satisfy, where the emailed link sends the person back to, what
// the request says (and must NOT say), how a dead link is recognised without ever echoing
// its text, and when the recovery flag is set and cleared. The supabase calls are injected.

type AuthResult = { error: { message: string; status?: number } | null };

function fakeApi(options: {
  reset?: AuthResult | Error;
  update?: AuthResult | Error;
} = {}) {
  const calls: Array<[string, ...unknown[]]> = [];
  const respond = async (result: AuthResult | Error | undefined): Promise<AuthResult> => {
    if (result instanceof Error) throw result;
    return result ?? { error: null };
  };
  const api: PasswordAuthApi = {
    resetPasswordForEmail: async (email, options_) => {
      calls.push(["resetPasswordForEmail", email, options_]);
      return respond(options.reset);
    },
    updateUser: async (attributes) => {
      calls.push(["updateUser", attributes]);
      return respond(options.update);
    },
  };
  return { api, calls };
}

describe("validateNewPassword", () => {
  it("takes a password of the registration minimum that matches its confirmation", () => {
    expect(validateNewPassword("abcdef", "abcdef")).toEqual({ ok: true });
    expect(validateNewPassword("a much longer passphrase", "a much longer passphrase")).toEqual({ ok: true });
  });

  it("uses the same minimum as registration (Login.tsx), and says it", () => {
    expect(MIN_PASSWORD_LENGTH).toBe(6);
    expect(loginSource).toContain("MIN_PASSWORD_LENGTH");
    expect(loginSource).not.toMatch(/minLength=\{[^}]*\b6\b/);
    const short = validateNewPassword("a".repeat(MIN_PASSWORD_LENGTH - 1), "a".repeat(MIN_PASSWORD_LENGTH - 1));
    expect(short).toEqual({ ok: false, message: `Use at least ${MIN_PASSWORD_LENGTH} characters.` });
  });

  it("refuses an empty password", () => {
    expect(validateNewPassword("", "")).toEqual({
      ok: false,
      message: `Use at least ${MIN_PASSWORD_LENGTH} characters.`,
    });
  });

  it("refuses a confirmation that does not match", () => {
    expect(validateNewPassword("abcdef", "abcdeg")).toEqual({
      ok: false,
      message: "The two passwords do not match.",
    });
    expect(validateNewPassword("abcdef", "")).toEqual({
      ok: false,
      message: "The two passwords do not match.",
    });
  });

  it("is case-sensitive: a confirmation that differs only by case does not match", () => {
    const mismatch = { ok: false, message: "The two passwords do not match." };
    expect(validateNewPassword("abcdef", "ABCDEF")).toEqual(mismatch);
    expect(validateNewPassword("Abcdef", "abcdef")).toEqual(mismatch);
    expect(validateNewPassword("abcdeF", "abcdef")).toEqual(mismatch);
  });

  it("checks the length before the match, so one problem is named at a time", () => {
    expect(validateNewPassword("abc", "xyz")).toEqual({
      ok: false,
      message: `Use at least ${MIN_PASSWORD_LENGTH} characters.`,
    });
  });

  it("does not trim: a password may begin or end with a space, and a space is a difference", () => {
    expect(validateNewPassword(" abcde", " abcde")).toEqual({ ok: true });
    expect(validateNewPassword("abcdef ", "abcdef").ok).toBe(false);
    // Six spaces are six characters: whether that is a good password is not this check's call.
    expect(validateNewPassword("      ", "      ")).toEqual({ ok: true });
  });
});

describe("buildResetRedirectUrl", () => {
  it("is the origin plus the update-password route", () => {
    expect(buildResetRedirectUrl("https://between-jobs.tech")).toBe("https://between-jobs.tech/update-password");
    expect(buildResetRedirectUrl("http://localhost:5173")).toBe("http://localhost:5173/update-password");
    expect(UPDATE_PASSWORD_PATH).toBe("/update-password");
  });

  it("keeps only the origin, whatever else was handed in", () => {
    expect(buildResetRedirectUrl("https://app.example/")).toBe("https://app.example/update-password");
    expect(buildResetRedirectUrl("https://app.example/path?next=https://evil.example#x")).toBe(
      "https://app.example/update-password",
    );
    expect(buildResetRedirectUrl("https://user:pw@app.example")).toBe("https://app.example/update-password");
  });

  it.each([
    ["an opaque origin", "null"],
    ["an empty string", ""],
    ["a javascript: URL", "javascript:alert(1)"],
    ["a data: URL", "data:text/html,x"],
    ["a file: URL", "file:///etc/passwd"],
    ["an ftp: URL", "ftp://app.example"],
    ["something that is not a URL", "not a url"],
  ])("is null for %s", (_name, origin) => {
    expect(buildResetRedirectUrl(origin)).toBeNull();
  });
});

describe("requestPasswordReset", () => {
  it("asks for a link to be sent, with the redirect built from the origin only", async () => {
    const { api, calls } = fakeApi();
    const outcome = await requestPasswordReset(api, "  pat@example.com ", "https://between-jobs.tech");
    expect(outcome).toEqual({ kind: "sent", message: RESET_SENT_MESSAGE });
    expect(calls).toEqual([
      ["resetPasswordForEmail", "pat@example.com", { redirectTo: "https://between-jobs.tech/update-password" }],
    ]);
  });

  it("does not reveal whether the address has an account", async () => {
    expect(RESET_SENT_MESSAGE).toBe("If an account exists for that address, a reset link is on its way.");
    const a = await requestPasswordReset(fakeApi().api, "known@example.com", "https://x.example");
    const b = await requestPasswordReset(fakeApi().api, "nobody@example.com", "https://x.example");
    expect(a).toEqual(b);
    // The address is not echoed back either.
    expect(JSON.stringify(a)).not.toContain("example.com");
  });

  // The auth server answers an address with no account with a plain success, and an address
  // WITH one can draw an error of its own (below). Each of these exists only for registered
  // addresses, so any difference on screen tells a stranger who is registered.
  describe.each([
    [
      "a second request inside the email frequency limit",
      { message: "For security purposes, you can only request this after 49 seconds.", status: 429 },
    ],
    ["the project-wide email rate limit", { message: "email rate limit exceeded", status: 429 }],
    [
      "an address the default mail sender is not authorised to write to",
      { message: "Email address \"victim@example.com\" is not authorized", status: 400 },
    ],
    ["an address the mail provider rejected", { message: "Email address is invalid", status: 400 }],
    ["a refusal with no message at all", { message: "", status: 422 }],
  ])("for %s", (_name, error) => {
    it("is exactly the outcome of a request that worked, so the form cannot tell them apart", async () => {
      const worked = await requestPasswordReset(fakeApi().api, "victim@example.com", "https://x.example");
      const refused = await requestPasswordReset(
        fakeApi({ reset: { error } }).api,
        "victim@example.com",
        "https://x.example",
      );
      expect(refused).toEqual(worked);
      expect(refused).toEqual({ kind: "sent", message: RESET_SENT_MESSAGE });
    });

    it("carries nothing of the error, and not the address", async () => {
      const refused = await requestPasswordReset(
        fakeApi({ reset: { error } }).api,
        "victim@example.com",
        "https://x.example",
      );
      const text = JSON.stringify(refused);
      expect(text).not.toContain("victim@example.com");
      expect(text).not.toContain("seconds");
      expect(text).not.toContain("rate limit");
      expect(text).not.toContain("authorized");
    });
  });

  // A failure to get an answer, or the server failing, does not depend on the address.
  it.each([
    ["a thrown network error", new TypeError("Failed to fetch")],
    ["no status at all", { error: { message: "unknown" } }],
    ["status 0 (supabase-js's own retryable network failure)", { error: { message: "fetch failed", status: 0 } }],
    ["a 503", { error: { message: "Service Unavailable", status: 503 } }],
    ["a 500", { error: { message: "Error sending recovery email", status: 500 } }],
  ])("says so in fixed words for %s, never in the server's", async (_name, reset) => {
    const { api } = fakeApi({ reset });
    const outcome = await requestPasswordReset(api, "pat@example.com", "https://x.example");
    expect(outcome).toEqual({ kind: "error", message: GENERIC_RESET_FAILURE });
  });

  it("does not throw when the call itself fails", async () => {
    const { api } = fakeApi({ reset: new TypeError("Failed to fetch") });
    await expect(requestPasswordReset(api, "pat@example.com", "https://x.example")).resolves.toEqual({
      kind: "error",
      message: GENERIC_RESET_FAILURE,
    });
  });

  it("does not call out for an address that cannot be one", async () => {
    for (const email of ["", "   ", "no-at-sign", "@", "a@", "@b.example", "a b@example.com", "a@b@c", "a@b c"]) {
      const { api, calls } = fakeApi();
      const outcome = await requestPasswordReset(api, email, "https://x.example");
      expect(outcome).toEqual({ kind: "error", message: "Enter the email address you signed up with." });
      expect(calls).toEqual([]);
    }
  });

  it("does not call out when it cannot work out where the link should lead back to", async () => {
    const { api, calls } = fakeApi();
    const outcome = await requestPasswordReset(api, "pat@example.com", "null");
    expect(outcome.kind).toBe("error");
    expect(calls).toEqual([]);
  });
});

describe("changePassword", () => {
  it("updates the password of the person whose recovery session this is", async () => {
    const { api, calls } = fakeApi();
    expect(await changePassword(api, "a-new-password", "a-new-password")).toEqual({ kind: "updated" });
    expect(calls).toEqual([["updateUser", { password: "a-new-password" }]]);
  });

  it("does not call out for a password that fails the checks, and says which", async () => {
    const short = fakeApi();
    expect(await changePassword(short.api, "abc", "abc")).toEqual({
      kind: "invalid",
      message: `Use at least ${MIN_PASSWORD_LENGTH} characters.`,
    });
    const mismatch = fakeApi();
    expect(await changePassword(mismatch.api, "abcdef", "abcdeg")).toEqual({
      kind: "invalid",
      message: "The two passwords do not match.",
    });
    expect(short.calls).toEqual([]);
    expect(mismatch.calls).toEqual([]);
  });

  it("does not send a confirmation that differs only by case", async () => {
    const { api, calls } = fakeApi();
    expect(await changePassword(api, "abcdef", "ABCDEF")).toEqual({
      kind: "invalid",
      message: "The two passwords do not match.",
    });
    expect(calls).toEqual([]);
  });

  it("shows the server's error plainly", async () => {
    const { api } = fakeApi({
      update: { error: { message: "New password should be different from the old password." } },
    });
    expect(await changePassword(api, "abcdef", "abcdef")).toEqual({
      kind: "failed",
      message: "New password should be different from the old password.",
    });
  });

  it("says so plainly, and does not throw, when the call itself fails", async () => {
    const { api } = fakeApi({ update: new TypeError("Failed to fetch") });
    expect(await changePassword(api, "abcdef", "abcdef")).toEqual({
      kind: "failed",
      message: GENERIC_CHANGE_FAILURE,
    });
  });
});

// Where a dead reset link lands: the page the reset email leads to.
const onUpdatePage = (hash: string, search = "") => parseRecoveryLinkProblem(hash, search, UPDATE_PASSWORD_PATH);

describe("parseRecoveryLinkProblem", () => {
  it("recognises the expired-or-used link the auth server reports", () => {
    const hash =
      "#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired";
    expect(onUpdatePage(hash)).toEqual({ kind: "expired" });
  });

  it("reads a bare query string the same way (a different sign-in flow puts it there)", () => {
    expect(onUpdatePage("", "?error=access_denied&error_code=otp_expired")).toEqual({
      kind: "expired",
    });
  });

  it("recognises expiry from the description when no code was sent", () => {
    expect(onUpdatePage("#error=access_denied&error_description=Email%20link%20has%20expired")).toEqual({
      kind: "expired",
    });
  });

  it("reads expiry in any case", () => {
    expect(onUpdatePage("#error_description=Link%20Expired")).toEqual({ kind: "expired" });
    expect(onUpdatePage("#error_description=LINK%20HAS%20EXPIRED")).toEqual({ kind: "expired" });
  });

  it("reads a code on its own, without the error parameter that normally comes with it", () => {
    expect(onUpdatePage("#error_code=otp_expired")).toEqual({ kind: "expired" });
    expect(onUpdatePage("#error_code=whatever")).toEqual({ kind: "invalid" });
  });

  it("reads any other auth error on the reset page as a link that could not be used", () => {
    expect(onUpdatePage("#error=server_error&error_code=unexpected_failure")).toEqual({
      kind: "invalid",
    });
    expect(onUpdatePage("#error=access_denied")).toEqual({ kind: "invalid" });
    expect(onUpdatePage("#error_description=something")).toEqual({ kind: "invalid" });
  });

  it("is null for a hash that carries no error, including the real recovery one", () => {
    expect(onUpdatePage("")).toBeNull();
    expect(onUpdatePage("#")).toBeNull();
    expect(onUpdatePage("#section")).toBeNull();
    expect(
      onUpdatePage("#access_token=abc&expires_in=3600&refresh_token=def&token_type=bearer&type=recovery"),
    ).toBeNull();
  });

  it("prefers the hash, where the auth server puts it, over the query string", () => {
    expect(onUpdatePage("#error=x", "?error_code=otp_expired")).toEqual({ kind: "invalid" });
    expect(onUpdatePage("#error_code=otp_expired", "?error=x")).toEqual({ kind: "expired" });
  });

  it("falls back to the query string when the hash has no error", () => {
    expect(onUpdatePage("#section", "?error=x")).toEqual({ kind: "invalid" });
    expect(onUpdatePage("", "?error=x")).toEqual({ kind: "invalid" });
  });

  it("never carries the URL's own text out: only a fixed kind", () => {
    const hostile =
      "#error=<img src=x onerror=alert(1)>&error_code=%3Cscript%3E&error_description=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E";
    const problem = onUpdatePage(hostile);
    expect(problem).toEqual({ kind: "invalid" });
    expect(JSON.stringify(problem)).not.toMatch(/script|img|alert/);
    expect(recoveryProblemMessage(problem!)).not.toMatch(/script|img|alert/);
  });

  it("does not throw on a malformed hash", () => {
    expect(() => onUpdatePage("#error=%E0%A4%A&error_code=%")).not.toThrow();
    expect(onUpdatePage("#error=%E0%A4%A")).toEqual({ kind: "invalid" });
  });

  // An auth error in the URL is not always a dead reset link: a cancelled or failed Google
  // sign-in comes back to the sign-in page ("/") with the same parameters.
  describe("on a page other than the one the reset email leads to", () => {
    it.each([
      ["a cancelled Google sign-in (hash)", "#error=access_denied&error_code=access_denied&error_description=The+user+denied", ""],
      ["a bad OAuth callback (query)", "", "?error=access_denied&error_code=bad_oauth_callback&error_description=User+cancelled"],
      [
        "a server failure during sign-in",
        "#error=server_error&error_code=unexpected_failure&error_description=Unable+to+exchange+external+code",
        "",
      ],
      ["a bare error", "#error=access_denied", ""],
      ["an error code of no kind we know", "#error_code=bad_oauth_state", ""],
      // Words in a description are not enough off the reset page: an OAuth state can "expire" too.
      ["an OAuth description that merely says expired", "#error=invalid_request&error_code=flow_state_expired&error_description=Flow+state+expired", ""],
    ])("is not a dead reset link: %s", (_name, hash, search) => {
      expect(parseRecoveryLinkProblem(hash, search, "/")).toBeNull();
      expect(parseRecoveryLinkProblem(hash, search, "/applications/123")).toBeNull();
    });

    it("is still an expired link when the server's own code says an emailed link expired", () => {
      // The project's redirect allow-list did not take the update-password address, so the
      // link fell back to the site's own address.
      expect(
        parseRecoveryLinkProblem("#error=access_denied&error_code=otp_expired", "", "/"),
      ).toEqual({ kind: "expired" });
      expect(parseRecoveryLinkProblem("", "?error_code=otp_expired", "/")).toEqual({ kind: "expired" });
    });

    it("is null with no error at all, wherever it is", () => {
      expect(parseRecoveryLinkProblem("", "", "/")).toBeNull();
      expect(parseRecoveryLinkProblem("", "", UPDATE_PASSWORD_PATH)).toBeNull();
    });
  });

  it("treats any error on the reset page itself as a dead reset link (that is the only way to get there signed out)", () => {
    expect(parseRecoveryLinkProblem("#error=access_denied", "", "/update-password")).toEqual({ kind: "invalid" });
  });
});

describe("loginLinkProblem", () => {
  it("is the fixed sentence for a dead reset link, and null for everything else", () => {
    expect(
      loginLinkProblem("#error=access_denied&error_code=otp_expired", "", "/update-password"),
    ).toBe("That link has expired or was already used. Request a new one.");
    expect(loginLinkProblem("#error=access_denied", "", "/update-password")).toBe(
      "That link could not be used. Request a new one.",
    );
    expect(loginLinkProblem("", "", "/")).toBeNull();
    expect(loginLinkProblem("#error=access_denied&error_code=bad_oauth_callback", "", "/")).toBeNull();
  });
});

describe("recoveryProblemMessage", () => {
  it("says what happened in fixed words", () => {
    expect(recoveryProblemMessage({ kind: "expired" })).toBe(
      "That link has expired or was already used. Request a new one.",
    );
    expect(recoveryProblemMessage({ kind: "invalid" })).toBe(
      "That link could not be used. Request a new one.",
    );
  });
});

describe("recoveryAfterEvent", () => {
  it("is set by the password-recovery event", () => {
    expect(recoveryAfterEvent(false, "PASSWORD_RECOVERY")).toBe(true);
    expect(recoveryAfterEvent(true, "PASSWORD_RECOVERY")).toBe(true);
  });

  it("is cleared by signing out", () => {
    expect(recoveryAfterEvent(true, "SIGNED_OUT")).toBe(false);
    expect(recoveryAfterEvent(false, "SIGNED_OUT")).toBe(false);
  });

  // supabase-js relays every auth event to the person's other tabs, so the update in one
  // tab is the only thing another tab hears about it.
  it("is cleared by a completed password update (USER_UPDATED), which every tab hears", () => {
    expect(recoveryAfterEvent(true, "USER_UPDATED")).toBe(false);
    expect(recoveryAfterEvent(false, "USER_UPDATED")).toBe(false);
  });

  it("is cleared by the update page's own report that it finished, the same way", () => {
    expect(recoveryAfterEvent(true, RECOVERY_COMPLETED)).toBe(false);
    expect(recoveryAfterEvent(false, RECOVERY_COMPLETED)).toBe(false);
  });

  it("is untouched by every other event, which fire all the time during a recovery", () => {
    for (const event of ["INITIAL_SESSION", "SIGNED_IN", "TOKEN_REFRESHED", "MFA_CHALLENGE_VERIFIED", "SOMETHING_NEW"]) {
      expect(recoveryAfterEvent(true, event)).toBe(true);
      expect(recoveryAfterEvent(false, event)).toBe(false);
    }
  });

  describe("across two tabs", () => {
    // Tab A is open on the sign-in page when the emailed link opens in tab B. Both hear
    // PASSWORD_RECOVERY; the password is set in B; both hear USER_UPDATED.
    const events = (tab: boolean, sequence: string[]) => sequence.reduce(recoveryAfterEvent, tab);

    it("ends the recovery in the tab that did NOT set the password, so it is not left pinned", () => {
      const tabA = events(false, ["PASSWORD_RECOVERY", "SIGNED_IN", "USER_UPDATED"]);
      expect(tabA).toBe(false);
      expect(recoveryRedirect(tabA, "/")).toBeNull();
    });

    it("ends it in the tab that did, whichever of the event and its own report comes first", () => {
      expect(events(false, ["PASSWORD_RECOVERY", "USER_UPDATED", RECOVERY_COMPLETED])).toBe(false);
      expect(events(false, ["PASSWORD_RECOVERY", RECOVERY_COMPLETED, "USER_UPDATED"])).toBe(false);
    });

    it("leaves a recovery that has not finished pinned to the update page", () => {
      const state = events(false, ["PASSWORD_RECOVERY", "SIGNED_IN", "TOKEN_REFRESHED"]);
      expect(state).toBe(true);
      expect(recoveryRedirect(state, "/")).toBe("/update-password");
    });
  });
});

describe("recoveryRedirect", () => {
  it("sends a person in recovery to the update page from anywhere else", () => {
    expect(recoveryRedirect(true, "/")).toBe("/update-password");
    expect(recoveryRedirect(true, "/applications/123")).toBe("/update-password");
    expect(recoveryRedirect(true, "/profile/integrations")).toBe("/update-password");
  });

  it("leaves them there once they are on it", () => {
    expect(recoveryRedirect(true, "/update-password")).toBeNull();
  });

  it("is exact about the path: a longer or different one is somewhere else", () => {
    expect(recoveryRedirect(true, "/update-password/extra")).toBe("/update-password");
    expect(recoveryRedirect(true, "/update-passwordx")).toBe("/update-password");
  });

  it("does nothing when there is no recovery", () => {
    expect(recoveryRedirect(false, "/")).toBeNull();
    expect(recoveryRedirect(false, "/update-password")).toBeNull();
  });

  it("follows the flag through a whole recovery: sent to the page, and free once it is done", () => {
    let recovery = recoveryAfterEvent(false, "PASSWORD_RECOVERY");
    expect(recoveryRedirect(recovery, "/")).toBe("/update-password");
    recovery = recoveryAfterEvent(recovery, RECOVERY_COMPLETED);
    // The page's own redirect to Today is no longer bounced back.
    expect(recoveryRedirect(recovery, "/")).toBeNull();
  });
});

describe("resetRequestReducer", () => {
  it("starts empty and idle", () => {
    expect(initialResetRequestState).toEqual({ email: "", busy: false, outcome: null });
  });

  it("keeps what is typed, and clears an old outcome when typing starts again", () => {
    let state = resetRequestReducer(initialResetRequestState, { type: "typed", value: "pat@example.com" });
    expect(state.email).toBe("pat@example.com");
    state = resetRequestReducer(state, { type: "submitted" });
    expect(state).toMatchObject({ busy: true, outcome: null });
    state = resetRequestReducer(state, { type: "finished", outcome: { kind: "error", message: "Too often." } });
    expect(state).toMatchObject({ busy: false, outcome: { kind: "error", message: "Too often." } });
    state = resetRequestReducer(state, { type: "typed", value: "pat@example.org" });
    expect(state.outcome).toBeNull();
  });

  it("clears the last error when it is sent again, so it is not shown beside 'Sending...'", () => {
    const state = resetRequestReducer(
      { email: "pat@example.com", busy: false, outcome: { kind: "error", message: "Too often." } },
      { type: "submitted" },
    );
    expect(state).toEqual({ email: "pat@example.com", busy: true, outcome: null });
  });

  it("holds the confirmation once sent", () => {
    const state = resetRequestReducer(
      { email: "pat@example.com", busy: true, outcome: null },
      { type: "finished", outcome: { kind: "sent", message: RESET_SENT_MESSAGE } },
    );
    expect(state).toEqual({
      email: "pat@example.com",
      busy: false,
      outcome: { kind: "sent", message: RESET_SENT_MESSAGE },
    });
  });
});

describe("updatePasswordReducer", () => {
  const typed = (password: string, confirm: string): UpdatePasswordState => ({
    ...initialUpdatePasswordState,
    password,
    confirm,
  });

  it("starts empty and idle", () => {
    expect(initialUpdatePasswordState).toEqual({ password: "", confirm: "", status: "idle", message: null });
  });

  it("keeps what is typed in the two fields, and drops an old error when typing starts again", () => {
    let state = updatePasswordReducer(initialUpdatePasswordState, { type: "typed", field: "password", value: "abc" });
    state = updatePasswordReducer(state, { type: "typed", field: "confirm", value: "abd" });
    expect(state).toMatchObject({ password: "abc", confirm: "abd", status: "idle" });
    state = updatePasswordReducer(state, { type: "failed", message: "Too short." });
    expect(state).toMatchObject({ status: "error", message: "Too short." });
    state = updatePasswordReducer(state, { type: "typed", field: "password", value: "abcdef" });
    expect(state).toMatchObject({ status: "idle", message: null, password: "abcdef", confirm: "abd" });
  });

  it("goes submitting, then done, and blanks the fields once the password is set", () => {
    let state = updatePasswordReducer(
      { password: "abcdef", confirm: "abcdef", status: "idle", message: null },
      { type: "submitted" },
    );
    expect(state.status).toBe("submitting");
    state = updatePasswordReducer(state, { type: "succeeded" });
    expect(state).toEqual({ password: "", confirm: "", status: "done", message: null });
  });

  it("keeps the typed passwords after a failure, so a retry does not mean typing both again", () => {
    // The real path: typed, then submitted, then failed -- not a state written out by hand.
    let state = typed("abcdef", "abcdef");
    state = updatePasswordReducer(state, { type: "submitted" });
    expect(state).toEqual({ password: "abcdef", confirm: "abcdef", status: "submitting", message: null });
    state = updatePasswordReducer(state, { type: "failed", message: "Network down." });
    expect(state).toEqual({ password: "abcdef", confirm: "abcdef", status: "error", message: "Network down." });
  });

  it("clears the last error when it is submitted again, and still keeps the passwords", () => {
    const state = updatePasswordReducer(
      { password: "abcdef", confirm: "abcdef", status: "error", message: "old" },
      { type: "submitted" },
    );
    expect(state).toEqual({ password: "abcdef", confirm: "abcdef", status: "submitting", message: null });
  });

  it("does not let typing move a submitting or finished form back to idle", () => {
    for (const status of ["submitting", "done"] as const) {
      const state = updatePasswordReducer(
        { password: "abcdef", confirm: "abcdef", status, message: null },
        { type: "typed", field: "confirm", value: "abcdeg" },
      );
      expect(state).toMatchObject({ status, message: null, confirm: "abcdeg" });
    }
  });
});

// ── what pressing the buttons does ─────────────────────────────────────────

// A promise that is settled by hand, so a test can hold a send "in flight".
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("submitNewPassword", () => {
  function setup(updatePassword: (p: string, c: string) => Promise<ChangeOutcome>) {
    const log: string[] = [];
    const events: UpdatePasswordEvent[] = [];
    const sent: Array<[string, string]> = [];
    const inFlight: InFlight = { current: false };
    const deps = {
      updatePassword: (password: string, confirmation: string) => {
        sent.push([password, confirmation]);
        return updatePassword(password, confirmation);
      },
      clearRecovery: () => void log.push("clearRecovery"),
      dispatch: (event: UpdatePasswordEvent) => {
        events.push(event);
        log.push(event.type);
      },
      inFlight,
    };
    return { deps, log, events, sent, inFlight };
  }
  const fields = { password: "first-one", confirm: "second-one" };

  it("clears the recovery flag BEFORE it shows success, and never reports a failure", async () => {
    const { deps, log, events } = setup(async () => ({ kind: "updated" }));
    await submitNewPassword(deps, fields);
    expect(log).toEqual(["submitted", "clearRecovery", "succeeded"]);
    expect(events.some((e) => e.type === "failed")).toBe(false);
  });

  it.each([
    ["the checks refused", { kind: "invalid", message: "The two passwords do not match." } as const],
    ["the server refused", { kind: "failed", message: "New password should be different from the old password." } as const],
  ])("when %s: only 'submitted' then 'failed' with that exact message, and the flag stays set", async (_name, outcome) => {
    const { deps, log, events } = setup(async () => outcome);
    await submitNewPassword(deps, fields);
    expect(events).toEqual([{ type: "submitted" }, { type: "failed", message: outcome.message }]);
    expect(log).not.toContain("clearRecovery");
    expect(log).not.toContain("succeeded");
  });

  it("sends the password and its confirmation as two separate arguments", async () => {
    const { deps, sent } = setup(async () => ({ kind: "updated" }));
    await submitNewPassword(deps, fields);
    expect(sent).toEqual([["first-one", "second-one"]]);
  });

  it("releases the guard after a failure, so a second attempt goes through", async () => {
    const { deps, sent, inFlight } = setup(async () => ({ kind: "failed", message: "Too short." }));
    await submitNewPassword(deps, fields);
    expect(inFlight.current).toBe(false);
    await submitNewPassword(deps, fields);
    expect(sent).toHaveLength(2);
  });

  it("releases the guard after a success too", async () => {
    const { deps, inFlight } = setup(async () => ({ kind: "updated" }));
    await submitNewPassword(deps, fields);
    expect(inFlight.current).toBe(false);
  });

  it("reads a thrown update as a failed one, and releases the guard", async () => {
    const { deps, events, inFlight, sent } = setup(async () => {
      throw new Error("boom");
    });
    await submitNewPassword(deps, fields);
    expect(events).toEqual([{ type: "submitted" }, { type: "failed", message: GENERIC_CHANGE_FAILURE }]);
    expect(inFlight.current).toBe(false);
    await submitNewPassword(deps, fields);
    expect(sent).toHaveLength(2);
  });

  it("ignores a second press while the first is still under way", async () => {
    const pending = deferred<ChangeOutcome>();
    const { deps, sent, events } = setup(() => pending.promise);
    const first = submitNewPassword(deps, fields);
    await submitNewPassword(deps, fields);
    expect(sent).toHaveLength(1);
    expect(events).toEqual([{ type: "submitted" }]);
    pending.resolve({ kind: "updated" });
    await first;
    expect(events.map((e) => e.type)).toEqual(["submitted", "succeeded"]);
  });

  it("does nothing at all when a send is already under way", async () => {
    const { deps, sent, events, inFlight } = setup(async () => ({ kind: "updated" }));
    inFlight.current = true;
    await submitNewPassword(deps, fields);
    expect(sent).toEqual([]);
    expect(events).toEqual([]);
    // Not its guard to release: the send that holds it will.
    expect(inFlight.current).toBe(true);
  });
});

describe("submitResetRequest", () => {
  function setup(resetPassword: (email: string) => Promise<ResetOutcome>) {
    const events: ResetRequestEvent[] = [];
    const sent: string[] = [];
    const inFlight: InFlight = { current: false };
    const deps = {
      resetPassword: (email: string) => {
        sent.push(email);
        return resetPassword(email);
      },
      dispatch: (event: ResetRequestEvent) => void events.push(event),
      inFlight,
    };
    return { deps, events, sent, inFlight };
  }
  const SENT: ResetOutcome = { kind: "sent", message: RESET_SENT_MESSAGE };

  it("reports 'submitted' and then the outcome, for the address it was given", async () => {
    const { deps, events, sent } = setup(async () => SENT);
    await submitResetRequest(deps, "pat@example.com");
    expect(sent).toEqual(["pat@example.com"]);
    expect(events).toEqual([{ type: "submitted" }, { type: "finished", outcome: SENT }]);
  });

  it("releases the guard after an error outcome, so the person can send again", async () => {
    const error: ResetOutcome = { kind: "error", message: GENERIC_RESET_FAILURE };
    const { deps, sent, inFlight } = setup(async () => error);
    await submitResetRequest(deps, "pat@example.com");
    expect(inFlight.current).toBe(false);
    await submitResetRequest(deps, "pat@example.com");
    expect(sent).toHaveLength(2);
  });

  it("reads a thrown request as an error outcome, and releases the guard", async () => {
    const { deps, events, inFlight, sent } = setup(async () => {
      throw new Error("boom");
    });
    await submitResetRequest(deps, "pat@example.com");
    expect(events).toEqual([
      { type: "submitted" },
      { type: "finished", outcome: { kind: "error", message: GENERIC_RESET_FAILURE } },
    ]);
    expect(inFlight.current).toBe(false);
    await submitResetRequest(deps, "pat@example.com");
    expect(sent).toHaveLength(2);
  });

  it("ignores a second press while the first is still under way", async () => {
    const pending = deferred<ResetOutcome>();
    const { deps, sent, events } = setup(() => pending.promise);
    const first = submitResetRequest(deps, "pat@example.com");
    await submitResetRequest(deps, "pat@example.com");
    expect(sent).toHaveLength(1);
    expect(events).toEqual([{ type: "submitted" }]);
    pending.resolve(SENT);
    await first;
    expect(events).toHaveLength(2);
  });

  it("sends nothing for an address it does not have (a session with no email)", async () => {
    const { deps, sent, events, inFlight } = setup(async () => SENT);
    await submitResetRequest(deps, null);
    expect(sent).toEqual([]);
    expect(events).toEqual([]);
    expect(inFlight.current).toBe(false);
  });
});

describe("the form actions", () => {
  it("sends each typed password to its own field, and the press to submit", () => {
    const events: UpdatePasswordEvent[] = [];
    let submitted = 0;
    const actions = updatePasswordActions(
      (event) => void events.push(event),
      () => void submitted++,
    );
    actions.setPassword("the-password");
    actions.setConfirm("the-confirmation");
    actions.submit();
    expect(events).toEqual([
      { type: "typed", field: "password", value: "the-password" },
      { type: "typed", field: "confirm", value: "the-confirmation" },
    ]);
    expect(submitted).toBe(1);
  });

  it("makes the email field editable, and hands submit and back through untouched", () => {
    const events: ResetRequestEvent[] = [];
    const calls: string[] = [];
    const actions = resetRequestActions(
      (event) => void events.push(event),
      () => void calls.push("submit"),
      () => void calls.push("back"),
    );
    actions.setEmail("pat@example.com");
    actions.submit();
    actions.back();
    expect(events).toEqual([{ type: "typed", value: "pat@example.com" }]);
    expect(calls).toEqual(["submit", "back"]);
  });

  it("drives the reducers: what is typed lands in the field it was typed into", () => {
    let state = initialUpdatePasswordState;
    const actions = updatePasswordActions(
      (event) => void (state = updatePasswordReducer(state, event)),
      () => {},
    );
    actions.setPassword("one-thing");
    actions.setConfirm("another");
    expect(state).toMatchObject({ password: "one-thing", confirm: "another" });

    let reset = initialResetRequestState;
    resetRequestActions((event) => void (reset = resetRequestReducer(reset, event)), () => {}, () => {}).setEmail(
      "pat@example.com",
    );
    expect(reset.email).toBe("pat@example.com");
  });
});

// ── the Login page's modes ─────────────────────────────────────────────────

describe("the Login page's modes", () => {
  it("starts on sign-in, and on the reset form for a person who followed a dead link", () => {
    expect(initialLoginMode(null)).toBe("sign_in");
    expect(initialLoginMode("That link could not be used. Request a new one.")).toBe("reset");
  });

  it.each<[LoginMode, LoginEvent, LoginMode]>([
    ["sign_in", "forgot", "reset"],
    ["reset", "back", "sign_in"],
    ["register", "back", "sign_in"],
    ["sign_in", "toggle", "register"],
    ["register", "toggle", "sign_in"],
    ["register", "confirmation_required", "sign_in"],
    ["sign_in", "confirmation_required", "sign_in"],
  ])("from %s, %s leads to %s", (mode, event, next) => {
    expect(loginModeAfter(mode, event)).toBe(next);
  });

  it("never opens the sign-up form from Forgot password", () => {
    expect(loginModeAfter("sign_in", "forgot")).not.toBe("register");
  });

  it("takes a dead link out of the address bar, and leaves every other visit's address alone", () => {
    expect(shouldClearUrl("That link has expired or was already used. Request a new one.")).toBe(true);
    // A deep link to a signed-out visitor is kept, so it still works after sign-in.
    expect(shouldClearUrl(null)).toBe(false);
  });
});
