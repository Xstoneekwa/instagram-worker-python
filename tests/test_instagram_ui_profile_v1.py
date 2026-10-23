"""Version fence exercised through the actual A0 runner/guard/resume code.

All device/database transports are offline doubles. No live authority, permit,
request, run or lease is created or consumed by this suite.
"""
import ast
import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
import device
import device_action_latch as latch
import instagram_ui_446_compat as compat
import instagram_ui_profile_v1 as ui
import runner
import supabase_client
import account_run_control
import account_session_resume_engine as plans
import account_session_resume_plan_store as store
import auto_restart_runtime as resume

P = 'com.instagram.android'
A, D, I, Q, ROOT_ID, L, RUN, PRIOR = [f'00000000-0000-4000-8000-{i:012d}' for i in range(1, 9)]


def metadata(version=446, package=P):
    name, code = ('446.0.0.49.77', 385211304) if version == 446 else ('372.0.0.48.60', 377611059)
    return ui.AppMetadata(package, name, code)


def dumpsys(version=446, package=P):
    m = metadata(version, package)
    return f'Packages:\n  Package [{package}] (123):\n    versionCode={m.version_code} minSdk=28\n    versionName={m.version_name}\n'


def binding(version=446, **changes):
    m = metadata(version)
    values = dict(account_id=A, request_id=Q, run_id=RUN, root_business_session_id=ROOT_ID,
                  attempt_id=1, device_id=D, serial='serial-a', app_instance_id=I, package=P,
                  version_name=m.version_name, version_code=m.version_code,
                  ui_profile=ui.resolve(m, P), lease_id=L)
    values.update(changes)
    return ui.RunUIBinding(**values)


class FakeDevice:
    def __init__(self):
        self.serial = 'serial-a'
        self.calls = []

    def click(self, *args, **kw):
        return self.jsonrpc_call('click', args)

    def jsonrpc_call(self, *args, **kw):
        self.calls.append(('rpc', args))
        return 'result'

    def shell(self, command, *args, **kw):
        self.calls.append(('shell', command))
        return 'result'

    def app_start(self, *args, **kw):
        self.calls.append(('start', args))

    def app_stop(self, *args, **kw):
        self.calls.append(('stop', args))

    def app_current(self):
        return dict(package=P, activity='activity')

    def dump_hierarchy(self):
        return '<hierarchy/>'


class Isolated(unittest.TestCase):
    def setUp(self):
        for target, kwargs in [(ui, {'_SESSION': None}),
                               (latch, {'_ENABLED': False, '_STOP_REQUESTED': False, '_ACTIVE_ACTIONS': 0}),
                               (config, {'INSTAGRAM_PACKAGE': P})]:
            for name, value in kwargs.items():
                p = patch.object(target, name, value)
                p.start(); self.addCleanup(p.stop)
        token = ui._DEPTH.set(0); self.addCleanup(ui._DEPTH.reset, token)
        p = patch.object(socket.socket, 'connect', side_effect=AssertionError('LIVE_NETWORK_FORBIDDEN'))
        p.start(); self.addCleanup(p.stop)

    def active(self, version=446):
        self.current = metadata(version)
        self.reads = 0
        def reader():
            self.reads += 1
            return self.current
        self.b = binding(version)
        ui.arm(self.b, reader)
        d = FakeDevice()
        ui.attach(d)
        latch.install_device_guard(d)
        return d


class MetadataTests(Isolated):
    def test_both_exact_builds(self):
        for version in (372, 446):
            self.assertEqual(ui.parse_installed_package(dumpsys(version), P), metadata(version))

    def test_first_foreign_section_never_selects_contract(self):
        raw = dumpsys(372, 'com.instagram.other') + dumpsys(446)
        self.assertEqual(ui.resolve(ui.parse_installed_package(raw, P), P), 'UI_PROFILE_446')

    def test_missing_ambiguous_unknown_rejected(self):
        for raw in ['', 'versionName=446.0.0.49.77\nversionCode=385211304', dumpsys()+dumpsys(),
                    dumpsys().replace('versionName=', 'missing='), dumpsys().replace('versionCode=', 'missing='),
                    dumpsys()+ ' versionName=446.0.0.49.77\n',
                    dumpsys().replace('385211304', '1'), dumpsys().replace('446.0.0.49.77', '447'),
                    dumpsys(372).replace('377611059', '385211304'), None]:
            with self.subTest(raw=str(raw)[:80]), self.assertRaises(ui.UIProfileStopped):
                ui.parse_installed_package(raw, P)

    def test_malformed_or_wrong_package(self):
        for pkg in ['', 'com.android.other', P+';echo unsafe', P+'\n', None]:
            with self.subTest(pkg=pkg), self.assertRaises(ui.UIProfileStopped):
                ui.parse_installed_package(dumpsys(), pkg)

    def test_bool_code_and_mismatched_profile_rejected(self):
        with self.assertRaises(ui.UIProfileStopped):
            ui.resolve(ui.AppMetadata(P, '446.0.0.49.77', True), P)
        with self.assertRaises(ui.UIProfileStopped):
            binding(ui_profile='UI_PROFILE_372')

    def test_existing_bounded_metadata_transport_once(self):
        with patch.object(device, '_adb_run', return_value=(0, dumpsys(), '')) as call:
            self.assertEqual(ui.read_metadata('serial-a', P), metadata())
        call.assert_called_once_with('serial-a', ['shell', 'dumpsys', 'package', P])

    def test_metadata_transport_failure_no_retry(self):
        for result in [(1, '', 'failure'), (0, '', '')]:
            with patch.object(device, '_adb_run', return_value=result) as call:
                with self.assertRaises(ui.UIProfileStopped):ui.read_metadata('serial-a', P)
                self.assertEqual(call.call_count, 1)


class SessionTests(Isolated):
    def test_frozen_binding_and_exact_schema(self):
        b = binding()
        with self.assertRaises(FrozenInstanceError):b.ui_profile = 'UI_PROFILE_372'
        self.assertEqual(ui.from_dict(b.to_dict()), b)
        for v in [None, {}, {**b.to_dict(), 'extra': 1}, {**b.to_dict(), 'attempt_id': True}]:
            with self.assertRaises(ui.UIProfileStopped):ui.from_dict(v)

    def test_one_selected_contract_for_both_versions(self):
        for version in (372, 446):
            with patch.object(ui, '_SESSION', None):
                d = self.active(version)
                d.click(1, 2)
                self.assertEqual(len(d.calls), 1)
                self.assertEqual(ui.snapshot()['ui_profile'], 'UI_PROFILE_'+str(version))
                self.assertEqual(self.reads, 2)  # arm + outer dispatch; nested RPC adds none

    def test_372_has_explicit_version_without_446_context(self):
        d = self.active(372)
        self.assertNotIn(compat.CONTEXT_KEY, vars(d))
        self.assertEqual(vars(d)['_instagram_search_observed_version'][3:], ('372.0.0.48.60', 377611059))

    def test_context_retention_reuses_original_object(self):
        d = self.active()
        context = vars(d)[compat.CONTEXT_KEY]
        compat.retain_context(d, serial=d.serial, package=P, version_name=self.b.version_name,
                              version_code=self.b.version_code, unsafe=False)
        self.assertIs(vars(d)[compat.CONTEXT_KEY], context)
        d.click(1, 2)

    def test_unknown_or_changed_version_latches_before_transport(self):
        d = self.active()
        self.current = metadata(372)
        with self.assertRaisesRegex(ui.UIProfileStopped, 'version_changed'):d.click(1, 2)
        self.current = metadata(446)
        with self.assertRaisesRegex(ui.UIProfileStopped, 'version_changed'):d.app_start(P)
        self.assertEqual(d.calls, [])

    def test_metadata_failure_cannot_be_caught_as_candidate_exception(self):
        d = self.active()
        ui._SESSION.reader = lambda: (_ for _ in ()).throw(TimeoutError('secret'))
        with self.assertRaises(ui.UIProfileStopped) as caught:d.click(1, 2)
        self.assertNotIsInstance(caught.exception, Exception)
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(d.calls, [])

    def test_removing_context_cannot_enable_legacy_fallback(self):
        d = self.active(); del vars(d)[compat.CONTEXT_KEY]
        with self.assertRaises(ui.UIProfileStopped):d.click(1, 2)
        self.assertEqual(d.calls, [])

    def test_replaced_observed_tuple_and_wrong_serial_rejected(self):
        for change in ('tuple', 'serial', 'context'):
            with patch.object(ui, '_SESSION', None):
                d = self.active()
                if change == 'tuple':vars(d)['_instagram_search_observed_version'] = None
                elif change == 'serial':d.serial = 'other-device'
                else:vars(d)[compat.CONTEXT_KEY] = replace(vars(d)[compat.CONTEXT_KEY])
                with self.assertRaises(ui.UIProfileStopped):d.click(1, 2)
                self.assertEqual(d.calls, [])

    def test_372_cannot_install_446_context(self):
        d = self.active(372)
        with self.assertRaises(ui.UIProfileStopped):
            compat.retain_context(d, serial=d.serial, package=P,
                                  version_name='446.0.0.49.77', version_code=385211304, unsafe=False)
        self.assertEqual(d.calls, [])

    def test_rebinding_same_process_rejected(self):
        self.active()
        with self.assertRaises(ui.UIProfileStopped):ui.arm(binding(372), lambda: metadata(372))
        with self.assertRaises(ui.UIProfileStopped):ui.begin_dispatch()

    def test_device_and_binding_replacement_rejected(self):
        for kind in ('binding', 'device'):
            with patch.object(ui, '_SESSION', None):
                d = self.active()
                if kind == 'binding':ui._SESSION.binding = replace(self.b)
                else:ui._SESSION.device = FakeDevice()
                with self.assertRaises(ui.UIProfileStopped):d.click(1, 2)
                self.assertEqual(d.calls, [])

    def test_changed_config_package_rejected(self):
        d = self.active()
        with patch.object(config, 'INSTAGRAM_PACKAGE', 'com.instagram.other'):
            with self.assertRaises(ui.UIProfileStopped):d.click(1, 2)
        self.assertEqual(d.calls, [])

    def test_stop_precedes_metadata_and_remains_available(self):
        d = self.active(); latch.configure(enabled=True, account_id=A, run_id=RUN)
        latch.request_stop(reason='operator_stop')
        reads = self.reads
        with self.assertRaises(latch.DeviceActionBlocked):d.click(1, 2)
        self.assertTrue(latch.stop_requested())
        self.assertEqual(self.reads, reads)
        self.assertEqual(d.calls, [])

    def test_shell_lifecycle_and_direct_rpc_fenced(self):
        for action in ('shell', 'rpc', 'start', 'stop'):
            with patch.object(ui, '_SESSION', None):
                d = self.active(); self.current = metadata(372)
                with self.assertRaises(ui.UIProfileStopped):
                    if action == 'shell':d.shell('am start '+P)
                    elif action == 'rpc':d.jsonrpc_call('setText', 'text')
                    elif action == 'start':d.app_start(P)
                    else:d.app_stop(P)
                self.assertEqual(d.calls, [])

    def lock_check(self, d, version, *, unsafe=False, read_error=False):
        state = SimpleNamespace(path='/fixture-only', read=lambda:{
            'authorized_version_name':self.b.version_name, 'authorized_version_code':self.b.version_code})
        with patch.object(device, '_fetch_package_version', return_value=version,
                          side_effect=RuntimeError('fixture read failed') if read_error else None), \
             patch.object(device, '_package_disabled_for_user0', return_value=True), \
             patch.object(device, '_package_suspended', return_value=unsafe), \
             patch.object(device, '_lock_state_store', return_value=state), patch.object(device, 'log'):
            return device.check_instagram_version_lock(d)

    def test_real_version_lock_accepts_same_bound_build_without_rebinding(self):
        for version in (372, 446):
            with patch.object(ui, '_SESSION', None):
                d = self.active(version); saved = ui.snapshot(); context = vars(d).get(compat.CONTEXT_KEY)
                result = self.lock_check(d, (self.b.version_name, self.b.version_code))
                self.assertFalse(result['unsafe_for_automation'])
                self.assertEqual(ui.snapshot(), saved)
                self.assertIs(vars(d).get(compat.CONTEXT_KEY), context)
                d.click(1, 2); self.assertEqual(len(d.calls), 1)

    def test_real_version_lock_failed_or_changed_observation_latches_no_ui(self):
        for version in (372, 446):
            for failure in ('changed', 'missing', 'unsafe', 'read_failure'):
                with self.subTest(version=version, failure=failure), patch.object(ui, '_SESSION', None):
                    d = self.active(version)
                    observed = (self.b.version_name, self.b.version_code)
                    if failure == 'changed':
                        other = metadata(372 if version == 446 else 446)
                        observed = (other.version_name, other.version_code)
                    elif failure == 'missing':observed = (None, None)
                    with self.assertRaises(ui.UIProfileStopped):
                        self.lock_check(d, observed, unsafe=failure == 'unsafe', read_error=failure == 'read_failure')
                    with self.assertRaises(ui.UIProfileStopped):d.click(1, 2)
                    self.assertEqual(d.calls, [])

    def test_adb_same_serial_bounded_revalidation_no_recursive_probe(self):
        d = self.active()
        count = 0
        def run(serial, argv):
            nonlocal count
            count += 1
            ui.check_adb(serial, argv)
            return 0, dumpsys(), ''
        with patch.object(device, '_adb_run', side_effect=run):
            ui._SESSION.reader = lambda:ui.read_metadata('serial-a', P)
            ui.check_adb('serial-a', ['shell', 'input', 'tap', '1', '2'])
        self.assertEqual(count, 1)
        with self.assertRaises(ui.UIProfileStopped):ui.check_adb('different', ['shell', 'input', 'tap'])
        self.assertEqual(d.calls, [])

    def test_raw_adb_mutations_use_same_fence(self):
        self.active(); self.current = metadata(372)
        for call in [lambda:device._adb_run('serial-a', ['shell', 'input', 'tap', '1', '2']),
                     lambda:device._adb_shell_stdin('serial-a', 'input text secret')]:
            with patch.object(device.subprocess, 'run', side_effect=AssertionError('TRANSPORT_MUST_NOT_RUN')) as transport:
                with self.assertRaises(ui.UIProfileStopped):call()
                transport.assert_not_called()

    def test_only_bound_device_can_use_guarded_reads(self):
        self.active()
        other = latch.install_device_guard(FakeDevice())
        with self.assertRaises(ui.UIProfileStopped):other.dump_hierarchy()

    def test_parent_profile_and_identity_all_preserved(self):
        old = binding(run_id=PRIOR)
        new = binding(attempt_id=2, request_id=ROOT_ID)
        self.assertEqual(ui.validate_parent(old.to_dict(), new, PRIOR), old)
        for field, value in [('serial', 'wrong'), ('account_id', Q), ('device_id', Q),
                             ('app_instance_id', Q), ('root_business_session_id', Q),
                             ('version_name', '372.0.0.48.60'), ('ui_profile', 'UI_PROFILE_372')]:
            bad = old.to_dict(); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ui.UIProfileStopped):
                ui.validate_parent(bad, new, PRIOR)

    def test_missing_parent_and_same_attempt_rejected(self):
        for parent in [None, {}, binding().to_dict()]:
            with self.assertRaises(ui.UIProfileStopped):ui.validate_parent(parent, binding(attempt_id=2), PRIOR)

    def test_plan_copy_rejects_replacement_and_null(self):
        original = {'quota_remaining': {'follow': 7}}
        result = ui.carry_plan(original, binding().to_dict())
        self.assertNotIn(ui.PLAN_KEY, original)
        self.assertEqual(result['quota_remaining'], original['quota_remaining'])
        with self.assertRaises(ui.UIProfileStopped):ui.carry_plan(result, binding(372).to_dict())
        with self.assertRaises(ui.UIProfileStopped):ui.carry_plan(original, None)


class RunnerTests(Isolated):
    def setUp(self):
        super().setUp()
        self.context = dict(device_id=D, app_instance_id=I, adb_serial='serial-a', package_name=P)
        self.args = SimpleNamespace(package_name=P, expected_app_instance_id=I, device_serial='serial-a')
        self.request = dict(id=Q, account_id=A, run_id=RUN, device_id=D, app_instance_id=I,
                            root_business_session_id=ROOT_ID, execution_attempt_no=1, retry_index=0, metadata_safe={})
        self.row = dict(id=RUN, account_id=A, status='running', updated_at='2026-10-23T00:00:00Z',
                        performance_summary={'existing_field': 'preserve'})
        self.lease = dict(device_id=D, request_id=Q, account_id=A, worker_id='worker', lease_id=L,
                          generation=L, lease_seconds=300)
        self.operations = []
        def load(rid, **kw):
            self.assertEqual(kw, {'request_timeout': 5.0, 'max_retries': 0})
            if rid == RUN:return copy.deepcopy(self.row)
            return copy.deepcopy(getattr(self, 'prior_row', {}))
        def patch_run(method, table, **kw):
            self.assertEqual((method, table), ('PATCH', 'ig_runs'))
            self.assertEqual(kw['max_retries'], 0)
            self.assertEqual(kw['request_timeout'], 5.0)
            self.assertEqual(set(kw['body']), {'performance_summary'})
            self.operations.append(copy.deepcopy(kw))
            self.row['performance_summary'] = copy.deepcopy(kw['body']['performance_summary'])
            return []
        for p in [patch.object(account_run_control, 'get_account_run_request', side_effect=lambda *a, **k:copy.deepcopy(self.request)),
                  patch.object(supabase_client, 'load_run_row', side_effect=load),
                  patch.object(supabase_client, '_request_json', side_effect=patch_run),
                  patch.object(ui, 'read_metadata', return_value=metadata()),
                  patch.dict(os.environ, FLEET_DEVICE_LOCK_BINDING_JSON=json.dumps(self.lease), AUTO_RESTART_RESUME_PLAN_V1='')]:
            p.start(); self.addCleanup(p.stop)

    def prepare(self, account_session=False):
        return runner._prepare_instagram_ui_profile(self.context, 'serial-a', A, RUN, Q,
                                                    args=self.args, account_session_run=account_session)

    def test_real_prepare_binds_exact_identity_and_preserves_summary(self):
        self.assertEqual(self.prepare(), binding())
        self.assertEqual(self.row['performance_summary']['existing_field'], 'preserve')
        self.assertEqual(self.row['performance_summary'][ui.PLAN_KEY], binding().to_dict())
        self.assertEqual(len(self.operations), 1)
        self.assertIn('updated_at', self.operations[0]['query'])
        self.assertTrue(ui.armed())

    def test_unknown_build_before_any_binding_write(self):
        with patch.object(ui, 'read_metadata', side_effect=ui.UIProfileStopped('unknown')):
            with self.assertRaises(ui.UIProfileStopped):self.prepare()
        self.assertFalse(ui.armed()); self.assertEqual(self.operations, [])

    def test_optional_update_lock_flags_cannot_disable_profile_selection(self):
        with patch.object(config, 'LOCK_INSTAGRAM_AUTO_UPDATE', False), patch.object(config, 'ENABLE_STRICT_UPDATE_LOCK', False):
            self.assertEqual(self.prepare().ui_profile, 'UI_PROFILE_446')
        self.assertTrue(ui.armed())

    def test_dispatcher_mismatch_rejected_before_metadata_or_write(self):
        for field, value in [('package_name', 'com.instagram.other'), ('expected_app_instance_id', Q), ('device_serial', 'other')]:
            old = getattr(self.args, field);setattr(self.args, field, value)
            with self.subTest(field=field), self.assertRaises(ui.UIProfileStopped):self.prepare()
            setattr(self.args, field, old)
        self.assertEqual(self.operations, [])

    def test_canonical_request_instance_and_lineage_are_required(self):
        for field, value in [('run_id', PRIOR), ('account_id', Q), ('device_id', Q),
                             ('app_instance_id', Q), ('root_business_session_id', None), ('execution_attempt_no', True)]:
            old = self.request[field];self.request[field] = value
            with self.subTest(field=field), self.assertRaises(ui.UIProfileStopped):self.prepare()
            self.request[field] = old
        self.assertEqual(self.operations, [])

    def test_lease_generation_mismatch_blocks(self):
        bad = dict(self.lease, generation=ROOT_ID)
        with patch.dict(os.environ, FLEET_DEVICE_LOCK_BINDING_JSON=json.dumps(bad)):
            with self.assertRaises(ui.UIProfileStopped):self.prepare()
        self.assertEqual(self.operations, [])

    def test_ambiguous_write_accepted_only_by_exact_readback(self):
        def lost(method, table, **kw):
            self.operations.append(kw)
            self.row['performance_summary'] = kw['body']['performance_summary']
            raise TimeoutError('secret')
        with patch.object(supabase_client, '_request_json', side_effect=lost):
            self.prepare()
        self.assertEqual(len(self.operations), 1)

    def test_lost_or_cas_refused_write_never_retried(self):
        with patch.object(supabase_client, '_request_json', side_effect=TimeoutError('secret')) as call:
            with self.assertRaisesRegex(ui.UIProfileStopped, 'persistence_unproved'):self.prepare()
        self.assertEqual(call.call_count, 1)
        self.assertFalse(ui.armed())

    def test_account_session_requires_actual_capsule_readback(self):
        with patch.object(store, 'bind_run_ui_profile', return_value={'persisted': False}) as write:
            with self.assertRaisesRegex(ui.UIProfileStopped, 'resume_binding'):self.prepare(account_session=True)
        self.assertEqual(write.call_count, 1)
        self.assertFalse(ui.armed())

    def test_resume_compares_canonical_parent_not_operator_json(self):
        old = binding(run_id=PRIOR)
        self.prior_row = dict(id=PRIOR, account_id=A, performance_summary={ui.PLAN_KEY: old.to_dict()})
        self.request.update(execution_attempt_no=2, retry_index=1,
                            metadata_safe={'prior_run_id': PRIOR})
        with patch.dict(os.environ, AUTO_RESTART_RESUME_PLAN_V1=json.dumps({ui.PLAN_KEY: old.to_dict()})):
            value = self.prepare()
        self.assertEqual(value.attempt_id, 2)
        self.assertEqual(value.ui_profile, old.ui_profile)
        self.assertEqual(ui.carry_plan({ui.PLAN_KEY: old.to_dict()})[ui.PLAN_KEY], value.to_dict())

    def test_manual_parent_json_cannot_supply_missing_canonical_binding(self):
        self.request.update(execution_attempt_no=2, retry_index=1, metadata_safe={'prior_run_id': PRIOR})
        with patch.dict(os.environ, AUTO_RESTART_RESUME_PLAN_V1=json.dumps({ui.PLAN_KEY: binding(run_id=PRIOR).to_dict()})):
            with self.assertRaises(ui.UIProfileStopped):self.prepare()
        self.assertEqual(self.operations, [])

    def test_replaced_parent_projection_and_profile_rejected(self):
        self.request.update(execution_attempt_no=2, retry_index=1, metadata_safe={'prior_run_id': PRIOR})
        self.prior_row = dict(id=PRIOR, account_id=A, performance_summary={ui.PLAN_KEY: binding(372, run_id=PRIOR).to_dict()})
        with self.assertRaises(ui.UIProfileStopped):self.prepare()
        self.assertEqual(self.operations, [])

    def test_real_runner_order_and_no_optional_selection_flag(self):
        tree = ast.parse(Path(runner.__file__).read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_main_impl')
        calls = [(n.lineno, ast.unparse(n.func)) for n in ast.walk(main) if isinstance(n, ast.Call)]
        prepare = next(line for line, name in calls if name == '_prepare_instagram_ui_profile')
        begin = next(line for line, name in calls if name == 'supabase_client.begin_device_activity_v1')
        connect = next(line for line, name in calls if name == 'connect_device')
        self.assertLess(begin, prepare);self.assertLess(prepare, connect)
        statement = next(n for n in main.body if isinstance(n, ast.Expr)
                         and isinstance(n.value, ast.Call) and ast.unparse(n.value.func) == '_prepare_instagram_ui_profile')
        self.assertIsInstance(statement, ast.Expr)  # unconditional, outside LOCK flags

    def test_profile_exception_runs_only_terminal_handler(self):
        with patch.object(runner, '_main_impl', side_effect=ui.UIProfileStopped('instagram_version_changed_during_run')), \
             patch.object(runner, '_CURRENT_RUN_ID', RUN), \
             patch.object(runner, '_update_run_status_safe', return_value='failed') as terminal, \
             patch.object(runner, '_cleanup_session_apps', side_effect=AssertionError('NO_UI_CLEANUP')):
            self.assertEqual(runner.main(), 1)
        self.assertEqual(terminal.call_args.kwargs['performance_summary']['root_failure_code'], 'instagram_version_changed_during_run')


class PlanStoreTests(Isolated):
    def setUp(self):
        super().setUp()
        self.b = binding()
        self.row = dict(run_id=RUN, run_request_id=Q, account_id=A, device_id=D, app_instance_id=I,
                        expected_package=P, last_updated_at='2026-10-23T00:00:00Z',
                        plan={'root_business_session_id': ROOT_ID, 'execution_attempt_no': 1, 'quota': 3})
        self.writes = []
        def write(method, table, **kw):
            self.assertEqual(method, 'PATCH')
            self.assertEqual(table, 'account_session_resume_plans')
            self.assertEqual(kw['max_retries'], 0)
            self.assertEqual(kw['query']['last_updated_at'], 'eq.2026-10-23T00:00:00Z')
            self.writes.append(kw)
            self.row.update(copy.deepcopy(kw['body']))
            return [copy.deepcopy(self.row)]
        for p in [patch.object(store, 'load_resume_plan', side_effect=lambda **kw:copy.deepcopy(self.row)),
                  patch.object(supabase_client, '_request_json', side_effect=write)]:
            p.start();self.addCleanup(p.stop)

    def test_existing_json_binding_exact_readback_and_idempotency(self):
        self.assertTrue(store.bind_run_ui_profile(run_id=RUN, binding=self.b.to_dict())['persisted'])
        self.assertEqual(self.row['plan']['quota'], 3)
        self.assertEqual(self.row['plan'][ui.PLAN_KEY], self.b.to_dict())
        self.assertTrue(store.bind_run_ui_profile(run_id=RUN, binding=self.b.to_dict())['persisted'])
        self.assertEqual(len(self.writes), 1)

    def test_other_account_instance_or_plan_cannot_be_rebound(self):
        for field in ('account_id', 'device_id', 'app_instance_id', 'expected_package', 'run_request_id'):
            old = self.row[field]; self.row[field] = 'different'
            with self.subTest(field=field):self.assertFalse(store.bind_run_ui_profile(run_id=RUN, binding=self.b.to_dict())['persisted'])
            self.row[field] = old
        self.assertEqual(self.writes, [])

    def test_competing_binding_is_not_overwritten(self):
        self.row['plan'][ui.PLAN_KEY] = binding(372).to_dict()
        self.assertFalse(store.bind_run_ui_profile(run_id=RUN, binding=self.b.to_dict())['persisted'])
        self.assertEqual(self.writes, [])

    def test_cas_loss_stops_without_overwrite(self):
        with patch.object(supabase_client, '_request_json', return_value=[]) as write:
            self.assertFalse(store.bind_run_ui_profile(run_id=RUN, binding=self.b.to_dict())['persisted'])
            self.assertEqual(write.call_count, 1)

    def test_terminal_merge_rejects_binding_change_before_rpc(self):
        self.row['plan'][ui.PLAN_KEY] = self.b.to_dict()
        with patch.object(supabase_client, 'persist_account_session_resume_plan_v1', side_effect=AssertionError('NO_RPC')):
            result = store.record_end_of_session(run_id=RUN, session_plan={ui.PLAN_KEY: binding(372).to_dict()}, session_status='failed')
        self.assertFalse(result['persisted'])
        self.assertEqual(result['error'], 'ui_profile_binding_changed')


class ResumeProjectionTests(Isolated):
    def test_builder_adds_only_exact_binding(self):
        summary = dict(account_id=A, business_session_id=ROOT_ID, run_id=RUN, attempt_id=1,
                       session_status="failed", session_termination_class="partial_resumable")
        expected = plans.build_account_session_resume_plan(summary)
        actual = plans.build_account_session_resume_plan({**summary, ui.PLAN_KEY: binding().to_dict()})
        self.assertEqual(actual.pop(ui.PLAN_KEY), binding().to_dict())
        self.assertEqual(actual, expected)

    def test_human_policy_uses_durable_binding_and_env_is_lossless(self):
        from tests.test_human_confirmed_resume_claim import _metadata, _plan_row, ACCOUNT_ID, ORIGINAL_RUN_ID, ROOT_BUSINESS_SESSION_ID
        old = binding(account_id=ACCOUNT_ID, run_id=ORIGINAL_RUN_ID, root_business_session_id=ROOT_BUSINESS_SESSION_ID).to_dict()
        row = _plan_row(); row["plan"][ui.PLAN_KEY] = old
        meta = _metadata(); meta["resume_plan"][ui.PLAN_KEY] = binding(372).to_dict()
        with patch.object(store, "load_resume_plan", return_value=row):
            ok, reason, policy = resume.validate_auto_restart_request_at_claim(account_id=ACCOUNT_ID, metadata=meta)
        self.assertTrue(ok, reason)
        self.assertEqual(policy[ui.PLAN_KEY], old)
        with patch.dict(os.environ, resume.runner_env_for_resume_policy(policy)):
            self.assertEqual(resume.load_resume_policy_from_env()[ui.PLAN_KEY], old)
        self.assertEqual(policy["quota_remaining"]["follow"], 10)

    def test_natural_policy_keeps_canonical_not_embedded_binding(self):
        old = binding(run_id=PRIOR).to_dict()
        embedded = {"schema": "AUTO_RESTART_RESUME_PLAN_V2", "account_id": A,
                    "attempt_id": 2, "restart_allowed": True, "package_contract_ready": True,
                    "phase_plan_source": "follow60_armed_control",
                    "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
                    "quota_remaining": {"follow": 10, "welcome": 0, "unfollow": 0},
                    "follow_60s_canary_contract": {"schema": "FOLLOW_60S_ONE_SHOT_V2", "control_id": Q,
                       "source_run_id": PRIOR, "follow_quota": 10, "expires_at": "2099-01-01T00:00:00+00:00",
                       "golden_fallback_policy": "proof_rejection_only"}}
        summary = dict(account_id=A, account_username="fixture", run_id=PRIOR, session_termination_class="",
                       restart_eligibility="eligible", auto_restart_resume_plan={**embedded, ui.PLAN_KEY: old})
        meta = dict(auto_restart=True, source="auto_restart_tick", attempt_id=2, retry_index=1,
                    resume_plan_version=2, resume_plan_schema="AUTO_RESTART_RESUME_PLAN_V2",
                    prior_run_id=PRIOR, resume_plan={**embedded, ui.PLAN_KEY: binding(372).to_dict()})
        row = dict(id=Q, account_id=A, root_business_session_id=ROOT_ID, execution_attempt_no=2, retry_index=1)
        with patch.object(resume, "load_prior_run_summary", return_value=summary):
            ok, reason, policy = resume.validate_claimed_auto_restart_request(account_id=A, metadata=meta, claimed_request=row)
        self.assertTrue(ok, reason)
        self.assertEqual(policy[ui.PLAN_KEY], old)
        self.assertEqual(policy["attempt_id"], 2)
        self.assertEqual(policy["quota_remaining"]["follow"], 10)


class PreservationTests(Isolated):
    def test_all_source_changes_stay_in_owner_scope(self):
        root = Path(runner.__file__).parent
        expected = {'account_session_orchestrator.py', 'account_session_resume_engine.py',
                    'account_session_resume_plan_store.py', 'auto_restart_runtime.py', 'device.py',
                    'device_action_latch.py', 'instagram_ui_446_compat.py', 'instagram_ui_profile_v1.py',
                    'runner.py', 'tests/test_certification_admission.py',
                    'tests/test_instagram_ui_446_compat.py', 'tests/test_instagram_ui_profile_v1.py',
                    'scripts/test-incident-system-global-resolution-v2.py',
                    'scripts/test-restriction-resume-natural-tick-v1.py',
                    'tests/test_dm_446_anticipatory_composer_postsend.py',
                    'tests/test_outreach_runner_mainline_routing.py',
                    'tests/test_runner_device_serial.py',
                    'tests/test_runner_package_dispatch.py',
                    }
        # Exact canonical baseline, independent of staged/unstaged state.
        rows = subprocess.check_output(['git', '-C', str(root), 'diff', '--name-only',
                                       'baf0bf85701e0c28e65ca8ebe71b9537e7293b73'], text=True).splitlines()
        extra = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--others', '--exclude-standard'], text=True).splitlines()
        self.assertLessEqual(set(rows + extra), expected)

    def test_navigation_sender_truth_and_business_primitives_unchanged(self):
        root = Path(runner.__file__).parent
        for name in ['instagram_navigation.py', 'dm_sender_engine.py', 'dm_thread_truth.py',
                     'follow60_mainline_v2.py', 'follow_action_engine.py', 'unfollow_profile_probe.py',
                     'outreach_session_orchestrator.py', 'account_run_request_consumer.py']:
            old = subprocess.check_output(['git', '-C', str(root), 'show',
                                          'baf0bf85701e0c28e65ca8ebe71b9537e7293b73:'+name])
            self.assertEqual((root/name).read_bytes(), old, name)


if __name__ == '__main__':unittest.main()
