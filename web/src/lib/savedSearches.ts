// Pausing and deleting a person's saved searches, as pure logic: what the API's list is read as, the
// requests, and the state of the card that shows them (components/SavedSearchesCard.tsx draws it, on
// the Integrations page).
//
// WHY THIS EXISTS BESIDE DISCOVER, WHICH ALREADY HAS THESE BUTTONS. A saved search runs in the
// background on a schedule, matching and scoring on the person's own AI key, whether or not they
// are enrolled in the tester programme (api/tester_enrollment.py: background work is not gated).
// The Tester Agreement tells someone who withdraws to pause or delete a saved search to stop that,
// and the enrollment gate closes Discover to them. So the controls must also be on a page the gate
// leaves open (lib/enrollment.ts: GATE_EXEMPT_PATHS), and this is that.
//
// The three-state rule applies to the list: loading, failed or known. A failed lookup is shown as
// failed, with a retry, and never as "no saved searches": a search that is still running must not
// look as though it does not exist.
//
// Pure module: the fetcher is injected (api.ts pulls in the Supabase client, which throws at import
// time without env vars) and there is no React here.

import { savedSearchLabel } from "./discover";
import type { SavedSearch } from "./discoverTypes";
import { friendlyApiMessage } from "./rateLimitMessage";

export const SAVED_SEARCHES_PATH = "/saved-searches";

export type SavedSearchesFetcher = <T>(path: string, init?: RequestInit) => Promise<T>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** One search from the API, or null when it is not one. Only the id and whether it is active are
 *  needed to act on it, so a field this app cannot read is replaced by an empty one rather than the
 *  search being dropped: a running search must stay on the list. */
export function parseSavedSearch(body: unknown): SavedSearch | null {
  if (!isRecord(body) || typeof body.id !== "string" || body.id === "") return null;
  if (typeof body.is_active !== "boolean") return null;
  return {
    id: body.id,
    query: typeof body.query === "string" ? body.query : "",
    location: typeof body.location === "string" ? body.location : null,
    companies: Array.isArray(body.companies)
      ? body.companies.filter((company): company is string => typeof company === "string")
      : [],
    remote_only: body.remote_only === true,
    is_active: body.is_active,
    created_at: typeof body.created_at === "string" ? body.created_at : "",
  };
}

/** The list, or null when the body is not a list or has an entry that is not a search (the whole
 *  answer is then unknown, never a shorter list than the server has). */
export function parseSavedSearches(body: unknown): SavedSearch[] | null {
  if (!Array.isArray(body)) return null;
  const searches: SavedSearch[] = [];
  for (const entry of body) {
    const search = parseSavedSearch(entry);
    if (search === null) return null;
    searches.push(search);
  }
  return searches;
}

// ── the card's state ───────────────────────────────────────────────────────

export type SavedSearchesState =
  | { kind: "loading" }
  | { kind: "failed"; message: string }
  | {
      kind: "ready";
      searches: SavedSearch[];
      // The search a pause, resume or delete is out for: its buttons wait, the others do not.
      busyId: string | null;
      // The last action's failure, shown above the list; the list itself is still what is known.
      error: string | null;
    };

export type SavedSearchesEvent =
  | { type: "load_started" }
  | { type: "loaded"; searches: SavedSearch[] }
  | { type: "load_failed"; message: string }
  | { type: "action_started"; id: string }
  | { type: "updated"; search: SavedSearch }
  | { type: "removed"; id: string }
  | { type: "action_failed"; message: string };

export const initialSavedSearchesState: SavedSearchesState = { kind: "loading" };

export function savedSearchesReducer(
  state: SavedSearchesState,
  event: SavedSearchesEvent,
): SavedSearchesState {
  switch (event.type) {
    case "load_started":
      // A retry after a failure goes back to loading; a refresh of a known list changes nothing.
      return state.kind === "failed" ? { kind: "loading" } : state;
    case "loaded":
      return { kind: "ready", searches: event.searches, busyId: null, error: null };
    case "load_failed":
      return state.kind === "ready" ? state : { kind: "failed", message: event.message };
    case "action_started":
      return state.kind === "ready" ? { ...state, busyId: event.id, error: null } : state;
    case "updated":
      return state.kind === "ready"
        ? {
            ...state,
            busyId: null,
            searches: state.searches.map((search) =>
              search.id === event.search.id ? event.search : search,
            ),
          }
        : state;
    case "removed":
      return state.kind === "ready"
        ? {
            ...state,
            busyId: null,
            searches: state.searches.filter((search) => search.id !== event.id),
          }
        : state;
    case "action_failed":
      return state.kind === "ready" ? { ...state, busyId: null, error: event.message } : state;
  }
}

// ── the requests ───────────────────────────────────────────────────────────

const LOAD_FAILED = "We could not load your saved searches.";
const READ_FAILED = "We could not read the server's answer about your saved searches.";
const UPDATE_FAILED = "We could not update that saved search.";
const DELETE_FAILED = "We could not delete that saved search.";

/** Asks for the list. Never throws: a failed ask is an event like any other. */
export async function loadSavedSearches(fetcher: SavedSearchesFetcher): Promise<SavedSearchesEvent> {
  try {
    const searches = parseSavedSearches(await fetcher<unknown>(SAVED_SEARCHES_PATH));
    return searches === null
      ? { type: "load_failed", message: READ_FAILED }
      : { type: "loaded", searches };
  } catch (e) {
    return { type: "load_failed", message: friendlyApiMessage(e, LOAD_FAILED) };
  }
}

/** Pauses (`isActive` false) or resumes one search. */
export async function setSavedSearchActive(
  fetcher: SavedSearchesFetcher,
  id: string,
  isActive: boolean,
): Promise<SavedSearchesEvent> {
  try {
    const search = parseSavedSearch(
      await fetcher<unknown>(`${SAVED_SEARCHES_PATH}/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: isActive }),
      }),
    );
    return search === null
      ? { type: "action_failed", message: READ_FAILED }
      : { type: "updated", search };
  } catch (e) {
    return { type: "action_failed", message: friendlyApiMessage(e, UPDATE_FAILED) };
  }
}

/** Deletes one search. */
export async function deleteSavedSearch(
  fetcher: SavedSearchesFetcher,
  id: string,
): Promise<SavedSearchesEvent> {
  try {
    await fetcher<unknown>(`${SAVED_SEARCHES_PATH}/${encodeURIComponent(id)}`, { method: "DELETE" });
    return { type: "removed", id };
  } catch (e) {
    return { type: "action_failed", message: friendlyApiMessage(e, DELETE_FAILED) };
  }
}

// ── the words ──────────────────────────────────────────────────────────────

export const SAVED_SEARCHES_HEADING = "Saved searches";

// Said once, above the list. What each part rests on: a saved search is matched on a schedule by the
// background job (Discover says "every few hours"), it scores what it finds with the person's own AI
// key, and neither depends on being in the tester programme.
export const SAVED_SEARCHES_NOTE =
  "Active saved searches are checked every few hours in the background, using your AI key to score what they find. Pause one to stop the checks, or delete it.";

/** The sentence for one row's buttons, so each has a name of its own ("Pause" alone, repeated down
 *  the list, tells a screen-reader user nothing). */
export function savedSearchActionLabel(
  action: "pause" | "resume" | "delete",
  search: SavedSearch,
): string {
  const verb = action === "pause" ? "Pause" : action === "resume" ? "Resume" : "Delete";
  return `${verb} the saved search ${savedSearchLabel(search)}`;
}
