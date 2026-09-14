"""Terminal ownership and real database recovery after a failed human resume."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import account_run_request_consumer as consumer
import account_session_resume_plan_store as store

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'supabase/migrations/20260914223102_human_resume_terminal_plan_v1.sql'
CURRENT = '00000000-0000-4000-8000-000000000301'
ORIGINAL = '00000000-0000-4000-8000-000000000302'
REQUEST = '00000000-0000-4000-8000-000000000101'
ACCOUNT = '00000000-0000-4000-8000-000000000201'
INCIDENT = '00000000-0000-4000-8000-000000000401'


class TerminalOwnershipTests(unittest.TestCase):
    def test_failed_resume_terminalizes_current_plan_after_original_outcome(self):
        summary = {'reason': 'instagram_account_restriction_unknown_scope',
                   'run_type': 'account_session', 'restriction_detected': True}
        writes = []
        with (
            patch.object(consumer.supabase_client, 'load_run_row', return_value={
                'id': CURRENT, 'status': 'failed', 'performance_summary': summary}),
            patch.object(consumer, '_project_missing_worker_terminal_summary', return_value=(summary, None)),
            patch.object(consumer.supabase_client, 'get_account_username', return_value='synthetic_account'),
            patch.object(consumer.runtime_incidents, 'publish_account_incident', return_value={
                'published': True, 'incident_id': INCIDENT}) as publish,
            patch.object(store, '_patch_plan_by_run_id', side_effect=lambda run, body:
                writes.append((run, body)) or {'persisted': True}),
            patch.object(consumer, '_update_incident_recovery_state') as recovery,
        ):
            consumer._publish_run_failure_incident(request_id=REQUEST, account_id=ACCOUNT,
                run_id=CURRENT, run_type='account_session', exit_code=75, timed_out=False,
                canceled=False, request_metadata={'recovery_mode': 'human_confirmed_resume',
                    'original_run_id': ORIGINAL, 'incident_id': INCIDENT})
        self.assertEqual([run for run, _ in writes], [ORIGINAL, CURRENT])
        body = writes[-1][1]
        self.assertEqual(body['resume_state'], 'awaiting_human_resume_authorization')
        self.assertFalse(body['restart_allowed'])
        self.assertEqual(body['incident_id'], INCIDENT)
        self.assertEqual(body['terminal_reason_code'], 'instagram_account_restriction_unknown_scope')
        self.assertEqual(publish.call_args.kwargs['run_id'], CURRENT)
        self.assertEqual(publish.call_args.kwargs['metadata']['original_run_id'], ORIGINAL)
        self.assertEqual(recovery.call_args.kwargs['state'], 'reintervention_required')

# Existing isolated fixtures use the real unchanged Backend tick and Worker
# admission. Only the physical observation is synthetic; no device is contacted.
import sys
sys.path.insert(0, str(ROOT / 'tests'))
import test_restriction_resume_natural_tick_v1 as previous
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext


class RealRecoveryTests(previous.RealDatabaseTests):
    def setUp(self):
        self.sql('select restriction_test.reset_ready()')
        self.sql("insert into ig_accounts(id,username,status) values ('00000000-0000-0000-0000-000000009099','synthetic_other','active') on conflict(id) do nothing")

    def make_failed_resume(self, historical=False):
        self.old_auth = self.auth()['id']
        q = self.enqueue(); q = self.claim()
        admitted = self.admit(q); self.assertTrue(admitted['ok'], admitted)
        self.current = admitted['run_id']; self.request = q['id']
        q = self.row(q['id'])
        created = store.create_early_resume_plan(run_id=self.current,
            run_request_id=q['id'], account_id=previous.A, assignment_id=previous.ASSIGNMENT,
            device_id=previous.DEVICE, app_instance_id=previous.APP,
            expected_username='synthetic_account', expected_package='com.instagram.android',
            scheduled_window_start=q.get('scheduled_window_start'),
            scheduled_window_end=q.get('scheduled_window_end'),
            business_session_id=q['root_business_session_id'], attempt_id=q['execution_attempt_no'],
            retry_index=q['retry_index'], previous_run_id=previous.PRIOR)
        self.assertTrue(created['persisted'], created)
        summary = {'reason':'instagram_account_restriction_unknown_scope',
            'run_type':'account_session','restriction_detected':True,
            'restriction_preflight_only':True,'business_actions_executed':0}
        self.sql(f"update ig_runs set status='failed',finished_at=now(),performance_summary='{json.dumps(summary)}' where id='{self.current}'")
        self.rpc('complete_account_run_request',dict(p_request_id=q['id'],p_worker_id=previous.WORKER,
            p_status='failed',p_error_code='instagram_account_restriction_unknown_scope'))
        from auto_restart_device_lock import release_device_lock
        released=release_device_lock(device_id=previous.DEVICE,worker_id=previous.WORKER,
            request_id=q['id'],expected_lease_id=q['metadata_safe']['device_lock_binding']['lease_id'],account_id=previous.A)
        self.assertTrue(released['released'],released)
        ctx = patch.object(store,'record_terminal_failure',return_value={'persisted':False,'reason':'synthetic_old_branch'}) if historical else nullcontext()
        with ctx:
            consumer._publish_run_failure_incident(request_id=q['id'],account_id=previous.A,
                run_id=self.current,run_type='account_session',exit_code=75,timed_out=False,
                canceled=False,request_metadata=q['metadata_safe'],request_snapshot=q)
        incidents=json.loads(self.sql(f"select json_agg(i order by created_at desc,id desc) from account_incidents i where run_id='{self.current}'"))
        self.assertTrue(incidents,'actual consumer must publish the current incident')
        self.incident=incidents[0]['id']
        return q

    def plan(self):
        return json.loads(self.sql(f"select row_to_json(p) from account_session_resume_plans p where run_id='{self.current}'"))

    def resolve_current(self):
        version=self.sql(f"select lifecycle_version from account_incidents where id='{self.incident}'")
        self.sql(f"""begin; set local role service_role;
          set local request.jwt.claims='{{"role":"service_role"}}';
          select transition_account_incident_human_review_v3('{self.incident}','resolve',{version},'admin',
          '580d7856-d60f-4838-a5f9-3b405d6ae79b','admin_dashboard','Synthetic second resolution',
          'cause_fixed','synthetic-second-resolution',repeat('a',40),'synthetic-human-resume-fix');commit;""")

    def reconcile(self):
        return self.rpc('reconcile_stale_account_session_resume_plans_v1',{'p_limit':100})

    def assert_next_tick(self):
        result=previous.run_backend(self)
        self.assertEqual(result['result']['enqueued_count'],1,result)
        q=self.claim();self.assertIsNotNone(q)
        self.assertNotEqual(q['id'],self.request)
        self.assertEqual(q['metadata_safe']['prior_run_id'],self.current)
        self.assertEqual(q['metadata_safe']['incident_id'],self.incident)
        self.assertTrue(q['metadata_safe']['restriction_preflight_only'])
        self.assertNotEqual(q['metadata_safe']['authorization_id'],self.old_auth)
        self.assertEqual(self.sql(f"select status from incident_resume_authorizations where id='{self.old_auth}'"),'consumed')
        admitted=self.admit(q);self.assertTrue(admitted['ok'],admitted)
        self.assertEqual(self.sql("select count(*) from instagram_account_restriction_holds where status='verification_required'"),'1')
        self.assertEqual(q['metadata_safe']['resume_plan']['quota_remaining']['total'],0)
        self.assertFalse(any(q['metadata_safe']['resume_plan']['phases_to_run'].values()))
        return q

    def test_00_historical_failure_before_then_repair_and_natural_tick(self):
        self.make_failed_resume(historical=True);self.resolve_current()
        self.assertEqual(self.plan()['resume_state'],'run_active')
        before=self.reconcile();self.assertEqual(before['reconciled_count'],0,before)
        self.assertEqual(self.rpc('reconcile_resolved_incident_resume_windows_v2')['restriction_windows_armed'],0)
        original=self.plan()['plan'];request=self.row(self.request)
        self.sql(MIGRATION.read_text())
        repaired=self.reconcile();self.assertEqual(repaired['reconciled_count'],1,repaired)
        self.assertEqual(repaired['attempts_consumed'],0);self.assertEqual(repaired['requests_enqueued'],0)
        p=self.plan();self.assertEqual(p['resume_state'],'awaiting_human_resume_authorization')
        self.assertEqual(p['incident_id'],self.incident);self.assertFalse(p['restart_allowed'])
        self.assertEqual(p['plan'],original);self.assertEqual(self.row(self.request),request)
        self.assertEqual(self.reconcile()['reconciled_count'],0)
        self.assert_next_tick()

    def test_01_actual_worker_terminalization_then_second_resolution(self):
        self.make_failed_resume()
        p=self.plan();self.assertEqual(p['resume_state'],'awaiting_human_resume_authorization')
        self.assertEqual(p['incident_id'],self.incident)
        self.assertEqual(p['terminal_reason_code'],'instagram_account_restriction_unknown_scope')
        self.assertFalse(p['restart_allowed'])
        self.assertEqual(self.rpc('reconcile_resolved_incident_resume_windows_v2')['restriction_windows_armed'],0)
        self.resolve_current()
        self.assertEqual(self.sql(f"select admin_lifecycle_status from ig_accounts where id='{previous.A}'"),'active')
        self.assert_next_tick()

    def test_02_concurrent_repair_only_one_transition(self):
        self.make_failed_resume(historical=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.reconcile(),range(4)))
        self.assertEqual(sum(x['reconciled_count'] for x in results),1,results)
        self.assertFalse(self.plan()['restart_allowed'])
        self.assertEqual(self.sql(f"select count(*) from incident_resume_authorizations where run_id='{self.current}'"),'0')

    def test_03_invalid_or_active_evidence_keeps_plan_blocked(self):
        cases=[
            "update ig_runs set status='running' where id='{run}'",
            "insert into ig_runs(account_id,status,started_at,finished_at) values ('00000000-0000-0000-0000-000000009002','failed',now(),now())",
            "update ig_runs set finished_at=null where id='{run}'",
            "update account_run_requests set completed_at=null where id='{req}'",
            "update account_run_requests set cancel_requested_at=now() where id='{req}'",
            "update account_run_requests set status='queued' where id='{req}'",
            "update account_run_requests set metadata_safe=metadata_safe-'authorization_id' where id='{req}'",
            "update account_run_requests set metadata_safe=metadata_safe||jsonb_build_object('original_run_id','bad') where id='{req}'",
            "update account_run_requests set metadata_safe=metadata_safe||jsonb_build_object('resume_plan_id','bad') where id='{req}'",
            "update account_incidents set run_id=null where id='{inc}'",
            "update account_incidents set reason=null where id='{inc}'",
            "update account_incidents set archived_at=now() where id='{inc}'",
            "update incident_resume_authorizations set consumed_at=null where id='{auth}'",
            "update account_session_resume_plans set account_id='{other}' where run_id='{run}'",
        ]
        for change in cases:
            with self.subTest(change=change):
                self.setUp();self.make_failed_resume(historical=True)
                before=self.plan()
                self.sql(change.format(run=self.current,req=self.request,inc=self.incident,auth=self.old_auth,other='00000000-0000-0000-0000-000000009099'))
                result=self.reconcile()
                self.assertEqual(result['reconciled_count'],0,result)
                self.assertEqual(self.plan()['resume_state'],'run_active')
                self.assertFalse(self.plan()['restart_allowed'])

    def test_04_permissions_and_replay_preserved(self):
        self.sql(MIGRATION.read_text())
        result=self.sql("select (select row(proacl::text,prosecdef,proconfig::text)::text from pg_proc where oid='public.reconcile_stale_account_session_resume_plans_v1(integer)'::regprocedure)=(select row(proacl,prosecdef,proconfig)::text from human_resume_test.permissions_before)")
        self.assertEqual(result,'t')
        for role in ('anon','authenticated'):
            self.assertEqual(self.sql(f"select has_function_privilege('{role}','public.reconcile_stale_account_session_resume_plans_v1(integer)','execute')"),'f')

    def test_05_parallel_definition_drift_rejected(self):
        original=self.sql("select pg_get_functiondef('public.reconcile_stale_account_session_resume_plans_v1(integer)'::regprocedure)")
        self.sql(original.replace('  v_now timestamptz', '  -- synthetic parallel drift\n  v_now timestamptz')+';')
        try:
            result=self.sql(MIGRATION.read_text(),ok=False)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('human_resume_terminal_plan_dependency_drift',result.stderr)
        finally:self.sql(original+';')

    def test_08_inherited_partial_safe_human_branch_preserved(self):
        self.make_failed_resume(historical=True)
        final={'session_termination_class':'partial_safe_stopped','restart_allowed':True,
            'quota_remaining':{'total':2},'phases_to_run':{'unfollow':True},'unsafe_markers':[]}
        self.sql(f"update ig_runs set performance_summary=jsonb_build_object('auto_restart_resume_plan','{json.dumps(final)}'::jsonb) where id='{self.current}'")
        before=self.plan()
        candidate=self.sql("select pg_get_functiondef('public.reconcile_stale_account_session_resume_plans_v1(integer)'::regprocedure)")
        self.sql(self.sql('select definition from human_resume_test.function_before')+';')
        expected=self.reconcile();expected_plan=self.plan()
        self.sql(f"update account_session_resume_plans set resume_state='run_active',plan='{json.dumps(before['plan'])}'::jsonb where run_id='{self.current}'")
        self.sql(candidate+';')
        actual=self.reconcile();actual_plan=self.plan()
        self.assertEqual(expected['reconciled_count'],1)
        self.assertEqual(expected['reconciled'],actual['reconciled'])
        for field in ('plan','resume_state','restart_allowed','restart_block_reason'):
            self.assertEqual(expected_plan[field],actual_plan[field])

    def test_07_only_authorized_function_changed(self):
        changed=json.loads(self.sql("select coalesce(json_agg(b.signature),'[]') from human_resume_test.catalog_before b full join (select p.oid::regprocedure::text as signature,pg_get_functiondef(p.oid) as definition,p.proacl::text as acl,p.proconfig::text as config from pg_proc p where p.pronamespace='public'::regnamespace and p.prokind='f') a using(signature) where a.definition is distinct from b.definition or a.acl is distinct from b.acl or a.config is distinct from b.config"))
        self.assertEqual(changed,['reconcile_stale_account_session_resume_plans_v1(integer)'])

    def test_06_rollback_is_schema_only_and_exact(self):
        self.make_failed_resume(historical=True)
        before=self.plan();self.reconcile();repaired=self.plan()
        self.sql(self.sql('select definition from human_resume_test.function_before')+';')
        self.assertEqual(self.plan(),repaired)
        self.assertNotEqual(before['resume_state'],repaired['resume_state'])
        self.sql(MIGRATION.read_text())


def load_tests(loader, tests, pattern):
    suite=unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TerminalOwnershipTests))
    if os.environ.get('RESTRICTION_TEST_SOCKET'):
        suite.addTests(RealRecoveryTests(name) for name in sorted(RealRecoveryTests.__dict__) if name.startswith('test_'))
    return suite
