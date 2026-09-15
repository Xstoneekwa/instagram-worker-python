"""Composed incident recovery boundaries; no device or production IO."""
import copy
import os
import unittest
from unittest.mock import patch

import auto_restart_runtime as runtime
import account_run_request_consumer as consumer
import account_session_resume_plan_store as store
import restriction_preflight_terminal_contract as terminal
from tests.test_human_confirmed_resume_claim import (
    _metadata, _plan_row, ACCOUNT_ID, INCIDENT_ID, RESUME_PLAN_ID,
    ROOT_BUSINESS_SESSION_ID,
)


class ClaimToTerminalTests(unittest.TestCase):
    def test_preflight_root_is_bound_by_claim_when_legacy_durable_plan_has_no_root(self):
        authorization='00000000-0000-4000-8000-000000000123'
        request_id='00000000-0000-4000-8000-000000000124'
        meta=_metadata(authorization_id=authorization,restriction_preflight_only=True)
        meta['resume_plan'].update(authorization_id=authorization,incident_id=INCIDENT_ID,
            resume_plan_id=RESUME_PLAN_ID,resume_kind='instagram_restriction_preflight',
            restriction_preflight_only=True,phases_to_run=dict(welcome=False,follow=False,unfollow=False),
            quota_remaining=dict(welcome=0,follow=0,unfollow=0,outreach=0,total=0))
        meta.pop('root_business_session_id',None);meta.pop('business_session_id',None)
        meta['resume_plan'].pop('root_business_session_id',None)
        meta['resume_plan'].pop('business_session_id',None)
        durable=_plan_row();durable['plan'].pop('root_business_session_id',None)
        request=dict(id=request_id,account_id=ACCOUNT_ID,requested_run_type='account_session',
            root_business_session_id=request_id,execution_attempt_no=1,retry_index=0)
        with patch.object(store,'load_resume_plan',return_value=durable):
            ok,reason,policy=runtime.validate_claimed_auto_restart_request(
                account_id=ACCOUNT_ID,metadata=meta,claimed_request=request)
        self.assertTrue(ok,reason)
        self.assertEqual(policy['root_business_session_id'],request_id)
        self.assertEqual(policy['parent_request_id'],request_id)

    def test_real_failure_keeps_structured_class_without_changing_incident_family(self):
        from runtime_incident_matrix import classify_terminal_run_failure
        decision=classify_terminal_run_failure(exit_code=1,run_status='failed',
            performance_summary={'reason':'worker_exit_nonzero','error_class':'ConnectError',
                                 'failure_module':'runner','failure_function':'main'})
        self.assertTrue(decision.should_publish)
        self.assertEqual(decision.incident_type,'run_worker_failure')
        self.assertIn('ConnectError',decision.admin_message)
        self.assertEqual(decision.metadata_safe['error_class'],'ConnectError')
        from incident_notifications import build_incident_notification_payload
        payload=build_incident_notification_payload(dict(
            incident_type=decision.incident_type,status='open',
            admin_message=decision.admin_message,metadata=decision.metadata_safe))
        self.assertIn('ConnectError',payload['text'])
        bad=classify_terminal_run_failure(exit_code=1,run_status='failed',
            performance_summary={'reason':'worker_exit_nonzero','error_class':'token=synthetic-private-value'})
        self.assertNotIn('error_class',bad.metadata_safe)
        self.assertNotIn('synthetic-private-value',bad.admin_message)

    def test_request_binding_survives_real_claim_and_env_to_terminal(self):
        authorization = '00000000-0000-4000-8000-000000000123'
        request_id = '00000000-0000-4000-8000-000000000124'
        meta = _metadata(authorization_id=authorization, restriction_preflight_only=True)
        meta['resume_plan'].update(
            authorization_id=authorization, incident_id=INCIDENT_ID,
            resume_plan_id=RESUME_PLAN_ID, resume_kind='instagram_restriction_preflight',
            restriction_preflight_only=True,
            phases_to_run=dict(welcome=False, follow=False, unfollow=False),
            quota_remaining=dict(welcome=0, follow=0, unfollow=0, outreach=0, total=0))
        original = copy.deepcopy(meta)
        request = dict(id=request_id, account_id=ACCOUNT_ID,
                       root_business_session_id=ROOT_BUSINESS_SESSION_ID,
                       execution_attempt_no=2, retry_index=1)
        with patch.object(store, 'load_resume_plan', return_value=_plan_row()):
            ok, reason, policy = runtime.validate_claimed_auto_restart_request(
                account_id=ACCOUNT_ID, metadata=meta, claimed_request=request)
        self.assertTrue(ok, reason)
        with patch.dict(os.environ, runtime.runner_env_for_resume_policy(policy)):
            loaded = runtime.load_resume_policy_from_env()
        proof = terminal.success_summary(
            account_id=ACCOUNT_ID, request_id=request_id, run_id='run', policy=loaded,
            released=dict(ok=True, hold_status='cleared', hold_id='hold'))
        self.assertEqual(proof['restriction_preflight_terminal_contract']['authorization_id'], authorization)
        self.assertEqual(proof['restriction_preflight_terminal_contract']['resume_plan_id'], RESUME_PLAN_ID)
        self.assertEqual(loaded['parent_request_id'], request_id)
        self.assertEqual(loaded['execution_attempt_no'], 2)
        self.assertEqual(meta, original)
        self.assertEqual(loaded['frozen_phase_plan'], original['resume_plan'])
        self.assertEqual(proof['business_actions_executed'], 0)
        self.assertNotIn('phase_terminal_contract', proof)


import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_restriction_resume_natural_tick_v1 as database


@unittest.skipUnless(os.environ.get('RESTRICTION_TEST_SOCKET'), 'isolated PG17 required')
class ResolutionDatabaseTests(database.RealDatabaseTests):
    def setUp(self):
        self.sql('select restriction_test.reset_ready()')
        # Every test starts in the fixture's current canonical occurrence.
        # Recurrence tests may advance this disposable clock, but that state
        # must never leak into a later, independent test case.
        self.sql('update restriction_test.deterministic_clock set observed_at=now()')
        self.sql(f"delete from account_credentials where account_id='{database.A}' and secret_provider='synthetic'")

    def test_control_plane_lineage_and_sql_permissions_preserved(self):
        differences=self.sql("""select coalesce(jsonb_agg(b.signature),'[]')
          from human_resume_test.catalog_before b join pg_proc p on p.oid::regprocedure::text=b.signature
          where (p.proname like '%fence%' or p.proname like '%promotion%'
            or p.proname like '%lineage%' or p.proname='consume_resume_authorization_and_create_request_v4'
            or p.proname='is_zero_business_sigterm_resume_child_v1')
          and (pg_get_functiondef(p.oid) is distinct from b.definition
            or p.proacl::text is distinct from b.acl or p.proconfig::text is distinct from b.config)""")
        self.assertEqual(json.loads(differences),[])
        self.assertEqual(self.sql("""select count(*) from human_resume_test.catalog_before b
          join pg_proc p on p.oid::regprocedure::text=b.signature
          where p.proacl::text is distinct from b.acl or p.proconfig::text is distinct from b.config"""),'0')

    def test_resolution_retires_hold_without_fabricating_physical_proof(self):
        row=json.loads(self.sql(f"select jsonb_build_object('status',h.status,'admin',a.admin_lifecycle_status,'verified',h.verified_by_run_id) from instagram_account_restriction_holds h join ig_accounts a on a.id=h.account_id where h.account_id='{database.A}'"))
        self.assertEqual(row,dict(status='superseded',admin='active',verified=None))
        self.assertEqual(self.sql(f"select count(*) from canonical_active_blocking_incidents_v1(array['{database.A}'::uuid])"),'0')
        self.assertEqual(self.rpc('reconcile_resolved_restriction_windows_v1')['restriction_windows_armed'],0)
        eligibility=database.run_backend(self,'eligibility')
        self.assertTrue(eligibility['ok'],eligibility)

    def test_independent_pauses_survive_incident_hold_retirement(self):
        for change in ["update ig_account_settings set manual_stop_requested=true",
                       "update ig_account_settings set pause_account_days=2",
                       "update ig_account_settings set account_status='paused'",
                       "update instagram_account_restriction_holds set metadata_safe=metadata_safe||'{\"independent_lifecycle_write_at\":\"synthetic\"}'::jsonb"]:
            with self.subTest(change=change):
                self.sql('select restriction_test.reset_legacy()')
                self.sql('update ig_accounts a set updated_at=h.detected_at from instagram_account_restriction_holds h where h.account_id=a.id;'+change)
                self.rpc('restore_resolved_restriction_lifecycle_v1',dict(p_incident_id=database.INCIDENT))
                self.assertEqual(self.sql(f"select status from instagram_account_restriction_holds where account_id='{database.A}'"),'superseded')
                self.assertEqual(self.sql(f"select admin_lifecycle_status from ig_accounts where id='{database.A}'"),'paused')

    def test_profiles_and_live_remove_resolved_blocker(self):
        # Credentials are an independent readiness authority. Supply only a
        # synthetic reference; no secret is fetched or login performed.
        self.sql(f"insert into account_credentials(account_id,status,secret_ref,secret_provider) values ('{database.A}','active','local-fixture-never-dereferenced','synthetic')")
        branch="""if(process.env.RESTRICTION_TEST_NODE_MODE.startsWith('profiles')){
    process.env.BOTAPP_RELAY_API_KEY='local-only-projection-test';
    const live=process.env.RESTRICTION_TEST_NODE_MODE==='profiles-live';
    const {GET}=require(root+'/app/api/instagram-dashboard/profiles/'+(live?'live/':'')+'route.ts');
    const response=await GET(new Request(origin+'/api/instagram-dashboard/profiles'+(live?'/live':'')+'?account_ids='+process.env.RESTRICTION_TEST_ACCOUNT,
      {headers:{'x-botapp-relay-key':'local-only-projection-test'}}));
    result={status:response.status,body:await response.json()};
  } else if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){"""
        code=database.NODE_TICK.replace("if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){",branch)
        for mode in ('profiles','profiles-live'):
            with self.subTest(mode=mode),patch.object(database,'NODE_TICK',code):
                result=database.run_backend(self,mode)
                self.assertEqual(result['status'],200,result)
                data=result['body'].get('data',result['body'])
                rows=data.get('profiles',[])
                account=next(x for x in rows if x.get('id',x.get('accountId'))==database.A)
                self.assertIsNone(account.get('operationalBlocker'),account)
                self.assertTrue(account.get('schedulerEligible'),account)

    def test_nab_claim_policy_terminal_persistence_and_all_projections(self):
        """Production-shaped preflight path with no device process or ADB."""
        import restriction_preflight_terminal_contract as preflight_terminal
        self.sql("""select restriction_test.reset_legacy();
          update ig_accounts a set updated_at=h.detected_at from instagram_account_restriction_holds h where h.account_id=a.id;
          update instagram_account_restriction_holds set metadata_safe=metadata_safe-'independent_lifecycle_write_at'-'resolved_growth_ready_v1'-'restriction_pause_updated_at';""")
        self.sql(f"insert into account_credentials(account_id,status,secret_ref,secret_provider) values ('{database.A}','active','local-fixture-never-dereferenced','synthetic')")
        rpc=self.rpc
        def production_shape(name,body=None):
            if name=='consume_resume_authorization_and_create_request_v4':
                body=copy.deepcopy(body)
                body['p_metadata_safe']['resume_plan_version']=2
                body['p_metadata_safe']['resume_plan_schema']='AUTO_RESTART_RESUME_PLAN_V2'
                body['p_metadata_safe']['resume_plan']['resume_plan_version']=2
            return rpc(name,body)
        with patch.object(self,'rpc',side_effect=production_shape):
            queued=self.enqueue()
        self.rpc('restore_resolved_restriction_lifecycle_v1',dict(p_incident_id=database.INCIDENT))
        claimed=self.claim();self.assertEqual(claimed['id'],queued['id'])
        ok,reason,policy=runtime.validate_claimed_auto_restart_request(
            account_id=database.A,metadata=claimed['metadata_safe'],claimed_request=claimed)
        self.assertTrue(ok,{'reason':reason,'claimed':claimed,
            'durable_plan':json.loads(self.sql(f"select row_to_json(p) from account_session_resume_plans p where id='{database.PLAN}'"))})
        with patch.dict(os.environ,runtime.runner_env_for_resume_policy(policy)):
            normalized=runtime.load_resume_policy_from_env()
        admitted=self.admit(claimed);self.assertTrue(admitted['ok'],admitted)
        run_id=admitted['run_id']
        released=self.rpc('release_instagram_action_restriction_hold_v1',dict(
            p_account_id=database.A,p_incident_id=database.INCIDENT,p_preflight_run_id=run_id))
        self.assertTrue(released['ok'],released)
        hold=json.loads(self.sql(f"select row_to_json(h) from instagram_account_restriction_holds h where account_id='{database.A}'"))
        proof=preflight_terminal.success_summary(account_id=database.A,
            request_id=claimed['id'],run_id=run_id,policy=normalized,
            released={'ok':True,'hold_status':'cleared','hold_id':hold['id']})
        proof['preflight_cleanup_completed']=True
        self.sql(f"update ig_runs set status='completed',finished_at=now(),performance_summary='{json.dumps(proof)}'::jsonb where id='{run_id}'")
        run=json.loads(self.sql(f"select row_to_json(r) from ig_runs r where id='{run_id}'"))
        current=self.row(claimed['id'])
        validated=preflight_terminal.validate_terminal(request=current,run=run,hold=hold,
            cleanup_completed=True,lock_released=True)
        self.assertTrue(validated['ok'],validated)
        authoritative=consumer._authoritative_success_before_internal_failure(
            request=current,run=run,hold=hold,failure_summary={'reason':'internal_projection_error'},
            cleanup_completed=True,lock_released=True)
        self.assertEqual(authoritative,(True,'restriction_preflight_terminal_success'))
        self.rpc('complete_account_run_request',dict(p_request_id=claimed['id'],
            p_worker_id=database.WORKER,p_status='completed'))
        from auto_restart_device_lock import release_device_lock
        lock=release_device_lock(device_id=database.DEVICE,worker_id=database.WORKER,
            request_id=claimed['id'],expected_lease_id=claimed['metadata_safe']['device_lock_binding']['lease_id'],
            account_id=database.A)
        self.assertTrue(lock['released'],lock)
        self.assertEqual(self.sql(f"select count(*) from account_incidents where run_id='{run_id}' and status in ('open','acknowledged')"),'0')
        self.assertEqual(self.sql(f"select count(*) from canonical_active_blocking_incidents_v1(array['{database.A}'::uuid])"),'0')
        eligibility=database.run_backend(self,'eligibility');self.assertTrue(eligibility['ok'],eligibility)
        tick=database.run_backend(self);self.assertEqual(tick['status'],200,tick)
        self.assertFalse(any(x.get('reason') in {'active_incident','blocking_incident'}
            for x in tick['result'].get('blocked',[])),tick)
        projections={}
        for mode in ('profiles','profiles-live'):
            projections[mode]=self._read_profile_projection(mode)
            self.assertIsNone(projections[mode].get('operationalBlocker'),projections[mode])
            self.assertTrue(projections[mode].get('schedulerEligible'),projections[mode])
        (Path(os.environ['RESTRICTION_TEST_OUTPUT'])/'nab-projection-chain.json').write_text(json.dumps({
            'authorization_id':normalized['authorization_id'],'request_id':claimed['id'],
            'run_id':run_id,'terminal_validator':validated,'false_incident_count':0,
            'canonical_blockers':0,'scheduler_eligibility':'PASS','auto_restart_tick':'PASS',
            'botapp_projection':'profiles/live','projections':projections},indent=2)+'\n')

    def _read_profile_projection(self, mode):
        branch="""if(process.env.RESTRICTION_TEST_NODE_MODE.startsWith('profiles')){
    process.env.BOTAPP_RELAY_API_KEY='local-only-projection-test';
    const live=process.env.RESTRICTION_TEST_NODE_MODE==='profiles-live';
    const {GET}=require(root+'/app/api/instagram-dashboard/profiles/'+(live?'live/':'')+'route.ts');
    const response=await GET(new Request(origin+'/api/instagram-dashboard/profiles'+(live?'/live':'')+'?account_ids='+process.env.RESTRICTION_TEST_ACCOUNT,
      {headers:{'x-botapp-relay-key':'local-only-projection-test'}}));
    result={status:response.status,body:await response.json()};
  } else if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){"""
        code=database.NODE_TICK.replace("if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){",branch)
        with patch.object(database,'NODE_TICK',code):
            result=database.run_backend(self,mode)
        self.assertEqual(result['status'],200,result)
        data=result['body'].get('data',result['body'])
        return next(x for x in data.get('profiles',[]) if x.get('id',x.get('accountId'))==database.A)

    def test_already_authorized_legacy_preflight_survives_resolution(self):
        self.sql("""select restriction_test.reset_legacy();
          update ig_accounts a set updated_at=h.detected_at from instagram_account_restriction_holds h where h.account_id=a.id;
          update instagram_account_restriction_holds set metadata_safe=metadata_safe-'independent_lifecycle_write_at'-'resolved_growth_ready_v1'-'restriction_pause_updated_at';""")
        queued=self.enqueue()
        self.rpc('restore_resolved_restriction_lifecycle_v1',dict(p_incident_id=database.INCIDENT))
        claimed=self.claim()
        self.assertEqual(claimed['id'],queued['id'])
        admitted=self.admit(claimed)
        self.assertTrue(admitted['ok'],admitted)
        result=self.rpc('release_instagram_action_restriction_hold_v1',dict(
            p_account_id=database.A,p_incident_id=database.INCIDENT,p_preflight_run_id=admitted['run_id']))
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.sql(f"select verified_by_run_id::text from instagram_account_restriction_holds where account_id='{database.A}'"),admitted['run_id'])

    def test_repeated_resolution_reconciliation_is_write_idempotent(self):
        before=self.sql(f"select row_to_json(h) from instagram_account_restriction_holds h where account_id='{database.A}'")
        logs=self.sql("select count(*) from ig_action_logs where action_type='restriction_resolution_lifecycle_restored'")
        for _ in range(3):
            self.rpc('restore_resolved_restriction_lifecycle_v1',dict(p_incident_id=database.INCIDENT))
        self.assertEqual(before,self.sql(f"select row_to_json(h) from instagram_account_restriction_holds h where account_id='{database.A}'"))
        self.assertEqual(logs,self.sql("select count(*) from ig_action_logs where action_type='restriction_resolution_lifecycle_restored'"))

    def test_second_independent_resolution_restores_owned_pause_immediately(self):
        import runtime_incidents
        self.sql("""select restriction_test.reset_legacy();
          update ig_accounts a set updated_at=h.detected_at from instagram_account_restriction_holds h where h.account_id=a.id;
          update instagram_account_restriction_holds set metadata_safe=metadata_safe-'independent_lifecycle_write_at'-'resolved_growth_ready_v1'-'restriction_pause_updated_at';""")
        other=runtime_incidents.publish_account_incident('run_worker_failure','local-independent',
            severity='error',account_id=database.A,reason='worker_exit_nonzero',metadata={'test':True})
        self.assertTrue(other['published'],other)
        self.rpc('restore_resolved_restriction_lifecycle_v1',dict(p_incident_id=database.INCIDENT))
        self.assertEqual(self.sql(f"select admin_lifecycle_status from ig_accounts where id='{database.A}'"),'paused')
        self.rpc('transition_account_incident_human_review_v3',dict(
            p_incident_id=other['incident_id'],p_action='resolve',p_expected_version=1,
            p_actor_type='admin',p_actor_id='580d7856-d60f-4838-a5f9-3b405d6ae79b',
            p_source='admin_dashboard',p_note='Synthetic second resolution',
            p_resolution_reason='cause_fixed',p_idempotency_key='second:'+other['incident_id'],
            p_expected_worker_sha='a'*40,p_cause_fixed_version='isolated-second-resolution'))
        state=self.sql(f"select jsonb_build_object('admin',a.admin_lifecycle_status,'account_updated',a.updated_at,'hold',h.status,'hold_meta',h.metadata_safe,'detected',h.detected_at,'eligible',restriction_resolution_lifecycle_eligible_v1(h.id),'blockers',(select count(*) from canonical_active_operational_blockers_v1(array[a.id]))) from ig_accounts a join instagram_account_restriction_holds h on h.account_id=a.id where a.id='{database.A}'")
        self.assertEqual(json.loads(state)['admin'],'active',state)

    def test_existing_category_resolution_and_new_occurrence_matrix(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/incident-system-category-labels-v2.json').read_text())
        categories=fixture['categories'];contracts=fixture['producer_contracts']
        self.assertEqual(set(categories),set(contracts))
        evidence=[]
        for category in categories:
            with self.subTest(category=category):
                self.sql('select restriction_test.reset_ready()')
                contract=contracts[category]
                if not contract['repeatable']:
                    evidence.append({'CATEGORY':category,'REPEATABLE':'NO',
                        'REAL_PRODUCER':contract['producer'],'REAL_SIGNAL':contract['signal'],
                        'NON_REPEATABLE_PROVED':contract['non_repeatable_reason'],
                        'FIRST_CYCLE':'NOT_SAFELY_EXECUTABLE','SECOND_CYCLE':'NOT_APPLICABLE'})
                    continue
                cycles=[]
                previous_id=None
                for ordinal in (1,2):
                    created=self._produce_category_signal(category,contract,ordinal)
                    self.assertTrue(created['published'],created)
                    self.assertNotEqual(created['incident_id'],previous_id)
                    incident=json.loads(self.sql(
                        f"select row_to_json(i) from account_incidents i where id='{created['incident_id']}'"))
                    self.assertEqual(self.sql(
                        f"select count(*) from canonical_active_operational_blockers_v1(array['{database.A}'::uuid])"),'1')
                    self._resolve_actual_incident(incident,f'category-resolve-{ordinal}')
                    tick=database.run_backend(self)
                    self.assertEqual(tick['status'],200,tick)
                    self.assertFalse(any(x.get('reason') in {'active_incident','blocking_incident'}
                        for x in tick['result'].get('blocked',[])),tick)
                    cycles.append({'real_signal':'PASS','create':'PASS','incident_id':created['incident_id'],
                        'resolve':'PASS','eligible_after_resolution':'PASS',
                        'natural_tick_decision':'PASS'})
                    previous_id=created['incident_id']
                evidence.append({'CATEGORY':category,'REPEATABLE':'YES',
                    'FIRST_CREATE':'PASS','FIRST_RESOLVE':'PASS','ELIGIBLE_AFTER_FIRST':'PASS',
                    'SECOND_REAL_SIGNAL':'PASS','SECOND_CREATE':'PASS','SECOND_RESOLVE':'PASS',
                    'ELIGIBLE_AFTER_SECOND':'PASS','producer':contract['producer'],
                    'real_signal':contract['signal'],
                    'resolution':'transition_account_incident_human_review_v3','cycles':cycles})
        (Path(os.environ['RESTRICTION_TEST_OUTPUT'])/'category-matrix.json').write_text(json.dumps(evidence,indent=2)+'\n')

    def _produce_category_signal(self, category, contract, ordinal):
        """Exercise the category owner before the shared persistence sink."""
        kind=contract['kind'];signal=contract['signal']
        if kind in {'terminal_classifier','auto_login_classifier'}:
            from runtime_incident_matrix import (classify_terminal_run_failure,
                build_run_failure_incident_payload)
            import runtime_incidents
            summary={'reason':signal,'reason_code':signal,
                     'failure_module':'v5_isolated_producer','failure_function':'signal'}
            if category=='run_identity_verification_failed':
                summary['account_identity_failure_reason']=signal
            decision=classify_terminal_run_failure(exit_code=1,run_status='failed',
                run_type='login_provisioning' if kind=='auto_login_classifier' else 'account_session',
                performance_summary=summary)
            self.assertTrue(decision.should_publish,decision)
            self.assertEqual(decision.incident_type,category,decision)
            request_ref=f'00000000-0000-4000-8000-{ordinal:012d}'
            payload=build_run_failure_incident_payload(decision,account_id=database.A,
                account_username='synthetic_account',run_request_id=request_ref,
                run_type='login_provisioning' if kind=='auto_login_classifier' else 'account_session')
            incident=runtime_incidents.publish_account_incident(**payload)
            if decision.requires_operator_review and incident.get('incident_id'):
                consumer._upsert_operator_review_action(decision=decision,
                    incident_id=incident['incident_id'],account_id=database.A,
                    request_id=request_ref,run_id=None)
            return incident
        if kind=='auto_restart_hard_stop':
            import auto_restart_hard_stop as hard_stop
            with patch.object(hard_stop,'upsert_hard_stop_dashboard_action',
                              return_value={'published':True,'dashboard_action_id':'isolated'}):
                result=hard_stop.execute_auto_restart_hard_stop(account_id=database.A,
                    reason=signal,request_id=f'producer-{ordinal}',cancel_pending=False,
                    account_username='synthetic_account',evidence={'isolated_test':True})
            self.assertTrue(result['executed'],result)
            return result['incident']
        if kind=='login_challenge':
            import login_challenge_runtime as login
            screen='instagram_ads_data_consent_popup' if category=='instagram_ads_data_consent_popup_requires_operator' else 'challenge'
            action='enter_email_verification_code' if category=='login_verification_code_required' else 'review_login_challenge'
            incident=login.publish_login_challenge_pending_incident(account_id=database.A,
                expected_username='synthetic_account',run_id=None,challenge_type='email',
                screen_type=screen,reason=signal,dashboard_action_type=action,
                request_id=f'producer-{ordinal}')
            login.sync_login_challenge_dashboard_action(account_id=database.A,
                dashboard_action_type=action,incident_id=incident.get('incident_id'),
                challenge_type='email',screen_type=screen,human_review_required=True)
            return incident
        if kind=='login_package':
            import login_challenge_runtime as login
            incident=login.publish_login_package_mismatch_incident(account_id=database.A,
                expected_username='synthetic_account',expected_package_name='com.instagram.android',
                actual_foreground_package=f'com.synthetic.foreign{ordinal}',reason=signal)
            login.sync_login_package_mismatch_dashboard_action(account_id=database.A,
                expected_package_name='com.instagram.android',
                actual_foreground_package=f'com.synthetic.foreign{ordinal}',reason=signal)
            return incident
        if kind=='backend_configuration':
            result=self._run_backend_configuration_producer()
            return {'published':result['ok'],'reason':'published' if result['ok'] else result.get('reason'),
                    'incident_id':result.get('incidentId')}
        self.fail({'unsupported_producer_contract':contract})

    def _run_backend_configuration_producer(self):
        branch="""if(process.env.RESTRICTION_TEST_NODE_MODE==='configuration-producer'){
    const {reportWelcomeTemplateMissingIncident}=require(root+'/lib/instagram-dashboard/schedule-session-configuration-incidents.ts');
    result=await reportWelcomeTemplateMissingIncident(db,{accountId:process.env.RESTRICTION_TEST_ACCOUNT,
      assignmentId:'00000000-0000-0000-0000-000000009008',startsAt:'2026-09-15T00:00:00Z',endsAt:'2026-09-15T06:00:00Z'});
  } else if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){"""
        code=database.NODE_TICK.replace("if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){",branch)
        with patch.object(database,'NODE_TICK',code):
            return database.run_backend(self,'configuration-producer')

    def test_multi_incident_and_independent_blocker_interactions(self):
        contracts=json.loads((Path(__file__).parent/'fixtures/incident-system-category-labels-v2.json').read_text())['producer_contracts']
        pairs=(
            ('instagram_account_restriction','run_worker_failure','timed_plus_operational'),
            ('active_instagram_account_mismatch','run_worker_failure','identity_plus_operational'),
        )
        evidence=[]
        for first,second,label in pairs:
            with self.subTest(interaction=label):
                self.sql('select restriction_test.reset_ready()')
                a=self._produce_category_signal(first,contracts[first],1)
                b=self._produce_category_signal(second,contracts[second],2)
                # Two durable incident rows coexist. The canonical consumer
                # deliberately projects one highest-priority account blocker,
                # so its cardinality is one even while both rows are active.
                self.assertEqual(self.sql(
                    f"select count(*) from account_incidents where account_id='{database.A}' "
                    "and status in ('open','acknowledged','in_progress')"),'2')
                self.assertEqual(self.sql(
                    f"select count(*) from canonical_active_operational_blockers_v1(array['{database.A}'::uuid])"),'1')
                ia=json.loads(self.sql(f"select row_to_json(i) from account_incidents i where id='{a['incident_id']}'"))
                ib=json.loads(self.sql(f"select row_to_json(i) from account_incidents i where id='{b['incident_id']}'"))
                self._transition_incident(ia,'interaction-a')
                self.assertEqual(self.sql(
                    f"select count(*) from canonical_active_operational_blockers_v1(array['{database.A}'::uuid])"),'1')
                self._resolve_actual_incident(ib,'interaction-b')
                self.assertEqual(self.sql(
                    f"select count(*) from canonical_active_operational_blockers_v1(array['{database.A}'::uuid])"),'0')
                self.assertTrue(database.run_backend(self,'eligibility')['ok'])
                evidence.append({'interaction':label,'resolve_a_keeps_b_blocking':'PASS',
                                 'resolve_b_restores_eligibility':'PASS'})

        self.sql("select restriction_test.reset_ready(); update ig_account_settings set manual_stop_requested=true")
        incident=self._produce_category_signal('run_worker_failure',contracts['run_worker_failure'],1)
        row=json.loads(self.sql(f"select row_to_json(i) from account_incidents i where id='{incident['incident_id']}'"))
        self._transition_incident(row,'manual-independent')
        self.assertEqual(self.sql(f"select count(*) from canonical_active_blocking_incidents_v1(array['{database.A}'::uuid])"),'0')
        self.assertEqual(self.sql('select manual_stop_requested from ig_account_settings'),'t')
        self.assertEqual(self.sql(
            'select restriction_resolution_lifecycle_eligible_v1(id) '
            'from instagram_account_restriction_holds limit 1'),'f')
        evidence.append({'interaction':'manual_commercial_plus_incident',
                         'incident_blocker_removed':'PASS','independent_manual_blocker_preserved':'PASS'})
        (Path(os.environ['RESTRICTION_TEST_OUTPUT'])/'interaction-matrix.json').write_text(json.dumps(evidence,indent=2)+'\n')

    def test_real_timed_restriction_producers_resolve_without_date_wait(self):
        from instagram_action_restriction import (InstagramActionRestrictionDetected,
            configure_restriction_runtime_context,guard_instagram_action_rate_limit)
        import instagram_action_restriction as action_restriction
        import follow_revert_restriction as follow_restriction
        class Device:
            def app_current(self):return {'package':'com.instagram.android'}
        producers=('try_again_later','follow_not_becoming_following')
        for producer in producers:
            with self.subTest(producer=producer):
                self.sql('select restriction_test.reset_ready()')
                if producer=='try_again_later':
                    configure_restriction_runtime_context(account_id=database.A,
                        account_username='synthetic_account',run_id=database.PRIOR,
                        request_id=database.SOURCE,device_id=database.DEVICE,
                        app_instance_id=database.APP,worker_sha='a'*40)
                    xml='''<hierarchy><node package="com.instagram.android" class="android.app.Dialog"
                      text="Try again later"/><node package="com.instagram.android"
                      text="We limit how often you can do certain things to protect our community"/>
                      <node package="com.instagram.android" class="android.widget.Button" text="OK"/></hierarchy>'''
                    with patch.object(action_restriction,'_capture_evidence',return_value={'fixture':True}):
                        with self.assertRaises(InstagramActionRestrictionDetected) as raised:
                            guard_instagram_action_rate_limit(Device(),phase='follow',
                                preceding_action='before_follow_tap',hierarchy_xml=xml,
                                package_name='com.instagram.android')
                else:
                    with self.assertRaises(InstagramActionRestrictionDetected) as raised:
                        follow_restriction.apply_preventive_follow_restriction(device=Device(),
                            account_id=database.A,account_username='synthetic_account',
                            run_id=database.PRIOR,request_id=database.SOURCE,
                            candidate_username='candidate-c',source_profile_username='source-profile',
                            streak_candidates=('candidate-a','candidate-b','candidate-c'))
                incident_id=raised.exception.summary['incident_id']
                incident=json.loads(self.sql(f"select row_to_json(i) from account_incidents i where id='{incident_id}'"))
                self.assertEqual(incident['incident_type'],'instagram_account_restriction')
                self.assertEqual(self.sql(f"select count(*) from canonical_active_blocking_incidents_v1(array['{database.A}'::uuid])"),'1')
                self._resolve_actual_incident(incident,'timed-resolve-'+producer)
                self.assertEqual(self.sql(f"select count(*) from instagram_account_restriction_holds where account_id='{database.A}' and status in ('active','verification_required')"),'0')
                tick=database.run_backend(self);self.assertEqual(tick['status'],200,tick)

    def test_exhausted_attempt_three_is_not_rebuilt_or_consumed(self):
        self.sql(f"""update account_incidents set incident_type='run_worker_failure',reason='worker_exit_nonzero' where id='{database.INCIDENT}';
          insert into account_run_requests select (jsonb_populate_record(null::account_run_requests,
            to_jsonb(q)||jsonb_build_object('id','00000000-0000-0000-0000-000000009013',
              'idempotency_key','synthetic-exhausted-third','source_surface','admin_dashboard',
              'execution_attempt_no',3,'retry_index',2,'root_business_session_id',q.id,
              'created_at',now(),'status','failed'))).* from account_run_requests q where q.id='{database.SOURCE}';
          update account_session_resume_plans set run_request_id='00000000-0000-0000-0000-000000009013' where id='{database.PLAN}';""")
        first=self.rpc('prepare_resolved_incident_recovery_v2',dict(p_incident_id=database.INCIDENT))
        self.assertEqual(first['reason'],'execution_attempt_exhausted')
        before=self.sql(f"select row_to_json(p) from account_session_resume_plans p where id='{database.PLAN}'")
        for _ in range(3):
            self.assertEqual(self.rpc('prepare_resolved_incident_recovery_v2',dict(p_incident_id=database.INCIDENT))['reason'],'execution_attempt_exhausted')
        self.assertEqual(before,self.sql(f"select row_to_json(p) from account_session_resume_plans p where id='{database.PLAN}'"))

        self.assertEqual(json.loads(before)['resume_state'],'not_recoverable')
        self.assertEqual(self.sql(f"select count(*) from incident_resume_authorizations where account_id='{database.A}' and status='armed'"),'0')
        self.assertEqual(self.sql(f"select count(*) from account_run_requests where account_id='{database.A}' and execution_attempt_no>3"),'0')
        eligibility=database.run_backend(self,'eligibility')
        self.assertTrue(eligibility['ok'],eligibility)
        for _ in range(2):
            result=database.run_backend(self)
            self.assertEqual(result['status'],200,result)
            self.assertEqual(result['result']['enqueued_count'],0,result)
        self.assertEqual(before,self.sql(f"select row_to_json(p) from account_session_resume_plans p where id='{database.PLAN}'"))

    def _produce_modeled_worker_failure(self, request, admitted, marker):
        request=self.row(request['id']);run_id=admitted['run_id']
        plan=store.create_early_resume_plan(run_id=run_id,run_request_id=request['id'],
            account_id=database.A,assignment_id=database.ASSIGNMENT,device_id=database.DEVICE,
            app_instance_id=database.APP,expected_username='synthetic_account',
            expected_package='com.instagram.android',scheduled_window_start=request.get('scheduled_window_start'),
            scheduled_window_end=request.get('scheduled_window_end'),
            business_session_id=request['root_business_session_id'],attempt_id=request['execution_attempt_no'],
            retry_index=request['retry_index'],previous_run_id=request['metadata_safe'].get('prior_run_id'))
        self.assertTrue(plan['persisted'],plan)
        summary={'reason':'worker_exit_nonzero','root_failure_code':'worker_exit_nonzero',
            'run_type':'account_session','error_class':'DeviceRuntimeError',
            'failure_module':'runner','failure_function':'main','failure_stage':'device_runtime',
            'synthetic_boundary_marker':marker}
        self.sql(f"update ig_runs set status='failed',finished_at=now(),performance_summary='{json.dumps(summary)}'::jsonb where id='{run_id}'")
        self.rpc('complete_account_run_request',dict(p_request_id=request['id'],
            p_worker_id=database.WORKER,p_status='failed',p_error_code='worker_exit_nonzero'))
        from auto_restart_device_lock import release_device_lock
        released=release_device_lock(device_id=database.DEVICE,worker_id=database.WORKER,
            request_id=request['id'],expected_lease_id=request['metadata_safe']['device_lock_binding']['lease_id'],
            account_id=database.A)
        self.assertTrue(released['released'],released)
        consumer._publish_run_failure_incident(request_id=request['id'],account_id=database.A,
            run_id=run_id,run_type='account_session',exit_code=1,timed_out=False,canceled=False,
            worker_summary=summary,request_metadata=request['metadata_safe'],request_snapshot=request,
            cleanup_completed=True,lock_released=True)
        rows=json.loads(self.sql(f"select coalesce(jsonb_agg(to_jsonb(i) order by created_at desc,id desc),'[]') from account_incidents i where run_id='{run_id}' and incident_type='run_worker_failure'"))
        self.assertEqual(len(rows),1,rows)
        return rows[0]

    def _enqueue_business_resume(self):
        rpc=self.rpc
        def business_rpc(name,body=None):
            if name=='consume_resume_authorization_and_create_request_v4':
                body=copy.deepcopy(body);meta=body['p_metadata_safe'];frozen=meta['resume_plan']
                meta.pop('restriction_preflight_only',None)
                frozen.pop('restriction_preflight_only',None)
                frozen['resume_kind']='business';frozen['package_contract_ready']=True
                frozen['phases_to_run']['follow']=True
                frozen['quota_remaining'].update(follow=5,total=5)
            return rpc(name,body)
        with patch.object(self,'rpc',side_effect=business_rpc):
            return self.enqueue()

    def _transition_incident(self, incident, marker):
        result=self.rpc('transition_account_incident_human_review_v3',dict(
            p_incident_id=incident['id'],p_action='resolve',
            p_expected_version=incident['lifecycle_version'],p_actor_type='admin',
            p_actor_id='580d7856-d60f-4838-a5f9-3b405d6ae79b',p_source='admin_dashboard',
            p_note='Synthetic composed resolution',p_resolution_reason='cause_fixed',
            p_idempotency_key=marker+':'+incident['id'],p_expected_worker_sha='a'*40,
            p_cause_fixed_version='isolated-composed-v3'))
        self.assertIn(result.get('status'),{'resolved','RESOLVED'},result)
        return result

    def _resolve_actual_incident(self, incident, marker):
        result=self._transition_incident(incident,marker)
        self.assertEqual(self.sql(f"select count(*) from canonical_active_blocking_incidents_v1(array['{database.A}'::uuid])"),'0')
        eligibility=database.run_backend(self,'eligibility')
        self.assertTrue(eligibility['ok'],eligibility)
        return result

    def _run_scheduled_cron(self, now_iso):
        # Advance the single isolated clock shared by the SQL eligibility gate
        # and the existing cron. Historical request/window bytes stay intact.
        self.sql(f"update restriction_test.deterministic_clock set observed_at='{now_iso}'")
        branch="""if(process.env.RESTRICTION_TEST_NODE_MODE==='cron'){
    const {runScheduleSessionCron}=require(root+'/lib/instagram-dashboard/schedule-session-cron.ts');
    result=await runScheduleSessionCron(db,{callerToken:'local-cron-fixture',
      now:new Date(process.env.RESTRICTION_TEST_NOW),
      env:{INSTAGRAM_SCHEDULE_SESSION_CRON_TOKEN:'local-cron-fixture',
        INSTAGRAM_SCHEDULE_SESSION_CRON_ENABLED:'true',INSTAGRAM_SCHEDULE_SESSION_CRON_DRY_RUN:'false'},
      loadRuntimeHealth:async()=>({schedulerConnected:true,status:'active'})});
  } else if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){"""
        code=database.NODE_TICK.replace(
            "if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){",branch)
        with patch.object(database,'NODE_TICK',code),patch.dict(os.environ,{'RESTRICTION_TEST_NOW':now_iso}):
            return database.run_backend(self,'cron')

    def _prepare_next_real_schedule_occurrence(self):
        window=json.loads(self.sql(f"select jsonb_build_object('starts_at',starts_at,'ends_at',ends_at) from account_assignments where id='{database.ASSIGNMENT}'"))
        source=self.row(database.SOURCE)
        self.assertEqual(source['idempotency_key'],
            f"schedule-session:{database.ASSIGNMENT}:{window['starts_at']}",
            {'source_key':source['idempotency_key'],'window':window})
        same_slot=self.sql(f"select to_json(('{window['starts_at']}'::timestamptz+interval '1 hour'))")
        next_slot=self.sql(f"select to_json(('{window['starts_at']}'::timestamptz+interval '1 day 1 hour'))")
        same_slot=json.loads(same_slot);next_slot=json.loads(next_slot)
        self.sql(f"""insert into account_credentials(account_id,status,secret_ref,secret_provider)
          values ('{database.A}','active','local-fixture-never-dereferenced','synthetic')
          on conflict do nothing;
          update account_assignments set schedule_mode='scheduled' where id='{database.ASSIGNMENT}';
          insert into device_heartbeats(device_id,status,last_seen_at) values ('{database.DEVICE}','online','{same_slot}')
            on conflict(device_id) do update set status='online',last_seen_at=excluded.last_seen_at;""")
        return same_slot,next_slot

    def _advance_fixture_heartbeat_only(self, now_iso):
        self.sql(f"update device_heartbeats set status='online',last_seen_at='{now_iso}' where device_id='{database.DEVICE}'")

    def test_bmy_full_two_incident_sequence_uses_producer_resolution_and_tick(self):
        # The fixture supplies the immutable attempt-1 source and armed
        # authorization; consume it through the canonical V4 boundary.
        source_before=self.row(database.SOURCE)
        attempt2=self._enqueue_business_resume();self.assertEqual(attempt2['execution_attempt_no'],2)
        attempt2=self.claim()
        admitted2=self.admit(attempt2);self.assertTrue(admitted2['ok'],admitted2)
        incident_a=self._produce_modeled_worker_failure(attempt2,admitted2,'incident-a-attempt-2')
        self._resolve_actual_incident(incident_a,'resolve-a')

        second_tick=database.run_backend(self)
        self.assertEqual(second_tick['result']['enqueued_count'],1,second_tick)
        attempt3=self.claim();self.assertEqual(attempt3['execution_attempt_no'],3)
        self.assertEqual(attempt3['root_business_session_id'],database.SOURCE)
        admitted3=self.admit(attempt3);self.assertTrue(admitted3['ok'],admitted3)
        incident_b=self._produce_modeled_worker_failure(attempt3,admitted3,'incident-b-attempt-3')
        self.assertNotEqual(incident_b['id'],incident_a['id'])
        self._resolve_actual_incident(incident_b,'resolve-b')

        self.assertEqual(self.sql(f"select count(*) from incident_resume_authorizations where account_id='{database.A}' and status='armed'"),'0')
        self.assertEqual(self.sql(f"select count(*) from account_run_requests where account_id='{database.A}' and execution_attempt_no>3"),'0')
        exhausted_before=self.sql(f"select coalesce(jsonb_agg(to_jsonb(p) order by id),'[]') from account_session_resume_plans p where account_id='{database.A}'")
        for _ in range(2):
            tick=database.run_backend(self);self.assertEqual(tick['result']['enqueued_count'],0,tick)
        self.assertEqual(exhausted_before,self.sql(f"select coalesce(jsonb_agg(to_jsonb(p) order by id),'[]') from account_session_resume_plans p where account_id='{database.A}'"))
        self.assertEqual(self.sql(f"select count(*) from account_run_requests where account_id='{database.A}' and execution_attempt_no>3"),'0')

        # Advance the injected scheduler clock to the next real daily
        # occurrence. The exhausted source row remains byte-for-byte intact.
        same_slot,next_slot=self._prepare_next_real_schedule_occurrence()
        before_same_slot=self.sql(f"select count(*) from account_run_requests where account_id='{database.A}'")
        for _ in range(2):
            cron=self._run_scheduled_cron(same_slot);self.assertEqual(cron['status'],200,cron)
            self.assertEqual(self.sql(f"select count(*) from account_run_requests where account_id='{database.A}'"),before_same_slot)
        self._advance_fixture_heartbeat_only(next_slot)
        cron=self._run_scheduled_cron(next_slot);self.assertEqual(cron['status'],200,cron)
        fresh=json.loads(self.sql(f"select coalesce(jsonb_agg(to_jsonb(q)),'[]') from account_run_requests q where account_id='{database.A}' and source_surface='instagram_schedule_session_cron' and id<>'{database.SOURCE}'"))
        self.assertEqual(len(fresh),1,fresh)
        self.assertEqual(fresh[0]['execution_attempt_no'],1)
        self.assertEqual(fresh[0]['root_business_session_id'],fresh[0]['id'])
        self.assertEqual(fresh[0]['idempotency_key'],
            f"schedule-session:{database.ASSIGNMENT}:{fresh[0]['metadata_safe']['scheduled_session_at']}")
        self.assertEqual(self.sql(f"select max(execution_attempt_no) from account_run_requests where root_business_session_id='{database.SOURCE}'"),'3')
        self.assertEqual(self.row(database.SOURCE),source_before)
        (Path(os.environ['RESTRICTION_TEST_OUTPUT'])/'bmy-distinct-session.json').write_text(
            json.dumps({'historical_source_unchanged':True,'source_request_id':database.SOURCE,
                'same_occurrence_observed_at':same_slot,'next_occurrence_observed_at':next_slot,
                'real_time_advance_used':True,'new_request_id':fresh[0]['id'],
                'new_root_business_session_id':fresh[0]['root_business_session_id'],
                'new_deterministic_schedule_key':fresh[0]['idempotency_key'],
                'old_lineage_max_execution_attempt_no':3,
                'execution_attempt_no':fresh[0]['execution_attempt_no'],'cron_result':cron},indent=2)+'\n')

    def test_next_distinct_session_uses_existing_cron(self):
        source_before=self.row(database.SOURCE)
        self.test_exhausted_attempt_three_is_not_rebuilt_or_consumed()
        same_slot,next_slot=self._prepare_next_real_schedule_occurrence()
        original_count=self.sql(f"select count(*) from account_run_requests where account_id='{database.A}'")
        for _ in range(2):
            same=self._run_scheduled_cron(same_slot)
            self.assertEqual(same['status'],200,same)
            self.assertEqual(self.sql(f"select count(*) from account_run_requests where account_id='{database.A}'"),original_count)
        self._advance_fixture_heartbeat_only(next_slot)
        result=self._run_scheduled_cron(next_slot)
        with self.subTest(boundary='next_real_occurrence'):
            self.assertEqual(result['status'],200,result)
            rows=json.loads(self.sql(f"select coalesce(jsonb_agg(to_jsonb(q)),'[]') from account_run_requests q where account_id='{database.A}' and source_surface='instagram_schedule_session_cron' and id<>'{database.SOURCE}'"))
            self.assertEqual(len(rows),1,result)
            self.assertEqual(rows[0]['execution_attempt_no'],1)
            self.assertEqual(rows[0]['root_business_session_id'],rows[0]['id'])
            self.assertEqual(rows[0]['idempotency_key'],
                f"schedule-session:{database.ASSIGNMENT}:{rows[0]['metadata_safe']['scheduled_session_at']}")
            self.assertEqual(self.sql(f"select max(execution_attempt_no) from account_run_requests where root_business_session_id='{database.SOURCE}'"),'3')
            self.assertEqual(self.row(database.SOURCE),source_before)
            self._run_scheduled_cron(next_slot)
            self.assertEqual(self.sql(f"select count(*) from account_run_requests where account_id='{database.A}' and source_surface='instagram_schedule_session_cron' and id<>'{database.SOURCE}'"),'1')
            baseline=Path(os.environ['INCIDENT_SYSTEM_BASELINE_BACKEND_ROOT'])
            with patch.dict(os.environ,{'RESTRICTION_TEST_BACKEND_ROOT':str(baseline)}):
                baseline_result=self._run_scheduled_cron(next_slot)
            self.assertEqual(result['result']['observability_error'],baseline_result['result']['observability_error'])
            (Path(os.environ['RESTRICTION_TEST_OUTPUT'])/'cron-baseline-comparison.json').write_text(json.dumps({
                'candidate':result,'baseline':baseline_result,
                'known_schema_observability_defect_unchanged':True},indent=2)+'\n')



for _name in dir(database.RealDatabaseTests):
    if _name.startswith('test_') and _name not in ResolutionDatabaseTests.__dict__:
        setattr(ResolutionDatabaseTests,_name,None)

if __name__ == '__main__':
    unittest.main()
