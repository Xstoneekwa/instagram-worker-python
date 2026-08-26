-- GOLDEN_RESUME_PLAN_CONTRACT_V1
--
-- partial_resumable is a business outcome kept inside plan JSON. It is not a
-- resume lifecycle state. Safe terminal partial work maps to resume_requested
-- and is reconsidered only by the next natural Auto Restart tick.

create or replace function public.resume_state_schema_contract_v1()
returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'contract_version', 'golden_resume_plan_contract_v1',
    'db_allowed_states', jsonb_build_array(
      'run_active', 'pre_device_stopped', 'recovery_enqueued',
      'awaiting_human_resume_authorization', 'resume_requested',
      'resume_succeeded', 'not_recoverable', 'completed'
    ),
    'business_outcomes_not_states', jsonb_build_array(
      'partial_resumable', 'partial_safe_stopped'
    )
  );
$$;

create or replace function public.persist_account_session_resume_plan_v1(
  p_run_id uuid,
  p_request_id uuid,
  p_root_business_session_id uuid,
  p_execution_attempt_no smallint,
  p_resume_stage text,
  p_resume_state text,
  p_restart_allowed boolean,
  p_restart_block_reason text,
  p_terminal_reason_code text,
  p_plan jsonb,
  p_terminal_plan_digest text
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
  v_run public.ig_runs%rowtype;
  v_plan public.account_session_resume_plans%rowtype;
  v_contract_key text;
  v_total_remaining integer;
begin
  perform set_config('lock_timeout', '2s', true);
  perform set_config('statement_timeout', '5s', true);

  if p_run_id is null or p_request_id is null
     or p_root_business_session_id is null
     or p_execution_attempt_no not between 1 and 3
     or p_resume_stage not in ('phases', 'completed')
     or p_resume_state not in ('resume_requested', 'not_recoverable', 'completed')
     or jsonb_typeof(p_plan) <> 'object'
     or p_terminal_plan_digest !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('ok', false, 'reason', 'resume_plan_contract_invalid');
  end if;

  select account_id, assignment_id, device_id
  into v_account_id, v_assignment_id, v_device_id
  from public.account_session_resume_plans
  where run_id = p_run_id and run_request_id = p_request_id;
  if v_account_id is null then
    return jsonb_build_object('ok', false, 'reason', 'resume_plan_not_found');
  end if;

  -- Global lock order inherited from CONTROL_PLANE_RELIABILITY_V1.
  perform 1 from public.ig_accounts where id = v_account_id for update;
  if v_assignment_id is not null then
    perform 1 from public.account_assignments where id = v_assignment_id for update;
  end if;
  if v_device_id is not null then
    perform 1 from public.auto_restart_device_locks where device_id = v_device_id for update;
  end if;
  select * into v_request from public.account_run_requests
  where id = p_request_id for update;
  select * into v_run from public.ig_runs where id = p_run_id for update;
  select * into v_plan from public.account_session_resume_plans
  where run_id = p_run_id for update;

  if v_request.id is null or v_run.id is null or v_plan.id is null
     or v_request.run_id is distinct from p_run_id
     or v_request.account_id is distinct from v_plan.account_id
     or v_run.account_id is distinct from v_plan.account_id
     or v_plan.run_request_id is distinct from p_request_id
     or v_request.root_business_session_id is distinct from p_root_business_session_id
     or v_request.execution_attempt_no is distinct from p_execution_attempt_no then
    return jsonb_build_object('ok', false, 'reason', 'resume_plan_lineage_mismatch');
  end if;

  v_contract_key := concat_ws(
    ':', p_run_id::text, p_request_id::text,
    p_root_business_session_id::text, p_execution_attempt_no::text
  );
  if p_plan->>'resume_contract_key' is distinct from v_contract_key
     or p_plan->>'terminal_plan_digest' is distinct from p_terminal_plan_digest
     or p_plan->>'resume_state_contract_version' is distinct from 'golden_resume_plan_contract_v1' then
    return jsonb_build_object('ok', false, 'reason', 'resume_plan_idempotency_key_invalid');
  end if;

  if v_plan.resume_state = p_resume_state
     and v_plan.plan->>'resume_contract_key' = v_contract_key
     and v_plan.plan->>'terminal_plan_digest' = p_terminal_plan_digest then
    return jsonb_build_object(
      'ok', true, 'idempotent', true, 'resume_state', v_plan.resume_state,
      'contract_version', 'golden_resume_plan_contract_v1'
    );
  end if;
  if v_plan.resume_state <> 'run_active' then
    return jsonb_build_object('ok', false, 'reason', 'resume_plan_state_conflict');
  end if;

  if p_resume_state = 'resume_requested' then
    v_total_remaining := case
      when coalesce(p_plan->'quota_remaining'->>'total', '') ~ '^[0-9]+$'
        then (p_plan->'quota_remaining'->>'total')::integer
      else 0
    end;
    if p_restart_allowed is not true
       or coalesce(p_plan->>'business_outcome', '') not in ('partial_resumable', 'partial_safe_stopped')
       or coalesce(p_plan->>'auto_restart_decision', '') <> 'schedule_resume'
       or v_total_remaining < 1
       or not (
         coalesce(p_plan->'phases_to_run'->>'welcome', '') = 'true'
         or coalesce(p_plan->'phases_to_run'->>'follow', '') = 'true'
         or coalesce(p_plan->'phases_to_run'->>'unfollow', '') = 'true'
       )
       or jsonb_array_length(
         case when jsonb_typeof(p_plan->'unsafe_markers') = 'array'
           then p_plan->'unsafe_markers' else '[]'::jsonb end
       ) <> 0 then
      return jsonb_build_object('ok', false, 'reason', 'resume_requested_proof_incomplete');
    end if;
  elsif p_restart_allowed is true then
    return jsonb_build_object('ok', false, 'reason', 'terminal_resume_state_restart_mismatch');
  end if;

  update public.account_session_resume_plans
  set resume_stage = p_resume_stage,
      resume_state = p_resume_state,
      restart_allowed = p_restart_allowed,
      restart_block_reason = coalesce(p_restart_block_reason, ''),
      terminal_reason_code = left(nullif(trim(p_terminal_reason_code), ''), 120),
      plan = coalesce(v_plan.plan, '{}'::jsonb) || p_plan,
      last_updated_at = v_now
  where id = v_plan.id;

  return jsonb_build_object(
    'ok', true, 'idempotent', false, 'resume_state', p_resume_state,
    'auto_restart_decision', p_plan->>'auto_restart_decision',
    'contract_version', 'golden_resume_plan_contract_v1'
  );
end;
$$;

create or replace function public.reconcile_stale_account_session_resume_plans_v1(
  p_limit integer default 100
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_now timestamptz := clock_timestamp();
  v_row record;
  v_final_plan jsonb;
  v_contract_key text;
  v_reconciled jsonb := '[]'::jsonb;
  v_ambiguous jsonb := '[]'::jsonb;
begin
  perform set_config('lock_timeout', '2s', true);
  perform set_config('statement_timeout', '8s', true);

  for v_row in
    select
      p.id as plan_id, p.run_id, p.run_request_id, p.account_id,
      p.plan as existing_plan, r.status as run_status,
      r.performance_summary, q.status as request_status,
      q.root_business_session_id, q.execution_attempt_no, q.retry_index,
      a.status as account_status,
      a.admin_lifecycle_status as account_lifecycle_status
    from public.account_session_resume_plans p
    join public.ig_runs r on r.id = p.run_id
    join public.account_run_requests q
      on q.id = p.run_request_id and q.run_id = p.run_id
    join public.ig_accounts a on a.id = p.account_id
    where p.resume_state = 'run_active'
      and r.status in ('completed', 'failed', 'blocked', 'cancelled', 'canceled', 'stopped')
      and q.status in ('completed', 'failed', 'blocked', 'cancelled', 'canceled')
      and not exists (
        select 1 from public.ig_runs ar
        where ar.account_id = p.account_id and ar.status in ('pending', 'running')
      )
      and not exists (
        select 1 from public.account_run_requests aq
        where aq.account_id = p.account_id
          and aq.status in ('pending', 'claimed', 'starting', 'running')
      )
      and not exists (
        select 1 from public.auto_restart_device_locks dl
        where dl.account_id = p.account_id and dl.lease_expires_at > v_now
      )
    order by p.last_updated_at
    for update of p skip locked
    limit greatest(1, least(coalesce(p_limit, 100), 100))
  loop
    v_final_plan := v_row.performance_summary->'auto_restart_resume_plan';
    if jsonb_typeof(v_final_plan) = 'object'
       and v_final_plan->>'session_termination_class' in ('partial_resumable', 'partial_safe_stopped')
       and coalesce(v_final_plan->>'restart_allowed', '') = 'true'
       and v_row.root_business_session_id is not null
       and v_row.execution_attempt_no between 1 and 3
       and v_row.retry_index between 0 and 2
       and lower(coalesce(v_row.account_status, '')) not in (
         'cancelled', 'canceled', 'archived', 'trashed', 'deleted',
         'rolled_back', 'rolled_back_test_onboarding', 'released_terminal',
         'tombstone', 'tombstoned'
       )
       and lower(coalesce(v_row.account_lifecycle_status, '')) not in (
         'cancelled', 'canceled', 'archived', 'trashed', 'deleted',
         'rolled_back', 'rolled_back_test_onboarding', 'released_terminal',
         'tombstone', 'tombstoned'
       )
       and coalesce(v_final_plan->'quota_remaining'->>'total', '') ~ '^[0-9]+$'
       and (v_final_plan->'quota_remaining'->>'total')::integer > 0
       and (
         coalesce(v_final_plan->'phases_to_run'->>'welcome', '') = 'true'
         or coalesce(v_final_plan->'phases_to_run'->>'follow', '') = 'true'
         or coalesce(v_final_plan->'phases_to_run'->>'unfollow', '') = 'true'
       )
       and jsonb_array_length(
         case when jsonb_typeof(v_final_plan->'unsafe_markers') = 'array'
           then v_final_plan->'unsafe_markers' else '[]'::jsonb end
       ) = 0 then
      v_contract_key := concat_ws(
        ':', v_row.run_id::text, v_row.run_request_id::text,
        v_row.root_business_session_id::text, v_row.execution_attempt_no::text
      );
      update public.account_session_resume_plans
      set resume_stage = 'phases',
          resume_state = 'resume_requested',
          restart_allowed = true,
          restart_block_reason = coalesce(v_final_plan->>'restart_block_reason', ''),
          plan = coalesce(v_row.existing_plan, '{}'::jsonb) || v_final_plan || jsonb_build_object(
            'root_business_session_id', v_row.root_business_session_id,
            'execution_attempt_no', v_row.execution_attempt_no,
            'attempt_id', v_row.execution_attempt_no,
            'retry_index', v_row.retry_index,
            'resume_state_contract_version', 'golden_resume_plan_contract_v1',
            'business_outcome', v_final_plan->>'session_termination_class',
            'auto_restart_decision', 'schedule_resume',
            'resume_contract_key', v_contract_key,
            'terminal_plan_digest', encode(extensions.digest(convert_to(v_final_plan::text, 'UTF8'), 'sha256'), 'hex')
          ),
          last_updated_at = v_now
      where id = v_row.plan_id;
      v_reconciled := v_reconciled || jsonb_build_array(jsonb_build_object(
        'run_id', v_row.run_id,
        'request_id', v_row.run_request_id,
        'resume_state', 'resume_requested',
        'auto_restart_decision', 'schedule_resume'
      ));
    else
      v_ambiguous := v_ambiguous || jsonb_build_array(jsonb_build_object(
        'run_id', v_row.run_id,
        'request_id', v_row.run_request_id,
        'reason', 'stale_resume_plan_evidence_ambiguous'
      ));
    end if;
  end loop;

  return jsonb_build_object(
    'ok', true,
    'contract_version', 'golden_resume_plan_contract_v1',
    'reconciled_count', jsonb_array_length(v_reconciled),
    'reconciled', v_reconciled,
    'ambiguous_count', jsonb_array_length(v_ambiguous),
    'ambiguous', v_ambiguous,
    'attempts_consumed', 0,
    'requests_enqueued', 0
  );
end;
$$;

comment on function public.resume_state_schema_contract_v1()
  is 'GOLDEN_RESUME_PLAN_CONTRACT_V1 registry; outcomes are separate from lifecycle states.';
comment on function public.persist_account_session_resume_plan_v1(uuid,uuid,uuid,smallint,text,text,boolean,text,text,jsonb,text)
  is 'GOLDEN_RESUME_PLAN_CONTRACT_V1 authoritative idempotent terminal-plan persistence.';
comment on function public.reconcile_stale_account_session_resume_plans_v1(integer)
  is 'GOLDEN_RESUME_PLAN_CONTRACT_V1 bounded terminal run_active reconciliation; never enqueues.';

revoke all on function public.resume_state_schema_contract_v1()
  from public, anon, authenticated;
revoke all on function public.persist_account_session_resume_plan_v1(uuid,uuid,uuid,smallint,text,text,boolean,text,text,jsonb,text)
  from public, anon, authenticated;
revoke all on function public.reconcile_stale_account_session_resume_plans_v1(integer)
  from public, anon, authenticated;
grant execute on function public.resume_state_schema_contract_v1() to service_role;
grant execute on function public.persist_account_session_resume_plan_v1(uuid,uuid,uuid,smallint,text,text,boolean,text,text,jsonb,text)
  to service_role;
grant execute on function public.reconcile_stale_account_session_resume_plans_v1(integer)
  to service_role;
