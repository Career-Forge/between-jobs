import { useEffect, useImperativeHandle, useMemo, useReducer, useRef, useState, type Ref } from "react";
import { apiFetch, apiFetchBytes } from "../lib/api";
import {
  activateDraft,
  discardDraft,
  draftToActivate,
  draftToDiscard,
  focusTargetAfterChange,
  importInProgress,
  importReducer,
  initialImportState,
  uploadFile,
  type ImportIo,
  type ImportState,
} from "../lib/resumeImport";
import type { ModelKeyReading } from "../lib/modelKey";
import { fileReviewTrail } from "../lib/profileTrails";
import { buildReview, fieldsToCheck } from "../lib/resumeImportReview";
import { BlockTrail } from "./paper/DocumentLayout";
import { IMPORT_MARK_ID, ResumeImportView, type ReviewChecks } from "./ResumeImportView";

// Connected wrapper for the resume-file import: thin glue over the tested state machine and requests
// in lib/resumeImport.ts, drawn by ResumeImportView.
//
// A draft is used in exactly one place: `confirmUse`, which only the confirm button of the view calls,
// and only after the person was asked: `activateDraft` sends nothing from any state that is not a
// draft the person was asked about (`draftToActivate`), and the card checks the same before it takes
// the in-flight flag. Leaving the page, a failed upload and a finished review never activate anything.
//
// THE PAGE'S BAND CAN USE A DRAFT TOO, with the same guard. While a draft waits, `onReviewChange` tells the
// page what is true of it (how many values were read from the file, how many were left out, how many the
// person has still to check), and the page's band holds a "Confirm and make active" button that calls
// `confirmAndUse` on this card's ref. That asks and confirms in one step through the same reducer events,
// and only for a draft that is waiting, is not already in use and has every value checked; the question
// "use this profile?" is still on the page before it (the review says that using it replaces the current
// one), and the button is the band's locked one until the last value is checked. Every path that sends the
// activation goes through `confirmUse`, which hands `activateDraft` a state the person was asked about.
//
// One request at a time: a ref, not the state, guards against a second click, since state only
// updates on the next render and a double click can land before it. FOCUS: after each change that
// removes the control the person just used, focus moves to what replaced it (focusTargetAfterChange).

const io: ImportIo<File> = {
  upload: (path, body, contentType) => apiFetchBytes(path, body, contentType),
  activate: (versionId) => apiFetch(`/profile/versions/${versionId}/activate`, { method: "POST" }),
  discard: (versionId) => apiFetch(`/profile/versions/${versionId}`, { method: "DELETE" }),
};

// What the page is told about a draft that is waiting for the person's decision.
export interface ReviewSummary {
  /** Values read from the file that the person is to check one by one. */
  found: number;
  /** Values the model proposed that the check against the file did not keep; null when the server did not say. */
  leftOut: number | null;
  /** How many of `found` are still unchecked. */
  remaining: number;
  /** The draft is being made the active profile right now. */
  using: boolean;
}

// What the page can ask of the card.
export interface ResumeImportHandle {
  /** Uses the waiting draft, if it may be used now (see the top of the file). Does nothing otherwise. */
  confirmAndUse: () => void;
}

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

export function ResumeImportCard({
  replacesCurrent,
  onActivated,
  blocked = false,
  onOpenChange,
  onReviewChange,
  modelKey,
  ref,
}: {
  // There is an active profile a draft would take the place of.
  replacesCurrent: boolean;
  // The profile in use changed: the page reloads it.
  onActivated: () => void;
  // The page is busy with the JSON import, so no file can be chosen meanwhile.
  blocked?: boolean;
  // Told whether a file import is under way (`importInProgress`), so the page can hold the JSON
  // import back: showing that one's preview replaces this card and would drop a review in progress.
  onOpenChange?: (open: boolean) => void;
  // Told what is true of a waiting draft whenever it changes, and null when none is waiting.
  onReviewChange?: (review: ReviewSummary | null) => void;
  // What the saved keys say about a usable model key, once the page has read them (see ResumeImportView).
  modelKey?: ModelKeyReading;
  ref?: Ref<ResumeImportHandle>;
}) {
  const [state, dispatch] = useReducer(importReducer, initialImportState);
  // Which values of the draft the person has checked. It belongs to one draft: a different draft starts empty.
  const [checks, setChecks] = useState<{ draftId: string | null; paths: ReadonlySet<string> }>({ draftId: null, paths: NOTHING_CHECKED });
  const fileInput = useRef<HTMLInputElement>(null);
  // The chosen file, kept so that "Try again" sends the same one.
  const chosen = useRef<File | null>(null);
  const inFlight = useRef(false);
  const previous = useRef<ImportState>(state);

  // The draft the person is deciding on, if there is one that is not already their profile.
  const waiting = state.kind === "review" || state.kind === "activating" || state.kind === "discarding" ? state : null;
  const draft = waiting !== null && !waiting.draft.alreadyActive ? waiting.draft : null;
  const draftId = draft === null ? null : draft.versionId;
  const toCheck = useMemo(() => (draft === null ? [] : fieldsToCheck(buildReview(draft.profile, draft.spans, draft.assumptions))), [draft]);
  const checked = checks.draftId === draftId ? checks.paths : NOTHING_CHECKED;
  const remaining = toCheck.filter((field) => !checked.has(field.path)).length;
  const found = toCheck.length;
  const leftOut = draft === null ? null : draft.droppedFields;
  const using = waiting !== null && waiting.kind === "activating";

  useEffect(() => {
    onOpenChange?.(importInProgress(state));
  }, [state, onOpenChange]);

  useEffect(() => {
    onReviewChange?.(draftId === null ? null : { found, leftOut, remaining, using });
  }, [draftId, found, leftOut, remaining, using, onReviewChange]);
  useEffect(() => () => onReviewChange?.(null), [onReviewChange]);
  // A card that goes away is no import under way.
  useEffect(() => () => onOpenChange?.(false), [onOpenChange]);

  useEffect(() => {
    const target = focusTargetAfterChange(previous.current, state);
    previous.current = state;
    if (target !== null) document.getElementById(target)?.focus();
  }, [state]);

  // Opening a value's source brings the marked part into view inside the text's own scroll area, moving
  // the page itself no more than it has to.
  const openPath = state.kind === "review" ? state.openPath : null;
  useEffect(() => {
    if (openPath === null) return;
    document.getElementById(IMPORT_MARK_ID)?.scrollIntoView?.({ block: "nearest" });
  }, [openPath]);

  async function start(file: File) {
    if (inFlight.current) return;
    inFlight.current = true;
    chosen.current = file;
    try {
      await uploadFile(file, io, dispatch);
    } finally {
      inFlight.current = false;
    }
  }

  // `asked` is the state the activation is allowed from: the card's own once the question is on screen, or
  // the same state with the question asked (the band's one-step button, which is locked until every value
  // is checked).
  async function confirmUse(asked: ImportState = state) {
    // Only a draft the person was asked about, which is where the button is drawn.
    const draft = draftToActivate(asked);
    if (inFlight.current || draft === null) return;
    inFlight.current = true;
    try {
      const done = await activateDraft(asked, replacesCurrent, io, dispatch);
      if (done) onActivated();
    } finally {
      inFlight.current = false;
    }
  }

  useImperativeHandle(ref, () => ({
    confirmAndUse: () => {
      if (state.kind !== "review" || state.draft.alreadyActive || remaining > 0) return;
      dispatch({ type: "confirm_asked" });
      void confirmUse(importReducer(state, { type: "confirm_asked" }));
    },
  }));

  async function discard() {
    const draft = draftToDiscard(state);
    if (inFlight.current || draft === null) return;
    inFlight.current = true;
    try {
      await discardDraft(draft.versionId, io, dispatch);
    } finally {
      inFlight.current = false;
    }
  }

  const reviewChecks: ReviewChecks | undefined =
    draft === null
      ? undefined
      : {
          checked,
          toggle: (path) =>
            setChecks((previous) => {
              const base = previous.draftId === draftId ? previous.paths : NOTHING_CHECKED;
              const next = new Set(base);
              if (next.has(path)) next.delete(path);
              else next.add(path);
              return { draftId, paths: next };
            }),
        };
  const spec = draft === null || waiting === null ? null : fileReviewTrail(draft, waiting.fileName, found);

  return (
    <ResumeImportView
      state={state}
      replacesCurrent={replacesCurrent}
      fileInputRef={fileInput}
      blocked={blocked}
      checks={reviewChecks}
      modelKey={modelKey}
      trail={spec === null ? undefined : <BlockTrail trail={spec.trail} receipt={spec.receipt} receiptTitle={spec.receiptTitle} />}
      actions={{
        chooseFile: () => fileInput.current?.click(),
        fileChosen: (files) => {
          const file = files?.[0];
          // The same file can be chosen again afterwards.
          if (fileInput.current) fileInput.current.value = "";
          if (file) void start(file);
        },
        retry: () => {
          if (chosen.current !== null) void start(chosen.current);
        },
        close: () => dispatch({ type: "closed" }),
        toggleSource: (path) => dispatch({ type: "source_toggled", path }),
        // Not until every value is checked (the view draws the button locked; this is the same rule where
        // a press gets round it).
        askUse: () => {
          if (remaining === 0) dispatch({ type: "confirm_asked" });
        },
        cancelUse: () => dispatch({ type: "confirm_cancelled" }),
        confirmUse: () => void confirmUse(state),
        discard: () => void discard(),
      }}
    />
  );
}
