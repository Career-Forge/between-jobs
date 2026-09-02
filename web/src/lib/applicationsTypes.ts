// Shared types for the Applications page and its two views (List --
// Applications.tsx's own original table; Board -- ApplicationsBoard.tsx,
// Applications Kanban K2). Split out so the pure column logic in
// applicationsBoard.ts and both view components import the same shapes,
// rather than a component file exporting types another file has to reach
// into -- same rationale as interviewPracticeTypes.ts's own split.
//
// `ApplicationStatus`/`ALL_STATUSES` are the real, server-enforced
// vocabulary (Applications Kanban K1 -- a Postgres CHECK constraint plus
// `change_application_stage`'s own copy of the same check; see
// `models.ApplicationStatus` and applications-kanban.md). This is the
// single frontend copy of that list, in the same left-to-right order the
// board's columns use -- List view's own status `<select>` already drew
// from this exact 7-value set before K1 shipped, so no behavior changes
// there.

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
  // K1: stamped once, the first time status becomes "applied" -- never
  // reset by a later bounce back out of that stage.
  date_applied: string | null;
  snapshot: JobSnapshot | null;
  // K1: real, batch-computed -- true only if a resume document has
  // actually been generated for this application.
  resume_exists: boolean;
}
