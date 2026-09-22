import type { AtsFieldMap } from "./ats-field-map";
import type { QuestionKind } from "./questionSafety";

/** E4/E5 -- the three ATS types this extension's content script can run
 * against. Threaded through detection/messaging so background.ts's own
 * `fetchFieldMap`/tab-state resolution and content.ts's own engine
 * dispatch both generalize on the same literal union rather than each
 * hardcoding "lever". */
export type AtsType = "lever" | "greenhouse" | "ashby";

// Mirrors between_jobs.api.applications_routes._extension_personal_info's
// real response shape exactly (see GET /applications/{id}/extension-payload).
// Deliberately has no work_authorization/dob/nationality/marital_status/
// work_authorization_status/photo fields -- D6, enforced server-side, not
// re-derived here.
export interface ExtensionPersonalInfo {
  name: string;
  email: string | null;
  phone: string | null;
  location: { city: string; region: string; country: string };
  linkedin: string;
  github: string;
  portfolio: string;
}

export interface ExtensionPayload {
  prepare_result: {
    // Both keys are always PRESENT on the real wire response (the backend's
    // GET /extension-payload always writes them, via `.get(...)` on the
    // stored PrepareApplicationResult) -- `| null` is the common case, not
    // an edge case: `generate_cover_letter` defaults to false, so most
    // applications' own real payload is `{ resume: {...}, cover_letter:
    // null }`. Only `?` (the whole `resume`/`cover_letter` key literally
    // absent) is the one shape that can't happen on this field today; kept
    // optional anyway so a stricter/older payload shape still type-checks.
    resume?: { artifact_id: string; version_id: string } | null;
    cover_letter?: { artifact_id: string; version_id: string } | null;
  } | null;
  personal_info: ExtensionPersonalInfo | null;
}

/** A generated PDF (résumé or cover letter), base64-encoded for
 * structured-clone transfer across the background <-> content-script
 * message boundary. Fetched by background (the only context allowed to
 * talk to the backend, per this phase's own architecture) -- E6
 * continuation: lazily, the first time a Fill is actually requested for
 * this application, via FETCH_APPLICATION_FILES, not eagerly at
 * PAGE_DETECTED/detection time the way it used to be. Detection used to
 * download both files on every tracked page visit regardless of whether
 * the person ever pressed Fill; moving the fetch to the Fill path cuts
 * that load on the backend and the user's own network to just once per
 * application actually filled. */
export interface GeneratedFile {
  base64: string;
  filename: string;
}

/** What the background script learns about a tab after the content
 * script reports a detected apply form and the backend lookup completes.
 * Held in background's own per-tab state map; the side panel and content
 * script both ask for it rather than duplicating the lookup.
 *
 * E3c (generalized in E4/E5) -- the verified, ATS-idiosyncratic field
 * map, fetched and signature-checked once per detection (background.ts
 * is the only context that talks to the backend, per this phase's own
 * architecture). Unlike the field map, `resume`/`coverLetter` below are
 * NOT fetched at detection time (E6 continuation) -- they start `null`
 * here regardless of whether the application actually has one, and a
 * content script fills them in itself, lazily, via FETCH_APPLICATION_
 * FILES, the first time a Fill is requested. `fieldMap:
 * null` means D4's fail-closed case fired for Lever (content.ts must not
 * attempt any Lever-idiosyncratic behavior -- custom questions, cover-
 * letter discovery, location/LinkedIn/portfolio -- in that case, though
 * the open-source GENERIC_FIELD_DEFAULTS fields remain available
 * regardless, since nothing signed ever backed them). For Greenhouse/
 * Ashby, `fieldMap` is always `null` today (this repo never curates or
 * signs a real map for either -- see lib/greenhouse.ts's/lib/ashby.ts's
 * own top-of-file notes): those two ATSs' engines work entirely off
 * their own open-source defaults and don't gate any behavior on this
 * field, matching the state Lever itself was in before E3c. The fetch
 * still happens for all three ATS types (a real, forward-compatible
 * `GET /extension/field-maps/{ats_type}` call, generalized off the
 * single Lever-only call site E2/E3c had) so a future signed map for
 * either new ATS has a ready path with zero further plumbing changes.
 * `fieldMapError` carries a human-readable reason for the side panel. */
export type TabState =
  | { status: "signed_out" }
  | { status: "untracked" }
  | {
      status: "tracked";
      applicationId: string;
      /** The Supabase user this state (personal info, résumé, cover
       * letter) was resolved for. A content script keeps its TabState for
       * as long as the page lives, but sessions don't -- before any fill it
       * re-checks with the service worker that this user is still the one
       * signed in, so signing out and signing in as someone else on the
       * same browser profile can't fill the previous user's data. */
      userId: string;
      payload: ExtensionPayload;
      /** E6 continuation: always `null` as returned by PAGE_DETECTED --
       * "not fetched yet," not "this application has none." Whether one
       * should exist at all comes from `payload.prepare_result`; a content
       * script fetches the real blob itself, lazily, on the first Fill
       * (FETCH_APPLICATION_FILES) and caches it locally rather than
       * re-detecting. See GeneratedFile's own doc comment for why. */
      resume: GeneratedFile | null;
      coverLetter: GeneratedFile | null;
      fieldMap: AtsFieldMap | null;
      fieldMapError: string | null;
    }
  | { status: "error"; message: string };

/** Content script -> background, on detecting a supported apply form.
 * Generalized in E4/E5 from the Lever-only `LEVER_PAGE_DETECTED` --
 * `atsType` is the only new piece of information background.ts needs to
 * generalize its own `fetchFieldMap` call site and personal-info lookup. */
export interface PageDetectedMessage {
  type: "PAGE_DETECTED";
  atsType: AtsType;
  url: string;
}

/** Side panel -> background: the human clicked "Mark as applied" --
 * tracking-confirmation (browser-extension.md), a new caller of the same
 * `change_application_stage` RPC K1 already built, not new machinery.
 * `idempotencyKey` is generated by the side panel per click (Proposal's
 * own "the caller who can retry owns the key" rule), not by background,
 * so a retried click never double-records the transition. */
export interface MarkAppliedMessage {
  type: "MARK_APPLIED";
  applicationId: string;
  idempotencyKey: string;
}

/** Side panel -> background (E3b, browser-extension.md): check known-
 * question memory before ever drafting anything new -- Proposal §25's
 * own match order, tier 1/2 only (tier 3, semantic matching, is
 * explicitly deferred past v1). `normalizedQuestion` must already be run
 * through `normalizeQuestionLabel` by the caller. */
export interface MatchAnswerMessage {
  type: "MATCH_ANSWER";
  normalizedQuestion: string;
  canonicalIntent?: string;
}

export interface MatchAnswerResult {
  answer: { answer_text: string } | null;
}

/** Side panel -> background: the human approved an answer (drafted or
 * hand-typed) and wants it remembered for next time. */
export interface SaveAnswerMessage {
  type: "SAVE_ANSWER";
  normalizedQuestion: string;
  answerText: string;
}

/** Side panel -> background: draft an answer via the LLM-fallback path.
 * Only ever sent for a `kind: "text"` question the memory check above
 * already missed on -- never for a radio/file field, and never
 * automatically without the human clicking a button first. */
export interface DraftAnswerMessage {
  type: "DRAFT_ANSWER";
  applicationId: string;
  questionText: string;
}

export interface DraftAnswerResult {
  eligible: boolean;
  answer_text: string | null;
  declined_reason: string | null;
  warnings: string[];
}

/** Content script -> background (E6 continuation): fetch this
 * application's résumé/cover-letter PDFs, lazily -- the first time a Fill
 * is actually requested, not at PAGE_DETECTED time (see GeneratedFile's
 * own doc comment). `wantResume`/`wantCoverLetter` say which of the two
 * are actually worth fetching (the content script already knows, from
 * `payload.prepare_result`, whether each one should exist and whether it
 * has already fetched it once this page load) -- background trusts them
 * as a fetch selector only, never as a substitute for its own
 * `prepare_result` read, so asking for a file that doesn't exist just
 * costs one extra round trip, not a security question. */
export interface FetchApplicationFilesMessage {
  type: "FETCH_APPLICATION_FILES";
  applicationId: string;
  wantResume: boolean;
  wantCoverLetter: boolean;
}

/** Each file's fetch is independent: a résumé fetch failing (or not being
 * requested) never prevents the cover letter from coming back, and vice
 * versa. `null` with a `null` error means "not requested"; `null` with a
 * non-null error means "requested and failed" -- content.ts turns that
 * into the same resumeError/coverLetterError a FillResult already shows. */
export interface FetchApplicationFilesResult {
  resume: GeneratedFile | null;
  resumeError: string | null;
  coverLetter: GeneratedFile | null;
  coverLetterError: string | null;
}

/** Side panel -> background (E6 continuation): the human clicked "Sign
 * out." Records this user's extension sign-out server-side
 * (POST /extension/sign-out -- extension_auth.py's own scoped liveness
 * check) BEFORE the panel clears its local Supabase session, so the call
 * is still made with a currently-valid bearer token. Best-effort: a
 * failure here (network down, backend unreachable) never blocks the
 * local sign-out itself -- the user's own device is always the thing
 * that must end the session promptly; the server-side revocation is
 * defense against a stolen/leftover token outliving that, not a
 * precondition for signing out at all. */
export interface SignOutMessage {
  type: "SIGN_OUT";
}

export interface SignOutResult {
  ok: boolean;
}

/** Content script -> background: "is `userId` still the signed-in user?"
 * Answers with a boolean only, so a content script (the least-trusted
 * extension context, sitting next to attacker-controlled page code) never
 * learns anything it didn't already hold. */
export interface VerifySessionMessage {
  type: "VERIFY_SESSION";
  userId: string;
}

export interface VerifySessionResult {
  valid: boolean;
}

export type BackgroundMessage =
  | PageDetectedMessage
  | VerifySessionMessage
  | MarkAppliedMessage
  | MatchAnswerMessage
  | SaveAnswerMessage
  | DraftAnswerMessage
  | FetchApplicationFilesMessage
  | SignOutMessage;

/** Content script -> side panel (broadcast via runtime.sendMessage): the
 * page navigated client-side (Greenhouse and Ashby are SPAs, so no new
 * content script starts), and whatever the panel is showing for it is
 * stale. */
export interface PageChangedMessage {
  type: "PAGE_CHANGED";
}

export type MarkAppliedResult =
  | { ok: true; status: string }
  | { ok: false; message: string };

/** Side panel -> content script (direct, via browser.tabs.sendMessage --
 * only the content script can see page DOM state, so it owns detection
 * and fill status; background is purely the backend-API proxy, per
 * browser-extension.md's own architecture: "content script never talks
 * to the backend directly"). */
export type ContentScriptMessage =
  | { type: "GET_DETECTION_STATE" }
  | { type: "RECHECK" }
  | { type: "REQUEST_FILL"; forceRefillAll: boolean }
  // `force` (E6 continuation): the per-question "Replace" action, D5's
  // only escape hatch for a custom question -- defaults to false/absent
  // for every existing caller, matching "Refill all"'s own explicit scope
  // (it never touches custom questions at all). See each ATS's own
  // `fillCustomTextAnswer` for exactly what `force` does and doesn't
  // bypass.
  | { type: "FILL_FIELD"; fieldName: string; value: string; force?: boolean };

/** Why a per-question fill wrote nothing. `not_empty` is D5 (the field
 * already has text); `page_changed` means the tab no longer shows the page
 * the panel was looking at, so nothing was written to it. */
export interface FillFieldResult {
  filled: boolean;
  reason?: "not_empty" | "page_changed" | "refused";
}

export interface DetectionStateResponse {
  formDetected: boolean;
  tabState: TabState | null;
}

/** `null` from a REQUEST_FILL response means detection hasn't resolved
 * yet, distinct from a genuine FillResult with everything empty.
 * `fieldMapError` (E3c) is non-null whenever D4's fail-closed case fired
 * for this fill -- cover-letter discovery and custom-question surfacing
 * were skipped entirely, not just left empty, so the side panel can tell
 * "nothing to find" apart from "couldn't check." */
export interface FillResult {
  filledFields: string[];
  skippedFields: string[];
  resumeAttached: boolean;
  resumeError: string | null;
  coverLetterAttached: boolean;
  coverLetterError: string | null;
  unresolvedQuestions: { fieldName: string; label: string | null; kind: QuestionKind }[];
  fieldMapError: string | null;
  /** Set when the fill itself threw unexpectedly. Whatever was written
   * before the failure is still reported above -- this only says the run
   * didn't complete, instead of the panel waiting on a reply that never
   * comes. */
  fillError: string | null;
}
