// Pure, testable column logic for the Applications board -- split out from
// the render component per this repo's own "new behavior needs tests" rule,
// same pattern as honestFloor.ts/scoreBreakdown.ts.

import { ALL_STATUSES, type Application, type ApplicationStatus } from "./applicationsTypes";
import { formatTrailDate } from "./trail";

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  saved: "Saved",
  applied: "Applied",
  screening: "Screening",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

// Buckets every application under its column, always returning all 7 keys
// (even when empty) so the board never drops a column just because
// nothing is in it yet.
//
// A status outside the known 7 can't happen for a row written once the
// database enforced the vocabulary (a CHECK constraint), but a defensive
// fallback into "saved" -- rather than silently dropping the card off the
// board entirely -- costs nothing and matches this project's "unknown
// labeled as unknown, never guessed" spirit for anything older than that.
export function groupByStatus(applications: readonly Application[]): Record<ApplicationStatus, Application[]> {
  const groups = {} as Record<ApplicationStatus, Application[]>;
  for (const status of ALL_STATUSES) {
    groups[status] = [];
  }
  for (const application of applications) {
    const bucket = groups[application.status] ? application.status : "saved";
    groups[bucket].push(application);
  }
  return groups;
}

// The other 6 statuses, in board order, excluding the card's current one
// -- backs the per-card "Move to..." fallback. Required, not optional:
// native HTML5 drag-and-drop has no keyboard or touch story of its own.
export function otherStatuses(current: ApplicationStatus): ApplicationStatus[] {
  return ALL_STATUSES.filter((status) => status !== current);
}

// The per-card cross-nav menu into the ApplicationWorkspace.tsx panels each
// item can jump straight to. Shared by the list card and the board card so
// the label/hash pairing only lives in one place. TailorPanel is a workspace
// panel but not one of the menu's destinations, so it has no entry here.
//
// CROSS_NAV_HASH is the single source of truth for the hash strings --
// ApplicationWorkspace.tsx imports these same constants for its panel
// wrapper `id`s rather than repeating the literal strings, so renaming or
// typoing one can't silently desync a menu item from the panel it's
// supposed to scroll to (a mismatch would otherwise fail silently: the
// menu still navigates, `scrollIntoView` just never finds its target).
export const CROSS_NAV_HASH = {
  generate: "generate",
  companyIntel: "company-intel",
  contacts: "contacts",
  warmPathEvents: "warm-path-events",
  hiringPosts: "hiring-posts",
  interviewPractice: "interview-practice",
  positioningBrief: "your-play",
} as const;

export interface CrossNavItem {
  hash: string;
  label: string;
}

export const CROSS_NAV_ITEMS: readonly CrossNavItem[] = [
  { hash: CROSS_NAV_HASH.generate, label: "Generate Docs" },
  { hash: CROSS_NAV_HASH.companyIntel, label: "Research Company" },
  { hash: CROSS_NAV_HASH.contacts, label: "Find Contacts" },
  { hash: CROSS_NAV_HASH.warmPathEvents, label: "Find Events" },
  { hash: CROSS_NAV_HASH.hiringPosts, label: "Find Hiring Posts" },
  { hash: CROSS_NAV_HASH.interviewPractice, label: "Practice Interview" },
  { hash: CROSS_NAV_HASH.positioningBrief, label: "Your Play" },
];

// The card's "Applied Sep 30" caption, or null when date_applied is unset or cannot be read as a
// date. Pulled out as its own pure function -- rather than an inline `application.date_applied &&
// ...` guard in the component's JSX -- so the common case (a "saved" application, where
// date_applied is always null until it first becomes "applied") has real test coverage. The day is
// written the way every trail line writes one (a year only when it is not this year), and a value
// that is not a date is left out rather than drawn as "Invalid Date".
export function formatAppliedDate(dateApplied: string | null, now: Date, timeZone?: string): string | null {
  if (!dateApplied) return null;
  const day = formatTrailDate(dateApplied, { now, timeZone });
  return day === null ? null : `Applied ${day}`;
}

// How many applications sit under each status, all 7 keys always present.
export function statusCounts(applications: readonly Application[]): Record<ApplicationStatus, number> {
  const groups = groupByStatus(applications);
  const counts = {} as Record<ApplicationStatus, number>;
  for (const status of ALL_STATUSES) counts[status] = groups[status].length;
  return counts;
}
