import { useCallback, useEffect, useState } from "react";
import { getSupabaseClient } from "@/lib/supabase";
import type {
  ContentScriptMessage,
  DetectionStateResponse,
  FillResult,
  MarkAppliedMessage,
  MarkAppliedResult,
} from "@/lib/types";
import "./App.css";

type AuthState = { status: "loading" } | { status: "signed_out" } | { status: "signed_in" };

type MarkAppliedState =
  | { status: "idle" }
  | { status: "busy" }
  | { status: "done" }
  | { status: "error"; message: string };

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
  const [markApplied, setMarkApplied] = useState<MarkAppliedState>({ status: "idle" });
  const [busy, setBusy] = useState(false);

  const refreshDetection = useCallback(async () => {
    setFillResult(null);
    setFillNotice(null);
    // Adversarially-confirmed gap: this used to reset unconditionally,
    // clobbering a genuinely in-flight "Mark as applied" call's busy
    // state (and, worse, re-enabling the button while that request was
    // still pending -- letting a second click fire a second, independently
    // idempotency-keyed POST that the idempotency key can't dedupe, since
    // it's not a retry of the same request). A completed "done"/"error"
    // outcome still resets on the next page, matching every other status.
    setMarkApplied((prev) => (prev.status === "busy" ? prev : { status: "idle" }));
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
    // Adversarially-confirmed gap: this used to ignore its own `tabId`
    // parameter and re-run for ANY tab in the browser reaching
    // load-complete, active or not -- including a background tab
    // finishing a load while the user was mid-click on "Mark as
    // applied" in the tab actually showing the side panel, clobbering
    // that in-flight state for no reason connected to what's on screen.
    const onUpdated = (tabId: number, changeInfo: Browser.tabs.OnUpdatedInfo) => {
      if (changeInfo.status !== "complete") return;
      void getActiveTabId().then((activeTabId) => {
        if (tabId === activeTabId) void refreshDetection();
      });
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

  // Tracking confirmation (browser-extension.md): the human confirms the
  // real application state after THEY submit on the real page -- this
  // never fires on its own, and never substitutes for the human's own
  // submit click on jobs.lever.co itself. A fresh idempotency key per
  // click means a retried click (e.g. a flaky network) can't double-record
  // the transition.
  async function handleMarkApplied(applicationId: string) {
    setMarkApplied({ status: "busy" });
    const message: MarkAppliedMessage = {
      type: "MARK_APPLIED",
      applicationId,
      idempotencyKey: crypto.randomUUID(),
    };
    try {
      const result: MarkAppliedResult = await browser.runtime.sendMessage(message);
      setMarkApplied(result.ok ? { status: "done" } : { status: "error", message: result.message });
    } catch (e) {
      // Adversarially-confirmed gap: sendMessage itself can throw (e.g.
      // "Extension context invalidated" after a reload while the panel
      // is open) -- unlike background.ts's own markApplied, which
      // already wraps its network call, this had no catch, leaving the
      // button stuck on "Marking..." forever with no way to tell
      // whether the real backend mutation happened.
      setMarkApplied({
        status: "error",
        message: e instanceof Error ? e.message : "Failed to reach the extension's background worker.",
      });
    }
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

  // Captured into its own const (not re-derived inline inside the JSX
  // ternary below) so the "tracked" narrowing survives into the onClick
  // closure -- TypeScript's control-flow narrowing doesn't persist
  // through a nested arrow function re-reading `detection.tabState`.
  const trackedTabState =
    detection?.tabState?.status === "tracked" ? detection.tabState : null;

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
              {(fillResult.coverLetterAttached || fillResult.coverLetterError !== null) && (
                <p>
                  Cover letter:{" "}
                  {fillResult.coverLetterAttached ? "attached" : fillResult.coverLetterError}
                </p>
              )}
              {fillResult.unresolvedQuestions.length > 0 && (
                <div>
                  <p>These need your own attention -- we don't touch them yet:</p>
                  <ul>
                    {fillResult.unresolvedQuestions.map((q) => (
                      <li key={q.fieldName}>
                        {q.label ?? q.fieldName}
                        {q.kind === "file" ? " (upload this file yourself)" : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          <div className="fill-result">
            {markApplied.status === "done" ? (
              <p>Marked as applied.</p>
            ) : (
              <button
                onClick={() => trackedTabState && handleMarkApplied(trackedTabState.applicationId)}
                disabled={markApplied.status === "busy" || trackedTabState === null}
              >
                {markApplied.status === "busy" ? "Marking..." : "I submitted this -- mark as applied"}
              </button>
            )}
            {markApplied.status === "error" && <p className="error">{markApplied.message}</p>}
          </div>
        </div>
      )}

      <p className="footer-note">You always review and submit this application yourself.</p>
    </div>
  );
}
