-- Follow60 Ordering V2: allow a variable evaluation barrier while preserving
-- the immutable hard maximum of ten complete V2 cycles.

do $$
declare
  v_constraint record;
begin
  for v_constraint in
    select c.conname
    from pg_catalog.pg_constraint c
    where c.conrelid = 'public.follow60_ordering_v2_behavioral_controls'::regclass
      and c.contype = 'c'
      and pg_catalog.pg_get_constraintdef(c.oid) ~ 'max_v2_cycles[^)]*= 10'
  loop
    execute format(
      'alter table public.follow60_ordering_v2_behavioral_controls drop constraint %I',
      v_constraint.conname
    );
  end loop;

  if not exists (
    select 1
    from pg_catalog.pg_constraint c
    where c.conrelid = 'public.follow60_ordering_v2_behavioral_controls'::regclass
      and c.conname = 'follow60_ordering_v2_behavioral_controls_max_v2_cycles_range'
  ) then
    alter table public.follow60_ordering_v2_behavioral_controls
      add constraint follow60_ordering_v2_behavioral_controls_max_v2_cycles_range
      check (max_v2_cycles between 1 and 10);
  end if;
end;
$$;

do $$
begin
  if to_regprocedure(
    'public.arm_follow60_ordering_v2_behavioral_control_v1(uuid,text,timestamptz,jsonb)'
  ) is not null then
    revoke all on function public.arm_follow60_ordering_v2_behavioral_control_v1(
      uuid, text, timestamptz, jsonb
    ) from public, anon, authenticated, service_role;
    drop function public.arm_follow60_ordering_v2_behavioral_control_v1(
      uuid, text, timestamptz, jsonb
    );
  end if;
end;
$$;

create or replace function public.arm_follow60_ordering_v2_behavioral_control_v1(
  p_account_id uuid,
  p_expected_worker_sha text,
  p_expires_at timestamptz,
  p_baseline jsonb default '{}'::jsonb,
  p_requested_max_v2_cycles integer default 10
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_control public.follow60_ordering_v2_behavioral_controls%rowtype;
  v_requested_max integer := coalesce(p_requested_max_v2_cycles, 10);
  v_hard_max constant integer := 10;
  v_configured_day_cap integer;
  v_configured_session_cap integer;
  v_package_day_cap integer;
  v_package_session_cap integer;
  v_warmup_day_cap integer;
  v_preview_day_cap integer;
  v_preview_session_cap integer;
  v_canonical_day_cap integer;
  v_canonical_session_cap integer;
  v_consumed_today integer;
  v_canonical_remaining integer;
  v_effective_max integer;
  v_resolved_baseline jsonb;
begin
  if p_account_id is null then
    return jsonb_build_object('ok', false, 'reason', 'v2_account_invalid');
  end if;
  if lower(coalesce(p_expected_worker_sha, '')) !~ '^[0-9a-f]{40}$' then
    return jsonb_build_object('ok', false, 'reason', 'v2_pre_run_identity_invalid');
  end if;
  if p_expires_at is null or p_expires_at <= now() then
    return jsonb_build_object('ok', false, 'reason', 'v2_control_expiry_invalid');
  end if;
  if v_requested_max < 1 then
    return jsonb_build_object('ok', false, 'reason', 'v2_requested_max_invalid');
  end if;

  -- The account row is the serialization boundary for concurrent arm calls.
  perform 1
  from public.ig_accounts
  where id = p_account_id
  for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'v2_account_not_found');
  end if;

  select
    nullif(s.max_actions_per_day, 0),
    coalesce(nullif(s.follow_limit, 0), nullif(s.max_follow_per_run, 0)),
    nullif(aps.package_caps ->> 'follow_day', '')::integer,
    nullif(aps.package_caps ->> 'follow_session', '')::integer,
    nullif(aps.effective_caps_preview ->> 'warmup_follow_day_cap', '')::integer,
    nullif(aps.effective_caps_preview ->> 'follow_day', '')::integer,
    nullif(aps.effective_caps_preview ->> 'follow_session', '')::integer
  into
    v_configured_day_cap,
    v_configured_session_cap,
    v_package_day_cap,
    v_package_session_cap,
    v_warmup_day_cap,
    v_preview_day_cap,
    v_preview_session_cap
  from public.ig_account_settings s
  join public.account_package_summary aps on aps.account_id = s.account_id
  where s.account_id = p_account_id;

  if not found then
    return jsonb_build_object('ok', false, 'reason', 'v2_canonical_follow_quota_unavailable');
  end if;

  select min(cap_value)
  into v_canonical_day_cap
  from unnest(array[
    v_configured_day_cap,
    v_package_day_cap,
    v_warmup_day_cap,
    v_preview_day_cap
  ]) as caps(cap_value)
  where cap_value > 0;

  select min(cap_value)
  into v_canonical_session_cap
  from unnest(array[
    v_configured_session_cap,
    v_package_session_cap,
    v_warmup_day_cap,
    v_preview_session_cap
  ]) as caps(cap_value)
  where cap_value > 0;

  if v_canonical_day_cap is null or v_canonical_session_cap is null then
    return jsonb_build_object('ok', false, 'reason', 'v2_canonical_follow_quota_unavailable');
  end if;

  select count(*)::integer
  into v_consumed_today
  from public.ig_interaction_events e
  where e.account_id = p_account_id
    and e.interaction_type = 'follow'
    and e.interaction_status = 'success'
    and e.event_type in ('follow_verified', 'follow_verified_persisted_v1')
    and e.run_id is not null
    and (e.event_at at time zone 'Africa/Johannesburg')::date
      = (now() at time zone 'Africa/Johannesburg')::date;

  v_canonical_remaining := least(
    greatest(0, v_canonical_day_cap - v_consumed_today),
    v_canonical_session_cap
  );
  if v_canonical_remaining <= 0 then
    return jsonb_build_object(
      'ok', false,
      'reason', 'v2_canonical_follow_quota_exhausted',
      'canonical_follow_remaining_at_arm', v_canonical_remaining,
      'canonical_follow_consumed_at_arm', v_consumed_today,
      'canonical_follow_day_cap', v_canonical_day_cap,
      'canonical_follow_session_cap', v_canonical_session_cap
    );
  end if;

  v_effective_max := least(v_requested_max, v_canonical_remaining, v_hard_max);
  v_resolved_baseline := coalesce(p_baseline, '{}'::jsonb) || jsonb_build_object(
    'baseline_follow_count', v_consumed_today,
    'requested_max_v2_cycles', v_requested_max,
    'resolved_max_v2_cycles', v_effective_max,
    'canonical_follow_remaining_at_arm', v_canonical_remaining,
    'canonical_follow_consumed_at_arm', v_consumed_today,
    'canonical_follow_day_cap', v_canonical_day_cap,
    'canonical_follow_session_cap', v_canonical_session_cap,
    'behavioral_canary_hard_max', v_hard_max,
    'canonical_follow_remaining_source',
      'min(configured,package,warmup,effective_preview,session)-verified_sast_follows'
  );

  select *
  into v_control
  from public.follow60_ordering_v2_behavioral_controls
  where account_id = p_account_id
    and status in ('armed', 'running', 'barrier_reached')
  for update;

  if found and v_control.expires_at <= now() then
    update public.follow60_ordering_v2_behavioral_controls
    set status = 'expired', terminal_reason = 'control_expired', updated_at = now()
    where control_id = v_control.control_id;
    v_control := null;
  elsif found then
    return jsonb_build_object(
      'ok',
        v_control.status = 'armed'
        and v_control.expected_worker_sha = lower(p_expected_worker_sha)
        and v_control.max_v2_cycles = v_effective_max
        and coalesce((v_control.baseline ->> 'requested_max_v2_cycles')::integer, 10)
          = v_requested_max,
      'reason', case
        when v_control.status = 'armed'
          and v_control.expected_worker_sha = lower(p_expected_worker_sha)
          and v_control.max_v2_cycles = v_effective_max
          and coalesce((v_control.baseline ->> 'requested_max_v2_cycles')::integer, 10)
            = v_requested_max
        then 'v2_pre_run_control_idempotent'
        else 'v2_active_control_collision'
      end,
      'control_id', v_control.control_id,
      'status', v_control.status,
      'binding_consumed', v_control.binding_consumed,
      'requested_max_v2_cycles',
        coalesce((v_control.baseline ->> 'requested_max_v2_cycles')::integer, 10),
      'canonical_follow_remaining_at_arm',
        (v_control.baseline ->> 'canonical_follow_remaining_at_arm')::integer,
      'behavioral_canary_hard_max',
        coalesce((v_control.baseline ->> 'behavioral_canary_hard_max')::integer, 10),
      'max_v2_cycles', v_control.max_v2_cycles
    );
  end if;

  insert into public.follow60_ordering_v2_behavioral_controls(
    account_id,
    expected_worker_sha,
    expires_at,
    baseline,
    max_v2_cycles
  ) values (
    p_account_id,
    lower(p_expected_worker_sha),
    p_expires_at,
    v_resolved_baseline,
    v_effective_max
  )
  returning * into v_control;

  return jsonb_build_object(
    'ok', true,
    'reason', case
      when v_effective_max < v_requested_max then 'v2_pre_run_control_armed_bounded'
      else 'v2_pre_run_control_armed'
    end,
    'control_id', v_control.control_id,
    'account_id', v_control.account_id,
    'expected_worker_sha', v_control.expected_worker_sha,
    'canary_type', v_control.canary_type,
    'requested_max_v2_cycles', v_requested_max,
    'canonical_follow_remaining_at_arm', v_canonical_remaining,
    'canonical_follow_consumed_at_arm', v_consumed_today,
    'canonical_follow_day_cap', v_canonical_day_cap,
    'canonical_follow_session_cap', v_canonical_session_cap,
    'behavioral_canary_hard_max', v_hard_max,
    'max_v2_cycles', v_control.max_v2_cycles,
    'baseline', v_control.baseline,
    'status', v_control.status,
    'run_id', null,
    'request_id', null,
    'business_session_id', null,
    'binding_consumed', false,
    'expires_at_epoch_s', extract(epoch from v_control.expires_at)
  );
end;
$$;

revoke all on function public.arm_follow60_ordering_v2_behavioral_control_v1(
  uuid, text, timestamptz, jsonb, integer
) from public, anon, authenticated;

grant execute on function public.arm_follow60_ordering_v2_behavioral_control_v1(
  uuid, text, timestamptz, jsonb, integer
) to service_role;

comment on function public.arm_follow60_ordering_v2_behavioral_control_v1(
  uuid, text, timestamptz, jsonb, integer
) is
  'Arms one Follow60 Ordering V2 control with a server-resolved barrier bounded by canonical SAST Follow remaining and hard max 10.';
