-- ContactFinder enrichment (outreach-contactfinder.md Phase C) -- Apollo
-- only for v1, opt-in and human-gated. Nullable columns directly on
-- contact_candidates, not a separate table -- this is a single-valued
-- piece of state per candidate (the confirmed work email Apollo reveals,
-- if any), not a list needing its own child rows the way evidence does.
-- Deliberately kept OFF the discovery path entirely: every row starts
-- null and stays null until a human explicitly triggers enrichment for
-- that one candidate (contact_research_routes.enrich_contact) --
-- discovery never claims an email, matching both reference repos'
-- own precedent of treating enrichment as a distinct, later, opt-in step.

alter table public.contact_candidates
  add column enriched_email text,
  add column enriched_email_status text,
  add column enrichment_provider text,
  add column enriched_at timestamptz;
