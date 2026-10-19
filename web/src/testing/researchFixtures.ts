// The shapes the research and prepare sections read, as small fictional answers for their tests. Every
// name, company and address here is made up.

import { SETUP_REQUIRED_CODE } from "../lib/setupRequired";
import { LEGACY_INTEGRATIONS_PATH } from "../lib/settingsPaths";
import { FakeApiError } from "./fakeApi";

export const NOW = new Date("2026-09-22T12:00:00Z");

// A 409 SETUP_REQUIRED the way the real client carries it: the message, the code, and the fields the
// setup notice reads (where the fix is, which capability needed it, what is missing).
export class FakeSetupError extends FakeApiError {
  constructor(
    message: string,
    public readonly settingsPath: string | undefined = LEGACY_INTEGRATIONS_PATH,
    public readonly capability: string | undefined = undefined,
    public readonly missing: readonly string[] | undefined = undefined,
  ) {
    super(message, SETUP_REQUIRED_CODE, undefined, 409);
  }
}

export const RUN = {
  id: "0123456789abcdef0123",
  company_name: "Northwind Labs",
  providers_used: ["you_com"],
  warnings: [] as string[],
  created_at: "2026-09-20T12:00:00Z",
};

export function claim(n: number, overrides: Record<string, unknown> = {}) {
  return {
    id: `c${n}`,
    category: n % 2 === 0 ? "funding_and_financial_health" : "product_and_mission",
    claim_text: `Claim number ${n} about Northwind.`,
    source_url: `https://news.example.com/story-${n}`,
    source_title: `Story ${n}`,
    confidence: n % 3 === 0 ? "low" : n % 2 === 0 ? "medium" : "high",
    ...overrides,
  };
}

export function candidate(n: number, overrides: Record<string, unknown> = {}) {
  return {
    id: `p${n}`,
    person_name: `Person ${n}`,
    claimed_title: "Engineering manager",
    claimed_team: "Data Platform",
    persona: "manager",
    relevance_reason: "Manager persona match; 2 independent sources.",
    priority_score: 71.4,
    evidence: [
      { id: `e${n}a`, source_url: `https://talks.example.com/person-${n}`, source_title: "A talk by the data platform team", source_snippet: "snippet", observed_at: "2026-09-20T12:00:00Z", evidence_kind: "talk", confidence: "verified" },
      { id: `e${n}b`, source_url: `https://blog.example.com/person-${n}`, source_title: "", source_snippet: "snippet", observed_at: "2026-09-20T12:00:00Z", evidence_kind: "blog", confidence: "inferred" },
    ],
    enriched_email: null,
    enriched_email_status: null,
    enrichment_provider: null,
    enriched_at: null,
    discovered_linkedin_url: null,
    linkedin_discovery_confidence: null,
    linkedin_discovery_provider: null,
    linkedin_discovered_at: null,
    approach_hint: "Mention the data platform talk.",
    ...overrides,
  };
}

export function event(n: number, certainty: string, overrides: Record<string, unknown> = {}) {
  return {
    id: `ev${n}`,
    event_name: `Event ${n}`,
    event_url: `https://events.example.com/e${n}`,
    event_date: "2026-10-14",
    location: "Jersey City, NJ",
    certainty,
    speaker_name: certainty === "confirmed_speaker" ? "Dana Example" : null,
    speaker_title: certainty === "confirmed_speaker" ? "Staff engineer" : null,
    talk_topic: certainty === "confirmed_speaker" ? "Batch pipelines" : null,
    source_title: `Listing ${n}`,
    source_snippet: "snippet",
    ...overrides,
  };
}

export const SESSION = {
  id: "s1234567890abcdef",
  user_id: "u1",
  application_id: "a1",
  company_name: "Northwind Labs",
  resume_evidence: "evidence",
  registry_entry_id: null,
  status: "in_progress" as const,
  started_at: "2026-09-21T12:00:00Z",
  completed_at: null,
};

export function question(n: number, overrides: Record<string, unknown> = {}) {
  return {
    id: `q${n}`,
    session_id: SESSION.id,
    ordinal: n - 1,
    question_text: `Question text ${n}?`,
    question_type: n % 2 === 0 ? "technical" : "behavioral",
    target_skill: n % 2 === 0 ? "Airflow" : "Mentoring",
    grounded_in: null,
    answer_text: null,
    score: null,
    feedback: null,
    answered_at: null,
    ...overrides,
  };
}

export function answered(n: number, score: number, overrides: Record<string, unknown> = {}) {
  const technical = n % 2 === 0;
  return question(n, {
    answer_text: `My answer to ${n}.`,
    score,
    answered_at: "2026-09-21T12:10:00Z",
    feedback: {
      score,
      structure_feedback: `Structure comment ${n}.`,
      specificity_feedback: `Specifics comment ${n}.`,
      star_coverage: technical ? null : { situation: true, task: true, action: false, result: false },
      improved_answer: `A stronger answer ${n}.`,
    },
    ...overrides,
  });
}

export const BRIEF = {
  id: "b1234567890",
  lead_with: "Lead with the batch pipelines you ran at Initech.",
  lead_with_citation: "Data pipelines",
  gap_that_matters: "Streaming experience is thin.",
  gap_citation: "Kafka",
  recommended_project: "Build a small Kafka consumer that feeds a dbt model.",
  rubric_warnings: [] as string[],
  created_at: "2026-09-20T12:00:00Z",
};
