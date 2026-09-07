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
  prepare_result: { resume?: { artifact_id: string; version_id: string } } | null;
  personal_info: ExtensionPersonalInfo | null;
}

/** The résumé PDF, base64-encoded for structured-clone transfer across
 * the background <-> content-script message boundary. Fetched by
 * background (the only context allowed to talk to the backend, per this
 * phase's own architecture) once per detection, not per fill click. */
export interface ResumeFile {
  base64: string;
  filename: string;
}

/** What the background script learns about a tab after the content
 * script reports a detected Lever form and the backend lookup completes.
 * Held in background's own per-tab state map; the side panel and content
 * script both ask for it rather than duplicating the lookup. */
export type TabState =
  | { status: "signed_out" }
  | { status: "untracked" }
  | { status: "tracked"; applicationId: string; payload: ExtensionPayload; resume: ResumeFile | null }
  | { status: "error"; message: string };

/** Content script -> background, on detecting a Lever apply form. */
export interface LeverPageDetectedMessage {
  type: "LEVER_PAGE_DETECTED";
  url: string;
}

export type BackgroundMessage = LeverPageDetectedMessage;

/** Side panel -> content script (direct, via browser.tabs.sendMessage --
 * only the content script can see page DOM state, so it owns detection
 * and fill status; background is purely the backend-API proxy, per
 * browser-extension.md's own architecture: "content script never talks
 * to the backend directly"). */
export type ContentScriptMessage =
  | { type: "GET_DETECTION_STATE" }
  | { type: "RECHECK" }
  | { type: "REQUEST_FILL"; forceRefillAll: boolean };

export interface DetectionStateResponse {
  formDetected: boolean;
  tabState: TabState | null;
}

/** `null` from a REQUEST_FILL response means detection hasn't resolved
 * yet, distinct from a genuine FillResult with everything empty. */
export interface FillResult {
  filledFields: string[];
  skippedFields: string[];
  resumeAttached: boolean;
  resumeError: string | null;
  unresolvedQuestions: { fieldName: string; label: string | null }[];
}
