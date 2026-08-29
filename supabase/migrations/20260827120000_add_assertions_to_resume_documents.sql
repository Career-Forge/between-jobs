-- S2 (honest-score-surfaces.md): dealbreaker assertions -- a candidate's own
-- "this is actually true for me" claim about ONE specific missing dealbreaker
-- (e.g. "on-site work is fine"), scoped per-application like every other
-- resume_documents override on this table. A plain jsonb array of the
-- requirement strings the candidate has confirmed, matched fuzzily against
-- freshly re-extracted dealbreaker phrasing at scoring time (see
-- forge-engines' ats_score.apply_dealbreaker_assertions) -- never a delta,
-- the whole list, same "full replace" convention
-- selected_evidence_fact_ids/shape_overrides already use on this table.
--
-- No CHECK constraint on the shape of the blob (same reasoning as
-- shape_overrides/header_layout): between-jobs' own Pydantic request model
-- is the real validation boundary, not Postgres.

alter table public.resume_documents
  add column assertions jsonb not null default '[]'::jsonb;
