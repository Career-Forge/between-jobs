// Mirrors resume_documents_routes.py's `POST /{id}/coverage` response
// (Sprint 3.3b/3.3c) and tailor.py's `ClusterCoverage`/skills.py's
// `SkillState`.

export interface ClusterCoverage {
  name: string;
  priority: string;
  keywords: string[];
  coverage_count: number;
  matched_fact_ids: string[];
}

export type SkillState = "verified" | "supported" | "adjacent" | "unsupported";

export interface SkillClassification {
  skill: string;
  requested_as: string;
  state: SkillState;
}

export interface CoverageResponse {
  step0: {
    clusters: ClusterCoverage[];
    dealbreakers?: string[];
    company_name?: string;
    role_name?: string;
  };
  coverage: ClusterCoverage[];
  skills: SkillClassification[];
}

export const SKILL_STATE_LABELS: Record<SkillState, string> = {
  verified: "Verified",
  supported: "Supported",
  adjacent: "Adjacent",
  unsupported: "Gap",
};
