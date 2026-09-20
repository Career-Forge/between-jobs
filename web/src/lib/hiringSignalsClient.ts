// Hiring Signals P3 -- the four requests the "Hiring posts" panel makes, as
// plain async functions.
//
// WHY THIS IS NOT INLINE IN THE COMPONENT. Two things about these requests are
// worth pinning with tests, and neither can be pinned from inside a React
// component without a browser:
//
//   1. What goes over the wire. A save sends the numeric activity id and the
//      search's label -- and NOTHING else. No url, author, title or text ever
//      leaves the browser for the server to store; the server rebuilds the post
//      address itself and rejects any extra field. The body is built in one
//      place here so a test can assert exactly which keys it has.
//   2. How each outcome is sorted. Every request ends in one of three results
//      -- ok, failed (with a classified, displayable failure) or disabled
//      (FEATURE_DISABLED: the panel then renders nothing at all) -- and never
//      throws, so the component's handling is one small `switch` instead of a
//      try/catch per call. A reply this page cannot read is a failure with
//      fixed wording, not a crash.
//
// The fetcher is passed in (in the app it is `apiFetch`) rather than imported:
// api.ts pulls in the Supabase client, which throws at import time without env
// vars, and this module must stay loadable from a plain test -- the same
// constraint hiringSignals.ts and hiringSignalsTypes.ts already live under.
//
// Nothing here contacts LinkedIn or any provider. These are calls to this
// app's own server, which is the only thing that ever queries a search index.

import {
  type Failure,
  UNREACHABLE_MESSAGE,
  UNREADABLE_MESSAGE,
  classifyFailure,
} from "./hiringSignals";
import {
  type Freshness,
  type SavedPost,
  type SearchOutcome,
  parseSavedPost,
  parseSavesResponse,
  parseSearchResponse,
} from "./hiringSignalsTypes";

// `apiFetch`'s shape: resolves with the parsed JSON body, undefined for a 204,
// and throws an ApiError (carrying `.status` and `.code`) for a non-2xx reply.
export type Fetcher = <T>(path: string, init?: RequestInit) => Promise<T>;

export type Result<T> =
  | { kind: "ok"; value: T }
  | { kind: "failed"; failure: Failure }
  // FEATURE_DISABLED: the server has this feature switched off.
  | { kind: "disabled" };

export const UNREADABLE: Failure = { kind: "error", message: UNREADABLE_MESSAGE, retryable: true };

// The application id comes from the route, so it is encoded like any other
// path segment; a uuid passes through unchanged.
export function applicationBase(applicationId: string): string {
  return `/applications/${encodeURIComponent(applicationId)}/hiring-signals`;
}

export function savedPostPath(saveId: string): string {
  return `/hiring-signals/saves/${encodeURIComponent(saveId)}`;
}

export async function attempt<T>(
  unreachableMessage: string,
  run: () => Promise<Result<T>>,
): Promise<Result<T>> {
  try {
    return await run();
  } catch (e) {
    const failure = classifyFailure(e, unreachableMessage);
    return failure.kind === "disabled" ? { kind: "disabled" } : { kind: "failed", failure };
  }
}

// POST .../search. The server queries the search-index provider the user
// connected and returns structured signals; `freshness` is the requested
// window (also the fallback if the reply's own echo of it is unusable).
export function searchHiringPosts(
  fetcher: Fetcher,
  applicationId: string,
  freshness: Freshness,
): Promise<Result<SearchOutcome>> {
  return attempt(UNREACHABLE_MESSAGE, async () => {
    const raw = await fetcher<unknown>(`${applicationBase(applicationId)}/search`, {
      method: "POST",
      body: JSON.stringify({ freshness }),
    });
    const outcome = parseSearchResponse(raw, freshness);
    return outcome === null
      ? { kind: "failed", failure: UNREADABLE }
      : { kind: "ok", value: outcome };
  });
}

// GET .../saves -- this user's saved posts for this application, newest first.
export function loadSavedPosts(
  fetcher: Fetcher,
  applicationId: string,
): Promise<Result<SavedPost[]>> {
  return attempt("Could not load your saved posts.", async () => {
    const raw = await fetcher<unknown>(`${applicationBase(applicationId)}/saves`);
    const saves = parseSavesResponse(raw);
    return saves === null ? { kind: "failed", failure: UNREADABLE } : { kind: "ok", value: saves };
  });
}

// The request body of a save: the numeric activity id and the label of the
// search that found the post (null when there is none). Exported so a test can
// assert the exact key set -- adding a field here is a privacy decision, not a
// convenience.
export function saveRequestBody(
  activityId: string,
  queryLabel: string,
): { activity_id: string; query_label: string | null } {
  return { activity_id: activityId, query_label: queryLabel === "" ? null : queryLabel };
}

// POST .../saves. Idempotent on the server: saving an already-saved post
// answers with the existing row, so a repeat is the same result, not an error.
export function savePost(
  fetcher: Fetcher,
  applicationId: string,
  activityId: string,
  queryLabel: string,
): Promise<Result<SavedPost>> {
  return attempt(UNREACHABLE_MESSAGE, async () => {
    const raw = await fetcher<unknown>(`${applicationBase(applicationId)}/saves`, {
      method: "POST",
      body: JSON.stringify(saveRequestBody(activityId, queryLabel)),
    });
    const saved = parseSavedPost(raw);
    return saved === null ? { kind: "failed", failure: UNREADABLE } : { kind: "ok", value: saved };
  });
}

// "already_gone" = the server had no such save (deleted in another tab, say):
// the state the user asked for, so it is a success, just one the panel does not
// announce as its own doing.
export type RemoveOutcome = "removed" | "already_gone";

// DELETE /hiring-signals/saves/{id}.
export async function removeSavedPost(
  fetcher: Fetcher,
  saveId: string,
): Promise<Result<RemoveOutcome>> {
  const result = await attempt<RemoveOutcome>(UNREACHABLE_MESSAGE, async () => {
    await fetcher<void>(savedPostPath(saveId), { method: "DELETE" });
    return { kind: "ok", value: "removed" };
  });
  if (result.kind === "failed" && result.failure.kind === "not_found") {
    return { kind: "ok", value: "already_gone" };
  }
  return result;
}
