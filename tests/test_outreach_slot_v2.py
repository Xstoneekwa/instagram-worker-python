import ast
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
import unittest
import outreach_slot_contract as contract
from outreach_measurements import validate_policy
import test_outreach_slot_contract as v1tests


def actual_function(file, name, env):
    node=next(n for n in ast.parse(Path(file).read_text()).body if isinstance(n,ast.FunctionDef) and n.name==name)
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),file,'exec'),env)
    return env[name]


class V2Tests(unittest.TestCase):
    setUp=v1tests.SlotTests.setUp
    budget=v1tests.SlotTests.budget

    def test_untrusted_sources(self):
        for source in ('synthetic','test','fixture','welcome','unknown',''):
            with self.subTest(source=source),self.assertRaisesRegex(ValueError,'provenance'):
                validate_policy({**self.ctx,'execution_policy':{**self.ctx['execution_policy'],'measurement_source':source}},self.clock.now)

    def test_stale_missing_and_incompatible_scope(self):
        for changes in ({'expires_at':'2000-01-01T00:00:00Z'}, {'approved_by':''},
                        {'device_id':'wrong'}, {'sender_identity':''}, {'sample_count':0},
                        {'sample_count_justification':''}, {'cleanup_grace_ms':0}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_policy({**self.ctx,'execution_policy':{**self.ctx['execution_policy'],**changes}},self.clock.now)

    def test_bootstrap_no_empirical_cutoff(self):
        policy={**self.ctx['execution_policy'],'mode':'bootstrap','slot_id':self.ctx['slot_id'],'target_dm':30}
        for key in ('unit_ms','cleanup_ms','measurement_source','sample_count'):policy.pop(key)
        b=self.budget(execution_policy=policy);self.clock.advance(2399.998);b.check(unit=True)
        for changes in ({'slot_id':'next-slot'},{'device_id':'wrong'},{'target_dm':31}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_policy({**self.ctx,'execution_policy':{**policy,**changes}},self.clock.now)
        with self.assertRaises(ValueError):validate_policy({**self.ctx,'execution_policy':policy,'attempt_id':2},self.clock.now)

    def test_valid_normal_and_no_self_promotion(self):
        self.budget().check(unit=True)
        for changes in ({'authority':'candidate_only'},{'approval_id':None}):
            with self.assertRaises(ValueError):validate_policy({**self.ctx,'execution_policy':{**self.ctx['execution_policy'],**changes}},self.clock.now)

    def test_boundary_minus_at_plus_one_ms_before_send(self):
        for offset in (-.001,0,.001):
            self.setUp();b=self.budget();self.clock.advance(2400+offset)
            if offset<0:b.before_send('job');self.assertEqual(len(self.calls),1)
            else:
                with self.assertRaisesRegex(contract.SlotBlocked,'slot_deadline'):b.before_send('job')
                self.assertEqual(self.calls,[])

    def test_boundary_claim_navigation_no_send(self):
        for phase in ('idle','claimed','navigation','before_send'):
            for offset in (0,.001):
                self.setUp();b=self.budget();self.clock.advance(2400+offset)
                with self.subTest(phase=phase,offset=offset),self.assertRaises(contract.SlotBlocked):
                    (b.claim if phase=='idle' else b.begin_unit if phase in ('claimed','navigation') else lambda:b.before_send('job'))()
                self.assertEqual(self.calls,[])

    def test_runtime_binding_rejects_changed_sender_and_instagram(self):
        b=self.budget()
        with patch('outreach_measurements.sender_identity',return_value='other'):
            with self.assertRaisesRegex(contract.SlotBlocked,'runtime_changed'):b.bind_device(SimpleNamespace(app_info=lambda _: {'versionName':'test-only'}))
        with patch('outreach_measurements.sender_identity',return_value='test-only'):
            with self.assertRaises(contract.SlotBlocked):b.bind_device(SimpleNamespace(app_info=lambda _: {'versionName':'changed'}))
            b.bind_device(SimpleNamespace(app_info=lambda _: {'versionName':'test-only'}));self.assertTrue(b.device_verified)

    def test_success_observations_remain_candidates(self):
        b=self.budget();b.begin_unit();self.clock.advance(2);b.record_unit({'outcome':'sent','navigation_to_username_typed_total_ms':123})
        b.begin_unit();self.clock.advance(1);b.record_unit({'outcome':'failed_retry'})
        self.assertEqual(len(b.observations),1);self.assertEqual(b.observations[0]['duration_ms'],2000)
        captured=[]
        b.rpc=lambda name,payload:captured.append(payload) or v1tests.terminal_receipt(self.ctx,sent=11,reason='no_safe_work')
        b.finish(cleanup_ok=True,cleanup_ms=100,run_id="run")
        self.assertEqual(captured[0]['p_observations']['promotion_status'],'candidate_only')
        self.assertEqual(captured[0]['p_observations']['measurement_source'],'unverified')


class WatchdogTests(unittest.TestCase):
    def supervise(self, exit_after=None, manual=False):
        start=datetime(2026,9,15,8,40,tzinfo=timezone.utc);now=[start];events=[]
        class FakeDate:
            @staticmethod
            def now(tz):return now[0]
        proc=SimpleNamespace(poll=lambda:0 if exit_after is not None and (now[0]-start).total_seconds()>=exit_after else None)
        env={'datetime':FakeDate,'timezone':timezone,'timedelta':timedelta,
          'time':SimpleNamespace(monotonic=lambda:(now[0]-start).total_seconds(),sleep=lambda sec:now.__setitem__(0,now[0]+timedelta(seconds=sec))),
          'parse_cooperative_stop_utc':lambda x:datetime.fromisoformat(x) if x else None,
          '_fleet_child_fully_exited':lambda *a:True,'get_account_run_request':lambda _: {'cancel_requested_at':'yes'} if manual else None,
          '_fleet_supervise_existing':lambda *args:args[3], 'request_cooperative_stop':lambda *a,**kw:events.append(kw['reason']),
          'read_cooperative_stop_intent':lambda _:None,'read_cooperative_stop_ack':lambda _:None,
          'record_termination_origin':lambda *a,**k:None,'_terminate_subprocess':lambda _:events.append('kill') or -15,'log':lambda *a,**k:None,
          'renew_account_run_request_lease':lambda *a,**k:True}
        fn=actual_function('account_run_request_consumer.py','_wait_for_subprocess',env)
        result=fn(SimpleNamespace(subprocess_timeout_seconds=7200,heartbeat_seconds=30,worker_id='w'),proc,
          request_id='r',account_id='a',stop_context=object(),business_action_deadline=start.isoformat(),
          scheduled_session_end=(start+timedelta(seconds=3)).isoformat(),outreach_cleanup_grace_ms=3000)
        return result,events
    def test_idle_or_cleanup_exits_normally_during_grace(self):
        for duration in (1,2):
            result,events=self.supervise(duration);self.assertEqual(result,(0,False));self.assertEqual(events,['scheduled_business_deadline'])
    def test_stuck_worker_or_cleanup_escalates_only_at_grace(self):
        result,events=self.supervise();self.assertEqual(result,(-15,True));self.assertEqual(events,['scheduled_business_deadline','kill'])
    def test_manual_stop_cooperative(self):
        result,events=self.supervise(1,manual=True);self.assertEqual(result,(0,False));self.assertEqual(events,['human_manual_stop'])


class CleanupTests(unittest.TestCase):
    def test_cleanup_attempted_and_publication_failure_is_technical(self):
        for stop_failed,home_failed,persist_failed in ((False,False,False),(True,False,False),(False,True,False),(False,False,True)):
            f=v1tests.SlotTests();f.setUp()
            slot=SimpleNamespace(event=Mock(),context=f.ctx,finished=False,
                finish=Mock(side_effect=RuntimeError('db') if persist_failed else None,return_value=v1tests.terminal_receipt(f.ctx,cleanup=not(stop_failed or home_failed))))
            stop=Mock(side_effect=RuntimeError('offline') if stop_failed else None,return_value=(0,'',''))
            d=SimpleNamespace(press=Mock(side_effect=RuntimeError('offline') if home_failed else None))
            env={'set_wait_event_callback':Mock(),'time':SimpleNamespace(monotonic=lambda:0),'force_stop':stop,'log':Mock(),'_CURRENT_RUN_ID':'run'}
            fn=actual_function('runner.py','_outreach_cleanup_and_finish',env)
            with patch.object(contract,'active',return_value=slot),patch('device.shell',stop),patch('cooperative_business_stop.StopContext.from_env',return_value=None):code=fn(d,0)
            stop.assert_called_once();d.press.assert_called_once_with('home');slot.finish.assert_called_once()
            self.assertEqual(slot.finish.call_args.kwargs['cleanup_ok'],not(stop_failed or home_failed))
            self.assertEqual(code,1 if stop_failed or home_failed or persist_failed else 0)

    def test_unsafe_continuity_does_not_force_device_gesture(self):
        slot=SimpleNamespace(event=Mock(),finished=False,finish=Mock(return_value={'business_failure':True}))
        stop=Mock();d=SimpleNamespace(press=Mock())
        env={'set_wait_event_callback':Mock(),'time':SimpleNamespace(monotonic=lambda:0),'force_stop':stop,'log':Mock(),'_CURRENT_RUN_ID':'run'}
        fn=actual_function('runner.py','_outreach_cleanup_and_finish',env)
        with patch.object(contract,'active',return_value=slot),patch('device.shell',stop),patch('cooperative_business_stop.StopContext.from_env',return_value=None):self.assertEqual(fn(d,1,allow_device_cleanup=False),1)
        stop.assert_not_called();d.press.assert_not_called();self.assertFalse(slot.finish.call_args.kwargs['cleanup_ok'])

class TerminalAndOwnershipTests(unittest.TestCase):
    def test_outer_runner_exits_always_attempt_cleanup(self):
        for label,result,error in [('deadline',0,None),('target',0,None),('manual',1,None),
              ('restriction',76,None),('challenge',78,None),('identity',75,None),
              ('offline',1,RuntimeError('offline')),('exception',1,RuntimeError('fault')),
              ('ambiguous',1,None)]:
            slot=SimpleNamespace(cleanup_attempted=False)
            cleanup=Mock(side_effect=lambda d,code,**kw:code)
            env={'_main_with_existing_handlers':Mock(return_value=result,side_effect=error),
                 '_outreach_cleanup_and_finish':cleanup,'_CURRENT_DEVICE':object(),'log':Mock()}
            fn=actual_function('runner.py','main',env)
            with patch.object(contract,'active',return_value=slot):code=fn()
            with self.subTest(label=label):cleanup.assert_called_once();self.assertEqual(code,result)

    def test_nonzero_android_force_stop_not_success(self):
        slot=SimpleNamespace(event=Mock(),context={'package_name':'com.instagram.android'},finished=False,
            finish=Mock(return_value={'business_failure':True}))
        d=SimpleNamespace(press=Mock())
        env={'set_wait_event_callback':Mock(),'time':SimpleNamespace(monotonic=lambda:0),'log':Mock(),'_CURRENT_RUN_ID':'run'}
        fn=actual_function('runner.py','_outreach_cleanup_and_finish',env)
        with patch.object(contract,'active',return_value=slot),patch('device.shell',return_value=(1,'failure','')),patch('cooperative_business_stop.StopContext.from_env',return_value=None):self.assertEqual(fn(d,0),1)
        self.assertFalse(slot.finish.call_args.kwargs['cleanup_ok']);d.press.assert_called_once()

    def test_actual_dispatch_tail_releases_exact_device_lease_before_finalization(self):
        tree=ast.parse(Path('account_run_request_consumer.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_handle_claimed_request')
        index=next(i for i,n in enumerate(node.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='lock_released' for t in n.targets))
        block=ast.fix_missing_locations(ast.Module(body=node.body[index:],type_ignores=[]))
        for exit_code,timed_out in ((0,False),(1,False),(-15,True)):
            events=[]
            release=Mock(side_effect=lambda **kw:events.append('release') or {'released':True})
            finalize=Mock(side_effect=lambda *a,**kw:events.append('finalize'))
            env=dict(device_id='d',device_lock_active=True,run_type='outreach_session',exit_code=exit_code,
                timed_out=timed_out,device_lock_binding={'lease_id':'exact-generation'},lock_owner_worker_id='w',
                account_id='a',request_id='r',cfg=object(),request={},dispatch_ctx={'device_id':'d','app_instance_id':'i','outreach_slot_v1':{'slot_id':'s'}},
                cooperative_stop_diagnostics={},cooperative_stop_context=None,release_device_lock=release,
                _finalize_manual_run_after_subprocess=finalize,_fleet_finish_execution=Mock(),
                _is_scheduled_session_preflight_run_type=lambda _:False)
            exec(compile(block,'<actual dispatcher tail>','exec'),env)
            release.assert_called_once_with(device_id='d',worker_id='w',request_id='r',expected_lease_id='exact-generation',account_id='a')
            self.assertEqual(events,['release','finalize']);self.assertTrue(finalize.call_args.kwargs['lock_released'])

    def test_dedicated_finalizer_demands_durable_terminal_receipt(self):
        import account_run_request_consumer as consumer
        f=v1tests.SlotTests();f.setUp()
        request={'id':'r','account_id':'a','run_id':'run','requested_run_type':'outreach_session','status':'running',
                 'metadata_safe':{'outreach_slot_v1':f.ctx}}
        for terminal,success in ((v1tests.terminal_receipt(f.ctx),True),
                                 (v1tests.terminal_receipt(f.ctx,cleanup=False),False),(None,False)):
            with patch.object(consumer,'get_account_run_request',return_value=request), \
                 patch.object(consumer.supabase_client,'call_rpc_once',return_value=terminal), \
                 patch.object(consumer,'_safe_complete_account_run_request') as complete, \
                 patch.object(consumer,'_reconcile_linked_run'),patch.object(consumer,'_audit'), \
                 patch.object(consumer,'_publish_run_failure_incident'):
                consumer._finalize_manual_run_after_subprocess(SimpleNamespace(worker_id='w'),request_id='r',account_id='a',exit_code=0)
            self.assertEqual(complete.call_args.args[2],'completed' if success else 'failed')
