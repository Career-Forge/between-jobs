-- Gmail draft integration (outreach-contactfinder.md Phase F) -- Proposal
-- §27.4/§28.7.
--
-- `oauth_states` exists ONLY to correlate Google's OAuth redirect back to
-- the between-jobs user who started the flow -- the callback is an
-- unauthenticated browser GET (no JWT attached, unlike every other route
-- in this codebase), so there is no other way to recover `user_id` at
-- that point. Carries no Gmail data itself. Single-use and short-lived
-- (10 minutes, matching link_codes.py's own TTL constant) -- the route
-- deletes a row the moment it's consumed, success or failure, so a state
-- value can never be replayed.

create table public.oauth_states (
  state text primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  provider text not null,
  created_at timestamptz not null default now()
);

alter table public.oauth_states enable row level security;
-- No policy -- pure backend bookkeeping, never read by any client
-- directly (same "service-role-only, RLS as defense-in-depth" stance as
-- every other backend-only table in this schema).

-- Tracks whether an already-generated outreach_drafts row (Phase E) has
-- been pushed into the user's real Gmail as an actual draft, and which
-- Gmail draft id it became -- lets a re-click be a safe no-op (return
-- the existing state) instead of creating a duplicate Gmail draft for
-- the same outreach draft.
alter table public.outreach_drafts
  add column gmail_draft_id text,
  add column pushed_to_gmail_at timestamptz;
