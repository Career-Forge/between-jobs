-- ContactFinder Phase H (outreach-v2-search-first.md) -- per-company
-- product vocabulary. `product_terms` is the flagship-software-product/
-- sub-area terms the L2 picker chose (0-2, always a subset of a
-- deterministically-built candidate list -- see contact_research.py's
-- extract_product_term_candidates/pick_product_terms). Stored on the run
-- row, not derived elsewhere, purely for transparency: so the exact
-- terms a given run's manager-persona queries were built from are
-- visible after the fact, the same reasoning providers_used/warnings
-- were already stored for.

alter table public.contact_research_runs
  add column product_terms text[] not null default '{}';
