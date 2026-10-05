-- No client reads these tables with a user's token. The web app and the extension use Supabase for
-- sign-in only; every read of these tables goes through the API, which uses the service role and
-- bypasses row-level security. The policies below let any signed-in user select from them directly,
-- which nothing needs, so drop them: with RLS still enabled and no policy, a user token is denied
-- everything on these tables and the service role is unaffected.
--
-- A new shared table gets no policy. If one is ever really needed, the RLS suite
-- (tests/integration/test_local_rls_isolation.py, SHARED_READABLE) makes that a visible decision.

drop policy if exists ats_field_maps_select_all on public.ats_field_maps;
drop policy if exists company_tiers_select_all on public.company_tiers;
drop policy if exists geo_gazetteer_cities_select_all on public.geo_gazetteer_cities;
drop policy if exists interview_process_registry_select_any_authenticated
  on public.interview_process_registry;
drop policy if exists job_registry_companies_select_all on public.job_registry_companies;
drop policy if exists job_registry_postings_select_all on public.job_registry_postings;
drop policy if exists job_snapshots_select_any_authenticated on public.job_snapshots;
drop policy if exists jobs_select_any_authenticated on public.jobs;
