import { useCallback, useEffect, useState } from "react";
import { getSupabaseClient } from "@/lib/supabase";
import type { ContentScriptMessage, DetectionStateResponse, FillResult } from "@/lib/types";
import "./App.css";

type AuthState = { status: "loading" } | { status: "signed_out" } | { status: "signed_in" };

// The specific message Chrome rejects `tabs.sendMessage` with when no
// content script is listening on the target tab -- the one case that
// genuinely means "not a supported page," as opposed to a real messaging
// failure (a mid-navigation port close, a just-reloaded extension
// context). An adversarial review caught the original bare `catch` here
// folding both into the same "not supported" bucket with no way to tell
// them apart or retry a transient one.
const NO_RECEIVER_MESSAGE = "Could not establish connection. Receiving end does not exist.";

async function getActiveTabId(): Promise<number | null> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  return tab?.id ?? null;
}

async function sendToActiveTab<T>(message: ContentScriptMessage): Promise<T | null> {
  const tabId = await getActiveTabId();
  if (tabId === null) return null;
  try {
    return await browser.tabs.sendMessage(tabId, message);
  } catch (e) {
    const isNoReceiver = e instanceof Error && e.message.includes(NO_RECEIVER_MESSAGE);
    if (!isNoReceiver) {
      console.error("[between-jobs] message to active tab failed", e);
    }
    return null;
  }
}

export default function App() {
  // One client for the side panel's whole lifetime, not one per call --
  // confirmed live: calling getSupabaseClient() fresh from multiple
  // places in this component (the mount effect's getSession() call,
  // plus its own onAuthStateChange subscription) triggered a real
  // "Multiple GoTrueClient instances detected... may produce undefined
  // behavior" warning from auth-js itself. getSupabaseClient()'s own
  // "fresh every call, don't memoize" design exists for the
  // BACKGROUND/side-panel cross-realm case (so a sign-in in one becomes
  // visible to the other); it was never meant to also apply to repeated
  // calls WITHIN one already-open realm, which is what this panel does.
  const [supabase] = useState(getSupabaseClient);
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [detection, setDetection] = useState<DetectionStateResponse | null>(null);
  const [fillResult, setFillResult] = useState<FillResult | null>(null);
  const [fillNotice, setFillNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshDetection = useCallback(async () => {
    setFillResult(null);
    setFillNotice(null);
    const response = await sendToActiveTab<DetectionStateResponse>({ type: "GET_DETECTION_STATE" });
    setDetection(response);
  }, []);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setAuth({ status: data.session ? "signed_in" : "signed_out" });
    });
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
      setAuth({ status: session ? "signed_in" : "signed_out" });
    });
    return () => subscription.subscription.unsubscribe();
  }, [supabase]);

  useEffect(() => {
    if (auth.status === "signed_in") void refreshDetection();
  }, [auth.status, refreshDetection]);

  // Adversarially-confirmed gap: without this, navigating the same tab to
  // a different posting (or a page reload) left the panel showing the
  // PREVIOUS page's cached "tracked" state -- including a still-enabled
  // Fill button -- while the freshly-injected content script on the new
  // page hadn't finished its own detection yet.
  useEffect(() => {
    if (auth.status !== "signed_in") return;
    const onUpdated = (_tabId: number, changeInfo: Browser.tabs.OnUpdatedInfo) => {
      if (changeInfo.status === "complete") void refreshDetection();
    };
    const onActivated = () => void refreshDetection();
    browser.tabs.onUpdated.addListener(onUpdated);
    browser.tabs.onActivated.addListener(onActivated);
    return () => {
      browser.tabs.onUpdated.removeListener(onUpdated);
      browser.tabs.onActivated.removeListener(onActivated);
    };
  }, [auth.status, refreshDetection]);

  async function handleSignIn(e: React.FormEvent) {
    e.preventDefault();
    setAuthError(null);
    setBusy(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) setAuthError(error.message);
  }

  async function handleSignOut() {
    const { error } = await supabase.auth.signOut();
    if (error) {
      // Sign-out failing is rare but not impossible (e.g. a refresh
      // token invalidated by a concurrent refresh elsewhere) -- an
      // adversarial review caught this being silently discarded, leaving
      // the panel showing "signed in" with a dead session underneath.
      setAuthError(error.message);
      return;
    }
    setDetection(null);
    setFillResult(null);
  }

  async function handleRecheck() {
    setBusy(true);
    const response = await sendToActiveTab<DetectionStateResponse>({ type: "RECHECK" });
    setDetection(response);
    setFillResult(null);
    setFillNotice(null);
    setBusy(false);
  }

  async function handleFill(forceRefillAll: boolean) {
    setBusy(true);
    setFillNotice(null);
    const result = await sendToActiveTab<FillResult | null>({ type: "REQUEST_FILL", forceRefillAll });
    if (result === null) {
      // Detection hadn't resolved yet on the content-script side --
      // distinct from a genuine fill that found nothing to do (an
      // adversarially-confirmed gap: these used to be indistinguishable).
      setFillNotice("Still checking this page -- try again in a moment.");
    } else {
      setFillResult(result);
    }
    setBusy(false);
  }

  if (auth.status === "loading") {
    return <div className="panel">Loading...</div>;
  }

  if (auth.status === "signed_out") {
    return (
      <div className="panel">
        <h1>Between Jobs</h1>
        <form onSubmit={handleSignIn}>
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button type="submit" disabled={busy}>
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>
        {authError && <p className="error">{authError}</p>}
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="header-row">
        <h1>Between Jobs</h1>
        <button onClick={handleSignOut}>Sign out</button>
      </div>
      {authError && <p className="error">{authError}</p>}

      {detection === null || !detection.formDetected ? (
        <p>Not on a supported application page yet. Open a Lever application form to use autofill.</p>
      ) : detection.tabState === null ? (
        <div>
          <p>Checking...</p>
          <button onClick={handleRecheck} disabled={busy}>
            Try this page
          </button>
        </div>
      ) : detection.tabState.status === "signed_out" ? (
        <div>
          <p>Your session may have refreshed. Try again.</p>
          <button onClick={handleRecheck} disabled={busy}>
            Try this page
          </button>
        </div>
      ) : detection.tabState.status === "untracked" ? (
        <div>
          <p>This job isn't tracked in Between Jobs yet. Track it from Discover or Applications first.</p>
          <button onClick={handleRecheck} disabled={busy}>
            Try this page
          </button>
        </div>
      ) : detection.tabState.status === "error" ? (
        <div>
          <p className="error">{detection.tabState.message}</p>
          <button onClick={handleRecheck} disabled={busy}>
            Try this page
          </button>
        </div>
      ) : (
        <div>
          <p>Application found. Ready to fill known fields.</p>
          <div className="button-row">
            <button onClick={() => handleFill(false)} disabled={busy}>
              {busy ? "Filling..." : "Fill this page"}
            </button>
            {fillResult !== null && (
              <button onClick={() => handleFill(true)} disabled={busy}>
                Refill all
              </button>
            )}
          </div>

          {fillNotice !== null && <p>{fillNotice}</p>}
          {fillResult === null ? null : (
            <div className="fill-result">
              <p>Filled {fillResult.filledFields.length} field(s).</p>
              <p>Résumé: {fillResult.resumeAttached ? "attached" : (fillResult.resumeError ?? "not attached")}</p>
              {fillResult.unresolvedQuestions.length > 0 && (
                <div>
                  <p>These questions need your own answer -- we don't touch them yet:</p>
                  <ul>
                    {fillResult.unresolvedQuestions.map((q) => (
                      <li key={q.fieldName}>{q.label ?? q.fieldName}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <p className="footer-note">You always review and submit this application yourself.</p>
    </div>
  );
}
