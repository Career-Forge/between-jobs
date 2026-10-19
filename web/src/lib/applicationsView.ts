// What an application card says about itself, as pure functions: the shape of its status chip, the
// trail line built from the fields the list returned, and the phone's "Showing status" picker.
//
// THE TRAIL'S OMIT RULE. A card's trail says who acted, on what basis and when, from `source_channel`
// and `created_at` only. A channel this module does not know is left out (never guessed from the
// name), and so is a date that cannot be read. Nothing here says a link was checked, a resume was
// reviewed or a status was confirmed: none of that is returned.

import { ALL_STATUSES, type Application, type ApplicationStatus } from "./applicationsTypes";
import { STATUS_LABELS } from "./applicationsBoard";
import { formatTrailDate, mono, type TrailInput } from "./trail";

// Statuses are neutral outline chips with a shape, so no color alone carries a status. Offer is the
// only emerald one; rejected and withdrawn are muted. Nothing is red: a rejection is not a failure.
export type ChipTone = "neutral" | "emerald" | "muted";

export const STATUS_CHIPS: Readonly<Record<ApplicationStatus, { glyph: string; tone: ChipTone }>> = {
  saved: { glyph: "◇", tone: "neutral" },
  applied: { glyph: "●", tone: "neutral" },
  screening: { glyph: "◔", tone: "neutral" },
  interviewing: { glyph: "◑", tone: "neutral" },
  offer: { glyph: "✓", tone: "emerald" },
  rejected: { glyph: "⊖", tone: "muted" },
  withdrawn: { glyph: "⊘", tone: "muted" },
};

// Where a tracked job came from, by the channel the API stamped on it. Only the channels the API
// writes are named; anything else is not guessed at.
const SOURCE_WORDS: Readonly<Record<string, string>> = {
  discover: "from Discover",
  web: "added on the web",
  telegram: "from Telegram",
  discord: "from Discord",
};

export function sourceWords(channel: unknown): string | null {
  if (typeof channel !== "string" || !Object.prototype.hasOwnProperty.call(SOURCE_WORDS, channel)) return null;
  return SOURCE_WORDS[channel];
}

// "● You tracked this · from Discover · Sep 22". Tracking is always the person's own act (the Track
// button, a pasted posting, a message they sent), so the actor is certain; the other two slots come
// back only when their field did.
export function cardTrail(application: Application, now: Date, timeZone?: string): TrailInput {
  const created = formatTrailDate(application.created_at, { now, timeZone });
  return {
    glyph: "you",
    actor: "You tracked this",
    basis: sourceWords(application.source_channel),
    when: created === null ? null : mono(created),
  };
}

// ── the phone's "Showing status" picker ────────────────────────────────────

// "Saved (2)", "Screening (0)".
export function statusOptionLabel(status: ApplicationStatus, count: number): string {
  return `${STATUS_LABELS[status]} (${count})`;
}

export function isStatus(value: unknown): value is ApplicationStatus {
  return typeof value === "string" && (ALL_STATUSES as readonly string[]).includes(value);
}

// The first status, in pipeline order, that has an application; Saved when nothing is tracked.
export function firstOccupiedStatus(counts: Readonly<Record<ApplicationStatus, number>>): ApplicationStatus {
  return ALL_STATUSES.find((status) => counts[status] > 0) ?? "saved";
}

// The last choice is a per-browser convenience: kept in localStorage, never read back as anything
// but one of the seven statuses, and never required (storage can be missing or throw).
export const PHONE_STATUS_KEY = "between-jobs:applications:phone-status";

export interface StatusStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function readStoredStatus(storage: StatusStorage | null): ApplicationStatus | null {
  try {
    const stored = storage?.getItem(PHONE_STATUS_KEY);
    return isStatus(stored) ? stored : null;
  } catch {
    return null;
  }
}

export function storeStatus(storage: StatusStorage | null, status: ApplicationStatus): void {
  try {
    storage?.setItem(PHONE_STATUS_KEY, status);
  } catch {
    // A convenience only: the picker works the same without it.
  }
}

// The status the phone list opens on: the one remembered, else the first with an application.
export function phoneStatus(
  remembered: ApplicationStatus | null,
  counts: Readonly<Record<ApplicationStatus, number>>,
): ApplicationStatus {
  return remembered ?? firstOccupiedStatus(counts);
}
