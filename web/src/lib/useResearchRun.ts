import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "./api";
import { friendlyApiMessage } from "./rateLimitMessage";
import { failureOf, type Failure } from "./setupRequired";

// The requests behind a research section (Company intel, Contacts, Events): one GET on mount for the
// stored run and one POST to make a new one, at the same path. The three sections differ only in
// what a run holds, so they share this.
//
//   read    the mount read: loading, failed (nothing to show, Retry reads again) or ready (`data`, which
//           may be an empty run)
//   running a POST is out. The section keeps whatever it was showing and busies only its run button
//   problem the last run's failure, or null. A failed run NEVER replaces what the section already
//           shows: the earlier result stays, and the problem is drawn beside it. A setup problem
//           (SETUP_REQUIRED, or a provider turning the person's key down) carries the notice with its
//           link and has no Retry, because asking again cannot work until it is fixed
//
// A press while a run is out is ignored here, not by the button: the button is aria-disabled (so a
// focused one keeps focus), which does not stop a click.

export type ResearchRead<T> = { kind: "loading" } | { kind: "failed"; message: string } | { kind: "ready"; data: T };

export interface ResearchRunController<T> {
  read: ResearchRead<T>;
  running: boolean;
  problem: Failure | null;
  /** Reads the stored run again (the mount read's Retry). */
  load: () => Promise<void>;
  /** Makes a new run (a paid one: it searches and calls the person's model on their own keys). */
  run: () => Promise<void>;
  /** Edits the data in place -- for a per-item result that does not need a new run. */
  update: (change: (data: T) => T) => void;
}

export function useResearchRun<T>(path: string, fallbacks: { read: string; run: string }): ResearchRunController<T> {
  const [read, setRead] = useState<ResearchRead<T>>({ kind: "loading" });
  const [running, setRunning] = useState(false);
  const [problem, setProblem] = useState<Failure | null>(null);
  // The path a reply belongs to: an answer for an application the page has moved on from is dropped.
  const current = useRef(path);
  const busy = useRef(false);

  const load = useCallback(async () => {
    const asked = path;
    current.current = asked;
    setRead({ kind: "loading" });
    setProblem(null);
    try {
      const data = await apiFetch<T>(asked);
      if (current.current === asked) setRead({ kind: "ready", data });
    } catch (e) {
      if (current.current === asked) setRead({ kind: "failed", message: friendlyApiMessage(e, fallbacks.read) });
    }
  }, [path, fallbacks.read]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    const asked = path;
    setRunning(true);
    setProblem(null);
    try {
      const data = await apiFetch<T>(asked, { method: "POST" });
      if (current.current === asked) setRead({ kind: "ready", data });
    } catch (e) {
      if (current.current === asked) setProblem(failureOf(e, fallbacks.run));
    } finally {
      busy.current = false;
      setRunning(false);
    }
  }, [path, fallbacks.run]);

  const update = useCallback((change: (data: T) => T) => {
    setRead((previous) => (previous.kind === "ready" ? { kind: "ready", data: change(previous.data) } : previous));
  }, []);

  return { read, running, problem, load, run, update };
}
