-- Synthetic fixture only, loaded by the disposable-cluster harness.
-- Legal six-hour assignment containing now; no wall-clock or gate override.
begin;
update public.account_assignments set
  starts_at=date_trunc('day',now())+floor(extract(hour from now())/6)*interval '6 hours',
  ends_at=date_trunc('day',now())+(floor(extract(hour from now())/6)+1)*interval '6 hours'
where id='00000000-0000-0000-0000-000000009008';
update public.account_session_resume_plans p set
  scheduled_window_start=a.starts_at, scheduled_window_end=a.ends_at,
  resume_window_key=a.id::text||':'||a.starts_at::text
from public.account_assignments a where p.assignment_id=a.id;
insert into public.worker_heartbeats(worker_id,host_machine,status,last_seen_at,git_sha)
values ('run-dispatcher:local-cert','local-cert','idle',now(),'320de84755eddcba5110c4e5ee18d28f5632b7c9');
insert into public.auto_restart_settings(id,auto_restart_enabled,mode)
values ('global',true,'production') on conflict(id) do update set auto_restart_enabled=true,mode='production';

-- Preserve a human-authorization fixture separately before modelling a normal
-- completed partial run. V4 tests restore these exact fixture bytes locally.
create schema lineage_test;
create table lineage_test.incident as select * from public.account_incidents;
create table lineage_test.plan as select * from public.account_session_resume_plans;
update public.account_session_resume_plans set incident_id=null;
delete from public.account_incidents;
update public.account_run_requests set status='completed',completed_at=now()-interval '1 minute'
where id='00000000-0000-0000-0000-000000009010';
update public.account_session_resume_plans set
  resume_state='resume_requested',restart_allowed=true,restart_block_reason='',
  plan=jsonb_build_object(
    'schema','AUTO_RESTART_RESUME_PLAN_V2','plan_version',2,
    'account_id','00000000-0000-0000-0000-000000009002',
    'root_business_session_id','00000000-0000-0000-0000-000000009010',
    'business_session_id','00000000-0000-0000-0000-000000009010',
    'execution_attempt_no',1,'attempt_id',1,'retry_index',0,
    'restart_allowed',true,'restart_block_reason','',
    'session_termination_class','partial_resumable',
    'phases_to_run',jsonb_build_object('welcome',false,'follow',false,'unfollow',true),
    'phase_order',jsonb_build_array('welcome','follow','unfollow'),
    'quota_remaining',jsonb_build_object('welcome',0,'follow',0,'unfollow',72,'outreach',0,'total',72),
    'cleanup_completed',true,'lock_released',true,'package_contract_ready',true)
where run_id='00000000-0000-0000-0000-000000009009';
update public.ig_runs r set status='completed', performance_summary=jsonb_build_object(
  'session_status','success','session_termination_class','partial_resumable',
  'restart_eligibility','eligible','auto_restart_resume_plan',p.plan)
from public.account_session_resume_plans p where p.run_id=r.id;
commit;
