import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./api";
import { modelKeyOf } from "./modelKey";
import type { CredentialsFacts, ProfileFacts, SetupCheck } from "./nextStep";
import { formatTrailTime } from "./trail";

// The two reads the next-step band's global rows need on a page that has no setup data of its own:
// whether an active profile exists (G2) and what the saved keys say about a usable model (G3). The
// Applications page and the Practice pointer page both ask, once on arrival and again on "Try again".
// Each answers loading, failed (with the call and the client time, for the band's why line) or ready.
//
// A 404 from GET /profile/current is the answer "no active profile yet", not a failure. A waiting
// import is known only in the session that made it, and neither page is that session, so it is
// never claimed here.

export interface SetupChecks {
  profile: SetupCheck<ProfileFacts>;
  credentials: SetupCheck<CredentialsFacts>;
}

export const SETUP_STILL_LOADING: SetupChecks = {
  profile: { state: "loading" },
  credentials: { state: "loading" },
};

type Fetcher = <T>(path: string) => Promise<T>;

function statusOf(error: unknown): number | null {
  const status = error instanceof Error ? (error as { status?: unknown }).status : undefined;
  return typeof status === "number" ? status : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Both reads, each failing on its own. `at` is the client time a failure is stamped with.
export async function readSetupChecks(fetcher: Fetcher, now: () => Date = () => new Date()): Promise<SetupChecks> {
  const failed = (call: string) => ({ state: "failed" as const, call, at: formatTrailTime(now().toISOString()) ?? "" });
  const [profile, credentials] = await Promise.all([
    (async (): Promise<SetupChecks["profile"]> => {
      try {
        const body = await fetcher<unknown>("/profile/current");
        if (!isRecord(body)) return failed("GET /profile/current");
        return { state: "ready", value: { active: true, importWaiting: false } };
      } catch (error) {
        return statusOf(error) === 404 ? { state: "ready", value: { active: false, importWaiting: false } } : failed("GET /profile/current");
      }
    })(),
    (async (): Promise<SetupChecks["credentials"]> => {
      try {
        const body = await fetcher<unknown>("/credentials");
        if (!Array.isArray(body)) return failed("GET /credentials");
        return { state: "ready", value: { modelKey: modelKeyOf(body.filter(isRecord)) } };
      } catch {
        return failed("GET /credentials");
      }
    })(),
  ]);
  return { profile, credentials };
}

export function useSetupChecks(): { checks: SetupChecks; retry: () => void } {
  const [checks, setChecks] = useState<SetupChecks>(SETUP_STILL_LOADING);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setChecks((current) => (current === SETUP_STILL_LOADING ? current : SETUP_STILL_LOADING));
    void readSetupChecks(apiFetch).then((next) => {
      if (!cancelled) setChecks(next);
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((current) => current + 1), []);
  return { checks, retry };
}
