-- Disposable-cluster fixtures only. No production identifiers or credentials.
begin;
update account_assignments set starts_at=date_trunc('day',now())+floor(extract(hour from now())/6)*interval '6 hours',
 ends_at=date_trunc('day',now())+(floor(extract(hour from now())/6)+1)*interval '6 hours'
 where id='00000000-0000-0000-0000-000000009008';
update account_session_resume_plans p set scheduled_window_start=a.starts_at,scheduled_window_end=a.ends_at,
 resume_window_key=a.id::text||':'||a.starts_at::text,restart_allowed=true,restart_block_reason=''
 from account_assignments a where p.assignment_id=a.id;
insert into worker_heartbeats(worker_id,host_machine,status,last_seen_at,git_sha)
 values('run-dispatcher:local-cert','local-cert','idle',now(),repeat('a',40));
insert into auto_restart_settings(id,auto_restart_enabled,mode) values('global',true,'production')
 on conflict(id) do update set auto_restart_enabled=true,mode='production';
update account_incidents set incident_type='instagram_account_restriction',
 reason='instagram_account_restriction_unknown_scope',severity='critical'
 where id='00000000-0000-0000-0000-000000009011';
set local role service_role;
set local request.jwt.claims='{"role":"service_role"}';
select transition_account_incident_human_review_v3(
 '00000000-0000-0000-0000-000000009011','resolve',1,'admin',
 '580d7856-d60f-4838-a5f9-3b405d6ae79b','admin_dashboard','Synthetic resolution','cause_fixed','local-restriction-resolve',
 repeat('a',40),'synthetic-local-restriction');
reset role;
create schema restriction_test;
create table restriction_test.accounts as select * from ig_accounts;
create table restriction_test.settings as select * from ig_account_settings;
create table restriction_test.assignments as select * from account_assignments;
create table restriction_test.runs as select * from ig_runs;
create table restriction_test.requests as select * from account_run_requests;
create table restriction_test.incidents as select * from account_incidents;
create table restriction_test.plans as select * from account_session_resume_plans;
create table restriction_test.authorizations as select * from incident_resume_authorizations;
create table restriction_test.dm_settings as select * from ig_account_dm_settings;
create table restriction_test.holds as select * from instagram_account_restriction_holds;
create function restriction_test.reset() returns void language plpgsql as $$
begin
 truncate incident_resume_authorizations,account_incidents,instagram_account_restriction_holds,
   account_session_resume_plans,account_run_requests,ig_runs cascade;
 delete from ig_account_dm_settings;
 insert into ig_account_dm_settings select * from restriction_test.dm_settings;
 delete from auto_restart_device_locks;
 delete from auto_restart_tick_locks;
 delete from auto_restart_decisions;
 delete from fleet_account_policy_v1;
 update ig_accounts set status='active',admin_lifecycle_status='active';
 update ig_account_settings set account_status='active',manual_stop_requested=false,pause_account_days=0,pause_account_until='';
 update account_assignments a set status=b.status,schedule_mode=b.schedule_mode,slot_kind=b.slot_kind,starts_at=b.starts_at,ends_at=b.ends_at,
  device_id=b.device_id,app_instance_id=b.app_instance_id from restriction_test.assignments b where a.id=b.id;
 insert into ig_runs select * from restriction_test.runs;
 insert into account_run_requests select * from restriction_test.requests;
 insert into account_incidents select * from restriction_test.incidents;
 insert into account_session_resume_plans select * from restriction_test.plans;
 insert into incident_resume_authorizations select * from restriction_test.authorizations;
 delete from instagram_account_restriction_holds;
 insert into instagram_account_restriction_holds select * from restriction_test.holds;
 update ig_accounts set admin_lifecycle_status='paused';
end $$;
commit;
