-- Resolution restores administrative readiness; physical admission stays separate.
begin;

do $precondition$
begin
  if to_regprocedure('public.reconcile_resolved_incident_resume_windows_v2()') is null or encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.reconcile_resolved_incident_resume_windows_v2()')),'UTF8')),'hex') not in ('355b9283872740b83acb0ad79b74f272931c17ca51cbdfcbebff5227f4443413','4a0138fc03ce00374ac97fc4428651c154fb9fb95022ee1efe51f983f125ef44') then
    raise exception 'resolved_growth_ready_dependency_drift: reconcile_resolved_incident_resume_windows_v2()';
  end if;
  if to_regprocedure('public.release_instagram_action_restriction_hold_v1(uuid,uuid,uuid)') is null or encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.release_instagram_action_restriction_hold_v1(uuid,uuid,uuid)')),'UTF8')),'hex') not in ('92bcb8d73d0a614660459790296c3f6d4ff15402a51c7bd43185007a9ce6094e','9366e0ae639d9cde04a18bfd5b11f6b6ae2c149c81336c3db0b2175e9a905ea3') then
    raise exception 'resolved_growth_ready_dependency_drift: release_instagram_action_restriction_hold_v1(uuid,uuid,uuid)';
  end if;
  if to_regprocedure('public.sync_identity_guard_restriction_hold_v1()') is null or encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.sync_identity_guard_restriction_hold_v1()')),'UTF8')),'hex') not in ('9bfcef9672a4370ea471754321a0cd9929fa529d5b21be1ab5f1b2952470a7c9','1f0e955bfe7272527a7117401e383293d1a2b24d4afa007a6f9ff361470c1bdc') then
    raise exception 'resolved_growth_ready_dependency_drift: sync_identity_guard_restriction_hold_v1()';
  end if;
  if to_regprocedure('public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer)') is null or encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer)')),'UTF8')),'hex') not in ('0e537b0a2e7740ce1078e594a438f0382947e67b1fd9527700cd2588afe88be1','5917937cdc4e14369bfc960537620b7ea7bf698a3e78e5a755a88d5c96e114b5') then
    raise exception 'resolved_growth_ready_dependency_drift: admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer)';
  end if;
  if to_regprocedure('public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer,uuid)') is null or encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer,uuid)')),'UTF8')),'hex') not in ('005ca2b3156b79519be047273ecc09f3af89d3e57bbbd4843e6543e59885f30d','2fb07cfb8d1c493fa580433de2f7c285c34af9976783d24b37aa4260f6b3f316') then
    raise exception 'resolved_growth_ready_dependency_drift: admit_account_run_attempt_v1(uuid,text,uuid,uuid,uuid,text,timestamptz,timestamptz,integer,uuid)';
  end if;
  if to_regprocedure('public.restriction_resume_request_authorized_v1(uuid)') is null or encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.restriction_resume_request_authorized_v1(uuid)')),'UTF8')),'hex') not in ('6beff1825e5394b7d4d7149fff5a3fef4497d08248364efcdd38a5b5f27556b9','4bbe7a0ec889cf393fbfa03971e770eaf494536d39032aeb7940beb3f1c772b3') then
    raise exception 'resolved_growth_ready_dependency_drift: restriction_resume_request_authorized_v1(uuid)';
  end if;
  if to_regprocedure('public.record_restriction_independent_lifecycle_write_v1()') is not null and encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.record_restriction_independent_lifecycle_write_v1()')),'UTF8')),'hex') <> '8825217780c835ffd18b9fc5a6188b6018815ed666323563199d076d46040bcd' then
    raise exception 'resolved_growth_ready_dependency_drift: record_restriction_independent_lifecycle_write_v1()';
  end if;
  if to_regprocedure('public.reconcile_resolved_restriction_windows_v1()') is not null and encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.reconcile_resolved_restriction_windows_v1()')),'UTF8')),'hex') <> 'e66343488d7f81903c255387476474ac9823bf1ff1dc06a26c3b8170088d1755' then
    raise exception 'resolved_growth_ready_dependency_drift: reconcile_resolved_restriction_windows_v1()';
  end if;
  if to_regprocedure('public.restriction_resolution_lifecycle_eligible_v1(uuid)') is not null and encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.restriction_resolution_lifecycle_eligible_v1(uuid)')),'UTF8')),'hex') <> 'f84144553bf7c63ac3f14feb0c83b52a40db713e80508babd097295099bd2e31' then
    raise exception 'resolved_growth_ready_dependency_drift: restriction_resolution_lifecycle_eligible_v1(uuid)';
  end if;
  if to_regprocedure('public.restore_resolved_restriction_lifecycle_v1(uuid)') is not null and encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.restore_resolved_restriction_lifecycle_v1(uuid)')),'UTF8')),'hex') <> '761342f15c3ff836277f0d11e772eb9fa1a882ac03a2dd7efd6bf9dc26c0410d' then
    raise exception 'resolved_growth_ready_dependency_drift: restore_resolved_restriction_lifecycle_v1(uuid)';
  end if;
  if to_regprocedure('public.restore_resolved_restriction_lifecycle_trigger_v1()') is not null and encode(sha256(convert_to(pg_get_functiondef(to_regprocedure('public.restore_resolved_restriction_lifecycle_trigger_v1()')),'UTF8')),'hex') <> '23f0c2568808f49dad2c8a122f06c3c4de3613ed5c651791b4bc38ac339498fd' then
    raise exception 'resolved_growth_ready_dependency_drift: restore_resolved_restriction_lifecycle_trigger_v1()';
  end if;
end;
$precondition$;

-- No account, username, date, or production Worker is special-cased.
create or replace function public.restriction_resolution_lifecycle_eligible_v1(p_hold_id uuid)
returns boolean language sql stable set search_path = '' as $function$
  select exists (
    select 1 from public.instagram_account_restriction_holds h
    join public.account_incidents i on i.id=h.incident_id and i.account_id=h.account_id
    join public.ig_accounts a on a.id=h.account_id
    join public.ig_account_settings s on s.account_id=a.id
    where h.id=p_hold_id and h.status='verification_required'
      and h.previous_admin_lifecycle_status='active'
      and h.verified_cleared_at is null and h.verified_by_run_id is null
      and i.status='resolved' and i.resolved_at is not null and i.resolved_by is not null and i.archived_at is null
      and i.incident_type='instagram_account_restriction'
      and i.metadata#>>'{manual_resolution_v2,expected_worker_sha}' ~ '^[0-9a-f]{40}$'
      and nullif(i.metadata#>>'{manual_resolution_v2,cause_fixed_version}','') is not null
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
          and not (h.metadata_safe ? 'resolved_growth_ready_v1')
          -- Legacy convergence requires exact pause ownership, never time proximity.
          and to_jsonb(a.updated_at)#>>'{}'=coalesce(h.metadata_safe->>'restriction_pause_updated_at',to_jsonb(h.detected_at)#>>'{}'))
      )
  );
$function$;
revoke all on function public.restriction_resolution_lifecycle_eligible_v1(uuid) from public,anon,authenticated;
grant execute on function public.restriction_resolution_lifecycle_eligible_v1(uuid) to service_role;

create or replace function public.restore_resolved_restriction_lifecycle_v1(p_incident_id uuid)
returns boolean language plpgsql security definer set search_path = '' as $function$
declare
  i public.account_incidents%rowtype;
  h public.instagram_account_restriction_holds%rowtype;
  account_id_value uuid;
begin
  select account_id into account_id_value from public.account_incidents where id=p_incident_id;
  -- Same account serialization as atomic admission; never wait on a locked
  -- incident while holding this account (human resolution locks incident first).
  perform 1 from public.ig_accounts where id=account_id_value for update;
  select * into i from public.account_incidents where id=p_incident_id for update skip locked;
  if i.id is null then return false; end if;
  select * into h from public.instagram_account_restriction_holds
    where incident_id=i.id and account_id=i.account_id for update;
  if not public.restriction_resolution_lifecycle_eligible_v1(h.id) then return false; end if;
  if h.metadata_safe ? 'resolved_growth_ready_v1' then return false; end if;
  update public.instagram_account_restriction_holds
  set metadata_safe=metadata_safe || jsonb_build_object('resolved_growth_ready_v1',jsonb_build_object(
    'incident_id',i.id,'lifecycle_version',i.lifecycle_version,'resolved_at',i.resolved_at,
    'restored_at',now(),'restored_status','active','physical_verification_pending',true)),updated_at=now()
  where id=h.id;
  update public.ig_accounts set admin_lifecycle_status='active',updated_at=now()
    where id=i.account_id and admin_lifecycle_status='paused';
  insert into public.ig_action_logs(account_id,action_type,status,message,metadata)
  values(i.account_id,'restriction_resolution_lifecycle_restored','success',
    'Resolved restriction restored administrative readiness; physical verification remains required.',
    jsonb_build_object('hold_id',h.id,'incident_id',i.id,'lifecycle_version',i.lifecycle_version,
      'previous_status','paused','new_status','active','physical_verification_pending',true));
  return true;
end;
$function$;
revoke all on function public.restore_resolved_restriction_lifecycle_v1(uuid) from public,anon,authenticated;
grant execute on function public.restore_resolved_restriction_lifecycle_v1(uuid) to service_role;

-- The canonical human-review RPC writes its resolution authority metadata
-- after changing status. Observe that final write in the same transaction.
create or replace function public.restore_resolved_restriction_lifecycle_trigger_v1()
returns trigger language plpgsql security definer set search_path = '' as $function$
declare item record;
begin
  if new.status='resolved' and new.incident_type='instagram_account_restriction' then
    -- The generation-resolution RPC may close equivalent incidents last.
    for item in select incident_id from public.instagram_account_restriction_holds
      where account_id=new.account_id and status='verification_required' order by incident_id
    loop
      perform public.restore_resolved_restriction_lifecycle_v1(item.incident_id);
    end loop;
  end if;
  return new;
end;
$function$;
revoke all on function public.restore_resolved_restriction_lifecycle_trigger_v1() from public,anon,authenticated;
drop trigger if exists zz_incident_resolved_growth_ready_v1 on public.account_incidents;
create trigger zz_incident_resolved_growth_ready_v1
after insert or update of status,resolved_at,metadata on public.account_incidents
for each row execute function public.restore_resolved_restriction_lifecycle_trigger_v1();

-- A later explicit pause, even paused -> paused, invalidates automatic ownership.
-- The restriction trigger's own nested write is the only excluded source.
create or replace function public.record_restriction_independent_lifecycle_write_v1()
returns trigger language plpgsql security definer set search_path = '' as $function$
begin
  if pg_trigger_depth()=1 and new.admin_lifecycle_status<>'active' then
    update public.instagram_account_restriction_holds
    set metadata_safe=metadata_safe || jsonb_build_object('independent_lifecycle_write_at',clock_timestamp()),
      updated_at=now()
    where account_id=new.id and status in ('active','verification_required');
  end if;
  return new;
end;
$function$;
revoke all on function public.record_restriction_independent_lifecycle_write_v1() from public,anon,authenticated;
drop trigger if exists ig_accounts_restriction_independent_lifecycle_write_v1 on public.ig_accounts;
create trigger ig_accounts_restriction_independent_lifecycle_write_v1
before update of admin_lifecycle_status on public.ig_accounts
for each row execute function public.record_restriction_independent_lifecycle_write_v1();

create or replace function public.reconcile_resolved_restriction_windows_v1()
returns jsonb language plpgsql security definer set search_path = '' as $function$
declare
  item record;
  i public.account_incidents%rowtype;
  h public.instagram_account_restriction_holds%rowtype;
  aa public.account_assignments%rowtype;
  p public.account_session_resume_plans%rowtype;
  source public.account_run_requests%rowtype;
  worker public.worker_heartbeats%rowtype;
  auth_id uuid;
  window_key text;
  restored integer:=0;
  armed integer:=0;
begin
  for item in select account_id,incident_id from public.instagram_account_restriction_holds
    where status='verification_required' order by account_id,incident_id
  loop
    -- Non-blocking incident lock avoids inversion with human resolution.
    perform 1 from public.ig_accounts where id=item.account_id for update skip locked;
    if not found then continue; end if;
    select * into i from public.account_incidents where id=item.incident_id for update skip locked;
    if i.id is null then continue; end if;
    if public.restore_resolved_restriction_lifecycle_v1(i.id) then restored:=restored+1; end if;
    select * into h from public.instagram_account_restriction_holds
      where incident_id=i.id and account_id=i.account_id for update;
    if not public.restriction_resolution_lifecycle_eligible_v1(h.id)
      or i.run_id is null or public.fleet_admission_reason_v1(i.account_id) is not null then continue; end if;
    -- A newer real run or any physical attempt consumes the human resolution.
    if not exists(select 1 from public.ig_runs r where r.id=i.run_id and r.account_id=i.account_id
      and r.status='failed')
      or i.run_id is distinct from (select r.id from public.ig_runs r where r.account_id=i.account_id
        order by r.created_at desc,r.id desc limit 1)
      or exists(select 1 from public.incident_resume_authorizations a
        left join public.account_run_requests q on q.id=a.consumed_by_request_id and q.account_id=a.account_id
        where a.incident_id=i.id and (
          a.status not in ('armed','expired','consumed')
          or (a.status='consumed' and not (
            q.id is not null and q.status='blocked' and q.error_code='account_not_active'
            and q.run_id is null and q.started_at is null and q.completed_at is not null
            and q.cancel_requested_at is null and q.cancel_reason is null
            and q.metadata_safe->'restriction_preflight_only'='true'::jsonb
            and not (a.metadata_safe ? 'restriction_window_recovery_v1')))
          or (a.expired_at is not null and coalesce(a.expires_at,a.scheduled_window_end)>now())
        )) then continue; end if;
    if exists(select 1 from public.account_run_requests q where q.account_id=i.account_id
        and q.status in ('queued','claimed','starting','running','stopping'))
      or exists(select 1 from public.ig_runs r where r.account_id=i.account_id and r.status in ('pending','running'))
      or exists(select 1 from public.auto_restart_device_locks l where l.account_id=i.account_id
        and l.lease_expires_at>now()) then continue; end if;
    select * into aa from public.account_assignments where account_id=i.account_id
      and status in ('reserved','active') and starts_at<=now() and ends_at>now()
      and coalesce(schedule_mode,'')<>'manual_only' order by starts_at desc,id limit 1 for update;
    if aa.id is null then continue; end if;
    -- Rebinding is explicit and auditable; ambiguity/stale heartbeat fails closed.
    if (select count(*) from public.worker_heartbeats w where w.worker_id like 'run-dispatcher:%'
      and w.status in ('idle','running') and w.last_seen_at>now()-interval '90 seconds'
      and w.last_seen_at<=now()+interval '5 seconds' and w.git_sha ~ '^[0-9a-f]{40}$')<>1 then continue; end if;
    select * into worker from public.worker_heartbeats w where w.worker_id like 'run-dispatcher:%'
      and w.status in ('idle','running') and w.last_seen_at>now()-interval '90 seconds'
      and w.last_seen_at<=now()+interval '5 seconds' and w.git_sha ~ '^[0-9a-f]{40}$';
    select * into source from public.account_run_requests q where q.account_id=i.account_id and q.run_id=i.run_id
      order by q.created_at desc,q.id desc limit 1;
    if source.id is null or source.cancel_requested_at is not null or source.cancel_reason is not null
      or source.status not in ('failed','blocked') then continue; end if;
    window_key:=i.account_id::text||':'||aa.starts_at::text||':restriction:'||i.id::text||':v'||i.lifecycle_version::text;
    -- Never extend an old authorization or rebind its Worker. Expiration only.
    update public.incident_resume_authorizations set status='expired',expired_at=coalesce(expired_at,now()),updated_at=now()
      where account_id=i.account_id and incident_id=i.id and status='armed'
        and coalesce(expires_at,scheduled_window_end)<=now();
    if exists(select 1 from public.incident_resume_authorizations a where a.account_id=i.account_id and a.status='armed')
      or exists(select 1 from public.incident_resume_authorizations a where a.incident_id=i.id
        and (a.resume_window_key=window_key or (a.scheduled_window_start=aa.starts_at and a.scheduled_window_end=aa.ends_at))) then continue; end if;
    insert into public.account_session_resume_plans(run_id,run_request_id,account_id,assignment_id,device_id,app_instance_id,
      expected_username,scheduled_window_start,scheduled_window_end,resume_window_key,source_surface,run_trigger,
      resume_stage,resume_state,restart_allowed,restart_block_reason,terminal_reason_code,incident_id,plan,test)
    values(i.run_id,source.id,i.account_id,aa.id,aa.device_id,aa.app_instance_id,i.account_username,
      aa.starts_at,aa.ends_at,window_key,'incident_resolution','scheduler','preflight',
      'awaiting_human_resume_authorization',true,'',coalesce(i.reason,i.failure_reason),i.id,
      jsonb_build_object('schema','RESOLVED_RESTRICTION_PREFLIGHT_SHELL_V1','source_run_id',i.run_id,
        'source_request_id',source.id,'business_actions_authorized',false),false)
    on conflict(run_id) do update set
      assignment_id=excluded.assignment_id,device_id=excluded.device_id,app_instance_id=excluded.app_instance_id,
      scheduled_window_start=excluded.scheduled_window_start,scheduled_window_end=excluded.scheduled_window_end,
      resume_window_key=excluded.resume_window_key,resume_state='awaiting_human_resume_authorization',
      restart_allowed=true,restart_block_reason='',incident_id=excluded.incident_id,last_updated_at=now()
    where public.account_session_resume_plans.account_id=i.account_id
      and public.account_session_resume_plans.run_request_id=source.id
      and public.account_session_resume_plans.resume_state in
        ('not_recoverable','partial_resumable','awaiting_human_resume_authorization','resume_requested')
    returning * into p;
    if p.id is null then continue; end if;
    auth_id:=null;
    insert into public.incident_resume_authorizations(incident_id,account_id,run_id,source_run_id,source_request_id,
      resume_plan_id,resume_window_key,scheduled_window_start,scheduled_window_end,status,armed_source,
      armed_by,resolved_by,resolved_at,cause_fixed_version,business_date,expected_worker_sha,expires_at,
      idempotency_key,resolution_note,metadata_safe,test)
    values(i.id,i.account_id,i.run_id,i.run_id,source.id,p.id,window_key,aa.starts_at,aa.ends_at,'armed',
      'resolved_restriction_window_reconciliation',i.resolved_by::text,i.resolved_by,i.resolved_at,
      i.metadata#>>'{manual_resolution_v2,cause_fixed_version}',(aa.starts_at at time zone 'Africa/Johannesburg')::date,
      worker.git_sha,aa.ends_at,'restriction-preflight:'||window_key,
      left(coalesce(i.resolution_note,''),500),jsonb_build_object('restriction_window_recovery_v1',jsonb_build_object(
        'hold_id',h.id,'hold_generation',h.generation_id,'incident_lifecycle_version',i.lifecycle_version,
        'resolved_at',i.resolved_at,'original_resolution_worker_sha',i.metadata#>>'{manual_resolution_v2,expected_worker_sha}',
        'verified_worker_id',worker.worker_id,'verified_worker_sha',worker.git_sha,'heartbeat_at',worker.last_seen_at,
        'prepared_at',now(),'verification_only',true)),
      lower(coalesce(i.metadata->>'test','false')) in ('true','1','yes'))
    on conflict do nothing returning id into auth_id;
    if auth_id is not null then
      update public.account_incidents set metadata=metadata||jsonb_build_object('recovery',
        coalesce(metadata->'recovery','{}'::jsonb)||jsonb_build_object('state','ready_to_resume',
          'authorization_id',auth_id,'resume_plan_id',p.id,'source','resolved_restriction_window_reconciliation',
          'updated_at',now())),updated_at=now() where id=i.id;
      armed:=armed+1;
    end if;
  end loop;
  return jsonb_build_object('restriction_lifecycles_restored',restored,'restriction_windows_armed',armed);
end;
$function$;
revoke all on function public.reconcile_resolved_restriction_windows_v1() from public,anon,authenticated;
grant execute on function public.reconcile_resolved_restriction_windows_v1() to service_role;


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
          and acct.status='active' and public.restriction_resolution_lifecycle_eligible_v1(h.id))
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

  -- Administrative readiness never bypasses pending physical verification.
  if exists(select 1 from public.instagram_account_restriction_holds h
       where h.account_id=v_account_id and h.status in ('active','verification_required'))
     and not public.restriction_resume_request_authorized_v1(v_request.id) then
    return jsonb_build_object('ok',false,'reason','restriction_physical_verification_required');
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

  -- Administrative readiness never bypasses pending physical verification.
  if exists(select 1 from public.instagram_account_restriction_holds h
       where h.account_id=v_account_id and h.status in ('active','verification_required'))
     and not public.restriction_resume_request_authorized_v1(v_request.id) then
    return jsonb_build_object('ok',false,'reason','restriction_physical_verification_required');
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
    if v_hold.id is null
       and not exists (
         select 1
         from public.instagram_account_restriction_holds other_hold
         where other_hold.account_id = new.account_id
           and other_hold.status in ('active', 'verification_required')
       ) then
      insert into public.instagram_account_restriction_holds (
        account_id,
        incident_id,
        stable_reason,
        status,
        previous_admin_lifecycle_status,
        human_resolved_at,
        verification_required_at,
        metadata_safe
      ) values (
        new.account_id,
        new.id,
        v_reason,
        'verification_required',
        v_previous_status,
        coalesce(new.resolved_at, now()),
        now(),
        jsonb_build_object(
          'source', 'identity_guard_direct_restriction_incident',
          'restriction_reason', v_reason,
          'incident_resolved_but_physical_preflight_pending', true
        )
      ) returning * into v_hold;
    elsif v_hold.id is not null and v_hold.status = 'active' then
      update public.instagram_account_restriction_holds
      set status = 'verification_required',
          human_resolved_at = coalesce(new.resolved_at, now()),
          verification_required_at = now(),
          metadata_safe = coalesce(metadata_safe, '{}'::jsonb)
            || jsonb_build_object('incident_resolved_but_physical_preflight_pending', true),
          updated_at = now()
      where id = v_hold.id;
    end if;

    perform public.restore_resolved_restriction_lifecycle_v1(new.id);
  end if;

  return new;
end
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
  if v_hold.status <> 'verification_required' then
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

CREATE OR REPLACE FUNCTION public.reconcile_resolved_incident_resume_windows_v2()
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_base jsonb;
  v_windows jsonb;
  v_pre_run jsonb;
  v_rearmed jsonb;
  v_enriched integer := 0;
  v_restriction jsonb;
begin
  v_windows := public.reconcile_resolved_restriction_windows_v1();
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

  return coalesce(v_windows, '{}'::jsonb) || coalesce(v_restriction, '{}'::jsonb) || coalesce(v_base, '{}'::jsonb)
    || coalesce(v_pre_run, '{}'::jsonb)
    || coalesce(v_rearmed, '{}'::jsonb)
    || jsonb_build_object('v2_enriched_count', v_enriched);
end
$function$
;

-- Converge only historically proven restriction-owned pauses. No run is created.
do $converge$
declare r record;
begin
  for r in select incident_id from public.instagram_account_restriction_holds where status='verification_required' order by account_id,incident_id loop
    perform public.restore_resolved_restriction_lifecycle_v1(r.incident_id);
  end loop;
end;
$converge$;
notify pgrst,'reload schema';
commit;
