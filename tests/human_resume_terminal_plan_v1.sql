-- Disposable test catalog, never installed in production.
CREATE OR REPLACE FUNCTION public.is_zero_business_sigterm_resume_child_v1(p_source_run_id uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
  select exists (
    select 1
    from public.incident_resume_authorizations a
    join public.ig_runs child on child.account_id = a.account_id
    join public.account_run_requests q on q.run_id = child.id and q.account_id = a.account_id
    where coalesce(a.source_run_id, a.run_id) = p_source_run_id
      and a.retry_generation < 2
      and child.id = (
        select latest.id from public.ig_runs latest
        where latest.account_id = a.account_id
        order by latest.created_at desc, latest.id desc limit 1
      )
      and lower(coalesce(child.status, '')) = 'failed'
      and q.status = 'failed'
      and q.error_code = 'worker_exit_nonzero'
      and coalesce((child.performance_summary ->> 'exit_code')::integer, 0) = 143
      and q.metadata_safe ->> 'authorization_id' = a.id::text
      and q.metadata_safe ->> 'recovery_mode' = 'human_confirmed_resume'
      and coalesce(
        q.metadata_safe ->> 'source_run_id',
        q.metadata_safe ->> 'original_run_id',
        q.metadata_safe ->> 'prior_run_id',
        q.metadata_safe ->> 'previous_run_id',
        q.metadata_safe #>> '{resume_plan,source_run_id}',
        q.metadata_safe #>> '{resume_plan,prior_run_id}'
      ) = p_source_run_id::text
      and coalesce(child.total_follow, 0) = 0
      and coalesce(child.total_like, 0) = 0
      and coalesce(child.total_dm, 0) = 0
      and coalesce(child.total_story, 0) = 0
      and coalesce((child.performance_summary #>> '{unfollow,verified_unfollows}')::integer, 0) = 0
      and coalesce((child.performance_summary #>> '{session_counters,successful_interactions}')::integer, 0) = 0
      and not exists (
        select 1 from public.ig_action_logs l
        where l.run_id = child.id
          and l.status in ('success','completed','verified','persisted')
          and l.action_type in ('follow','unfollow','like','dm','welcome_dm','outreach_dm')
      )
  );
$function$
;
revoke all on function public.is_zero_business_sigterm_resume_child_v1(uuid) from public,anon,authenticated;
grant execute on function public.is_zero_business_sigterm_resume_child_v1(uuid) to service_role;

create schema human_resume_test;
create table human_resume_test.function_before as select pg_get_functiondef('public.reconcile_stale_account_session_resume_plans_v1(integer)'::regprocedure) as definition;
create table human_resume_test.permissions_before as select proacl::text,prosecdef,proconfig::text from pg_proc where oid='public.reconcile_stale_account_session_resume_plans_v1(integer)'::regprocedure;

-- Exact read-only production dependency: latest real run authority.
CREATE OR REPLACE FUNCTION public.reconcile_resolved_incident_resume_windows_v1()
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_row record;
  v_result jsonb;
  v_prepared integer := 0;
  v_armed integer := 0;
  v_superseded_plans integer := 0;
  v_superseded_authorizations integer := 0;
begin
  update public.account_session_resume_plans p
  set resume_state = 'not_recoverable',
      restart_allowed = false,
      restart_block_reason = 'resume_source_run_superseded',
      last_updated_at = now()
  where p.resume_state in ('run_active', 'partial_resumable', 'awaiting_human_resume_authorization')
    and not public.is_zero_business_sigterm_resume_child_v1(p.run_id)
    and exists (
      select 1 from public.ig_runs source_run
      join lateral (
        select latest.id from public.ig_runs latest
        where latest.account_id = source_run.account_id
        order by latest.created_at desc, latest.id desc limit 1
      ) current_run on true
      where source_run.id = p.run_id and current_run.id is distinct from p.run_id
    );
  get diagnostics v_superseded_plans = row_count;

  update public.incident_resume_authorizations a
  set status = 'expired',
      expired_at = coalesce(a.expired_at, now()),
      consume_error = coalesce(a.consume_error, 'resume_source_run_superseded'),
      metadata_safe = coalesce(a.metadata_safe, '{}'::jsonb) || jsonb_build_object(
        'terminal_reconciliation', jsonb_build_object(
          'reason', 'resume_source_run_superseded', 'reconciled_at', now()
        )
      ), updated_at = now()
  where a.status = 'armed'
    and not public.is_zero_business_sigterm_resume_child_v1(coalesce(a.source_run_id, a.run_id))
    and exists (
      select 1 from public.ig_runs source_run
      join lateral (
        select latest.id from public.ig_runs latest
        where latest.account_id = source_run.account_id
        order by latest.created_at desc, latest.id desc limit 1
      ) current_run on true
      where source_run.id = a.run_id and current_run.id is distinct from a.run_id
    );
  get diagnostics v_superseded_authorizations = row_count;

  for v_row in
    select i.id as incident_id
    from public.account_incidents i
    join public.ig_runs r on r.id = i.run_id and r.account_id = i.account_id
    where i.status = 'resolved'
      and i.incident_type in (
        'run_identity_verification_failed','active_instagram_account_mismatch',
        'account_login_required','assigned_instagram_package_unavailable',
        'run_device_unavailable','run_worker_failure'
      )
      and lower(coalesce(r.status, '')) in ('failed','stopped','canceled')
      and r.id = (
        select latest.id from public.ig_runs latest
        where latest.account_id = i.account_id
        order by latest.created_at desc, latest.id desc limit 1
      )
      and i.id = (
        select latest_incident.id from public.account_incidents latest_incident
        where latest_incident.account_id = i.account_id
          and latest_incident.run_id = i.run_id
          and latest_incident.status = 'resolved'
          and latest_incident.incident_type in (
            'run_identity_verification_failed','active_instagram_account_mismatch',
            'account_login_required','assigned_instagram_package_unavailable',
            'run_device_unavailable','run_worker_failure'
          )
        order by latest_incident.resolved_at desc nulls last,
                 latest_incident.created_at desc, latest_incident.id desc limit 1
      )
    order by i.account_id for update of i skip locked
  loop
    v_result := public.prepare_resolved_incident_recovery_v2(v_row.incident_id);
    if coalesce((v_result ->> 'prepared')::boolean, false) then v_prepared := v_prepared + 1; end if;
    if coalesce((v_result ->> 'armed')::boolean, false) then v_armed := v_armed + 1; end if;
  end loop;

  return jsonb_build_object(
    'prepared_count', v_prepared,
    'armed_count', v_armed,
    'superseded_plan_count', v_superseded_plans,
    'superseded_authorization_count', v_superseded_authorizations
  );
end
$function$
;

CREATE OR REPLACE FUNCTION public.auto_restart_latest_resume_plans_v1(p_account_ids uuid[])
 RETURNS TABLE(run_id uuid, run_request_id uuid, account_id uuid, restart_allowed boolean, restart_block_reason text, resume_state text, attempts_in_window integer, plan jsonb, zero_work_contract_version smallint, irreversible_work_state text, zero_work_certified_at timestamp with time zone, assignment_id uuid, device_id uuid, app_instance_id uuid, expected_package text, last_updated_at timestamp with time zone)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
  select distinct on (p.account_id)
    p.run_id, p.run_request_id, p.account_id, p.restart_allowed,
    p.restart_block_reason, p.resume_state, p.attempts_in_window::integer,
    coalesce(p.plan,'{}'::jsonb), p.zero_work_contract_version,
    p.irreversible_work_state, p.zero_work_certified_at, p.assignment_id,
    p.device_id, p.app_instance_id, p.expected_package, p.last_updated_at
  from public.account_session_resume_plans p
  where p.account_id=any(coalesce(p_account_ids,'{}'::uuid[]))
  order by p.account_id,p.last_updated_at desc nulls last,p.created_at desc,p.id desc
$function$

;
create table human_resume_test.catalog_before as select p.oid::regprocedure::text as signature,pg_get_functiondef(p.oid) as definition,p.proacl::text as acl,p.proconfig::text as config from pg_proc p where p.pronamespace='public'::regnamespace and p.prokind='f';
