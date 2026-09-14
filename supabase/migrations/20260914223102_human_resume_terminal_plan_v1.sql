-- Terminal ownership repair only; no authorization consumption or run creation.
begin;
do $precondition$
begin
  if to_regprocedure('public.reconcile_stale_account_session_resume_plans_v1(integer)') is null
     or encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.reconcile_stale_account_session_resume_plans_v1(integer)')),'UTF8')),'hex')
       not in ('94b4e34528a8d079db3659f1ba8e985ec7870e4a7f7d6ccdc196aaec3e75a725','55a840bb8e6f07263fcb9250d8cda54be26c224646f678e810fbb08ff69d2349') then
    raise exception 'human_resume_terminal_plan_dependency_drift';
  end if;
end;
$precondition$;

CREATE OR REPLACE FUNCTION public.reconcile_stale_account_session_resume_plans_v1(p_limit integer DEFAULT 100)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_now timestamptz := clock_timestamp();
  v_row record;
  v_final_plan jsonb;
  v_contract_key text;
  v_incident record;
  v_reconciled jsonb := '[]'::jsonb;
  v_ambiguous jsonb := '[]'::jsonb;
begin
  perform set_config('lock_timeout', '2s', true);
  perform set_config('statement_timeout', '8s', true);

  for v_row in
    select
      p.id as plan_id, p.run_id, p.run_request_id, p.account_id,
      p.plan as existing_plan, r.status as run_status, p.device_id as plan_device_id,
      r.performance_summary, q.status as request_status,
      q.metadata_safe as request_metadata,
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
    -- Preserve the existing partial-safe branch above byte-for-byte.
    elsif v_row.request_metadata->>'recovery_mode' = 'human_confirmed_resume' then
      select i.id, i.reason into v_incident
      from public.ig_runs r
      join public.account_run_requests q
        on q.id=v_row.run_request_id and q.run_id=r.id and q.account_id=r.account_id
      join public.incident_resume_authorizations auth
        on auth.consumed_by_request_id=q.id and auth.account_id=r.account_id
       and auth.status='consumed' and auth.consumed_at is not null
       and auth.id::text=q.metadata_safe->>'authorization_id'
      join public.account_session_resume_plans source_plan
        on source_plan.id=auth.resume_plan_id and source_plan.account_id=r.account_id
       and source_plan.run_id=auth.source_run_id
       and source_plan.run_request_id=auth.source_request_id
       and source_plan.id::text=q.metadata_safe->>'resume_plan_id'
      join public.ig_runs source_run
        on source_run.id=source_plan.run_id and source_run.account_id=r.account_id
       and source_run.id<>r.id and source_run.created_at<r.created_at
       and source_run.id::text=coalesce(nullif(q.metadata_safe->>'original_run_id',''),q.metadata_safe->>'prior_run_id')
       and coalesce(nullif(q.metadata_safe->>'prior_run_id',''),source_run.id::text)=source_run.id::text
      join public.account_incidents i on i.run_id=r.id and i.account_id=r.account_id
      where r.id=v_row.run_id and r.account_id=v_row.account_id
        and r.status='failed' and r.finished_at is not null
        and q.status='failed' and q.completed_at is not null and q.cancel_requested_at is null
        and q.metadata_safe->>'recovery_mode'='human_confirmed_resume'
        and i.archived_at is null and i.status in ('open','resolved')
        and nullif(btrim(i.reason),'') is not null
        and i.incident_type in ('run_identity_verification_failed',
          'active_instagram_account_mismatch','account_login_required',
          'assigned_instagram_package_unavailable','run_device_unavailable',
          'run_worker_failure','instagram_account_restriction')
        and r.id=(select latest.id from public.ig_runs latest
          where latest.account_id=r.account_id order by latest.created_at desc,latest.id desc limit 1)
        and i.id=(select latest.id from public.account_incidents latest
          where latest.account_id=r.account_id and latest.run_id=r.id
          order by latest.created_at desc,latest.id desc limit 1)
        and not exists(select 1 from public.account_run_requests active
          where active.account_id=r.account_id
            and active.status in ('pending','queued','claimed','starting','running','stopping'))
        and not exists(select 1 from public.ig_runs active
          where active.account_id=r.account_id and active.status in ('pending','running','stopping'))
        and not exists(select 1 from public.auto_restart_device_locks lease
          where (lease.account_id=r.account_id or lease.device_id=v_row.plan_device_id)
            and lease.lease_expires_at>clock_timestamp())
      for update of r,q,i skip locked;
      if found then
        update public.account_session_resume_plans
        set resume_state='awaiting_human_resume_authorization', restart_allowed=false,
            restart_block_reason='awaiting_human_resume_authorization',
            incident_id=v_incident.id, terminal_reason_code=v_incident.reason,
            last_updated_at=v_now
        where id=v_row.plan_id and resume_state='run_active';
        v_reconciled := v_reconciled || jsonb_build_array(jsonb_build_object(
          'run_id',v_row.run_id,'request_id',v_row.run_request_id,
          'incident_id',v_incident.id,'resume_state','awaiting_human_resume_authorization',
          'auto_restart_decision','await_human_authorization'));
      else
        v_ambiguous := v_ambiguous || jsonb_build_array(jsonb_build_object(
          'run_id',v_row.run_id,'request_id',v_row.run_request_id,
          'reason','stale_resume_plan_evidence_ambiguous'));
      end if;
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
$function$
;
notify pgrst, 'reload schema';
commit;
