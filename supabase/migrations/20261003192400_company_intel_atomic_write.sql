-- P0.10 -- a company-intel run and its claims are written together or not at all.
--
-- `create_run` used to insert the run row and then, in a second request, its claims. If the
-- claims insert failed (a null source_url, a dropped connection, a timeout) the run row stayed:
-- an empty dossier that `get_latest_run` returns as "the current dossier", displacing the
-- previous good one, with the research spend already gone. Shared state lives in transactions
-- (CLAUDE.md): both inserts now happen inside this one function, so a failure in either leaves
-- nothing behind.
--
-- The caller passes the user id (it is a SECURITY DEFINER function the backend alone can call,
-- the same shape as change_application_stage), and the function checks that the application
-- belongs to that user, so a bug upstream cannot attach a run to someone else's application.
-- Returns the new run row, exactly what the old `insert ... returning` did. Claims that are
-- not a JSON array fail in jsonb_array_elements (22023) and, like any other failure, leave
-- nothing behind.

create function public.create_company_intel_run(
  p_user_id uuid,
  p_application_id uuid,
  p_company_name text,
  p_providers_used text[],
  p_warnings jsonb,
  p_claims jsonb
) returns public.company_intel_runs
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_run public.company_intel_runs;
begin
  if not exists (
    select 1 from public.applications a
    where a.id = p_application_id and a.user_id = p_user_id
  ) then
    raise exception 'application not found' using errcode = 'P0002';
  end if;

  insert into public.company_intel_runs
    (user_id, application_id, company_name, providers_used, warnings)
  values
    (p_user_id, p_application_id, p_company_name, coalesce(p_providers_used, '{}'),
     coalesce(p_warnings, '[]'::jsonb))
  returning * into v_run;

  insert into public.company_intel_claims
    (run_id, category, claim_text, source_url, source_title, confidence)
  select v_run.id,
         c ->> 'category',
         c ->> 'claim_text',
         c ->> 'source_url',
         c ->> 'source_title',
         c ->> 'confidence'
  from jsonb_array_elements(coalesce(p_claims, '[]'::jsonb)) as c;

  return v_run;
end;
$$;

revoke execute on function public.create_company_intel_run(uuid, uuid, text, text[], jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function public.create_company_intel_run(uuid, uuid, text, text[], jsonb, jsonb)
  to service_role;
