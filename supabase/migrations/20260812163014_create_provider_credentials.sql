-- Provider credentials (Sprint 2.7c) -- Proposal §11's CredentialResolver
-- needs somewhere to resolve BYOK credentials FROM. Shape mirrors
-- careerforge-command-center's `user_api_keys` (frozen reference repo):
-- service/provider/model/encrypted-secret/base_url/is_validated, one row
-- per (user, service, provider).
--
-- `secret_encrypted` is safe to read via a normal RLS SELECT -- it's
-- ciphertext from Sprint 2.7b's `encrypt_secret`, and nothing about
-- reading it exposes the plaintext without also calling `decrypt_secret`,
-- which is revoked from every client role. The web UI reads this column
-- only to answer "does the user have a key configured," never to decrypt
-- client-side.
--
-- Unlike command-center's design, this table has no accompanying
-- upsert/read RPC of its own -- command-center's browser calls Postgrest
-- directly with the user's own JWT, so its RPCs are themselves the
-- authorization boundary (`IF p_user_id != auth.uid()`). This platform's
-- browser never talks to Supabase except for auth (master plan §3.2:
-- channels hit the FastAPI orchestrator, never Supabase directly) -- the
-- backend is always the intermediary, using the service-role client,
-- filtering by a verified user_id in Python, same as every other store in
-- this codebase. So provider_credentials_store.py (Sprint 2.7c/e) just
-- calls the plain encrypt_secret/decrypt_secret RPCs around ordinary
-- table reads/writes; RLS here is defense-in-depth, not the primary gate.
--
-- Delete policy included (unlike applications/profile_versions, which
-- preserve history) -- removing a credential is a normal, expected BYOK
-- action (rotating or revoking a key), not something with an audit trail
-- to protect.

create table public.provider_credentials (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  service text not null,
  provider text not null,
  model text,
  secret_encrypted text not null,
  base_url text,
  is_default boolean not null default false,
  is_validated boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, service, provider)
);

create index provider_credentials_user_service_idx
  on public.provider_credentials using btree (user_id, service);

alter table public.provider_credentials enable row level security;

create policy provider_credentials_select_own on public.provider_credentials
  for select
  using (auth.uid() = user_id);

create policy provider_credentials_insert_own on public.provider_credentials
  for insert
  with check (auth.uid() = user_id);

create policy provider_credentials_update_own on public.provider_credentials
  for update
  using (auth.uid() = user_id);

create policy provider_credentials_delete_own on public.provider_credentials
  for delete
  using (auth.uid() = user_id);

create trigger provider_credentials_set_updated_at
  before update on public.provider_credentials
  for each row
  execute function public.set_updated_at();
