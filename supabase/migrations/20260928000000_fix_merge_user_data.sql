-- Fix merge_user_data's data loss and close the remaining link-hijack paths
-- (P0.7). See between-jobs-private/claude/P0.7-approach.md for the full
-- design and the research it's based on.
--
-- What was wrong: merge_user_data moved only 11 of the 28 tables that carry
-- a user_id, and telegram_webhook.py used to delete the source auth user
-- unconditionally right after -- the delete cascaded through the other 17,
-- destroying whatever the merge itself had missed. The Python hotfix
-- (P0.7 S-1/S2) already stopped that delete and confined /link to an
-- account's own auto-provisioned identity. This migration rewrites the SQL
-- side to match: every user-owned table is either reparented or explicitly
-- retired, application/profile collisions are resolved instead of raising
-- 23505 and rolling the whole link back, the hijack guard is enforced here
-- too (not just in Python, so the RPC can't be driven around it), and a
-- completeness check refuses the link rather than silently losing a table
-- a future migration forgets to add here.
--
-- Deleting the source auth user is moved into its own function,
-- finish_link_delete_source, called only after every table (and, from
-- Python, every storage object) is confirmed empty -- closing the window
-- between "we think everything moved" and "we deleted the account" that
-- the old unconditional delete never had.

-- ============================================================
-- Schema changes
-- ============================================================

alter table public.link_codes
  add column consumed_by_user_id uuid,
  add column consumed_by_subject text;
-- No FK on consumed_by_user_id: it names the source of a completed merge,
-- and the source auth user is deleted once its data is fully drained. It
-- exists so a crash between committing the merge and finishing the
-- Python-side storage move (or between that and deleting the source) can
-- resume instead of telling the user their code is invalid.

create unique index channel_identities_user_id_channel_key
  on public.channel_identities (user_id, channel);
-- One Telegram (or any other channel) identity per web account. Makes
-- "the target is already linked to a different Telegram account" (D-Q1,
-- decided: refuse) a database rule, and closes the race of two Telegram
-- accounts linking the same web account at once -- the second commits only
-- after the first, then fails this index instead of silently landing.

-- ============================================================
-- assert_unreferenced: a generic "nothing still points at this row" guard
-- ============================================================

create or replace function public.assert_unreferenced(
  p_table regclass, p_id uuid, p_ignore regclass[] default '{}'
) returns void
language plpgsql
set search_path = ''
as $$
declare
  v_conrelid regclass;
  v_column name;
  v_count bigint;
begin
  if exists (
    select 1 from pg_catalog.pg_constraint c
    where c.contype = 'f' and c.confrelid = p_table and array_length(c.conkey, 1) > 1
      and not (c.conrelid = any(p_ignore))
  ) then
    -- A composite FK into this table would be silently skipped by the
    -- single-column check below (conkey[1] alone can't express "check all
    -- of these columns together"). Fail loudly instead of quietly losing
    -- the guarantee this function exists to provide.
    raise exception 'a composite foreign key into % is not supported by assert_unreferenced', p_table
      using errcode = 'BJ008';
  end if;

  for v_conrelid, v_column in
    select c.conrelid, a.attname
    from pg_catalog.pg_constraint c
    join pg_catalog.pg_attribute a
      on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
    where c.contype = 'f'
      and c.confrelid = p_table
      and array_length(c.conkey, 1) = 1
      and not (c.conrelid = any(p_ignore))
  loop
    execute format('select count(*) from %s where %I = $1', v_conrelid, v_column)
      into v_count using p_id;
    if v_count > 0 then
      raise exception 'still referenced: % row(s) in % via %', v_count, v_conrelid, v_column
        using errcode = 'BJ001';
    end if;
  end loop;
end;
$$;
revoke execute on function public.assert_unreferenced(regclass, uuid, regclass[])
  from public, anon, authenticated;
-- No explicit grant: called only from within other functions already
-- running as postgres (their SECURITY DEFINER owner), which always has
-- implicit EXECUTE on functions it owns.

-- ============================================================
-- is_auto_provisioned_telegram_user: the hijack guard, enforced in SQL too
-- ============================================================

create or replace function public.is_auto_provisioned_telegram_user(
  p_user_id uuid, p_subject text
) returns boolean
language sql
security definer
set search_path = ''
stable
as $$
  select coalesce(
    (
      select (u.raw_app_meta_data ->> 'bj_provisioned_by') = 'telegram'
         and (u.raw_app_meta_data ->> 'bj_telegram_subject') = p_subject
      from auth.users u
      where u.id = p_user_id
    ),
    false
  );
$$;
revoke execute on function public.is_auto_provisioned_telegram_user(uuid, text)
  from public, anon, authenticated;
-- No grant to service_role: only postgres-owned functions in this file call
-- it, running as postgres already. service_role has no SELECT on auth.users
-- at all (verified), which is exactly why this is SECURITY DEFINER.

-- ============================================================
-- user_owned_row_counts: what a user still owns, table by table
-- ============================================================

create or replace function public.user_owned_row_counts(p_user_id uuid)
returns jsonb
language plpgsql
set search_path = ''
set statement_timeout = '30s'
as $$
declare
  v_result jsonb := '{}'::jsonb;
  v_conrelid regclass;
  v_column name;
  v_count bigint;
begin
  if exists (
    select 1 from pg_catalog.pg_constraint c
    where c.contype = 'f' and c.confrelid = 'auth.users'::regclass
      and c.connamespace = 'public'::regnamespace and array_length(c.conkey, 1) > 1
  ) then
    -- Same reasoning as assert_unreferenced: a composite FK straight to
    -- auth.users would need auth.users to expose a matching compound
    -- unique key, which it doesn't today -- but if one is ever added, fail
    -- loudly rather than silently under-counting what a user still owns.
    raise exception 'a composite foreign key to auth.users is not supported by user_owned_row_counts'
      using errcode = 'BJ008';
  end if;

  -- Every table with a real FK to auth.users, using the FK's own column --
  -- not an assumed "user_id" -- so a future table is covered automatically,
  -- the whole reason this walks the catalog instead of a hard-coded list.
  for v_conrelid, v_column in
    select c.conrelid, a.attname
    from pg_catalog.pg_constraint c
    join pg_catalog.pg_attribute a
      on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
    where c.contype = 'f'
      and c.confrelid = 'auth.users'::regclass
      and c.connamespace = 'public'::regnamespace
      and array_length(c.conkey, 1) = 1
  loop
    execute format('select count(*) from %s where %I = $1', v_conrelid, v_column)
      into v_count using p_user_id;
    if v_count > 0 then
      v_result := v_result || jsonb_build_object(v_conrelid::text, v_count);
    end if;
  end loop;

  -- application_events.actor_id holds a user id as text, not a real FK.
  execute 'select count(*) from public.application_events where actor_type = ''user'' and actor_id = $1'
    into v_count using p_user_id::text;
  if v_count > 0 then
    v_result := v_result || jsonb_build_object('application_events.actor_id', v_count);
  end if;

  -- storage_key is a de-normalized "<user>/<artifact>/<version>" string,
  -- not a live reference -- it only catches up once the Python-side storage
  -- move (P0.7 S2) rewrites it, so this is expected to be nonzero for a
  -- merge whose storage move hasn't finished yet.
  execute 'select count(*) from public.artifact_versions where storage_key like $1'
    into v_count using p_user_id::text || '/%';
  if v_count > 0 then
    v_result := v_result || jsonb_build_object('artifact_versions.storage_key', v_count);
  end if;

  -- Every bucket, keyed by the same "<user>/..." prefix every upload uses.
  execute 'select count(*) from storage.objects where name like $1'
    into v_count using p_user_id::text || '/%';
  if v_count > 0 then
    v_result := v_result || jsonb_build_object('storage.objects', v_count);
  end if;

  return v_result;
end;
$$;
revoke execute on function public.user_owned_row_counts(uuid) from public, anon, authenticated;
grant execute on function public.user_owned_row_counts(uuid) to service_role;
-- SECURITY INVOKER (the default): service_role already has direct SELECT on
-- every table this touches, plus BYPASSRLS, so it doesn't need to borrow
-- postgres's privileges the way is_auto_provisioned_telegram_user does.

-- ============================================================
-- merge_user_data: move everything a user owns, or account for why not
-- ============================================================

drop function if exists public.merge_user_data(uuid, uuid);

create or replace function public.merge_user_data(
  p_source_user_id uuid, p_target_user_id uuid, p_subject text
) returns jsonb
language plpgsql
security definer
set search_path = ''
set statement_timeout = '30s'
as $$
declare
  v_ns constant uuid := '6f6e9b64-6b8e-4c3e-9a0c-2f6a2b1a2d4e';
  v_summary jsonb := '{}'::jsonb;
  v_count integer;
  v_target_active_id uuid;
  v_kept_id uuid;
  v_kept_json jsonb;
  v_src_pv record;
  v_matched_src_ids uuid[];
  v_matched_kept_ids uuid[];
  v_i integer;
  v_collision record;
  v_kind record;
  v_old_artifact_id uuid;
  v_new_artifact_id uuid;
  v_max_target_version integer;
  v_leftover jsonb;
begin
  if p_source_user_id = p_target_user_id then
    return '{}'::jsonb;
  end if;

  -- Defense in depth: consume_link_code already checks this before ever
  -- calling here, but this function is also called on resume (from
  -- finish_link_merge), and this is the one guard the SQL itself can never
  -- be driven around by a caller that skips the Python check.
  if not public.is_auto_provisioned_telegram_user(p_source_user_id, p_subject) then
    raise exception 'source is not an auto-provisioned account for this subject'
      using errcode = 'BJ004';
  end if;

  -- Recorded now, before anything moves, so a source profile with a newer
  -- (or null) activated_at can never displace what the target already
  -- considered active. Re-stamped at the very end.
  select id into v_target_active_id from public.profile_versions
    where user_id = p_target_user_id
    order by activated_at desc nulls last
    limit 1;

  -- 1. Rows that don't move: ephemeral or single-use state with nothing
  -- worth carrying over. Left alone, any of these would fail the
  -- completeness check at the end of this function -- there is no "keep
  -- both" for a nonce or a rate-limit window.
  delete from public.oauth_states where user_id = p_source_user_id;
  delete from public.extension_sign_outs where user_id = p_source_user_id;
  delete from public.extension_draft_answer_rate_limits where user_id = p_source_user_id;
  delete from public.working_sets where user_id = p_source_user_id;
  -- An auto-provisioned source can never mint a code (that needs a JWT it
  -- can't get), so this should always be zero rows -- kept for the same
  -- reason as everything else here: an unaccounted-for row must fail
  -- loudly, not get silently deleted by a later cascade.
  delete from public.link_codes where user_id = p_source_user_id and consumed_at is null;

  -- 2. Profile dedupe. profile_versions is unique on (user_id,
  -- content_hash); once user_id is reparented, a source version whose hash
  -- the target already has would collide. Resolve every such collision
  -- before the bulk reparent below ever runs.
  for v_src_pv in
    select pv_s.* from public.profile_versions pv_s
    where pv_s.user_id = p_source_user_id
      and exists (
        select 1 from public.profile_versions pv_t
        where pv_t.user_id = p_target_user_id and pv_t.content_hash = pv_s.content_hash
      )
  loop
    select id, canonical_json into v_kept_id, v_kept_json
      from public.profile_versions
      where user_id = p_target_user_id and content_hash = v_src_pv.content_hash;

    -- content_hash is supposed to be a hash of canonical_json, but
    -- profile_versions_update_own lets a client rewrite content_hash
    -- directly -- never trust that equal hashes mean equal content.
    if v_kept_json is distinct from v_src_pv.canonical_json then
      raise exception 'profile version content_hash collision with different content'
        using errcode = 'BJ002';
    end if;

    -- Repoint the three NO ACTION FKs into profile_versions away from the
    -- row about to be deleted.
    update public.artifact_versions set profile_version_id = v_kept_id
      where profile_version_id = v_src_pv.id;
    update public.resume_documents set profile_version_id = v_kept_id
      where profile_version_id = v_src_pv.id;
    update public.profile_versions set supersedes_id = v_kept_id
      where supersedes_id = v_src_pv.id;

    -- career_facts is a CASCADE child of profile_versions, so anything
    -- still pointing at the source version when it's deleted below is
    -- gone. A fact matching one the kept version ALREADY had (same
    -- fact_type + source_pointer) is a genuine duplicate -- let it cascade
    -- away, but first remap any evidence_fact_ids array entry pointing at
    -- it onto the kept version's copy. A fact with no match is real,
    -- unique content -- move it onto the kept version so it survives.
    --
    -- The match set below is computed in one plain query, before any of
    -- this loop's own writes happen -- it must never be re-queried live
    -- inside the loop. An earlier version did exactly that (a fresh SELECT
    -- per fact, inside the loop body), and under this function's own
    -- transaction a later iteration's lookup then saw the EARLIER
    -- iteration's own "move" -- so two source facts sharing one
    -- (fact_type, source_pointer) that matched nothing on the target (a
    -- source-side duplicate, not a target-side one) had the second
    -- wrongly treated as matching the first, its content silently
    -- cascade-deleted. Freezing the match set up front closes that.
    select array_agg(sf.id order by sf.id), array_agg(kf.id order by sf.id)
      into v_matched_src_ids, v_matched_kept_ids
      from public.career_facts sf
      join public.career_facts kf
        on kf.profile_version_id = v_kept_id
        and kf.fact_type = sf.fact_type
        and kf.source_pointer = sf.source_pointer
      where sf.profile_version_id = v_src_pv.id;

    if v_matched_src_ids is not null then
      for v_i in 1 .. array_length(v_matched_src_ids, 1) loop
        update public.artifact_versions
          set evidence_fact_ids =
            array_replace(evidence_fact_ids, v_matched_src_ids[v_i], v_matched_kept_ids[v_i])
          where v_matched_src_ids[v_i] = any(evidence_fact_ids);
        update public.resume_documents
          set selected_evidence_fact_ids =
            array_replace(selected_evidence_fact_ids, v_matched_src_ids[v_i], v_matched_kept_ids[v_i])
          where v_matched_src_ids[v_i] = any(selected_evidence_fact_ids);
        update public.approved_answers
          set evidence_fact_ids =
            array_replace(evidence_fact_ids, v_matched_src_ids[v_i], v_matched_kept_ids[v_i])
          where v_matched_src_ids[v_i] = any(evidence_fact_ids);
      end loop;
    end if;

    -- Every source fact NOT in the frozen match set above moves onto the
    -- kept version, including two source facts that share a (fact_type,
    -- source_pointer) with each other but with nothing pre-existing on the
    -- target: there's no unique key on (profile_version_id, fact_type,
    -- source_pointer), and both are genuinely new content, not duplicates
    -- of anything the target already had.
    update public.career_facts
      set profile_version_id = v_kept_id
      where profile_version_id = v_src_pv.id
        and not (id = any(coalesce(v_matched_src_ids, array[]::uuid[])));

    if v_src_pv.activated_at is not null then
      update public.profile_versions
        set activated_at = coalesce(activated_at, v_src_pv.activated_at)
        where id = v_kept_id;
    end if;

    -- Only career_facts may still reference the source version at this
    -- point (the genuine duplicates, deliberately left for the cascade);
    -- anything else still pointing at it means a repoint above was missed.
    perform public.assert_unreferenced(
      'public.profile_versions'::regclass, v_src_pv.id, array['public.career_facts'::regclass]
    );
    delete from public.profile_versions where id = v_src_pv.id;
  end loop;

  -- 3. Application collapse. applications is unique on (user_id, job_id);
  -- when both accounts track the same job, fold the source's application
  -- into the target's instead of colliding when user_id is reparented.
  for v_collision in
    select s.id as source_app_id, s.date_applied as source_date_applied,
           t.id as target_app_id
    from public.applications s
    join public.applications t on t.user_id = p_target_user_id and t.job_id = s.job_id
    where s.user_id = p_source_user_id
  loop
    -- Repoint every CASCADE child of applications onto the target's row.
    update public.application_events set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.application_status_proposals set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.artifact_versions set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.company_intel_runs set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.contact_research_runs set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.hiring_signal_saves set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.interview_sessions set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.positioning_briefs set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.resume_documents set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.today_items set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    update public.warm_path_runs set application_id = v_collision.target_app_id
      where application_id = v_collision.source_app_id;
    -- event_outbox has no FK to applications (aggregate_id is polymorphic
    -- by aggregate_type), so it's repointed explicitly instead.
    update public.event_outbox set aggregate_id = v_collision.target_app_id
      where aggregate_type = 'application' and aggregate_id = v_collision.source_app_id;

    -- artifact_id is uuid5(application_id || ':' || document_kind)
    -- (artifact_versions_store.py's artifact_id_for), so repointing
    -- application_id above leaves these rows' artifact_id stale. Recompute
    -- it per kind and renumber (artifact_id, version) to fit: stage both
    -- sides through negative version numbers first (a range neither side
    -- ever holds) so the non-deferrable unique constraint never sees a
    -- collision at any intermediate step, then renumber 1..N by
    -- created_at -- also a range nothing currently holds.
    for v_kind in
      select distinct document_kind from public.artifact_versions
      where application_id = v_collision.target_app_id
    loop
      v_old_artifact_id := extensions.uuid_generate_v5(
        v_ns, v_collision.source_app_id::text || ':' || v_kind.document_kind
      );
      v_new_artifact_id := extensions.uuid_generate_v5(
        v_ns, v_collision.target_app_id::text || ':' || v_kind.document_kind
      );

      select coalesce(max(version), 0) into v_max_target_version
        from public.artifact_versions where artifact_id = v_new_artifact_id;

      update public.artifact_versions set version = -version
        where artifact_id = v_new_artifact_id;

      update public.artifact_versions
        set artifact_id = v_new_artifact_id, version = -(version + v_max_target_version)
        where artifact_id = v_old_artifact_id;

      update public.artifact_versions av
        set version = sub.rn
        from (
          select id, row_number() over (order by created_at) as rn
          from public.artifact_versions where artifact_id = v_new_artifact_id
        ) sub
        where av.id = sub.id;
    end loop;

    -- The target's own status and snapshot are kept as-is; only the
    -- earliest non-null date_applied survives (least() ignores nulls
    -- unless both are null).
    update public.applications
      set date_applied = least(date_applied, v_collision.source_date_applied)
      where id = v_collision.target_app_id;

    insert into public.application_events
      (application_id, user_id, event_type, payload, actor_type, actor_id, idempotency_key)
    values (
      v_collision.target_app_id, p_target_user_id, 'application.merged',
      jsonb_build_object(
        'merged_from_application_id', v_collision.source_app_id,
        'merged_from_user_id', p_source_user_id
      ),
      'system', 'merge_user_data', 'application_merged:' || v_collision.source_app_id::text
    )
    on conflict (user_id, idempotency_key) do nothing;

    perform public.assert_unreferenced('public.applications'::regclass, v_collision.source_app_id);
    delete from public.applications where id = v_collision.source_app_id;
  end loop;

  -- 4. Target wins: on every other user-scoped unique key, drop the
  -- source's competing row rather than raise. Runs after the collapse
  -- above, so keys that include an application id see the collapsed ones.
  delete from public.provider_credentials pc
    where pc.user_id = p_source_user_id
      and exists (
        select 1 from public.provider_credentials t
        where t.user_id = p_target_user_id and t.service = pc.service and t.provider = pc.provider
      );

  delete from public.capability_preferences cp
    where cp.user_id = p_source_user_id
      and exists (
        select 1 from public.capability_preferences t
        where t.user_id = p_target_user_id and t.capability = cp.capability
      );

  delete from public.approved_answers aa
    where aa.user_id = p_source_user_id
      and exists (
        select 1 from public.approved_answers t
        where t.user_id = p_target_user_id and t.normalized_question = aa.normalized_question
      );

  -- hiring_signal_searches is unique on the LOWERED query/location, not the
  -- raw columns -- match the same expressions the index uses.
  delete from public.hiring_signal_searches hs
    where hs.user_id = p_source_user_id
      and exists (
        select 1 from public.hiring_signal_searches t
        where t.user_id = p_target_user_id
          and lower(t.query) = lower(hs.query)
          and coalesce(lower(t.location), '') = coalesce(lower(hs.location), '')
      );

  -- hiring_signal_saves has two partial unique indexes: one for a save
  -- with no application (one per user+activity) and one for a save tied
  -- to an application (per user+application+activity).
  delete from public.hiring_signal_saves hsv
    where hsv.user_id = p_source_user_id and hsv.application_id is null
      and exists (
        select 1 from public.hiring_signal_saves t
        where t.user_id = p_target_user_id and t.application_id is null
          and t.activity_id = hsv.activity_id
      );
  delete from public.hiring_signal_saves hsv
    where hsv.user_id = p_source_user_id and hsv.application_id is not null
      and exists (
        select 1 from public.hiring_signal_saves t
        where t.user_id = p_target_user_id and t.application_id = hsv.application_id
          and t.activity_id = hsv.activity_id
      );

  -- resume_documents has two unique keys of its own: one master resume per
  -- user (application_id is null), and one per (user, application).
  delete from public.resume_documents rd
    where rd.user_id = p_source_user_id and rd.application_id is null
      and exists (
        select 1 from public.resume_documents t
        where t.user_id = p_target_user_id and t.application_id is null
      );
  delete from public.resume_documents rd
    where rd.user_id = p_source_user_id and rd.application_id is not null
      and exists (
        select 1 from public.resume_documents t
        where t.user_id = p_target_user_id and t.application_id = rd.application_id
      );

  -- channel_identities is unique on (user_id, channel) (the index this
  -- migration adds). 'telegram' can never collide here -- consume_link_code
  -- already refused the link earlier if the target had one -- but the
  -- channel column also allows other values (the browser extension,
  -- future channels) nothing writes yet; if one ever links both accounts
  -- to the same channel independently, target wins here rather than the
  -- bulk reparent below raising a raw, unrecoverable 23505.
  delete from public.channel_identities ci
    where ci.user_id = p_source_user_id
      and exists (
        select 1 from public.channel_identities t
        where t.user_id = p_target_user_id and t.channel = ci.channel
      );

  -- 5. application_events keeps every row (it's an audit trail) --
  -- a colliding idempotency_key is renamed instead of dropped, and past
  -- actions taken "as" the Telegram identity are re-attributed to the
  -- target, the same real person.
  update public.application_events ae
    set idempotency_key = 'merged:' || ae.id::text || ':' || ae.idempotency_key
    where ae.user_id = p_source_user_id
      and exists (
        select 1 from public.application_events tae
        where tae.user_id = p_target_user_id and tae.idempotency_key = ae.idempotency_key
      );

  update public.application_events
    set actor_id = p_target_user_id::text
    where actor_type = 'user' and actor_id = p_source_user_id::text;

  -- 6. Bulk-reparent everything else. Every remaining table with a user_id
  -- column, whatever collisions it could have were already resolved above.
  update public.profile_versions set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('profile_versions', v_count);

  update public.career_facts set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('career_facts', v_count);

  update public.applications set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('applications', v_count);

  update public.application_events set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('application_events', v_count);

  update public.application_status_proposals set user_id = p_target_user_id
    where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('application_status_proposals', v_count);

  update public.event_outbox set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('event_outbox', v_count);

  update public.artifact_versions set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('artifact_versions', v_count);

  update public.provider_credentials set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('provider_credentials', v_count);

  update public.capability_preferences set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('capability_preferences', v_count);

  update public.channel_identities set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('channel_identities', v_count);

  update public.company_intel_runs set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('company_intel_runs', v_count);

  update public.contact_research_runs set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('contact_research_runs', v_count);

  update public.hiring_signal_saves set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('hiring_signal_saves', v_count);

  update public.hiring_signal_searches set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('hiring_signal_searches', v_count);

  update public.interview_sessions set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('interview_sessions', v_count);

  update public.outreach_drafts set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('outreach_drafts', v_count);

  update public.positioning_briefs set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('positioning_briefs', v_count);

  update public.resume_documents set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('resume_documents', v_count);

  update public.saved_searches set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('saved_searches', v_count);

  update public.sessions set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('sessions', v_count);

  update public.today_items set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('today_items', v_count);

  update public.warm_path_runs set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('warm_path_runs', v_count);

  update public.approved_answers set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('approved_answers', v_count);

  -- 7. Re-stamp the target's own previously-active profile so it stays
  -- unambiguously the most recent, whatever activated_at the source's
  -- (now reparented) profiles carry.
  if v_target_active_id is not null then
    update public.profile_versions set activated_at = now() where id = v_target_active_id;
  end if;

  -- 8. Completeness check. A table this function doesn't know about --
  -- today, or added after this migration and not updated here -- makes
  -- the link refuse instead of silently losing whatever it held. Storage
  -- is excluded: it's moved by Python after this function returns, and
  -- finish_link_delete_source does its own un-excluded recount before ever
  -- deleting the source auth user.
  v_leftover := public.user_owned_row_counts(p_source_user_id)
    - 'storage.objects' - 'artifact_versions.storage_key';
  if v_leftover <> '{}'::jsonb then
    raise exception 'merge left rows behind: %', v_leftover using errcode = 'BJ003';
  end if;

  return v_summary;
end;
$$;
revoke execute on function public.merge_user_data(uuid, uuid, text) from public, anon, authenticated, service_role;
-- postgres only: not reachable via RPC at all. consume_link_code and
-- finish_link_merge, both owned by postgres, call it directly.

-- ============================================================
-- consume_link_code: the /link entry point
-- ============================================================

create or replace function public.consume_link_code(
  p_channel text, p_external_subject text, p_code text, p_source_user_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = 'public', 'extensions'
set statement_timeout = '30s'
as $$
declare
  v_owner uuid;
  v_lockout record;
  v_lockout_found boolean;
  v_new_failed_count integer;
  v_code_row record;
  v_resume record;
  v_summary jsonb;
  v_reason text;
  v_max_attempts constant integer := 5;
  v_lockout_minutes constant integer := 15;
  v_resume_window constant interval := interval '24 hours';
begin
  if p_channel <> 'telegram' then
    raise exception 'unsupported channel: %', p_channel using errcode = 'BJ005';
  end if;

  -- Locks this Telegram subject's identity row for the rest of the
  -- transaction, serializing every concurrent link attempt for it: a
  -- second concurrent caller blocks here, then sees whatever the first one
  -- committed once it's released.
  select user_id into v_owner
    from public.channel_identities
    where channel = p_channel and external_tenant = '' and external_subject = p_external_subject
    for update;

  if not found or v_owner is distinct from p_source_user_id then
    return jsonb_build_object('ok', false, 'reason', 'source_mismatch');
  end if;

  select * into v_lockout from public.link_code_attempts
    where channel = p_channel and external_subject = p_external_subject
    for update;
  v_lockout_found := found;

  if v_lockout_found and v_lockout.locked_until is not null and v_lockout.locked_until > now() then
    return jsonb_build_object('ok', false, 'reason', 'rate_limited', 'locked_until', v_lockout.locked_until);
  end if;

  select * into v_code_row from public.link_codes
    where channel = p_channel
      and code_hash = encode(digest(p_code, 'sha256'), 'hex')
      and consumed_at is null
    for update;

  if not found then
    -- Not an unconsumed code. Maybe it's one this exact Telegram account
    -- already used successfully, and the caller (retried after a crash
    -- between the commit below and finishing the storage move, P0.7 S2)
    -- never saw the result -- recognize that instead of saying it's invalid.
    select * into v_resume from public.link_codes
      where channel = p_channel
        and code_hash = encode(digest(p_code, 'sha256'), 'hex')
        and consumed_by_subject = p_external_subject
        and consumed_at > now() - v_resume_window
      order by consumed_at desc
      limit 1;
    if found then
      return jsonb_build_object(
        'ok', true, 'resumed', true,
        'target_user_id', v_resume.user_id, 'source_user_id', v_resume.consumed_by_user_id
      );
    end if;
    v_reason := 'invalid_code';
  elsif v_code_row.expires_at < now() then
    v_reason := 'expired_code';
  else
    v_reason := null;
  end if;

  if v_reason is not null then
    -- A failure resets the count to 1 once the last one is more than 15
    -- minutes old, rather than accumulating forever across unrelated
    -- attempts. A success (below) never touches this row at all, so a
    -- self-link can't be used to clear a real lockout.
    if v_lockout_found and v_lockout.updated_at > now() - (v_lockout_minutes || ' minutes')::interval then
      v_new_failed_count := v_lockout.failed_count + 1;
    else
      v_new_failed_count := 1;
    end if;
    insert into public.link_code_attempts (channel, external_subject, failed_count, updated_at, locked_until)
    values (
      p_channel, p_external_subject, v_new_failed_count, now(),
      case when v_new_failed_count >= v_max_attempts
        then now() + (v_lockout_minutes || ' minutes')::interval
        else null
      end
    )
    on conflict (channel, external_subject) do update
    set failed_count = excluded.failed_count,
        updated_at = excluded.updated_at,
        locked_until = excluded.locked_until;
    return jsonb_build_object('ok', false, 'reason', v_reason);
  end if;

  if v_code_row.user_id = p_source_user_id then
    update public.link_codes
      set consumed_at = now(), consumed_by_user_id = p_source_user_id, consumed_by_subject = p_external_subject
      where id = v_code_row.id;
    return jsonb_build_object('ok', true, 'target_user_id', v_code_row.user_id, 'source_user_id', p_source_user_id);
  end if;

  -- The hijack guard, enforced here too so the RPC can't be driven around
  -- Python's own check: only an account this Telegram subject was
  -- auto-provisioned as may be merged away. The code is still burned --
  -- otherwise a linked account could be probed for whether a code is real.
  if not public.is_auto_provisioned_telegram_user(p_source_user_id, p_external_subject) then
    update public.link_codes
      set consumed_at = now(), consumed_by_user_id = p_source_user_id, consumed_by_subject = p_external_subject
      where id = v_code_row.id;
    return jsonb_build_object('ok', false, 'reason', 'source_already_linked');
  end if;

  -- The new (user_id, channel) unique index would refuse this anyway; catch
  -- it here first for a clean reason instead of a raw 23505.
  if exists (
    select 1 from public.channel_identities
    where user_id = v_code_row.user_id and channel = p_channel
  ) then
    update public.link_codes
      set consumed_at = now(), consumed_by_user_id = p_source_user_id, consumed_by_subject = p_external_subject
      where id = v_code_row.id;
    return jsonb_build_object('ok', false, 'reason', 'target_linked_elsewhere');
  end if;

  update public.link_codes
    set consumed_at = now(), consumed_by_user_id = p_source_user_id, consumed_by_subject = p_external_subject
    where id = v_code_row.id;

  v_summary := public.merge_user_data(p_source_user_id, v_code_row.user_id, p_external_subject);

  return jsonb_build_object(
    'ok', true, 'target_user_id', v_code_row.user_id, 'source_user_id', p_source_user_id, 'summary', v_summary
  );
end;
$$;
revoke execute on function public.consume_link_code(text, text, text, uuid) from public, anon, authenticated;
grant execute on function public.consume_link_code(text, text, text, uuid) to service_role;

-- ============================================================
-- finish_link_merge / finish_link_delete_source: resuming a link, and
-- retiring the source account only once it's genuinely empty
-- ============================================================

create or replace function public.finish_link_merge(
  p_channel text, p_subject text, p_source uuid, p_target uuid
) returns jsonb
language plpgsql
security definer
set search_path = ''
set statement_timeout = '30s'
as $$
declare
  v_owner uuid;
begin
  if not exists (select 1 from auth.users where id = p_source) then
    return jsonb_build_object('ok', true, 'source_gone', true);
  end if;

  select user_id into v_owner
    from public.channel_identities
    where channel = p_channel and external_tenant = '' and external_subject = p_subject
    for update;

  if not found or v_owner <> p_target then
    raise exception 'not the confirmed target of this link' using errcode = 'BJ006';
  end if;

  if not exists (
    select 1 from public.link_codes
    where channel = p_channel and user_id = p_target
      and consumed_by_user_id = p_source and consumed_by_subject = p_subject
      and consumed_at > now() - interval '24 hours'
  ) then
    raise exception 'no matching consumed link code found' using errcode = 'BJ007';
  end if;

  -- Safe to call again: merge_user_data's own writes are all "where
  -- user_id = source", so once source has nothing left, every statement in
  -- it is a no-op and the completeness check passes trivially.
  return jsonb_build_object(
    'ok', true, 'source_gone', false,
    'summary', public.merge_user_data(p_source, p_target, p_subject)
  );
end;
$$;
revoke execute on function public.finish_link_merge(text, text, uuid, uuid) from public, anon, authenticated;
grant execute on function public.finish_link_merge(text, text, uuid, uuid) to service_role;

create or replace function public.finish_link_delete_source(
  p_channel text, p_subject text, p_source uuid, p_target uuid
) returns jsonb
language plpgsql
security definer
set search_path = ''
set statement_timeout = '30s'
as $$
declare
  v_owner uuid;
  v_counts jsonb;
begin
  if not exists (select 1 from auth.users where id = p_source) then
    return jsonb_build_object('ok', true, 'source_gone', true);
  end if;

  select user_id into v_owner
    from public.channel_identities
    where channel = p_channel and external_tenant = '' and external_subject = p_subject
    for update;

  if not found or v_owner <> p_target then
    raise exception 'not the confirmed target of this link' using errcode = 'BJ006';
  end if;

  if not exists (
    select 1 from public.link_codes
    where channel = p_channel and user_id = p_target
      and consumed_by_user_id = p_source and consumed_by_subject = p_subject
      and consumed_at > now() - interval '24 hours'
  ) then
    raise exception 'no matching consumed link code found' using errcode = 'BJ007';
  end if;

  -- Locks the row so an in-flight insert into any FK-backed column this
  -- lock conflicts with (its own FOR KEY SHARE lock) is waited on rather
  -- than raced -- it then fails visibly with a foreign-key violation once
  -- unblocked, instead of being silently cascaded away by the delete
  -- below. Disclosed gap, not closed by this lock: user_owned_row_counts
  -- also reports two signals with no real FK to auth.users --
  -- application_events.actor_id (a plain text column) and storage.objects
  -- (matched by path, not a foreign key) -- so a row landing there in the
  -- narrow window between the recount just below and the delete can still
  -- end up referencing a user id that no longer exists. Low-consequence
  -- (an audit-trail or storage-path reference, not data a user reads back
  -- through the merged account) and narrow enough that closing it -- a
  -- trigger-backed guard on actor_id, most plausibly -- isn't done here.
  perform 1 from auth.users where id = p_source for update;

  v_counts := public.user_owned_row_counts(p_source);
  if v_counts <> '{}'::jsonb then
    return jsonb_build_object('ok', true, 'source_gone', false, 'counts', v_counts);
  end if;

  delete from auth.users where id = p_source;
  return jsonb_build_object('ok', true, 'source_gone', true);
end;
$$;
revoke execute on function public.finish_link_delete_source(text, text, uuid, uuid) from public, anon, authenticated;
grant execute on function public.finish_link_delete_source(text, text, uuid, uuid) to service_role;
-- postgres owns this (from running this migration), which is what lets it
-- reach auth.users at all -- service_role has no grant there (verified);
-- only postgres does.
