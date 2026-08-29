-- The /link one-time-code flow (Sprint 2.8c) -- Proposal §14 Telegram-
-- linking steps 1-2: "Signed-in web user requests a short-lived one-time
-- code. Store only its hash with expiry and attempt limit."
--
-- `link_codes` holds the code side (hash + expiry); `link_code_attempts`
-- holds the rate limit. They're deliberately separate: a wrong code
-- guess doesn't match any row's hash, so there's no specific `link_codes`
-- row to attach an "attempt" to -- the only thing a failed guess
-- identifies is WHO made it (the external channel account doing the
-- guessing), which is the actual attack surface worth rate-limiting.
-- `link_code_attempts` is keyed by (channel, external_subject), not by
-- code or by target user, for exactly that reason.
--
-- No RLS-facing write policy on either table -- both are minted/consumed
-- entirely server-side (mint from a JWT-authed route, consume from the
-- Telegram webhook). `link_codes` gets a SELECT-own policy so a signed-in
-- user's own client could show "you have a pending code" if a future UI
-- wants that; `link_code_attempts` has RLS enabled with no policies at
-- all (deny-everything), same as event_outbox -- it isn't owned by any
-- single auth.users row, since the external channel account making a
-- guess may not resolve to any user at all yet.

create table public.link_codes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  channel text not null check (
    channel in ('telegram', 'discord', 'slack', 'whatsapp', 'sms', 'voice', 'extension')
  ),
  code_hash text not null,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

-- Powers both the mint-time "invalidate my other pending codes" cleanup
-- and the consume-time hash lookup.
create index link_codes_pending_idx
  on public.link_codes using btree (user_id, channel)
  where consumed_at is null;

create index link_codes_code_hash_idx
  on public.link_codes using btree (code_hash)
  where consumed_at is null;

alter table public.link_codes enable row level security;

create policy link_codes_select_own on public.link_codes
  for select
  using (auth.uid() = user_id);

create table public.link_code_attempts (
  channel text not null,
  external_subject text not null,
  failed_count integer not null default 0,
  locked_until timestamptz,
  updated_at timestamptz not null default now(),
  primary key (channel, external_subject)
);

alter table public.link_code_attempts enable row level security;
