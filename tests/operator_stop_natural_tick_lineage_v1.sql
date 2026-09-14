-- Disposable-cluster fixture only. Never loaded by a production migration.
create schema operator_stop_test;
create table operator_stop_test.source as select * from account_run_requests;
create table operator_stop_test.run as select * from ig_runs;
create table operator_stop_test.plan as select * from account_session_resume_plans;
create function operator_stop_test.reset_ready(p_attempt integer default 1)
returns void language plpgsql as $$
declare
  q public.account_run_requests%rowtype;
  r public.ig_runs%rowtype;
  p public.account_session_resume_plans%rowtype;
begin
  delete from incident_resume_authorizations;
  delete from instagram_account_restriction_holds;
  delete from account_session_resume_plans;
  delete from account_incidents;
  delete from auto_restart_device_locks;
  delete from account_run_requests;
  delete from ig_runs;
  select * into q from operator_stop_test.source limit 1;
  select * into r from operator_stop_test.run limit 1;
  select * into p from operator_stop_test.plan limit 1;
  q.status:='canceled'; q.cancel_reason:='botapp_manual_stop';
  q.created_at:=now()-interval '30 minutes';
  q.cancel_requested_at:=now()-interval '15 minutes';
  q.completed_at:=q.cancel_requested_at;
  q.execution_attempt_no:=p_attempt; q.retry_index:=p_attempt-1;
  r.status:='stopped';r.started_at:=now()-interval '20 minutes';
  r.finished_at:=q.cancel_requested_at;
  p.resume_state:='completed';p.restart_allowed:=false;p.restart_block_reason:='operator_canceled';
  p.plan:=p.plan||jsonb_build_object('restart_allowed',false,'restart_block_reason','operator_canceled',
    'attempt_id',p_attempt,'execution_attempt_no',p_attempt,'retry_index',p_attempt-1,
    'session_termination_class','operator_canceled');
  r.performance_summary:=jsonb_build_object('session_status','stopped',
    'session_termination_class','operator_canceled','auto_restart_resume_plan',p.plan);
  insert into ig_runs select r.*;
  insert into account_run_requests select q.*;
  insert into account_session_resume_plans select p.*;
end;
$$;
