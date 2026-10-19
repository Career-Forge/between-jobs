// Shared types for the Applications page and its views (the list, the board
// and the phone's one-status-at-a-time list). Split out so the pure column
// logic in applicationsBoard.ts and every view component import the same
// shapes, rather than a component file exporting types another file has to
// reach into -- same rationale as interviewPracticeTypes.ts's own split.
//
// `ApplicationStatus`/`ALL_STATUSES` are the real, server-enforced
// vocabulary (a Postgres CHECK constraint plus `change_application_stage`'s
// own copy of the same check; see `models.ApplicationStatus`). This is the
// single frontend copy of that list, in the same left-to-right order the
// board's columns use.

export type ApplicationStatus =
  | "saved"
  | "applied"
  | "screening"
  | "interviewing"
  | "offer"
  | "rejected"
  | "withdrawn";

export const ALL_STATUSES: readonly ApplicationStatus[] = [
  "saved",
  "applied",
  "screening",
  "interviewing",
  "offer",
  "rejected",
  "withdrawn",
];

export interface JobSnapshot {
  id: string;
  title: string;
  company_name: string;
  location_text: string | null;
  source_url: string;
}

export interface Application {
  id: string;
  status: ApplicationStatus;
  source_channel: string;
  created_at: string;
  updated_at: string;
  // Stamped once, the first time status becomes "applied" -- never
  // reset by a later bounce back out of that stage.
  date_applied: string | null;
  snapshot: JobSnapshot | null;
  // Real, batch-computed -- true only if a resume document has
  // actually been generated for this application.
  resume_exists: boolean;
}
