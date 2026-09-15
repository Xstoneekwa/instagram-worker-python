"""Real dispatcher boundary and runner cleanup; no device or network."""
import copy
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import account_run_request_consumer as consumer
import account_session_resume_plan_store as store
import restriction_preflight_terminal_contract as contract


def fixture():
    policy=dict(incident_id='incident',account_id='account',authorization_id='auth',
        resume_plan_id='source-plan',restriction_preflight_only=True,
        phases_to_run=dict(welcome=False,follow=False,unfollow=False))
    summary=contract.success_summary(account_id='account',request_id='request',run_id='run',
        policy=policy,released=dict(ok=True,hold_status='cleared',hold_id='hold'))
    summary['preflight_cleanup_completed']=True
    request=dict(id='request',run_id='run',account_id='account',status='completed',
        requested_run_type='account_session',metadata_safe=dict(recovery_mode='human_confirmed_resume',
        restriction_preflight_only=True,incident_id='incident',authorization_id='auth',
        resume_plan_id='source-plan',resume_plan=policy))
    run=dict(id='run',account_id='account',status='completed',finished_at='2026-09-15T00:00:00Z',
        performance_summary=summary,total_follow=0,total_like=0,total_dm=0,total_story=0)
    hold=dict(id='hold',account_id='account',incident_id='incident',status='cleared',
              verified_by_run_id='run',verified_cleared_at='2026-09-15T00:00:00Z')
    return request,run,hold


class DispatcherBoundaryTests(unittest.TestCase):
    def test_runtime_physical_fields_cannot_hide_false_equals_zero(self):
        for field,bad in [('business_actions_executed',False),
                          ('physical_preflight_passed',1),
                          ('preflight_cleanup_completed',1),
                          ('restriction_preflight_only',1)]:
            with self.subTest(field=field):
                q,r,h=fixture()
                with patch.object(consumer,'_proven_preflight_binding_error',
                        wraps=consumer._proven_preflight_binding_error) as fallback:
                    accepted,_=consumer._authoritative_success_before_internal_failure(
                        request=q,run=r,hold=h,failure_summary={field:bad},
                        cleanup_completed=True,lock_released=True)
                self.assertFalse(accepted)
                fallback.assert_not_called()

    def test_initial_reload_and_runtime_proof_precedence_cross_product(self):
        import itertools
        key='restriction_preflight_terminal_contract'
        valid=fixture()[1]['performance_summary'][key]
        values={'absent':None,'valid':valid,'foreign':{**valid,'authorization_id':'foreign'},
                'malformed':[]}
        for names in itertools.product(values,repeat=3):
            with self.subTest(initial_reload_runtime=names):
                q,r,h=fixture();reload=copy.deepcopy(r)
                incoming={'reason':'internal_projection_error'}
                sources=(r['performance_summary'],reload['performance_summary'],incoming)
                for name,source in zip(names,sources):
                    source.pop(key,None)
                    if name!='absent':source[key]=copy.deepcopy(values[name])
                with ExitStack() as s:
                    s.enter_context(patch.object(consumer.supabase_client,'load_run_row',return_value=r))
                    s.enter_context(patch.object(consumer,'get_account_run_request',return_value=q))
                    s.enter_context(patch.object(contract,'load_terminal_evidence',return_value=(reload,h)))
                    s.enter_context(patch.object(consumer.supabase_client,'get_account_username',return_value='synthetic'))
                    s.enter_context(patch.object(consumer,'log'))
                    audit=s.enter_context(patch.object(consumer,'_audit'))
                    publish=s.enter_context(patch.object(consumer.runtime_incidents,'publish_account_incident',
                        return_value={'published':False}))
                    fallback=s.enter_context(patch.object(consumer,'_proven_preflight_binding_error',
                        wraps=consumer._proven_preflight_binding_error))
                    consumer._publish_run_failure_incident(request_id=q['id'],account_id=q['account_id'],
                        run_id=r['id'],run_type='account_session',exit_code=1,timed_out=False,canceled=False,
                        worker_summary=incoming,cleanup_completed=True,lock_released=True)
                rejected=any(n in {'foreign','malformed'} for n in names)
                preserved=any(c.kwargs.get('payload',{}).get('authoritative_success_preserved') is True
                              for c in audit.call_args_list)
                self.assertEqual(preserved,not rejected)
                self.assertEqual(publish.call_count,int(rejected))
                self.assertEqual(fallback.call_count,int(names==('absent','absent','absent')))

    def test_all_terminal_proof_sources_at_real_incident_boundary(self):
        valid = fixture()[1]['performance_summary']['restriction_preflight_terminal_contract']
        foreign = {**valid, 'authorization_id': 'foreign-authorization'}
        other = {**valid, 'run_id': 'foreign-run'}
        absent = object()
        cases = [
            ('valid_persisted', valid, absent, True),
            ('valid_runtime', absent, valid, True),
            ('foreign_persisted', foreign, absent, False),
            ('foreign_runtime_review_counterexample', absent, foreign, False),
            ('valid_persisted_foreign_runtime', valid, foreign, False),
            ('foreign_persisted_valid_runtime', foreign, valid, False),
            ('contradictory_sources', foreign, other, False),
            ('absent_everywhere_legacy_authority', absent, absent, True),
            ('null_runtime', valid, None, False),
            ('incomplete_persisted', {}, valid, False),
            ('malformed_runtime', absent, ['invalid'], False),
            ('matching_valid_sources', valid, valid, True),
        ]
        for name, persisted, incoming, accepted in cases:
            with self.subTest(case=name):
                q, r, h = fixture()
                key = 'restriction_preflight_terminal_contract'
                r['performance_summary'].pop(key)
                failure = {'reason': 'internal_projection_error'}
                if persisted is not absent:
                    r['performance_summary'][key] = copy.deepcopy(persisted)
                if incoming is not absent:
                    failure[key] = copy.deepcopy(incoming)
                before = copy.deepcopy((q, r, h, failure))
                with ExitStack() as stack:
                    stack.enter_context(patch.object(consumer.supabase_client, 'load_run_row', return_value=r))
                    stack.enter_context(patch.object(consumer, 'get_account_run_request', return_value=q))
                    stack.enter_context(patch.object(contract, 'load_terminal_evidence', return_value=(r, h)))
                    stack.enter_context(patch.object(consumer.supabase_client, 'get_account_username', return_value='synthetic'))
                    stack.enter_context(patch.object(consumer, 'log'))
                    audit = stack.enter_context(patch.object(consumer, '_audit'))
                    publish = stack.enter_context(patch.object(consumer.runtime_incidents,
                        'publish_account_incident', return_value={'published': False}))
                    fallback = stack.enter_context(patch.object(consumer, '_proven_preflight_binding_error',
                        wraps=consumer._proven_preflight_binding_error))
                    consumer._publish_run_failure_incident(request_id=q['id'], account_id=q['account_id'],
                        run_id=r['id'], run_type='account_session', exit_code=1, timed_out=False,
                        canceled=False, worker_summary=failure, cleanup_completed=True, lock_released=True)
                preserved = any(call.kwargs.get('payload', {}).get('authoritative_success_preserved') is True
                                for call in audit.call_args_list)
                self.assertEqual(preserved, accepted)
                self.assertEqual(publish.call_count, 0 if accepted else 1)
                self.assertEqual(fallback.call_count, int(persisted is absent and incoming is absent))
                self.assertEqual((q, r, h, failure), before)
                if name=='foreign_runtime_review_counterexample':
                    merged={**r,'performance_summary':{**r['performance_summary'],**failure}}
                    canonical=contract.validate_terminal(request=q,run=merged,hold=h,
                        cleanup_completed=True,lock_released=True)
                    self.assertIs(canonical.get('ok'),False,canonical)
                    self.assertFalse(preserved)
                    fallback.assert_not_called()
                    self.assertEqual(publish.call_count,1)
                    import json
                    print(json.dumps({'COUNTEREXAMPLE':name,'CANONICAL_MERGED_PROOF_VALID':False,
                        'AUTHORITATIVE_SUCCESS_PRESERVED':preserved,'FALLBACK_USED':False,
                        'FAIL_CLOSED':True}))

    def test_internal_binding_failure_requires_independent_physical_evidence(self):
        q,r,h=fixture()
        r['performance_summary'].update(reason='restriction_preflight_terminal_binding_missing',
            failure_module='restriction_preflight_terminal_contract',failure_function='success_summary')
        r['performance_summary'].pop('restriction_preflight_terminal_contract')
        r['status']='failed'
        self.assertTrue(consumer._proven_preflight_binding_error(request=q,run=r,hold=h))
        with patch.object(consumer.supabase_client,'load_run_row',return_value=r), \
             patch.object(consumer,'get_account_run_request',return_value=q), \
             patch.object(contract,'load_terminal_evidence',return_value=(r,h)), \
             patch.object(consumer,'_audit') as audit, \
             patch.object(consumer.runtime_incidents,'publish_account_incident') as publish:
            consumer._publish_run_failure_incident(request_id=q['id'],account_id=q['account_id'],
                run_id=r['id'],run_type='account_session',exit_code=1,timed_out=False,canceled=False,
                cleanup_completed=True,lock_released=True)
            publish.assert_not_called()
            self.assertTrue(audit.call_args.kwargs['payload']['authoritative_success_preserved'])
        for mutate in (lambda q,r,h:h.update(verified_by_run_id='other'),
                       lambda q,r,h:h.update(status='active'),
                       lambda q,r,h:r.update(total_follow=1),
                       lambda q,r,h:q['metadata_safe'].update(authorization_id='other')):
            a,b,c=copy.deepcopy((q,r,h));mutate(a,b,c)
            self.assertFalse(consumer._proven_preflight_binding_error(request=a,run=b,hold=c))

    def finalize(self, request,run,hold,*,cleanup=True,released=True,persisted=True):
        with ExitStack() as s:
            mocks={}
            for obj,name,value in [(consumer,'get_account_run_request',request),
                (consumer.supabase_client,'load_run_row',run),
                (contract,'load_terminal_evidence',(run,hold)),
                (store,'complete_restriction_preflight',dict(persisted=persisted)),
                (consumer,'_safe_complete_account_run_request',True),
                (consumer,'_reconcile_linked_run',{}),(consumer,'_audit',None),
                (consumer,'_publish_run_failure_incident',None),
                (consumer,'_safe_login_provisioner_summary_for_audit',{})]:
                mocks[name]=s.enter_context(patch.object(obj,name,return_value=value))
            consumer._finalize_manual_run_after_subprocess(SimpleNamespace(worker_id='worker'),
                request_id=request['id'],account_id=request['account_id'],exit_code=0,
                cleanup_completed=cleanup,lock_released=released)
            return mocks

    def test_success_is_not_a_business_phase_completion(self):
        q,r,h=fixture();m=self.finalize(q,r,h)
        self.assertNotIn('phase_terminal_contract',r['performance_summary'])
        m['_publish_run_failure_incident'].assert_not_called()
        self.assertEqual(m['_safe_complete_account_run_request'].call_args.args[2],'completed')
        self.assertEqual(m['complete_restriction_preflight'].call_args.kwargs['run_id'],'run')

    def test_old_reason_only_success_cannot_bypass(self):
        q,r,h=fixture();r['performance_summary'].pop('restriction_preflight_terminal_contract')
        self.assert_rejected(q,r,h)

    def assert_rejected(self,q,r,h,**kw):
        m=self.finalize(q,r,h,**kw)
        self.assertEqual(m['_safe_complete_account_run_request'].call_args.args[2],'failed')
        m['_publish_run_failure_incident'].assert_called_once()

    def test_cross_binding_and_missing_proofs_rejected(self):
        for field in ('run_id','request_id','account_id','incident_id','authorization_id','resume_plan_id','hold_id'):
            with self.subTest(field=field):
                q,r,h=fixture();r['performance_summary']['restriction_preflight_terminal_contract'][field]='other'
                self.assert_rejected(q,r,h)

    def test_foreign_canonical_authorization_cannot_fall_back_to_success(self):
        q,r,h=fixture()
        proof=r['performance_summary']['restriction_preflight_terminal_contract']
        proof['authorization_id']='foreign-authorization'
        validated=contract.validate_terminal(request=q,run=r,hold=h,
            cleanup_completed=True,lock_released=True)
        self.assertFalse(validated['ok'],validated)
        accepted,authority=consumer._authoritative_success_before_internal_failure(
            request=q,run=r,hold=h,failure_summary={},
            cleanup_completed=True,lock_released=True)
        self.assertFalse(accepted)
        self.assertEqual(authority,'restriction_preflight_terminal_proof_rejected')
        self.assert_rejected(q,r,h)

    def test_absent_canonical_proof_keeps_existing_physical_authority_only(self):
        q,r,h=fixture()
        r['performance_summary'].pop('restriction_preflight_terminal_contract')
        r['performance_summary'].update(
            reason='restriction_preflight_terminal_binding_missing',
            failure_module='restriction_preflight_terminal_contract',
            failure_function='success_summary')
        r['status']='failed'
        accepted,authority=consumer._authoritative_success_before_internal_failure(
            request=q,run=r,hold=h,failure_summary={},
            cleanup_completed=True,lock_released=True)
        self.assertTrue(accepted)
        self.assertEqual(authority,'restriction_preflight_terminal_success')

    def test_nonzero_or_unknown_actions_rejected(self):
        for value in (1,None,False,'0'):
            with self.subTest(value=value):
                q,r,h=fixture();r['performance_summary']['business_actions_executed']=value
                self.assert_rejected(q,r,h)
        for field in ('total_follow','total_like','total_dm','total_story'):
            q,r,h=fixture();r[field]=1;self.assert_rejected(q,r,h)

    def test_physical_hold_and_status_failure_rejected(self):
        for change in (lambda q,r,h:h.update(status='active'),
                       lambda q,r,h:h.update(verified_by_run_id='old'),
                       lambda q,r,h:r.update(status='failed'),
                       lambda q,r,h:r['performance_summary'].update(physical_preflight_passed=False),
                       lambda q,r,h:q['metadata_safe']['resume_plan']['phases_to_run'].update(follow=True)):
            q,r,h=fixture();change(q,r,h);self.assert_rejected(q,r,h)

    def test_cleanup_lock_or_plan_persistence_failure_rejected(self):
        for kw in ({'cleanup':False},{'released':False}):
            self.assert_rejected(*fixture(),**kw)

    def test_post_success_plan_failure_preserves_proof_without_false_incident(self):
        q,r,h=fixture(); before=copy.deepcopy(r)
        m=self.finalize(q,r,h,persisted=False)
        self.assertEqual(m['_safe_complete_account_run_request'].call_args.args[2],'failed')
        self.assertEqual(m['_safe_complete_account_run_request'].call_args.kwargs['error_code'],
                         'resume_plan_reconciliation_required')
        m['_publish_run_failure_incident'].assert_not_called()
        m['_reconcile_linked_run'].assert_not_called()
        self.assertEqual(r,before)
        self.assertTrue(m['_audit'].call_args.kwargs['payload']['physical_success_preserved'])

    def test_ordinary_business_contract_is_still_required(self):
        q,r,h=fixture();q['metadata_safe']={};r['performance_summary']={}
        self.assert_rejected(q,r,h)
        r['performance_summary']['phase_terminal_contract']={'ok':True}
        m=self.finalize(q,r,h);m['_publish_run_failure_incident'].assert_not_called()
        m['complete_restriction_preflight'].assert_not_called()

    def test_preflight_cannot_use_a_forged_business_phase_contract(self):
        q,r,h=fixture();r['performance_summary']['phase_terminal_contract']={'ok':True}
        h['status']='active';self.assert_rejected(q,r,h)

    @staticmethod
    def business_fixture():
        from account_session_orchestrator import _TERMINAL_PHASE_STATUSES
        request=dict(id='request',run_id='run',account_id='account',status='completed',
            requested_run_type='account_session',metadata_safe={})
        phases=dict(welcome='not_planned',follow='completed',unfollow='partial_resumable',
                    outreach='skipped_cleanly')
        run=dict(id='run',account_id='account',status='completed',
            finished_at='2026-09-15T00:00:00Z',performance_summary=dict(
                session_status='success',exit_code=0,phase_terminal_contract=dict(
                    ok=True,reason='all_planned_phases_terminal',phases=phases,
                    non_terminal_phases={},terminal_statuses=sorted(_TERMINAL_PHASE_STATUSES))))
        failure=dict(reason='resume_plan_reconciliation_required',
            failure_module='account_session_resume_plan_store',
            failure_function='record_end_of_session')
        return request,run,failure

    def test_generic_post_success_internal_failure_is_observable_not_blocking(self):
        request,run,failure=self.business_fixture()
        with patch.object(consumer.supabase_client,'load_run_row',return_value=run), \
             patch.object(consumer,'get_account_run_request',return_value=request), \
             patch.object(consumer,'_audit') as audit, patch.object(consumer,'log') as log, \
             patch.object(consumer.runtime_incidents,'publish_account_incident') as publish:
            consumer._publish_run_failure_incident(request_id='request',account_id='account',
                run_id='run',run_type='account_session',exit_code=1,timed_out=False,canceled=False,
                worker_summary=failure,cleanup_completed=True,lock_released=True)
        publish.assert_not_called()
        self.assertEqual(log.call_args.args[:2],('error','post_success_internal_reconciliation_failed'))
        self.assertTrue(audit.call_args.kwargs['payload']['authoritative_success_preserved'])
        self.assertEqual(audit.call_args.kwargs['payload']['success_proof'],
                         'account_session_phase_terminal_success')

    def test_generic_guard_fails_closed_without_exact_success_binding(self):
        mutations=(
            lambda q,r,f:r.update(status='failed'),
            lambda q,r,f:r.update(finished_at=None),
            lambda q,r,f:q.update(run_id='foreign-run'),
            lambda q,r,f:r['performance_summary'].update(session_status='failed'),
            lambda q,r,f:r['performance_summary']['phase_terminal_contract'].update(ok=False),
            lambda q,r,f:r['performance_summary']['phase_terminal_contract']['phases'].update(follow='failed'),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                request,run,failure=copy.deepcopy(self.business_fixture());mutate(request,run,failure)
                proven,_=consumer._authoritative_success_before_internal_failure(
                    request=request,run=run,hold=None,failure_summary=failure,
                    cleanup_completed=True,lock_released=True)
                self.assertFalse(proven)
        request,run,failure=self.business_fixture()
        for cleanup,released in ((False,True),(True,False),(None,True),(True,None)):
            proven,_=consumer._authoritative_success_before_internal_failure(
                request=request,run=run,hold=None,failure_summary=failure,
                cleanup_completed=cleanup,lock_released=released)
            self.assertFalse(proven)

    def test_guard_uses_success_truth_not_error_name(self):
        request,run,failure=self.business_fixture()
        for reason,module,function in (
            ('terminal_proof_normalization_failed','unknown_serializer','normalize'),
            ('worker_exit_nonzero','runner','main'),
            ('brand_new_unlisted_failure','future_module','future_function'),
        ):
            with self.subTest(reason=reason):
                failure.update(reason=reason,failure_module=module,failure_function=function)
                proven,proof=consumer._authoritative_success_before_internal_failure(
                    request=request,run=run,hold=None,failure_summary=failure,
                    cleanup_completed=True,lock_released=True)
                self.assertTrue(proven)
                self.assertEqual(proof,'account_session_phase_terminal_success')

    def test_arbitrary_post_success_error_is_observable_and_never_blocking(self):
        request,run,failure=self.business_fixture()
        failure.update(reason='unlisted_projection_contract_defect',
                       failure_module='future_projection_module',
                       failure_function='serialize_future_receipt')
        with patch.object(consumer.supabase_client,'load_run_row',return_value=run), \
             patch.object(consumer,'get_account_run_request',return_value=request), \
             patch.object(consumer,'_audit') as audit, patch.object(consumer,'log') as log, \
             patch.object(consumer.runtime_incidents,'publish_account_incident') as publish:
            consumer._publish_run_failure_incident(request_id='request',account_id='account',
                run_id='run',run_type='account_session',exit_code=1,timed_out=False,canceled=False,
                worker_summary=failure,cleanup_completed=True,lock_released=True)
        publish.assert_not_called()
        self.assertTrue(audit.call_args.kwargs['payload']['authoritative_success_preserved'])
        self.assertEqual(log.call_args.args[:2],('error','post_success_internal_reconciliation_failed'))

    def test_later_real_operation_failure_is_not_masked_by_prior_phase_success(self):
        request,run,failure=self.business_fixture()
        # The later operation has changed the authoritative whole-run state;
        # the earlier phase receipt is historical evidence, not session success.
        run.update(status='failed',finished_at='2026-09-15T00:01:00Z')
        run['performance_summary']['session_status']='failed'
        run['performance_summary']['phase_terminal_contract'].update(
            ok=False,reason='planned_phase_non_terminal',
            non_terminal_phases={'outreach':'failed'})
        run['performance_summary']['phase_terminal_contract']['phases']['outreach']='failed'
        failure.update(reason='device_unavailable',failure_module='runner',failure_function='outreach')
        with patch.object(consumer.supabase_client,'load_run_row',return_value=run), \
             patch.object(consumer,'get_account_run_request',return_value=request), \
             patch.object(consumer.supabase_client,'get_account_username',return_value='synthetic'), \
             patch.object(consumer.runtime_incidents,'publish_account_incident',
                          return_value={'published':False}) as publish:
            consumer._publish_run_failure_incident(request_id='request',account_id='account',
                run_id='run',run_type='account_session',exit_code=1,timed_out=False,canceled=False,
                worker_summary=failure,cleanup_completed=True,lock_released=True)
        publish.assert_called_once()

    def test_incoming_operation_failure_cannot_use_stale_persisted_success(self):
        request,run,failure=self.business_fixture()
        failure.update(session_status='failed',exit_code=1,
            phase_terminal_contract=dict(ok=False,reason='planned_phase_non_terminal',
                phases={'outreach':'failed'},non_terminal_phases={'outreach':'failed'}),
            reason='device_unavailable',failure_module='runner',failure_function='outreach')
        proven,authority=consumer._authoritative_success_before_internal_failure(
            request=request,run=run,hold=None,failure_summary=failure,
            cleanup_completed=True,lock_released=True)
        self.assertFalse(proven)
        self.assertEqual(authority,'incoming_terminal_failure_contradicts_persisted_success')

    def test_pre_success_real_failure_still_reaches_existing_publisher(self):
        request,run,failure=self.business_fixture();run['status']='failed'
        failure.update(reason='worker_exit_nonzero',failure_module='runner',failure_function='main')
        with patch.object(consumer.supabase_client,'load_run_row',return_value=run), \
             patch.object(consumer,'get_account_run_request',return_value=request), \
             patch.object(consumer.supabase_client,'get_account_username',return_value='synthetic'), \
             patch.object(consumer.runtime_incidents,'publish_account_incident',
                          return_value={'published':False}) as publish:
            consumer._publish_run_failure_incident(request_id='request',account_id='account',
                run_id='run',run_type='account_session',exit_code=1,timed_out=False,canceled=False,
                worker_summary=failure,cleanup_completed=True,lock_released=True)
        publish.assert_called_once()


    def test_malformed_structures_fail_closed(self):
        for field in ('restriction_preflight_terminal_contract','physical_preflight_passed'):
            q,r,h=fixture();r['performance_summary'][field]=['invalid'];self.assert_rejected(q,r,h)
        q,r,h=fixture();r['performance_summary']['preflight_cleanup_completed']=False
        self.assert_rejected(q,r,h)


class PlanPersistenceTests(unittest.TestCase):
    def test_exact_plan_patch_preserves_quotas_and_original_plan(self):
        import supabase_client
        q,r,h=fixture();writes=[]
        row=dict(run_id='run',run_request_id='request',account_id='account')
        def write(*args,**kwargs):
            writes.append(kwargs);row.update(kwargs['body']);return [row]
        with patch.object(supabase_client,'_request_json',side_effect=write),patch.object(store,'load_resume_plan',return_value=row):
            result=store.complete_restriction_preflight(run_id='run',request_id='request',account_id='account',terminal_summary=r['performance_summary'])
        self.assertTrue(result['persisted'])
        self.assertEqual(writes[0]['query']['run_request_id'],'eq.request')
        self.assertNotIn('plan',writes[0]['body']);self.assertNotIn('quota_done',writes[0]['body'])

    def test_ambiguous_transport_requires_matching_canonical_reread(self):
        import supabase_client
        q,r,h=fixture()
        with patch.object(supabase_client,'_request_json',side_effect=OSError),patch.object(store,'load_resume_plan',return_value={}):
            self.assertFalse(store.complete_restriction_preflight(run_id='run',request_id='request',account_id='account',terminal_summary=r['performance_summary'])['persisted'])


class RunnerCleanupTests(unittest.TestCase):
    def test_actual_cleanup_persists_bound_summary_before_request_completion(self):
        import runner
        q,r,h=fixture();events=[]
        with ExitStack() as s:
            for name,val in [('_DEFER_TERMINAL_RUN_STATUS_UNTIL_CLEANUP',True),('_RUNNER_SESSION_CLEANUP_COMPLETE',False),('_PENDING_TERMINAL_RUN_STATUS',None)]:
                s.enter_context(patch.object(runner,name,val))
            s.enter_context(patch.object(runner,'set_wait_event_callback'))
            s.enter_context(patch.object(runner,'_cleanup_session_apps',side_effect=lambda d:events.append('cleanup') or True))
            s.enter_context(patch.object(runner,'_terminalize_run_request_after_cleanup',side_effect=lambda status:events.append(status)))
            s.enter_context(patch.object(runner,'_orf_publish_event'))
            s.enter_context(patch.object(runner,'_orf_heartbeat_worker'))
            s.enter_context(patch.object(runner,'log'))
            def persist(*a,**k):
                events.append(k['performance_summary']);return {'_supabase_call_ok':True}
            s.enter_context(patch.object(runner,'_timed_safe_supabase_call',side_effect=persist))
            runner._update_run_status_safe(run_id='run',status='completed',totals={},performance_summary=r['performance_summary'])
            self.assertEqual(events,[])
            self.assertEqual(runner._return_with_cleanup(None,0),0)
        self.assertEqual(events[0],'cleanup');self.assertEqual(events[1],r['performance_summary']);self.assertEqual(events[2],'completed')


    def test_failed_cleanup_never_publishes_preflight_success(self):
        import runner
        q,r,h=fixture()
        pending=dict(run_id='run',status='completed',totals={},performance_summary=r['performance_summary'])
        with patch.object(runner,'_PENDING_TERMINAL_RUN_STATUS',pending),patch.object(runner,'_cleanup_session_apps',return_value=False),patch.object(runner,'_update_run_status_safe',return_value='failed') as update,patch.object(runner,'_terminalize_run_request_after_cleanup'),patch.object(runner,'set_wait_event_callback'),patch.object(runner,'log'):
            self.assertEqual(runner._return_with_cleanup(None,0),1)
        self.assertEqual(update.call_args.kwargs['status'],'failed')
        self.assertEqual(update.call_args.kwargs['performance_summary']['reason'],'restriction_preflight_cleanup_failed')


import os
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_restriction_resume_natural_tick_v1 as previous

@unittest.skipUnless(os.environ.get('RESTRICTION_TEST_SOCKET'), 'isolated PG17 required')
class PreflightDatabaseTests(previous.RealDatabaseTests):
    def setUp(self):self.sql('select restriction_test.reset_ready()')

    def test_current_plan_terminalizes_and_business_eligibility_survives(self):
        first=previous.run_backend(self)
        self.assertEqual(first['result']['enqueued_count'],1,first)
        q=self.claim();admitted=self.admit(q);self.assertTrue(admitted['ok'],admitted)
        run_id=admitted['run_id'];q=self.row(q['id'])
        release=self.rpc('release_instagram_action_restriction_hold_v1',dict(
            p_account_id=previous.A,p_incident_id=previous.INCIDENT,p_preflight_run_id=run_id))
        summary=contract.success_summary(account_id=previous.A,request_id=q['id'],run_id=run_id,
            policy=q['metadata_safe']['resume_plan'],released=release)
        summary['preflight_cleanup_completed']=True
        self.sql(f"update ig_runs set status='completed',finished_at=now(),performance_summary='{json.dumps(summary)}',total_follow=0,total_like=0,total_dm=0,total_story=0 where id='{run_id}'")
        self.rpc('complete_account_run_request',dict(p_request_id=q['id'],p_worker_id=previous.WORKER,p_status='completed'))
        from auto_restart_device_lock import release_device_lock
        result=release_device_lock(device_id=previous.DEVICE,worker_id=previous.WORKER,
            request_id=q['id'],expected_lease_id=q['metadata_safe']['device_lock_binding']['lease_id'],account_id=previous.A)
        self.assertTrue(result['released'],result)
        before=self.sql(f"select plan from account_session_resume_plans where run_id='{run_id}'")
        with patch.object(consumer,'_publish_run_failure_incident') as incident,patch.object(consumer,'_audit'):
            consumer._finalize_manual_run_after_subprocess(SimpleNamespace(worker_id=previous.WORKER),
                request_id=q['id'],account_id=previous.A,exit_code=0,cleanup_completed=True,lock_released=True)
        incident.assert_not_called()
        self.assertEqual(self.sql(f"select status from ig_runs where id='{run_id}'"),'completed')
        self.assertEqual(self.row(q['id'])['status'],'completed')
        self.assertEqual(self.sql(f"select resume_state from account_session_resume_plans where run_id='{run_id}'"),'completed')
        self.assertEqual(self.sql(f"select plan from account_session_resume_plans where run_id='{run_id}'"),before)
        eligible=previous.run_backend(self,'eligibility');self.assertTrue(eligible['ok'],eligible)
        self.assertEqual(self.rpc('restore_restriction_admission_credit_v1')['restriction_admission_credits_restored'],0)

for _name in dir(previous.RealDatabaseTests):
    if _name.startswith('test_'):
        setattr(PreflightDatabaseTests,_name,None)
