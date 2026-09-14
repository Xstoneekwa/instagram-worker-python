-- Generic natural-tick restriction preflight admission and one pre-device retry.
begin;

-- Refuse to overwrite a parallel change. Exact predecessor or exact replay only.
do $precondition$
begin
  if encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer)')),'UTF8')),'hex') not in ('d67b3b1df0f57da6e4d31fd48e7de8b24f8fe18d09fa48ecefe43154765e091c','0e537b0a2e7740ce1078e594a438f0382947e67b1fd9527700cd2588afe88be1') or to_regprocedure('public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer)') is null then
    raise exception 'restriction_resume_dependency_drift: public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer)';
  end if;
  if encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer,uuid)')),'UTF8')),'hex') not in ('df58eecddd35f975b2d692254b43ab5bcc7117199d6f64367235ec88187e8a4b','005ca2b3156b79519be047273ecc09f3af89d3e57bbbd4843e6543e59885f30d') or to_regprocedure('public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer,uuid)') is null then
    raise exception 'restriction_resume_dependency_drift: public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer,uuid)';
  end if;
  if encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.reconcile_resolved_incident_resume_windows_v2()')),'UTF8')),'hex') not in ('7cc799ed0fb3e130359b33157baa915f82bc3ca3878209a04fe1b9aadd4ae731','355b9283872740b83acb0ad79b74f272931c17ca51cbdfcbebff5227f4443413') or to_regprocedure('public.reconcile_resolved_incident_resume_windows_v2()') is null then
    raise exception 'restriction_resume_dependency_drift: public.reconcile_resolved_incident_resume_windows_v2()';
  end if;
end;
$precondition$;

-- A resolved restriction permits a verification-only attempt, never business work.
-- This predicate is internal to atomic admission and bounded retry restoration.
create or replace function public.restriction_resume_request_authorized_v1(p_request_id uuid)
returns boolean language plpgsql set search_path = public, pg_temp as $function$
declare
  q public.account_run_requests%rowtype;
  a public.incident_resume_authorizations%rowtype;
  i public.account_incidents%rowtype;
  h public.instagram_account_restriction_holds%rowtype;
  p public.account_session_resume_plans%rowtype;
  aa public.account_assignments%rowtype;
  m jsonb;
  f jsonb;
begin
  select * into q from public.account_run_requests where id=p_request_id;
  m := q.metadata_safe;
  f := m->'resume_plan';
  if q.id is null or q.requested_run_type is distinct from 'account_session'
     or q.source_surface is distinct from 'auto_restart_tick' or q.actor_type is distinct from 'system'
     or m->>'recovery_mode' is distinct from 'human_confirmed_resume'
     or m->'restriction_preflight_only' is distinct from 'true'::jsonb
     or f->'restriction_preflight_only' is distinct from 'true'::jsonb
     or f->>'schema' is distinct from 'AUTO_RESTART_RESUME_PLAN_V2'
     or f->'plan_version' is distinct from '2'::jsonb
     or f->>'resume_kind' is distinct from 'instagram_restriction_preflight'
     or f->'phases_to_run' is distinct from '{"welcome":false,"follow":false,"unfollow":false}'::jsonb
     or f->'quota_remaining' is distinct from '{"welcome":0,"follow":0,"unfollow":0,"outreach":0,"total":0}'::jsonb
     or f->'phase_order' is distinct from '["welcome","follow","unfollow"]'::jsonb then
    return false;
  end if;
  -- Text comparison deliberately avoids casts of requester-controlled UUIDs.
  select * into a from public.incident_resume_authorizations
    where id::text=m->>'authorization_id' and account_id=q.account_id;
  if a.id is null or a.status is distinct from 'consumed'
     or a.consumed_by_request_id is distinct from q.id or a.consumed_at is null
     or a.frozen_phase_plan is distinct from f or a.test
     or a.expires_at is null or a.expires_at<=now()
     or a.scheduled_window_start is null or a.scheduled_window_start>now()
     or a.scheduled_window_end is null or a.scheduled_window_end<=now()
     or coalesce(a.expected_worker_sha,'') !~ '^[0-9a-f]{40}$'
     or nullif(a.cause_fixed_version,'') is null
     or m->>'expected_worker_sha' is distinct from a.expected_worker_sha
     or m->>'cause_fixed_version' is distinct from a.cause_fixed_version
     or m->>'incident_id' is distinct from a.incident_id::text
     or m->>'resume_plan_id' is distinct from a.resume_plan_id::text
     or f->>'authorization_id' is distinct from a.id::text
     or f->>'incident_id' is distinct from a.incident_id::text
     or f->>'resume_plan_id' is distinct from a.resume_plan_id::text
     or f->>'account_id' is distinct from a.account_id::text
     or f->>'prior_run_id' is distinct from coalesce(a.source_run_id,a.run_id)::text
     or f->'retry_generation' is distinct from to_jsonb(a.retry_generation) then
    return false;
  end if;
  select * into i from public.account_incidents where id=a.incident_id and account_id=a.account_id;
  select * into h from public.instagram_account_restriction_holds where incident_id=a.incident_id and account_id=a.account_id;
  select * into p from public.account_session_resume_plans where id=a.resume_plan_id and account_id=a.account_id;
  if i.id is null or i.status is distinct from 'resolved' or i.resolved_at is null
     or i.incident_type is distinct from 'instagram_account_restriction'
     or lower(coalesce(i.metadata->>'security_incident','false')) in ('true','1','yes')
     or h.id is null or h.status is distinct from 'verification_required'
     or h.previous_admin_lifecycle_status is distinct from 'active'
     or h.verified_cleared_at is not null or h.verified_by_run_id is not null
     or p.id is null or p.run_id is distinct from a.run_id
     or p.run_id is distinct from coalesce(a.source_run_id,a.run_id)
     or p.resume_state is distinct from 'resume_requested' or p.restart_allowed is distinct from true
     or not exists(select 1 from public.account_run_requests src
          where src.id=a.source_request_id and src.account_id=a.account_id and src.run_id=p.run_id)
     or exists(select 1 from public.instagram_account_restriction_holds other_hold
          where other_hold.account_id=a.account_id and other_hold.id<>h.id
            and other_hold.status in ('active','verification_required')) then
    return false;
  end if;
  select * into aa from public.account_assignments where id=p.assignment_id and account_id=a.account_id;
  if aa.id is null or aa.status not in ('reserved','active')
     or aa.starts_at>now() or aa.ends_at<=now() or coalesce(aa.schedule_mode,'')='manual_only'
     or aa.starts_at is distinct from a.scheduled_window_start or aa.ends_at is distinct from a.scheduled_window_end
     or aa.device_id is distinct from p.device_id or aa.app_instance_id is distinct from p.app_instance_id
     or f->>'assignment_id' is distinct from aa.id::text
     or f->>'device_id' is distinct from aa.device_id::text
     or f->>'app_instance_id' is distinct from aa.app_instance_id::text
     or not exists(select 1 from public.ig_accounts acct where acct.id=a.account_id
          and acct.status='active' and acct.admin_lifecycle_status='paused')
     or not exists(select 1 from public.ig_account_settings s where s.account_id=a.account_id
          and s.account_status='active' and not coalesce(s.manual_stop_requested,false)
          and coalesce(s.pause_account_days,0)=0 and coalesce(s.pause_account_until,'')='')
     or exists(select 1 from public.canonical_active_operational_blockers_v1(array[a.account_id]))
     or public.fleet_admission_reason_v1(a.account_id) is not null then
    return false;
  end if;
  return true;
end;
$function$;
revoke all on function public.restriction_resume_request_authorized_v1(uuid) from public,anon,authenticated;
grant execute on function public.restriction_resume_request_authorized_v1(uuid) to service_role;

CREATE OR REPLACE FUNCTION public.admit_account_run_attempt_v1(p_request_id uuid, p_worker_id text, p_assignment_id uuid, p_device_id uuid, p_app_instance_id uuid, p_expected_package text, p_scheduled_window_start timestamp with time zone DEFAULT NULL::timestamp with time zone, p_scheduled_window_end timestamp with time zone DEFAULT NULL::timestamp with time zone, p_lease_seconds integer DEFAULT 300)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_now timestamptz := clock_timestamp();
  v_account_id uuid;
  v_account public.ig_accounts%rowtype;
  v_assignment public.account_assignments%rowtype;
  v_lock public.auto_restart_device_locks%rowtype;
  v_request public.account_run_requests%rowtype;
  v_run public.ig_runs%rowtype;
  v_plan public.account_session_resume_plans%rowtype;
  v_device public.phone_devices%rowtype;
  v_instance public.phone_app_instances%rowtype;
  v_contract jsonb;
  v_schedule jsonb;
  v_run_id uuid;
begin
  perform set_config('lock_timeout', '2s', true);
  perform set_config('statement_timeout', '5s', true);
  if p_request_id is null or nullif(trim(p_worker_id), '') is null
     or p_assignment_id is null or p_device_id is null
     or p_app_instance_id is null or nullif(trim(p_expected_package), '') is null then
    return jsonb_build_object('ok', false, 'reason', 'admission_binding_missing');
  end if;

  select account_id into v_account_id
  from public.account_run_requests where id = p_request_id;
  if v_account_id is null then
    return jsonb_build_object('ok', false, 'reason', 'request_not_found');
  end if;

  -- Global lock order: account -> assignment -> device lock -> request -> run -> capsule.
  select * into v_account from public.ig_accounts
  where id = v_account_id for update;
  select * into v_assignment from public.account_assignments
  where id = p_assignment_id for update;
  select * into v_lock from public.auto_restart_device_locks
  where device_id = p_device_id for update;
  select * into v_request from public.account_run_requests
  where id = p_request_id for update;
  if v_request.run_id is not null then
    select * into v_run from public.ig_runs
    where id = v_request.run_id for update;
  end if;
  if v_request.run_id is not null then
    select * into v_plan from public.account_session_resume_plans
    where run_id = v_request.run_id for update;
  end if;

  if v_request.account_id is distinct from v_account.id
     or v_request.requested_run_type <> 'account_session'
     or v_request.status not in ('claimed', 'starting', 'running')
     or v_request.claimed_by is distinct from p_worker_id
     or v_request.lease_expires_at is null or v_request.lease_expires_at <= v_now
     or v_request.cancel_requested_at is not null or v_request.cancel_reason is not null then
    return jsonb_build_object('ok', false, 'reason', 'request_not_admissible');
  end if;
  perform public.assert_fleet_admission_v1(v_account_id);

  if v_plan.zero_work_contract_version = 1 then
    if v_plan.irreversible_work_state <> 'PRE_DEVICE' then
      return jsonb_build_object('ok', false, 'reason', 'admission_already_left_pre_device');
    end if;
    return jsonb_build_object(
      'ok', true, 'idempotent', true, 'run_id', v_plan.run_id,
      'request_id', v_request.id,
      'irreversible_work_state', v_plan.irreversible_work_state
    );
  end if;
  if v_request.run_id is not null then
    return jsonb_build_object('ok', false, 'reason', 'legacy_run_not_admissible');
  end if;
  if (v_request.metadata_safe ? 'restriction_preflight_only'
        and v_request.metadata_safe->'restriction_preflight_only' is distinct from 'false'::jsonb)
     or (v_request.metadata_safe->'resume_plan' ? 'restriction_preflight_only') then
    if not public.restriction_resume_request_authorized_v1(v_request.id)
       or v_request.metadata_safe#>>'{resume_plan,assignment_id}' is distinct from p_assignment_id::text
       or v_request.metadata_safe#>>'{resume_plan,device_id}' is distinct from p_device_id::text
       or v_request.metadata_safe#>>'{resume_plan,app_instance_id}' is distinct from p_app_instance_id::text then
      return jsonb_build_object('ok',false,'reason','restriction_preflight_not_authorized');
    end if;
  elsif v_account.id is null
     or coalesce(v_account.admin_lifecycle_status, 'active') <> 'active' then
    return jsonb_build_object('ok', false, 'reason', 'account_not_active');
  end if;
  if v_assignment.id is null
     or v_assignment.account_id <> v_account.id
     or v_assignment.device_id <> p_device_id
     or v_assignment.app_instance_id <> p_app_instance_id
     or v_assignment.status not in ('pending', 'reserved', 'active')
     or (p_scheduled_window_start is not null and v_assignment.starts_at is distinct from p_scheduled_window_start)
     or (p_scheduled_window_end is not null and v_assignment.ends_at is distinct from p_scheduled_window_end) then
    return jsonb_build_object('ok', false, 'reason', 'assignment_not_admissible');
  end if;
  select * into v_device from public.phone_devices where id = p_device_id;
  select * into v_instance from public.phone_app_instances where id = p_app_instance_id;
  if v_device.id is null or v_device.status in ('maintenance','offline','unauthorized','disabled','retired')
     or v_instance.id is null or v_instance.device_id <> p_device_id
     or not v_instance.is_launchable or not v_instance.usable_for_auto_login
     or v_instance.status = 'disabled'
     or v_instance.package_name is distinct from p_expected_package then
    return jsonb_build_object('ok', false, 'reason', 'device_or_instance_not_admissible');
  end if;
  if v_lock.device_id is null or v_lock.lease_expires_at <= v_now
     or v_lock.account_id is distinct from v_account.id
     or v_lock.request_id is distinct from v_request.id then
    return jsonb_build_object('ok', false, 'reason', 'device_lock_not_bound');
  end if;

  if exists (
    select 1
    from public.canonical_active_operational_blockers_v1(array[v_account.id])
  ) then
    return jsonb_build_object('ok', false, 'reason', 'active_blocking_incident');
  end if;
  v_contract := public.account_package_runtime_contract_status(v_account.id);
  if not coalesce((v_contract->>'ok')::boolean, false) then
    return jsonb_build_object('ok', false, 'reason', coalesce(v_contract->>'reason','package_contract_blocked'));
  end if;
  v_schedule := public.evaluate_account_schedule_gate(
    v_account.id,
    'account_session',
    case
      when v_request.metadata_safe->>'schedule_trigger' = 'manual'
        or v_request.actor_type in ('admin', 'client') then 'manual'
      else 'auto'
    end
  );
  if not coalesce((v_schedule->>'ok')::boolean, false)
     or (v_schedule->>'assignment_id')::uuid is distinct from v_assignment.id then
    return jsonb_build_object('ok', false, 'reason', coalesce(v_schedule->>'reason','assignment_window_closed'));
  end if;
  if v_request.execution_attempt_no > 3 then
    return jsonb_build_object('ok', false, 'reason', 'execution_attempt_exhausted');
  end if;

  insert into public.ig_runs(account_id, status, started_at, updated_at)
  values (v_account.id, 'running', v_now, v_now)
  returning id into v_run_id;

  update public.account_run_requests
  set run_id = v_run_id, status = 'running', started_at = coalesce(started_at, v_now),
      lease_expires_at = v_now + make_interval(secs => greatest(p_lease_seconds, 30)),
      updated_at = v_now
  where id = v_request.id;

  insert into public.account_session_resume_plans(
    run_id, run_request_id, account_id, assignment_id, device_id, app_instance_id,
    expected_username, expected_package, scheduled_window_start, scheduled_window_end,
    source_surface, run_trigger, resume_stage, resume_state, restart_allowed,
    restart_block_reason, attempts_in_window, plan, zero_work_contract_version,
    irreversible_work_state, last_updated_at
  ) values (
    v_run_id, v_request.id, v_account.id, v_assignment.id, p_device_id, p_app_instance_id,
    v_account.username, p_expected_package, v_assignment.starts_at, v_assignment.ends_at,
    v_request.source_surface, 'atomic_admission_v1', 'preflight', 'run_active', false,
    'run_in_progress', v_request.retry_index,
    jsonb_build_object(
      'root_business_session_id', v_request.root_business_session_id,
      'execution_attempt_no', v_request.execution_attempt_no,
      'retry_index', v_request.retry_index
    ), 1, 'PRE_DEVICE', v_now
  );

  update public.auto_restart_device_locks
  set run_id = v_run_id, lease_expires_at = greatest(lease_expires_at, v_now + make_interval(secs => greatest(p_lease_seconds,30))), updated_at = v_now
  where device_id = p_device_id;

  return jsonb_build_object(
    'ok', true, 'idempotent', false, 'run_id', v_run_id,
    'request_id', v_request.id, 'root_business_session_id', v_request.root_business_session_id,
    'execution_attempt_no', v_request.execution_attempt_no,
    'irreversible_work_state', 'PRE_DEVICE'
  );
end;
$function$;

CREATE OR REPLACE FUNCTION public.admit_account_run_attempt_v1(p_request_id uuid, p_worker_id text, p_assignment_id uuid, p_device_id uuid, p_app_instance_id uuid, p_expected_package text, p_scheduled_window_start timestamp with time zone, p_scheduled_window_end timestamp with time zone, p_lease_seconds integer, p_expected_lease_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_now timestamptz := clock_timestamp();
  v_account_id uuid;
  v_account public.ig_accounts%rowtype;
  v_assignment public.account_assignments%rowtype;
  v_lock public.auto_restart_device_locks%rowtype;
  v_request public.account_run_requests%rowtype;
  v_run public.ig_runs%rowtype;
  v_plan public.account_session_resume_plans%rowtype;
  v_device public.phone_devices%rowtype;
  v_instance public.phone_app_instances%rowtype;
  v_contract jsonb;
  v_schedule jsonb;
  v_run_id uuid;
begin
  perform set_config('lock_timeout', '2s', true);
  perform set_config('statement_timeout', '5s', true);
  if p_request_id is null or nullif(trim(p_worker_id), '') is null
     or p_assignment_id is null or p_device_id is null
     or p_app_instance_id is null or nullif(trim(p_expected_package), '') is null then
    return jsonb_build_object('ok', false, 'reason', 'admission_binding_missing');
  end if;

  select account_id into v_account_id
  from public.account_run_requests where id = p_request_id;
  if v_account_id is null then
    return jsonb_build_object('ok', false, 'reason', 'request_not_found');
  end if;

  -- Global lock order: account -> assignment -> device lock -> request -> run -> capsule.
  select * into v_account from public.ig_accounts
  where id = v_account_id for update;
  select * into v_assignment from public.account_assignments
  where id = p_assignment_id for update;
  select * into v_lock from public.auto_restart_device_locks
  where device_id = p_device_id for update;
      if p_expected_lease_id is null or v_lock.lease_id is distinct from p_expected_lease_id
        or v_lock.request_id is distinct from p_request_id or v_lock.worker_id is distinct from p_worker_id
        or v_lock.account_id is distinct from v_account_id then
        return jsonb_build_object('ok',false,'reason','device_lock_stale_or_misbound');
      end if;

  select * into v_request from public.account_run_requests
  where id = p_request_id for update;
  if v_request.run_id is not null then
    select * into v_run from public.ig_runs
    where id = v_request.run_id for update;
  end if;
  if v_request.run_id is not null then
    select * into v_plan from public.account_session_resume_plans
    where run_id = v_request.run_id for update;
  end if;

  if v_request.account_id is distinct from v_account.id
     or v_request.requested_run_type <> 'account_session'
     or v_request.status not in ('claimed', 'starting', 'running')
     or v_request.claimed_by is distinct from p_worker_id
     or v_request.lease_expires_at is null or v_request.lease_expires_at <= v_now
     or v_request.cancel_requested_at is not null or v_request.cancel_reason is not null then
    return jsonb_build_object('ok', false, 'reason', 'request_not_admissible');
  end if;
  perform public.assert_fleet_admission_v1(v_account_id);

  if v_plan.zero_work_contract_version = 1 then
    if v_plan.irreversible_work_state <> 'PRE_DEVICE' then
      return jsonb_build_object('ok', false, 'reason', 'admission_already_left_pre_device');
    end if;
    return jsonb_build_object(
      'ok', true, 'idempotent', true, 'run_id', v_plan.run_id,
      'request_id', v_request.id,
      'irreversible_work_state', v_plan.irreversible_work_state
    );
  end if;
  if v_request.run_id is not null then
    return jsonb_build_object('ok', false, 'reason', 'legacy_run_not_admissible');
  end if;
  if (v_request.metadata_safe ? 'restriction_preflight_only'
        and v_request.metadata_safe->'restriction_preflight_only' is distinct from 'false'::jsonb)
     or (v_request.metadata_safe->'resume_plan' ? 'restriction_preflight_only') then
    if not public.restriction_resume_request_authorized_v1(v_request.id)
       or v_request.metadata_safe#>>'{resume_plan,assignment_id}' is distinct from p_assignment_id::text
       or v_request.metadata_safe#>>'{resume_plan,device_id}' is distinct from p_device_id::text
       or v_request.metadata_safe#>>'{resume_plan,app_instance_id}' is distinct from p_app_instance_id::text then
      return jsonb_build_object('ok',false,'reason','restriction_preflight_not_authorized');
    end if;
  elsif v_account.id is null
     or coalesce(v_account.admin_lifecycle_status, 'active') <> 'active' then
    return jsonb_build_object('ok', false, 'reason', 'account_not_active');
  end if;
  if v_assignment.id is null
     or v_assignment.account_id <> v_account.id
     or v_assignment.device_id <> p_device_id
     or v_assignment.app_instance_id <> p_app_instance_id
     or v_assignment.status not in ('pending', 'reserved', 'active')
     or (p_scheduled_window_start is not null and v_assignment.starts_at is distinct from p_scheduled_window_start)
     or (p_scheduled_window_end is not null and v_assignment.ends_at is distinct from p_scheduled_window_end) then
    return jsonb_build_object('ok', false, 'reason', 'assignment_not_admissible');
  end if;
  select * into v_device from public.phone_devices where id = p_device_id;
  select * into v_instance from public.phone_app_instances where id = p_app_instance_id;
  if v_device.id is null or v_device.status in ('maintenance','offline','unauthorized','disabled','retired')
     or v_instance.id is null or v_instance.device_id <> p_device_id
     or not v_instance.is_launchable or not v_instance.usable_for_auto_login
     or v_instance.status = 'disabled'
     or v_instance.package_name is distinct from p_expected_package then
    return jsonb_build_object('ok', false, 'reason', 'device_or_instance_not_admissible');
  end if;
  if v_lock.device_id is null or v_lock.lease_expires_at <= v_now
     or v_lock.account_id is distinct from v_account.id
     or v_lock.request_id is distinct from v_request.id then
    return jsonb_build_object('ok', false, 'reason', 'device_lock_not_bound');
  end if;

  if exists (
    select 1
    from public.canonical_active_operational_blockers_v1(array[v_account.id])
  ) then
    return jsonb_build_object('ok', false, 'reason', 'active_blocking_incident');
  end if;
  v_contract := public.account_package_runtime_contract_status(v_account.id);
  if not coalesce((v_contract->>'ok')::boolean, false) then
    return jsonb_build_object('ok', false, 'reason', coalesce(v_contract->>'reason','package_contract_blocked'));
  end if;
  v_schedule := public.evaluate_account_schedule_gate(
    v_account.id,
    'account_session',
    case
      when v_request.metadata_safe->>'schedule_trigger' = 'manual'
        or v_request.actor_type in ('admin', 'client') then 'manual'
      else 'auto'
    end
  );
  if not coalesce((v_schedule->>'ok')::boolean, false)
     or (v_schedule->>'assignment_id')::uuid is distinct from v_assignment.id then
    return jsonb_build_object('ok', false, 'reason', coalesce(v_schedule->>'reason','assignment_window_closed'));
  end if;
  if v_request.execution_attempt_no > 3 then
    return jsonb_build_object('ok', false, 'reason', 'execution_attempt_exhausted');
  end if;

  insert into public.ig_runs(account_id, status, started_at, updated_at)
  values (v_account.id, 'running', v_now, v_now)
  returning id into v_run_id;

  update public.account_run_requests
  set run_id = v_run_id, status = 'running', started_at = coalesce(started_at, v_now),
      lease_expires_at = v_now + make_interval(secs => greatest(p_lease_seconds, 30)),
      updated_at = v_now
  where id = v_request.id;

  insert into public.account_session_resume_plans(
    run_id, run_request_id, account_id, assignment_id, device_id, app_instance_id,
    expected_username, expected_package, scheduled_window_start, scheduled_window_end,
    source_surface, run_trigger, resume_stage, resume_state, restart_allowed,
    restart_block_reason, attempts_in_window, plan, zero_work_contract_version,
    irreversible_work_state, last_updated_at
  ) values (
    v_run_id, v_request.id, v_account.id, v_assignment.id, p_device_id, p_app_instance_id,
    v_account.username, p_expected_package, v_assignment.starts_at, v_assignment.ends_at,
    v_request.source_surface, 'atomic_admission_v1', 'preflight', 'run_active', false,
    'run_in_progress', v_request.retry_index,
    jsonb_build_object(
      'root_business_session_id', v_request.root_business_session_id,
      'execution_attempt_no', v_request.execution_attempt_no,
      'retry_index', v_request.retry_index
    ), 1, 'PRE_DEVICE', v_now
  );

  update public.auto_restart_device_locks
  set run_id = v_run_id, lease_expires_at = greatest(lease_expires_at, v_now + make_interval(secs => greatest(p_lease_seconds,30))), updated_at = v_now
  where device_id = p_device_id;

  return jsonb_build_object(
    'ok', true, 'idempotent', false, 'run_id', v_run_id,
    'request_id', v_request.id, 'root_business_session_id', v_request.root_business_session_id,
    'execution_attempt_no', v_request.execution_attempt_no,
    'irreversible_work_state', 'PRE_DEVICE'
  );
end;
$function$;

-- One additional enqueue credit, not a retry of a physical preflight failure.
create or replace function public.restore_restriction_admission_credit_v1()
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $function$
declare
  candidate record;
  a public.incident_resume_authorizations%rowtype;
  q public.account_run_requests%rowtype;
  aa public.account_assignments%rowtype;
  restored integer := 0;
begin
  for candidate in
    select auth.id,auth.account_id,auth.resume_plan_id,auth.consumed_by_request_id
    from public.incident_resume_authorizations auth
    join public.account_run_requests req on req.id=auth.consumed_by_request_id
    where auth.status='consumed' and not auth.test
      and not (coalesce(auth.metadata_safe,'{}'::jsonb) ? 'restriction_admission_credit_v1')
      and req.status='blocked' and req.error_code='account_not_active'
      and req.run_id is null and req.started_at is null
      and req.metadata_safe->'restriction_preflight_only'='true'::jsonb
    order by req.updated_at,auth.id
  loop
    -- Same account/assignment/device/request/authorization/plan lock order as enqueue.
    perform 1 from public.ig_accounts where id=candidate.account_id for update skip locked;
    if not found then continue; end if;
    select assignment.* into aa from public.account_assignments assignment
      join public.account_session_resume_plans plan on plan.assignment_id=assignment.id
      where plan.id=candidate.resume_plan_id for share of assignment;
    if aa.id is null then continue; end if;
    perform pg_advisory_xact_lock(hashtextextended('fleet-device:'||aa.device_id::text,0));
    perform 1 from public.auto_restart_device_locks where device_id=aa.device_id for update;
    select * into q from public.account_run_requests where id=candidate.consumed_by_request_id for update;
    select * into a from public.incident_resume_authorizations where id=candidate.id for update;
    perform 1 from public.account_session_resume_plans where id=a.resume_plan_id for update;
    if a.status is distinct from 'consumed' or a.consumed_by_request_id is distinct from q.id
       or coalesce(a.metadata_safe,'{}'::jsonb) ? 'restriction_admission_credit_v1'
       or q.status is distinct from 'blocked' or q.error_code is distinct from 'account_not_active'
       or q.run_id is not null or q.started_at is not null or q.completed_at is null
       or q.cancel_requested_at is not null or q.cancel_reason is not null
       or exists(select 1 from public.account_session_resume_plans where run_request_id=q.id)
       or exists(select 1 from public.account_run_requests newer where newer.account_id=a.account_id
          and newer.id<>q.id and newer.created_at>=q.created_at)
       or exists(select 1 from public.ig_runs r where r.account_id=a.account_id
          and (r.status in ('pending','running') or r.started_at>=q.created_at))
       or exists(select 1 from public.auto_restart_device_locks l where l.device_id=aa.device_id
          and l.lease_expires_at>now())
       or not public.restriction_resume_request_authorized_v1(q.id) then
      continue;
    end if;
    update public.incident_resume_authorizations
    set status='armed',retry_generation=retry_generation+1,
        retry_credit_restored_at=now(),retry_credit_restore_reason='restriction_admission_rejected_pre_device',
        consumed_at=null,consumed_by_request_id=null,consume_error=null,frozen_phase_plan=null,
        armed_at=now(),updated_at=now(),
        metadata_safe=coalesce(metadata_safe,'{}'::jsonb)||jsonb_build_object(
          'restriction_admission_credit_v1',jsonb_build_object(
            'request_id',q.id,'previous_retry_generation',a.retry_generation,
            'restored_at',now(),'reason','account_not_active','run_id',q.run_id,
            'started_at',q.started_at,'completed_at',q.completed_at,
            'frozen_phase_plan',a.frozen_phase_plan))
    where id=a.id;
    update public.account_session_resume_plans
    set resume_state='awaiting_human_resume_authorization',restart_allowed=true,
        restart_block_reason='',last_updated_at=now()
    where id=a.resume_plan_id;
    restored := restored+1;
  end loop;
  return jsonb_build_object('restriction_admission_credits_restored',restored);
end;
$function$;
revoke all on function public.restore_restriction_admission_credit_v1() from public,anon,authenticated;
grant execute on function public.restore_restriction_admission_credit_v1() to service_role;

CREATE OR REPLACE FUNCTION public.reconcile_resolved_incident_resume_windows_v2()
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_base jsonb;
  v_pre_run jsonb;
  v_rearmed jsonb;
  v_enriched integer := 0;
  v_restriction jsonb;
begin
  v_restriction := public.restore_restriction_admission_credit_v1();
  v_base := public.reconcile_resolved_incident_resume_windows_v1();
  v_pre_run := public.reconcile_resolved_pre_run_incident_authorizations_v1();
  v_rearmed := public.rearm_resolved_pre_run_incident_authorizations_v1();

  update public.incident_resume_authorizations a
  set source_run_id = coalesce(a.source_run_id, a.run_id, i.run_id),
      source_request_id = coalesce(a.source_request_id, p.run_request_id),
      resolved_by = coalesce(a.resolved_by, i.resolved_by),
      resolved_at = coalesce(a.resolved_at, i.resolved_at),
      cause_fixed_version = coalesce(a.cause_fixed_version, i.metadata #>> '{manual_resolution_v2,cause_fixed_version}'),
      business_date = coalesce(a.business_date, (coalesce(i.resolved_at, a.armed_at) at time zone 'Africa/Johannesburg')::date),
      expected_worker_sha = coalesce(a.expected_worker_sha, lower(i.metadata #>> '{manual_resolution_v2,expected_worker_sha}')),
      expires_at = coalesce(a.expires_at, a.scheduled_window_end),
      idempotency_key = coalesce(a.idempotency_key, 'incident-resume:' || a.account_id::text || ':' || a.incident_id::text || ':v' || i.lifecycle_version::text),
      updated_at = now()
  from public.account_incidents i, public.account_session_resume_plans p
  where a.incident_id = i.id and a.resume_plan_id = p.id
    and a.status = 'armed'
    and i.status = 'resolved'
    and (i.metadata #>> '{manual_resolution_v2,expected_worker_sha}') ~ '^[0-9a-f]{40}$'
    and nullif(i.metadata #>> '{manual_resolution_v2,cause_fixed_version}', '') is not null
    and (a.expected_worker_sha is null or a.cause_fixed_version is null or a.idempotency_key is null);
  get diagnostics v_enriched = row_count;

  return coalesce(v_restriction, '{}'::jsonb) || coalesce(v_base, '{}'::jsonb)
    || coalesce(v_pre_run, '{}'::jsonb)
    || coalesce(v_rearmed, '{}'::jsonb)
    || jsonb_build_object('v2_enriched_count', v_enriched);
end
$function$;

-- No reconciliation or enqueue is executed by this migration.
notify pgrst, 'reload schema';
commit;
