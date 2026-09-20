// Hiring Signals P3 -- the "Hiring posts" panel's state machine, with no React
// and no network in it.
//
// WHY THIS IS NOT INSIDE THE COMPONENT. The panel's interesting behavior is not
// what it looks like, it is how it behaves when things overlap: a second click
// while a search is still in flight, two Save clicks on one card, a save that
// finishes while the saved list is still loading, a reply that arrives after
// the person has moved on. Those guards lived in the component (a ref here, a
// counter there) and nothing tested them: the web package has no DOM
// environment, so a test could not click anything, and every one of them could
// be deleted with the whole suite still green (a mutation run confirmed six).
// Here they are plain synchronous code around an injected fetcher, so a test can
// hold a request open, fire another, and assert what the state and the wire
// did.
//
// The component subscribes with `useSyncExternalStore`; `getState()` returns an
// immutable snapshot that is replaced (never mutated) on every change.
//
// THE GUARDS, and where each one is decided:
//   - one search at a time: `searchInFlight`, a plain field read synchronously,
//     so two calls in the same tick send ONE request (React state would not have
//     flushed between them);
//   - one save per post at a time, and no save of a post already saved:
//     `saving` (a synchronous Set) plus the saved list itself;
//   - the saved list refresh vs a save or remove that lands while it is in
//     flight: `savesEpoch`. A refresh started before a mutation carries an older
//     epoch, and when it returns it is DISCARDED and taken again, so a stale list
//     can never overwrite a newer result;
//   - a switched-off feature is one-way: once any request answers
//     FEATURE_DISABLED the panel stays hidden.
//
// FOCUS. A button that is disabled loses keyboard focus, and the person who
// pressed Enter on it lands back at the top of a very long page. The view uses
// `aria-disabled` instead (the handlers below are the guard), and for the one
// case where the focused control disappears anyway -- an Unsave or Remove that
// removes its own button -- the model records where focus should go
// (`focusRequest`); the component moves it and clears the request.

import {
  type Failure,
  failureMessage,
  removeSave,
  savedCardKey,
  searchCardKey,
  upsertSave,
  withoutSearchKeys,
} from "./hiringSignals";
import {
  type Fetcher,
  loadSavedPosts,
  removeSavedPost,
  savePost,
  searchHiringPosts,
} from "./hiringSignalsClient";
import {
  DEFAULT_FRESHNESS,
  type Freshness,
  type HiringSignal,
  type SavedPost,
  type SearchOutcome,
} from "./hiringSignalsTypes";

export type SearchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; outcome: SearchOutcome }
  | { kind: "failed"; failure: Failure; freshness: Freshness };

export type SavesStatus = "loading" | "ready" | "error";

// Where keyboard focus should go after the control that had it went away.
export type FocusTarget =
  | { kind: "search_button" }
  | { kind: "save_button"; activityId: string }
  | { kind: "saved_heading" };

export interface FocusRequest {
  id: number;
  target: FocusTarget;
}

// A status message for the polite live region. Every notice has its own `id`,
// so an identical message twice in a row is still a NEW node (a live region
// only announces a change, and the same text in the same node is none).
export interface Notice {
  id: number;
  text: string;
}

export interface PanelState {
  disabled: boolean;
  freshness: Freshness;
  search: SearchState;
  saves: SavedPost[];
  savesStatus: SavesStatus;
  savesError: string | null;
  savingIds: ReadonlySet<string>;
  removingIds: ReadonlySet<string>;
  openKeys: ReadonlySet<string>;
  cardErrors: Readonly<Record<string, string>>;
  notice: Notice | null;
  focusRequest: FocusRequest | null;
}

export function initialPanelState(overrides: Partial<PanelState> = {}): PanelState {
  return {
    disabled: false,
    freshness: DEFAULT_FRESHNESS,
    search: { kind: "idle" },
    saves: [],
    savesStatus: "loading",
    savesError: null,
    savingIds: new Set(),
    removingIds: new Set(),
    openKeys: new Set(),
    cardErrors: {},
    notice: null,
    focusRequest: null,
    ...overrides,
  };
}

// The element ids the view gives the controls focus can be sent to.
export const SEARCH_BUTTON_ID = "hs-search-button";
export const SAVED_HEADING_ID = "hs-saved-heading";

export function saveButtonId(activityId: string): string {
  return `hs-save-${activityId}`;
}

export function focusTargetId(target: FocusTarget): string {
  switch (target.kind) {
    case "search_button":
      return SEARCH_BUTTON_ID;
    case "save_button":
      return saveButtonId(target.activityId);
    case "saved_heading":
      return SAVED_HEADING_ID;
  }
}

function without<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set);
  next.delete(value);
  return next;
}

function withEntry<T>(set: ReadonlySet<T>, value: T): Set<T> {
  return new Set(set).add(value);
}

function withoutKey(
  record: Readonly<Record<string, string>>,
  key: string,
): Readonly<Record<string, string>> {
  if (!(key in record)) return record;
  const { [key]: _dropped, ...rest } = record;
  return rest;
}

export class HiringPanelModel {
  private state: PanelState = initialPanelState();
  private readonly listeners = new Set<() => void>();
  private searchInFlight = false;
  private readonly saving = new Set<string>();
  private readonly removing = new Set<string>();
  private savesEpoch = 0;
  private counter = 0;

  constructor(
    private readonly fetcher: Fetcher,
    private readonly applicationId: string,
  ) {}

  // Arrow properties, so they can be handed to `useSyncExternalStore` unbound.
  getState = (): PanelState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private set(patch: Partial<PanelState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  private nextId(): number {
    this.counter += 1;
    return this.counter;
  }

  private notice(text: string): Notice {
    return { id: this.nextId(), text };
  }

  private focus(target: FocusTarget): FocusRequest {
    return { id: this.nextId(), target };
  }

  // The component calls this once it has moved focus.
  consumeFocusRequest = (id: number): void => {
    if (this.state.focusRequest?.id === id) this.set({ focusRequest: null });
  };

  // ── saved posts ──────────────────────────────────────────────────────────

  loadSaves = async (): Promise<void> => {
    const epoch = this.savesEpoch;
    const result = await loadSavedPosts(this.fetcher, this.applicationId);
    if (result.kind === "disabled") {
      this.set({ disabled: true });
      return;
    }
    // A save or remove finished while this request was in flight, so this
    // snapshot (a list, or a failure) is older than what the list already
    // shows and must not overwrite it. Take a fresh one instead of just
    // dropping it, so the list never stays stuck on "loading" behind a
    // discarded snapshot (this ends as soon as no save or remove lands
    // mid-request).
    if (epoch !== this.savesEpoch) {
      void this.loadSaves();
      return;
    }
    if (result.kind === "failed") {
      this.set({ savesStatus: "error", savesError: failureMessage(result.failure) });
      return;
    }
    this.set({ saves: result.value, savesStatus: "ready", savesError: null });
  };

  // ── search ───────────────────────────────────────────────────────────────

  // Choosing a window is not allowed while a search is running (the pills look
  // disabled, but stay focusable -- see the module comment).
  setFreshness = (freshness: Freshness): void => {
    if (this.searchInFlight) return;
    this.set({ freshness });
  };

  runSearch = async (requested: Freshness = this.state.freshness): Promise<void> => {
    if (this.searchInFlight) return;
    this.searchInFlight = true;
    this.set({
      freshness: requested,
      search: { kind: "loading" },
      openKeys: withoutSearchKeys(this.state.openKeys),
      cardErrors: {},
      notice: null,
    });
    try {
      const result = await searchHiringPosts(this.fetcher, this.applicationId, requested);
      if (result.kind === "disabled") {
        this.set({ disabled: true });
        return;
      }
      if (result.kind === "failed") {
        this.set({ search: { kind: "failed", failure: result.failure, freshness: requested } });
        return;
      }
      this.set({ search: { kind: "ready", outcome: result.value } });
      // The server's per-signal `saved` flags and the saved list should agree;
      // reload the list so a save made elsewhere shows up here too.
      void this.loadSaves();
    } finally {
      this.searchInFlight = false;
    }
  };

  toggleEmbed = (key: string): void => {
    const open = this.state.openKeys;
    this.set({ openKeys: open.has(key) ? without(open, key) : withEntry(open, key) });
  };

  // ── save / remove ────────────────────────────────────────────────────────

  saveSignal = async (signal: HiringSignal, queryLabel: string): Promise<void> => {
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
      const result = await savePost(this.fetcher, this.applicationId, id, queryLabel);
      if (result.kind === "disabled") {
        this.set({ disabled: true });
        return;
      }
      if (result.kind === "failed") {
        this.set({ cardErrors: { ...this.state.cardErrors, [key]: failureMessage(result.failure) } });
        return;
      }
      this.savesEpoch += 1;
      this.set({
        saves: upsertSave(this.state.saves, result.value),
        notice: this.notice("Saved to this application."),
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
        this.set({ cardErrors: { ...this.state.cardErrors, [key]: failureMessage(result.failure) } });
        return;
      }
      this.savesEpoch += 1;
      const saves = removeSave(this.state.saves, save.id);
      // Already gone (removed in another tab) is the state the user asked for,
      // but not something this click did, so it is not announced as such.
      const notice = result.value === "removed" ? this.notice("Removed from saved posts.") : null;
      this.set({
        saves,
        notice,
        focusRequest: this.focus(this.focusAfterRemoving(save, key, saves.length)),
      });
    } finally {
      this.removing.delete(save.id);
      this.set({ removingIds: new Set(this.removing) });
    }
  };

  // The control that had focus (Unsave on a result card, Remove on a saved card)
  // is unmounted by the removal. A result card gets its own Save button back;
  // a saved card's neighbors are other cards, so focus goes to the list's
  // heading -- or, with nothing left saved, to the search button.
  private focusAfterRemoving(save: SavedPost, key: string, remaining: number): FocusTarget {
    if (key === searchCardKey(save.activity_id)) {
      return { kind: "save_button", activityId: save.activity_id };
    }
    if (key === savedCardKey(save.id) && remaining > 0) return { kind: "saved_heading" };
    return { kind: "search_button" };
  }
}
