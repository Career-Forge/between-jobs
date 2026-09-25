-- R6 (resumeforge-shape-and-fit.md): resume settings -- page count, density,
-- summary, bullet style, region, show-GPA -- stored as one jsonb blob,
-- same convention `header_layout`/`section_visibility` already established
-- on this table: the exact knob set is still being worked out in the UI
-- layer, so jsonb lets it evolve without a migration per field. Applies to
-- BOTH the master document (`application_id is null`) and a per-application
-- document via the column this table already has -- no new nullable-key
-- machinery needed, R4's own locale_resolver.py already anticipated exactly
-- this shape as its (until-now unfed) `document_override`/`user_default`
-- parameters.
--
-- No CHECK constraint on the shape of the blob (same reasoning as
-- header_layout): between-jobs' own `ShapeOverrides` Pydantic model is the
-- real validation boundary for what the API accepts, not Postgres.

alter table public.resume_documents
  add column shape_overrides jsonb not null default '{}'::jsonb;
