// Mirrors forge-engines' `HeaderLayout`/`HeaderChipConfig` (Sprint 3.2b) and
// this platform's `resume_documents` row (Sprint 3.2a) -- see
// resume_documents_store.py and forge_engines_client.py's ResolvedChip
// equivalents. Kept as plain interfaces, not re-derived from a shared
// schema: both sides are small and stable enough that hand-mirroring is
// cheaper than generating types across the Python/TS boundary.

export type ChipDisplayMode = "full" | "short" | "label" | "custom";

export interface HeaderChipConfig {
  field: string;
  display_mode?: ChipDisplayMode;
  display_text?: string;
}

export interface HeaderLayout {
  chips?: HeaderChipConfig[];
  separator?: "pipe" | "dot" | "bullet";
}

export interface ResolvedChip {
  field: string;
  text: string;
  href: string | null;
}

// R6 (resumeforge-shape-and-fit.md) -- mirrors between-jobs' `ShapeOverrides`
// pydantic model exactly, field for field, including the "every field
// optional, unset means inherit" contract: an unset field here means "no
// opinion, use the master document's value, or the system default if
// master has none either" -- see shape_overrides.py's own docstring for
// the full master -> per-application -> system-default precedence chain.
export type PageCountOverride = "auto" | "1" | "2";
export type Density = "compact" | "balanced" | "spacious";
export type SummaryMode = "auto" | "on" | "off";
export type BulletStyleOverride = "plain" | "bold_lead_in";

export interface ShapeOverrides {
  page_count?: PageCountOverride | null;
  density?: Density | null;
  summary?: SummaryMode | null;
  bullet_style?: BulletStyleOverride | null;
  region?: string | null;
  show_gpa?: boolean | null;
  // S5 (honest-score-surfaces.md, D1): opt-in only, default off. Even when
  // on, forge-engines only actually renders a nationality chip when the
  // RESOLVED locale expects it (today: Germany/Austria) -- a separate,
  // backend-only gate this frontend can't preview before generation (the
  // resolved locale isn't returned by any response this app reads today).
  show_nationality?: boolean | null;
}

export interface ResumeDocument {
  id: string;
  user_id: string;
  application_id: string | null;
  profile_version_id: string;
  job_snapshot_id: string | null;
  section_order: string[];
  section_visibility: Record<string, boolean>;
  header_layout: HeaderLayout;
  shape_overrides: ShapeOverrides;
  assertions: string[];
}

// forge-engines' `_DEFAULT_CHIPS` (assemble.py) -- kept in sync by hand;
// this is the set a caller can ever add back once removed, matching what
// the engine itself would render with no layout at all.
export const ALL_HEADER_FIELDS = [
  "phone",
  "email",
  "linkedin",
  "github",
  "portfolio",
  "location",
] as const;

export const FIELD_LABELS: Record<string, string> = {
  phone: "Phone",
  email: "Email",
  linkedin: "LinkedIn",
  github: "GitHub",
  portfolio: "Portfolio",
  location: "Location",
};
