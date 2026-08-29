-- Transactional account merge + /link code consumption (Sprint 2.8d) --
-- the "merge story": a Telegram identity (auto-provisioned or not) that
-- redeems a valid /link code has every user-owned row it has accumulated
-- reparented to the target web account, in one transaction.
--
-- `merge_user_data` is a straightforward `UPDATE ... SET user_id = target
-- WHERE user_id = source` per table. No conflict-resolution policy is
-- implemented -- Postgres's own unique constraints ARE the policy: a
-- genuine collision (both accounts already have, say, a provider
-- credential for the same service/provider) raises unique_violation and
-- rolls back the ENTIRE merge, not just that one table. Nothing is
-- silently overwritten or dropped; the caller sees a real error and the
-- accounts stay exactly as they were. "Unknown means labeled as unknown,
-- never guessed" (master plan) -- a genuine data collision between two
-- populated accounts has no algorithmic right answer, so this doesn't
-- invent one.
--
-- Every table with a user_id column gets reparented: profile_versions,
-- career_facts, applications, application_events, event_outbox,
-- working_sets, artifact_versions, provider_credentials,
-- capability_preferences, channel_identities, link_codes.
-- `link_code_attempts` is excluded -- it's keyed by (channel,
-- external_subject), not user_id, so there's nothing to reparent.
--
-- `consume_link_code` is the transactional step 5 of Proposal §14's
-- Telegram-linking flow ("the server consumes the code transactionally
-- and creates the channel identity"), extended to also run the merge in
-- the SAME transaction -- hashing, matching, expiry-checking, and
-- consuming the code all have to happen atomically with the merge itself
-- to avoid a race where two concurrent requests both think they
-- successfully redeemed the same code. It returns a JSONB result rather
-- than raising for the three EXPECTED failure modes (wrong code, expired
-- code, rate-limited) -- those are normal outcomes a bot conversation
-- should handle gracefully, not exceptional ones. A genuine merge
-- collision, by contrast, raises a real Postgres exception (propagated
-- to the caller as an unhandled unique_violation) -- that failure mode
-- IS exceptional, and Python maps it to a clean CONFLICT ApiError rather
-- than exposing the raw constraint name.
--
-- Both functions: SECURITY DEFINER, revoked from public/anon/
-- authenticated in this same migration -- Sprint 2.6d's lesson, applied
-- from the start every time since.

create function public.merge_user_data(p_source_user_id uuid, p_target_user_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_summary jsonb := '{}'::jsonb;
  v_count integer;
begin
  if p_source_user_id = p_target_user_id then
    return '{}'::jsonb;
  end if;

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

  update public.event_outbox set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('event_outbox', v_count);

  update public.working_sets set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('working_sets', v_count);

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

  update public.link_codes set user_id = p_target_user_id where user_id = p_source_user_id;
  get diagnostics v_count = row_count;
  v_summary := v_summary || jsonb_build_object('link_codes', v_count);

  return v_summary;
end;
$$;

revoke execute on function public.merge_user_data(uuid, uuid) from public, anon, authenticated;

create function public.consume_link_code(
  p_channel text,
  p_external_subject text,
  p_code text,
  p_source_user_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  v_lockout record;
  v_code_row record;
  v_summary jsonb;
  v_reason text;
  v_max_attempts constant integer := 5;
  v_lockout_minutes constant integer := 15;
begin
  select * into v_lockout from public.link_code_attempts
  where channel = p_channel and external_subject = p_external_subject
  for update;

  if found and v_lockout.locked_until is not null and v_lockout.locked_until > now() then
    return jsonb_build_object('ok', false, 'reason', 'rate_limited', 'locked_until', v_lockout.locked_until);
  end if;

  select * into v_code_row from public.link_codes
  where channel = p_channel
    and code_hash = encode(digest(p_code, 'sha256'), 'hex')
    and consumed_at is null
  for update;

  if not found then
    v_reason := 'invalid_code';
  elsif v_code_row.expires_at < now() then
    v_reason := 'expired_code';
  else
    v_reason := null;
  end if;

  if v_reason is not null then
    insert into public.link_code_attempts (channel, external_subject, failed_count, updated_at)
    values (p_channel, p_external_subject, 1, now())
    on conflict (channel, external_subject) do update
    set failed_count = link_code_attempts.failed_count + 1,
        locked_until = case
          when link_code_attempts.failed_count + 1 >= v_max_attempts
            then now() + (v_lockout_minutes || ' minutes')::interval
          else link_code_attempts.locked_until
        end,
        updated_at = now();
    return jsonb_build_object('ok', false, 'reason', v_reason);
  end if;

  update public.link_codes set consumed_at = now() where id = v_code_row.id;

  delete from public.link_code_attempts
  where channel = p_channel and external_subject = p_external_subject;

  v_summary := public.merge_user_data(p_source_user_id, v_code_row.user_id);

  return jsonb_build_object('ok', true, 'target_user_id', v_code_row.user_id, 'summary', v_summary);
end;
$$;

revoke execute on function public.consume_link_code(text, text, text, uuid)
  from public, anon, authenticated;
