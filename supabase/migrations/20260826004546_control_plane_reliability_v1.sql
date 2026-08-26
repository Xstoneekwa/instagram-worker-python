-- CONTROL_PLANE_RELIABILITY_V1
-- Mandatory V1 only: transactional admission, PRE_DEVICE gate, certified
-- zero-work recovery, immutable lineage and bounded retry attempts.

alter table public.account_run_requests
  add column if not exists root_business_session_id uuid,
  add column if not exists execution_attempt_no smallint,
  add column if not exists retry_index smallint;

update public.account_run_requests
set root_business_session_id = coalesce(root_business_session_id, id),
    execution_attempt_no = coalesce(execution_attempt_no, 1),
    retry_index = coalesce(retry_index, 0)
where root_business_session_id is null
   or execution_attempt_no is null
   or retry_index is null;

alter table public.account_run_requests
  alter column root_business_session_id set not null,
  alter column execution_attempt_no set not null,
  alter column retry_index set not null;

alter table public.account_run_requests
  drop constraint if exists account_run_requests_execution_attempt_v1_check,
  add constraint account_run_requests_execution_attempt_v1_check
    check (execution_attempt_no between 1 and 3),
  drop constraint if exists account_run_requests_retry_index_v1_check,
  add constraint account_run_requests_retry_index_v1_check
    check (retry_index between 0 and 2),
  drop constraint if exists account_run_requests_attempt_retry_v1_check,
  add constraint account_run_requests_attempt_retry_v1_check
    check (execution_attempt_no = retry_index + 1);

create unique index if not exists account_run_requests_root_attempt_v1_uidx
  on public.account_run_requests (root_business_session_id, execution_attempt_no);

create or replace function public.account_run_request_lineage_v1_guard()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
  if tg_op = 'INSERT' then
    new.root_business_session_id := coalesce(new.root_business_session_id, new.id);
    new.execution_attempt_no := coalesce(new.execution_attempt_no, 1);
    new.retry_index := coalesce(new.retry_index, new.execution_attempt_no - 1);
    return new;
  end if;
  if new.root_business_session_id is distinct from old.root_business_session_id
     or new.execution_attempt_no is distinct from old.execution_attempt_no
     or new.retry_index is distinct from old.retry_index then
    raise exception using errcode = '23514', message = 'account_run_request_lineage_immutable';
  end if;
  return new;
end;
$$;

drop trigger if exists account_run_request_lineage_v1_guard
  on public.account_run_requests;
create trigger account_run_request_lineage_v1_guard
  before insert or update on public.account_run_requests
  for each row execute function public.account_run_request_lineage_v1_guard();

alter table public.account_session_resume_plans
  add column if not exists zero_work_contract_version smallint,
  add column if not exists irreversible_work_state text,
  add column if not exists zero_work_certified_at timestamptz;

alter table public.account_session_resume_plans
  drop constraint if exists account_session_resume_plans_zero_work_version_v1_check,
  add constraint account_session_resume_plans_zero_work_version_v1_check
    check (zero_work_contract_version is null or zero_work_contract_version = 1),
  drop constraint if exists account_session_resume_plans_irreversible_state_v1_check,
  add constraint account_session_resume_plans_irreversible_state_v1_check
    check (
      irreversible_work_state is null
      or irreversible_work_state in ('PRE_DEVICE', 'STARTED_OR_AMBIGUOUS')
    ),
  drop constraint if exists account_session_resume_plans_zero_work_shape_v1_check,
  add constraint account_session_resume_plans_zero_work_shape_v1_check
    check (
      (zero_work_contract_version is null and irreversible_work_state is null)
      or (zero_work_contract_version = 1 and irreversible_work_state is not null)
    );

alter table public.account_session_resume_plans
  drop constraint if exists account_session_resume_plans_resume_state_check,
  add constraint account_session_resume_plans_resume_state_check
    check (resume_state in (
      'run_active', 'pre_device_stopped', 'recovery_enqueued',
      'awaiting_human_resume_authorization', 'resume_requested',
      'resume_succeeded', 'not_recoverable', 'completed'
    ));

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'account_session_resume_plans_run_request_v1_fk'
      and conrelid = 'public.account_session_resume_plans'::regclass
  ) then
    alter table public.account_session_resume_plans
      add constraint account_session_resume_plans_run_request_v1_fk
      foreign key (run_request_id) references public.account_run_requests(id)
      on delete set null not valid;
  end if;
end;
$$;

create unique index if not exists account_session_resume_plans_run_request_v1_uidx
  on public.account_session_resume_plans (run_request_id)
  where run_request_id is not null;

create index if not exists account_run_requests_root_created_v1_idx
  on public.account_run_requests (root_business_session_id, created_at desc);

create or replace function public.admit_account_run_attempt_v1(
  p_request_id uuid,
  p_worker_id text,
  p_assignment_id uuid,
  p_device_id uuid,
  p_app_instance_id uuid,
  p_expected_package text,
  p_scheduled_window_start timestamptz default null,
  p_scheduled_window_end timestamptz default null,
  p_lease_seconds integer default 300
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
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
  if v_account.id is null
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
    select 1 from public.account_incidents i
    where i.account_id = v_account.id
      and i.status in ('open','acknowledged')
      and (
        i.severity in ('error','critical')
        or i.incident_type in (
          'instagram_human_confirmation_required', 'instagram_account_restriction',
          'active_instagram_account_mismatch', 'assigned_instagram_package_unavailable',
          'account_login_required'
        )
      )
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
$$;

create or replace function public.begin_device_activity_v1(
  p_run_id uuid,
  p_request_id uuid,
  p_worker_id text
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_now timestamptz := clock_timestamp();
  v_account_id uuid;
  v_assignment_id uuid;
  v_device_id uuid;
  v_request public.account_run_requests%rowtype;
  v_plan public.account_session_resume_plans%rowtype;
begin
  perform set_config('lock_timeout', '2s', true);
  perform set_config('statement_timeout', '5s', true);
  select account_id, assignment_id, device_id into v_account_id, v_assignment_id, v_device_id
  from public.account_session_resume_plans
  where run_id = p_run_id and run_request_id = p_request_id;
  if v_account_id is null then return jsonb_build_object('ok',false,'reason','capsule_not_found'); end if;

  perform 1 from public.ig_accounts where id = v_account_id for update;
  perform 1 from public.account_assignments where id = v_assignment_id for update;
  perform 1 from public.auto_restart_device_locks where device_id = v_device_id for update;
  select * into v_request from public.account_run_requests where id = p_request_id for update;
  perform 1 from public.ig_runs where id = p_run_id for update;
  select * into v_plan from public.account_session_resume_plans where run_id = p_run_id for update;

  if v_plan.zero_work_contract_version <> 1
     or v_plan.run_request_id <> v_request.id
     or v_request.run_id <> p_run_id
     or v_request.status <> 'running'
     or v_request.claimed_by is distinct from p_worker_id
     or v_request.lease_expires_at is null or v_request.lease_expires_at <= v_now
     or v_request.cancel_requested_at is not null or v_request.cancel_reason is not null then
    return jsonb_build_object('ok',false,'reason','device_gate_lineage_or_lease_invalid');
  end if;
  if v_plan.irreversible_work_state = 'STARTED_OR_AMBIGUOUS' then
    return jsonb_build_object('ok',true,'idempotent',true,'irreversible_work_state','STARTED_OR_AMBIGUOUS');
  end if;
  if v_plan.irreversible_work_state <> 'PRE_DEVICE' then
    return jsonb_build_object('ok',false,'reason','device_gate_state_invalid');
  end if;
  update public.account_session_resume_plans
  set irreversible_work_state = 'STARTED_OR_AMBIGUOUS', last_updated_at = v_now
  where id = v_plan.id;
  return jsonb_build_object('ok',true,'idempotent',false,'irreversible_work_state','STARTED_OR_AMBIGUOUS');
end;
$$;

create or replace function public.mark_pre_device_safe_stop_v1(
  p_run_id uuid,
  p_request_id uuid,
  p_worker_id text,
  p_reason_code text default 'control_plane_unavailable_pre_device'
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_now timestamptz := clock_timestamp();
  v_account_id uuid; v_assignment_id uuid; v_device_id uuid;
  v_request public.account_run_requests%rowtype;
  v_plan public.account_session_resume_plans%rowtype;
begin
  perform set_config('lock_timeout','2s',true);
  perform set_config('statement_timeout','5s',true);
  select account_id, assignment_id, device_id into v_account_id, v_assignment_id, v_device_id
  from public.account_session_resume_plans where run_id=p_run_id and run_request_id=p_request_id;
  if v_account_id is null then return jsonb_build_object('ok',false,'reason','capsule_not_found'); end if;
  perform 1 from public.ig_accounts where id=v_account_id for update;
  perform 1 from public.account_assignments where id=v_assignment_id for update;
  perform 1 from public.auto_restart_device_locks where device_id=v_device_id for update;
  select * into v_request from public.account_run_requests where id=p_request_id for update;
  perform 1 from public.ig_runs where id=p_run_id for update;
  select * into v_plan from public.account_session_resume_plans where run_id=p_run_id for update;
  if v_plan.zero_work_contract_version <> 1
     or v_plan.irreversible_work_state <> 'PRE_DEVICE'
     or v_request.run_id <> p_run_id
     or v_request.claimed_by is distinct from p_worker_id
     or v_request.cancel_requested_at is not null or v_request.cancel_reason is not null then
    return jsonb_build_object('ok',false,'reason','zero_work_not_certifiable');
  end if;
  if p_reason_code not in (
    'control_plane_unavailable_pre_device',
    'PRE_DEVICE_SAFE_STOP'
  ) then
    return jsonb_build_object('ok',false,'reason','safe_stop_reason_not_transient');
  end if;
  update public.account_run_requests
  set status='failed', completed_at=v_now, lease_expires_at=null,
      error_code=left(p_reason_code,120),
      error_message_safe='Account session stopped before device activity; recovery may be enqueued.',
      updated_at=v_now
  where id=p_request_id;
  update public.ig_runs
  set status='stopped', finished_at=v_now, completed_at=v_now, updated_at=v_now
  where id=p_run_id and status in ('pending','running');
  update public.account_session_resume_plans
  set resume_state='pre_device_stopped', restart_allowed=true,
      restart_block_reason='certified_zero_work', terminal_reason_code=left(p_reason_code,120),
      zero_work_certified_at=v_now, last_updated_at=v_now
  where id=v_plan.id;
  return jsonb_build_object('ok',true,'zero_work_certified',true,'resume_state','pre_device_stopped');
end;
$$;

create or replace function public.certify_zero_work_and_enqueue_recovery_v1(
  p_source_run_id uuid,
  p_source_request_id uuid,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_now timestamptz := clock_timestamp();
  v_account_id uuid; v_assignment_id uuid; v_device_id uuid; v_app_instance_id uuid;
  v_account public.ig_accounts%rowtype;
  v_assignment public.account_assignments%rowtype;
  v_lock public.auto_restart_device_locks%rowtype;
  v_request public.account_run_requests%rowtype;
  v_plan public.account_session_resume_plans%rowtype;
  v_device public.phone_devices%rowtype;
  v_instance public.phone_app_instances%rowtype;
  v_contract jsonb;
  v_schedule jsonb;
  v_key text;
  v_new_request_id uuid := gen_random_uuid();
  v_existing_request_id uuid;
  v_next_attempt smallint;
begin
  perform set_config('lock_timeout','2s',true);
  perform set_config('statement_timeout','5s',true);
  if nullif(trim(p_worker_id),'') is null then
    return jsonb_build_object('ok',false,'reason','worker_id_required');
  end if;
  select account_id, assignment_id, device_id, app_instance_id
  into v_account_id, v_assignment_id, v_device_id, v_app_instance_id
  from public.account_session_resume_plans
  where run_id=p_source_run_id and run_request_id=p_source_request_id;
  if v_account_id is null then return jsonb_build_object('ok',false,'reason','capsule_not_found'); end if;

  -- Same global order as admission and the device gate.
  select * into v_account from public.ig_accounts where id=v_account_id for update;
  select * into v_assignment from public.account_assignments where id=v_assignment_id for update;
  select * into v_lock from public.auto_restart_device_locks where device_id=v_device_id for update;
  select * into v_request from public.account_run_requests where id=p_source_request_id for update;
  perform 1 from public.ig_runs where id=p_source_run_id for update;
  select * into v_plan from public.account_session_resume_plans where run_id=p_source_run_id for update;

  v_next_attempt := v_request.execution_attempt_no + 1;
  v_key := 'control-plane-zero-work:' || v_request.root_business_session_id::text || ':S' || v_next_attempt::text;
  select id into v_existing_request_id
  from public.account_run_requests where idempotency_key=v_key;
  if v_existing_request_id is not null then
    return jsonb_build_object(
      'ok',true,'enqueued',true,'deduplicated',true,
      'request_id',v_existing_request_id,'idempotency_key',v_key,
      'execution_attempt_no',v_next_attempt
    );
  end if;

  if v_plan.zero_work_contract_version <> 1
     or v_plan.irreversible_work_state <> 'PRE_DEVICE'
     or v_plan.run_request_id <> v_request.id
     or v_request.run_id <> p_source_run_id then
    return jsonb_build_object('ok',false,'reason','zero_work_not_certifiable');
  end if;
  if v_request.cancel_requested_at is not null or v_request.cancel_reason is not null
     or v_request.status = 'canceled' then
    return jsonb_build_object('ok',false,'reason','user_canceled');
  end if;
  if not (
    (v_request.status in ('failed','blocked')
      and v_request.error_code in (
        'control_plane_unavailable_pre_device',
        'PRE_DEVICE_SAFE_STOP',
        'certified_zero_work_recovery'
      ))
    or (v_request.status in ('claimed','starting','running')
      and v_request.lease_expires_at is not null
      and v_request.lease_expires_at <= v_now)
  ) then
    return jsonb_build_object('ok',false,'reason','source_not_pre_device_safe_stopped');
  end if;
  if v_next_attempt > 3 then
    update public.account_session_resume_plans
    set resume_state='not_recoverable', restart_allowed=false,
        restart_block_reason='execution_attempt_exhausted', last_updated_at=v_now
    where id=v_plan.id;
    return jsonb_build_object('ok',false,'reason','execution_attempt_exhausted');
  end if;
  if v_account.id is null or coalesce(v_account.admin_lifecycle_status,'active') <> 'active' then
    return jsonb_build_object('ok',false,'reason','account_not_active');
  end if;
  if v_assignment.id is null or v_assignment.account_id <> v_account.id
     or v_assignment.status not in ('pending','reserved','active')
     or v_assignment.device_id <> v_device_id
     or v_assignment.app_instance_id <> v_app_instance_id then
    return jsonb_build_object('ok',false,'reason','assignment_not_admissible');
  end if;
  select * into v_device from public.phone_devices where id=v_device_id;
  select * into v_instance from public.phone_app_instances where id=v_app_instance_id;
  if v_device.id is null or v_device.status in ('maintenance','offline','unauthorized','disabled','retired')
     or v_instance.id is null or v_instance.device_id <> v_device_id
     or not v_instance.is_launchable or not v_instance.usable_for_auto_login
     or v_instance.status='disabled'
     or v_instance.package_name is distinct from v_plan.expected_package then
    return jsonb_build_object('ok',false,'reason','device_or_instance_not_admissible');
  end if;
  if v_lock.device_id is not null and v_lock.lease_expires_at > v_now
     and v_lock.request_id is not null
     and v_lock.request_id <> p_source_request_id then
    return jsonb_build_object('ok',false,'reason','device_lock_held');
  end if;
  if exists (
    select 1 from public.account_incidents i
    where i.account_id=v_account.id and i.status in ('open','acknowledged')
      and (i.severity in ('error','critical') or i.incident_type in (
        'instagram_human_confirmation_required','instagram_account_restriction',
        'active_instagram_account_mismatch','assigned_instagram_package_unavailable',
        'account_login_required'
      ))
  ) then return jsonb_build_object('ok',false,'reason','active_blocking_incident'); end if;
  v_contract := public.account_package_runtime_contract_status(v_account.id);
  if not coalesce((v_contract->>'ok')::boolean,false) then
    return jsonb_build_object('ok',false,'reason',coalesce(v_contract->>'reason','package_contract_blocked'));
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
  if not coalesce((v_schedule->>'ok')::boolean,false)
     or (v_schedule->>'assignment_id')::uuid is distinct from v_assignment.id then
    return jsonb_build_object('ok',false,'reason',coalesce(v_schedule->>'reason','assignment_window_closed'));
  end if;

  -- Terminalize the source and certify zero work before creating its successor.
  update public.account_run_requests
  set status='failed', completed_at=coalesce(completed_at,v_now), lease_expires_at=null,
      error_code=coalesce(error_code,'certified_zero_work_recovery'),
      error_message_safe=coalesce(error_message_safe,'Certified zero-work attempt superseded by bounded recovery.'),
      updated_at=v_now
  where id=v_request.id and status in ('claimed','starting','running','failed','blocked');
  update public.ig_runs
  set status='stopped', finished_at=coalesce(finished_at,v_now),
      completed_at=coalesce(completed_at,v_now), updated_at=v_now
  where id=p_source_run_id and status in ('pending','running');
  update public.account_session_resume_plans
  set resume_state='recovery_enqueued', restart_allowed=false,
      restart_block_reason='recovery_enqueued', zero_work_certified_at=coalesce(zero_work_certified_at,v_now),
      last_updated_at=v_now
  where id=v_plan.id;

  insert into public.account_run_requests(
    id, account_id, requested_by, actor_type, source_surface, requested_run_type,
    status, priority, idempotency_key, metadata_safe, root_business_session_id,
    execution_attempt_no, retry_index
  ) values (
    v_new_request_id, v_account.id, v_request.requested_by, 'system',
    'control_plane_zero_work_recovery_v1', 'account_session', 'queued',
    v_request.priority, v_key,
    coalesce(v_request.metadata_safe,'{}'::jsonb) || jsonb_build_object(
      'zero_work_recovery_v1',true,
      'source_run_id',p_source_run_id::text,
      'source_request_id',p_source_request_id::text,
      'root_business_session_id',v_request.root_business_session_id::text,
      'execution_attempt_no',v_next_attempt,
      'retry_index',v_next_attempt-1,
      'schedule_trigger',case
        when v_request.metadata_safe->>'schedule_trigger' = 'manual'
          or v_request.actor_type in ('admin', 'client') then 'manual'
        else 'auto'
      end,
      'worker_id',p_worker_id
    ),
    v_request.root_business_session_id, v_next_attempt, v_next_attempt-1
  );

  insert into public.auto_restart_device_locks(
    device_id, worker_id, account_id, app_instance_id, request_id, run_id,
    lease_expires_at, reason, metadata_safe, created_at, updated_at
  ) values (
    v_device_id, p_worker_id, v_account.id, v_app_instance_id, v_new_request_id, null,
    v_now + make_interval(secs=>greatest(p_lease_seconds,30)),
    'control_plane_zero_work_recovery_v1',
    jsonb_build_object('root_business_session_id',v_request.root_business_session_id::text,'execution_attempt_no',v_next_attempt),
    v_now,v_now
  )
  on conflict (device_id) do update
  set worker_id=excluded.worker_id, account_id=excluded.account_id,
      app_instance_id=excluded.app_instance_id, request_id=excluded.request_id,
      run_id=null, lease_expires_at=excluded.lease_expires_at,
      reason=excluded.reason, metadata_safe=excluded.metadata_safe, updated_at=v_now
  where public.auto_restart_device_locks.lease_expires_at <= v_now
     or public.auto_restart_device_locks.request_id = p_source_request_id;

  if not exists (
    select 1 from public.auto_restart_device_locks
    where device_id=v_device_id and request_id=v_new_request_id
  ) then raise exception using errcode='40001', message='device_lock_concurrent_change'; end if;

  return jsonb_build_object(
    'ok',true,'enqueued',true,'deduplicated',false,'request_id',v_new_request_id,
    'idempotency_key',v_key,'root_business_session_id',v_request.root_business_session_id,
    'execution_attempt_no',v_next_attempt,'retry_index',v_next_attempt-1
  );
end;
$$;

drop function if exists public.auto_restart_latest_resume_plans_v1(uuid[]);
create function public.auto_restart_latest_resume_plans_v1(p_account_ids uuid[])
returns table (
  run_id uuid, run_request_id uuid, account_id uuid,
  restart_allowed boolean, restart_block_reason text, resume_state text,
  attempts_in_window integer, plan jsonb,
  zero_work_contract_version smallint, irreversible_work_state text,
  zero_work_certified_at timestamptz, assignment_id uuid, device_id uuid,
  app_instance_id uuid, expected_package text, last_updated_at timestamptz
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select distinct on (p.account_id)
    p.run_id, p.run_request_id, p.account_id, p.restart_allowed,
    p.restart_block_reason, p.resume_state, p.attempts_in_window::integer,
    coalesce(p.plan,'{}'::jsonb), p.zero_work_contract_version,
    p.irreversible_work_state, p.zero_work_certified_at, p.assignment_id,
    p.device_id, p.app_instance_id, p.expected_package, p.last_updated_at
  from public.account_session_resume_plans p
  where p.account_id=any(coalesce(p_account_ids,'{}'::uuid[]))
  order by p.account_id,p.last_updated_at desc nulls last,p.created_at desc,p.id desc
$$;

revoke all on function public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer) from public,anon,authenticated;
revoke all on function public.account_run_request_lineage_v1_guard() from public,anon,authenticated;
revoke all on function public.begin_device_activity_v1(uuid,uuid,text) from public,anon,authenticated;
revoke all on function public.mark_pre_device_safe_stop_v1(uuid,uuid,text,text) from public,anon,authenticated;
revoke all on function public.certify_zero_work_and_enqueue_recovery_v1(uuid,uuid,text,integer) from public,anon,authenticated;
revoke all on function public.auto_restart_latest_resume_plans_v1(uuid[]) from public,anon,authenticated;
grant execute on function public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer) to service_role;
grant execute on function public.begin_device_activity_v1(uuid,uuid,text) to service_role;
grant execute on function public.mark_pre_device_safe_stop_v1(uuid,uuid,text,text) to service_role;
grant execute on function public.certify_zero_work_and_enqueue_recovery_v1(uuid,uuid,text,integer) to service_role;
grant execute on function public.auto_restart_latest_resume_plans_v1(uuid[]) to service_role;
