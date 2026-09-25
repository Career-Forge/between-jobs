-- Make the API roles' grants explicit, exactly as prod has them.
--
-- No earlier migration grants anything to anon, authenticated or
-- service_role. Prod was created when Supabase's default privileges granted
-- every new table, sequence and function in public to all three roles, so
-- the grants came for free. Projects created since then (the dev branch
-- included) grant new objects to postgres only, so replaying the migrations
-- there left service_role unable to read a single table or call a single
-- function the backend uses.
--
-- This migration writes down prod's grants as they stand on 2026-09-25, read
-- from prod's own ACLs: on prod it changes nothing, and on a fresh project
-- it produces the same privileges. Whether anon and authenticated need any
-- table access at all is a separate question -- neither the web app nor the
-- extension queries tables directly, and the backend uses service_role only
-- -- so tightening them is left to its own migration.

-- All 45 tables: every privilege, for all three roles (prod's current state).
grant all on all tables in schema public to anon, authenticated, service_role;

-- Sequences (company_tiers_id_seq, geo_gazetteer_cities_id_seq).
grant select, update, usage on all sequences in schema public to anon, authenticated, service_role;

-- The trigger helper is executable by all three roles on prod.
grant execute on function public.set_updated_at() to anon, authenticated, service_role;

-- SECURITY DEFINER functions: service_role only. Each one's own migration
-- already revoked public, anon and authenticated.
grant execute on function public.advance_job_registry_poll_state(jsonb) to service_role;
grant execute on function public.backfill_job_registry_posting_descriptions(jsonb) to service_role;
grant execute on function public.change_application_stage(uuid, uuid, text, text, text, text) to service_role;
grant execute on function public.claim_and_publish_outbox_batch(integer) to service_role;
grant execute on function public.claim_extension_draft_answer_slot(uuid, integer, integer) to service_role;
grant execute on function public.close_stale_job_registry_postings(jsonb) to service_role;
grant execute on function public.consume_link_code(text, text, text, uuid) to service_role;
grant execute on function public.decrypt_secret(text) to service_role;
grant execute on function public.encrypt_secret(text) to service_role;
grant execute on function public.insert_high_fit_job_today_item(uuid, text, text, uuid, uuid, text, text, text, text, integer, text, text, text) to service_role;
grant execute on function public.insert_status_proposal_today_item(uuid, uuid, text, text, uuid, uuid) to service_role;
grant execute on function public.list_drafts_due_for_reply_check(timestamp with time zone, integer) to service_role;
grant execute on function public.merge_user_data(uuid, uuid) to service_role;
grant execute on function public.penalize_failed_job_registry_boards(text[], text[]) to service_role;
grant execute on function public.search_job_registry_postings(text, integer) to service_role;
grant execute on function public.search_new_job_registry_postings(text, timestamp with time zone, integer) to service_role;
grant execute on function public.select_due_job_registry_companies() to service_role;
grant execute on function public.select_eightfold_jd_backfill_candidates() to service_role;
grant execute on function public.upsert_job_registry_postings(jsonb) to service_role;
