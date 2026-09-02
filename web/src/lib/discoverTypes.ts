// Job Finder P8 (job-finder-port.md's own build order) -- shared types for
// the Discover page and its pure logic module, split out the same way
// applicationsTypes.ts is (discover.test.ts needs these shapes too, and
// importing types from a component file would be backwards).

export type ScoreBin = "Strong" | "Good" | "Mixed" | "Poor";
export type LocationMatch = "match" | "mismatch" | "unknown";

export interface SubScores {
  skills: number;
  experience: number;
  workauth: number;
  location: number;
  company_health: number;
  compensation: number;
}

export interface JobScore {
  fit_score: number;
  one_liner: string;
  score100: number;
  bin: ScoreBin;
  bottleneck: string | null;
  sub_scores: SubScores;
  inapplicable_dims: string[];
  detected_location: string | null;
  location_match: LocationMatch;
}

export interface JobCard {
  provider: string;
  title: string;
  company: string | null;
  location: string | null;
  remote: boolean | null;
  apply_url: string;
  snippet: string;
  posted_at: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  sponsorship_signal: string;
  source_tier: number;
  location_verified: boolean | null;
  link_checked: boolean;
  score: JobScore | null;
}

export interface DiscoverResponse {
  query: string;
  scored: JobCard[];
  more: JobCard[];
  dead_removed: number;
  scoring_strategy: string;
  warnings: string[];
}

export interface TrackDiscoveredJobBody {
  apply_url: string;
  title: string;
  company: string | null;
  location: string | null;
  snippet: string;
  provider: string;
}

// Job Finder P9a (today-feed-job-matching.md) -- reuses this same filter
// shape exactly, since the real UX is "save the search I already ran".
export interface SavedSearch {
  id: string;
  query: string;
  location: string | null;
  companies: string[];
  remote_only: boolean;
  is_active: boolean;
  created_at: string;
}

export interface CreateSavedSearchBody {
  query: string;
  location: string | null;
  companies: string[];
  remote_only: boolean;
}
