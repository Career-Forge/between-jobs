import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../api";
import { formatTrailTime } from "../trail";
import type { ProfileFacts, SetupCheck } from "../nextStep";
import { parseCredentialRows, type CredentialSummary } from "./credentials";

// What the Settings page reads about the account: the saved credentials (the page's own data) and
// whether there is an active profile (which the next-step band needs: without one, the band sends
// the person to their profile first; a waiting import is known only in the session that made it, so
// it is never claimed here). Each is loading, failed (with the call and the time, for the
// band's why line) or ready.
//
// A re-read after a change never goes back to "loading": the rows stay where they are while it is
// out. If the re-read itself fails, the rows stay and `refreshFailed` says they may be out of date,
// so a key that was just saved is not shown as missing.

export interface SettingsData {
  credentials: SetupCheck<CredentialSummary[]>;
  profile: SetupCheck<ProfileFacts>;
  /** A re-read after a change failed: what is shown may be out of date. */
  refreshFailed: boolean;
  /** Re-reads the credentials only; resolves when it has settled. */
  reloadCredentials: () => Promise<void>;
  /** Re-reads everything (the band's "Try again"). */
  reloadAll: () => Promise<void>;
}

function failedAt(call: string): { state: "failed"; call: string; at: string } {
  return { state: "failed", call, at: formatTrailTime(new Date().toISOString()) ?? "" };
}

function statusOf(error: unknown): number | null {
  const status = error instanceof Error ? (error as { status?: unknown }).status : undefined;
  return typeof status === "number" ? status : null;
}

export function useSettingsData(): SettingsData {
  const [credentials, setCredentials] = useState<SetupCheck<CredentialSummary[]>>({ state: "loading" });
  const [profile, setProfile] = useState<SetupCheck<ProfileFacts>>({ state: "loading" });
  const [refreshFailed, setRefreshFailed] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const reloadCredentials = useCallback(async () => {
    try {
      const rows = parseCredentialRows(await apiFetch<unknown>("/credentials"));
      if (rows === null) throw new Error("not a list");
      if (!alive.current) return;
      setCredentials({ state: "ready", value: rows });
      setRefreshFailed(false);
    } catch {
      if (!alive.current) return;
      // Rows already on the page stay; only a first read that fails leaves the page without them.
      setCredentials((current) => (current.state === "ready" ? current : failedAt("GET /credentials")));
      setRefreshFailed(true);
    }
  }, []);

  const reloadProfile = useCallback(async () => {
    try {
      await apiFetch<unknown>("/profile/current");
      if (alive.current) setProfile({ state: "ready", value: { active: true, importWaiting: false } });
    } catch (e) {
      if (!alive.current) return;
      // "No active profile yet" is a 404, and is the answer, not a failure.
      setProfile(statusOf(e) === 404 ? { state: "ready", value: { active: false, importWaiting: false } } : failedAt("GET /profile/current"));
    }
  }, []);

  const reloadAll = useCallback(async () => {
    setCredentials((current) => (current.state === "failed" ? { state: "loading" } : current));
    setProfile((current) => (current.state === "failed" ? { state: "loading" } : current));
    await Promise.all([reloadCredentials(), reloadProfile()]);
  }, [reloadCredentials, reloadProfile]);

  useEffect(() => {
    void reloadAll();
  }, [reloadAll]);

  return { credentials, profile, refreshFailed, reloadCredentials, reloadAll };
}
