-- Reopen the postings the registry poller closed off incomplete fetches.
--
-- Every paginated adapter reported a fetch that ran out of its per-tick page
-- budget as the board's complete listing, so close-stale closed every posting
-- past the budget. It went unnoticed while every tick died on a statement
-- timeout before closing anything; the first ticks to complete, from
-- 2026-09-26 06:41 UTC, closed 20,630 postings on the 17 boards below, each
-- bigger than its budget (14,372 of them Amazon's). The adapters now report
-- such a fetch as "partial", which closes nothing.
--
-- Closures since then on boards whose fetch really was complete (PayPal,
-- 16 SmartRecruiters boards, 4 Workday boards) are correct and stay closed.
-- No-op on a database without the registry data (local, dev).

set local statement_timeout = '15min';

update public.job_registry_postings
set status = 'active', closed_at = null
where status = 'closed'
  and closed_at >= '2026-09-26 00:00:00+00'
  and board in (
    'amazon:amazon:',
    'apple:apple:',
    'avature:usijobs.deloitte.com:careersUSI',
    'avature:apply.deloitte.com:careers',
    'eightfold:microsoft.com:microsoft.eightfold.ai',
    'oracle:CX_1001:jpmc.fa.oraclecloud.com',
    'oracle:LateralHiring:hdpc.fa.us2.oraclecloud.com',
    'oracle:Honeywell:ibqbjb.fa.ocs.oraclecloud.com',
    'smartrecruiters:Dominos:',
    'smartrecruiters:AECOM2:',
    'successfactors:ey:careers.ey.com',
    'workday:external_career_site:salesforce.wd12',
    'workday:nexstar:nexstar.wd5',
    'workday:ExternalCareerSite:hp.wd5',
    'workday:logitech:logitech.wd5',
    'workday:External:ms.wd5',
    'workday:finc:finastra.wd3'
  );