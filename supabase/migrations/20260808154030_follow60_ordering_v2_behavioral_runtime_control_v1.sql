-- Dormant source contract for the Follow60 Ordering V2 behavioral canary.
-- Production activation and data creation are intentionally separate GOs.

create table if not exists public.follow60_ordering_v2_behavioral_controls (
  control_id uuid primary key default gen_random_uuid(),
  account_id uuid not null,
  expected_worker_sha text not null check (expected_worker_sha ~ '^[0-9a-f]{40}$'),
  actual_worker_sha text,
  canary_type text not null default 'FOLLOW60_ORDERING_V2_BEHAVIORAL_CANARY_V1',
  max_v2_cycles integer not null default 10 check (max_v2_cycles = 10),
  baseline jsonb not null default '{}'::jsonb,
  expires_at timestamptz not null,
  status text not null default 'armed'
    check (status in ('armed','running','barrier_reached','completed','stopped','failed','canceled','expired')),
  run_id uuid,
  request_id uuid,
  business_session_id uuid,
  attempt_id integer,
  binding_consumed boolean not null default false,
  lease_id uuid,
  lease_nonce text,
  lease_expires_at timestamptz,
  claimed_at timestamptz,
  candidate_seen_count integer not null default 0 check (candidate_seen_count >= 0),
  v2_selected_count integer not null default 0 check (v2_selected_count >= 0),
  v2_complete_count integer not null default 0 check (v2_complete_count between 0 and 10),
  v2_partial_count integer not null default 0 check (v2_partial_count >= 0),
  v1_fallback_count integer not null default 0 check (v1_fallback_count >= 0),
  terminal_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  constraint follow60_ordering_v2_bound_identity_all_or_none check (
    (run_id is null and request_id is null and business_session_id is null and attempt_id is null
      and lease_id is null and lease_nonce is null and claimed_at is null and binding_consumed = false)
    or
    (run_id is not null and request_id is not null and business_session_id is not null and attempt_id >= 1
      and lease_id is not null and nullif(trim(lease_nonce), '') is not null and claimed_at is not null
      and binding_consumed = true)
  )
);

create unique index if not exists follow60_ordering_v2_one_active_control_per_account
  on public.follow60_ordering_v2_behavioral_controls(account_id)
  where status in ('armed','running','barrier_reached');

create unique index if not exists follow60_ordering_v2_one_runtime_binding
  on public.follow60_ordering_v2_behavioral_controls(run_id, request_id, business_session_id)
  where run_id is not null;

create table if not exists public.follow60_ordering_v2_behavioral_events (
  event_id bigint generated always as identity primary key,
  control_id uuid not null references public.follow60_ordering_v2_behavioral_controls(control_id),
  account_id uuid not null,
  run_id uuid not null,
  request_id uuid not null,
  business_session_id uuid not null,
  action_id text not null check (nullif(trim(action_id), '') is not null),
  event_kind text not null check (event_kind in (
    'candidate_seen','v2_selected','v2_complete','v2_partial','v1_fallback'
  )),
  metadata_safe jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(control_id, action_id, event_kind)
);

alter table public.follow60_ordering_v2_behavioral_controls enable row level security;
alter table public.follow60_ordering_v2_behavioral_events enable row level security;

revoke all on public.follow60_ordering_v2_behavioral_controls from public, anon, authenticated;
revoke all on public.follow60_ordering_v2_behavioral_events from public, anon, authenticated;
grant select, insert, update on public.follow60_ordering_v2_behavioral_controls to service_role;
grant select, insert on public.follow60_ordering_v2_behavioral_events to service_role;
grant usage, select on sequence public.follow60_ordering_v2_behavioral_events_event_id_seq to service_role;

create or replace function public.arm_follow60_ordering_v2_behavioral_control_v1(
  p_account_id uuid,
  p_expected_worker_sha text,
  p_expires_at timestamptz,
  p_baseline jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_control public.follow60_ordering_v2_behavioral_controls%rowtype;
begin
  if p_account_id is null or lower(coalesce(p_expected_worker_sha, '')) !~ '^[0-9a-f]{40}$' then
    return jsonb_build_object('ok', false, 'reason', 'v2_pre_run_identity_invalid');
  end if;
  if p_expires_at <= now() then
    return jsonb_build_object('ok', false, 'reason', 'v2_control_expiry_invalid');
  end if;
  select * into v_control
  from public.follow60_ordering_v2_behavioral_controls
  where account_id = p_account_id and status in ('armed','running','barrier_reached')
  for update;
  if found then
    return jsonb_build_object(
      'ok', v_control.status = 'armed' and v_control.expected_worker_sha = lower(p_expected_worker_sha),
      'reason', case when v_control.status = 'armed' and v_control.expected_worker_sha = lower(p_expected_worker_sha)
        then 'v2_pre_run_control_idempotent' else 'v2_active_control_collision' end,
      'control_id', v_control.control_id,
      'status', v_control.status,
      'binding_consumed', v_control.binding_consumed
    );
  end if;
  insert into public.follow60_ordering_v2_behavioral_controls(
    account_id, expected_worker_sha, expires_at, baseline
  ) values (
    p_account_id, lower(p_expected_worker_sha), p_expires_at, coalesce(p_baseline, '{}'::jsonb)
  ) returning * into v_control;
  return jsonb_build_object(
    'ok', true, 'reason', 'v2_pre_run_control_armed',
    'control_id', v_control.control_id, 'account_id', v_control.account_id,
    'expected_worker_sha', v_control.expected_worker_sha,
    'canary_type', v_control.canary_type, 'max_v2_cycles', v_control.max_v2_cycles,
    'baseline', v_control.baseline, 'status', v_control.status,
    'run_id', null, 'request_id', null, 'business_session_id', null,
    'binding_consumed', false, 'expires_at_epoch_s', extract(epoch from v_control.expires_at)
  );
end;
$$;

create or replace function public.claim_follow60_ordering_v2_behavioral_binding_v1(
  p_account_id uuid,
  p_actual_worker_sha text,
  p_run_id uuid,
  p_request_id uuid,
  p_business_session_id uuid,
  p_attempt_id integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_control public.follow60_ordering_v2_behavioral_controls%rowtype;
  v_request public.account_run_requests%rowtype;
  v_run public.ig_runs%rowtype;
begin
  select * into v_control
  from public.follow60_ordering_v2_behavioral_controls
  where account_id = p_account_id and status in ('armed','running','barrier_reached')
  for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'v2_control_not_found');
  end if;
  if v_control.status = 'barrier_reached' then
    return jsonb_build_object(
      'ok', true, 'reason', 'v2_cycle_barrier_reached', 'barrier_reached', true,
      'control_id', v_control.control_id, 'status', v_control.status,
      'v2_complete_count', v_control.v2_complete_count, 'max_new_cycles', v_control.max_v2_cycles
    );
  end if;
  if v_control.binding_consumed then
    if v_control.run_id = p_run_id and v_control.request_id = p_request_id
       and v_control.business_session_id = p_business_session_id
       and v_control.attempt_id = p_attempt_id and v_control.actual_worker_sha = lower(p_actual_worker_sha) then
      return jsonb_build_object(
        'ok', true, 'reason', 'v2_runtime_binding_idempotent', 'schema', v_control.canary_type,
        'canary_type', v_control.canary_type, 'control_id', v_control.control_id,
        'account_id', v_control.account_id, 'run_id', v_control.run_id,
        'request_id', v_control.request_id, 'business_session_id', v_control.business_session_id,
        'attempt_id', v_control.attempt_id, 'expected_worker_sha', v_control.expected_worker_sha,
        'actual_worker_sha', v_control.actual_worker_sha, 'max_new_cycles', v_control.max_v2_cycles,
        'binding_consumed', v_control.binding_consumed,
        'baseline_follow_count', coalesce((v_control.baseline->>'baseline_follow_count')::integer, 0),
        'baseline', v_control.baseline, 'expires_at_epoch_s', extract(epoch from v_control.expires_at),
        'lease_id', v_control.lease_id, 'lease_nonce', v_control.lease_nonce,
        'lease_expires_at_epoch_s', extract(epoch from v_control.lease_expires_at),
        'claimed_at_epoch_s', extract(epoch from v_control.claimed_at), 'status', v_control.status,
        'candidate_seen_count', v_control.candidate_seen_count,
        'v2_selected_count', v_control.v2_selected_count, 'v2_complete_count', v_control.v2_complete_count,
        'v2_partial_count', v_control.v2_partial_count, 'v1_fallback_count', v_control.v1_fallback_count
      );
    end if;
    return jsonb_build_object('ok', false, 'reason', 'v2_runtime_binding_already_consumed');
  end if;
  if v_control.expires_at <= now() then
    update public.follow60_ordering_v2_behavioral_controls
    set status='expired', updated_at=now(), terminal_reason='control_expired'
    where control_id=v_control.control_id;
    return jsonb_build_object('ok', false, 'reason', 'v2_control_expired');
  end if;
  if v_control.expected_worker_sha <> lower(coalesce(p_actual_worker_sha, '')) then
    return jsonb_build_object('ok', false, 'reason', 'v2_expected_worker_sha_mismatch');
  end if;
  if p_attempt_id < 1 then
    return jsonb_build_object('ok', false, 'reason', 'v2_attempt_id_invalid');
  end if;
  select * into v_request from public.account_run_requests where id=p_request_id for share;
  if not found or v_request.account_id <> p_account_id or v_request.run_id <> p_run_id
     or v_request.requested_run_type <> 'account_session'
     or coalesce((v_request.metadata_safe->>'manual_start')::boolean, false) is not true
     or lower(coalesce(v_request.metadata_safe->>'trigger', '')) <> 'manual' then
    return jsonb_build_object('ok', false, 'reason', 'v2_manual_play_request_mismatch');
  end if;
  select * into v_run from public.ig_runs where id=p_run_id for share;
  if not found or v_run.account_id <> p_account_id then
    return jsonb_build_object('ok', false, 'reason', 'v2_run_account_mismatch');
  end if;
  update public.follow60_ordering_v2_behavioral_controls
  set status='running', actual_worker_sha=lower(p_actual_worker_sha), run_id=p_run_id,
      request_id=p_request_id, business_session_id=p_business_session_id,
      attempt_id=p_attempt_id, binding_consumed=true, lease_id=extensions.gen_random_uuid(),
      lease_nonce=encode(extensions.gen_random_bytes(24), 'hex'),
      lease_expires_at=least(expires_at, now()+interval '12 hours'),
      claimed_at=now(), updated_at=now()
  where control_id=v_control.control_id returning * into v_control;
  return public.claim_follow60_ordering_v2_behavioral_binding_v1(
    p_account_id, p_actual_worker_sha, p_run_id, p_request_id, p_business_session_id, p_attempt_id
  );
end;
$$;

create or replace function public.record_follow60_ordering_v2_behavioral_event_v1(
  p_control_id uuid, p_account_id uuid, p_run_id uuid, p_request_id uuid,
  p_business_session_id uuid, p_attempt_id integer, p_lease_id uuid,
  p_lease_nonce text, p_worker_sha text, p_action_id text, p_event_kind text,
  p_metadata_safe jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_control public.follow60_ordering_v2_behavioral_controls%rowtype;
  v_inserted boolean := false;
  v_row_count integer := 0;
begin
  select * into v_control from public.follow60_ordering_v2_behavioral_controls
  where control_id=p_control_id for update;
  if not found or v_control.account_id<>p_account_id or v_control.run_id<>p_run_id
     or v_control.request_id<>p_request_id or v_control.business_session_id<>p_business_session_id
     or v_control.attempt_id<>p_attempt_id or v_control.lease_id<>p_lease_id
     or v_control.lease_nonce<>p_lease_nonce or v_control.actual_worker_sha<>lower(p_worker_sha) then
    return jsonb_build_object('ok', false, 'reason', 'v2_runtime_binding_mismatch');
  end if;
  if v_control.status <> 'running' or v_control.lease_expires_at <= now() then
    return jsonb_build_object('ok', false, 'reason', case when v_control.status='barrier_reached'
      then 'v2_cycle_barrier_reached' else 'v2_runtime_binding_inactive' end,
      'barrier_reached', v_control.status='barrier_reached',
      'v2_complete_count', v_control.v2_complete_count);
  end if;
  if p_event_kind not in ('candidate_seen','v2_selected','v2_complete','v2_partial','v1_fallback')
     or nullif(trim(p_action_id),'') is null then
    return jsonb_build_object('ok', false, 'reason', 'v2_event_invalid');
  end if;
  if p_event_kind='v2_complete' and v_control.v2_complete_count >= v_control.max_v2_cycles then
    return jsonb_build_object('ok', false, 'reason', 'v2_cycle_barrier_reached',
      'barrier_reached', true, 'v2_complete_count', v_control.v2_complete_count);
  end if;
  if p_event_kind='v2_complete' and exists (
    select 1 from public.follow60_ordering_v2_behavioral_events
    where control_id=p_control_id and action_id=p_action_id and event_kind='v2_partial'
  ) then
    return jsonb_build_object('ok', false, 'reason', 'v2_partial_cannot_complete');
  end if;
  if p_event_kind='v2_partial' and exists (
    select 1 from public.follow60_ordering_v2_behavioral_events
    where control_id=p_control_id and action_id=p_action_id and event_kind='v2_complete'
  ) then
    return jsonb_build_object('ok', false, 'reason', 'v2_complete_cannot_become_partial');
  end if;
  insert into public.follow60_ordering_v2_behavioral_events(
    control_id,account_id,run_id,request_id,business_session_id,action_id,event_kind,metadata_safe
  ) values (
    p_control_id,p_account_id,p_run_id,p_request_id,p_business_session_id,trim(p_action_id),p_event_kind,coalesce(p_metadata_safe,'{}'::jsonb)
  ) on conflict (control_id,action_id,event_kind) do nothing;
  get diagnostics v_row_count = row_count;
  v_inserted := v_row_count = 1;
  if v_inserted then
    update public.follow60_ordering_v2_behavioral_controls set
      candidate_seen_count=candidate_seen_count+(p_event_kind='candidate_seen')::int,
      v2_selected_count=v2_selected_count+(p_event_kind='v2_selected')::int,
      v2_complete_count=v2_complete_count+(p_event_kind='v2_complete')::int,
      v2_partial_count=v2_partial_count+(p_event_kind='v2_partial')::int,
      v1_fallback_count=v1_fallback_count+(p_event_kind='v1_fallback')::int,
      updated_at=now()
    where control_id=p_control_id returning * into v_control;
    if v_control.v2_complete_count = v_control.max_v2_cycles then
      update public.follow60_ordering_v2_behavioral_controls
      set status='barrier_reached', completed_at=now(), lease_expires_at=now(),
          terminal_reason='v2_complete_barrier_reached', updated_at=now()
      where control_id=p_control_id returning * into v_control;
    end if;
  else
    select * into v_control from public.follow60_ordering_v2_behavioral_controls where control_id=p_control_id;
  end if;
  return jsonb_build_object(
    'ok', true, 'reason', case when v_inserted then 'v2_event_recorded' else 'v2_event_replay_idempotent' end,
    'duplicate', not v_inserted, 'status', v_control.status,
    'barrier_reached', v_control.status='barrier_reached',
    'candidate_seen_count',v_control.candidate_seen_count,'v2_selected_count',v_control.v2_selected_count,
    'v2_complete_count',v_control.v2_complete_count,'v2_partial_count',v_control.v2_partial_count,
    'v1_fallback_count',v_control.v1_fallback_count,'max_new_cycles',v_control.max_v2_cycles
  );
end;
$$;

create or replace function public.terminalize_follow60_ordering_v2_behavioral_control_v1(
  p_account_id uuid, p_run_id uuid, p_request_id uuid,
  p_terminal_status text, p_reason text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_control public.follow60_ordering_v2_behavioral_controls%rowtype;
  v_status text;
  v_new_partial_count integer := 0;
begin
  v_status := case lower(coalesce(p_terminal_status,''))
    when 'completed' then 'completed' when 'stopped' then 'stopped'
    when 'canceled' then 'stopped' when 'failed' then 'failed'
    when 'blocked' then 'failed' when 'aborted' then 'failed' else 'failed' end;
  select * into v_control from public.follow60_ordering_v2_behavioral_controls
  where account_id=p_account_id and run_id=p_run_id and request_id=p_request_id for update;
  if not found then return jsonb_build_object('ok',true,'reason','v2_control_not_bound_noop'); end if;
  if v_control.status in ('completed','stopped','failed','canceled','expired') then
    return jsonb_build_object('ok',true,'reason','v2_control_terminal_idempotent',
      'status',v_control.status,'v2_complete_count',v_control.v2_complete_count);
  end if;
  with inserted as (
    insert into public.follow60_ordering_v2_behavioral_events(
      control_id,account_id,run_id,request_id,business_session_id,action_id,event_kind,metadata_safe
    )
    select v_control.control_id,v_control.account_id,v_control.run_id,v_control.request_id,
      v_control.business_session_id,selected.action_id,'v2_partial',
      jsonb_build_object('reason','run_terminal_before_v2_complete')
    from public.follow60_ordering_v2_behavioral_events selected
    where selected.control_id=v_control.control_id and selected.event_kind='v2_selected'
      and not exists (
        select 1 from public.follow60_ordering_v2_behavioral_events terminal
        where terminal.control_id=selected.control_id and terminal.action_id=selected.action_id
          and terminal.event_kind in ('v2_complete','v2_partial')
      )
    on conflict (control_id,action_id,event_kind) do nothing
    returning 1
  ) select count(*) into v_new_partial_count from inserted;
  if v_control.status='barrier_reached' then v_status := 'completed'; end if;
  update public.follow60_ordering_v2_behavioral_controls set status=v_status,
    v2_partial_count=v2_partial_count+v_new_partial_count,
    terminal_reason=left(coalesce(p_reason,'run_terminal'),160),
    lease_expires_at=now(), completed_at=coalesce(completed_at,now()), updated_at=now()
  where control_id=v_control.control_id returning * into v_control;
  return jsonb_build_object('ok',true,'reason','v2_control_terminalized','status',v_control.status,
    'candidate_seen_count',v_control.candidate_seen_count,'v2_selected_count',v_control.v2_selected_count,
    'v2_complete_count',v_control.v2_complete_count,'v2_partial_count',v_control.v2_partial_count,
    'v1_fallback_count',v_control.v1_fallback_count);
end;
$$;

revoke all on function public.arm_follow60_ordering_v2_behavioral_control_v1(uuid,text,timestamptz,jsonb) from public, anon, authenticated;
revoke all on function public.claim_follow60_ordering_v2_behavioral_binding_v1(uuid,text,uuid,uuid,uuid,integer) from public, anon, authenticated;
revoke all on function public.record_follow60_ordering_v2_behavioral_event_v1(uuid,uuid,uuid,uuid,uuid,integer,uuid,text,text,text,text,jsonb) from public, anon, authenticated;
revoke all on function public.terminalize_follow60_ordering_v2_behavioral_control_v1(uuid,uuid,uuid,text,text) from public, anon, authenticated;
grant execute on function public.arm_follow60_ordering_v2_behavioral_control_v1(uuid,text,timestamptz,jsonb) to service_role;
grant execute on function public.claim_follow60_ordering_v2_behavioral_binding_v1(uuid,text,uuid,uuid,uuid,integer) to service_role;
grant execute on function public.record_follow60_ordering_v2_behavioral_event_v1(uuid,uuid,uuid,uuid,uuid,integer,uuid,text,text,text,text,jsonb) to service_role;
grant execute on function public.terminalize_follow60_ordering_v2_behavioral_control_v1(uuid,uuid,uuid,text,text) to service_role;
