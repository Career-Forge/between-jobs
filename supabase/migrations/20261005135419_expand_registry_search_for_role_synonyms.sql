-- The registry lane's text search understands role synonyms.
--
-- Both search functions built their tsquery with plainto_tsquery, which ANDs every word
-- of the caller's text. That cannot express "sde OR software engineer": the backend now
-- sends the query already expanded (an OR over the title spellings of one role, built
-- from sanitized words only), so the functions must read that text as websearch syntax
-- -- `ml engineer or "machine learning" engineer` -- instead of as plain words. Words are
-- still ANDed; a quoted run is an adjacent phrase; `or` separates alternatives.
--
-- That is the only change to the two statements that do the searching: the tsquery
-- constructor. Everything the earlier migrations fought for stays exactly as it was --
-- no ORDER BY on the text branch, the candidate window taken inside a subquery before
-- the join, the freshness filter after the window, the partial GIN index on jd_tsv. The
-- signatures, return shapes and grants are unchanged too (so `create or replace` is
-- allowed), and the grants are restated below: nothing here relies on a default.
--
-- One behaviour is new, and it is the old rule made complete. An empty query has always
-- meant "browse the most recent postings". A query that is not empty but contains no
-- searchable word -- only English stop words, or nothing but punctuation -- produced an
-- empty tsquery, which matches nothing, so such a query returned no rows at all. It now
-- takes the same browse branch as the empty query. `v_browse` decides this once, with a
-- CASE so the tsquery is not even built for an empty string.

create or replace function public.search_job_registry_postings(search_query text, result_limit integer)
returns table (
  posting_id uuid,
  title text,
  company_name text,
  location text,
  remote boolean,
  apply_url text,
  posted_at timestamptz,
  salary_min numeric,
  salary_max numeric,
  salary_currency text,
  sponsorship_signal text,
  snippet text,
  link_fresh boolean
)
language plpgsql
security definer
set search_path = public
stable
as $$
declare
  v_candidates integer := greatest(coalesce(result_limit, 0), 1) * 10;
  v_browse boolean := case
    when search_query = '' then true
    else numnode(websearch_to_tsquery('english', search_query)) = 0
  end;
begin
  if v_browse then
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500),
             coalesce(
               c.last_polled_at >= now() - interval '48 hours'
                 and c.consecutive_failures = 0
                 and p.last_seen >= now() - interval '3 days',
               false)
      from (
        select w.* from (
          select * from public.job_registry_postings jp
          where jp.status = 'active'
          order by jp.posted_at desc nulls last
          limit v_candidates
        ) w
        where w.last_seen > now() - interval '7 days'
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  else
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500),
             coalesce(
               c.last_polled_at >= now() - interval '48 hours'
                 and c.consecutive_failures = 0
                 and p.last_seen >= now() - interval '3 days',
               false)
      from (
        select w.* from (
          select * from public.job_registry_postings jp
          where jp.status = 'active'
            and jp.jd_tsv @@ websearch_to_tsquery('english', search_query)
          limit v_candidates
        ) w
        where w.last_seen > now() - interval '7 days'
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  end if;
end;
$$;

revoke execute on function public.search_job_registry_postings(text, integer)
  from public, anon, authenticated;
grant execute on function public.search_job_registry_postings(text, integer) to service_role;

create or replace function public.search_new_job_registry_postings(
  search_query text, since_timestamp timestamptz, result_limit integer
)
returns table (
  posting_id uuid,
  title text,
  company_name text,
  location text,
  remote boolean,
  apply_url text,
  posted_at timestamptz,
  salary_min numeric,
  salary_max numeric,
  salary_currency text,
  sponsorship_signal text,
  snippet text
)
language plpgsql
security definer
set search_path = public
stable
as $$
declare
  v_candidates integer := greatest(coalesce(result_limit, 0), 1) * 10;
  v_browse boolean := case
    when search_query = '' then true
    else numnode(websearch_to_tsquery('english', search_query)) = 0
  end;
begin
  if v_browse then
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500)
      from (
        select w.* from (
          select * from public.job_registry_postings jp
          where jp.status = 'active' and jp.first_seen > since_timestamp
          order by jp.first_seen desc
          limit v_candidates
        ) w
        where w.last_seen > now() - interval '7 days'
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  else
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500)
      from (
        select w.* from (
          select * from public.job_registry_postings jp
          where jp.status = 'active' and jp.first_seen > since_timestamp
            and jp.jd_tsv @@ websearch_to_tsquery('english', search_query)
          limit v_candidates
        ) w
        where w.last_seen > now() - interval '7 days'
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  end if;
end;
$$;

revoke execute on function public.search_new_job_registry_postings(text, timestamptz, integer)
  from public, anon, authenticated;
grant execute on function public.search_new_job_registry_postings(text, timestamptz, integer)
  to service_role;
