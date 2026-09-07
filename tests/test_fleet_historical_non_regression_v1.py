"""Primitive-level invariants only; not a historical runner/dispatcher certificate."""
import unittest
from fleet_runtime_barrier import FleetBlocked, FleetRuntimeBarrier


class HistoricalOverlayTests(unittest.TestCase):
    def test_release_preserves_independent_blockers(self):
        for reason in ('incident', 'manual_only', 'identity_guard', 'operator_canceled'):
            with self.subTest(reason=reason):
                b = FleetRuntimeBarrier('tenant', reason, lambda snapshot: None)
                b.register_owner('owner')
                b.apply('hold', expected_revision=0, operation='hold')
                b.apply('release', expected_revision=1, operation='release')
                b.acknowledge('owner', 2)
                with self.assertRaises(FleetBlocked):
                    b.register_attempt('attempt', 'owner', 2, historical_allowed=False)
                self.assertEqual(b.snapshot()['attempts'], {})

    def test_request_sources_are_equally_fenced(self):
        for origin in ('Play', 'Auto Restart', 'scheduler', 'after_request_creation', 'after_claim'):
            with self.subTest(origin=origin):
                b = FleetRuntimeBarrier('tenant', origin, lambda snapshot: None)
                b.register_owner('owner'); b.apply('hold', expected_revision=0, operation='hold')
                with self.assertRaises(FleetBlocked): b.register_attempt('attempt', 'owner', 1)

    def test_account_isolation(self):
        one = FleetRuntimeBarrier('tenant', 'one', lambda snapshot: None)
        two = FleetRuntimeBarrier('tenant', 'two', lambda snapshot: None)
        one.register_owner('same-device'); two.register_owner('same-device')
        one.apply('hold', expected_revision=0, operation='hold')
        two.register_attempt('second-account', 'same-device', 0)
        self.assertEqual(two.snapshot()['hold'], 'CLEARED')

    def test_no_business_fields_are_written(self):
        saved = []
        b = FleetRuntimeBarrier('tenant', 'account', saved.append)
        b.register_owner('owner'); b.apply('cap', expected_revision=0, operation='cap', cap=60, package_max=120)
        for snapshot in saved:
            self.assertTrue(set(snapshot).isdisjoint({
                'package', 'warmup_started_at', 'manual_override', 'configured_limit',
                'lifecycle', 'scheduler', 'auto_restart', 'operator_canceled'}))



class FleetCancellationIsolationTests(unittest.TestCase):
    """Real finalizer, inert persistence adapters. Not a Backend E2E claim."""

    @classmethod
    def setUpClass(cls):
        import ast
        from pathlib import Path
        source = ast.parse((Path(__file__).resolve().parents[1] / 'account_run_request_consumer.py').read_text(encoding='utf-8'))
        nodes = [n for n in source.body if isinstance(n, ast.FunctionDef)
                 and n.name == '_finalize_manual_run_after_subprocess']
        if len(nodes) != 1:
            raise AssertionError('finalizer extraction ambiguous')
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), nodes[0]], type_ignores=[])
        cls.compiled = compile(ast.fix_missing_locations(module), '<real-finalizer-fixture>', 'exec')

    def setUp(self):
        import copy
        from types import SimpleNamespace
        self.trace = []
        self.business = {'incident': {'id': 'incident-1', 'status': 'open', 'origin': 'independent'},
                         'manual_only': True, 'identity_guard': 'blocked', 'restriction': 'active'}
        self.before = copy.deepcopy(self.business)
        self.binding = dict(account_id='account', request_id='request', attempt_id='run',
                            consumer_instance_id='worker', consumer_boot_id='boot')
        self.receipt = dict(self.binding, revision=1, termination_source='fleet_control', termination_reason='fleet_hold')
        self.row = dict(id='request', account_id='account', run_id='run', requested_run_type='account_session',
                        cancel_requested_at='fixture-time', cancel_reason='fleet_hold',
                        metadata_safe={'fleet_cancellation_v1': self.receipt})
        self.group_absent = True
        self.persist_ok = True
        self.stale_ack = False
        self.terminal = None
        self.hold = 'REQUESTED'
        def reconcile():
            if not self.group_absent:
                raise FleetBlocked('process_group_still_alive')
            if not self.persist_ok:
                raise FleetBlocked('canonical_terminal_not_proven')
            self.terminal = 'fleet_hold'
            self.trace.append('fleet_persisted')
            if self.stale_ack:
                raise FleetBlocked('invalid_or_stale_backend_ack')
            self.trace.append('fleet_ack')
            self.hold = 'ACTIVE'
        self.session = SimpleNamespace(binding=self.binding, reconcile=reconcile)
        def operator(**kw):
            self.trace.append('operator_override')
            self.business['incident']['status'] = 'resolved'
            return {'ok': True}
        self.scope = dict(get_account_run_request=lambda *a, **k: self.row,
                          PRE_DEVICE_SAFE_STOP_EXIT_CODE=92,
                          _FLEET_EXECUTIONS={'request': self.session},
                          _audit=lambda **k: self.trace.append(k['action_type']),
                          _safe_complete_account_run_request=lambda *a, **k: self.trace.append('legacy_request_terminal'),
                          _reconcile_linked_run=lambda **k: self.trace.append('legacy_run_terminal'),
                          supabase_client=SimpleNamespace(reconcile_operator_canceled_run_v1=operator),
                          log=lambda *a, **k: None)
        exec(self.compiled, self.scope)
        self.cfg = SimpleNamespace(worker_id='worker')

    def invoke(self, **kwargs):
        return self.scope['_finalize_manual_run_after_subprocess'](
            self.cfg, request_id='request', account_id='account', exit_code=0, **kwargs)

    def test_fleet_preserves_run_created_incident(self):
        self.business['incident']['origin'] = 'run_created'
        self.invoke()
        self.assertEqual(self.business['incident']['status'], 'open')
        self.assertEqual(self.terminal, 'fleet_hold')
        self.assertEqual(self.trace[:2], ['fleet_persisted', 'fleet_ack'])
        self.assertNotIn('operator_override', self.trace)

    def test_fleet_preserves_independent_incident_and_blockers(self):
        self.invoke()
        self.assertEqual(self.business, self.before)
        self.assertEqual(self.hold, 'ACTIVE')

    def test_duplicate_cancellation_keeps_same_terminal_and_business_state(self):
        self.invoke(); self.invoke()
        self.assertEqual(self.terminal, 'fleet_hold')
        self.assertEqual(self.business, self.before)
        self.assertNotIn('operator_override', self.trace)

    def test_live_process_group_prevents_terminal_and_ack(self):
        self.group_absent = False
        with self.assertRaisesRegex(FleetBlocked, 'process_group_still_alive'): self.invoke()
        self.assertIsNone(self.terminal)
        self.assertEqual(self.trace, [])

    def test_failed_persistence_prevents_ack(self):
        self.persist_ok = False
        with self.assertRaisesRegex(FleetBlocked, 'canonical_terminal_not_proven'): self.invoke()
        self.assertEqual(self.trace, [])

    def test_stale_ack_does_not_fall_through_to_operator_override(self):
        self.stale_ack = True
        with self.assertRaisesRegex(FleetBlocked, 'invalid_or_stale_backend_ack'): self.invoke()
        self.assertEqual(self.trace, ['fleet_persisted'])
        self.assertEqual(self.business, self.before)
        self.assertEqual(self.hold, 'REQUESTED')

    def test_missing_receipt_fails_closed(self):
        self.row['metadata_safe'] = {}
        with self.assertRaisesRegex(FleetBlocked, 'provenance_unproven'): self.invoke()
        self.assertEqual(self.trace, [])

    def test_wrong_owner_fails_closed(self):
        self.receipt['consumer_boot_id'] = 'other-boot'
        with self.assertRaisesRegex(FleetBlocked, 'owner_unproven'): self.invoke()
        self.assertEqual(self.trace, [])

    def test_snapshot_cannot_replace_backend_provenance(self):
        self.scope['get_account_run_request'] = lambda *a, **k: None
        with self.assertRaisesRegex(FleetBlocked, 'provenance_unproven'):
            self.invoke(request_snapshot=self.row)
        self.assertEqual(self.trace, [])

    def test_restart_requires_recovered_owner_before_terminalization(self):
        self.scope['_FLEET_EXECUTIONS'] = {}
        with self.assertRaisesRegex(FleetBlocked, 'owner_unproven'): self.invoke()
        self.assertEqual(self.trace, [])
        self.scope['_FLEET_EXECUTIONS']['request'] = self.session
        self.invoke()
        self.assertEqual(self.terminal, 'fleet_hold')
        self.assertNotIn('operator_override', self.trace)

    def test_fleet_provenance_precedes_watchdog_terminal_branch(self):
        self.invoke(timed_out=True)
        self.assertEqual(self.terminal, 'fleet_hold')
        self.assertEqual(self.business, self.before)

    def test_individual_stop_keeps_operator_override(self):
        import types
        from unittest.mock import patch
        self.row['cancel_reason'] = 'manual_stop'
        self.row['metadata_safe'] = {}
        self.scope['_FLEET_EXECUTIONS'] = {}
        store = types.ModuleType('account_session_resume_plan_store')
        plans = []
        store.record_end_of_session = lambda **kw: plans.append(kw)
        with patch.dict('sys.modules', {store.__name__: store}): self.invoke()
        self.assertEqual(plans[0]['session_plan'], {'restart_allowed': False,
            'restart_block_reason': 'operator_canceled', 'terminal_reason_code': 'operator_canceled'})
        self.assertIn('operator_override', self.trace)
        self.assertEqual(self.business['incident']['status'], 'resolved')
        self.assertNotIn('fleet_ack', self.trace)

if __name__ == '__main__': unittest.main()
