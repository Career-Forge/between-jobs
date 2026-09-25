-- R6 (resumeforge-shape-and-fit.md): the export checklist's new
-- page_fill/no_orphan_lines/pins_honored/page_count_within_shape checks
-- need forge-engines' structured ShapeReport, but `GET .../export-checklist`
-- is a separate, later HTTP call that only re-fetches the latest
-- artifact_versions row -- by then the in-memory ShapeReport from
-- generation time is long gone. Persisting it here, alongside the
-- `warnings` jsonb this table already carries from the very same
-- generation call, is the smallest change that makes it reachable again.
--
-- Same `not null default '{}'::jsonb` shape as `warnings` (2.7a) --
-- `create_version` is only ever called once forge-engines has already
-- returned a resume, and both the regen and non-regen paths build a
-- ShapeReport unconditionally whenever that's true, so the two columns
-- share the same "always populated going forward" lifecycle; old rows
-- backfill to '{}', read by the checklist as "nothing to check against."

alter table public.artifact_versions
  add column shape_report jsonb not null default '{}'::jsonb;
