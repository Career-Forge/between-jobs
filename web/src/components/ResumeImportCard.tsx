import { useEffect, useReducer, useRef } from "react";
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
import { IMPORT_MARK_ID, ResumeImportView } from "./ResumeImportView";

// Connected wrapper for the resume-file import: thin glue over the tested state machine and requests
// in lib/resumeImport.ts, drawn by ResumeImportView.
//
// A draft is used in exactly one place: `confirmUse`, which only the confirm button of the view calls,
// and only after the person was asked: `activateDraft` sends nothing from any state that is not a
// draft the person was asked about (`draftToActivate`), and the card checks the same before it takes
// the in-flight flag. Leaving the page, a failed upload and a finished review never activate anything.
//
// One request at a time: a ref, not the state, guards against a second click, since state only
// updates on the next render and a double click can land before it. FOCUS: after each change that
// removes the control the person just used, focus moves to what replaced it (focusTargetAfterChange).

const io: ImportIo<File> = {
  upload: (path, body, contentType) => apiFetchBytes(path, body, contentType),
  activate: (versionId) => apiFetch(`/profile/versions/${versionId}/activate`, { method: "POST" }),
  discard: (versionId) => apiFetch(`/profile/versions/${versionId}`, { method: "DELETE" }),
};

export function ResumeImportCard({
  replacesCurrent,
  onActivated,
  blocked = false,
  onOpenChange,
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
}) {
  const [state, dispatch] = useReducer(importReducer, initialImportState);
  const fileInput = useRef<HTMLInputElement>(null);
  // The chosen file, kept so that "Try again" sends the same one.
  const chosen = useRef<File | null>(null);
  const inFlight = useRef(false);
  const previous = useRef<ImportState>(state);

  useEffect(() => {
    onOpenChange?.(importInProgress(state));
  }, [state, onOpenChange]);
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

  async function confirmUse() {
    // Only a draft the person was asked about, which is where the button is drawn.
    const draft = draftToActivate(state);
    if (inFlight.current || draft === null) return;
    inFlight.current = true;
    try {
      const done = await activateDraft(state, replacesCurrent, io, dispatch);
      if (done) onActivated();
    } finally {
      inFlight.current = false;
    }
  }

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

  return (
    <ResumeImportView
      state={state}
      replacesCurrent={replacesCurrent}
      fileInputRef={fileInput}
      blocked={blocked}
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
        askUse: () => dispatch({ type: "confirm_asked" }),
        cancelUse: () => dispatch({ type: "confirm_cancelled" }),
        confirmUse: () => void confirmUse(),
        discard: () => void discard(),
      }}
    />
  );
}
