-- merge_user_data handles the product-event log and the tester enrollment.
--
-- product_events and tester_enrollments (20261005162139) both reference auth.users, so a link
-- that merges a source account has to say what happens to their rows; without this a source
-- holding either would fail the completeness check at the end of the function.
--
--   * product_events MOVE to the target. They are the history of one person's use of the
--     product, and an account that was only a Telegram identity until now and the web account it
--     is being linked to are the same person: a funnel that dropped the Telegram-side events
--     would say that person never generated anything. Moved events keep their application: when
--     two applications collapse into one (step 3), the events that named the source's are
--     repointed at the target's first, because assert_unreferenced would otherwise (rightly)
--     refuse to delete a row that something still points at, even through a set-null key.
--   * tester_enrollments are deleted from the source, the target's row wins. An enrollment is one
--     consent record per person, written when the person signs up on the web and agrees to the
--     tester terms; an auto-provisioned Telegram account never did that, so in practice it holds
--     none. If it ever does, letting it overwrite or sit beside the target's own record (the
--     primary key allows one row per user) would make the consent on file ambiguous, so the
--     record the web account gave stands, the same rule provider_credentials and the other
--     one-per-user keys follow.
--
-- This is exactly the function in 20261005113453_merge_user_data_forgets_api_rate_limits.sql
-- with those three statements added (the delete in step 1, the repoint in step 3, the move in
-- step 6); nothing else in it changed. (A migration that has been applied is never edited, so the
-- change ships as a new one.) Same grants as before: not reachable by any API role, only by the
-- postgres-owned functions that call it.

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
  delete from public.api_rate_limits where user_id = p_source_user_id;
  delete from public.tester_enrollments where user_id = p_source_user_id;
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
    update public.product_events set application_id = v_collision.target_app_id
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

  update public.product_events set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('product_events', v_count);

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
