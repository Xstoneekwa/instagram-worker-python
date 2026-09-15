"""Real PG17/PostgREST/Backend resolution-to-natural-tick contracts."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import unittest

import test_restriction_resume_natural_tick_v1 as previous

A,INCIDENT,PLAN,PRIOR,SOURCE,ASSIGNMENT = (getattr(previous,n) for n in ('A','INCIDENT','PLAN','PRIOR','SOURCE','ASSIGNMENT'))
ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/'supabase/migrations/20260914104008_resolved_incident_growth_ready_v1.sql'

class ResolvedGrowthReadyTests(previous.RealDatabaseTests):
    def setUp(self):
        self.sql('select restriction_test.reset_ready()')

    def state(self):
        return json.loads(self.sql(f"select jsonb_build_object('admin',a.admin_lifecycle_status,'hold',h.status,'physical',h.verified_cleared_at,'meta',h.metadata_safe) from ig_accounts a join instagram_account_restriction_holds h on h.account_id=a.id where a.id='{A}'"))

    def restore(self):
        return self.rpc('restore_resolved_restriction_lifecycle_v1',{'p_incident_id':INCIDENT})

    def prepare_authorized_legacy_request(self, claim=True):
        """Queue under the historical hold, then cross the V6 resolution edge."""
        self.sql("""select restriction_test.reset_legacy();
          update ig_accounts a set updated_at=h.detected_at
            from instagram_account_restriction_holds h where h.account_id=a.id;
          update instagram_account_restriction_holds
            set metadata_safe=metadata_safe-'independent_lifecycle_write_at'-'resolved_growth_ready_v1'-'restriction_pause_updated_at';""")
        queued=self.enqueue()
        self.restore()
        if not claim:return queued
        claimed=self.claim();self.assertEqual(claimed['id'],queued['id'])
        return claimed

    def assert_currently_eligible(self):
        state=self.state()
        self.assertEqual(state['admin'],'active',state)
        self.assertEqual(state['hold'],'superseded',state)
        self.assertEqual(self.sql(f"select count(*) from canonical_active_blocking_incidents_v1(array['{A}'::uuid])"),'0')
        result=previous.run_backend(self,'eligibility')
        self.assertTrue(result['ok'],result)

    def prepare_next_window(self,old_credit=False):
        if old_credit:
            q=self.enqueue();q=self.claim();self.reject_before_device(q)
            self.old_request=self.row(q['id'])
        self.sql("update incident_resume_authorizations set expires_at=now()-interval '1 minute',scheduled_window_start=scheduled_window_start-interval '1 day',scheduled_window_end=scheduled_window_end-interval '1 day';")
        if not old_credit:self.sql('delete from incident_resume_authorizations')
        self.sql("update worker_heartbeats set git_sha=repeat('b',40),last_seen_at=now() where worker_id='run-dispatcher:local-cert'")
        return self.rpc('reconcile_resolved_incident_resume_windows_v2')

    def test_00_reproduce_before_patch_then_identical_request_passes(self):
        self.assert_currently_eligible()
        self.assertFalse(self.restore())
        self.assertEqual(self.sql("select count(*) from ig_action_logs where action_type='restriction_resolution_lifecycle_restored'"),'1')

    def test_01_canonical_dispatcher_admission_and_legacy_overload(self):
        from account_run_request_consumer import _admit_account_session_v1
        for legacy in (False,True):
            q=self.prepare_authorized_legacy_request()
            if legacy:self.assertTrue(self.admit(q,legacy=True)['ok'])
            else:
                run=_admit_account_session_v1(request_id=q['id'],worker_id=previous.WORKER,
                  dispatch_ctx=dict(assignment_id=ASSIGNMENT,device_id=previous.DEVICE,
                    app_instance_id=previous.APP,package_name='com.instagram.android'),
                  device_lock_binding=q['metadata_safe']['device_lock_binding'])
                self.assertEqual(run,self.row(q['id'])['run_id'])

    def test_02_historical_rejection_recovers_once_and_keeps_request(self):
        q=self.prepare_authorized_legacy_request();before=self.row(q['id'])
        self.reject_before_device(q)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.rpc('restore_restriction_admission_credit_v1'),range(4)))
        self.assertEqual(sum(r['restriction_admission_credits_restored'] for r in results),1)
        self.assertEqual(self.row(q['id'])['id'],before['id'])
        self.assertEqual(self.auth()['status'],'armed')

    def test_03_paused_business_request_and_corrupt_proofs_rejected(self):
        q=self.prepare_authorized_legacy_request()
        self.sql(f"update account_run_requests set metadata_safe=metadata_safe-'authorization_id' where id='{q['id']}'")
        result=self.admit(self.row(q['id']))
        self.assertFalse(result['ok'],result)

    def test_04_expired_auth_and_independent_manual_pause_block(self):
        for query in ("update incident_resume_authorizations set expires_at=now()-interval '1 minute'",
                      "update ig_account_settings set manual_stop_requested=true"):
            q=self.prepare_authorized_legacy_request();self.sql(query)
            with self.subTest(query=query):self.assertFalse(self.admit(q)['ok'])

    def test_05_real_preflight_failure_never_restores_credit(self):
        q=self.prepare_authorized_legacy_request();result=self.admit(q);self.assertTrue(result['ok'],result)
        self.rpc('complete_account_run_request',dict(p_request_id=q['id'],p_worker_id=previous.WORKER,
          p_status='blocked',p_error_code='account_not_active'))
        self.assertEqual(self.rpc('restore_restriction_admission_credit_v1')['restriction_admission_credits_restored'],0)

    def test_06_success_clears_hold_through_existing_rpc(self):
        q=self.prepare_authorized_legacy_request();result=self.admit(q);self.assertTrue(result['ok'],result)
        released=self.rpc('release_instagram_action_restriction_hold_v1',dict(
          p_account_id=A,p_incident_id=INCIDENT,p_preflight_run_id=result['run_id']))
        self.assertTrue(released['ok'],released)
        self.assertEqual(self.state()['admin'],'active')

    def test_07_migration_replay_and_internal_permissions(self):
        for name,args in [('restriction_resolution_lifecycle_eligible_v1','uuid'),('restore_resolved_restriction_lifecycle_v1','uuid'),('reconcile_resolved_restriction_windows_v1','')]:
            for role in ('anon','authenticated'):
                self.assertEqual(self.sql(f"select has_function_privilege('{role}','public.{name}({args})','execute')"),'f')
            self.assertEqual(self.sql(f"select has_function_privilege('service_role','public.{name}({args})','execute')"),'t')

    def test_08_real_natural_tick_and_post_preflight_eligibility(self):
        self.assert_currently_eligible()
        self.assertEqual(self.rpc('reconcile_resolved_restriction_windows_v1')['restriction_windows_armed'],0)

    def test_09_additional_authority_and_binding_negatives(self):
        for query in ("update incident_resume_authorizations set consumed_by_request_id=null",
                      "update incident_resume_authorizations set expected_worker_sha=repeat('b',40)",
                      "update account_incidents set lifecycle_version=lifecycle_version+1"):
            q=self.prepare_authorized_legacy_request();self.sql(query)
            with self.subTest(query=query):self.assertFalse(self.admit(q)['ok'])

    def test_10_historical_credit_negative_proofs(self):
        q=self.prepare_authorized_legacy_request();self.reject_before_device(q)
        self.sql("update incident_resume_authorizations set expires_at=now()-interval '1 minute'")
        self.assertEqual(self.rpc('restore_restriction_admission_credit_v1')['restriction_admission_credits_restored'],0)
        self.assertEqual(self.auth()['status'],'consumed')

    def test_11_historical_credit_is_consumed_by_real_natural_tick(self):
        """Replacement: a generic tick cannot fabricate the retired special plan."""
        q=self.prepare_authorized_legacy_request();self.reject_before_device(q)
        self.assertEqual(self.rpc('restore_restriction_admission_credit_v1')['restriction_admission_credits_restored'],1)
        result=previous.run_backend(self)
        self.assertEqual(result['result']['enqueued_count'],0,result)
        reasons={row['reason'] for row in result['result']['blocked']}
        self.assertIn('restriction_preflight_not_authorized',reasons)
        self.assertEqual(self.auth()['status'],'armed')

    def test_12_fence_remains_authoritative(self):
        self.sql('select restriction_test.reset_legacy()')
        tx='restriction-local-fence'
        fence=self.rpc('acquire_phonefarm_worker_promotion_fence_v1',dict(p_transaction_id=tx,p_candidate_sha='a'*40))
        self.assertTrue(fence.get('active'),fence)
        try:
            with self.assertRaisesRegex(Exception,'phonefarm_runtime_admission_fenced'):
                self.enqueue()
        finally:
            self.rpc('release_phonefarm_worker_promotion_fence_v1',dict(p_transaction_id=tx,p_terminal_state='ROLLED_BACK'))

    def test_13_predecessor_drift_rejected(self):
        original=self.sql("select pg_get_functiondef('public.reconcile_resolved_incident_resume_windows_v2()'::regprocedure)")
        self.sql(original.replace('  v_base jsonb;','  v_base jsonb;\n  -- synthetic parallel change')+';')
        try:
            rejected=self.sql(MIGRATION.read_text(),ok=False)
            self.assertNotEqual(rejected.returncode,0)
            self.assertIn('resolved_growth_ready_dependency_drift',rejected.stderr)
            self.assertIn('synthetic parallel change',self.sql("select pg_get_functiondef('public.reconcile_resolved_incident_resume_windows_v2()'::regprocedure)"))
        finally:self.sql(original+';')

    def test_20_manual_pause_after_resolution_survives_release(self):
        q=self.prepare_authorized_legacy_request();admitted=self.admit(q);self.assertTrue(admitted['ok'],admitted)
        self.sql(f"update ig_accounts set admin_lifecycle_status='paused' where id='{A}'")
        self.assertFalse(self.restore())
        self.rpc('release_instagram_action_restriction_hold_v1',dict(p_account_id=A,p_incident_id=INCIDENT,p_preflight_run_id=admitted['run_id']))
        self.assertEqual(self.state()['admin'],'paused')

    def test_21_independent_pauses_never_unpaused(self):
        for change in ["update ig_account_settings set manual_stop_requested=true",
                       "update ig_account_settings set pause_account_days=2",
                       "update ig_accounts set admin_lifecycle_status='paused'",
                       "update ig_accounts set archived_at=now()",
                       "update instagram_account_restriction_holds set previous_admin_lifecycle_status='paused'"]:
            self.sql('select restriction_test.reset_ready()');self.sql(change)
            with self.subTest(change=change):
                self.assertFalse(self.restore())
                self.assertEqual(self.rpc('reconcile_resolved_restriction_windows_v1')['restriction_windows_armed'],0)
        self.sql('update ig_accounts set archived_at=null')

    def test_22_resolution_outside_slot_then_next_natural_tick(self):
        self.sql("update account_assignments set starts_at=starts_at+interval '1 day',ends_at=ends_at+interval '1 day'")
        self.assertEqual(self.rpc('reconcile_resolved_incident_resume_windows_v2')['restriction_windows_armed'],0)
        self.assertEqual(self.state()['admin'],'active')
        self.sql("update account_assignments set starts_at=starts_at-interval '1 day',ends_at=ends_at-interval '1 day'")
        self.assert_currently_eligible()

    def test_23_expired_pre_device_credit_new_worker_fresh_authorization(self):
        self.sql("update incident_resume_authorizations set expires_at=now()-interval '1 minute'; update worker_heartbeats set git_sha=repeat('b',40)")
        self.assertEqual(self.rpc('reconcile_resolved_incident_resume_windows_v2')['restriction_windows_armed'],0)
        self.assert_currently_eligible()

    def test_24_concurrent_reconciliation_arms_once(self):
        self.sql('delete from incident_resume_authorizations')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.rpc('reconcile_resolved_restriction_windows_v1'),range(4)))
        self.assertEqual(sum(r['restriction_windows_armed'] for r in results),0,results)
        self.assertEqual(self.sql("select count(*) from incident_resume_authorizations where status='armed'"),'0')

    def test_25_no_fresh_worker_no_automatic_rebind(self):
        self.sql('delete from incident_resume_authorizations')
        self.sql("update worker_heartbeats set last_seen_at=now()-interval '10 minutes'")
        self.assertEqual(self.rpc('reconcile_resolved_restriction_windows_v1')['restriction_windows_armed'],0)

    def test_26_physical_failure_no_rearm(self):
        q=self.prepare_authorized_legacy_request();admitted=self.admit(q);self.assertTrue(admitted['ok'],admitted)
        self.sql(f"update ig_runs set status='failed',finished_at=now() where id='{admitted['run_id']}'; update account_run_requests set status='failed',completed_at=now() where id='{q['id']}'; delete from auto_restart_device_locks;")
        self.assertEqual(self.rpc('reconcile_resolved_restriction_windows_v1')['restriction_windows_armed'],0)

    def test_27_new_occurrence_restores_restriction_pause(self):
        import runtime_incidents
        created=runtime_incidents.publish_account_incident('instagram_account_restriction',
          'historical-debt-new-occurrence',account_id=A,severity='critical',
          reason='instagram_account_restriction_unknown_scope')
        self.assertTrue(created['published'],created)
        self.assertNotEqual(created['incident_id'],INCIDENT)
        self.assertEqual(self.sql(f"select status from account_incidents where id='{created['incident_id']}'"),'open')
        self.assertEqual(self.sql(f"select count(*) from canonical_active_blocking_incidents_v1(array['{A}'::uuid])"),'1')

    def test_28_obsolete_migration_replay_is_rejected_without_overwrite(self):
        signature='public.restore_resolved_restriction_lifecycle_v1(uuid)'
        before=self.sql(f"select pg_get_functiondef('{signature}'::regprocedure)")
        rejected=self.sql(MIGRATION.read_text(),ok=False)
        self.assertNotEqual(rejected.returncode,0)
        self.assertIn('resolved_growth_ready_dependency_drift',rejected.stderr)
        self.assertEqual(self.sql(f"select pg_get_functiondef('{signature}'::regprocedure)"),before)

    def test_29_old_authorization_cannot_cross_resolution_generation(self):
        q=self.prepare_authorized_legacy_request()
        self.sql(f"update account_incidents set lifecycle_version=lifecycle_version+1 where id='{INCIDENT}'")
        result=self.admit(q)
        self.assertFalse(result['ok'],result)

    def test_30_manual_cancellation_blocks_window_rearm(self):
        self.sql('delete from incident_resume_authorizations')
        self.sql(f"update account_run_requests set cancel_requested_at=now(),cancel_reason='operator_cancel' where id='{SOURCE}'")
        self.assertEqual(self.rpc('reconcile_resolved_restriction_windows_v1')['restriction_windows_armed'],0)

    def test_31_revoked_and_ambiguous_worker_never_rearm(self):
        self.sql("update incident_resume_authorizations set status='expired',expired_at=now(),consume_error='operator_revoked'")
        self.assertEqual(self.rpc('reconcile_resolved_restriction_windows_v1')['restriction_windows_armed'],0)
        self.sql('delete from incident_resume_authorizations')
        self.sql("insert into worker_heartbeats(worker_id,host_machine,status,last_seen_at,git_sha) values('run-dispatcher:ambiguous','local-cert','idle',now(),repeat('b',40))")
        try:self.assertEqual(self.rpc('reconcile_resolved_restriction_windows_v1')['restriction_windows_armed'],0)
        finally:self.sql("delete from worker_heartbeats where worker_id='run-dispatcher:ambiguous'")

    def test_32_v6_preserves_authorized_v5_request_without_history_rewrite(self):
        history=self.sql(f"select row_to_json(q) from account_run_requests q where id='{SOURCE}'")
        q=self.prepare_authorized_legacy_request();admitted=self.admit(q)
        self.assertTrue(admitted['ok'],admitted)
        self.assertEqual(self.sql(f"select row_to_json(q) from account_run_requests q where id='{SOURCE}'"),history)
        self.assertEqual(self.state()['admin'],'active')
        self.assertEqual(self.state()['hold'],'superseded')

    def test_33_current_migration_replay_preserves_later_manual_pause(self):
        self.sql(f"update ig_accounts set admin_lifecycle_status='paused' where id='{A}'")
        current=Path('/Users/admin/Projects/worker-incident-system-global-resolution-v2/supabase/migrations/20260915102538_incident_system_global_resolution_v2.sql')
        self.sql(current.read_text())
        self.assertEqual(self.state()['admin'],'paused')

    def test_34_unchanged_backend_readiness_projection(self):
        original=previous.NODE_TICK
        projection=r'''
  if(process.env.RESTRICTION_TEST_NODE_MODE==='projection'){
    const {buildAdminReadinessProjection}=require(root+'/lib/instagram-dashboard/readiness-projection.ts');
    const {data,error}=await db.from('ig_accounts').select('id,username,admin_lifecycle_status').eq('id',process.env.RESTRICTION_TEST_ACCOUNT).single();
    if(error)throw Error(error.message);
    const healthy={accountId:data.id,username:data.username,clientId:null,clientName:null,
      adminStatus:data.admin_lifecycle_status,customerStatus:'active',subscriptionStatus:'active',
      packageName:'Growth',runtimeProfilesLabel:'Growth',credentialsConfigured:true,credentialsStatus:'active',
      reauthRequired:false,loginStatus:'connected',provisioningStatus:'ready',onboardingStatus:'ready',
      loginIdentityProofStatus:'verified',loginIdentityProfileOpened:true,loginIdentityUsernameMatch:true,
      loginIdentityVerifiedAt:new Date().toISOString(),assignmentStatus:'reserved',assignmentStartsAt:null,
      phoneStatus:'available',appInstanceStatus:'available',appPackageName:'com.instagram.android',
      appInstanceLaunchable:true,appInstanceUsableForAutoLogin:true,dmSettingsPresent:true,welcomeSettingsPresent:true,
      unfollowSettingsPresent:true,eligibleTargetCount:20,dashboardActionsCount:0,blockingActionsCount:0};
    result={actual:buildAdminReadinessProjection(healthy),paused:buildAdminReadinessProjection({...healthy,adminStatus:'paused'}),
      blocked:buildAdminReadinessProjection({...healthy,blockingActionsCount:1})};
  } else if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){
'''
        previous.NODE_TICK=original.replace("  if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){",projection)
        try:
            result=previous.run_backend(self,'projection')
            self.assertEqual(result['actual']['overall_readiness_status'],'ready',result)
            self.assertEqual(result['paused']['overall_readiness_status'],'paused')
            self.assertEqual(result['blocked']['overall_readiness_status'],'blocked')
        finally:previous.NODE_TICK=original

    def test_35_ordinary_scheduler_attempt_cannot_poison_preflight(self):
        ordinary=self.rpc('create_account_run_request',dict(p_account_id=A,p_actor_type='system',
          p_source_surface='instagram_schedule_session_cron',p_requested_run_type='account_session',
          p_idempotency_key='synthetic-ordinary-schedule',p_metadata_safe=dict(worker_id=previous.WORKER,
            execution_worker_id=previous.WORKER,assignment_id=ASSIGNMENT,trigger='scheduler')))
        q=self.claim();self.assertEqual(q['id'],ordinary['id'])
        admitted=self.admit(q);self.assertTrue(admitted['ok'],admitted)
        self.rpc('complete_account_run_request',dict(p_request_id=q['id'],p_worker_id=previous.WORKER,
          p_status='completed'))
        from auto_restart_device_lock import release_device_lock
        release_device_lock(device_id=previous.DEVICE,worker_id=previous.WORKER,request_id=q['id'],
          expected_lease_id=q['metadata_safe']['device_lock_binding']['lease_id'],account_id=A)
        self.assertEqual(self.sql(f"select count(*) from account_incidents where account_id='{A}'"),'1')
        self.assertEqual(self.sql(f"select count(*) from canonical_active_blocking_incidents_v1(array['{A}'::uuid])"),'0')

    def test_99_second_unrelated_account(self):
        names=('A','DEVICE','APP','ASSIGNMENT','PRIOR','SOURCE','INCIDENT','PLAN')
        saved={n:getattr(previous,n) for n in names}
        changed={n:v.replace('000000009','000000008') for n,v in saved.items()}
        for n,v in changed.items():setattr(previous,n,v)
        try:
            b=Path(os.environ['RESTRICTION_TEST_BACKEND_ROOT'])/'tests/auto-restart-real-pg17-v2'
            for name in ('seed.sql','ready.sql','settings.sql'):
                self.sql((b/name).read_text().replace('000000009','000000008').replace('local_cert_identity','local_cert_identity_two').replace('local-source','local-source-two').replace('local-identity','local-identity-two').replace('Synthetic no ADB','Synthetic second no ADB'))
            account,assignment,incident=changed['A'],changed['ASSIGNMENT'],changed['INCIDENT']
            self.sql(f"""insert into account_commercial_packages(account_id,package_code) values('{account}','growth');
              select reconcile_legacy_account_assignment_binding_v1('{account}','local-fixture-two');
              update account_assignments set starts_at=(select starts_at from restriction_test.assignments limit 1),
               ends_at=(select ends_at from restriction_test.assignments limit 1) where id='{assignment}';
              update account_session_resume_plans p set scheduled_window_start=a.starts_at,scheduled_window_end=a.ends_at,
               resume_window_key=a.id::text||':'||a.starts_at::text,restart_allowed=true,restart_block_reason=''
               from account_assignments a where p.assignment_id=a.id and p.account_id='{account}';
              update account_incidents set status='open',incident_type='instagram_account_restriction',
               reason='instagram_account_restriction_unknown_scope',severity='critical' where id='{incident}';""")
            self.assertEqual(self.sql(f"select admin_lifecycle_status from ig_accounts where id='{account}'"),'paused')
            self.rpc('transition_account_incident_human_review_v3',dict(p_incident_id=incident,p_action='resolve',p_expected_version=1,
              p_actor_type='admin',p_actor_id='580d7856-d60f-4838-a5f9-3b405d6ae79b',p_source='admin_dashboard',
              p_note='Synthetic second resolution',p_resolution_reason='cause_fixed',p_idempotency_key='local-second-resolution',
              p_expected_worker_sha='a'*40,p_cause_fixed_version='synthetic-local-restriction'))
            self.assertEqual(self.sql(f"select admin_lifecycle_status from ig_accounts where id='{account}'"),'active')
            result=previous.run_backend(self,'eligibility')
            self.assertTrue(result['ok'],result)
        finally:
            for n,v in saved.items():setattr(previous,n,v)

    @classmethod
    def tearDownClass(cls):
        result=cls.sql("select json_agg(json_build_object('name',p.proname,'args',pg_get_function_identity_arguments(p.oid),'definition',pg_get_functiondef(p.oid),'sha256',encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex'))) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname in ('restriction_resolution_lifecycle_eligible_v1','restore_resolved_restriction_lifecycle_v1','restore_resolved_restriction_lifecycle_trigger_v1','record_restriction_independent_lifecycle_write_v1','reconcile_resolved_restriction_windows_v1','sync_identity_guard_restriction_hold_v1','release_instagram_action_restriction_hold_v1','reconcile_resolved_incident_resume_windows_v2','admit_account_run_attempt_v1','restriction_resume_request_authorized_v1')")
        Path(os.environ['RESTRICTION_TEST_OUTPUT'],'candidate-catalog.json').write_text(result+'\n')

if __name__=='__main__':unittest.main()
