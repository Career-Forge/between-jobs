-- Importing a resume file resolves a model credential for its own capability, `profile_import`
-- (it falls back to the person's default model like every other capability), so a "setup
-- required" event for it must be recordable. The capability list on product_events is closed
-- and mirrors the keys the backend resolves; this adds the new key and nothing else.
--
-- Revert: drop the constraint and add it back without 'profile_import' (any rows that already
-- carry that value would have to be deleted or changed first).

alter table public.product_events drop constraint product_events_capability_check;

alter table public.product_events
  add constraint product_events_capability_check check (
    capability in (
      'application_answer_generation',
      'company_intel',
      'contact_enrichment',
      'contact_research',
      'gmail_draft',
      'gmail_reply_check',
      'hiring_signals',
      'interview_practice',
      'job_scoring',
      'job_url_ingest',
      'linkedin_discovery',
      'outreach_writer',
      'positioning_brief',
      'prepare_application',
      'profile',
      'profile_import',
      'warm_path_events'
    )
  );
