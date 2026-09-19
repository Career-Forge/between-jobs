import { useCallback, useEffect, useRef, useState } from "react";
import { normalizeQuestionLabel } from "@/lib/questionMatching";
import { sanitizeDraftText } from "@/lib/questionSafety";
import { getSupabaseClient } from "@/lib/supabase";
import type {
  ContentScriptMessage,
  DetectionStateResponse,
  DraftAnswerMessage,
  DraftAnswerResult,
  FillFieldResult,
  FillResult,
  MarkAppliedMessage,
  MarkAppliedResult,
  MatchAnswerMessage,
  MatchAnswerResult,
  SaveAnswerMessage,
} from "@/lib/types";
import "./App.css";

type AuthState = { status: "loading" } | { status: "signed_out" } | { status: "signed_in" };

type MarkAppliedState =
  | { status: "idle" }
  | { status: "busy" }
  | { status: "done" }
  | { status: "error"; message: string };

// E3b's per-question state machine, keyed by fieldName -- a question the
// user never clicked "Draft answer" for stays absent from this map
// entirely (treated as "idle"), rather than every unresolved question
// needing an eagerly-initialized entry.
type QuestionAnswerState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; text: string; warnings: string[]; fromMemory: boolean; notice?: string }
  // A real DOM write is in flight for this exact text -- the textarea and
  // both Fill buttons render disabled while in this state, closing two
  // adversarially-confirmed gaps at once: an in-flight fill resolving
  // after the user has already edited the textarea to something else
  // (which used to discard the edit silently, since the fill's own
  // success handler unconditionally overwrote state to "filled"), and a
  // double-click/Fill-then-Fill&remember race with no busy guard.
  | { status: "filling"; text: string; warnings: string[]; fromMemory: boolean; notice?: string }
  | { status: "declined"; reason: string | null }
  | { status: "error"; message: string }
  | { status: "filled" };

// The specific message Chrome rejects `tabs.sendMessage` with when no
// content script is listening on the target tab -- the one case that
// genuinely means "not a supported page," as opposed to a real messaging
// failure (a mid-navigation port close, a just-reloaded extension
// context). An adversarial review caught the original bare `catch` here
// folding both into the same "not supported" bucket with no way to tell
// them apart or retry a transient one.
const NO_RECEIVER_MESSAGE = "Could not establish connection. Receiving end does not exist.";

// Shown when a per-question fill declined because the page field already
// holds text (D5: never clobber). The draft stays in the box, so it isn't
// lost -- the user can copy it, or clear the field on the page and Fill again.
const FIELD_HAS_TEXT_NOTICE =
  "That field on the page already has text, so it was left alone. Clear it there first if you want this answer in it.";

const PAGE_CHANGED_MESSAGE =
  "This page changed since the panel last looked at it -- it has been refreshed. Check the application shown, then try again.";

async function getActiveTabId(): Promise<number | null> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  return tab?.id ?? null;
}

// Client-side only -- `flagged_answer_warnings()` (application_answer_
// generator.py) formats each warning as a plain string with the verdict
// embedded at the start ("unsupported claim (unverifiable): ..." /
// "unsupported claim (contradicted): ..."), so this panel can tell them
// apart for display without the backend needing to change its response
// shape. "unverifiable" is a genuine but unconfirmed claim -- review-
// toned (gold), not alarming; "contradicted" actively conflicts with the
// candidate's own facts -- the one case that gets the danger (red)
// treatment. Anything unrecognized defaults to "review," never "danger,"
// since a false alarm here is worse than an under-alarm.
function warningSeverity(warning: string): "review" | "danger" {
  return warning.includes("(contradicted)") ? "danger" : "review";
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
  const [answerStates, setAnswerStates] = useState<Record<string, QuestionAnswerState>>({});
  const [busy, setBusy] = useState(false);

  // `recheck` forces a fresh lookup instead of reading what the page
  // already holds -- used when the signed-in user has just changed, so a
  // previous user's cached state (personal info, PDFs) is replaced rather
  // than displayed.
  const refreshDetection = useCallback(async (options?: { recheck?: boolean }) => {
    setFillResult(null);
    setFillNotice(null);
    setAnswerStates({});
    // Adversarially-confirmed gap: this used to reset unconditionally,
    // clobbering a genuinely in-flight "Mark as applied" call's busy
    // state (and, worse, re-enabling the button while that request was
    // still pending -- letting a second click fire a second, independently
    // idempotency-keyed POST that the idempotency key can't dedupe, since
    // it's not a retry of the same request). A completed "done"/"error"
    // outcome still resets on the next page, matching every other status.
    setMarkApplied((prev) => (prev.status === "busy" ? prev : { status: "idle" }));
    const response = await sendToActiveTab<DetectionStateResponse>({
      type: options?.recheck ? "RECHECK" : "GET_DETECTION_STATE",
    });
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

  // Coming from signed-out (a fresh sign-in, possibly as a different
  // user on the same browser profile) re-checks the page rather than
  // reading the content script's cache; merely opening the panel while
  // already signed in doesn't need to re-hit the backend.
  const previousAuthStatus = useRef<AuthState["status"]>("loading");
  useEffect(() => {
    const cameFromSignedOut = previousAuthStatus.current === "signed_out";
    previousAuthStatus.current = auth.status;
    if (auth.status === "signed_in") void refreshDetection({ recheck: cameFromSignedOut });
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
    // A client-side route change (Greenhouse/Ashby are SPAs) fires neither
    // of the tab events above -- the content script announces it instead.
    // Only the active tab's announcement matters to what this panel shows.
    const onMessage = (message: unknown, sender: Browser.runtime.MessageSender) => {
      if (typeof message !== "object" || message === null) return;
      if ((message as { type?: unknown }).type !== "PAGE_CHANGED") return;
      const senderTabId = sender.tab?.id;
      if (senderTabId === undefined) return;
      void getActiveTabId().then((activeTabId) => {
        if (senderTabId === activeTabId) void refreshDetection();
      });
    };
    browser.tabs.onUpdated.addListener(onUpdated);
    browser.tabs.onActivated.addListener(onActivated);
    browser.runtime.onMessage.addListener(onMessage);
    return () => {
      browser.tabs.onUpdated.removeListener(onUpdated);
      browser.tabs.onActivated.removeListener(onActivated);
      browser.runtime.onMessage.removeListener(onMessage);
    };
  }, [auth.status, refreshDetection]);

  async function handleSignIn(e: React.FormEvent) {
    e.preventDefault();
    setAuthError(null);
    setBusy(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) {
      setAuthError(error.message);
      return;
    }
    // Neither the address nor -- especially -- the plaintext password
    // should outlive the request in component state: on a shared browser
    // the next person to sign out and back in would find both pre-filled.
    setEmail("");
    setPassword("");
  }

  async function handleSignOut() {
    // `scope: "local"`: this extension's session is its own (D2), so
    // signing out of it must not revoke the same account's web-app
    // sessions -- auth-js's default scope is "global", which signs the
    // user out everywhere.
    const { error } = await supabase.auth.signOut({ scope: "local" });
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
    setFillNotice(null);
    setAnswerStates({});
    setMarkApplied({ status: "idle" });
    setEmail("");
    setPassword("");
    // The content script keeps the last user's personal info and PDFs in
    // memory for as long as the page lives and never hears about a
    // sign-out. Re-detecting now replaces them (with a "signed out"
    // state) in the tab being looked at; every other tab is protected by
    // the per-use session check instead.
    void sendToActiveTab<DetectionStateResponse>({ type: "RECHECK" });
  }

  async function handleRecheck() {
    setBusy(true);
    const response = await sendToActiveTab<DetectionStateResponse>({ type: "RECHECK" });
    setDetection(response);
    setFillResult(null);
    setFillNotice(null);
    setAnswerStates({});
    setBusy(false);
  }

  async function handleFill(forceRefillAll: boolean) {
    setBusy(true);
    setFillNotice(null);
    const result = await sendToActiveTab<FillResult | null>({ type: "REQUEST_FILL", forceRefillAll });
    if (result === null) {
      // Nothing usable for this page yet -- detection hadn't resolved, or
      // it belonged to a page/user that's gone and a fresh lookup has just
      // been started. Distinct from a genuine fill that found nothing to
      // do (an adversarially-confirmed gap: these used to be
      // indistinguishable).
      setFillNotice("Still checking this page -- try again in a moment.");
    } else {
      setFillResult(result);
    }
    setBusy(false);
  }

  // Everything the panel does on behalf of an application -- marking it
  // applied, drafting an answer for it -- is keyed on the application id
  // this panel LAST SAW. A client-side navigation (Greenhouse/Ashby)
  // can put a different posting under the same tab without any event this
  // panel hears in time, so before acting, ask the page what application
  // it is showing NOW; if it isn't the one on screen, show the real state
  // instead of acting on the stale one.
  async function confirmStillOnApplication(applicationId: string): Promise<boolean> {
    const live = await sendToActiveTab<DetectionStateResponse>({ type: "GET_DETECTION_STATE" });
    if (live?.tabState?.status === "tracked" && live.tabState.applicationId === applicationId) return true;
    setDetection(live);
    setFillResult(null);
    setAnswerStates({});
    setFillNotice(PAGE_CHANGED_MESSAGE);
    return false;
  }

  // Tracking confirmation (browser-extension.md): the human confirms the
  // real application state after THEY submit on the real page -- this
  // never fires on its own, and never substitutes for the human's own
  // submit click on jobs.lever.co itself. A fresh idempotency key per
  // click means a retried click (e.g. a flaky network) can't double-record
  // the transition.
  async function handleMarkApplied(applicationId: string) {
    setMarkApplied({ status: "busy" });
    try {
      if (!(await confirmStillOnApplication(applicationId))) {
        setMarkApplied({ status: "idle" });
        return;
      }
      const message: MarkAppliedMessage = {
        type: "MARK_APPLIED",
        applicationId,
        idempotencyKey: crypto.randomUUID(),
      };
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

  // E3b -- known-question-memory first (cheap, no LLM call), falling back
  // to a fresh LLM draft only on a genuine miss. `applicationId` comes
  // from the caller's own already-narrowed `trackedTabState` rather than
  // re-reading `detection` here, since this function has no reason to
  // duplicate that narrowing.
  async function handleDraftAnswer(applicationId: string, fieldName: string, label: string | null) {
    // A question whose label couldn't be read is human-only: its "question
    // text" would be a raw field name, and nothing can show it isn't a
    // sensitive question under a renamed label class. (The content script
    // already downgrades these; this is the second lock.)
    if (label === null) return;
    setAnswerStates((prev) => ({ ...prev, [fieldName]: { status: "loading" } }));
    try {
      if (!(await confirmStillOnApplication(applicationId))) return;
      const matchMessage: MatchAnswerMessage = {
        type: "MATCH_ANSWER",
        normalizedQuestion: normalizeQuestionLabel(label),
      };
      const match: MatchAnswerResult = await browser.runtime.sendMessage(matchMessage);
      if (match.answer !== null) {
        setAnswerStates((prev) => ({
          ...prev,
          [fieldName]: {
            status: "ready",
            text: sanitizeDraftText(match.answer!.answer_text),
            warnings: [],
            fromMemory: true,
          },
        }));
        return;
      }

      const draftMessage: DraftAnswerMessage = { type: "DRAFT_ANSWER", applicationId, questionText: label };
      const drafted: DraftAnswerResult = await browser.runtime.sendMessage(draftMessage);
      if (!drafted.eligible || drafted.answer_text === null) {
        setAnswerStates((prev) => ({
          ...prev,
          [fieldName]: { status: "declined", reason: drafted.declined_reason },
        }));
        return;
      }
      setAnswerStates((prev) => ({
        ...prev,
        [fieldName]: {
          status: "ready",
          text: sanitizeDraftText(drafted.answer_text as string),
          warnings: drafted.warnings,
          fromMemory: false,
        },
      }));
    } catch (e) {
      setAnswerStates((prev) => ({
        ...prev,
        [fieldName]: { status: "error", message: e instanceof Error ? e.message : "Failed to draft an answer." },
      }));
    }
  }

  function handleEditAnswer(fieldName: string, text: string) {
    setAnswerStates((prev) => {
      const current = prev[fieldName];
      if (current === undefined || current.status !== "ready") return prev;
      return { ...prev, [fieldName]: { ...current, text } };
    });
  }

  // Fills the real DOM via content.ts's own re-validating
  // `fillCustomTextAnswer` -- this is a distinct action from the
  // memory-check/draft step above, matching the standard-fields Fill
  // button's own "draft, then a separate explicit act to apply it"
  // shape. "Fill & remember" additionally saves to known-question
  // memory (best-effort -- a save failure still leaves the real field
  // filled, so it's logged, not surfaced as an error on top of a
  // successful fill).
  async function handleFillAnswer(fieldName: string, label: string | null, remember: boolean) {
    const current = answerStates[fieldName];
    if (current === undefined || current.status !== "ready") return;
    const { text, warnings, fromMemory } = current;
    setAnswerStates((prev) => ({ ...prev, [fieldName]: { status: "filling", text, warnings, fromMemory } }));
    const result = await sendToActiveTab<FillFieldResult>({ type: "FILL_FIELD", fieldName, value: text });
    if (result !== null && !result.filled && result.reason === "not_empty") {
      // D5: the field already has text. Nothing was written, and the draft
      // stays in the box -- so this isn't an error state (which would offer
      // to re-draft), it's the same draft with a reason it wasn't applied.
      setAnswerStates((prev) => ({
        ...prev,
        [fieldName]: { status: "ready", text, warnings, fromMemory, notice: FIELD_HAS_TEXT_NOTICE },
      }));
      return;
    }
    if (result !== null && !result.filled && result.reason === "page_changed") {
      // Re-read what the tab is showing now; that also clears this list,
      // which belonged to a page that's gone.
      void refreshDetection();
      setFillNotice(PAGE_CHANGED_MESSAGE);
      return;
    }
    if (result === null || !result.filled) {
      setAnswerStates((prev) => ({
        ...prev,
        [fieldName]: { status: "error", message: "Couldn't fill this field -- try again." },
      }));
      return;
    }
    if (remember) {
      try {
        const saveMessage: SaveAnswerMessage = {
          type: "SAVE_ANSWER",
          normalizedQuestion: normalizeQuestionLabel(label ?? fieldName),
          answerText: text,
        };
        await browser.runtime.sendMessage(saveMessage);
      } catch (e) {
        console.error("[between-jobs] saving approved answer failed", e);
      }
    }
    setAnswerStates((prev) => ({ ...prev, [fieldName]: { status: "filled" } }));
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
          <button type="submit" className="primary" disabled={busy}>
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
        <p>
          Not on a supported application page yet. Open a Lever, Greenhouse, or Ashby application
          form to use autofill.
        </p>
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
            <button className="primary" onClick={() => handleFill(false)} disabled={busy}>
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
              {fillResult.fieldMapError !== null && (
                // E3c, D4 fail-closed: the signed field map couldn't be
                // used (unverifiable, or wrong for this ATS), so the
                // parts of the fill that depend on it -- cover-letter
                // discovery, custom-question surfacing -- were skipped
                // entirely (not just left empty). The basic fields above
                // still filled regardless, since nothing signed backs
                // them. E6: also covers a single unusable selector inside
                // an otherwise-valid map, where only that field is skipped.
                <p className="error">
                  Part of this ATS&apos;s field map couldn&apos;t be used, so some fields were skipped.{" "}
                  {fillResult.fieldMapError}
                </p>
              )}
              {fillResult.fillError !== null && (
                // The fill itself threw. What was written before that is
                // still counted above -- this says the run didn't finish.
                <p className="error">
                  Something went wrong while filling this page, so it may be incomplete:{" "}
                  {fillResult.fillError}
                </p>
              )}
              {fillResult.unresolvedQuestions.length > 0 && (
                <div>
                  <p>These need your own attention -- we don't touch them yet:</p>
                  <ul className="question-list">
                    {fillResult.unresolvedQuestions.map((q) => {
                      const state: QuestionAnswerState = answerStates[q.fieldName] ?? { status: "idle" };
                      // Only a plain text field with a readable label is
                      // ever offered for drafting; everything else is the
                      // human's own.
                      const draftable = q.kind === "text" && q.label !== null;
                      return (
                        <li key={q.fieldName} className="question-card">
                          <p className="question-label">{q.label ?? q.fieldName}</p>
                          {q.kind === "file" && (
                            <p className="question-meta">Upload this file yourself.</p>
                          )}
                          {!draftable && q.kind !== "file" && (
                            <p className="question-meta">Answer this one yourself.</p>
                          )}
                          {draftable && (
                            <div className="question-answer">
                              {state.status === "idle" && trackedTabState !== null && (
                                <button
                                  onClick={() =>
                                    handleDraftAnswer(trackedTabState.applicationId, q.fieldName, q.label)
                                  }
                                >
                                  Draft answer
                                </button>
                              )}
                              {state.status === "loading" && (
                                <p className="question-meta">Checking for an answer...</p>
                              )}
                              {(state.status === "declined" || state.status === "error") && (
                                <div>
                                  <p className="error">
                                    {state.status === "declined"
                                      ? (state.reason ??
                                        "We can't draft this one -- please answer it yourself.")
                                      : state.message}
                                  </p>
                                  {trackedTabState !== null && (
                                    <button
                                      onClick={() =>
                                        handleDraftAnswer(trackedTabState.applicationId, q.fieldName, q.label)
                                      }
                                    >
                                      Try again
                                    </button>
                                  )}
                                </div>
                              )}
                              {state.status === "filled" && <span className="badge badge-success">Filled</span>}
                              {(state.status === "ready" || state.status === "filling") && (
                                <div>
                                  <p>
                                    {state.fromMemory ? (
                                      <span className="badge badge-success">Saved answer</span>
                                    ) : (
                                      <span className="badge badge-ai">AI drafted -- review before filling</span>
                                    )}
                                  </p>
                                  {state.warnings.length > 0 && (
                                    <ul className="warning-list">
                                      {state.warnings.map((warning, i) => (
                                        <li key={i} className={`warning-item ${warningSeverity(warning)}`}>
                                          {warning}
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                  {state.notice !== undefined && (
                                    <p className="question-meta" role="status">
                                      {state.notice}
                                    </p>
                                  )}
                                  {/* Sized to the whole draft (CSS caps the height and scrolls
                                      inside it), with a length readout: Fill writes ALL of this
                                      text into the form, so none of it should sit below the
                                      fold unread. */}
                                  <textarea
                                    value={state.text}
                                    onChange={(e) => handleEditAnswer(q.fieldName, e.target.value)}
                                    rows={4}
                                    disabled={state.status === "filling"}
                                  />
                                  <p className="question-meta">{state.text.length} characters</p>
                                  <div className="button-row">
                                    <button
                                      className="primary"
                                      onClick={() => handleFillAnswer(q.fieldName, q.label, false)}
                                      disabled={state.status === "filling"}
                                    >
                                      {state.status === "filling" ? "Filling..." : "Fill"}
                                    </button>
                                    <button
                                      onClick={() => handleFillAnswer(q.fieldName, q.label, true)}
                                      disabled={state.status === "filling"}
                                    >
                                      Fill &amp; remember
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </li>
                      );
                    })}
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
