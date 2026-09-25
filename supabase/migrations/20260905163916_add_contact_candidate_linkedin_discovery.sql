-- ContactFinder LinkedIn discovery (outreach-v2-search-first.md Phase K)
-- -- Exa People Search, opt-in and human-gated, same "single-valued
-- state directly on contact_candidates" shape as Phase C's Apollo/Hunter
-- email enrichment columns (20260902150833). Every row starts null and
-- stays null until a human explicitly triggers this for that one
-- candidate; discovery never claims a LinkedIn URL for a candidate that
-- didn't already surface one as ordinary search evidence.

alter table public.contact_candidates
  add column discovered_linkedin_url text,
  add column linkedin_discovery_confidence text,
  add column linkedin_discovery_provider text,
  add column linkedin_discovered_at timestamptz;
