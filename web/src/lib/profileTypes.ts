// Mirrors profile.py's Pydantic models exactly (schema v1.2). Kept as a
// single source of truth for the shape the sectioned Profile view reads --
// every field optional-on-the-TS-side even where the backend always sends
// it, since `canonical_json` crosses an API boundary and defensive reads
// are cheap insurance against drift.

export interface Email {
  address: string;
  primary?: boolean;
}

export interface Phone {
  number: string;
  primary?: boolean;
  region?: string;
}

export interface OtherLink {
  label: string;
  url: string;
}

export interface Links {
  linkedin?: string;
  github?: string;
  portfolio?: string;
  scholar?: string;
  other?: OtherLink[];
}

export interface PersonalLocation {
  city?: string;
  region?: string;
  country?: string;
  show_on_resume?: boolean;
}

export interface Personal {
  name: string;
  headline?: string;
  emails?: Email[];
  phones?: Phone[];
  links?: Links;
  location?: PersonalLocation;
  work_authorization?: string;
  // S5 (honest-score-surfaces.md, D1): render-only, locale-gated on
  // generation -- see profile.py's own field comment. No editing UI for
  // this yet; manual JSON paste already accepts it (extra="forbid" only
  // rejects UNKNOWN fields, not known-but-omitted ones).
  nationality?: string;
}

// R5 (resumeforge-shape-and-fit.md): a candidate-set hard guarantee that
// this entry survives resume generation regardless of relevance ranking.
// `min_bullets` (1-6) is interpreted only for experience/projects --
// forge-engines ignores it harmlessly on education. Capped at 6 pinned
// entries total across experience+projects+education (profile.py enforces
// the cap; this type doesn't need to know the number).
export interface Pin {
  mandatory: boolean;
  min_bullets?: number | null;
}

export interface Experience {
  title: string;
  company: string;
  location?: string;
  start_date: string;
  end_date: string;
  is_current?: boolean;
  bullets?: string[];
  skills?: string[];
  metrics?: string[];
  pin?: Pin | null;
}

export interface Project {
  name: string;
  url?: string;
  tech?: string[];
  bullets?: string[];
  metrics?: string[];
  pin?: Pin | null;
}

export interface Education {
  degree: string;
  field?: string;
  institution: string;
  location?: string;
  start_date?: string;
  end_date?: string;
  gpa?: string;
  coursework?: string[];
  pin?: Pin | null;
}

export interface Publication {
  title: string;
  authors?: string;
  venue?: string;
  date?: string;
  url?: string;
}

export interface Patent {
  title: string;
  patent_number?: string;
  status?: string;
  date?: string;
  url?: string;
}

export interface Skills {
  programming?: string[];
  ai_ml?: string[];
  data_mlops?: string[];
  cloud_devops?: string[];
  tools?: string[];
  other?: string[];
}

export interface Certification {
  name: string;
  issuer?: string;
  date?: string;
  url?: string;
}

export interface LanguageEntry {
  language: string;
  fluency?: string;
}

export interface VolunteerEntry {
  organization: string;
  role?: string;
  start_date?: string;
  end_date?: string;
  bullets?: string[];
}

export interface CanonicalProfile {
  personal: Personal;
  summary_bullets?: string[];
  experience?: Experience[];
  projects?: Project[];
  education?: Education[];
  publications?: Publication[];
  patents?: Patent[];
  skills?: Skills;
  certifications?: Certification[];
  achievements?: string[];
  languages?: LanguageEntry[];
  volunteering?: VolunteerEntry[];
}

export const SKILL_CATEGORY_LABELS: Record<keyof Skills, string> = {
  programming: "Programming",
  ai_ml: "AI / ML",
  data_mlops: "Data & MLOps",
  cloud_devops: "Cloud & DevOps",
  tools: "Tools",
  other: "Other",
};
