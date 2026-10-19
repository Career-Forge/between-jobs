// What the workspace reads of an application (GET /applications/{id}): the row, the job snapshot it
// points at, and whether a resume exists. A field a server of another age may not send is optional,
// and the page treats a missing one as not returned rather than as a default.

export interface WorkspaceSnapshot {
  id: string;
  title: string;
  company_name: string;
  location_text: string | null;
  description_text: string;
  source_url: string;
}

export interface WorkspaceApplication {
  id: string;
  status: string;
  /** "web", "discover" or "telegram" today; anything else is not named. */
  source_channel?: string;
  created_at?: string;
  /** Stamped once, the first time the status became "applied". */
  date_applied?: string | null;
  snapshot: WorkspaceSnapshot | null;
  resume_exists: boolean;
}
