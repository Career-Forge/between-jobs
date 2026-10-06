import { createContext, useCallback, useContext, useMemo, useReducer, useRef } from "react";
import { apiFetch } from "./api";
import { deadlineSignal } from "./deadline";
import {
  ENROLLMENT_LOOKUP_TIMEOUT_MS,
  enrollmentLoadReducer,
  initialEnrollmentLoad,
  loadEnrollment,
  type Enrollment,
  type EnrollmentFetcher,
  type EnrollmentLoad,
} from "./enrollment";

// The one holder of "where does this person stand in the tester programme", bound to React. The
// reading and deciding is enrollment.ts; this is only the wiring, and the only place here that
// imports the API client, so that module stays loadable from a test.
//
// ONE ANSWER, SHARED. The gate (components/EnrollmentGate.tsx), the enrollment page and the
// Profile page's line all need the same fact, and a join or a withdrawal must change it for all
// three at once, so it lives in one reducer behind a context rather than in each of them. It is
// asked for lazily: a server that does not require the programme never has the gate ask, and the
// two pages that need it ask when they open.

// The lookup, with a deadline of its own: the gate draws nothing while it is out, so a server that
// hangs must turn into the gate's retry state in a bounded time.
const lookupFetcher: EnrollmentFetcher = <T>(path: string, init?: RequestInit) =>
  apiFetch<T>(path, { ...init, signal: deadlineSignal(ENROLLMENT_LOOKUP_TIMEOUT_MS) });

export interface EnrollmentController {
  load: EnrollmentLoad;
  // Asks, once, if nothing has been asked yet. Safe to call from every mount.
  ensureLoaded: () => void;
  // Asks again, even after a failure (the retry button).
  reload: () => void;
  // Takes what a join or a withdrawal answered with as the new truth.
  replace: (enrollment: Enrollment) => void;
}

export const EnrollmentContext = createContext<EnrollmentController | null>(null);

/** The shared state, or null outside the signed-in shell (nothing above it provides one). */
export function useEnrollmentController(): EnrollmentController | null {
  return useContext(EnrollmentContext);
}

/** Builds the controller. Used once, by the gate, which provides it to everything below. */
export function useEnrollmentState(): EnrollmentController {
  const [load, dispatch] = useReducer(enrollmentLoadReducer, initialEnrollmentLoad);
  // The latest state for `ensureLoaded`, which is called from effects that must not re-run
  // every time the state changes.
  const latest = useRef(load);
  latest.current = load;

  // One ask at a time: the effects that call `ensureLoaded` can run twice before the first
  // answer has changed the state (React's development double-mount, two consumers mounting
  // together), and a retry pressed while an ask is in flight has nothing to add.
  const asking = useRef(false);

  const run = useCallback(async () => {
    if (asking.current) return;
    asking.current = true;
    try {
      dispatch({ type: "load_started" });
      dispatch(await loadEnrollment(lookupFetcher));
    } finally {
      asking.current = false;
    }
  }, []);

  const ensureLoaded = useCallback(() => {
    if (latest.current.kind === "idle") void run();
  }, [run]);

  const reload = useCallback(() => {
    void run();
  }, [run]);

  const replace = useCallback((enrollment: Enrollment) => {
    dispatch({ type: "replaced", enrollment });
  }, []);

  return useMemo(
    () => ({ load, ensureLoaded, reload, replace }),
    [load, ensureLoaded, reload, replace],
  );
}
