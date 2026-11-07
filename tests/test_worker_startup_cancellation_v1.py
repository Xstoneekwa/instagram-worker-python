"""Synthetic actual runner boundaries; no device, provider or business execution."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock
import cooperative_business_stop as stops
import outreach_slot_contract as slots

# Exact approved startup delta only, using the existing frozen inverse contract.
# Historical Golden pins remain byte-identical. A foreign byte is never normalized.
_STARTUP_EXACT_INVERSE = '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'

def restore_startup_exact_delta(name, source):
    import base64, hashlib, json, zlib
    row=json.loads(zlib.decompress(base64.b64decode(_STARTUP_EXACT_INVERSE))).get(name)
    if row is None or hashlib.sha256(source.encode()).hexdigest()==row['base_sha256']:
        return source
    if hashlib.sha256(source.encode()).hexdigest()!=row['candidate_sha256']:
        raise AssertionError('startup_source_changed:'+name)
    for h in reversed(row['hunks']):
        offset,new=h['offset'],h['new']
        if source[offset:offset+len(new)]!=new:
            raise AssertionError('startup_hunk_changed:'+name)
        source=source[:offset]+h['old']+source[offset+len(new):]
    if hashlib.sha256(source.encode()).hexdigest()!=row['base_sha256']:
        raise AssertionError('startup_inverse_mismatch:'+name)
    return source


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT/'runner.py').read_text())


def actual(name, env):
    node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-runner','exec'),env)
    return env[name]


class RunnerStartupCancellationTests(TestCase):
    def setUp(self):
        self.slot=SimpleNamespace(context={'account_id':'a','request_id':'r','device_id':'d'},event=mock.Mock())
        self.active=mock.patch.object(slots,'active',return_value=self.slot)
        self.active.start();self.addCleanup(self.active.stop)
        self.env={'log':mock.Mock(),'StartupCancellationStopped':stops.StartupCancellationStopped}
        self.require=actual('_require_startup_cancellation_clear',self.env)
        self.mutate=actual('_startup_phone_mutation',self.env)
        self.context=stops.StopContext('a','r','d','r',generation='generation')
        self.context_patch=mock.patch.object(stops.StopContext,'from_env',return_value=self.context)
        self.context_patch.start();self.addCleanup(self.context_patch.stop)
        self.status='ACTIVE'
        self.decision_patch=mock.patch.object(stops,'startup_stop_decision',side_effect=lambda *a,**kw:
            stops.StartupStopDecision(self.status,'startup_request_terminal' if self.status=='STOP'
                else 'startup_control_plane_unavailable' if self.status=='UNKNOWN'
                else 'startup_cancellation_clear',stop_reason='human_manual_stop' if self.status=='STOP' else ''))
        self.reader=self.decision_patch.start();self.addCleanup(self.decision_patch.stop)
        self.guard=lambda name:self.require(account_id='a',request_id='r',run_id='run',
            device_id='d',boundary=name,required=True)

    def startup_calls(self):
        main=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='_main_impl')
        calls=sorted((n for n in ast.walk(main) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Name) and n.func.id=='_startup_phone_mutation'),key=lambda n:n.lineno)
        mutations=[];phone=SimpleNamespace()
        self.env.update(_startup_boundary=self.guard,d=phone,device_serial='SYNTHETIC',
            config=SimpleNamespace(INSTAGRAM_PACKAGE='com.instagram.android'),account_id='a',
            account_username='synthetic-emitter',run_id='run',effective_run_type='outreach_session')
        for call in calls:
            self.env[call.args[2].id]=mock.Mock(side_effect=lambda *a,**k:mutations.append('action'))
        return calls,mutations

    def execute(self,calls):
        for call in calls:eval(compile(ast.Expression(call),'actual-startup-call','eval'),self.env)

    def test_cancel_during_integrity_returns_before_every_startup_mutation(self):
        calls,mutations=self.startup_calls();self.status='STOP'
        with self.assertRaises(stops.StartupCancellationStopped):
            self.guard('after_integrity_and_activation');self.execute(calls)
        self.assertEqual(mutations,[])
        self.assertTrue(self.slot.suppress_startup_phone_cleanup)

    def test_unknown_after_long_startup_never_authorizes_mutation(self):
        calls,mutations=self.startup_calls();self.status='UNKNOWN'
        with self.assertRaises(stops.StartupCancellationStopped):self.execute(calls)
        self.assertEqual(mutations,[])
        self.assertEqual(self.slot.startup_cancellation['state'],'UNKNOWN')

    def test_cancellation_at_each_actual_startup_boundary_blocks_every_later_call(self):
        calls,_=self.startup_calls()
        for limit in range(len(calls)):
            with self.subTest(boundary=ast.literal_eval(calls[limit].args[1])):
                self.status='ACTIVE';calls,mutations=self.startup_calls()
                for call in calls[:limit]:self.execute([call])
                self.status='STOP'
                with self.assertRaises(stops.StartupCancellationStopped):self.execute(calls[limit:])
                self.assertEqual(len(mutations),limit)

    def test_cancellation_between_force_stop_and_app_start_prevents_launch(self):
        calls,mutations=self.startup_calls()
        force=next(c for c in calls if c.args[1].value=='before_force_stop')
        start=next(c for c in calls if c.args[1].value=='before_app_start')
        self.execute([force]);self.status='STOP'
        with self.assertRaises(stops.StartupCancellationStopped):self.execute([start])
        self.assertEqual(mutations,['action'])

    def test_slow_action_can_finish_but_no_new_action_after_cancellation(self):
        action=mock.Mock(side_effect=lambda:setattr(self,'status','STOP'))
        self.mutate(self.guard,'before_force_stop',action)
        after=mock.Mock()
        with self.assertRaises(stops.StartupCancellationStopped):self.mutate(self.guard,'before_app_start',after)
        action.assert_called_once();after.assert_not_called()

    def test_positive_boundaries_retain_original_action_args_and_result(self):
        action=mock.Mock(return_value='original-result')
        self.assertEqual(self.mutate(self.guard,'before_app_start',action,'d','pkg',flag=True),'original-result')
        action.assert_called_once_with('d','pkg',flag=True)
        self.assertTrue(self.slot.startup_phone_mutations_started)

    def test_non_dispatched_legacy_route_does_not_gain_a_new_get(self):
        self.require(account_id=None,request_id=None,run_id=None,device_id=None,boundary='legacy',required=False)
        self.reader.assert_not_called()

    def test_action_exception_is_not_falsely_recorded_as_zero_phone_work(self):
        with self.assertRaises(TimeoutError):
            self.mutate(self.guard,'before_app_start',mock.Mock(side_effect=TimeoutError))
        self.assertTrue(self.slot.startup_phone_mutations_started)

    def test_actual_startup_has_no_unwrapped_lifecycle_mutation(self):
        main=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='_main_impl')
        start=next(n.lineno for n in ast.walk(main) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Name) and n.func.id=='_prepare_instagram_ui_profile')
        end=next(n.lineno for n in ast.walk(main) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Name) and n.func.id=='_startup_boundary'
            and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value=='after_emitter_identity')
        names={'connect_device','disable_android_animations','lock_instagram_update_system',
            'check_instagram_version_lock','dismiss_android_permission_dialog','force_stop','app_start',
            '_prepare_device_for_account_identity_preflight','verify_active_instagram_account_matches_expected'}
        self.assertFalse([n.func.id for n in ast.walk(main) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Name) and n.func.id in names and start<=n.lineno<=end])

    def test_full_integrity_and_foreground_gates_are_not_replaced_by_attestation(self):
        main=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='_main_impl')
        calls=[n for n in ast.walk(main) if isinstance(n,ast.Call)]
        integrity=[n for n in calls if isinstance(n.func,ast.Name) and n.func.id=='verify_runtime_integrity']
        self.assertEqual(len(integrity),1)
        boundary=next(n for n in calls if isinstance(n.func,ast.Name) and n.func.id=='_startup_boundary'
            and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value=='after_integrity_and_activation')
        self.assertLess(integrity[0].lineno,boundary.lineno)
        self.assertTrue(any(isinstance(n.func,ast.Attribute) and n.func.attr=='certify_account_startup_foreground_v1' for n in calls))
        self.assertFalse(any(isinstance(n.func,ast.Name) and n.func.id in ('verify_status_trust','install_status_attestation') for n in calls))

    def test_cancellation_handler_never_rewrites_terminal_state(self):
        self.env.update(_main_impl=mock.Mock(side_effect=stops.StartupCancellationStopped(
            stops.StartupStopDecision('STOP','startup_request_terminal'))),
            certification_admission=SimpleNamespace(is_enabled=lambda:False),
            _CURRENT_ACCOUNT_ID='a',_CURRENT_RUN_REQUEST_ID='r',_CURRENT_RUN_ID='run')
        handler=actual('_main_with_existing_handlers',self.env)
        self.assertEqual(handler(),1)
        self.env['_main_impl'].assert_called_once()


class ExactStartupPreservationTests(TestCase):
    def test_three_approved_production_deltas_restore_exact_signed_parent(self):
        import subprocess
        for name in ('runner.py','cooperative_business_stop.py','outreach_slot_contract.py'):
            old=subprocess.check_output(['git','show','c6ec96c28023210ab9ead33c344d5fdc99896c10:'+name],cwd=ROOT,text=True)
            self.assertEqual(restore_startup_exact_delta(name,(ROOT/name).read_text()),old)
            self.assertEqual(restore_startup_exact_delta(name,old),old)

    def test_no_unapproved_byte_is_normalized_away(self):
        for name in ('runner.py','cooperative_business_stop.py','outreach_slot_contract.py'):
            current=(ROOT/name).read_text()
            for foreign in (current+'\n# unrelated byte\n','# moved\n'+current,current[:-1]):
                with self.subTest(name=name),self.assertRaisesRegex(AssertionError,'startup_source_changed:'):
                    restore_startup_exact_delta(name,foreign)
