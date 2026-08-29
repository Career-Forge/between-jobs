-- Today feed items (Horizon Sprint 4.0) -- Proposal §37.1's daily control
-- surface, scoped to the three items genuinely producible from real
-- events today (a new application tracked, a resume generated or failed,
-- a stage change) -- see digest_listener.py's own docstring for why the
-- other five §37.1 bullets (high-fit jobs, outreach followups, interview
-- prep, stale applications, artifact approval) aren't attempted here.
--
-- Populated exclusively by the digest listener (the first real
-- event_outbox subscriber) as it consumes claimed outbox rows -- never
-- written directly by a route handling a user request, the same
-- "backend always mediates through the service-role client" shape every
-- other table in this project follows. No RLS policies for
-- authenticated/anon at all, matching event_outbox's own precedent:
-- RLS stays enabled (default deny), but there is no legitimate direct
-- client read/write path -- the GET /today and POST /today/{id}/dismiss
-- routes go through the service-role client with a verified user_id
-- filter, same as every other route in this codebase.
--
-- `source_outbox_event_id` is unique -- the digest listener's own
-- idempotency boundary: a claimed outbox row already turned into a
-- today_item is never turned into a second one, even if the listener
-- reruns over the same row (e.g. after a partial-batch failure).

create table public.today_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid references public.applications (id) on delete cascade,
  kind text not null,
  headline text not null,
  detail text,
  source_outbox_event_id uuid not null references public.event_outbox (id) on delete cascade,
  created_at timestamptz not null default now(),
  dismissed_at timestamptz
);

create unique index today_items_source_event_uidx
  on public.today_items (source_outbox_event_id);

-- Powers the feed's own read: this user's undismissed items, newest first.
create index today_items_user_undismissed_idx
  on public.today_items using btree (user_id, created_at desc)
  where dismissed_at is null;

alter table public.today_items enable row level security;
