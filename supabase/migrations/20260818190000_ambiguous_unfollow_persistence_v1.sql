-- P0C: idempotent canonical persistence for a verified or reconciled Unfollow.
-- Physical Instagram execution remains at-least-once; this RPC guarantees
-- exactly-once canonical projection for one deterministic action id.

create or replace function public.persist_verified_unfollow_success_v1(
  p_action_id uuid,
  p_account_id uuid,
  p_run_id uuid,
  p_request_id uuid,
  p_candidate_username text,
  p_interaction_row_id uuid,
  p_attempted_at timestamptz,
  p_business_date date,
  p_unfollow_mode text,
  p_verification_method text,
  p_metadata_safe jsonb default '{}'::jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_username text := lower(trim(leading '@' from coalesce(p_candidate_username, '')));
  v_row public.ig_interacted_users%rowtype;
  v_event public.ig_interaction_events%rowtype;
  v_attempted_at timestamptz := coalesce(p_attempted_at, now());
  v_metadata jsonb := coalesce(p_metadata_safe, '{}'::jsonb);
begin
  if p_action_id is null or p_account_id is null or p_run_id is null
     or p_request_id is null or p_interaction_row_id is null or v_username = '' then
    raise exception 'unfollow_persistence_identity_incomplete';
  end if;
  if jsonb_typeof(v_metadata) <> 'object' then
    raise exception 'unfollow_persistence_metadata_not_object';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(p_action_id::text, 0));

  select * into v_event
  from public.ig_interaction_events
  where id = p_action_id;
  if found then
    if v_event.account_id is distinct from p_account_id
       or v_event.run_id is distinct from p_run_id
       or lower(trim(leading '@' from coalesce(v_event.username, ''))) <> v_username
       or lower(coalesce(v_event.interaction_type, '')) <> 'unfollow' then
      raise exception 'unfollow_persistence_action_id_binding_mismatch';
    end if;
    return jsonb_build_object(
      'ok', true,
      'status', 'idempotent_replay',
      'action_id', p_action_id,
      'interaction_row_id', p_interaction_row_id,
      'counter_delta', 0,
      'business_date', p_business_date,
      'invariants_confirmed', true
    );
  end if;

  select * into v_row
  from public.ig_interacted_users
  where id = p_interaction_row_id
    and account_id = p_account_id
    and lower(trim(leading '@' from username)) = v_username
  for update;
  if not found then
    raise exception 'unfollow_persistence_interaction_binding_missing';
  end if;

  update public.ig_interacted_users
  set unfollow_attempts = coalesce(unfollow_attempts, 0) + 1,
      last_unfollow_attempt_at = v_attempted_at,
      unfollow_result = 'success',
      unfollow_mode_applied = left(coalesce(p_unfollow_mode, ''), 120),
      run_id = p_run_id,
      last_run_id = p_run_id,
      unfollowed_at = v_attempted_at,
      unfollowed = true,
      followed = false,
      follow_status = 'unfollowed',
      interaction_lifecycle_state = 'unfollowed_completed',
      interaction_status = 'success',
      last_interaction_at = v_attempted_at,
      was_successful = true,
      unfollow_skip_reason = null,
      updated_at = now()
  where id = p_interaction_row_id;

  insert into public.ig_interaction_events (
    id, account_id, run_id, request_id, username,
    event_type, event_status, event_reason, event_at,
    interaction_type, interaction_status,
    evidence_source, evidence_confidence, evidence_summary,
    metadata_safe, payload
  ) values (
    p_action_id, p_account_id, p_run_id, p_request_id, v_username,
    'unfollow_verified', 'success', null, v_attempted_at,
    'unfollow', 'success',
    p_verification_method, 'high', 'Exact target identity and fresh relationship state verified',
    v_metadata,
    jsonb_build_object(
      'interaction_row_id', p_interaction_row_id,
      'business_date', p_business_date,
      'unfollow_mode', p_unfollow_mode,
      'verification_method', p_verification_method
    )
  );

  return jsonb_build_object(
    'ok', true,
    'status', 'persisted',
    'action_id', p_action_id,
    'interaction_row_id', p_interaction_row_id,
    'counter_delta', 1,
    'business_date', p_business_date,
    'invariants_confirmed', true
  );
end;
$$;

revoke all on function public.persist_verified_unfollow_success_v1(
  uuid, uuid, uuid, uuid, text, uuid, timestamptz, date, text, text, jsonb
) from public, anon, authenticated;
grant execute on function public.persist_verified_unfollow_success_v1(
  uuid, uuid, uuid, uuid, text, uuid, timestamptz, date, text, text, jsonb
) to service_role;
