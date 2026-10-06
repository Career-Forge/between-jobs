import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ConsentNotice } from "../components/ConsentNotice";
import { LegalLinks } from "../components/LegalLinks";
import { ResetRequestForm } from "../components/ResetRequestForm";
import {
  MIN_PASSWORD_LENGTH,
  initialLoginMode,
  loginLinkProblem,
  loginModeAfter,
  shouldClearUrl,
  type LoginMode,
} from "../lib/passwordReset";
import { LOGIN_PATH, loginPageTitle } from "../lib/publicRoutes";
import { useDocumentTitle } from "../lib/useDocumentTitle";

export default function Login() {
  const { signIn, signUp, signInWithGoogle } = useAuth();
  const navigate = useNavigate();
  // A person who followed a dead reset link (expired, or already used -- a mail scanner
  // that opens links first uses them up) lands here, signed out, with the reason in the
  // URL. They see a fixed sentence about it and the reset form to ask for a new link; no
  // text from the URL is shown. Read once, in the initialiser, so it survives the effect
  // below clearing the URL. Which URLs count, and what each mode leads to, is decided in
  // lib/passwordReset.ts: an auth error that is not a dead reset link (a cancelled Google
  // sign-in) is left alone, and this page shows the ordinary sign-in.
  const [linkProblem, setLinkProblem] = useState(() =>
    loginLinkProblem(window.location.hash, window.location.search, window.location.pathname),
  );
  // The landing page's Create account link opens this page on the registration form
  // (?mode=register); a dead reset link still wins, so it is never hidden behind another form.
  const [mode, setMode] = useState<LoginMode>(() =>
    initialLoginMode(linkProblem, window.location.search),
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);

  // The tab says which form this is: Sign in, Create account or the password reset.
  useDocumentTitle(loginPageTitle(mode));

  // Drops the dead link's error (and the /update-password path it arrived on) from the
  // address bar, so a reload, or signing in afterwards, does not replay it. It goes to /login,
  // not "/": the root is the public landing page for a signed-out visitor, and this page (with
  // the dead-link sentence and the reset form) must stay on screen.
  useEffect(() => {
    if (shouldClearUrl(linkProblem)) navigate(LOGIN_PATH, { replace: true });
  }, [linkProblem, navigate]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);

    if (mode === "sign_in") {
      const message = await signIn(email, password);
      if (message) {
        setError(message);
      }
    } else {
      const result = await signUp(email, password);
      if (result.kind === "error") {
        setError(result.message);
      } else if (result.kind === "confirmation_required") {
        setNotice("Check your email to confirm your account, then sign in.");
        setMode((current) => loginModeAfter(current, "confirmation_required"));
      }
      // "signed_in" needs nothing further -- onAuthStateChange picks up
      // the new session and App.tsx renders past this page on its own.
    }
    setBusy(false);
  }

  async function onGoogleClick() {
    setGoogleBusy(true);
    setError(null);
    const message = await signInWithGoogle();
    // No further handling on success -- a real OAuth click navigates the
    // whole page away to Google's consent screen and back, so this
    // component won't be mounted to update state at that point anyway.
    if (message) {
      setError(message);
      setGoogleBusy(false);
    }
  }

  if (mode === "reset") {
    return (
      <div className="bj-login">
        <div className="bj-login-card">
          <h1>Between Jobs</h1>
          <div className="bj-sub">Reset your password.</div>
          <ResetRequestForm
            problem={linkProblem}
            onBack={() => {
              setMode((current) => loginModeAfter(current, "back"));
              setLinkProblem(null);
              setError(null);
              setNotice(null);
            }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="bj-login">
      <div className="bj-login-card">
        <h1>Between Jobs</h1>
        <div className="bj-sub">
          {mode === "sign_in" ? "Sign in to your career workspace." : "Create your account."}
        </div>
        <form onSubmit={(e) => void onSubmit(e)}>
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "sign_in" ? "current-password" : "new-password"}
            minLength={mode === "register" ? MIN_PASSWORD_LENGTH : undefined}
            required
          />
          {notice && <div className="bj-muted bj-small">{notice}</div>}
          {error && <div className="bj-error">{error}</div>}
          <button className="bj-primary" type="submit" disabled={busy}>
            {busy
              ? mode === "sign_in"
                ? "Signing in..."
                : "Creating account..."
              : mode === "sign_in"
                ? "Sign in"
                : "Create account"}
          </button>
        </form>
        {mode === "sign_in" && (
          <button
            type="button"
            className="bj-link-button"
            onClick={() => {
              setMode((current) => loginModeAfter(current, "forgot"));
              setError(null);
              setNotice(null);
            }}
          >
            Forgot password?
          </button>
        )}
        {/* Both buttons below the form create an account for a new person (Google sign-in does
            too), so the one line that says what that means sits above Google, in either mode. */}
        <ConsentNotice />
        <button
          className="bj-google-button"
          onClick={() => void onGoogleClick()}
          disabled={googleBusy}
        >
          {googleBusy ? "Redirecting..." : "Continue with Google"}
        </button>
        <button
          className="bj-link-button"
          onClick={() => {
            setMode((current) => loginModeAfter(current, "toggle"));
            setError(null);
            setNotice(null);
          }}
        >
          {mode === "sign_in" ? "Need an account? Register" : "Already have an account? Sign in"}
        </button>
        <div className="bj-login-legal">
          <LegalLinks newTab />
        </div>
      </div>
    </div>
  );
}
