// Importing a resume from a PDF or DOCX: the page's state, what each failure says, and the three
// things it asks the server to do.
//
// The server reads the file with the person's own AI model and answers with a DRAFT: a pending
// profile version that nothing uses until the person says so. The model can be wrong, so the draft
// is shown value by value, next to the place in the file each came from, and ONLY the confirm
// button uses it:
//
//   - `uploadFile` sends the file and only ever answers with a draft to review. It never activates.
//   - `activateDraft` is the one function that does. It is given the page's state and returns
//     without sending anything unless that state is a draft the person has been asked about
//     ("confirming") and has said yes to (`draftToActivate`). The reducer separately ignores the
//     matching events from any other state, so the page does not change either.
//   - `discardDraft` deletes the pending version.
//
// A wiring test (resumeImportWiring.test.ts) fails if any other code in the web app calls the
// activation route after an upload.
//
// Pure module: the requests are injected (api.ts pulls in the Supabase client, which throws at
// import time without env vars) and there is no React here. The page that binds it is
// components/ResumeImportCard.tsx; what it draws is components/ResumeImportView.tsx.

import { ENROLLMENT_REQUIRED_MESSAGE } from "./rateLimitMessage";
import {
  FILE_TOO_LARGE_MESSAGE,
  NOT_PDF_OR_DOCX_MESSAGE,
  prepareImport,
  type ImportableFile,
} from "./resumeImportFile";
import { parseImportResponse, type ImportDraft } from "./resumeImportDraft";
import { failureOf, setupRequiredNotice, type Failure } from "./setupRequired";

// ── the state ──────────────────────────────────────────────────────────────

export type ImportState =
  // Nothing chosen, or the last one is finished with.
  | { kind: "idle" }
  // A file was chosen and its first bytes are being read, to say what it is.
  | { kind: "reading"; fileName: string }
  // The file is with the server, which is reading it with the person's model.
  | { kind: "uploading"; fileName: string }
  // The draft, for the person to check. `openPath` is the value whose place in the file is on
  // screen; `confirming` is whether the person has been asked "really use this?".
  | {
      kind: "review";
      draft: ImportDraft;
      fileName: string;
      confirming: boolean;
      openPath: string | null;
      // The last attempt to use or discard the draft failed: why.
      problem: Failure | null;
    }
  | { kind: "activating"; draft: ImportDraft; fileName: string; openPath: string | null }
  | { kind: "discarding"; draft: ImportDraft; fileName: string; openPath: string | null }
  // The draft is now the person's profile. `replacedCurrent`: it took the place of another one,
  // which stays in their history.
  | { kind: "activated"; replacedCurrent: boolean }
  // The draft is gone (or, when `keptInHistory`, it matched a version used before, which stays).
  | { kind: "discarded"; keptInHistory: boolean }
  // Nothing was imported. `retry` says whether sending the same file again could work.
  | { kind: "error"; failure: Failure; retry: boolean; fileName: string | null };

export const initialImportState: ImportState = { kind: "idle" };

export type ImportEvent =
  | { type: "file_chosen"; fileName: string }
  | { type: "file_refused"; message: string }
  | { type: "upload_started" }
  | { type: "uploaded"; draft: ImportDraft }
  | { type: "upload_failed"; failure: Failure; retry: boolean }
  | { type: "source_toggled"; path: string }
  | { type: "confirm_asked" }
  | { type: "confirm_cancelled" }
  | { type: "activate_started" }
  | { type: "activated"; replacedCurrent: boolean }
  | { type: "activate_failed"; failure: Failure }
  | { type: "discard_started" }
  | { type: "discarded"; keptInHistory: boolean }
  | { type: "discard_failed"; failure: Failure }
  | { type: "closed" };

// An event that does not belong in the state it arrives in is ignored, never applied: a late answer
// for a draft that was already dealt with, a second click that raced the first.
export function importReducer(state: ImportState, event: ImportEvent): ImportState {
  switch (event.type) {
    case "file_chosen":
      if (state.kind === "idle" || state.kind === "error" || state.kind === "activated" || state.kind === "discarded") {
        return { kind: "reading", fileName: event.fileName };
      }
      return state;
    case "file_refused":
      if (state.kind !== "reading") return state;
      return { kind: "error", failure: { kind: "error", message: event.message }, retry: false, fileName: state.fileName };
    case "upload_started":
      return state.kind === "reading" ? { kind: "uploading", fileName: state.fileName } : state;
    case "uploaded":
      if (state.kind !== "uploading") return state;
      return {
        kind: "review",
        draft: event.draft,
        fileName: state.fileName,
        confirming: false,
        openPath: null,
        problem: null,
      };
    case "upload_failed":
      if (state.kind !== "uploading") return state;
      return { kind: "error", failure: event.failure, retry: event.retry, fileName: state.fileName };
    case "source_toggled":
      if (state.kind !== "review") return state;
      return { ...state, openPath: state.openPath === event.path ? null : event.path };
    case "confirm_asked":
      // Not for a draft that already is the profile in use: there is nothing to confirm.
      if (state.kind !== "review" || state.draft.alreadyActive) return state;
      return { ...state, confirming: true, problem: null };
    case "confirm_cancelled":
      return state.kind === "review" ? { ...state, confirming: false, problem: null } : state;
    case "activate_started":
      // The only way in: a draft the person was asked about, that is not already in use.
      if (state.kind !== "review" || !state.confirming || state.draft.alreadyActive) return state;
      return { kind: "activating", draft: state.draft, fileName: state.fileName, openPath: state.openPath };
    case "activated":
      return state.kind === "activating" ? { kind: "activated", replacedCurrent: event.replacedCurrent } : state;
    case "activate_failed":
      if (state.kind !== "activating") return state;
      return {
        kind: "review",
        draft: state.draft,
        fileName: state.fileName,
        confirming: true,
        openPath: state.openPath,
        problem: event.failure,
      };
    case "discard_started":
      if (state.kind !== "review" || state.draft.alreadyActive) return state;
      return { kind: "discarding", draft: state.draft, fileName: state.fileName, openPath: state.openPath };
    case "discarded":
      return state.kind === "discarding" ? { kind: "discarded", keptInHistory: event.keptInHistory } : state;
    case "discard_failed":
      if (state.kind !== "discarding") return state;
      return {
        kind: "review",
        draft: state.draft,
        fileName: state.fileName,
        confirming: false,
        openPath: state.openPath,
        problem: event.failure,
      };
    case "closed":
      // A finished attempt, a refused file, or a draft that is already the profile in use (which
      // has nothing to use or discard): back to the start. A draft that is still pending is not
      // closed this way -- it is used or discarded.
      if (state.kind === "activated" || state.kind === "discarded" || state.kind === "error") {
        return initialImportState;
      }
      if (state.kind === "review" && state.draft.alreadyActive) return initialImportState;
      return state;
  }
}

// The draft the confirm button may use: only one the person has been asked about, that is not
// already the profile in use. Null from every other state, which is what keeps a stray click, a
// late render or a second press from using anything.
export function draftToActivate(state: ImportState): ImportDraft | null {
  return state.kind === "review" && state.confirming && !state.draft.alreadyActive ? state.draft : null;
}

// The draft the discard button may delete: a pending one (not the profile in use, which cannot be
// discarded).
export function draftToDiscard(state: ImportState): ImportDraft | null {
  return state.kind === "review" && !state.draft.alreadyActive ? state.draft : null;
}

// Whether the file button may be pressed: not while something is being read, sent or decided.
export function canChooseFile(state: ImportState): boolean {
  return (
    state.kind === "idle" || state.kind === "error" || state.kind === "activated" || state.kind === "discarded"
  );
}

// Whether the person has a file import under way that the page must not take away from them: a file
// being read or sent, a draft waiting for their decision (or being used or discarded). The page
// holds the JSON import back meanwhile, since showing that one's preview replaces this card and
// drops the review without a word. Not a draft that already is the profile in use (there is
// nothing to decide, and closing it loses nothing), nor a finished or failed attempt.
export function importInProgress(state: ImportState): boolean {
  switch (state.kind) {
    case "reading":
    case "uploading":
    case "activating":
    case "discarding":
      return true;
    case "review":
      return !state.draft.alreadyActive;
    case "idle":
    case "activated":
    case "discarded":
    case "error":
      return false;
  }
}

// ── focus ──────────────────────────────────────────────────────────────────

// What the page moves keyboard focus to when it changes. The control a person just used often goes
// away (the file button is disabled while the file is sent, "Use this profile" becomes a confirm
// question), and a removed element drops focus to the document, which sends a keyboard or screen
// reader user back to the top of the page. The view draws each of these with its id and tabIndex
// -1, so they take focus without joining the tab order.
export const IMPORT_IDS = {
  choose: "bj-import-choose",
  status: "bj-import-status",
  error: "bj-import-error",
  review: "bj-import-review-title",
  use: "bj-import-use",
  confirm: "bj-import-confirm",
  problem: "bj-import-problem",
  notice: "bj-import-notice",
  closeReview: "bj-import-close",
} as const;

export function focusTargetAfterChange(before: ImportState, after: ImportState): string | null {
  if (before === after) return null;
  if (after.kind === "reading" && before.kind !== "reading") return IMPORT_IDS.status;
  if (after.kind === "error" && before.kind !== "error") return IMPORT_IDS.error;
  // The question became a request in flight: the status line says what is happening.
  if ((after.kind === "activating" || after.kind === "discarding") && before.kind === "review") {
    return IMPORT_IDS.status;
  }
  if (after.kind === "review") {
    if (before.kind === "uploading") return IMPORT_IDS.review;
    if (after.problem !== null && (before.kind !== "review" || before.problem !== after.problem)) {
      return IMPORT_IDS.problem;
    }
    if (before.kind === "review") {
      if (after.confirming && !before.confirming) return IMPORT_IDS.confirm;
      if (!after.confirming && before.confirming) return IMPORT_IDS.use;
    }
    return null;
  }
  if ((after.kind === "activated" || after.kind === "discarded") && before.kind !== after.kind) {
    return IMPORT_IDS.notice;
  }
  if (after.kind === "idle") return IMPORT_IDS.choose;
  return null;
}

// ── what a failure says ────────────────────────────────────────────────────

export const NETWORK_MESSAGE = "We could not reach the server. Check your connection, then try again.";
// An edge's own reply for a long request that did not finish (a restart, a timeout): the route never
// activates anything, so "your profile was not changed" is true. It says nothing about whether a
// draft was saved, which is not known here.
export const SERVER_DID_NOT_FINISH_MESSAGE =
  "The server did not finish reading your file. Your profile was not changed. Try again.";
export const SESSION_ENDED_MESSAGE = "Your session has ended. Sign in again, then try again.";
export const UNREADABLE_ANSWER_MESSAGE =
  "The server's answer could not be read. If a draft was saved it is not in use. Try uploading the file again.";
export const DRAFT_GONE_MESSAGE = "That draft no longer exists. Upload the file again.";
export const IMPORT_SETUP_MESSAGE =
  "Reading a resume file needs your own AI model. Add a model key in Integrations, then upload the file again.";
// The server's `details.reason` for its 429 when every file reader is busy (profile_routes.py,
// `_READERS_BUSY`; a test compares the two). That is the server's own capacity, not a count of the
// person's requests, so it is not worded as "you are doing that too often".
export const READERS_BUSY_REASON = "readers_busy";
export const IMPORT_BUSY_MESSAGE = "Several resume files are being read right now. Try again in a few seconds.";

// What `ApiError` carries, read structurally so this module needs no import of it.
interface ApiErrorFields {
  status?: unknown;
  code?: unknown;
  retryable?: unknown;
  message?: unknown;
  reason?: unknown;
}

function fieldsOf(error: unknown): ApiErrorFields | null {
  if (!(error instanceof Error)) return null;
  return typeof (error as ApiErrorFields).status === "number" ? (error as ApiErrorFields) : null;
}

export interface ImportFailure {
  failure: Failure;
  // Whether sending the same thing again can change the outcome.
  retry: boolean;
}

// Why a request of the import failed, in words for a person, and whether to offer a retry.
//   - SETUP_REQUIRED: the shared notice with its link to Integrations (a model key). No retry:
//     asking again cannot work until the key is saved.
//   - 413 (too large) and 415 (not a PDF or DOCX): the file is the problem; no retry.
//   - 422: the server's own sentence, which is written for the person (a scan with no text, an
//     encrypted file, a file that cannot be read). No retry: it is the same file.
//   - RATE_LIMITED because every file reader is busy (the reply says so, by `reason`): a sentence
//     that blames nobody, and a retry. It is told apart by that marker, never by the wait or the
//     wording, which the per-user limit can share.
//   - any other RATE_LIMITED: the wait, and a retry.
//   - ENROLLMENT_REQUIRED: the programme sentence.
//   - a gateway reply with no error envelope (502, 503, 504 and no code): the request was cut off
//     on the way, not refused for anything about the file. A sentence that says so, and a retry.
//     A 502 or 503 WITH a code is the server's own answer and keeps its own sentence.
//   - a failed fetch: the network sentence, and a retry.
//   - anything else: the server's own sentence; a retry only when the server says it is retryable.
export function importFailureOf(error: unknown, fallback: string): ImportFailure {
  const notice = setupRequiredNotice(error);
  if (notice !== null) {
    // The server's own sentence for this capability names it ("'profile_import' has no execution
    // mode configured yet."), which is not for a person; the notice's link is the server's, and kept.
    const ours = (error as { capability?: unknown }).capability === "profile_import";
    return {
      failure: { kind: "setup", notice: ours ? { ...notice, message: IMPORT_SETUP_MESSAGE } : notice },
      retry: false,
    };
  }

  const fields = fieldsOf(error);
  if (fields === null) {
    // Not an answer from the server at all: the request never got one.
    return { failure: { kind: "error", message: NETWORK_MESSAGE }, retry: true };
  }

  if (fields.status === 401) {
    return { failure: { kind: "error", message: SESSION_ENDED_MESSAGE }, retry: false };
  }
  if (fields.code === "PAYLOAD_TOO_LARGE" || fields.status === 413) {
    return { failure: { kind: "error", message: FILE_TOO_LARGE_MESSAGE }, retry: false };
  }
  if (fields.code === "UNSUPPORTED_MEDIA_TYPE" || fields.status === 415) {
    const sentence = typeof fields.message === "string" && fields.message.trim() !== "" ? fields.message : NOT_PDF_OR_DOCX_MESSAGE;
    return { failure: { kind: "error", message: sentence }, retry: false };
  }
  if (fields.code === "ENROLLMENT_REQUIRED") {
    return { failure: { kind: "error", message: ENROLLMENT_REQUIRED_MESSAGE }, retry: false };
  }
  if (fields.code === undefined && (fields.status === 502 || fields.status === 503 || fields.status === 504)) {
    return { failure: { kind: "error", message: SERVER_DID_NOT_FINISH_MESSAGE }, retry: true };
  }
  if (fields.code === "RATE_LIMITED" && fields.reason === READERS_BUSY_REASON) {
    return { failure: { kind: "error", message: IMPORT_BUSY_MESSAGE }, retry: true };
  }
  const rateLimited = fields.code === "RATE_LIMITED" || (fields.code === undefined && fields.status === 429);
  return {
    failure: failureOf(error, fallback),
    retry: rateLimited || fields.retryable === true,
  };
}

// ── the three requests ─────────────────────────────────────────────────────

export interface ImportIo<F> {
  // POST the file's bytes (`apiFetchBytes`).
  upload(path: string, body: F, contentType: string): Promise<unknown>;
  // POST /profile/versions/{id}/activate
  activate(versionId: string): Promise<unknown>;
  // DELETE /profile/versions/{id}
  discard(versionId: string): Promise<unknown>;
}

export const UPLOAD_FAILED = "Could not import that file.";
export const ACTIVATE_FAILED = "Could not switch to that profile. Nothing was changed.";
export const DISCARD_FAILED = "Could not discard that draft.";

// Chooses what to send for a file, sends it, and reports each step as an event, in order. It ends in
// either a draft to review or a failure; it never activates anything.
export async function uploadFile<F extends ImportableFile>(
  file: F,
  io: ImportIo<F>,
  dispatch: (event: ImportEvent) => void,
): Promise<void> {
  dispatch({ type: "file_chosen", fileName: file.name });
  const prepared = await prepareImport(file);
  if (!prepared.ok) {
    dispatch({ type: "file_refused", message: prepared.message });
    return;
  }
  dispatch({ type: "upload_started" });
  let answer: unknown;
  try {
    answer = await io.upload(prepared.request.path, prepared.request.body, prepared.request.contentType);
  } catch (error) {
    const { failure, retry } = importFailureOf(error, UPLOAD_FAILED);
    dispatch({ type: "upload_failed", failure, retry });
    return;
  }
  const draft = parseImportResponse(answer);
  if (draft === null) {
    dispatch({ type: "upload_failed", failure: { kind: "error", message: UNREADABLE_ANSWER_MESSAGE }, retry: true });
    return;
  }
  dispatch({ type: "uploaded", draft });
}

// Makes the draft the person's active profile. Called from the confirm button and from nowhere
// else. It acts only on `draftToActivate(state)`: from any state that is not a draft the person was
// asked about, nothing is sent, nothing is dispatched and the answer is false, so a stray call, a
// stale render or a second press cannot use a draft. `activated` is dispatched only when the server
// said yes. `replacesCurrent` is whether there was an active profile for it to take the place of
// (the page knows), so the message afterwards does not claim a history that is not there.
export async function activateDraft<F>(
  state: ImportState,
  replacesCurrent: boolean,
  io: ImportIo<F>,
  dispatch: (event: ImportEvent) => void,
): Promise<boolean> {
  const draft = draftToActivate(state);
  if (draft === null) return false;
  dispatch({ type: "activate_started" });
  try {
    await io.activate(draft.versionId);
  } catch (error) {
    dispatch({ type: "activate_failed", failure: draftFailure(error, ACTIVATE_FAILED) });
    return false;
  }
  dispatch({ type: "activated", replacedCurrent: replacesCurrent });
  return true;
}

// Deletes the pending draft. A draft the server says is already gone, or that matches a version
// used before (and so stays in the history), is closed all the same: there is nothing left to
// discard.
export async function discardDraft<F>(
  versionId: string,
  io: ImportIo<F>,
  dispatch: (event: ImportEvent) => void,
): Promise<void> {
  dispatch({ type: "discard_started" });
  try {
    await io.discard(versionId);
  } catch (error) {
    const fields = fieldsOf(error);
    if (fields !== null && fields.code === "CONFLICT") {
      dispatch({ type: "discarded", keptInHistory: true });
      return;
    }
    if (isDraftGone(fields)) {
      dispatch({ type: "discarded", keptInHistory: false });
      return;
    }
    dispatch({ type: "discard_failed", failure: draftFailure(error, DISCARD_FAILED) });
    return;
  }
  dispatch({ type: "discarded", keptInHistory: false });
}

// The server says the draft does not exist: its own 404 NOT_FOUND. A 404 with no envelope is a
// proxy, a CDN or a wrong API address answering, which says nothing about the draft, so it is not
// taken to mean that the draft is gone (and "discarded" is never claimed for a request that may
// not have reached the API).
function isDraftGone(fields: ApiErrorFields | null): boolean {
  return fields !== null && fields.status === 404 && fields.code === "NOT_FOUND";
}

// A failed attempt to use or discard a draft. A draft the server no longer has is said plainly;
// the rest is `importFailureOf`.
function draftFailure(error: unknown, fallback: string): Failure {
  if (isDraftGone(fieldsOf(error))) return { kind: "error", message: DRAFT_GONE_MESSAGE };
  return importFailureOf(error, fallback).failure;
}
