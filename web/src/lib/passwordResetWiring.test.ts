import { describe, expect, it } from "vitest";
import app from "../App.tsx?raw";
import auth from "../auth.tsx?raw";
import resetForm from "../components/ResetRequestForm.tsx?raw";
import login from "../pages/Login.tsx?raw";
import updatePage from "../pages/UpdatePassword.tsx?raw";

// The parts of forgot-password that only exist as wiring in files the package cannot render
// (there is no DOM environment): where the reset link is told to lead back to, that the shell
// reacts to the recovery flag, and that each page hands the tested logic what it expects. The
// logic itself -- the order of the steps after an update, the guards, the Login modes, what
// ends a recovery -- is run, not read, in passwordReset.test.ts, and the shell's reaction to
// the flag is run in App.test.tsx. A reverted line would leave every pure test green.

describe("the reset link's redirect", () => {
  it("is built from this page's origin and nothing else, never from the address or a parameter", () => {
    expect(auth).toContain("requestPasswordReset(supabase.auth, email, window.location.origin)");
    // No other part of the location, and no query parsing, reaches the redirect.
    expect(auth).not.toMatch(/location\.(search|href|hash|pathname)/);
    expect(auth).not.toContain("URLSearchParams");
  });

  it("hands the typed password and its confirmation on as two separate arguments", () => {
    expect(auth).toContain("changePassword(supabase.auth, password, confirmation)");
  });
});

describe("the recovery flag", () => {
  it("is updated from every auth event with the tested rule", () => {
    expect(auth).toMatch(/onAuthStateChange\(\(event, next\) => \{[\s\S]*recoveryAfterEvent\(current, event\)/);
  });

  it("is cleared by the update page through the same rule, never by a literal", () => {
    expect(auth).toMatch(
      /function clearRecovery\(\): void \{\s*setRecovery\(\(current\) => recoveryAfterEvent\(current, RECOVERY_COMPLETED\)\);\s*\}/,
    );
    expect(auth).not.toMatch(/setRecovery\((true|false)\)/);
  });

  it("is taken from the auth context by the shell (not a constant), and acted on only for a signed-in person", () => {
    expect(app).toMatch(/const \{[^}]*\brecovery\b[^}]*\} = useAuth\(\);/);
    expect(app).not.toMatch(/\bconst recovery\b/);
    // The shell asks the routing rule (lib/publicRoutes.ts) with the real session, and every
    // signed-out screen -- the last of them the terms page -- has returned before the recovery
    // check, so a person with no session is never sent to the update page.
    expect(app).toContain("signedIn: session !== null");
    const lastSignedOutScreen = app.indexOf('if (view === "terms")');
    const redirect = app.indexOf("recoveryRedirect(recovery, location.pathname)");
    const route = app.indexOf("<Route path={UPDATE_PASSWORD_PATH}");
    expect(lastSignedOutScreen).toBeGreaterThan(-1);
    expect(redirect).toBeGreaterThan(lastSignedOutScreen);
    expect(route).toBeGreaterThan(redirect);
    expect(app).toContain("<Navigate to={recoveryTarget} replace />");
  });
});

describe("the Login page", () => {
  it("offers Forgot password? in sign-in mode, and shows the reset form for it", () => {
    expect(login).toContain("Forgot password?");
    expect(login).toContain("<ResetRequestForm");
  });

  it("reads a dead link from the URL, with where it landed, only through the pure rules", () => {
    expect(login).toContain(
      "loginLinkProblem(window.location.hash, window.location.search, window.location.pathname)",
    );
    expect(login).toContain("initialLoginMode(linkProblem, window.location.search)");
    expect(login).toContain("shouldClearUrl(linkProblem)");
    // No parsing of its own, and no text from the URL is stored or rendered.
    expect(login).not.toContain("parseRecoveryLinkProblem");
    expect(login).not.toMatch(/error_description|dangerouslySetInnerHTML/);
  });

  it("moves between its modes only through the tested transitions, never by naming a mode", () => {
    for (const event of ["forgot", "back", "toggle", "confirmation_required"]) {
      expect(login, event).toContain(`loginModeAfter(current, "${event}")`);
    }
    expect(login).not.toMatch(/setMode\(["']/);
  });
});

describe("the forgot-password form", () => {
  it("is submitted through the tested guard, with the typed address", () => {
    expect(resetForm).toContain("submitResetRequest({ resetPassword, dispatch, inFlight }, state.email)");
    expect(resetForm).toContain("resetRequestActions(dispatch, submit, onBack)");
  });
});

describe("the update-password page", () => {
  it("submits through the tested steps, and goes on to Today only once the update is done", () => {
    expect(updatePage).toContain(
      "submitNewPassword({ updatePassword, clearRecovery, dispatch, inFlight }, state)",
    );
    expect(updatePage).toContain("updatePasswordActions(dispatch, submit)");
    // The only navigation sits behind the done gate.
    const gate = updatePage.indexOf('if (state.status !== "done") return;');
    const navigated = updatePage.indexOf('navigate("/"');
    expect(gate).toBeGreaterThan(-1);
    expect(navigated).toBeGreaterThan(gate);
    expect((updatePage.match(/navigate\("\//g) ?? []).length).toBe(1);
    expect(updatePage).not.toContain("dangerouslySetInnerHTML");
  });

  it("sends a new link through the same guard as the sign-in page's form", () => {
    expect(updatePage).toContain("submitResetRequest({ resetPassword, dispatch, inFlight }, email)");
  });

  it("lets a real recovery win over a stale error in the URL, and judges the URL by where it is", () => {
    expect(updatePage).toMatch(
      /recovery\s*\?\s*null\s*:\s*parseRecoveryLinkProblem\(location\.hash, location\.search, location\.pathname\)/,
    );
  });
});
