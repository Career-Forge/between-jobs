-- Browser extension E1 (browser-extension.md) -- "known-question memory."
--
-- Checked directly before writing this rather than trusting a research
-- summary's own hedge: `PrepareApplicationResult.application_answers_id`
-- (engine_contract.py) is a pure placeholder, always None
-- (prepare_orchestrator.py's own docstring: "forge-engines has no
-- application-answers generation yet"). There is nothing to extend --
-- this is a genuinely new concept.
--
-- Live DOM research across Greenhouse/Lever/Ashby found none of the three
-- expose a screening question's MEANING in a durable, cross-context key --
-- Greenhouse's question ids and Lever's card UUIDs are stable only per
-- posting; Ashby's custom-field UUIDs are stable per org but still
-- label-text-only for meaning. So the real memory key has to be the
-- rendered question's own normalized text, matching Proposal §25's
-- `approved_answers` schema. User-scoped, not job-scoped: an answer to
-- "are you legally authorized to work in the US" is a property of the
-- person, reused across every application -- the only scope that makes
-- the reuse exclusions below coherent.
--
-- `jurisdiction` is load-bearing, not decorative: Proposal's hard
-- exclusion "work-related eligibility facts when a job's jurisdiction
-- differs" requires the stored answer to actually carry a tag to check
-- against, not just prose describing the rule.
--
-- `sensitive_category` backs D6 (browser-extension.md): EEO/demographic/
-- work-authorization answers default to opt-in only, never silently
-- autofilled -- null means "not sensitive," a non-null value names which
-- category gate it falls under (e.g. 'eeo_demographic',
-- 'legal_attestation', 'salary_expectation'). Enforcement of the opt-in
-- itself is an E2/E3 application-layer concern; this column just carries
-- the fact needed to enforce it.
create table public.approved_answers (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  normalized_question text not null,
  canonical_intent text,
  answer_text text not null,
  evidence_fact_ids uuid[] not null default '{}',
  jurisdiction text,
  sensitive_category text,
  expires_at timestamptz,
  times_used integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, normalized_question)
);

-- Serves the deterministic-intent-alias match tier (step 2 of Proposal
-- §25's 5-step order) without a full-table scan per lookup.
create index approved_answers_user_intent_idx
  on public.approved_answers (user_id, canonical_intent)
  where canonical_intent is not null;

alter table public.approved_answers enable row level security;

create policy approved_answers_select_own on public.approved_answers
  for select
  using (auth.uid() = user_id);

create policy approved_answers_insert_own on public.approved_answers
  for insert
  with check (auth.uid() = user_id);

create policy approved_answers_update_own on public.approved_answers
  for update
  using (auth.uid() = user_id);

create policy approved_answers_delete_own on public.approved_answers
  for delete
  using (auth.uid() = user_id);

create trigger approved_answers_set_updated_at
  before update on public.approved_answers
  for each row
  execute function public.set_updated_at();
