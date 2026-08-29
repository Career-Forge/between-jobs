import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";

type Mode = "sign_in" | "register";

export default function Login() {
  const { signIn, signUp, signInWithGoogle } = useAuth();
  const [mode, setMode] = useState<Mode>("sign_in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);

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
        setMode("sign_in");
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
            minLength={mode === "register" ? 6 : undefined}
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
            setMode(mode === "sign_in" ? "register" : "sign_in");
            setError(null);
            setNotice(null);
          }}
        >
          {mode === "sign_in" ? "Need an account? Register" : "Already have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}
