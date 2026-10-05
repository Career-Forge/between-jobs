-- No client writes tables with a user's token. The web app and the extension use Supabase for
-- sign-in only; every write goes through the API, which uses the service role and bypasses
-- row-level security and applies its own validation, caps and rate limits. A write policy for a
-- user role is only a second, weaker path past those: an INSERT policy that checks `user_id` alone
-- does not tie a row to its parent's owner (the foreign keys are single-column). So drop them all.
-- With RLS still enabled and no write policy, a user token is refused every INSERT, UPDATE and
-- DELETE; the service role is unaffected. Each user keeps read access to their own rows (the
-- *_select_own policies stay).
--
-- The same for Storage: the API is the only thing that writes the artifacts bucket and it also
-- serves the files, so the upload policy goes and the SELECT policy stays as a second wall.
--
-- The RLS suite (tests/integration/test_local_rls_isolation.py) keeps this true: it fails if any
-- policy for a user role allows a write.

drop policy if exists application_events_insert_own on public.application_events;

drop policy if exists applications_insert_own on public.applications;
drop policy if exists applications_update_own on public.applications;

drop policy if exists approved_answers_delete_own on public.approved_answers;
drop policy if exists approved_answers_insert_own on public.approved_answers;
drop policy if exists approved_answers_update_own on public.approved_answers;

drop policy if exists artifact_versions_insert_own on public.artifact_versions;

drop policy if exists capability_preferences_delete_own on public.capability_preferences;
drop policy if exists capability_preferences_insert_own on public.capability_preferences;
drop policy if exists capability_preferences_update_own on public.capability_preferences;

drop policy if exists career_facts_insert_own on public.career_facts;

drop policy if exists hiring_signal_saves_delete_own on public.hiring_signal_saves;
drop policy if exists hiring_signal_searches_delete_own on public.hiring_signal_searches;

drop policy if exists profile_versions_insert_own on public.profile_versions;
drop policy if exists profile_versions_update_own on public.profile_versions;

drop policy if exists provider_credentials_delete_own on public.provider_credentials;
drop policy if exists provider_credentials_insert_own on public.provider_credentials;
drop policy if exists provider_credentials_update_own on public.provider_credentials;

drop policy if exists resume_documents_delete_own on public.resume_documents;
drop policy if exists resume_documents_insert_own on public.resume_documents;
drop policy if exists resume_documents_update_own on public.resume_documents;

drop policy if exists saved_searches_delete_own on public.saved_searches;
drop policy if exists saved_searches_insert_own on public.saved_searches;
drop policy if exists saved_searches_update_own on public.saved_searches;

drop policy if exists sessions_insert_own on public.sessions;
drop policy if exists sessions_update_own on public.sessions;

drop policy if exists working_sets_insert_own on public.working_sets;

drop policy if exists artifacts_insert_own on storage.objects;
