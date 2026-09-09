-- Browser extension E3c (browser-extension.md) -- signed field-map
-- serving. E2 shipped Lever's field map (STANDARD_FIELDS and friends)
-- hardcoded directly in extension/lib/lever.ts, which is public,
-- committed, AGPL-licensed code. This project's own CLAUDE.md already
-- lists "ATS selector maps" under "Hosted-service internals... belong to
-- the hosted services, not this repo" -- a prior session in this exact
-- repo already confirmed (via MASTER_PLAN/Proposal) that this phrase
-- means browser-extension autofill maps specifically. E2's map has sat
-- in git in violation of that rule since it shipped; this table is where
-- the real, curated data moves to close that gap.
--
-- Same "real data lives only in Supabase, never git-tracked" shape as
-- P5d's geo_gazetteer_cities and P6b's company_tiers, diverging from
-- that precedent only where the trust boundary forces it: this data is
-- read by an untrusted browser extension over the network, not trusted
-- backend Python, so each row also carries an Ed25519 signature the
-- extension verifies client-side before trusting anything in
-- `payload_canonical` -- D4's "remote executable code is not permitted,
-- only signed data," and its fail-closed verification-failure mode.
--
-- `payload_canonical` is stored and served byte-for-byte as it was
-- signed (never re-serialized by this backend) -- JSON key-order/
-- whitespace differences would silently invalidate the signature
-- otherwise. Versioning is append-only per `ats_type`: "current" is
-- just `max(version)`, matching D4's own "versioned JSON payload"
-- language literally; there is no update/delete policy, only inserts
-- via the offline signing script (scripts/sign_and_publish_ats_field_
-- map.py), which holds the only copy of the Ed25519 private key --
-- never the running backend service.
--
-- Not every field in a Lever posting's markup is genuinely Lever-
-- specific IP: `name`/`email`/`phone` and the résumé-upload selector
-- are plain, unremarkable HTML-form conventions any ATS's markup could
-- plausibly use. Those stay as an open-source, unsigned constant
-- directly in extension/lib/lever.ts (GENERIC_FIELD_DEFAULTS) so a
-- fresh self-hosted clone gets baseline autofill with zero setup,
-- matching this repo's own "BYOK-first... no demo shells" rule. Only
-- the genuinely Lever-idiosyncratic conventions (the urls[LinkedIn]/
-- urls[Other...] field names, the cards[ custom-question prefix, the
-- application-field/application-label DOM-nesting shape, the cover-
-- letter label pattern) live in the signed payload this table holds.
create table public.ats_field_maps (
  id uuid primary key default gen_random_uuid(),
  ats_type text not null,
  version integer not null,
  schema text not null,
  payload_canonical text not null,
  signature_b64 text not null,
  signing_key_id text not null,
  created_at timestamptz not null default now(),
  unique (ats_type, version)
);

create index ats_field_maps_latest_idx
  on public.ats_field_maps (ats_type, version desc);

alter table public.ats_field_maps enable row level security;

create policy ats_field_maps_select_all on public.ats_field_maps
  for select
  to authenticated
  using (true);
-- Deliberately no insert/update/delete policy: only the service-role
-- signing script writes, matching geo_gazetteer_cities/company_tiers.
