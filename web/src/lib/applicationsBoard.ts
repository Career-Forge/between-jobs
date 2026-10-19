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

// The per-card cross-nav menu into the workspace's sections: each item jumps straight to one. Shared
// by the list card and the board card so the label/hash pairing only lives in one place. The menu
// lists the destinations a person goes to; the resume part's own sections (Tailor, Draft, Review) are
// reached from the workspace's jump bar and the next-step band, so they have no menu item.
//
// CROSS_NAV_HASH is the single source of truth for the hash strings of every workspace section, menu
// destination or not. The workspace takes its section ids from it through SECTION_IDS
// (lib/workspaceSections.ts) rather than repeating the literal strings, so renaming or typoing one
// can't silently desync a link from the section it's supposed to scroll to (a mismatch would
// otherwise fail silently: the link still navigates, `scrollIntoView` just never finds its target).
export const CROSS_NAV_HASH = {
  // The sections of the workspace's resume part that have no panel of their own: the posting and
  // tailoring, the draft's header, structure and settings, and the review of a generated resume.
  tailor: "ch-sources",
  draft: "ch-draft",
  review: "ch-check",
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

// The "Applied Sep 30" caption, or null when date_applied is unset or cannot be read as a date. It is
// the one place that words this fact, for the Applications cards and the workspace header alike. The
// server stamps date_applied when an application first becomes Applied and keeps it when the
// application is moved back to Saved, so a Saved application with a date reads "First applied Sep 30":
// "Applied Sep 30" beside a Saved status would read as a contradiction. Pulled out as its own pure
// function -- rather than an inline `application.date_applied && ...` guard in the component's JSX --
// so the common case (a "saved" application that never was applied, where date_applied is null) has
// real test coverage. The day is written the way every trail line writes one (a year only when it is
// not this year), and a value that is not a date is left out rather than drawn as "Invalid Date".
export function formatAppliedDate(dateApplied: string | null, now: Date, timeZone?: string, status?: string): string | null {
  if (!dateApplied) return null;
  const day = formatTrailDate(dateApplied, { now, timeZone });
  if (day === null) return null;
  return `${status === "saved" ? "First applied" : "Applied"} ${day}`;
}

// How many applications sit under each status, all 7 keys always present.
export function statusCounts(applications: readonly Application[]): Record<ApplicationStatus, number> {
  const groups = groupByStatus(applications);
  const counts = {} as Record<ApplicationStatus, number>;
  for (const status of ALL_STATUSES) counts[status] = groups[status].length;
  return counts;
}
