-- Local candidate only. Preserve installed functions and existing exhaustion states.
BEGIN;

CREATE OR REPLACE FUNCTION public.prepare_resolved_incident_recovery_v2(p_incident_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_incident public.account_incidents%rowtype;
  v_run public.ig_runs%rowtype;
  v_latest_run_id uuid;
  v_request public.account_run_requests%rowtype;
  v_assignment public.account_assignments%rowtype;
  v_plan public.account_session_resume_plans%rowtype;
  v_authorization_id uuid;
  v_authorization_status text;
  v_window_key text;
  v_superseded_authorizations integer := 0;
begin
  select i.* into v_incident
  from public.account_incidents i
  where i.id = p_incident_id
  for update;

  if v_incident.id is null or v_incident.status <> 'resolved' then
    return jsonb_build_object('prepared', false, 'reason', 'incident_not_resolved');
  end if;
  if v_incident.account_id is null or v_incident.run_id is null then
    return jsonb_build_object('prepared', false, 'reason', 'incident_lineage_missing');
  end if;
  if v_incident.incident_type not in (
    'run_identity_verification_failed',
    'active_instagram_account_mismatch',
    'account_login_required',
    'assigned_instagram_package_unavailable',
    'run_device_unavailable',
    'run_worker_failure'
  ) then
    return jsonb_build_object('prepared', false, 'reason', 'incident_not_recovery_eligible');
  end if;

  select r.* into v_run
  from public.ig_runs r
  where r.id = v_incident.run_id
    and r.account_id = v_incident.account_id;

  if v_run.id is null or lower(coalesce(v_run.status, '')) not in ('failed', 'stopped', 'canceled') then
    return jsonb_build_object('prepared', false, 'reason', 'source_run_not_failed');
  end if;

  select r.id into v_latest_run_id
  from public.ig_runs r
  where r.account_id = v_incident.account_id
  order by r.created_at desc, r.id desc
  limit 1;

  if v_latest_run_id is distinct from v_incident.run_id then
    update public.incident_resume_authorizations a
    set status = 'expired',
        expired_at = coalesce(a.expired_at, now()),
        consume_error = coalesce(a.consume_error, 'resume_source_run_superseded'),
        metadata_safe = coalesce(a.metadata_safe, '{}'::jsonb) || jsonb_build_object(
          'terminal_reconciliation', jsonb_build_object(
            'reason', 'resume_source_run_superseded',
            'latest_run_id', v_latest_run_id,
            'reconciled_at', now()
          )
        ),
        updated_at = now()
    where a.incident_id = v_incident.id
      and a.status = 'armed';

    update public.account_session_resume_plans p
    set resume_state = 'not_recoverable',
        restart_allowed = false,
        restart_block_reason = 'resume_source_run_superseded',
        last_updated_at = now()
    where p.run_id = v_incident.run_id
      and p.resume_state in ('run_active', 'partial_resumable', 'awaiting_human_resume_authorization');

    update public.account_incidents i
    set metadata = coalesce(i.metadata, '{}'::jsonb) || jsonb_build_object(
          'recovery', coalesce(i.metadata -> 'recovery', '{}'::jsonb) || jsonb_build_object(
            'state', 'resume_source_run_superseded',
            'latest_canonical_run_id', v_latest_run_id,
            'updated_at', now()
          )
        ),
        updated_at = now()
    where i.id = v_incident.id;

    return jsonb_build_object(
      'prepared', false,
      'reason', 'resume_source_run_superseded',
      'latest_run_id', v_latest_run_id
    );
  end if;

  update public.incident_resume_authorizations a
  set status = 'expired',
      expired_at = coalesce(a.expired_at, now()),
      consume_error = coalesce(
        a.consume_error,
        case when a.run_id is distinct from v_incident.run_id
          then 'resume_source_run_superseded'
          else 'resume_incident_superseded'
        end
      ),
      metadata_safe = coalesce(a.metadata_safe, '{}'::jsonb) || jsonb_build_object(
        'terminal_reconciliation', jsonb_build_object(
          'reason', case when a.run_id is distinct from v_incident.run_id
            then 'resume_source_run_superseded'
            else 'resume_incident_superseded'
          end,
          'latest_run_id', v_incident.run_id,
          'latest_incident_id', v_incident.id,
          'reconciled_at', now()
        )
      ),
      updated_at = now()
  where a.account_id = v_incident.account_id
    and a.status = 'armed'
    and (
      a.run_id is distinct from v_incident.run_id
      or a.incident_id is distinct from v_incident.id
    );
  get diagnostics v_superseded_authorizations = row_count;

  select q.* into v_request
  from public.account_run_requests q
  where q.run_id = v_incident.run_id
    and q.account_id = v_incident.account_id
  order by q.created_at desc, q.id desc
  limit 1;

  -- Exhausted source lineage cannot be rearmed by human resolution.
  -- Eligibility is owned by canonical blockers, not this terminal plan.
  if v_request.id is not null and v_request.root_business_session_id is not null
     and v_request.execution_attempt_no = 3
     and v_request.status in ('failed', 'completed', 'canceled', 'blocked') then
    update public.account_session_resume_plans p
    set resume_state='not_recoverable', restart_allowed=false,
        restart_block_reason='execution_attempt_exhausted', last_updated_at=now()
    where p.run_id=v_incident.run_id and p.account_id=v_incident.account_id
      and p.run_request_id=v_request.id
      and (p.resume_state, p.restart_allowed, p.restart_block_reason)
        is distinct from ('not_recoverable', false, 'execution_attempt_exhausted');
    update public.incident_resume_authorizations a
    set status='expired', expired_at=coalesce(a.expired_at,now()),
        consume_error='execution_attempt_exhausted', updated_at=now()
    where a.account_id=v_incident.account_id and a.run_id=v_incident.run_id
      and a.status='armed';
    return jsonb_build_object('prepared',false,'armed',false,'reason','execution_attempt_exhausted');
  end if;

  select aa.* into v_assignment
  from public.account_assignments aa
  where aa.account_id = v_incident.account_id
    and aa.status in ('reserved', 'active')
    and coalesce(aa.schedule_mode, '') <> 'manual_only'
  order by
    case when aa.starts_at <= now() and now() < aa.ends_at then 0 else 1 end,
    aa.starts_at desc,
    aa.created_at desc
  limit 1;

  insert into public.account_session_resume_plans (
    run_id, run_request_id, account_id, assignment_id, device_id,
    app_instance_id, expected_username, scheduled_window_start,
    scheduled_window_end, resume_window_key, source_surface, run_trigger,
    resume_stage, resume_state, restart_allowed, restart_block_reason,
    terminal_reason_code, incident_id, plan, test
  ) values (
    v_incident.run_id, v_request.id, v_incident.account_id, v_assignment.id,
    v_assignment.device_id, v_assignment.app_instance_id,
    v_incident.account_username, v_assignment.starts_at, v_assignment.ends_at,
    case when v_assignment.id is null then null
      else v_incident.account_id::text || ':' || v_assignment.starts_at::text end,
    coalesce(v_request.source_surface, 'incident_resolution'), 'scheduler',
    'preflight', 'awaiting_human_resume_authorization', true,
    'resolved_incident_live_plan_rebuild',
    coalesce(v_incident.reason, v_incident.failure_reason), v_incident.id,
    jsonb_build_object(
      'schema', 'RESOLVED_INCIDENT_PLAN_SHELL_V1',
      'source_run_id', v_incident.run_id,
      'source_request_id', v_request.id,
      'business_phases_source', 'live_canonical_candidate_only',
      'cursor_invented', false,
      'created_from_resolution', true
    ),
    lower(coalesce(v_incident.metadata ->> 'test', 'false')) in ('true', '1', 'yes')
  )
  on conflict (run_id) do update
  set run_request_id = coalesce(public.account_session_resume_plans.run_request_id, excluded.run_request_id),
      assignment_id = coalesce(excluded.assignment_id, public.account_session_resume_plans.assignment_id),
      device_id = coalesce(excluded.device_id, public.account_session_resume_plans.device_id),
      app_instance_id = coalesce(excluded.app_instance_id, public.account_session_resume_plans.app_instance_id),
      scheduled_window_start = coalesce(excluded.scheduled_window_start, public.account_session_resume_plans.scheduled_window_start),
      scheduled_window_end = coalesce(excluded.scheduled_window_end, public.account_session_resume_plans.scheduled_window_end),
      resume_window_key = coalesce(excluded.resume_window_key, public.account_session_resume_plans.resume_window_key),
      resume_state = 'awaiting_human_resume_authorization',
      restart_allowed = true,
      restart_block_reason = 'resolved_incident_live_plan_rebuild',
      terminal_reason_code = coalesce(public.account_session_resume_plans.terminal_reason_code, excluded.terminal_reason_code),
      incident_id = excluded.incident_id,
      plan = coalesce(public.account_session_resume_plans.plan, '{}'::jsonb) || jsonb_build_object(
        'resolution_finalization', jsonb_build_object(
          'contract', 'RESOLVED_INCIDENT_RECOVERY_FINALIZATION_V2',
          'business_phases_source', 'live_canonical_candidate_only',
          'cursor_invented', false,
          'updated_at', now()
        )
      ),
      last_updated_at = now()
  where public.account_session_resume_plans.resume_state in (
    'run_active', 'partial_resumable', 'awaiting_human_resume_authorization', 'not_recoverable'
  )
  returning * into v_plan;

  if v_plan.id is null then
    select p.* into v_plan
    from public.account_session_resume_plans p
    where p.run_id = v_incident.run_id;
  end if;

  if v_plan.id is null or v_plan.resume_state <> 'awaiting_human_resume_authorization' then
    return jsonb_build_object('prepared', false, 'reason', 'resume_plan_not_recoverable');
  end if;

  if v_assignment.id is null
     or not (v_assignment.starts_at <= now() and now() < v_assignment.ends_at) then
    update public.account_incidents i
    set metadata = coalesce(i.metadata, '{}'::jsonb) || jsonb_build_object(
          'recovery', coalesce(i.metadata -> 'recovery', '{}'::jsonb) || jsonb_build_object(
            'state', 'awaiting_scheduled_window',
            'resume_plan_id', v_plan.id,
            'source', 'resolved_incident_recovery_finalization_v2',
            'updated_at', now()
          )
        ),
        updated_at = now()
    where i.id = v_incident.id;

    return jsonb_build_object(
      'prepared', true,
      'armed', false,
      'reason', 'assignment_window_closed',
      'resume_plan_id', v_plan.id,
      'superseded_authorizations', v_superseded_authorizations
    );
  end if;

  v_window_key := v_incident.account_id::text || ':' || v_assignment.starts_at::text;

  if exists (
    select 1
    from public.incident_resume_authorizations a
    where a.incident_id = v_incident.id
      and a.resume_window_key = v_window_key
      and a.status in ('armed', 'consumed', 'expired')
  ) then
    select a.id, a.status into v_authorization_id, v_authorization_status
    from public.incident_resume_authorizations a
    where a.incident_id = v_incident.id
      and a.resume_window_key = v_window_key
    order by a.created_at desc
    limit 1;
  else
    insert into public.incident_resume_authorizations (
      incident_id, account_id, run_id, resume_plan_id, resume_window_key,
      scheduled_window_start, scheduled_window_end, status, armed_source,
      resolution_note, metadata_safe, test
    ) values (
      v_incident.id, v_incident.account_id, v_incident.run_id, v_plan.id,
      v_window_key, v_assignment.starts_at, v_assignment.ends_at, 'armed',
      'resolved_incident_recovery_finalization_v2',
      left(coalesce(v_incident.resolution_note, ''), 500),
      jsonb_build_object(
        'incident_type', v_incident.incident_type,
        'reason_code', coalesce(v_incident.reason, v_incident.failure_reason),
        'generic_resolved_incident_rule', true,
        'plan_contract', 'RESOLVED_INCIDENT_PLAN_SHELL_V1',
        'business_phases_source', 'live_canonical_candidate_only',
        'cursor_invented', false
      ),
      v_plan.test
    )
    on conflict do nothing
    returning id, status into v_authorization_id, v_authorization_status;
  end if;

  if v_authorization_status = 'armed' then
    update public.account_incidents i
    set metadata = coalesce(i.metadata, '{}'::jsonb) || jsonb_build_object(
          'recovery', coalesce(i.metadata -> 'recovery', '{}'::jsonb) || jsonb_build_object(
            'state', 'ready_to_resume',
            'authorization_id', v_authorization_id,
            'resume_plan_id', v_plan.id,
            'source', 'resolved_incident_recovery_finalization_v2',
            'updated_at', now()
          )
        ),
        updated_at = now()
    where i.id = v_incident.id;
  end if;

  return jsonb_build_object(
    'prepared', true,
    'armed', v_authorization_status = 'armed',
    'reason', case
      when v_authorization_status = 'armed' then 'ready_to_resume'
      when v_authorization_status in ('consumed', 'expired') then 'authorization_already_terminal'
      else 'authorization_conflict'
    end,
    'resume_plan_id', v_plan.id,
    'authorization_id', v_authorization_id,
    'superseded_authorizations', v_superseded_authorizations
  );
end
$function$
;

CREATE OR REPLACE FUNCTION public.restriction_resolution_lifecycle_eligible_v1(p_hold_id uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE
 SET search_path TO ''
AS $function$
  select exists (
    select 1 from public.instagram_account_restriction_holds h
    join public.account_incidents i on i.id=h.incident_id and i.account_id=h.account_id
    join public.ig_accounts a on a.id=h.account_id
    join public.ig_account_settings s on s.account_id=a.id
    where h.id=p_hold_id and (h.status in ('active','verification_required')
      or (h.status='superseded' and h.metadata_safe#>>'{resolved_growth_ready_v1,incident_resolution_retires_hold}'='true'))
      and h.previous_admin_lifecycle_status='active'
      and h.verified_cleared_at is null and h.verified_by_run_id is null
      and i.status='resolved' and i.resolved_at is not null and i.resolved_by is not null and i.archived_at is null
      and i.incident_type='instagram_account_restriction'
      and lower(coalesce(i.metadata->>'security_incident','false')) not in ('true','1','yes')
      and a.status='active' and a.archived_at is null and a.trashed_at is null
      and a.scheduled_trash_at is null and a.scheduled_delete_at is null
      and s.account_status='active' and not coalesce(s.manual_stop_requested,false)
      and coalesce(s.pause_account_days,0)=0 and coalesce(s.pause_account_until,'')=''
      and not (h.metadata_safe ? 'independent_lifecycle_write_at')
      and not exists(select 1 from public.commercial_account_lifecycle_states c
        where c.account_id=a.id and (c.commercial_state<>'active' or c.stripe_billing_paused
          or to_jsonb(c)->>'terminal_cancel_operation_id' is not null))
      and not exists(select 1 from public.canonical_active_operational_blockers_v1(array[a.id]))
      and not exists(select 1 from public.instagram_account_restriction_holds other_hold
        where other_hold.account_id=a.id and other_hold.id<>h.id
          and other_hold.status in ('active','verification_required'))
      and not exists(select 1 from public.ig_action_logs l where l.account_id=a.id
        and l.action_type='account_admin_status_changed' and l.created_at>=h.detected_at)
      and (
        (a.admin_lifecycle_status='active'
          and h.metadata_safe#>>'{resolved_growth_ready_v1,incident_id}'=i.id::text
          and h.metadata_safe#>>'{resolved_growth_ready_v1,lifecycle_version}'=i.lifecycle_version::text
          and h.metadata_safe#>>'{resolved_growth_ready_v1,resolved_at}'=to_jsonb(i.resolved_at)#>>'{}')
        or (a.admin_lifecycle_status='paused'
          -- Legacy convergence requires exact pause ownership, never time proximity.
          and to_jsonb(a.updated_at)#>>'{}'=coalesce(h.metadata_safe->>'restriction_pause_updated_at',to_jsonb(h.detected_at)#>>'{}'))
      )
  );
$function$
;

CREATE OR REPLACE FUNCTION public.restore_resolved_restriction_lifecycle_v1(p_incident_id uuid)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path TO ''
AS $function$
declare
  i public.account_incidents%rowtype;
  h public.instagram_account_restriction_holds%rowtype;
  account_id_value uuid;
  lifecycle_owned boolean;
begin
  select account_id into account_id_value from public.account_incidents where id=p_incident_id;
  perform 1 from public.ig_accounts where id=account_id_value for update;
  select * into i from public.account_incidents where id=p_incident_id for update skip locked;
  if i.id is null or i.status<>'resolved' or i.resolved_at is null then return false; end if;
  select * into h from public.instagram_account_restriction_holds
    where incident_id=i.id and account_id=i.account_id for update;
  if h.id is null or (h.status not in ('active','verification_required')
    and not (h.status='superseded' and h.metadata_safe#>>'{resolved_growth_ready_v1,incident_resolution_retires_hold}'='true')) then return false; end if;
  lifecycle_owned := public.restriction_resolution_lifecycle_eligible_v1(h.id);
  if h.status='superseded' and h.metadata_safe#>>'{resolved_growth_ready_v1,lifecycle_restored}'='true'
    then return false; end if;
  if h.status='superseded' and not lifecycle_owned then return false; end if;
  -- Human resolution retires incident-derived authority, never asserts physical proof.
  update public.instagram_account_restriction_holds
  set status='superseded', human_resolved_at=i.resolved_at,
      verification_required_at=null,
      metadata_safe=coalesce(metadata_safe,'{}'::jsonb)
        || jsonb_build_object('incident_resolved_but_physical_preflight_pending',false,
           'resolved_growth_ready_v1',jsonb_build_object(
             'incident_id',i.id,'lifecycle_version',i.lifecycle_version,'resolved_at',i.resolved_at,
             'restored_at',now(),'physical_verification_pending',false,
             'lifecycle_restored',lifecycle_owned,
             'incident_resolution_retires_hold',true)),
      updated_at=now()
  where id=h.id;
  if lifecycle_owned then
    update public.ig_accounts set admin_lifecycle_status='active',updated_at=now()
      where id=i.account_id and admin_lifecycle_status='paused';
  end if;
  insert into public.ig_action_logs(account_id,action_type,status,message,metadata)
  values(i.account_id,'restriction_resolution_lifecycle_restored','success',
    'Resolved restriction retired its incident-derived hold; independent blockers preserved.',
    jsonb_build_object('hold_id',h.id,'incident_id',i.id,'lifecycle_version',i.lifecycle_version,
      'lifecycle_restored',lifecycle_owned,'physical_verification_pending',false));
  return true;
end;
$function$;

CREATE OR REPLACE FUNCTION public.mark_instagram_restriction_preflight_required_v1()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
begin
  if new.status='resolved' then
    perform public.restore_resolved_restriction_lifecycle_v1(h.incident_id)
      from public.instagram_account_restriction_holds h
      where h.account_id=new.account_id and (h.status in ('active','verification_required')
        or (h.status='superseded' and h.metadata_safe#>>'{resolved_growth_ready_v1,lifecycle_restored}'='false'));
  end if;
  return new;
end
$function$;

CREATE OR REPLACE FUNCTION public.sync_identity_guard_restriction_hold_v1()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_hold public.instagram_account_restriction_holds%rowtype;
  v_prior_hold public.instagram_account_restriction_holds%rowtype;
  v_previous_status text;
  v_reason text := coalesce(nullif(trim(new.reason), ''), nullif(trim(new.failure_reason), ''), 'instagram_account_restriction_unknown_scope');
begin
  if new.incident_type <> 'instagram_account_restriction'
     or new.account_id is null
     or v_reason not in (
       'instagram_account_restriction_messages_disabled',
       'instagram_account_restriction_unknown_scope'
     ) then
    return new;
  end if;

  -- Serialize every hold transition for an account and capture the lifecycle
  -- that existed before this restriction family acquired authority.
  select admin_lifecycle_status into v_previous_status
  from public.ig_accounts
  where id = new.account_id
  for update;

  select * into v_hold
  from public.instagram_account_restriction_holds
  where incident_id = new.id
  for update;

  if new.status in ('open', 'acknowledged') then
    if v_hold.id is null then
      select * into v_prior_hold
      from public.instagram_account_restriction_holds
      where account_id = new.account_id
        and incident_id <> new.id
        and status in ('active', 'verification_required')
      for update;

      if v_prior_hold.id is not null then
        v_previous_status := coalesce(
          nullif(v_prior_hold.previous_admin_lifecycle_status, ''),
          v_previous_status
        );
      end if;

      -- A freshly observed direct restriction is a new generation. Preserve
      -- any prior generation as evidence before installing the new authority.
      update public.instagram_account_restriction_holds
      set status = 'superseded',
          last_seen_at = now(),
          metadata_safe = coalesce(metadata_safe, '{}'::jsonb)
            || jsonb_build_object('superseded_by_incident_id', new.id),
          updated_at = now()
      where id = v_prior_hold.id;

      insert into public.instagram_account_restriction_holds (
        account_id,
        incident_id,
        stable_reason,
        status,
        previous_admin_lifecycle_status,
        metadata_safe
      ) values (
        new.account_id,
        new.id,
        v_reason,
        'active',
        v_previous_status,
        jsonb_build_object(
          'source', 'identity_guard_direct_restriction_incident',
          'restriction_reason', v_reason,
          'incident_created_at', new.created_at
        )
      ) returning * into v_hold;
    else
      update public.instagram_account_restriction_holds
      set status = case
            when status = 'verification_required' then 'active'
            else status
          end,
          human_resolved_at = case
            when status = 'verification_required' then null
            else human_resolved_at
          end,
          verification_required_at = case
            when status = 'verification_required' then null
            else verification_required_at
          end,
          last_seen_at = now(),
          occurrence_count = occurrence_count + 1,
          stable_reason = v_reason,
          metadata_safe = coalesce(metadata_safe, '{}'::jsonb)
            || jsonb_build_object('restriction_reason', v_reason),
          updated_at = now()
      where id = v_hold.id;
    end if;

    update public.ig_accounts
    set admin_lifecycle_status = 'paused', updated_at = now()
    where id = new.account_id
      and coalesce(admin_lifecycle_status, '') <> 'paused';
    -- Record the exact write owned by this restriction, not a guessed age.
    update public.instagram_account_restriction_holds h
    set metadata_safe=(h.metadata_safe - 'resolved_growth_ready_v1') || jsonb_build_object(
      'restriction_pause_updated_at',(select a.updated_at from public.ig_accounts a where a.id=new.account_id)),
      updated_at=now()
    where h.id=v_hold.id;
  elsif new.status = 'resolved' then
    perform public.restore_resolved_restriction_lifecycle_v1(new.id);
  end if;

  return new;
end
$function$
;

CREATE OR REPLACE FUNCTION public.reconcile_resolved_restriction_windows_v1()
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO ''
AS $function$
declare item record; restored integer:=0;
begin
  for item in select h.account_id,h.incident_id
    from public.instagram_account_restriction_holds h
    join public.account_incidents i on i.id=h.incident_id and i.account_id=h.account_id
    where (h.status in ('active','verification_required')
      or (h.status='superseded' and h.metadata_safe#>>'{resolved_growth_ready_v1,lifecycle_restored}'='false'))
      and i.status='resolved'
    order by h.account_id,h.incident_id
  loop
    perform 1 from public.ig_accounts where id=item.account_id for update skip locked;
    if not found then continue; end if;
    if public.restore_resolved_restriction_lifecycle_v1(item.incident_id) then restored:=restored+1; end if;
  end loop;
  -- Eligibility does not require a special physical-preflight request.
  return jsonb_build_object('restriction_lifecycles_restored',restored,'restriction_windows_armed',0);
end;
$function$;

CREATE OR REPLACE FUNCTION public.restriction_resume_request_authorized_v1(p_request_id uuid)
 RETURNS boolean
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
     or a.resolved_at is distinct from i.resolved_at or a.resolved_by is distinct from i.resolved_by
     or not coalesce((
       a.idempotency_key='incident-resume:'||i.account_id::text||':'||i.id::text||':v'||i.lifecycle_version::text
       or (a.idempotency_key='restriction-preflight:'||a.resume_window_key
         and a.metadata_safe#>>'{restriction_window_recovery_v1,incident_lifecycle_version}'=i.lifecycle_version::text
         and a.metadata_safe#>>'{restriction_window_recovery_v1,hold_generation}'=h.generation_id::text
         and a.metadata_safe#>>'{restriction_window_recovery_v1,verified_worker_sha}'=a.expected_worker_sha)
     ),false)
     or i.incident_type is distinct from 'instagram_account_restriction'
     or lower(coalesce(i.metadata->>'security_incident','false')) in ('true','1','yes')
     or h.id is null or not (h.status='verification_required'
       or (h.status='superseded'
         and h.metadata_safe#>>'{resolved_growth_ready_v1,incident_resolution_retires_hold}'='true'
         and h.metadata_safe#>>'{resolved_growth_ready_v1,incident_id}'=i.id::text
         and h.metadata_safe#>>'{resolved_growth_ready_v1,lifecycle_version}'=i.lifecycle_version::text))
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
          and acct.status='active' and (
            public.restriction_resolution_lifecycle_eligible_v1(h.id)
            or (acct.admin_lifecycle_status='active' and acct.archived_at is null
              and acct.trashed_at is null and acct.scheduled_trash_at is null
              and acct.scheduled_delete_at is null and h.status='superseded'
              and h.metadata_safe#>>'{resolved_growth_ready_v1,incident_resolution_retires_hold}'='true')))
     or exists(select 1 from public.commercial_account_lifecycle_states c
          where c.account_id=a.account_id and (c.commercial_state<>'active' or c.stripe_billing_paused
            or to_jsonb(c)->>'terminal_cancel_operation_id' is not null))
     or not exists(select 1 from public.ig_account_settings s where s.account_id=a.account_id
          and s.account_status='active' and not coalesce(s.manual_stop_requested,false)
          and coalesce(s.pause_account_days,0)=0 and coalesce(s.pause_account_until,'')='')
     or exists(select 1 from public.canonical_active_operational_blockers_v1(array[a.account_id]))
     or public.fleet_admission_reason_v1(a.account_id) is not null then
    return false;
  end if;
  return true;
end;
$function$
;

CREATE OR REPLACE FUNCTION public.release_instagram_action_restriction_hold_v1(p_account_id uuid, p_incident_id uuid, p_preflight_run_id uuid DEFAULT NULL::uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_hold public.instagram_account_restriction_holds%rowtype;
  v_incident public.account_incidents%rowtype;
  v_restored_status text;
  v_lifecycle_owned boolean;
begin
  if coalesce(auth.jwt() ->> 'role', '') <> 'service_role' then
    raise exception 'service_role_required' using errcode = '42501';
  end if;

  select * into v_hold
  from public.instagram_account_restriction_holds
  where account_id = p_account_id
    and incident_id = p_incident_id
  for update;
  if v_hold.id is null then
    return jsonb_build_object('ok', false, 'reason', 'restriction_hold_missing');
  end if;
  if v_hold.status = 'cleared' then
    return jsonb_build_object('ok', true, 'idempotent', true, 'hold_id', v_hold.id, 'hold_status', v_hold.status);
  end if;
  if v_hold.status <> 'verification_required' and not (
       v_hold.status='superseded'
       and v_hold.metadata_safe#>>'{resolved_growth_ready_v1,incident_resolution_retires_hold}'='true'
       and v_hold.metadata_safe#>>'{resolved_growth_ready_v1,incident_id}'=p_incident_id::text
     ) then
    return jsonb_build_object('ok', false, 'reason', 'restriction_preflight_not_authorized', 'hold_status', v_hold.status);
  end if;

  select * into v_incident
  from public.account_incidents
  where id = p_incident_id and account_id = p_account_id
  for share;
  if v_incident.id is null or v_incident.status <> 'resolved' then
    return jsonb_build_object('ok', false, 'reason', 'restriction_incident_not_resolved');
  end if;

  v_lifecycle_owned := public.restriction_resolution_lifecycle_eligible_v1(v_hold.id);

  update public.instagram_account_restriction_holds
  set status = 'cleared',
      verified_cleared_at = now(),
      verified_by_run_id = p_preflight_run_id,
      metadata_safe = coalesce(metadata_safe, '{}'::jsonb)
        || jsonb_build_object(
          'physical_preflight_passed', true,
          'physical_preflight_run_id', p_preflight_run_id,
          'hold_released_at', now()
        ),
      updated_at = now()
  where id = v_hold.id
  returning * into v_hold;

  v_restored_status := coalesce(nullif(v_hold.previous_admin_lifecycle_status, ''), 'active');
  if v_lifecycle_owned and not (v_hold.metadata_safe ? 'resolved_growth_ready_v1')
     and v_restored_status <> 'paused'
     and not exists (
       select 1 from public.instagram_account_restriction_holds h
       where h.account_id = p_account_id
         and h.id <> v_hold.id
         and h.status in ('active', 'verification_required')
     ) then
    update public.ig_accounts
    set admin_lifecycle_status = v_restored_status, updated_at = now()
    where id = p_account_id and admin_lifecycle_status = 'paused';
  end if;

  return jsonb_build_object(
    'ok', true,
    'idempotent', false,
    'hold_id', v_hold.id,
    'hold_status', v_hold.status,
    'restored_admin_lifecycle_status', v_restored_status,
    'physical_preflight_passed', true
  );
end
$function$
;

COMMIT;
