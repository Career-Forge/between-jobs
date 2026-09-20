// Hiring Signals P4 -- the requests the standalone "Hiring signals" tab makes,
// as plain async functions.
//
// Same shape and same reasons as hiringSignalsClient.ts (P3), and it REUSES that
// module's machinery rather than repeating it: the `Fetcher` and `Result` types,
// the never-throws wrapper `attempt`, the fixed "unreadable reply" failure, the
// save request body and the delete of a saved post (a saved post is one row in
// one table whichever page created it, so `removeSavedPost` is the same call).
// What is new is only what the tab needs: the company-less search, the
// STANDALONE saves list (rows with no application), and saved searches.
//
// What goes over the wire is built in one place so a test can assert exactly
// which keys it has: a search sends the role, location, window and -- only when
// the person chose one -- the wording; a save sends the numeric activity id and
// the search's label and NOTHING else (no url, author, title or text ever leaves
// the browser for the server to store); a saved search sends the person's own
// typed role and location and nothing else.
//
// Every request ends in one of three results -- ok, failed (a classified,
// displayable failure) or disabled (FEATURE_DISABLED) -- and never throws. The
// fetcher is passed in (in the app it is `apiFetch`) because api.ts pulls in the
// Supabase client, which throws at import time without env vars.
//
// Nothing here contacts LinkedIn or any provider: these are calls to this app's
// own server, which is the only thing that ever queries a search index.

import { UNREACHABLE_MESSAGE } from "./hiringSignals";
import {
  type Fetcher,
  type RemoveOutcome,
  type Result,
  UNREADABLE,
  attempt,
  saveRequestBody,
} from "./hiringSignalsClient";
import {
  SAVED_POSTS_LOAD_MESSAGE,
  SAVED_SEARCHES_LOAD_MESSAGE,
  type TabSearchRequest,
  savedSearchBody,
  searchRequestBody,
} from "./hiringSignalsTab";
import {
  type SavedHiringSearch,
  type TabSearchOutcome,
  parseSavedHiringSearch,
  parseSavedSearchesResponse,
  parseTabSearchResponse,
} from "./hiringSignalsTabTypes";
import { type SavedPost, parseSavedPost, parseSavesResponse } from "./hiringSignalsTypes";

export const SEARCH_PATH = "/hiring-signals/search";
export const SAVES_PATH = "/hiring-signals/saves";
export const SEARCHES_PATH = "/hiring-signals/searches";

// The id comes from a server reply, so it is encoded like any other path
// segment; a uuid passes through unchanged.
export function savedSearchPath(searchId: string): string {
  return `${SEARCHES_PATH}/${encodeURIComponent(searchId)}`;
}

// POST /hiring-signals/search. The server queries the search-index provider the
// person connected and returns structured signals; `request.freshness` is also
// the fallback if the reply's own echo of the window is unusable.
export function searchHiringTab(
  fetcher: Fetcher,
  request: TabSearchRequest,
): Promise<Result<TabSearchOutcome>> {
  return attempt(UNREACHABLE_MESSAGE, async () => {
    const raw = await fetcher<unknown>(SEARCH_PATH, {
      method: "POST",
      body: JSON.stringify(searchRequestBody(request)),
    });
    const outcome = parseTabSearchResponse(raw, request.freshness);
    return outcome === null
      ? { kind: "failed", failure: UNREADABLE }
      : { kind: "ok", value: outcome };
  });
}

// GET /hiring-signals/saves -- ONLY the posts saved from this tab (no
// application), newest first. The per-application list is a different route.
export function loadStandaloneSaves(fetcher: Fetcher): Promise<Result<SavedPost[]>> {
  return attempt(SAVED_POSTS_LOAD_MESSAGE, async () => {
    const raw = await fetcher<unknown>(SAVES_PATH);
    const saves = parseSavesResponse(raw);
    return saves === null ? { kind: "failed", failure: UNREADABLE } : { kind: "ok", value: saves };
  });
}

// POST /hiring-signals/saves. Idempotent on the server: saving an already-saved
// post answers with the existing row, so a repeat is the same result, not an
// error.
export function saveStandalonePost(
  fetcher: Fetcher,
  activityId: string,
  queryLabel: string,
): Promise<Result<SavedPost>> {
  return attempt(UNREACHABLE_MESSAGE, async () => {
    const raw = await fetcher<unknown>(SAVES_PATH, {
      method: "POST",
      body: JSON.stringify(saveRequestBody(activityId, queryLabel)),
    });
    const saved = parseSavedPost(raw);
    return saved === null ? { kind: "failed", failure: UNREADABLE } : { kind: "ok", value: saved };
  });
}

// GET /hiring-signals/searches -- this person's saved searches, newest first.
export function loadSavedSearches(fetcher: Fetcher): Promise<Result<SavedHiringSearch[]>> {
  return attempt(SAVED_SEARCHES_LOAD_MESSAGE, async () => {
    const raw = await fetcher<unknown>(SEARCHES_PATH);
    const searches = parseSavedSearchesResponse(raw);
    return searches === null
      ? { kind: "failed", failure: UNREADABLE }
      : { kind: "ok", value: searches };
  });
}

// POST /hiring-signals/searches. 201 when created, 200 with the existing row when
// an equivalent search is already saved. `apiFetch` returns only the body, so the
// two cannot be told apart here; the model tells them apart by whether the
// returned id was already in its own list. A refusal (the 25-search cap, a bad
// value) comes back as a `failed` result carrying the server's own message.
export function createSavedSearch(
  fetcher: Fetcher,
  request: TabSearchRequest,
): Promise<Result<SavedHiringSearch>> {
  return attempt(UNREACHABLE_MESSAGE, async () => {
    const raw = await fetcher<unknown>(SEARCHES_PATH, {
      method: "POST",
      body: JSON.stringify(savedSearchBody(request)),
    });
    const saved = parseSavedHiringSearch(raw);
    return saved === null ? { kind: "failed", failure: UNREADABLE } : { kind: "ok", value: saved };
  });
}

// DELETE /hiring-signals/searches/{id}. "already_gone" = the server had no such
// search (deleted in another tab, say): the state the person asked for, so a
// success -- one the page does not announce as its own doing.
export async function removeSavedSearch(
  fetcher: Fetcher,
  searchId: string,
): Promise<Result<RemoveOutcome>> {
  const result = await attempt<RemoveOutcome>(UNREACHABLE_MESSAGE, async () => {
    await fetcher<void>(savedSearchPath(searchId), { method: "DELETE" });
    return { kind: "ok", value: "removed" };
  });
  if (result.kind === "failed" && result.failure.kind === "not_found") {
    return { kind: "ok", value: "already_gone" };
  }
  return result;
}
