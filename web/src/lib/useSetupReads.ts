import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./api";
import { readSetup, SETUP_LOADING, type SetupReads } from "./setupReads";
import { formatTrailTime } from "./trail";

// The profile and credentials reads (lib/setupReads.ts) bound to React: made once when the page
// opens, and again when `retry` is called (the band's "Try again" on a failed setup check). While a
// retry is out the reads go back to loading, so the band never shows the old failure beside a new ask.

export interface SetupState {
  reads: SetupReads;
  /** The client time the reads came back ("09:14"), for the failure line of a read that failed. */
  at: string;
  retry: () => void;
}

export function useSetupReads(): SetupState {
  const [state, setState] = useState<{ reads: SetupReads; at: string }>({ reads: SETUP_LOADING, at: "" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState((current) => (current.reads === SETUP_LOADING ? current : { reads: SETUP_LOADING, at: "" }));
    void readSetup(apiFetch).then((reads) => {
      if (!cancelled) setState({ reads, at: formatTrailTime(new Date().toISOString()) ?? "" });
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return { reads: state.reads, at: state.at, retry };
}
