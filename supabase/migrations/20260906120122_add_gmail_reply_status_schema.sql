-- Gmail reply/status parsing R1 (outreach-v2-search-first.md Phase 5's
-- second half, Proposal §28.7) -- schema for widening the Gmail OAuth
-- grant, tracking a pushed draft's real thread/send/reply state, and
-- persisting the classifier's own stage-inference proposals.

-- Which scope(s) a stored Gmail refresh token was actually granted under
-- -- without this, there's no way to tell an old gmail.compose-only
-- grant apart from a newer gmail.compose+gmail.readonly one at read
-- time. Nullable: every credential saved before this migration has no
-- recorded scope, which the reply-checker (R3) must treat the safe way
-- -- as "read access not confirmed," never assumed present.
alter table public.provider_credentials
  add column scope text;

-- gmail_thread_id is captured at draft-creation time (Gmail assigns a
-- thread/message id to a draft even before it's sent) -- the one value
-- a later poll needs to ask Gmail "what's happened in this thread since."
-- sent_confirmed_at/last_reply_checked_at are the poller's own two
-- watermarks: whether a real SENT-labeled message from the user has ever
-- been observed in the thread (closing outreach-v2-search-first.md's own
-- "draft-never-send bridge" gap -- nothing upstream of this ever
-- confirms a draft was actually sent, only that it was created), and
-- when this draft was last checked at all (mirrors saved_searches.
-- last_matched_at's own per-row-watermark role, not one global cursor).
alter table public.outreach_drafts
  add column gmail_thread_id text,
  add column sent_confirmed_at timestamptz,
  add column last_reply_checked_at timestamptz;

-- One real Postgres row per classifier proposal (Proposal §28.7's
-- ProposedApplicationEvent) -- shared state lives in Postgres rows, never
-- a mutable blob. proposed_type is the real 8-value pipeline-stage
-- taxonomy the private book specifies; status is this project's own
-- three-state-over-boolean discipline applied to "has a human (or an
-- above-threshold auto-apply) resolved this yet." The unique constraint
-- on (outreach_draft_id, source_gmail_message_id) is what makes
-- re-polling the same reply a no-op rather than a duplicate proposal.
create table public.application_status_proposals (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid not null references public.applications (id) on delete cascade,
  outreach_draft_id uuid not null references public.outreach_drafts (id) on delete cascade,
  proposed_type text not null check (
    proposed_type in (
      'application.acknowledged',
      'assessment.received',
      'interview.requested',
      'interview.scheduled',
      'application.rejected',
      'offer.received',
      'recruiter.replied',
      'unknown'
    )
  ),
  confidence double precision not null,
  evidence_spans jsonb not null default '[]'::jsonb,
  source_gmail_message_id text not null,
  status text not null default 'pending' check (status in ('pending', 'accepted', 'dismissed')),
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  unique (outreach_draft_id, source_gmail_message_id)
);

create index application_status_proposals_application_idx
  on public.application_status_proposals using btree (application_id, created_at desc);

alter table public.application_status_proposals enable row level security;

create policy application_status_proposals_select_own
  on public.application_status_proposals for select
  using (auth.uid() = user_id);
