-- Job Finder P5d (live-search-track.md) -- the city half of the
-- gazetteer-backed 3-state location filter. n8n's real Aggregate Jobs
-- node (aggregate_jobs.js) needs a real GeoNames-derived city/alias
-- index to resolve free-text locations on both the job side and the
-- request side -- confirmed live incident (s96) motivating this: a tiny
-- hand-curated alias list missed "US Remote" entirely and a mismatching
-- job was silently KEPT instead of correctly dropped.
--
-- Same precedent as P1's own registry-seed import: this is DATA, not
-- code, and n8n's reference repo already has it fully built
-- (`data/reference/geonames_cities.json`, 34,006 cities, 252 countries,
-- CC BY 4.0 -- GeoNames.org `cities15000` + `countryInfo`) -- copying it
-- via a one-time import script (`scripts/import_geo_gazetteer.py`) is
-- not a "modify the frozen reference" violation. Per this project's own
-- "never commit registry or interview-intel datasets" rule, the actual
-- city rows live ONLY in the real Supabase project, never as a
-- git-tracked file in this repo -- only the schema and the import
-- script (which reads its source from the separate, already-frozen n8n
-- reference repo, not from anything checked into this one) are public.
--
-- The much smaller country name/alias table (252 entries, ~10.7KB) is
-- NOT here -- generic ISO-3166-adjacent reference data, small and
-- non-competitive enough to just bundle directly as a JSON file in this
-- repo (`src/between_jobs/api/data/geo_country_aliases.json`), no import
-- script needed for that half.
--
-- No per-column btree indexes for name/alt-name lookup: the whole table
-- (a few MB) is loaded into memory ONCE per backend process and an
-- alias index is built there, mirroring n8n's own real design (load the
-- whole JSON file once per Code-node execution, build an in-memory
-- index) rather than a per-lookup SQL query -- resolveLocation()'s own
-- multi-candidate, multi-segment matching isn't a shape SQL expresses
-- cleanly anyway. `population desc` ordering at load time is what makes
-- "the same city name in two countries" disambiguation pick the bigger
-- city by default (matching the reference's own documented behavior),
-- so the one index this table has supports that read pattern.
create table public.geo_gazetteer_cities (
  id bigint generated always as identity primary key,
  name text not null,
  ascii_name text not null,
  alt_names text[] not null default '{}',
  country_code text not null,
  population integer not null default 0,
  latitude numeric,
  longitude numeric
);

create index geo_gazetteer_cities_population_idx
  on public.geo_gazetteer_cities (population desc);

alter table public.geo_gazetteer_cities enable row level security;

create policy geo_gazetteer_cities_select_all on public.geo_gazetteer_cities
  for select
  to authenticated
  using (true);
