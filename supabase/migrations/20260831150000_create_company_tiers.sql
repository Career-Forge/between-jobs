-- Job Finder P6b (live-search-track.md) -- the company_health half of
-- P6a's batch fit-scorer composite. Ported from n8n's real Parse Scorer
-- Output node's `tierLookup`, backed by its own `data/reference/
-- company_tiers.json` (576 companies, Fortune 500 2025 base + a
-- hand-curated MAANGO/top-fintech-quant/hot-AI-startup/other-dream/
-- Big-4 overlay from `company_tier_overrides.json`, CC BY 4.0 --
-- already fully merged by n8n's own build step, confirmed directly:
-- the overlay file is n8n's build-time INPUT, not something this
-- project re-merges itself).
--
-- Same precedent as P1's registry seed and P5d's gazetteer: this is
-- DATA, not code -- imported via a one-time script
-- (`scripts/import_company_tiers.py`) that reads directly from n8n's
-- own already-frozen reference repo. Per this project's own "never
-- commit registry or interview-intel datasets" rule, the 576 company
-- rows live ONLY in the real Supabase project, never as a git-tracked
-- file in this repo -- only this schema and the import script are
-- public.
--
-- `normalized_name` carries a UNIQUE constraint (company_tiers.json's
-- own `companies` dict is already keyed by normalized name, one entry
-- per company) -- a plain btree index would be redundant on top of it.
create table public.company_tiers (
  id bigint generated always as identity primary key,
  normalized_name text not null unique,
  display_name text not null,
  weight numeric not null,
  tier text not null,
  rank integer,
  hq text
);

alter table public.company_tiers enable row level security;

create policy company_tiers_select_all on public.company_tiers
  for select
  to authenticated
  using (true);
