import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, apiFetchBlob } from "./api";
import type { ChecklistItem, PrepareApplicationResult } from "./generateTypes";
import { friendlyApiMessage } from "./rateLimitMessage";
import { failureOf, type SetupNotice } from "./setupRequired";
import { clockTime } from "./todayFeed";

// Everything the workspace does about a resume, in one place, so the page (which lights the band from
// it) and the sections that draw it (Generate, Review) read the same state:
//
//  - the stored outcome of the last run, read once on mount (GET /applications/{id}/prepare-result);
//    a failed read leaves the section at "no result" and says so (`read`), it never fakes one;
//  - a run (POST /applications/{id}/prepare), with the setup failures kept apart from the rest so
//    they keep their link, and the client time a failure happened, for the band's why line;
//  - the PDF and cover-letter downloads, and that a PDF was downloaded in this visit (nothing stores
//    that, so after a reload the band does not say it);
//  - the export checks, fetched only when asked and once per run: they recompile the LaTeX, so they
//    never run on mount and never read as free.
//
// A drafted resume has no stored time, so nothing here carries one. A result that came from the
// stored read rather than from a run in this visit is marked `restored`.

export type GenerateState =
  | { kind: "idle" }
  | { kind: "generating" }
  | { kind: "ready"; result: PrepareApplicationResult; restored: boolean }
  | { kind: "setup"; notice: SetupNotice }
  | { kind: "error"; message: string; at: string };

export type ChecklistState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; items: ChecklistItem[] }
  | { kind: "error"; message: string };

export type ReadState = "loading" | "done" | "failed";

export interface GenerationController {
  read: ReadState;
  state: GenerateState;
  /** A resume exists for this application, from the page's read or from a run in this visit. */
  hasResume: boolean;
  /**
   * A stored result for this resume may exist that this page is not showing: the read of it failed, or
   * a result that was on screen was dropped by a run that did not finish. Not the same as "nothing is
   * stored", which is a read that answered with none.
   */
  resultUnreadable: boolean;
  /** The result in view made a cover letter. */
  hasCoverLetter: boolean;
  wantCoverLetter: boolean;
  setWantCoverLetter: (want: boolean) => void;
  /** A PDF of the resume was downloaded in this visit. */
  downloaded: boolean;
  downloading: "resume" | "cover" | null;
  downloadError: string | null;
  checklist: ChecklistState;
  generate: (forceGenerate?: boolean) => Promise<void>;
  downloadResume: () => Promise<void>;
  downloadCoverLetter: () => Promise<void>;
  loadChecklist: () => Promise<void>;
  retryRead: () => void;
}

async function save(blob: Blob, name: string): Promise<void> {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function useGeneration(applicationId: string, initialHasResume: boolean): GenerationController {
  const [state, setState] = useState<GenerateState>({ kind: "idle" });
  const [read, setRead] = useState<ReadState>("loading");
  const [readAttempt, setReadAttempt] = useState(0);
  // A result has been in view in this visit. A run clears it, so if that run then fails the page no
  // longer shows one it did have; that is "couldn't show it", not "none stored".
  const [hadResult, setHadResult] = useState(false);
  const [checklist, setChecklist] = useState<ChecklistState>({ kind: "idle" });
  const [downloading, setDownloading] = useState<"resume" | "cover" | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const [wantCoverLetter, setWantCoverLetter] = useState(false);
  // A press while a run is out does nothing: the controls are aria-disabled, which leaves them
  // focusable and clickable, so the guard is here.
  const running = useRef(false);
  // Which request for the export checks may still write its answer. The checks recompile the resume
  // that existed when they were asked for, so an answer that lands after a new generation began (or
  // after a newer ask) is about a PDF that is no longer the one in view, and is dropped.
  const checklistRun = useRef(0);

  // Restores the last real run after a reload. A component that unmounts mid-fetch is harmless: the
  // result is only applied while this effect is still the current one. A run started while the read
  // was out wins: the stored outcome is older than the one on screen.
  useEffect(() => {
    let cancelled = false;
    setRead("loading");
    void (async () => {
      try {
        const response = await apiFetch<{ result: PrepareApplicationResult | null }>(`/applications/${applicationId}/prepare-result`);
        if (cancelled) return;
        const restored = response.result;
        if (restored !== null && restored !== undefined) {
          setState((current) => (current.kind === "idle" ? { kind: "ready", result: restored, restored: true } : current));
        }
        setRead("done");
      } catch {
        if (!cancelled) setRead("failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applicationId, readAttempt]);

  useEffect(() => {
    if (state.kind === "ready") setHadResult(true);
  }, [state.kind]);

  const retryRead = useCallback(() => setReadAttempt((attempt) => attempt + 1), []);

  const generate = useCallback(
    async (forceGenerate = false) => {
      if (running.current) return;
      running.current = true;
      setState({ kind: "generating" });
      checklistRun.current += 1;
      setChecklist({ kind: "idle" });
      setDownloadError(null);
      setDownloaded(false);
      try {
        const result = await apiFetch<PrepareApplicationResult>(`/applications/${applicationId}/prepare`, {
          method: "POST",
          body: JSON.stringify({
            idempotency_key: crypto.randomUUID(),
            force_generate: forceGenerate,
            generate_cover_letter: wantCoverLetter,
          }),
        });
        setState({ kind: "ready", result, restored: false });
      } catch (e) {
        // No profile yet, or no model key: the first thing a new account hits here, and the fix is a
        // page away, so a setup failure keeps its link to it.
        const failure = failureOf(e, "Failed to generate");
        setState(failure.kind === "setup" ? failure : { kind: "error", message: failure.message, at: clockTime(new Date()) });
      } finally {
        running.current = false;
      }
    },
    [applicationId, wantCoverLetter],
  );

  const download = useCallback(
    async (which: "resume" | "cover") => {
      setDownloading(which);
      setDownloadError(null);
      try {
        const blob = await apiFetchBlob(`/applications/${applicationId}/${which === "resume" ? "resume.pdf" : "cover-letter.pdf"}`);
        await save(blob, which === "resume" ? "resume.pdf" : "cover-letter.pdf");
        if (which === "resume") setDownloaded(true);
      } catch (e) {
        setDownloadError(friendlyApiMessage(e, "Failed to download"));
      } finally {
        setDownloading(null);
      }
    },
    [applicationId],
  );

  const downloadResume = useCallback(() => download("resume"), [download]);
  const downloadCoverLetter = useCallback(() => download("cover"), [download]);

  const loadChecklist = useCallback(async () => {
    const run = ++checklistRun.current;
    setChecklist({ kind: "loading" });
    try {
      const response = await apiFetch<{ items: ChecklistItem[] }>(`/applications/${applicationId}/export-checklist`);
      if (run === checklistRun.current) setChecklist({ kind: "ready", items: response.items });
    } catch (e) {
      if (run === checklistRun.current) setChecklist({ kind: "error", message: friendlyApiMessage(e, "Failed to load checklist") });
    }
  }, [applicationId]);

  const hasResume = initialHasResume || (state.kind === "ready" && state.result.resume !== null);
  const hasCoverLetter = state.kind === "ready" && !!state.result.cover_letter;
  const resultUnreadable = read === "failed" || (hadResult && (state.kind === "error" || state.kind === "setup"));

  return {
    read,
    state,
    hasResume,
    resultUnreadable,
    hasCoverLetter,
    wantCoverLetter,
    setWantCoverLetter,
    downloaded,
    downloading,
    downloadError,
    checklist,
    generate,
    downloadResume,
    downloadCoverLetter,
    loadChecklist,
    retryRead,
  };
}
