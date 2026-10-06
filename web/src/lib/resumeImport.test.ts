import { describe, expect, it } from "vitest";
import profileRoutesSource from "../../../src/between_jobs/api/profile_routes.py?raw";
import { parseImportResponse, type ImportDraft } from "./resumeImportDraft";
import {
  ACTIVATE_FAILED,
  DISCARD_FAILED,
  DRAFT_GONE_MESSAGE,
  IMPORT_BUSY_MESSAGE,
  IMPORT_IDS,
  IMPORT_SETUP_MESSAGE,
  NETWORK_MESSAGE,
  READERS_BUSY_REASON,
  SERVER_DID_NOT_FINISH_MESSAGE,
  SESSION_ENDED_MESSAGE,
  UNREADABLE_ANSWER_MESSAGE,
  UPLOAD_FAILED,
  activateDraft,
  canChooseFile,
  discardDraft,
  focusTargetAfterChange,
  importFailureOf,
  importInProgress,
  importReducer,
  initialImportState,
  uploadFile,
  type ImportEvent,
  type ImportIo,
  type ImportState,
} from "./resumeImport";
import { FILE_TOO_LARGE_MESSAGE, MAX_IMPORT_BYTES, NOT_PDF_OR_DOCX_MESSAGE } from "./resumeImportFile";
import { ENROLLMENT_REQUIRED_MESSAGE } from "./rateLimitMessage";

// What ApiError (api.ts) carries, without importing it (that pulls in the Supabase client).
class FakeApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
    public readonly retryable?: boolean,
    public readonly retryAfterSeconds?: number,
    public readonly settingsPath?: string,
    public readonly capability?: string,
    public readonly missing?: readonly string[],
    public readonly reason?: string,
  ) {
    super(message);
  }
}

function draft(overrides: Record<string, unknown> = {}): ImportDraft {
  const parsed = parseImportResponse({
    version_id: "v-1",
    already_active: false,
    profile: { personal: { name: "Pat Example" } },
    span_unit: "utf16",
    source_spans: { "/personal/name": { start: 0, end: 11 } },
    dropped: [],
    assumptions: [],
    extracted_text: "Pat Example",
    stats: {},
    warnings: [],
    document: {},
    ...overrides,
  });
  if (parsed === null) throw new Error("the fixture is not a draft");
  return parsed;
}

const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37]);

function pdfFile(name = "cv.pdf"): File {
  return new File([PDF], name);
}

// Runs events through the reducer from a start state.
function run(start: ImportState, ...events: ImportEvent[]): ImportState {
  return events.reduce(importReducer, start);
}

const IN_REVIEW: ImportState = run(
  initialImportState,
  { type: "file_chosen", fileName: "cv.pdf" },
  { type: "upload_started" },
  { type: "uploaded", draft: draft() },
);

describe("the import's states, step by step", () => {
  it("goes from nothing, through reading and sending, to a draft under review", () => {
    expect(initialImportState).toEqual({ kind: "idle" });
    const reading = importReducer(initialImportState, { type: "file_chosen", fileName: "cv.pdf" });
    expect(reading).toEqual({ kind: "reading", fileName: "cv.pdf" });
    const uploading = importReducer(reading, { type: "upload_started" });
    expect(uploading).toEqual({ kind: "uploading", fileName: "cv.pdf" });
    const review = importReducer(uploading, { type: "uploaded", draft: draft() });
    expect(review).toMatchObject({ kind: "review", fileName: "cv.pdf", confirming: false, openPath: null, problem: null });
  });

  it("goes from a refused file or a failed upload to an error that remembers whether to offer a retry", () => {
    const refused = run(initialImportState, { type: "file_chosen", fileName: "x.txt" }, { type: "file_refused", message: "no" });
    expect(refused).toEqual({ kind: "error", failure: { kind: "error", message: "no" }, retry: false, fileName: "x.txt" });
    const failed = run(
      initialImportState,
      { type: "file_chosen", fileName: "cv.pdf" },
      { type: "upload_started" },
      { type: "upload_failed", failure: { kind: "error", message: "down" }, retry: true },
    );
    expect(failed).toMatchObject({ kind: "error", retry: true, fileName: "cv.pdf" });
  });

  it("lets a person choose again from an error, a finished draft and a discarded one -- and not from anything in progress", () => {
    const choose: ImportEvent = { type: "file_chosen", fileName: "again.pdf" };
    for (const state of [
      initialImportState,
      run(initialImportState, { type: "file_chosen", fileName: "a" }, { type: "file_refused", message: "x" }),
      { kind: "activated", replacedCurrent: true } as ImportState,
      { kind: "discarded", keptInHistory: false } as ImportState,
    ]) {
      expect(importReducer(state, choose).kind, state.kind).toBe("reading");
    }
    for (const state of [
      { kind: "reading", fileName: "a" } as ImportState,
      { kind: "uploading", fileName: "a" } as ImportState,
      IN_REVIEW,
    ]) {
      expect(importReducer(state, choose), state.kind).toBe(state);
    }
  });

  it("shows where a value came from one at a time: opening another closes the first, opening it again closes it", () => {
    const opened = importReducer(IN_REVIEW, { type: "source_toggled", path: "/personal/name" });
    expect(opened).toMatchObject({ openPath: "/personal/name" });
    expect(importReducer(opened, { type: "source_toggled", path: "/personal/headline" })).toMatchObject({
      openPath: "/personal/headline",
    });
    expect(importReducer(opened, { type: "source_toggled", path: "/personal/name" })).toMatchObject({ openPath: null });
  });

  it("asks before using the draft, and backs out of the question", () => {
    const asked = importReducer(IN_REVIEW, { type: "confirm_asked" });
    expect(asked).toMatchObject({ kind: "review", confirming: true });
    expect(importReducer(asked, { type: "confirm_cancelled" })).toMatchObject({ confirming: false });
  });

  it("uses the draft, and shows that it did", () => {
    const asked = importReducer(IN_REVIEW, { type: "confirm_asked" });
    const activating = importReducer(asked, { type: "activate_started" });
    expect(activating.kind).toBe("activating");
    expect(importReducer(activating, { type: "activated", replacedCurrent: true })).toEqual({ kind: "activated", replacedCurrent: true });
  });

  it("goes back to the question, with the draft and the reason, when using it failed", () => {
    const activating = run(IN_REVIEW, { type: "confirm_asked" }, { type: "activate_started" });
    const back = importReducer(activating, { type: "activate_failed", failure: { kind: "error", message: "nope" } });
    expect(back).toMatchObject({
      kind: "review",
      confirming: true,
      problem: { kind: "error", message: "nope" },
    });
    if (back.kind !== "review") throw new Error("unreachable");
    expect(back.draft.versionId).toBe("v-1");
  });

  it("discards the draft, or goes back to it with the reason when that failed", () => {
    const discarding = importReducer(IN_REVIEW, { type: "discard_started" });
    expect(discarding.kind).toBe("discarding");
    expect(importReducer(discarding, { type: "discarded", keptInHistory: false })).toEqual({
      kind: "discarded",
      keptInHistory: false,
    });
    expect(importReducer(discarding, { type: "discard_failed", failure: { kind: "error", message: "x" } })).toMatchObject({
      kind: "review",
      confirming: false,
      problem: { kind: "error", message: "x" },
    });
  });

  it("asking the question again clears the failure of a discard that did not go through", () => {
    const failed = run(
      IN_REVIEW,
      { type: "discard_started" },
      { type: "discard_failed", failure: { kind: "error", message: "Could not discard that draft." } },
    );
    expect(failed).toMatchObject({ kind: "review", problem: { kind: "error" } });
    const asked = importReducer(failed, { type: "confirm_asked" });
    expect(asked).toMatchObject({ kind: "review", confirming: true, problem: null });
  });

  it("keeps the open source through using or discarding that fails", () => {
    const opened = importReducer(IN_REVIEW, { type: "source_toggled", path: "/personal/name" });
    const failed = run(opened, { type: "discard_started" }, { type: "discard_failed", failure: { kind: "error", message: "x" } });
    expect(failed).toMatchObject({ kind: "review", openPath: "/personal/name" });
  });

  it("closes a finished or failed attempt back to the start, but never a draft that is still pending", () => {
    for (const state of [
      { kind: "activated", replacedCurrent: true } as ImportState,
      { kind: "discarded", keptInHistory: true } as ImportState,
      run(initialImportState, { type: "file_chosen", fileName: "a" }, { type: "file_refused", message: "x" }),
    ]) {
      expect(importReducer(state, { type: "closed" }), state.kind).toEqual(initialImportState);
    }
    expect(importReducer(IN_REVIEW, { type: "closed" })).toBe(IN_REVIEW);
    const asked = importReducer(IN_REVIEW, { type: "confirm_asked" });
    expect(importReducer(asked, { type: "closed" })).toBe(asked);
  });
});

describe("a draft is used only when the person has said so", () => {
  it("cannot start activating from anywhere but a question that was asked", () => {
    const start: ImportEvent = { type: "activate_started" };
    for (const state of [
      initialImportState,
      { kind: "reading", fileName: "a" } as ImportState,
      { kind: "uploading", fileName: "a" } as ImportState,
      IN_REVIEW, // a draft that was never asked about
      { kind: "activated", replacedCurrent: true } as ImportState,
      { kind: "discarded", keptInHistory: false } as ImportState,
      run(initialImportState, { type: "file_chosen", fileName: "a" }, { type: "file_refused", message: "x" }),
    ]) {
      expect(importReducer(state, start), state.kind).toBe(state);
    }
  });

  it("an upload that comes back never lands in anything but a draft to review: no event goes from there straight to activating", () => {
    const uploading = run(initialImportState, { type: "file_chosen", fileName: "a" }, { type: "upload_started" });
    const everyEvent: ImportEvent[] = [
      { type: "uploaded", draft: draft() },
      { type: "activated", replacedCurrent: true },
      { type: "activate_started" },
      { type: "confirm_asked" },
    ];
    // however they are ordered, activating needs asking first
    for (const order of [everyEvent, [...everyEvent].reverse()]) {
      expect(run(uploading, ...order).kind).not.toBe("activated");
    }
    expect(run(uploading, { type: "uploaded", draft: draft() }).kind).toBe("review");
  });

  it("ignores an answer that arrives for a draft that is not being activated or discarded any more", () => {
    expect(importReducer(IN_REVIEW, { type: "activated", replacedCurrent: true })).toBe(IN_REVIEW);
    expect(importReducer(IN_REVIEW, { type: "discarded", keptInHistory: false })).toBe(IN_REVIEW);
    expect(importReducer(IN_REVIEW, { type: "activate_failed", failure: { kind: "error", message: "x" } })).toBe(IN_REVIEW);
    expect(importReducer(initialImportState, { type: "uploaded", draft: draft() })).toBe(initialImportState);
  });

  it("offers nothing to use or discard for a draft that already is the profile in use", () => {
    const same = run(
      initialImportState,
      { type: "file_chosen", fileName: "cv.pdf" },
      { type: "upload_started" },
      { type: "uploaded", draft: draft({ already_active: true }) },
    );
    expect(importReducer(same, { type: "confirm_asked" })).toBe(same);
    expect(importReducer(same, { type: "discard_started" })).toBe(same);
    expect(importReducer(same, { type: "closed" })).toEqual(initialImportState);
  });

  it("does not let a draft be discarded while it is being activated, or the other way round", () => {
    const activating = run(IN_REVIEW, { type: "confirm_asked" }, { type: "activate_started" });
    expect(importReducer(activating, { type: "discard_started" })).toBe(activating);
    const discarding = importReducer(IN_REVIEW, { type: "discard_started" });
    expect(importReducer(discarding, { type: "confirm_asked" })).toBe(discarding);
    expect(importReducer(discarding, { type: "activate_started" })).toBe(discarding);
  });
});

describe("the file button", () => {
  it("is available only when nothing is in progress and no draft is waiting to be decided", () => {
    expect(canChooseFile(initialImportState)).toBe(true);
    expect(canChooseFile({ kind: "activated", replacedCurrent: true })).toBe(true);
    expect(canChooseFile({ kind: "discarded", keptInHistory: false })).toBe(true);
    expect(canChooseFile({ kind: "error", failure: { kind: "error", message: "x" }, retry: false, fileName: null })).toBe(true);
    for (const state of [
      { kind: "reading", fileName: "a" } as ImportState,
      { kind: "uploading", fileName: "a" } as ImportState,
      IN_REVIEW,
      run(IN_REVIEW, { type: "confirm_asked" }, { type: "activate_started" }),
      importReducer(IN_REVIEW, { type: "discard_started" }),
    ]) {
      expect(canChooseFile(state), state.kind).toBe(false);
    }
  });
});

// What the page holds the JSON import back for: a file import with something in it the person could
// lose if the page replaced the card (components/ResumeImportCard.tsx reports it, pages/Profile.tsx
// acts on it).
describe("whether a file import is under way", () => {
  it("is, while a file is read or sent, a draft waits to be decided, or a decision is being sent", () => {
    for (const state of [
      { kind: "reading", fileName: "a" } as ImportState,
      { kind: "uploading", fileName: "a" } as ImportState,
      IN_REVIEW,
      run(IN_REVIEW, { type: "confirm_asked" }), // the question is up
      run(IN_REVIEW, { type: "confirm_asked" }, { type: "activate_started" }),
      importReducer(IN_REVIEW, { type: "discard_started" }),
    ]) {
      expect(importInProgress(state), state.kind).toBe(true);
    }
  });

  it("is not at the start, after an error, or once the draft is used or discarded", () => {
    for (const state of [
      initialImportState,
      { kind: "error", failure: { kind: "error", message: "x" }, retry: true, fileName: "a" } as ImportState,
      { kind: "activated", replacedCurrent: true } as ImportState,
      { kind: "discarded", keptInHistory: false } as ImportState,
    ]) {
      expect(importInProgress(state), state.kind).toBe(false);
    }
  });

  it("is not for a draft that already is the profile in use: there is nothing to decide, or to lose", () => {
    const inUse = run(
      initialImportState,
      { type: "file_chosen", fileName: "cv.pdf" },
      { type: "upload_started" },
      { type: "uploaded", draft: draft({ already_active: true }) },
    );
    expect(inUse.kind).toBe("review");
    expect(importInProgress(inUse)).toBe(false);
  });

  it("covers every state there is (a new one has to be decided here)", () => {
    const kinds = new Set<string>();
    for (const state of [
      initialImportState,
      { kind: "reading", fileName: "a" } as ImportState,
      { kind: "uploading", fileName: "a" } as ImportState,
      IN_REVIEW,
      run(IN_REVIEW, { type: "confirm_asked" }, { type: "activate_started" }),
      importReducer(IN_REVIEW, { type: "discard_started" }),
      { kind: "activated", replacedCurrent: false } as ImportState,
      { kind: "discarded", keptInHistory: false } as ImportState,
      { kind: "error", failure: { kind: "error", message: "x" }, retry: false, fileName: null } as ImportState,
    ]) {
      kinds.add(state.kind);
      expect(typeof importInProgress(state)).toBe("boolean");
    }
    expect([...kinds].sort()).toEqual(
      ["activated", "activating", "discarded", "discarding", "error", "idle", "reading", "review", "uploading"],
    );
  });
});

describe("where focus goes when the page changes", () => {
  const asked = importReducer(IN_REVIEW, { type: "confirm_asked" });
  const uploading: ImportState = { kind: "uploading", fileName: "cv.pdf" };
  const errored = run(
    initialImportState,
    { type: "file_chosen", fileName: "a" },
    { type: "file_refused", message: "x" },
  );

  it("moves to the status when a file is chosen (the button that opened the chooser goes away)", () => {
    expect(focusTargetAfterChange(initialImportState, { kind: "reading", fileName: "a" })).toBe(IMPORT_IDS.status);
  });

  it("moves to the review's heading when the draft arrives", () => {
    expect(focusTargetAfterChange(uploading, IN_REVIEW)).toBe(IMPORT_IDS.review);
  });

  it("moves to the error when one appears, once", () => {
    expect(focusTargetAfterChange(uploading, errored)).toBe(IMPORT_IDS.error);
    expect(focusTargetAfterChange(errored, { ...errored })).toBeNull(); // still the same error
  });

  it("moves to the question when it is asked, and back to the button when it is cancelled", () => {
    expect(focusTargetAfterChange(IN_REVIEW, asked)).toBe(IMPORT_IDS.confirm);
    expect(focusTargetAfterChange(asked, importReducer(asked, { type: "confirm_cancelled" }))).toBe(IMPORT_IDS.use);
  });

  it("moves to the status while a decision is being sent, and to the notice when it is done", () => {
    const activating = importReducer(asked, { type: "activate_started" });
    expect(focusTargetAfterChange(asked, activating)).toBe(IMPORT_IDS.status);
    expect(focusTargetAfterChange(activating, { kind: "activated", replacedCurrent: true })).toBe(IMPORT_IDS.notice);
    const discarding = importReducer(IN_REVIEW, { type: "discard_started" });
    expect(focusTargetAfterChange(IN_REVIEW, discarding)).toBe(IMPORT_IDS.status);
    expect(focusTargetAfterChange(discarding, { kind: "discarded", keptInHistory: false })).toBe(IMPORT_IDS.notice);
  });

  it("moves to the problem when using or discarding the draft failed", () => {
    const activating = importReducer(asked, { type: "activate_started" });
    const failed = importReducer(activating, { type: "activate_failed", failure: { kind: "error", message: "x" } });
    expect(focusTargetAfterChange(activating, failed)).toBe(IMPORT_IDS.problem);
  });

  it("moves to the file button when the page goes back to the start", () => {
    expect(focusTargetAfterChange({ kind: "activated", replacedCurrent: true }, initialImportState)).toBe(IMPORT_IDS.choose);
    expect(focusTargetAfterChange(errored, initialImportState)).toBe(IMPORT_IDS.choose);
  });

  it("leaves focus alone when nothing it cares about changed (a source opened, the same state again)", () => {
    expect(focusTargetAfterChange(IN_REVIEW, importReducer(IN_REVIEW, { type: "source_toggled", path: "/personal/name" }))).toBeNull();
    expect(focusTargetAfterChange(IN_REVIEW, IN_REVIEW)).toBeNull();
    expect(focusTargetAfterChange(initialImportState, initialImportState)).toBeNull();
  });

  it("only names ids the view draws, each once", () => {
    const ids = Object.values(IMPORT_IDS);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^bj-import-[a-z-]+$/);
  });
});

describe("what each failure says, and whether to offer a retry", () => {
  it("a missing model key is the shared setup notice, with a plain sentence of its own and no retry", () => {
    const error = new FakeApiError(
      409,
      "'profile_import' has no execution mode configured yet.",
      "SETUP_REQUIRED",
      false,
      undefined,
      "/profile/integrations?capability=profile_import",
      "profile_import",
      ["execution_mode"],
    );
    const { failure, retry } = importFailureOf(error, UPLOAD_FAILED);
    expect(retry).toBe(false);
    expect(failure).toEqual({
      kind: "setup",
      notice: {
        message: IMPORT_SETUP_MESSAGE,
        linkTo: "/profile/integrations?capability=profile_import",
        linkLabel: "Add a model key in Integrations",
      },
    });
  });

  it("another capability's setup notice keeps the server's own sentence", () => {
    const error = new FakeApiError(409, "Add your profile first.", "SETUP_REQUIRED", false, undefined, "/profile", "profile", ["profile_version"]);
    const { failure } = importFailureOf(error, UPLOAD_FAILED);
    expect(failure).toMatchObject({ kind: "setup", notice: { message: "Add your profile first." } });
  });

  it("a file that is too large (413) says the limit, however the server worded it, with no retry", () => {
    for (const error of [
      new FakeApiError(413, "whatever the proxy said", "PAYLOAD_TOO_LARGE", false),
      new FakeApiError(413, "Request failed (413)"),
    ]) {
      expect(importFailureOf(error, UPLOAD_FAILED)).toEqual({
        failure: { kind: "error", message: FILE_TOO_LARGE_MESSAGE },
        retry: false,
      });
    }
    expect(FILE_TOO_LARGE_MESSAGE).toContain("5 MiB");
    expect(MAX_IMPORT_BYTES).toBe(5242880);
  });

  it("a file of the wrong kind (415) shows the server's sentence, or says PDF or DOCX only, with no retry", () => {
    expect(
      importFailureOf(new FakeApiError(415, "That looks like a legacy Word .doc file. Only PDF or DOCX is supported.", "UNSUPPORTED_MEDIA_TYPE"), UPLOAD_FAILED),
    ).toEqual({
      failure: { kind: "error", message: "That looks like a legacy Word .doc file. Only PDF or DOCX is supported." },
      retry: false,
    });
    expect(importFailureOf(new FakeApiError(415, "", "UNSUPPORTED_MEDIA_TYPE"), UPLOAD_FAILED).failure).toEqual({
      kind: "error",
      message: NOT_PDF_OR_DOCX_MESSAGE,
    });
    expect(importFailureOf(new FakeApiError(415, "Request failed (415)"), UPLOAD_FAILED).failure).toMatchObject({ kind: "error" });
  });

  it("a file the server could not use (422) is the server's own sentence, word for word, with no retry", () => {
    for (const sentence of [
      "That PDF has no text in it: it looks like a scan. Export a text-based PDF and try again.",
      "That PDF is password-protected.",
      "That file could not be read.",
    ]) {
      expect(importFailureOf(new FakeApiError(422, sentence, "INVALID_INPUT", false), UPLOAD_FAILED)).toEqual({
        failure: { kind: "error", message: sentence },
        retry: false,
      });
    }
  });

  it("a rate limit says the wait, and offers a retry", () => {
    const limited = importFailureOf(new FakeApiError(429, "x", "RATE_LIMITED", true, 725), UPLOAD_FAILED);
    expect(limited.retry).toBe(true);
    expect(limited.failure).toEqual({
      kind: "error",
      message: "You are doing that too often. Try again in about 13 minutes.",
    });
    // a short wait of the per-user limit (a window about to roll over) is still the person's own count
    const shortWait = importFailureOf(new FakeApiError(429, "x", "RATE_LIMITED", true, 5), UPLOAD_FAILED);
    expect(shortWait.retry).toBe(true);
    expect(shortWait.failure).toMatchObject({ kind: "error", message: "You are doing that too often. Try again in less than a minute." });
    // an edge's own 429, which has no code
    expect(importFailureOf(new FakeApiError(429, "Too Many Requests"), UPLOAD_FAILED).retry).toBe(true);
    // Boundary hardening, not a reply this route sends today: a 429 that carries some other code
    // (an upstream provider's throttle) is not the platform's limit, and is retried only if it says so.
    expect(importFailureOf(new FakeApiError(429, "x", "PROVIDER_RATE_LIMITED", false), UPLOAD_FAILED).retry).toBe(false);
    expect(importFailureOf(new FakeApiError(429, "x", "PROVIDER_RATE_LIMITED", true), UPLOAD_FAILED).retry).toBe(true);
  });

  it("every reader being busy is not the person's fault: its own sentence, and a retry", () => {
    // told apart by the reply's own marker, not by the wait or the words
    const busy = importFailureOf(
      new FakeApiError(429, "Several resume files are being read right now.", "RATE_LIMITED", true, 5, undefined, undefined, undefined, "readers_busy"),
      UPLOAD_FAILED,
    );
    expect(busy).toEqual({ failure: { kind: "error", message: IMPORT_BUSY_MESSAGE }, retry: true });
    expect(IMPORT_BUSY_MESSAGE).toBe("Several resume files are being read right now. Try again in a few seconds.");
    expect(IMPORT_BUSY_MESSAGE).not.toMatch(/too often/i);
    // whatever the wait was
    expect(
      importFailureOf(new FakeApiError(429, "x", "RATE_LIMITED", true, 725, undefined, undefined, undefined, "readers_busy"), UPLOAD_FAILED).failure,
    ).toEqual({ kind: "error", message: IMPORT_BUSY_MESSAGE });
    // a 429 that names some other reason is the rate limit's wording, and one with the marker but
    // another code is not taken for it
    expect(
      importFailureOf(new FakeApiError(429, "x", "RATE_LIMITED", true, 5, undefined, undefined, undefined, "something_else"), UPLOAD_FAILED).failure,
    ).toEqual({ kind: "error", message: "You are doing that too often. Try again in less than a minute." });
    expect(
      importFailureOf(new FakeApiError(500, "Boom", "INTERNAL_ERROR", false, undefined, undefined, undefined, undefined, "readers_busy"), UPLOAD_FAILED),
    ).toEqual({ failure: { kind: "error", message: "Boom" }, retry: false });
  });

  it("the marker is the one the server sends", () => {
    expect(READERS_BUSY_REASON).toBe("readers_busy");
    expect(profileRoutesSource).toContain(`_READERS_BUSY = "${READERS_BUSY_REASON}"`);
    expect(profileRoutesSource).toContain('"reason": _READERS_BUSY');
  });

  // A reply from an edge in front of the API (a restart, a timeout on a long request) has no error
  // envelope, so no code: nothing is wrong with the file, and the route activates nothing, so asking
  // again is safe.
  it("a gateway reply with no envelope (502, 503, 504) says the server did not finish, and offers a retry", () => {
    for (const status of [502, 503, 504]) {
      expect(importFailureOf(new FakeApiError(status, `Request failed (${status})`), UPLOAD_FAILED)).toEqual({
        failure: { kind: "error", message: SERVER_DID_NOT_FINISH_MESSAGE },
        retry: true,
      });
    }
    expect(SERVER_DID_NOT_FINISH_MESSAGE).toBe(
      "The server did not finish reading your file. Your profile was not changed. Try again.",
    );
    // it claims nothing about whether a draft was saved, which is not known
    expect(SERVER_DID_NOT_FINISH_MESSAGE).not.toMatch(/draft|saved/i);
  });

  it("an answer of the server's own with a code keeps its own sentence, even at a gateway status", () => {
    expect(
      importFailureOf(new FakeApiError(502, "The model's answer could not be read.", "RUN_FAILED", true), UPLOAD_FAILED),
    ).toEqual({ failure: { kind: "error", message: "The model's answer could not be read." }, retry: true });
    expect(importFailureOf(new FakeApiError(503, "Try again shortly.", "PROVIDER_UNAVAILABLE", false), UPLOAD_FAILED)).toEqual({
      failure: { kind: "error", message: "Try again shortly." },
      retry: false,
    });
  });

  it("an enrollment refusal is the programme sentence", () => {
    expect(importFailureOf(new FakeApiError(403, "x", "ENROLLMENT_REQUIRED"), UPLOAD_FAILED)).toEqual({
      failure: { kind: "error", message: ENROLLMENT_REQUIRED_MESSAGE },
      retry: false,
    });
  });

  it("a request that never got an answer is the network sentence, with a retry", () => {
    for (const error of [new TypeError("Failed to fetch"), new Error("NetworkError when attempting to fetch resource.")]) {
      expect(importFailureOf(error, UPLOAD_FAILED)).toEqual({
        failure: { kind: "error", message: NETWORK_MESSAGE },
        retry: true,
      });
    }
    expect(importFailureOf("a thrown string", UPLOAD_FAILED).failure).toEqual({ kind: "error", message: NETWORK_MESSAGE });
  });

  it("a session that has ended says to sign in again", () => {
    expect(importFailureOf(new FakeApiError(401, "Not signed in."), UPLOAD_FAILED)).toEqual({
      failure: { kind: "error", message: SESSION_ENDED_MESSAGE },
      retry: false,
    });
  });

  it("anything else is the server's sentence, with a retry only when the server says it can help", () => {
    const failed = importFailureOf(
      new FakeApiError(502, "The model's answer couldn't be read as a profile. Try again, or paste the JSON from your own AI tool instead.", "RUN_FAILED", true),
      UPLOAD_FAILED,
    );
    expect(failed.retry).toBe(true);
    expect(failed.failure).toMatchObject({ kind: "error", message: expect.stringContaining("Try again, or paste the JSON") });
    expect(importFailureOf(new FakeApiError(500, "Boom", "INTERNAL_ERROR", false), UPLOAD_FAILED)).toEqual({
      failure: { kind: "error", message: "Boom" },
      retry: false,
    });
    expect(importFailureOf(new FakeApiError(500, "Boom"), UPLOAD_FAILED).retry).toBe(false);
  });
});

// ── the three requests, with the network replaced ──────────────────────────

interface Calls {
  uploads: { path: string; contentType: string; body: unknown }[];
  activations: string[];
  discards: string[];
}

function io(overrides: Partial<ImportIo<File>> = {}): { io: ImportIo<File>; calls: Calls } {
  const calls: Calls = { uploads: [], activations: [], discards: [] };
  const fake: ImportIo<File> = {
    upload: async (path, body, contentType) => {
      calls.uploads.push({ path, contentType, body });
      return {
        version_id: "v-9",
        already_active: false,
        profile: { personal: { name: "Pat Example" } },
        span_unit: "utf16",
        source_spans: {},
        extracted_text: "Pat Example",
      };
    },
    activate: async (versionId) => {
      calls.activations.push(versionId);
      return {};
    },
    discard: async (versionId) => {
      calls.discards.push(versionId);
    },
    ...overrides,
  };
  return { io: fake, calls };
}

function recorder(): { events: ImportEvent[]; dispatch: (event: ImportEvent) => void } {
  const events: ImportEvent[] = [];
  return { events, dispatch: (event) => events.push(event) };
}

describe("uploadFile", () => {
  it("sends the file as it is with the type its bytes give, then hands back a draft -- and activates nothing", async () => {
    const { io: fake, calls } = io();
    const { events, dispatch } = recorder();
    const file = pdfFile("Pat Example CV.pdf");

    await uploadFile(file, fake, dispatch);

    expect(events.map((event) => event.type)).toEqual(["file_chosen", "upload_started", "uploaded"]);
    expect(events[0]).toEqual({ type: "file_chosen", fileName: "Pat Example CV.pdf" });
    expect(calls.uploads).toHaveLength(1);
    expect(calls.uploads[0].path).toBe("/profile/import-document?filename=resume.pdf");
    expect(calls.uploads[0].contentType).toBe("application/pdf");
    expect(calls.uploads[0].body).toBe(file);
    expect(calls.activations).toEqual([]);
    expect(calls.discards).toEqual([]);
    const done = events[2];
    expect(done.type === "uploaded" && done.draft.versionId).toBe("v-9");
  });

  it("refuses a file that is not a PDF or a DOCX without sending anything", async () => {
    const { io: fake, calls } = io();
    const { events, dispatch } = recorder();
    await uploadFile(new File([new Uint8Array([0x68, 0x69])], "notes.txt"), fake, dispatch);
    expect(events.map((event) => event.type)).toEqual(["file_chosen", "file_refused"]);
    expect(events[1]).toEqual({ type: "file_refused", message: NOT_PDF_OR_DOCX_MESSAGE });
    expect(calls.uploads).toEqual([]);
  });

  it("refuses a file that is too large without reading or sending it", async () => {
    const { io: fake, calls } = io();
    const { events, dispatch } = recorder();
    const big = { name: "big.pdf", size: MAX_IMPORT_BYTES + 1, slice: () => ({ arrayBuffer: async () => PDF.buffer as ArrayBuffer }) } as unknown as File;
    await uploadFile(big, fake, dispatch);
    expect(events[1]).toEqual({ type: "file_refused", message: FILE_TOO_LARGE_MESSAGE });
    expect(calls.uploads).toEqual([]);
  });

  it("reports a failed request as a failure with its words and its retry", async () => {
    const { io: fake } = io({
      upload: async () => {
        throw new FakeApiError(422, "That PDF is password-protected.", "INVALID_INPUT", false);
      },
    });
    const { events, dispatch } = recorder();
    await uploadFile(pdfFile(), fake, dispatch);
    expect(events.map((event) => event.type)).toEqual(["file_chosen", "upload_started", "upload_failed"]);
    expect(events[2]).toEqual({
      type: "upload_failed",
      failure: { kind: "error", message: "That PDF is password-protected." },
      retry: false,
    });
  });

  it("a network failure offers a retry, which sends the same file again", async () => {
    let attempts = 0;
    const { io: fake, calls } = io({
      upload: async (path, body, contentType) => {
        attempts += 1;
        if (attempts === 1) throw new TypeError("Failed to fetch");
        calls.uploads.push({ path, contentType, body });
        return { version_id: "v-2", already_active: false, profile: { personal: { name: "Pat" } } };
      },
    });
    const { events, dispatch } = recorder();
    const file = pdfFile();
    await uploadFile(file, fake, dispatch);
    const failed = events[events.length - 1];
    expect(failed).toMatchObject({ type: "upload_failed", retry: true, failure: { message: NETWORK_MESSAGE } });
    await uploadFile(file, fake, dispatch);
    expect(events[events.length - 1].type).toBe("uploaded");
    expect(calls.uploads).toHaveLength(1);
  });

  it("treats an answer it cannot read as a failure that can be retried, and uses nothing from it", async () => {
    const { io: fake } = io({ upload: async () => ({ version_id: "v", profile: {} }) });
    const { events, dispatch } = recorder();
    await uploadFile(pdfFile(), fake, dispatch);
    expect(events[events.length - 1]).toEqual({
      type: "upload_failed",
      failure: { kind: "error", message: UNREADABLE_ANSWER_MESSAGE },
      retry: true,
    });
  });

  it("through the reducer: a whole upload ends in a draft to review, with the file's own name", async () => {
    const { io: fake } = io();
    let state: ImportState = initialImportState;
    await uploadFile(pdfFile("Pat Example CV.pdf"), fake, (event) => {
      state = importReducer(state, event);
    });
    expect(state).toMatchObject({ kind: "review", fileName: "Pat Example CV.pdf", confirming: false });
  });
});

// A draft the person has been asked about ("Use this profile?"), which is the only state
// activateDraft acts on.
function askedAbout(versionId = "v-9", overrides: Record<string, unknown> = {}): ImportState {
  return run(
    initialImportState,
    { type: "file_chosen", fileName: "cv.pdf" },
    { type: "upload_started" },
    { type: "uploaded", draft: draft({ version_id: versionId, ...overrides }) },
    { type: "confirm_asked" },
  );
}

describe("activateDraft", () => {
  it("asks the server to activate that one version, and says so only when it answered", async () => {
    const { io: fake, calls } = io();
    const { events, dispatch } = recorder();
    expect(await activateDraft(askedAbout("v-9"), true, fake, dispatch)).toBe(true);
    expect(calls.activations).toEqual(["v-9"]);
    expect(events.map((event) => event.type)).toEqual(["activate_started", "activated"]);
  });

  it("remembers whether the draft took the place of a profile, for the message afterwards", async () => {
    for (const replaces of [true, false]) {
      const { io: fake } = io();
      const { events, dispatch } = recorder();
      await activateDraft(askedAbout(), replaces, fake, dispatch);
      expect(events[1]).toEqual({ type: "activated", replacedCurrent: replaces });
    }
  });

  it("reports a refusal as a failure, in words, and not as a success", async () => {
    const { io: fake } = io({
      activate: async () => {
        throw new FakeApiError(500, "Boom", "INTERNAL_ERROR");
      },
    });
    const { events, dispatch } = recorder();
    expect(await activateDraft(askedAbout(), true, fake, dispatch)).toBe(false);
    expect(events.map((event) => event.type)).toEqual(["activate_started", "activate_failed"]);
    expect(events[1]).toEqual({ type: "activate_failed", failure: { kind: "error", message: "Boom" } });
  });

  it("says plainly when the draft is gone", async () => {
    const { io: fake } = io({
      activate: async () => {
        throw new FakeApiError(404, "no profile version found for id 'v-9'", "NOT_FOUND");
      },
    });
    const { events, dispatch } = recorder();
    await activateDraft(askedAbout(), true, fake, dispatch);
    expect(events[1]).toEqual({ type: "activate_failed", failure: { kind: "error", message: DRAFT_GONE_MESSAGE } });
  });

  it("does not say the draft is gone for a 404 that is not the server's own answer (a proxy, a wrong address)", async () => {
    const { io: fake } = io({
      activate: async () => {
        throw new FakeApiError(404, "Request failed (404)");
      },
    });
    const { events, dispatch } = recorder();
    expect(await activateDraft(askedAbout(), true, fake, dispatch)).toBe(false);
    const failed = events[1];
    expect(failed.type).toBe("activate_failed");
    expect(failed).not.toEqual({ type: "activate_failed", failure: { kind: "error", message: DRAFT_GONE_MESSAGE } });
    expect(failed).toMatchObject({ failure: { kind: "error", message: "Request failed (404)" } });
  });

  it("a network failure is the network sentence, and the draft is still there to try again", async () => {
    const { io: fake } = io({
      activate: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    let state: ImportState = askedAbout("v-1");
    await activateDraft(state, true, fake, (event) => {
      state = importReducer(state, event);
    });
    expect(state).toMatchObject({ kind: "review", confirming: true, problem: { kind: "error", message: NETWORK_MESSAGE } });
  });

  it("through the reducer: asking and then confirming ends with the profile activated", async () => {
    const { io: fake, calls } = io();
    let state: ImportState = askedAbout("v-1");
    await activateDraft(state, true, fake, (event) => {
      state = importReducer(state, event);
    });
    expect(state).toEqual({ kind: "activated", replacedCurrent: true });
    expect(calls.activations).toEqual(["v-1"]);
  });

  // The guard is in the function itself, not left to its caller: from a state that is not a draft the
  // person was asked about, no request is made at all (and so nothing can become the profile in use
  // with no visible effect, which a request that the page ignores would be).
  it("sends nothing, and dispatches nothing, from any state that is not a draft that was asked about", async () => {
    const neverAsked = IN_REVIEW;
    const cancelled = importReducer(askedAbout("v-1"), { type: "confirm_cancelled" });
    const alreadyInUse = run(
      initialImportState,
      { type: "file_chosen", fileName: "cv.pdf" },
      { type: "upload_started" },
      { type: "uploaded", draft: draft({ already_active: true }) },
    );
    const activating = importReducer(askedAbout("v-1"), { type: "activate_started" });
    const discarding = importReducer(IN_REVIEW, { type: "discard_started" });
    const states: [string, ImportState][] = [
      ["idle", initialImportState],
      ["a draft never asked about", neverAsked],
      ["a question backed out of", cancelled],
      ["a draft already in use", alreadyInUse],
      ["a draft being activated", activating],
      ["a draft being discarded", discarding],
      ["an upload in progress", { kind: "uploading", fileName: "cv.pdf" }],
      ["an activated draft", { kind: "activated", replacedCurrent: true }],
      ["an error", { kind: "error", failure: { kind: "error", message: "x" }, retry: false, fileName: null }],
    ];
    for (const [name, state] of states) {
      const { io: fake, calls } = io();
      const { events, dispatch } = recorder();
      expect(await activateDraft(state, true, fake, dispatch), name).toBe(false);
      expect(calls.activations, name).toEqual([]);
      expect(events, name).toEqual([]);
    }
  });
});

describe("discardDraft", () => {
  it("deletes the draft and says it is gone", async () => {
    const { io: fake, calls } = io();
    const { events, dispatch } = recorder();
    await discardDraft("v-9", fake, dispatch);
    expect(calls.discards).toEqual(["v-9"]);
    expect(events).toEqual([{ type: "discard_started" }, { type: "discarded", keptInHistory: false }]);
  });

  it("treats a draft the server says is already gone as discarded", async () => {
    const { io: fake } = io({
      discard: async () => {
        throw new FakeApiError(404, "no profile version found", "NOT_FOUND");
      },
    });
    const { events, dispatch } = recorder();
    await discardDraft("v-9", fake, dispatch);
    expect(events[1]).toEqual({ type: "discarded", keptInHistory: false });
  });

  // A 404 with no error envelope is a proxy, a CDN or a wrong API address, not the API saying the
  // draft is gone: "discarded" would be claimed for a request that may never have reached it, and the
  // draft, which holds the person's resume, would stay saved.
  it("does not call a draft discarded because of a 404 that is not the server's own answer", async () => {
    for (const error of [new FakeApiError(404, "Request failed (404)"), new FakeApiError(404, "Not Found")]) {
      const { io: fake } = io({
        discard: async () => {
          throw error;
        },
      });
      const { events, dispatch } = recorder();
      await discardDraft("v-9", fake, dispatch);
      expect(events.map((event) => event.type)).toEqual(["discard_started", "discard_failed"]);

      let state: ImportState = IN_REVIEW;
      for (const event of events) state = importReducer(state, event);
      expect(state).toMatchObject({ kind: "review", confirming: false, problem: { kind: "error" } });
      expect(state).not.toMatchObject({ kind: "discarded" });
    }
  });

  it("treats a draft that matches a version used before (a 409) as closed, and says it stays in the history", async () => {
    const { io: fake } = io({
      discard: async () => {
        throw new FakeApiError(409, "that version has already been activated and can't be cancelled", "CONFLICT");
      },
    });
    const { events, dispatch } = recorder();
    await discardDraft("v-9", fake, dispatch);
    expect(events[1]).toEqual({ type: "discarded", keptInHistory: true });
  });

  it("reports any other failure and leaves the draft where it was", async () => {
    const { io: fake } = io({
      discard: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    let state: ImportState = IN_REVIEW;
    await discardDraft("v-1", fake, (event) => {
      state = importReducer(state, event);
    });
    expect(state).toMatchObject({ kind: "review", problem: { kind: "error", message: NETWORK_MESSAGE } });
    expect(DISCARD_FAILED.length).toBeGreaterThan(0);
    expect(ACTIVATE_FAILED).toContain("Nothing was changed");
  });
});

describe("which draft a button may act on", () => {
  const asked = importReducer(IN_REVIEW, { type: "confirm_asked" });
  const inUse = run(
    initialImportState,
    { type: "file_chosen", fileName: "cv.pdf" },
    { type: "upload_started" },
    { type: "uploaded", draft: draft({ already_active: true }) },
  );

  it("may be used only once the person was asked, and only if it is not already the profile in use", async () => {
    const { draftToActivate } = await import("./resumeImport");
    expect(draftToActivate(IN_REVIEW)).toBeNull(); // never asked
    expect(draftToActivate(asked)?.versionId).toBe("v-1");
    expect(draftToActivate(inUse)).toBeNull();
    for (const state of [
      initialImportState,
      { kind: "reading", fileName: "a" } as ImportState,
      { kind: "uploading", fileName: "a" } as ImportState,
      { kind: "activated", replacedCurrent: true } as ImportState,
      importReducer(asked, { type: "activate_started" }),
    ]) {
      expect(draftToActivate(state), state.kind).toBeNull();
    }
  });

  it("may be discarded while pending, but not once the person has already been asked to use it elsewhere, and not when it is in use", async () => {
    const { draftToDiscard } = await import("./resumeImport");
    expect(draftToDiscard(IN_REVIEW)?.versionId).toBe("v-1");
    expect(draftToDiscard(asked)?.versionId).toBe("v-1");
    expect(draftToDiscard(inUse)).toBeNull();
    expect(draftToDiscard(initialImportState)).toBeNull();
    expect(draftToDiscard(importReducer(IN_REVIEW, { type: "discard_started" }))).toBeNull();
  });
});
