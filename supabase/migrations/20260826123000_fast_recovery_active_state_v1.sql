-- FAST_CONTROL_PLANE_RECOVERY_AND_ACTIVE_STATE_V1
-- One monotonic startup evidence row per already-admitted account session.

alter table public.account_session_resume_plans
  add column if not exists worker_spawned_at timestamptz,
  add column if not exists runner_started_at timestamptz,
  add column if not exists device_activity_started_at timestamptz,
  add column if not exists device_connected_at timestamptz,
  add column if not exists instagram_launch_requested_at timestamptz,
  add column if not exists instagram_foreground_verified_at timestamptz;

alter table public.account_session_resume_plans
  drop constraint if exists account_session_resume_plans_startup_order_v1_check,
  add constraint account_session_resume_plans_startup_order_v1_check check (
    (runner_started_at is null or worker_spawned_at is null or runner_started_at >= worker_spawned_at)
    and (device_activity_started_at is null or runner_started_at is null or device_activity_started_at >= runner_started_at)
    and (device_connected_at is null or device_activity_started_at is null or device_connected_at >= device_activity_started_at)
    and (instagram_launch_requested_at is null or device_connected_at is null or instagram_launch_requested_at >= device_connected_at)
    and (instagram_foreground_verified_at is null or device_connected_at is null or instagram_foreground_verified_at >= device_connected_at)
  );

create index if not exists account_session_resume_plans_active_projection_v1_idx
  on public.account_session_resume_plans (account_id, last_updated_at desc)
  include (run_id, run_request_id, resume_state, irreversible_work_state, device_connected_at, instagram_foreground_verified_at);

create or replace function public.certify_account_startup_foreground_v1(
  p_run_id uuid,
  p_request_id uuid,
  p_worker_id text,
  p_foreground_package text,
  p_worker_spawned_at timestamptz,
  p_runner_started_at timestamptz,
  p_device_activity_started_at timestamptz,
  p_device_connected_at timestamptz,
  p_instagram_launch_requested_at timestamptz,
  p_instagram_foreground_verified_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_plan public.account_session_resume_plans%rowtype;
  v_request public.account_run_requests%rowtype;
  v_run public.ig_runs%rowtype;
begin
  perform set_config('lock_timeout', '2s', true);
  perform set_config('statement_timeout', '5s', true);

  select * into v_request from public.account_run_requests
  where id = p_request_id for update;
  select * into v_run from public.ig_runs
  where id = p_run_id for update;
  select * into v_plan from public.account_session_resume_plans
  where run_id = p_run_id for update;

  if v_request.id is null or v_run.id is null or v_plan.run_id is null
     or v_request.run_id is distinct from p_run_id
     or v_plan.run_request_id is distinct from p_request_id
     or v_request.account_id is distinct from v_run.account_id
     or v_request.account_id is distinct from v_plan.account_id
     or v_request.claimed_by is distinct from p_worker_id
     or v_request.status not in ('starting', 'running')
     or v_run.status <> 'running'
     or v_plan.irreversible_work_state <> 'STARTED_OR_AMBIGUOUS' then
    return jsonb_build_object('ok', false, 'reason', 'startup_lineage_not_active');
  end if;
  if nullif(trim(p_foreground_package), '') is null
     or p_foreground_package is distinct from v_plan.expected_package then
    return jsonb_build_object('ok', false, 'reason', 'foreground_package_mismatch');
  end if;
  if p_device_connected_at is null or p_instagram_foreground_verified_at is null
     or p_instagram_foreground_verified_at < p_device_connected_at then
    return jsonb_build_object('ok', false, 'reason', 'foreground_evidence_invalid');
  end if;

  update public.account_session_resume_plans
  set worker_spawned_at = coalesce(worker_spawned_at, p_worker_spawned_at),
      runner_started_at = coalesce(runner_started_at, p_runner_started_at),
      device_activity_started_at = coalesce(device_activity_started_at, p_device_activity_started_at),
      device_connected_at = coalesce(device_connected_at, p_device_connected_at),
      instagram_launch_requested_at = coalesce(instagram_launch_requested_at, p_instagram_launch_requested_at),
      instagram_foreground_verified_at = coalesce(instagram_foreground_verified_at, p_instagram_foreground_verified_at),
      last_updated_at = greatest(last_updated_at, clock_timestamp())
  where run_id = p_run_id;

  return jsonb_build_object(
    'ok', true,
    'run_id', p_run_id,
    'request_id', p_request_id,
    'irreversible_work_state', v_plan.irreversible_work_state,
    'device_connected_at', coalesce(v_plan.device_connected_at, p_device_connected_at),
    'instagram_foreground_verified_at', coalesce(v_plan.instagram_foreground_verified_at, p_instagram_foreground_verified_at)
  );
end;
$$;

revoke all on function public.certify_account_startup_foreground_v1(uuid,uuid,text,text,timestamptz,timestamptz,timestamptz,timestamptz,timestamptz,timestamptz)
  from public, anon, authenticated;
grant execute on function public.certify_account_startup_foreground_v1(uuid,uuid,text,text,timestamptz,timestamptz,timestamptz,timestamptz,timestamptz,timestamptz)
  to service_role;
