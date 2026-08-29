-- Sprint 3.0e: resolves the design question Sprint 2.7a's own migration
-- explicitly punted ("how a caller distinguishes 'the resume artifact'
-- from 'the cover letter artifact' for a given application... is that
-- sprint's own call to make"). `document_kind` gets the same literal CHECK
-- treatment as `channel_identities.channel` (2.8a) rather than this
-- project's usual unconstrained-status habit: Proposal §9's own
-- `PrepareApplicationInput.requested_artifacts` already writes this as a
-- real `Literal["resume", "cover_letter", "application_answers"]` enum,
-- so the DB mirrors a spec that was already an enum, not inventing one.
--
-- No new "artifact identity" table. `artifact_id` for a given
-- (application_id, document_kind) pair is derived deterministically
-- (uuid5, application-side) rather than looked up or minted -- the same
-- inputs always produce the same artifact_id, so "which artifact_id does
-- this application's resume use" needs no registry row to answer, and
-- `unique (artifact_id, version)` (2.7a) is still the only constraint
-- doing real work.

alter table public.artifact_versions
  add column document_kind text not null
    check (document_kind in ('resume', 'cover_letter', 'application_answers'));

create index artifact_versions_application_document_idx
  on public.artifact_versions using btree (application_id, document_kind, version desc);

-- Private bucket for generated document content (LaTeX source today --
-- Sprint 3.0 was descoped to headless text output, no PDF compilation
-- exists yet, see the v12 plan's Sprint 3.0 AskUserQuestion decision).
-- Objects are keyed `{user_id}/{artifact_id}/{version}` -- RLS below is
-- defense-in-depth like every other table here: this backend always
-- uploads via the service-role client, which bypasses storage RLS too.
insert into storage.buckets (id, name, public)
values ('artifacts', 'artifacts', false)
on conflict (id) do nothing;

create policy artifacts_select_own on storage.objects
  for select
  using (bucket_id = 'artifacts' and auth.uid()::text = (storage.foldername(name))[1]);

create policy artifacts_insert_own on storage.objects
  for insert
  with check (bucket_id = 'artifacts' and auth.uid()::text = (storage.foldername(name))[1]);
