-- Discord as a second chat channel: let its link codes be redeemed, keep the account-merge
-- protections exactly as strong as they are for Telegram, and add the claim table the Discord
-- interactions endpoint uses so the same interaction is never processed twice.
--
-- WHAT WAS TELEGRAM-ONLY, from reading 20260928000000 and every migration after it that touches
-- link codes, channel identities or the merge:
--
--   1. consume_link_code refused every channel but 'telegram' (BJ005), so a code minted for
--      Discord could never be redeemed.
--   2. The hijack guard -- "only an account this chat sender was auto-provisioned as may be merged
--      away" -- was is_auto_provisioned_telegram_user(user, subject), which reads
--      app_metadata.bj_provisioned_by = 'telegram' and bj_telegram_subject = subject. It is called
--      by consume_link_code AND by merge_user_data itself (the "SQL can never be driven around"
--      copy of the check, also reached through finish_link_merge on a resumed link). A Discord
--      bot-only account carries bj_provisioned_by = 'discord' and bj_discord_subject, so the guard
--      would have raised BJ004 and rolled the whole link back. So merge_user_data is NOT
--      channel-blind in its guard; everything else it does (every table it moves, collapses or
--      deletes) is.
--
-- WHAT THIS CHANGES
--
--   * is_auto_provisioned_channel_user(user, channel, subject) replaces the Telegram helper: the
--     same check, for the two channels that make bot-only accounts, naming the channel in both
--     metadata keys (bj_provisioned_by = channel and bj_<channel>_subject = subject). The Telegram
--     helper is dropped (nothing else calls it).
--   * merge_user_data gains a p_channel argument (the 3-argument version is dropped; only the two
--     functions below call it). The body is the one in 20261005162316_merge_user_data_handles_
--     product_events_and_enrollments.sql with exactly two lines changed: the signature and the
--     guard. Its comments are carried over unchanged and still say "Telegram" where they now mean
--     the chat channel. It stays unreachable by every API role.
--   * consume_link_code accepts 'telegram' and 'discord'. The code lookup, the resume lookup and
--     the failed-attempt counter were already keyed by the channel, so a code is only ever redeemed
--     on the channel it was minted for: a Telegram code presented by a Discord sender (and the
--     reverse) is not found, and is answered exactly as a wrong code is -- invalid_code, counted
--     against that sender's lockout. Nothing else in it changed but wording in comments.
--   * finish_link_merge passes the channel on. finish_link_delete_source needs no change (it never
--     called the guard).
--   * discord_processed_interactions and its three functions: the same lease-based claim as
--     telegram_processed_updates (20261003120448), keyed by the interaction id Discord gives (a
--     snowflake, kept as text), with the same states 'claimed' / 'done' / 'in_progress'. The one
--     difference is the default lease: 15 minutes, which is how long an interaction's token lives,
--     because Discord does not retry an interaction, so nothing legitimate ever takes a claim over.
--     Cleanup is the same as for Telegram: every claim deletes up to 200 rows older than 7 days (the
--     Telegram table has no worker either; its purge is inside its claim function), so each table
--     is bounded by its own traffic. No personal data: an interaction id is a counter Discord
--     assigns.
--
-- Same grants as before everywhere: not reachable by anon or authenticated; the claim functions,
-- consume_link_code and finish_link_merge to service_role; merge_user_data and the guard to nobody
-- but the postgres-owned functions that call them.

-- ============================================================
-- is_auto_provisioned_channel_user: the hijack guard, for every channel that makes bot-only accounts
-- ============================================================

create or replace function public.is_auto_provisioned_channel_user(
  p_user_id uuid, p_channel text, p_subject text
) returns boolean
language sql
security definer
set search_path = ''
stable
as $$
  select coalesce(
    (
      select p_channel in ('telegram', 'discord')
         and (u.raw_app_meta_data ->> 'bj_provisioned_by') = p_channel
         and (u.raw_app_meta_data ->> ('bj_' || p_channel || '_subject')) = p_subject
      from auth.users u
      where u.id = p_user_id
    ),
    false
  );
$$;
revoke execute on function public.is_auto_provisioned_channel_user(uuid, text, text)
  from public, anon, authenticated, service_role;
-- No grant to anyone: only the postgres-owned functions below call it, running as postgres already.
-- service_role has no SELECT on auth.users at all, which is why this is SECURITY DEFINER.

-- ============================================================
-- merge_user_data: the same merge, told which channel the source was provisioned for
-- ============================================================

create or replace function public.merge_user_data(
  p_source_user_id uuid, p_target_user_id uuid, p_channel text, p_subject text
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
  if not public.is_auto_provisioned_channel_user(p_source_user_id, p_channel, p_subject) then
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
revoke execute on function public.merge_user_data(uuid, uuid, text, text)
  from public, anon, authenticated, service_role;
-- postgres only: not reachable via RPC at all. consume_link_code and finish_link_merge, both owned
-- by postgres, call it directly.

drop function public.merge_user_data(uuid, uuid, text);
drop function public.is_auto_provisioned_telegram_user(uuid, text);

-- ============================================================
-- consume_link_code and finish_link_merge: Discord accepted, the channel passed on
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
  if p_channel not in ('telegram', 'discord') then
    raise exception 'unsupported channel: %', p_channel using errcode = 'BJ005';
  end if;

  -- Locks this chat sender's identity row for the rest of the
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
    -- Not an unconsumed code. Maybe it's one this exact chat account
    -- already used to link, and the caller (retried after a crash between
    -- the commit below and finishing the storage move) never saw
    -- the result -- recognize that instead of saying it's invalid. Only a
    -- completed merge counts (a refused or no-op burn below never records
    -- who used it), and only while the code's owner still holds this
    -- subject's identity: a link since undone by /unlink has nothing to
    -- resume, and pretending otherwise would tell the user they're linked
    -- to an account the bot no longer sends anything to.
    select * into v_resume from public.link_codes
      where channel = p_channel
        and code_hash = encode(digest(p_code, 'sha256'), 'hex')
        and consumed_by_subject = p_external_subject
        and user_id = v_owner
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
    update public.link_codes set consumed_at = now() where id = v_code_row.id;
    return jsonb_build_object('ok', true, 'target_user_id', v_code_row.user_id, 'source_user_id', p_source_user_id);
  end if;

  -- The hijack guard, enforced here too so the RPC can't be driven around
  -- Python's own check: only an account this chat sender was
  -- auto-provisioned as may be merged away. The code is still burned --
  -- otherwise a linked account could be probed for whether a code is real.
  if not public.is_auto_provisioned_channel_user(p_source_user_id, p_channel, p_external_subject) then
    update public.link_codes set consumed_at = now() where id = v_code_row.id;
    return jsonb_build_object('ok', false, 'reason', 'source_already_linked');
  end if;

  -- Two chat accounts each holding a code for this one web account hold
  -- different identity-row locks, so nothing above serializes them: both
  -- could pass the check below before either commits, and the loser's merge
  -- would then find the winner's identity already committed and apply the
  -- target-wins delete to its own instead of being refused. One lock per
  -- target, held to the end of the transaction, makes the check see the
  -- winner's identity. Taken after the identity-row lock everywhere, and
  -- nothing holding it waits on another link's row, so it can't deadlock.
  perform pg_advisory_xact_lock(hashtextextended('bj-link-target:' || v_code_row.user_id::text, 0));

  -- The new (user_id, channel) unique index would refuse this anyway; catch
  -- it here first for a clean reason instead of a raw 23505.
  if exists (
    select 1 from public.channel_identities
    where user_id = v_code_row.user_id and channel = p_channel
  ) then
    update public.link_codes set consumed_at = now() where id = v_code_row.id;
    return jsonb_build_object('ok', false, 'reason', 'target_linked_elsewhere');
  end if;

  update public.link_codes
    set consumed_at = now(), consumed_by_user_id = p_source_user_id, consumed_by_subject = p_external_subject
    where id = v_code_row.id;

  v_summary := public.merge_user_data(
    p_source_user_id, v_code_row.user_id, p_channel, p_external_subject
  );

  return jsonb_build_object(
    'ok', true, 'target_user_id', v_code_row.user_id, 'source_user_id', p_source_user_id, 'summary', v_summary
  );
end;
$$;
revoke execute on function public.consume_link_code(text, text, text, uuid) from public, anon, authenticated;
grant execute on function public.consume_link_code(text, text, text, uuid) to service_role;

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

  -- A concurrent finisher (the channel redelivering the /link) may have retired
  -- the source while this waited on the identity lock above; the check at the
  -- top ran before that and can't have seen it. Asked again after the wait,
  -- on a fresh snapshot, it does -- otherwise merge_user_data's guard would
  -- raise BJ004 about an account that is simply already gone.
  if not exists (select 1 from auth.users where id = p_source) then
    return jsonb_build_object('ok', true, 'source_gone', true);
  end if;

  -- Safe to call again: merge_user_data's own writes are all "where
  -- user_id = source", so once source has nothing left, every statement in
  -- it is a no-op and the completeness check passes trivially.
  return jsonb_build_object(
    'ok', true, 'source_gone', false,
    'summary', public.merge_user_data(p_source, p_target, p_channel, p_subject)
  );
end;
$$;
revoke execute on function public.finish_link_merge(text, text, uuid, uuid) from public, anon, authenticated;
grant execute on function public.finish_link_merge(text, text, uuid, uuid) to service_role;

-- ============================================================
-- discord_processed_interactions: the same interaction is never processed twice
-- ============================================================
--
-- claim_discord_interaction answers one of three things, like claim_telegram_update:
--   'claimed'     this delivery owns the interaction: process it.
--   'done'        an earlier delivery finished it: do nothing.
--   'in_progress' an earlier delivery claimed it and has not finished, and its lease has not run out.
-- complete_discord_interaction marks it done; release_discord_interaction deletes an unfinished claim.
-- The interaction id is a Discord snowflake: digits only, at most 25 of them, kept as text so no
-- id is ever out of range of an integer type.
--
-- The table is not readable or writable through the API by any role; the three functions are
-- SECURITY DEFINER and callable by the backend only.

create table public.discord_processed_interactions (
  interaction_id text primary key check (interaction_id ~ '^[0-9]{1,25}$'),
  claimed_at     timestamptz not null default now(),
  completed_at   timestamptz
);

create index discord_processed_interactions_claimed_at_idx
  on public.discord_processed_interactions (claimed_at);

alter table public.discord_processed_interactions enable row level security;

-- Prod's default privileges grant new tables to anon, authenticated and service_role; a fresh
-- stack grants nothing. Revoke by name so both agree.
revoke all on table public.discord_processed_interactions
  from public, anon, authenticated, service_role;

create function public.claim_discord_interaction(
  p_interaction_id text, p_lease_seconds integer default 900
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_rows integer;
  v_done boolean;
begin
  if p_interaction_id is null or p_interaction_id !~ '^[0-9]{1,25}$' then
    raise exception 'p_interaction_id must be a Discord id (1 to 25 digits)' using errcode = '22023';
  end if;
  if p_lease_seconds is null or p_lease_seconds < 1 or p_lease_seconds > 86400 then
    raise exception 'p_lease_seconds must be between 1 and 86400' using errcode = '22023';
  end if;

  delete from public.discord_processed_interactions
  where interaction_id in (
    select interaction_id from public.discord_processed_interactions
    where claimed_at < now() - interval '7 days'
    limit 200
  );

  -- One statement, so two deliveries of the same interaction arriving together are arbitrated by
  -- the primary-key index: the loser waits on the winner's row, re-evaluates the WHERE against the
  -- committed row (READ COMMITTED), and changes nothing.
  insert into public.discord_processed_interactions as t (interaction_id)
  values (p_interaction_id)
  on conflict (interaction_id) do update
    set claimed_at = now()
    where t.completed_at is null
      and t.claimed_at <= now() - make_interval(secs => p_lease_seconds);

  get diagnostics v_rows = row_count;
  if v_rows > 0 then
    return 'claimed';
  end if;

  select completed_at is not null into v_done
  from public.discord_processed_interactions
  where interaction_id = p_interaction_id;
  if v_done then
    return 'done';
  end if;
  -- An unfinished claim inside its lease -- or the row was released between the two statements.
  -- Either way: not done.
  return 'in_progress';
end;
$$;

create function public.complete_discord_interaction(p_interaction_id text)
returns void
language sql
security definer
set search_path = ''
as $$
  update public.discord_processed_interactions
  set completed_at = now()
  where interaction_id = p_interaction_id;
$$;

-- Only an unfinished claim can be released: a completed interaction stays recorded.
create function public.release_discord_interaction(p_interaction_id text)
returns void
language sql
security definer
set search_path = ''
as $$
  delete from public.discord_processed_interactions
  where interaction_id = p_interaction_id and completed_at is null;
$$;

revoke execute on function public.claim_discord_interaction(text, integer)
  from public, anon, authenticated;
grant execute on function public.claim_discord_interaction(text, integer) to service_role;
revoke execute on function public.complete_discord_interaction(text)
  from public, anon, authenticated;
grant execute on function public.complete_discord_interaction(text) to service_role;
revoke execute on function public.release_discord_interaction(text)
  from public, anon, authenticated;
grant execute on function public.release_discord_interaction(text) to service_role;

-- Revert (in this order). Discord identities and link codes written meanwhile stay in their tables;
-- the Telegram-only functions simply stop being able to merge or redeem them.
--
--   -- 1. the claim table and its functions
--   drop function public.claim_discord_interaction(text, integer);
--   drop function public.complete_discord_interaction(text);
--   drop function public.release_discord_interaction(text);
--   drop table public.discord_processed_interactions;
--
--   -- 2. the Telegram-only guard and merge come back first (create or replace):
--   --    is_auto_provisioned_telegram_user and merge_user_data(uuid, uuid, text) from
--   --    20260928000000_fix_merge_user_data.sql and
--   --    20261005162316_merge_user_data_handles_product_events_and_enrollments.sql (the latest
--   --    body of merge_user_data); then consume_link_code and finish_link_merge from
--   --    20260928000000_fix_merge_user_data.sql, which call them. Their grants are NOT
--   --    simply those the defining file states. The guard and the 3-argument merge are
--   --    recreated as NEW functions, and prod's default privileges grant EXECUTE on a new function
--   --    to service_role, so both are revoked from public, anon, authenticated AND
--   --    service_role (20260928000000 revoked only the first three from the guard, and
--   --    20260930181212_lock_link_helpers_from_service_role.sql had to take service_role away
--   --    afterwards; copying the older file's revoke would re-open that). consume_link_code and
--   --    finish_link_merge are revoked from public, anon, authenticated and granted to
--   --    service_role, as in 20260928000000.
--
--   -- 3. then the Discord versions go
--   drop function public.merge_user_data(uuid, uuid, text, text);
--   drop function public.is_auto_provisioned_channel_user(uuid, text, text);
