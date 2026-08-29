-- Resume storage (Sprint 2.4) -- raw text only, deliberately not the real
-- structured contract. Retired in Sprint 2.5's follow-up migration
-- (profile_versions/career_facts replace it); kept here, backfilled, so the
-- migration history stays reproducible from a clean database.
--
-- Backfilled into the repo in Sprint 2.5 -- see the sibling 2.1 migration's
-- header for why these two were applied via MCP before being checked in.

create function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table public.resumes (
  user_id uuid primary key references auth.users (id) on delete cascade,
  raw_text text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.resumes enable row level security;

create policy resumes_select_own on public.resumes
  for select
  using (auth.uid() = user_id);

create policy resumes_insert_own on public.resumes
  for insert
  with check (auth.uid() = user_id);

create policy resumes_update_own on public.resumes
  for update
  using (auth.uid() = user_id);

create trigger resumes_set_updated_at
  before update on public.resumes
  for each row
  execute function public.set_updated_at();
