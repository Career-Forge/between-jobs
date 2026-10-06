import { useCallback, useEffect, useRef, useState } from "react";
import {
  CONSENT_REQUIRED_MESSAGE,
  CONSENT_STORAGE_KEY,
  CONSENT_VERSION,
  consentDecisionFromChange,
  readConsentDecision,
  type StoredConsent,
} from "@/lib/consent";
import { asJurisdiction, isEligibilityQuestion, memoryTagsForLookup, memoryTagsForSave } from "@/lib/questionIntent";
import { normalizeQuestionLabel } from "@/lib/questionMatching";
import { sanitizeDraftText } from "@/lib/questionSafety";
import { getSupabaseClient } from "@/lib/supabase";
import type {
  AnswerUsedMessage,
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
  SignOutMessage,
  SignOutResult,
  TabState,
} from "@/lib/types";
import "./App.css";

// ---- E6 continuation: in-product data-collection disclosure and consent ----
//
// Chrome Web Store's User Data policy (confirmed live, 2026-09-21, against
// developer.chrome.com/docs/webstore/program-policies/user-data-faq and
// .../blog/cws-policy-updates-2026): the prominent disclosure and consent
// "must occur within the Product's user interface" -- store-listing text
// does not satisfy it -- and must happen BEFORE the product collects or
// handles user data, via "a specific action clearly agreeing to the
// disclosure." The 2026 update (enforced from 2026-08-01) removed the
// "closely related to the single purpose" qualifier (every data type must
// be disclosed, not just ones tied to the extension's stated purpose) and
// separately requires "proactively disclos[ing] to users if their data
// handling practices change at any point after the initial installation."
// Both match extension/store/LISTING.md section 4's own draft exactly, so
// this pass implements that draft close to verbatim rather than rewriting
// it -- the live check found nothing materially wrong with it.
//
// The stored flag carries a VERSION, not a bare boolean, specifically so a
// future change in what this extension collects can force re-consent by
// bumping CONSENT_VERSION -- without that, shipping a materially different
// disclosure later would need a storage-schema migration instead of a
// one-line constant change. There is only one version today. The flag, the
// version and the one function that decides whether the flag is good enough
// live in lib/consent.ts, because the content script and the service worker
// must ask the same question before they read a page or call the API.
export { CONSENT_STORAGE_KEY, CONSENT_VERSION };

type ConsentState = { status: "loading" } | { status: "needed" } | { status: "granted" };

// The gating screen itself, shown before any sign-in UI. Copy is
// extension/store/LISTING.md section 4's draft, adapted to JSX (a bullet
// list instead of a blockquote) but otherwise close to verbatim -- it's
// real product copy someone already got right, and it describes exactly
// what handleFill/handleDraftAnswer/handleFillAnswer above actually do.
// The privacy-policy link is left as an explicit maintainer placeholder
// (LISTING.md's own convention) rather than inventing a URL.
function ConsentGate({ onAgree, error }: { onAgree: () => void; error: string | null }): React.JSX.Element {
  return (
    <div className="panel">
      <h1>Between Jobs</h1>
      <div className="consent-gate">
        <h2>Before you sign in</h2>
        <p>
          Between Jobs autofill works with your Between Jobs account. When you are signed in and
          open a job application on Lever, Greenhouse or Ashby, this extension will:
        </p>
        <ul>
          <li>
            send the address of that page (without any query string) to the Between Jobs service
            to find the job you track;
          </li>
          <li>
            download your profile details (name, email, phone, location, links) and the résumé
            and cover letter you prepared, and use them to fill the form when you press Fill;
          </li>
          <li>
            only if you press Draft answer or Fill &amp; remember, send that question&apos;s text
            to the service -- together with the question&apos;s kind (when it is one of a few
            common ones) and the country the job names (when it names one) -- which may pass it,
            with a summary of your profile and the job description, to the AI provider you
            configured with your own key, and save answers you choose to remember;
          </li>
          <li>
            after each fill, send the service a small report of counts only: which site, which of
            your applications, how many fields it tried and filled, and one word for how it went --
            never what is in any field;
          </li>
          <li>
            when you fill a saved answer exactly as it was saved, tell the service which saved
            answer was used, so it can count how often you reuse it.
          </li>
        </ul>
        <p>
          It never submits an application, never ticks a checkbox, and never fills
          self-identification questions. It has no third-party analytics and sells nothing. Full
          policy: [MAINTAINER TO FILL: privacy policy URL]
        </p>
        {error !== null && <p className="error">{error}</p>}
        <div className="button-row">
          <button className="primary" onClick={onAgree}>
            I understand and agree
          </button>
          {/* Deliberately does nothing: no state is written, so the gate
              is still here next time the panel opens. This button exists
              only so declining is an explicit, visible choice rather than
              the person having no way to say "not yet" but closing the
              panel. */}
          <button type="button">Not now</button>
        </div>
      </div>
      <p className="footer-note">You always review and submit this application yourself.</p>
    </div>
  );
}

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
// A remembered answer offered for a question. `text` is what was stored (after the same
// clean-up every draft gets): filling it unedited counts as a use of that answer.
// `byIntent` is true when it was found through its intent, from a question worded
// differently -- the panel says so, since the wording it was written for may name another
// company.
interface ReusedAnswer {
  answerId: string;
  text: string;
  byIntent: boolean;
}

type QuestionAnswerState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; text: string; warnings: string[]; fromMemory: boolean; reuse?: ReusedAnswer; notice?: string }
  // A real DOM write is in flight for this exact text -- the textarea and
  // both Fill buttons render disabled while in this state, closing two
  // adversarially-confirmed gaps at once: an in-flight fill resolving
  // after the user has already edited the textarea to something else
  // (which used to discard the edit silently, since the fill's own
  // success handler unconditionally overwrote state to "filled"), and a
  // double-click/Fill-then-Fill&remember race with no busy guard.
  | { status: "filling"; text: string; warnings: string[]; fromMemory: boolean; reuse?: ReusedAnswer; notice?: string }
  | { status: "declined"; reason: string | null }
  | { status: "error"; message: string }
  // `note` says something the person should know about what happened after the fill (an answer
  // that was filled but not remembered, and why).
  | { status: "filled"; note?: string };

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

// Fill & remember on a work-eligibility answer when neither the question nor the posting names a
// country: the field was filled, but the answer is not kept, because an answer saved without a
// country would be offered as true in every country.
const NOT_REMEMBERED_NOTICE =
  "Filled, but not remembered: neither this question nor the posting says which country it is about, and an answer about work eligibility is only kept together with a country.";

// A question whose label could not be read is shown under this fixed text, never under the page's
// own name for the field: that name is page-controlled (any length, any characters), and it is
// only an identifier.
const UNREADABLE_QUESTION_LABEL = "A question this extension couldn't read the label of";

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
  //
  // Created only once the person has agreed to the disclosure, never on mount: constructing the
  // client is itself a network event when a stored sign-in session has expired (the library
  // starts its auth listener, which refreshes an expired token with the sign-in service), and
  // nothing may be sent before agreement. A ref holds it, so a repeated effect run (StrictMode)
  // still makes exactly one.
  const supabaseRef = useRef<ReturnType<typeof getSupabaseClient> | null>(null);
  const [supabase, setSupabase] = useState<ReturnType<typeof getSupabaseClient> | null>(null);
  const [consent, setConsent] = useState<ConsentState>({ status: "loading" });
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
  const [consentError, setConsentError] = useState<string | null>(null);

  // Every detection answer goes through here. `consent_required` means the
  // page-side script (or the service worker) found the stored flag invalid while
  // this panel believed it was good -- the flag changed in between, or one side's
  // read failed. Whichever it was, nothing was read or sent, so show what the
  // flag really says now (the gate, if it is gone) instead of a page state.
  const acceptDetection = useCallback((response: DetectionStateResponse | null) => {
    setDetection(response);
    if (response?.tabState?.status === "consent_required") {
      void readConsentDecision().then((decision) => {
        if (decision === "needed") setConsent({ status: "needed" });
      });
    }
  }, []);

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
    acceptDetection(response);
  }, [acceptDetection]);

  // E6 continuation -- reads the stored consent flag once, on mount. This
  // is the one thing allowed to happen before the person has agreed to
  // anything: it's a local `chrome.storage.local` read, never a network
  // request, and it isn't "collecting" anything FROM the person or about
  // them (see ConsentGate's own doc comment for the policy citation this
  // is built against). A version mismatch (or nothing stored at all) is
  // treated identically to "never agreed" -- fail closed, ask again.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const decision = await readConsentDecision();
      if (cancelled) return;
      setConsent(decision === "granted" ? { status: "granted" } : { status: "needed" });
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // The flag can change under an open panel: withdrawn, or replaced by another
  // version. The page-side script and the service worker already re-read it on
  // every action, so nothing more is collected either way; this closes the
  // panel's own screen at the same moment, and drops what it was showing from
  // the page (profile-derived answers and labels) so it can't reappear stale.
  useEffect(() => {
    const onChanged = (changes: Record<string, { newValue?: unknown }>, areaName: string) => {
      const decision = consentDecisionFromChange(changes, areaName);
      if (decision === null) return;
      if (decision === "granted") {
        setConsent({ status: "granted" });
        return;
      }
      setConsent({ status: "needed" });
      setDetection(null);
      setFillResult(null);
      setFillNotice(null);
      setAnswerStates({});
      setMarkApplied({ status: "idle" });
    };
    chrome.storage.onChanged.addListener(onChanged);
    return () => chrome.storage.onChanged.removeListener(onChanged);
  }, []);

  // "I understand and agree": persists the flag (so a remount, or the
  // panel reopening tomorrow, doesn't ask again) and lets the rest of the
  // panel render. If the write fails the gate stays up and says so: the page
  // script and the service worker read the same flag, so proceeding on a flag
  // that was never stored would only produce a panel where nothing works.
  async function handleAgreeToConsent() {
    setConsentError(null);
    try {
      await chrome.storage.local.set({ [CONSENT_STORAGE_KEY]: { version: CONSENT_VERSION } satisfies StoredConsent });
    } catch (e) {
      console.error("[between-jobs] saving consent failed", e);
      setConsentError("Couldn't save your choice. Please try again.");
      return;
    }
    setConsent({ status: "granted" });
  }

  // A request the service worker refused because the flag was no longer valid
  // (it reads it afresh each time): show what the flag says now.
  function noteIfConsentRequired(e: unknown): void {
    if (e instanceof Error && e.message === CONSENT_REQUIRED_MESSAGE) {
      void readConsentDecision().then((decision) => {
        if (decision === "needed") setConsent({ status: "needed" });
      });
    }
  }

  useEffect(() => {
    if (consent.status !== "granted") return;
    if (supabaseRef.current === null) supabaseRef.current = getSupabaseClient();
    setSupabase(supabaseRef.current);
  }, [consent.status]);

  useEffect(() => {
    if (supabase === null) return;
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
  //
  // Gated on consent (E6 continuation): while the flag is unset (or
  // stale), this effect must not fire refreshDetection at all -- that's
  // the one call in this panel that reaches the content script and, from
  // there, the backend. `previousAuthStatus` is deliberately left
  // untouched on an early return too, not just the refreshDetection call:
  // once consent is granted, "did this session just sign in" should still
  // reflect the real auth history, not a transition that happened while
  // the gate was blocking everything downstream of it.
  const previousAuthStatus = useRef<AuthState["status"]>("loading");
  useEffect(() => {
    if (consent.status !== "granted") return;
    const cameFromSignedOut = previousAuthStatus.current === "signed_out";
    previousAuthStatus.current = auth.status;
    if (auth.status === "signed_in") void refreshDetection({ recheck: cameFromSignedOut });
  }, [auth.status, refreshDetection, consent.status]);

  // Adversarially-confirmed gap: without this, navigating the same tab to
  // a different posting (or a page reload) left the panel showing the
  // PREVIOUS page's cached "tracked" state -- including a still-enabled
  // Fill button -- while the freshly-injected content script on the new
  // page hadn't finished its own detection yet.
  useEffect(() => {
    // Gated on consent too (E6 continuation), not just auth status: the
    // auth-state listener above runs unconditionally, so a returning user
    // with an already-valid session can reach `auth.status === "signed_in"`
    // before ever seeing (let alone agreeing to) the consent gate. Without
    // this check, THESE listeners -- not the render gate -- would be the
    // ones actually reaching the content script/backend behind the
    // person's back the moment a tab updates or activates.
    if (auth.status !== "signed_in" || consent.status !== "granted") return;
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
  }, [auth.status, refreshDetection, consent.status]);

  async function handleSignIn(e: React.FormEvent) {
    e.preventDefault();
    if (supabase === null) return; // only reachable after agreement, once the client exists
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
    // E6 continuation -- record the server-side revocation (the ORIGINAL
    // spec's own "server-side revocation" requirement -- see
    // extension_auth.py's module docstring) BEFORE clearing the local
    // Supabase session below: the bearer token this call needs is only
    // still readable from `getSession()` up until `signOut()` clears it.
    // Best-effort and non-blocking on purpose (SignOutMessage's own doc
    // comment) -- a network hiccup here must never trap the user signed
    // in on THIS device; it only means a stolen/leftover token stays
    // valid a little longer than intended, not that sign-out itself
    // fails. `sendMessage` itself can throw (e.g. "Extension context
    // invalidated"), same gap handleMarkApplied already guards -- caught
    // the same way, never allowed to block the local sign-out below.
    try {
      const signOutMessage: SignOutMessage = { type: "SIGN_OUT" };
      const result: SignOutResult | undefined = await browser.runtime.sendMessage(signOutMessage);
      if (!result?.ok) {
        console.error("[between-jobs] server-side extension sign-out failed");
      }
    } catch (e) {
      console.error("[between-jobs] server-side extension sign-out failed", e);
    }

    // `scope: "local"`: this extension's session is its own (D2), so
    // signing out of it must not revoke the same account's web-app
    // sessions -- auth-js's default scope is "global", which signs the
    // user out everywhere.
    if (supabase === null) return;
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
    acceptDetection(response);
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
  async function confirmStillOnApplication(
    applicationId: string,
  ): Promise<Extract<TabState, { status: "tracked" }> | null> {
    const live = await sendToActiveTab<DetectionStateResponse>({ type: "GET_DETECTION_STATE" });
    if (live?.tabState?.status === "tracked" && live.tabState.applicationId === applicationId) return live.tabState;
    acceptDetection(live);
    setFillResult(null);
    setAnswerStates({});
    setFillNotice(PAGE_CHANGED_MESSAGE);
    return null;
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
      if ((await confirmStillOnApplication(applicationId)) === null) {
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
      noteIfConsentRequired(e);
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
      const live = await confirmStillOnApplication(applicationId);
      if (live === null) return;
      // Same wording first; failing that, the service looks for an answer stored under the
      // same intent (see lib/questionIntent.ts) -- "Why do you want to work here?" finds
      // what was written for "...at Acme?". The job's country, when its posting names one,
      // lets the service keep a work-eligibility answer to the country it was written for.
      const normalizedQuestion = normalizeQuestionLabel(label);
      const matchMessage: MatchAnswerMessage = {
        type: "MATCH_ANSWER",
        normalizedQuestion,
        ...memoryTagsForLookup(label, asJurisdiction(live.payload.job_jurisdiction)),
      };
      const match: MatchAnswerResult = await browser.runtime.sendMessage(matchMessage);
      // A work-eligibility answer is offered only if it was saved together with a country. One
      // stored without a country (from before answers were tagged) would be true "everywhere",
      // which it is not, so it is not offered here.
      const stored =
        match.answer !== null && isEligibilityQuestion(label) && (match.answer.jurisdiction ?? null) === null
          ? null
          : match.answer;
      if (stored !== null) {
        const storedText = sanitizeDraftText(stored.answer_text);
        const answerId = stored.id;
        setAnswerStates((prev) => ({
          ...prev,
          [fieldName]: {
            status: "ready",
            text: storedText,
            warnings: [],
            fromMemory: true,
            ...(answerId === undefined
              ? {}
              : {
                  reuse: {
                    answerId,
                    text: storedText,
                    byIntent:
                      stored.normalized_question !== undefined && stored.normalized_question !== normalizedQuestion,
                  },
                }),
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
      noteIfConsentRequired(e);
      setAnswerStates((prev) => ({
        ...prev,
        [fieldName]: { status: "error", message: e instanceof Error ? e.message : "Failed to draft an answer." },
      }));
    }
  }

  async function reportAnswerUsed(answerId: string): Promise<void> {
    try {
      const message: AnswerUsedMessage = { type: "ANSWER_USED", answerId };
      await browser.runtime.sendMessage(message);
    } catch (e) {
      console.error("[between-jobs] recording a used answer failed", e);
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
  // `force` (E6 continuation): the per-question "Replace" action, D5's
  // only escape hatch for a custom question -- defaults to false for the
  // ordinary Fill/Fill & remember buttons, and is passed true only from
  // the Replace button rendered specifically for the "not_empty" notice
  // below. content.ts's own fillCustomTextAnswer re-validates everything
  // this bypasses is limited to (the D5 not-empty check only, never the
  // D6/namespace/element-kind refusals) -- this panel just threads the
  // flag through, it doesn't re-implement that scoping.
  async function handleFillAnswer(fieldName: string, label: string | null, remember: boolean, force = false) {
    const current = answerStates[fieldName];
    if (current === undefined || current.status !== "ready") return;
    const { text, warnings, fromMemory, reuse } = current;
    setAnswerStates((prev) => ({ ...prev, [fieldName]: { status: "filling", text, warnings, fromMemory, reuse } }));
    const result = await sendToActiveTab<FillFieldResult>({ type: "FILL_FIELD", fieldName, value: text, force });
    if (result !== null && !result.filled && result.reason === "not_empty") {
      // D5: the field already has text. Nothing was written, and the draft
      // stays in the box -- so this isn't an error state (which would offer
      // to re-draft), it's the same draft with a reason it wasn't applied.
      setAnswerStates((prev) => ({
        ...prev,
        [fieldName]: { status: "ready", text, warnings, fromMemory, reuse, notice: FIELD_HAS_TEXT_NOTICE },
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
    // A remembered answer that went into the form exactly as stored has been used once more.
    // Edited text is a new answer, not a use of the old one. Best effort, and never awaited
    // against the fill: the count is bookkeeping.
    if (reuse !== undefined && text === reuse.text) void reportAnswerUsed(reuse.answerId);
    let note: string | undefined;
    // Only a question whose label was readable is ever remembered: the page's own name for a
    // field is not question text.
    if (remember && label !== null) {
      const live = detection?.tabState?.status === "tracked" ? detection.tabState : null;
      const tags = memoryTagsForSave(label, asJurisdiction(live?.payload.job_jurisdiction));
      if (tags === null) {
        note = NOT_REMEMBERED_NOTICE;
      } else {
        try {
          const saveMessage: SaveAnswerMessage = {
            type: "SAVE_ANSWER",
            normalizedQuestion: normalizeQuestionLabel(label),
            answerText: text,
            ...tags,
          };
          await browser.runtime.sendMessage(saveMessage);
        } catch (e) {
          console.error("[between-jobs] saving approved answer failed", e);
        }
      }
    }
    setAnswerStates((prev) => ({ ...prev, [fieldName]: note === undefined ? { status: "filled" } : { status: "filled", note } }));
  }

  // E6 continuation -- the consent gate renders before anything else,
  // including the auth-loading screen: while it's unresolved or unagreed,
  // nothing past it (the sign-in form, any detection/fill UI) is shown.
  if (consent.status === "loading") {
    return <div className="panel">Loading...</div>;
  }
  if (consent.status === "needed") {
    return <ConsentGate onAgree={() => void handleAgreeToConsent()} error={consentError} />;
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

      {detection?.tabState?.status === "consent_required" ? (
        <p>
          This page hasn&apos;t been read: the disclosure needs your agreement first. Close and reopen this
          panel to review it.
        </p>
      ) : detection === null || !detection.formDetected ? (
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
              {fillResult.skippedFields.length > 0 && (
                // Fields the form has that the profile could not honestly fill (a one-word
                // name has no last name; a country no option matches; two boxes that could
                // both be the phone number). Left empty, never guessed -- this is the person's
                // cue to check them. Fixed text from the extension, never page text.
                <div>
                  <p>Left empty -- check these yourself:</p>
                  <ul className="warning-list">
                    {fillResult.skippedFields.map((item, i) => (
                      <li key={i} className="warning-item review">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
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
                          <p className="question-label">{q.label ?? UNREADABLE_QUESTION_LABEL}</p>
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
                              {state.status === "filled" && (
                                <div>
                                  <span className="badge badge-success">Filled</span>
                                  {state.note !== undefined && (
                                    <p className="question-meta" role="status">
                                      {state.note}
                                    </p>
                                  )}
                                </div>
                              )}
                              {(state.status === "ready" || state.status === "filling") && (
                                <div>
                                  <p>
                                    {state.fromMemory ? (
                                      <span className="badge badge-success">Saved answer</span>
                                    ) : (
                                      <span className="badge badge-ai">AI drafted -- review before filling</span>
                                    )}
                                  </p>
                                  {state.reuse?.byIntent === true && (
                                    <p className="question-meta">
                                      Reused from a similar question you answered before -- check that it fits this
                                      job.
                                    </p>
                                  )}
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
                                    <div>
                                      <p className="question-meta" role="status">
                                        {state.notice}
                                      </p>
                                      {/* E6 continuation -- D5's only escape hatch for a custom
                                          question ("Refill all" explicitly never touches these).
                                          Shown only alongside FIELD_HAS_TEXT_NOTICE specifically
                                          (not any future notice this same field might carry), and
                                          only for this one question -- it re-sends FILL_FIELD for
                                          `q.fieldName` with force:true, nothing else on the page. */}
                                      {state.notice === FIELD_HAS_TEXT_NOTICE && (
                                        <button
                                          onClick={() => handleFillAnswer(q.fieldName, q.label, false, true)}
                                          disabled={state.status === "filling"}
                                        >
                                          Replace
                                        </button>
                                      )}
                                    </div>
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
