import { useEffect, useState } from "react";
import { apiFetch } from "./api";
import { type CapabilitiesState, loadCapabilities } from "./capabilities";

// The one reader of GET /capabilities bound to React. A definite answer is kept
// for the session (the flags are server settings, not something a person toggles
// mid-visit); a failed ask is not, so the next page to mount asks again. The
// reading and deciding is capabilities.ts; this is only the wiring, and the only
// place that imports the API client, so that module stays loadable from a test.

let known: CapabilitiesState | null = null;

export function useCapabilities(): CapabilitiesState {
  const [state, setState] = useState<CapabilitiesState>(known ?? { kind: "checking" });

  useEffect(() => {
    if (known !== null) return;
    let cancelled = false;
    void loadCapabilities(apiFetch).then((next) => {
      if (next.kind === "ready") known = next;
      if (!cancelled) setState(next);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
