// The two reads a page that needs setup makes before it says anything about it: is there an active
// profile, and which keys are saved. Discover and Hiring signals both start from them, so the next-step
// band knows what is missing before any request that would need it is made. Both are plain GETs the
// app already makes elsewhere (GET /profile/current, GET /credentials).
//
// Each read is loading, failed or answered, and a failure is never read as "missing": a call that
// errored leaves the setup unknown, and the band says it could not check (global row G1).
//
// Pure module: the fetcher is injected (api.ts pulls in the Supabase client, which throws at import
// time without env vars), and there is no React here -- the hook is lib/useSetupReads.ts.

import type { CredentialsFacts, ProfileFacts, SetupCheck } from "./nextStep";
import { modelKeyOf } from "./modelKey";

export type Read<T> = { kind: "loading" } | { kind: "ok"; value: T } | { kind: "failed" };

export interface SetupReads {
  /**
   * `active` false is the 404 ("no active profile yet"), which is an answer, not a failure. "skipped"
   * is a page that does not need a profile (Hiring signals) and so never asks for one.
   */
  profile: Read<{ active: boolean }> | { kind: "skipped" };
  credentials: Read<readonly Record<string, unknown>[]>;
}

export const SETUP_LOADING: SetupReads = { profile: { kind: "loading" }, credentials: { kind: "loading" } };

export type Fetcher = <T>(path: string) => Promise<T>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function statusOf(error: unknown): number | null {
  const status = error instanceof Error ? (error as { status?: unknown }).status : undefined;
  return typeof status === "number" ? status : null;
}

export async function readSetup(fetcher: Fetcher, options: { profile: boolean } = { profile: true }): Promise<SetupReads> {
  const [profile, credentials] = await Promise.all([
    (async (): Promise<SetupReads["profile"]> => {
      if (!options.profile) return { kind: "skipped" };
      try {
        const body = await fetcher<unknown>("/profile/current");
        return isRecord(body) ? { kind: "ok", value: { active: true } } : { kind: "failed" };
      } catch (e) {
        return statusOf(e) === 404 ? { kind: "ok", value: { active: false } } : { kind: "failed" };
      }
    })(),
    (async (): Promise<SetupReads["credentials"]> => {
      try {
        const body = await fetcher<unknown>("/credentials");
        return Array.isArray(body) ? { kind: "ok", value: body.filter(isRecord) } : { kind: "failed" };
      } catch {
        return { kind: "failed" };
      }
    })(),
  ]);
  return { profile, credentials };
}

// The four providers a search can use, which is what both pages mean by "a search key" (the other
// search-section keys on Settings, Apollo, Hunter and Exa, look people up and do not run a search).
export const SEARCH_KEY_PROVIDERS: readonly string[] = ["brave", "serper", "firecrawl", "you_com"];

export function searchKeyCount(rows: readonly Record<string, unknown>[]): number {
  const connected = new Set<string>();
  for (const row of rows) {
    if (row.service === "search" && typeof row.provider === "string" && SEARCH_KEY_PROVIDERS.includes(row.provider)) {
      connected.add(row.provider);
    }
  }
  return connected.size;
}

// The reads as the band's setup checks. `at` is the client time the reads came back, for the failure
// line of a read that failed. The credentials check carries the model-key reading only when `modelKey`
// is asked for: a page that says the same thing in its own rows (Discover's D1) leaves it out, so the
// global row that asks for a key never speaks for it, and one that needs no model key (Hiring signals)
// is never told to add one.
export function setupChecks(
  reads: SetupReads,
  at: string,
  options: { modelKey: boolean } = { modelKey: false },
): { profile: SetupCheck<ProfileFacts> | undefined; credentials: SetupCheck<CredentialsFacts> } {
  const profile: SetupCheck<ProfileFacts> | undefined =
    reads.profile.kind === "skipped"
      ? undefined
      : reads.profile.kind === "loading"
      ? { state: "loading" }
      : reads.profile.kind === "failed"
        ? { state: "failed", call: "GET /profile/current", at }
        // A waiting import is known only in the session that made it, and this is not that page.
        : { state: "ready", value: { active: reads.profile.value.active, importWaiting: false } };
  const credentials: SetupCheck<CredentialsFacts> =
    reads.credentials.kind === "loading"
      ? { state: "loading" }
      : reads.credentials.kind === "failed"
        ? { state: "failed", call: "GET /credentials", at }
        : { state: "ready", value: options.modelKey ? { modelKey: modelKeyOf(reads.credentials.value) } : {} };
  return { profile, credentials };
}
