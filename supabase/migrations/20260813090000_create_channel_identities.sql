-- Identity model (Sprint 2.8a) -- Proposal.md §14, DDL verbatim (RLS
-- added, same pattern as telegram_links' own SELECT-only policy: only
-- the service-role backend ever writes this table).
--
-- Generalizes `telegram_links` (Sprint 2.1) -- this migration also
-- migrates its data and drops it in the same transaction, so there's no
-- window where a Telegram identity link could be lost between the two
-- steps. `telegram_user_id bigint` becomes `external_subject text`
-- (Telegram's id space fits in bigint, but the generalized column has to
-- hold Discord/Slack/etc. ids too, which aren't always numeric) and
-- `linked_at` becomes `verified_at` -- same meaning, renamed to match
-- what it now represents for every channel, not just Telegram.
--
-- Unlike this project's usual precedent of leaving a status-like column
-- unconstrained text, `channel` gets the literal CHECK constraint §14
-- specifies -- the Proposal wrote it as an explicit enum this time, not
-- an open vocabulary, so it's honored exactly rather than loosened to
-- match this repo's other tables.

create table public.channel_identities (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  channel text not null check (
    channel in ('telegram', 'discord', 'slack', 'whatsapp', 'sms', 'voice', 'extension')
  ),
  external_subject text not null,
  external_tenant text not null default '',
  verified_at timestamptz not null,
  metadata jsonb not null default '{}'::jsonb,
  unique (channel, external_tenant, external_subject)
);

create index channel_identities_user_id_idx on public.channel_identities using btree (user_id);

alter table public.channel_identities enable row level security;

create policy channel_identities_select_own on public.channel_identities
  for select
  using (auth.uid() = user_id);

insert into public.channel_identities (user_id, channel, external_subject, external_tenant, verified_at)
select user_id, 'telegram', telegram_user_id::text, '', linked_at
from public.telegram_links;

drop table public.telegram_links;
