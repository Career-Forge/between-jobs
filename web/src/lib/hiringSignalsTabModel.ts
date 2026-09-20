// Hiring Signals P4 -- the standalone "Hiring signals" tab's state machine, with
// no React and no network in it.
//
// WHY THIS IS NOT INSIDE THE COMPONENT. Same reason as P3's panel model
// (hiringSignalsPanelModel.ts): the page's interesting behavior is how it
// behaves when things overlap, and the web package has no DOM environment, so
// none of it could be tested from inside a component. Here the guards are plain
// synchronous code around an injected fetcher, so a test can hold a request open,
// fire another, and assert what the state and the wire did.
//
// The component subscribes with `useSyncExternalStore`; `getState()` returns an
// immutable snapshot that is replaced (never mutated) on every change.
//
// WHAT IS DIFFERENT FROM P3. The per-application panel has a fixed query, so one
// search at a time was enough: a second press was simply ignored. Here the query
// is free text, and ignoring a press while a slow search runs would strand a
// person who just noticed a typo. So:
//   - LATEST WINS. Every search takes a sequence number; a reply that arrives
//     for an older one is discarded without touching the state, so a slow first
//     search can never overwrite a newer one. (FEATURE_DISABLED is the one
//     exception: that is a fact about the server, not about a search, so it
//     applies whichever reply carries it.)
//   - A DOUBLE PRESS IS NOT A NEW SEARCH. Pressing Search (or Enter) twice for
//     the same words sends ONE request -- the second is recognized as identical
//     to the one in flight and ignored. Searches spend the person's own provider
//     credit, so an accidental double click must not spend it twice. A DIFFERENT
//     request while one is in flight is sent, and supersedes it.
//   - The form lives here, not in the component, so "what is in the boxes" and
//     "what would be sent" and "is it already saved" are one source of truth, and
//     a re-run (a saved search, a widened window, a retry) writes the request it
//     ran back into the boxes: what the results say was searched is always what
//     the inputs show.
//
// The rest are P3's guards, reproduced for this page's three lists:
//   - one save per post at a time, and no save of a post already saved;
//   - the saved lists' refresh vs a save or remove that lands while it is in
//     flight: an epoch per list. A refresh started before a mutation carries an
//     older epoch, and when it returns it is DISCARDED and taken again, so a stale
//     list can never overwrite a newer result;
//   - a switched-off feature is one-way: once any request answers
//     FEATURE_DISABLED the page stays on its "switched off" note.
//
// FOCUS. A disabled button drops keyboard focus, so busy controls are
// `aria-disabled` (the handlers below are the guard). For the cases where the
// focused control disappears anyway -- an Unsave or Remove that removes its own
// button, a deleted saved search, a "Search the last 7 days" or "Try again" button
// that lives in results the next search replaces -- or where focus should go to a
// field (an invalid form), the model records where focus should go
// (`focusRequest`); the component moves it and clears the request.

import {
  removeSave,
  savedCardKey,
  searchCardKey,
  upsertSave,
  withoutSearchKeys,
} from "./hiringSignals";
import { type Fetcher, removeSavedPost } from "./hiringSignalsClient";
import { saveButtonId, without, withEntry, withoutKey } from "./hiringSignalsPanelModel";
import {
  ALREADY_SAVED_REASON,
  type FieldErrors,
  INITIAL_FORM,
  NOTICE_POST_REMOVED,
  NOTICE_POST_SAVED,
  NOTICE_SEARCH_ALREADY_SAVED,
  NOTICE_SEARCH_REMOVED,
  NOTICE_SEARCH_SAVED,
  NO_FIELD_ERRORS,
  type TabForm,
  type TabSearchRequest,
  checkForm,
  findEquivalentSearch,
  formFromRequest,
  sameRequest,
  tabFailureMessage,
  withoutSearchErrors,
} from "./hiringSignalsTab";
import {
  createSavedSearch,
  loadSavedSearches,
  loadStandaloneSaves,
  removeSavedSearch,
  saveStandalonePost,
  searchHiringTab,
} from "./hiringSignalsTabClient";
import type {
  LocaleChoice,
  SavedHiringSearch,
  TabSearchOutcome,
  TabSignal,
} from "./hiringSignalsTabTypes";
import type { Failure } from "./hiringSignals";
import type { Freshness, SavedPost } from "./hiringSignalsTypes";

export type TabSearchState =
  | { kind: "idle" }
  | { kind: "loading"; request: TabSearchRequest }
  // `request` is the search that PRODUCED this result. It is what "widen the
  // window" re-runs, so that button always means "the same search, longer" even
  // if the boxes were edited after the results arrived.
  | { kind: "ready"; outcome: TabSearchOutcome; request: TabSearchRequest }
  | { kind: "failed"; failure: Failure; request: TabSearchRequest };

export type LoadStatus = "loading" | "ready" | "error";

// Where keyboard focus should go after the control that had it went away.
export type TabFocusTarget =
  | { kind: "query_input" }
  | { kind: "location_input" }
  | { kind: "search_button" }
  | { kind: "save_button"; activityId: string }
  | { kind: "saved_posts_heading" }
  | { kind: "saved_searches_heading" };

export interface TabFocusRequest {
  id: number;
  target: TabFocusTarget;
}

// A status message for the polite live region. Every notice has its own `id`, so
// an identical message twice in a row is still a NEW node (a live region only
// announces a change, and the same text in the same node is none).
export interface TabNotice {
  id: number;
  text: string;
}

export interface TabState {
  disabled: boolean;
  form: TabForm;
  fieldErrors: FieldErrors;
  search: TabSearchState;
  // Standalone saves (posts saved from this tab; no application).
  saves: SavedPost[];
  savesStatus: LoadStatus;
  savesError: string | null;
  savingIds: ReadonlySet<string>;
  removingIds: ReadonlySet<string>;
  // Saved searches (a role and a location; nothing else).
  searches: SavedHiringSearch[];
  searchesStatus: LoadStatus;
  searchesError: string | null;
  // The server's own message for a refused "Save this search" (the 25-search cap
  // arrives here), shown under that button exactly as it was sent.
  saveSearchError: string | null;
  // The same for a failed delete, shown in the saved-searches list.
  deleteSearchError: string | null;
  savingSearch: boolean;
  deletingSearchIds: ReadonlySet<string>;
  openKeys: ReadonlySet<string>;
  cardErrors: Readonly<Record<string, string>>;
  notice: TabNotice | null;
  focusRequest: TabFocusRequest | null;
}

export function initialTabState(overrides: Partial<TabState> = {}): TabState {
  return {
    disabled: false,
    form: INITIAL_FORM,
    fieldErrors: NO_FIELD_ERRORS,
    search: { kind: "idle" },
    saves: [],
    savesStatus: "loading",
    savesError: null,
    savingIds: new Set(),
    removingIds: new Set(),
    searches: [],
    searchesStatus: "loading",
    searchesError: null,
    saveSearchError: null,
    deleteSearchError: null,
    savingSearch: false,
    deletingSearchIds: new Set(),
    openKeys: new Set(),
    cardErrors: {},
    notice: null,
    focusRequest: null,
    ...overrides,
  };
}

// The element ids the view gives the controls focus can be sent to, and the ones
// its fields point at with aria-describedby. Prefixed `hst-` so they can never
// collide with the per-application panel's `hs-` ids.
export const TAB_QUERY_INPUT_ID = "hst-query";
export const TAB_LOCATION_INPUT_ID = "hst-location";
export const TAB_QUERY_ERROR_ID = "hst-query-error";
export const TAB_LOCATION_ERROR_ID = "hst-location-error";
export const TAB_SEARCH_BUTTON_ID = "hst-search-button";
export const TAB_SAVE_SEARCH_BUTTON_ID = "hst-save-search";
export const TAB_SAVE_SEARCH_REASON_ID = "hst-save-search-reason";
export const TAB_SAVED_POSTS_HEADING_ID = "hst-saved-posts-heading";
export const TAB_SAVED_SEARCHES_HEADING_ID = "hst-saved-searches-heading";

// The result cards are P3's SignalCard, whose Save button carries the id P3's
// `saveButtonId` builds. This is that function, not a second spelling of it: a
// literal here would drift from the card's id silently, and a focus request for an
// id that no element has does nothing and says nothing.
export function tabSaveButtonId(activityId: string): string {
  return saveButtonId(activityId);
}

export function tabFocusTargetId(target: TabFocusTarget): string {
  switch (target.kind) {
    case "query_input":
      return TAB_QUERY_INPUT_ID;
    case "location_input":
      return TAB_LOCATION_INPUT_ID;
    case "search_button":
      return TAB_SEARCH_BUTTON_ID;
    case "save_button":
      return tabSaveButtonId(target.activityId);
    case "saved_posts_heading":
      return TAB_SAVED_POSTS_HEADING_ID;
    case "saved_searches_heading":
      return TAB_SAVED_SEARCHES_HEADING_ID;
  }
}

// A repeat of a saved search the server answered with (200, the existing row) is
// replaced in place; otherwise the new one goes on top, newest first.
function upsertSearch(
  searches: readonly SavedHiringSearch[],
  saved: SavedHiringSearch,
): SavedHiringSearch[] {
  const index = searches.findIndex((s) => s.id === saved.id);
  if (index === -1) return [saved, ...searches];
  const next = searches.slice();
  next[index] = saved;
  return next;
}

export class HiringTabModel {
  private state: TabState = initialTabState();
  private readonly listeners = new Set<() => void>();
  // The newest search's number, and the one currently awaiting a reply (if any).
  private searchSeq = 0;
  private inFlight: { seq: number; request: TabSearchRequest } | null = null;
  private readonly saving = new Set<string>();
  private readonly removing = new Set<string>();
  private readonly deletingSearches = new Set<string>();
  private savesEpoch = 0;
  private searchesEpoch = 0;
  private counter = 0;

  constructor(private readonly fetcher: Fetcher) {}

  // Arrow properties, so they can be handed to `useSyncExternalStore` unbound.
  getState = (): TabState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private set(patch: Partial<TabState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  private nextId(): number {
    this.counter += 1;
    return this.counter;
  }

  private notice(text: string): TabNotice {
    return { id: this.nextId(), text };
  }

  private focus(target: TabFocusTarget): TabFocusRequest {
    return { id: this.nextId(), target };
  }

  // The component calls this once it has moved focus.
  consumeFocusRequest = (id: number): void => {
    if (this.state.focusRequest?.id === id) this.set({ focusRequest: null });
  };

  // ── the form ─────────────────────────────────────────────────────────────

  // Typing clears that field's own error and the "could not save this search"
  // message (both are about what WAS in the boxes), and nothing else.
  setQuery = (query: string): void => {
    this.set({
      form: { ...this.state.form, query },
      fieldErrors: { ...this.state.fieldErrors, query: null },
      saveSearchError: null,
    });
  };

  setLocation = (location: string): void => {
    this.set({
      form: { ...this.state.form, location },
      fieldErrors: { ...this.state.fieldErrors, location: null },
      saveSearchError: null,
    });
  };

  // Changing the window or the wording does not search by itself: a search
  // spends the person's provider credit, so it runs only when they ask.
  setFreshness = (freshness: Freshness): void => {
    this.set({ form: { ...this.state.form, freshness } });
  };

  setLocale = (locale: LocaleChoice): void => {
    this.set({ form: { ...this.state.form, locale } });
  };

  // ── search ───────────────────────────────────────────────────────────────

  // Search with what is in the boxes. An invalid form sends nothing: the error
  // is shown next to the field and focus goes to it.
  search = (): Promise<void> => {
    const checked = checkForm(this.state.form);
    if (!checked.ok) {
      this.set({
        fieldErrors: checked.errors,
        focusRequest: this.focus(
          checked.errors.query !== null ? { kind: "query_input" } : { kind: "location_input" },
        ),
      });
      return Promise.resolve();
    }
    return this.execute(checked.request);
  };

  // Re-run a saved search: its role and location go into the boxes and it runs
  // at once. A saved search does not remember a wording, so that goes back to
  // Auto (derived from THIS location) rather than carrying over whatever the last
  // manual search chose; the window stays as the person has it.
  runSavedSearch = (saved: SavedHiringSearch): Promise<void> => {
    this.set({
      form: {
        ...this.state.form,
        query: saved.query,
        location: saved.location ?? "",
        locale: "auto",
      },
      fieldErrors: NO_FIELD_ERRORS,
      saveSearchError: null,
    });
    return this.search();
  };

  // The empty state's "Search the last N days": the search the results came
  // from, with a wider window. The button that was pressed lives in the results,
  // which are replaced while the search runs, so focus goes to the Search button
  // rather than falling to the top of the page.
  widen = (freshness: Freshness): Promise<void> => {
    const current = this.state.search;
    if (current.kind !== "ready") return Promise.resolve();
    return this.execute({ ...current.request, freshness }, { kind: "search_button" });
  };

  // "Try again" after a failure: the same search that failed. Its button, too, is
  // replaced while the search runs.
  retry = (): Promise<void> => {
    const current = this.state.search;
    if (current.kind !== "failed") return Promise.resolve();
    return this.execute(current.request, { kind: "search_button" });
  };

  // `refocus`: where focus should go because the control that started this search
  // is about to be unmounted (see the FOCUS note above).
  private execute = async (
    request: TabSearchRequest,
    refocus: TabFocusTarget | null = null,
  ): Promise<void> => {
    const flying = this.inFlight;
    // The same words already on their way: a double press, not a new search.
    if (flying !== null && sameRequest(flying.request, request)) return;

    this.searchSeq += 1;
    const seq = this.searchSeq;
    this.inFlight = { seq, request };
    this.set({
      form: formFromRequest(request),
      fieldErrors: NO_FIELD_ERRORS,
      saveSearchError: null,
      search: { kind: "loading", request },
      openKeys: withoutSearchKeys(this.state.openKeys),
      cardErrors: withoutSearchErrors(this.state.cardErrors),
      notice: null,
      ...(refocus === null ? {} : { focusRequest: this.focus(refocus) }),
    });
    try {
      const result = await searchHiringTab(this.fetcher, request);
      if (result.kind === "disabled") {
        this.set({ disabled: true });
        return;
      }
      // A newer search has taken over the page; this reply is history.
      if (seq !== this.searchSeq) return;
      if (result.kind === "failed") {
        this.set({ search: { kind: "failed", failure: result.failure, request } });
        return;
      }
      this.set({ search: { kind: "ready", outcome: result.value, request } });
      // The server's per-signal `saved` flags and the saved list should agree;
      // reload the list so a save made in another tab shows up here too.
      void this.loadSaves();
    } finally {
      if (this.inFlight?.seq === seq) this.inFlight = null;
    }
  };

  toggleEmbed = (key: string): void => {
    const open = this.state.openKeys;
    this.set({ openKeys: open.has(key) ? without(open, key) : withEntry(open, key) });
  };

  // ── saved posts (standalone) ─────────────────────────────────────────────

  loadSaves = async (): Promise<void> => {
    const epoch = this.savesEpoch;
    const result = await loadStandaloneSaves(this.fetcher);
    if (result.kind === "disabled") {
      this.set({ disabled: true });
      return;
    }
    // A save or remove finished while this request was in flight, so this
    // snapshot (a list, or a failure) is older than what the list already shows
    // and must not overwrite it. Take a fresh one instead of just dropping it, so
    // the list never stays stuck on "loading" behind a discarded snapshot.
    if (epoch !== this.savesEpoch) {
      void this.loadSaves();
      return;
    }
    if (result.kind === "failed") {
      this.set({ savesStatus: "error", savesError: tabFailureMessage(result.failure) });
      return;
    }
    this.set({ saves: result.value, savesStatus: "ready", savesError: null });
  };

  saveSignal = async (signal: TabSignal, queryLabel: string): Promise<void> => {
    const id = signal.activity_id;
    if (this.saving.has(id) || this.state.saves.some((s) => s.activity_id === id)) return;
    const key = searchCardKey(id);
    this.saving.add(id);
    this.set({
      savingIds: new Set(this.saving),
      cardErrors: withoutKey(this.state.cardErrors, key),
      notice: null,
    });
    try {
      const result = await saveStandalonePost(this.fetcher, id, queryLabel);
      if (result.kind === "disabled") {
        this.set({ disabled: true });
        return;
      }
      if (result.kind === "failed") {
        this.set({
          cardErrors: { ...this.state.cardErrors, [key]: tabFailureMessage(result.failure) },
        });
        return;
      }
      this.savesEpoch += 1;
      this.set({
        saves: upsertSave(this.state.saves, result.value),
        notice: this.notice(NOTICE_POST_SAVED),
      });
    } finally {
      this.saving.delete(id);
      this.set({ savingIds: new Set(this.saving) });
    }
  };

  // `key` is the card the click came from (searchCardKey or savedCardKey): it
  // says whose error to show, and where focus goes when this card's button is
  // gone.
  removeSaved = async (save: SavedPost, key: string): Promise<void> => {
    if (this.removing.has(save.id)) return;
    this.removing.add(save.id);
    this.set({
      removingIds: new Set(this.removing),
      cardErrors: withoutKey(this.state.cardErrors, key),
      notice: null,
    });
    try {
      const result = await removeSavedPost(this.fetcher, save.id);
      if (result.kind === "disabled") {
        this.set({ disabled: true });
        return;
      }
      if (result.kind === "failed") {
        this.set({
          cardErrors: { ...this.state.cardErrors, [key]: tabFailureMessage(result.failure) },
        });
        return;
      }
      this.savesEpoch += 1;
      const saves = removeSave(this.state.saves, save.id);
      // Already gone (removed in another tab) is the state the person asked for,
      // but not something this click did, so it is not announced as such.
      const notice = result.value === "removed" ? this.notice(NOTICE_POST_REMOVED) : null;
      this.set({
        saves,
        notice,
        focusRequest: this.focus(this.focusAfterRemovingPost(save, key, saves.length)),
      });
    } finally {
      this.removing.delete(save.id);
      this.set({ removingIds: new Set(this.removing) });
    }
  };

  // The control that had focus (Unsave on a result card, Remove on a saved card)
  // is unmounted by the removal. A result card gets its own Save button back; a
  // saved card's neighbors are other cards, so focus goes to the list's heading
  // -- or, with nothing left saved, to the search button.
  private focusAfterRemovingPost(save: SavedPost, key: string, remaining: number): TabFocusTarget {
    if (key === searchCardKey(save.activity_id)) {
      return { kind: "save_button", activityId: save.activity_id };
    }
    if (key === savedCardKey(save.id) && remaining > 0) return { kind: "saved_posts_heading" };
    return { kind: "search_button" };
  }

  // ── saved searches ───────────────────────────────────────────────────────

  loadSearches = async (): Promise<void> => {
    const epoch = this.searchesEpoch;
    const result = await loadSavedSearches(this.fetcher);
    if (result.kind === "disabled") {
      this.set({ disabled: true });
      return;
    }
    if (epoch !== this.searchesEpoch) {
      void this.loadSearches();
      return;
    }
    if (result.kind === "failed") {
      this.set({ searchesStatus: "error", searchesError: tabFailureMessage(result.failure) });
      return;
    }
    this.set({ searches: result.value, searchesStatus: "ready", searchesError: null });
  };

  // Save what is in the boxes as a search. The same two checks as the button's
  // own aria-disabled state (see saveSearchGate), so pressing a button that looks
  // unavailable is answered instead of ignored: an invalid form shows its field
  // error, an equivalent saved search is announced.
  saveCurrentSearch = async (): Promise<void> => {
    if (this.state.savingSearch) return;
    const checked = checkForm(this.state.form);
    if (!checked.ok) {
      this.set({
        fieldErrors: checked.errors,
        focusRequest: this.focus(
          checked.errors.query !== null ? { kind: "query_input" } : { kind: "location_input" },
        ),
      });
      return;
    }
    if (
      findEquivalentSearch(this.state.searches, checked.request.query, checked.request.location) !==
      null
    ) {
      this.set({ notice: this.notice(ALREADY_SAVED_REASON) });
      return;
    }
    this.set({ savingSearch: true, saveSearchError: null, notice: null });
    try {
      const result = await createSavedSearch(this.fetcher, checked.request);
      if (result.kind === "disabled") {
        this.set({ disabled: true });
        return;
      }
      if (result.kind === "failed") {
        // The server's own words (the cap arrives here), not ours.
        this.set({ saveSearchError: tabFailureMessage(result.failure) });
        return;
      }
      this.searchesEpoch += 1;
      // 201 (created) and 200 (already there) look the same to `apiFetch`; the
      // row's id tells them apart. Either way the list gets exactly one entry.
      const existed = this.state.searches.some((s) => s.id === result.value.id);
      this.set({
        searches: upsertSearch(this.state.searches, result.value),
        notice: this.notice(existed ? NOTICE_SEARCH_ALREADY_SAVED : NOTICE_SEARCH_SAVED),
      });
    } finally {
      this.set({ savingSearch: false });
    }
  };

  deleteSearch = async (search: SavedHiringSearch): Promise<void> => {
    if (this.deletingSearches.has(search.id)) return;
    this.deletingSearches.add(search.id);
    this.set({
      deletingSearchIds: new Set(this.deletingSearches),
      deleteSearchError: null,
      notice: null,
    });
    try {
      const result = await removeSavedSearch(this.fetcher, search.id);
      if (result.kind === "disabled") {
        this.set({ disabled: true });
        return;
      }
      if (result.kind === "failed") {
        this.set({ deleteSearchError: tabFailureMessage(result.failure) });
        return;
      }
      this.searchesEpoch += 1;
      const searches = this.state.searches.filter((s) => s.id !== search.id);
      // The deleted entry's own buttons are gone: focus goes to the list's
      // heading, or -- with nothing left saved -- to the role box. A refused save
      // ("keep at most 25") is stale once a search is gone: what it told the person
      // to do is what they just did.
      this.set({
        searches,
        saveSearchError: null,
        notice: result.value === "removed" ? this.notice(NOTICE_SEARCH_REMOVED) : null,
        focusRequest: this.focus(
          searches.length > 0 ? { kind: "saved_searches_heading" } : { kind: "query_input" },
        ),
      });
    } finally {
      this.deletingSearches.delete(search.id);
      this.set({ deletingSearchIds: new Set(this.deletingSearches) });
    }
  };
}
