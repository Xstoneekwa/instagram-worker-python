import copy
import threading
import unittest
from types import SimpleNamespace
from fleet_runtime_barrier import FleetBlocked, FleetRuntimeBarrier


class RuntimeBarrierTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.b = FleetRuntimeBarrier('tenant', 'account', self.saved.append)
        self.b.register_owner('consumer')

    def hold(self):
        return self.b.apply('hold', expected_revision=0, operation='hold')

    def attempt(self):
        self.b.register_attempt('attempt', 'consumer', 0)

    def test_hold_before_admission(self):
        self.hold()
        with self.assertRaises(FleetBlocked): self.attempt()

    def test_hold_after_admission(self):
        self.attempt(); self.hold()
        with self.assertRaises(FleetBlocked): self.b.spawn('attempt', lambda: self.fail('spawned'))

    def test_no_active_before_terminal_proof(self):
        self.attempt(); self.hold()
        with self.assertRaises(FleetBlocked): self.b.acknowledge('consumer', 1)
        self.assertEqual(self.b.snapshot()['hold'], 'REQUESTED')

    def test_spawn_wins_deterministic_race(self):
        self.attempt()
        entered = threading.Event(); release = threading.Event(); requested = threading.Event()
        errors = []
        def child():
            entered.set()
            if not release.wait(5): raise RuntimeError('test barrier timeout')
            return SimpleNamespace(pid=123)
        def spawn():
            try: self.b.spawn('attempt', child)
            except BaseException as exc: errors.append(exc)
        def hold():
            requested.set()
            try: self.hold()
            except BaseException as exc: errors.append(exc)
        one = threading.Thread(target=spawn); one.start(); self.assertTrue(entered.wait(5))
        two = threading.Thread(target=hold); two.start(); self.assertTrue(requested.wait(5))
        release.set(); one.join(5); two.join(5)
        self.assertFalse(one.is_alive() or two.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(self.b.snapshot()['attempts']['attempt']['state'], 'STARTED')
        with self.assertRaises(FleetBlocked): self.b.acknowledge('consumer', 1)

    def test_close_wins(self):
        self.attempt(); self.hold()
        with self.assertRaises(FleetBlocked): self.b.spawn('attempt', lambda: None)

    def test_restart_orphan_cannot_ack(self):
        self.attempt()
        with self.assertRaises(RuntimeError):
            self.b.spawn('attempt', lambda: (_ for _ in ()).throw(RuntimeError('crash')))
        restored = FleetRuntimeBarrier('tenant', 'account', self.saved.append, self.saved[-1])
        restored.apply('hold', expected_revision=0, operation='hold')
        with self.assertRaises(FleetBlocked): restored.acknowledge('consumer', 1)

    def test_terminal_proof_then_ack(self):
        self.attempt(); self.hold()
        self.b.reconcile_terminal('attempt', 'consumer', canonical_terminal=True, child_gone=True)
        self.assertEqual(self.b.acknowledge('consumer', 1), 'ACTIVE')

    def test_incomplete_terminal_proof(self):
        self.attempt()
        with self.assertRaises(FleetBlocked):
            self.b.reconcile_terminal('attempt', 'consumer', canonical_terminal=True, child_gone=False)

    def test_stale_ack(self):
        self.hold()
        with self.assertRaises(FleetBlocked): self.b.acknowledge('consumer', 0)

    def test_release_during_requested(self):
        self.hold(); self.b.apply('release', expected_revision=1, operation='release')
        with self.assertRaises(FleetBlocked): self.b.acknowledge('consumer', 1)
        self.assertEqual(self.b.acknowledge('consumer', 2), 'CLEARED')
        self.assertEqual(self.b.snapshot()['attempts'], {})

    def test_duplicate_idempotency(self):
        first = self.hold(); self.assertEqual(first, self.hold())
        self.assertEqual(self.b.snapshot()['revision'], 1)

    def test_key_conflict(self):
        self.hold()
        with self.assertRaises(FleetBlocked): self.b.apply('hold', expected_revision=1, operation='release')

    def test_owner_offline(self):
        self.b.register_owner('offline'); self.hold()
        self.assertEqual(self.b.acknowledge('consumer', 1), 'REQUESTED')

    def test_durable_adapter_failure_fails_closed(self):
        def fail(_): raise OSError('storage unavailable')
        self.b._persist = fail
        with self.assertRaises(OSError): self.hold()
        with self.assertRaises(FleetBlocked): self.attempt()

    def test_scope_restore_refused(self):
        with self.assertRaises(FleetBlocked):
            FleetRuntimeBarrier('other-tenant', 'account', self.saved.append, self.b.snapshot())

    def test_snapshot_cannot_mutate_authority(self):
        snapshot = self.b.snapshot(); snapshot['revision'] = 99
        self.assertEqual(self.b.snapshot()['revision'], 0)

    def test_unregistered_attempt_cannot_spawn(self):
        with self.assertRaises(FleetBlocked): self.b.spawn('missing', lambda: None)

    def test_cross_owner_reconciliation_refused(self):
        self.attempt()
        with self.assertRaises(FleetBlocked):
            self.b.reconcile_terminal('attempt', 'other', canonical_terminal=True, child_gone=True)



class RevisionAckInvalidationTests(unittest.TestCase):
    def active(self, owners=('one',), initial_cap=None):
        self.saved = []
        b = FleetRuntimeBarrier('tenant', 'account', self.saved.append)
        for owner in owners:
            b.register_owner(owner)
        revision = 0
        if initial_cap is not None:
            b.apply('initial-cap', expected_revision=revision, operation='cap',
                    cap=initial_cap, package_max=120)
            revision += 1
        b.apply('hold', expected_revision=revision, operation='hold')
        revision += 1
        for owner in owners:
            b.acknowledge(owner, revision)
        self.assertEqual(b.snapshot()['hold'], 'ACTIVE')
        self.assertEqual(b.snapshot()['active_revision'], revision)
        return b, revision

    def change(self, b, revision, operation='cap', cap=60):
        kwargs = dict(cap=cap, package_max=120) if operation == 'cap' else {}
        b.apply('change', expected_revision=revision, operation=operation, **kwargs)
        state = b.snapshot()
        self.assertEqual(state['revision'], revision + 1)
        self.assertEqual(state['hold'], 'REQUESTED')
        self.assertIsNone(state['active_revision'])
        self.assertTrue(all(ack == -1 for ack in state['owners'].values()))
        for owner in state['owners']:
            with self.assertRaises(FleetBlocked):
                b.register_attempt('forbidden-start', owner, revision + 1)
        return revision + 1

    def test_exact_revision_one_to_two(self):
        b, revision = self.active()
        self.assertEqual(revision, 1)
        self.assertEqual(b.snapshot()['owners']['one'], 1)
        current = self.change(b, revision)
        self.assertEqual(current, 2)
        self.assertEqual(b.acknowledge('one', current), 'ACTIVE')
        self.assertEqual(b.snapshot()['active_revision'], 2)

    def test_cap_clear_requires_new_ack(self):
        b, revision = self.active(initial_cap=60)
        current = self.change(b, revision, operation='clear_cap')
        self.assertIsNone(b.snapshot()['cap'])
        self.assertEqual(b.acknowledge('one', current), 'ACTIVE')
        self.assertEqual(b.snapshot()['active_revision'], current)

    def test_cap_increase_requires_new_ack(self):
        b, revision = self.active(initial_cap=40)
        current = self.change(b, revision, cap=80)
        self.assertEqual(b.acknowledge('one', current), 'ACTIVE')
        self.assertEqual(b.snapshot()['active_revision'], current)

    def test_cap_decrease_requires_new_ack(self):
        b, revision = self.active(initial_cap=80)
        current = self.change(b, revision, cap=40)
        self.assertEqual(b.acknowledge('one', current), 'ACTIVE')
        self.assertEqual(b.snapshot()['active_revision'], current)

    def test_delayed_stale_ack_cannot_reactivate(self):
        b, revision = self.active()
        self.change(b, revision)
        before = b.snapshot()
        with self.assertRaises(FleetBlocked):
            b.acknowledge('one', revision)
        self.assertEqual(before, b.snapshot())

    def test_partial_owner_ack_is_not_active(self):
        b, revision = self.active(('one', 'two'))
        current = self.change(b, revision)
        self.assertEqual(b.acknowledge('one', current), 'REQUESTED')
        self.assertIsNone(b.snapshot()['active_revision'])
        with self.assertRaises(FleetBlocked):
            b.register_attempt('forbidden-start', 'one', current)

    def test_all_current_owners_required(self):
        b, revision = self.active(('one', 'two'))
        current = self.change(b, revision)
        b.acknowledge('one', current)
        with self.assertRaises(FleetBlocked):
            b.acknowledge('two', revision)
        self.assertEqual(b.acknowledge('two', current), 'ACTIVE')
        self.assertEqual(b.snapshot()['active_revision'], current)
        self.assertTrue(all(ack == current for ack in b.snapshot()['owners'].values()))

    def test_duplicate_cap_does_not_invalidate_again(self):
        b, revision = self.active()
        current = self.change(b, revision)
        b.acknowledge('one', current)
        before = b.snapshot()
        b.apply('change', expected_revision=revision, operation='cap', cap=60, package_max=120)
        self.assertEqual(before, b.snapshot())

    def test_new_owner_invalidates_active_revision(self):
        b, revision = self.active()
        b.register_owner('new-owner')
        self.assertEqual(b.snapshot()['hold'], 'REQUESTED')
        self.assertIsNone(b.snapshot()['active_revision'])
        self.assertEqual(b.acknowledge('new-owner', revision), 'ACTIVE')
        self.assertEqual(b.snapshot()['active_revision'], revision)



class ConsumerEnrollmentIntegrationTests(unittest.TestCase):
    def setUp(self):
        import ast
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        self.temp = tempfile.TemporaryDirectory(prefix='fleet-integration-', dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        source = (Path(__file__).resolve().parents[1] / 'account_run_request_consumer.py').read_text()
        tree = ast.parse(source)
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith('_fleet_')]
        self.rpc_calls = []
        self.spawn_calls = []
        self.binding = dict(account_id='account', request_id='request', attempt_id='attempt',
                            consumer_instance_id='consumer', consumer_boot_id='current-boot')
        self.frame = dict(self.binding, schema='FLEET_EXECUTION_AUTHORITY_V1', process_generation='a' * 32, tenant_id='tenant',
                          revision=0, attempt_revision=0, execution_registered=True,
                          normal_effective_day_cap=120, canonical_follows_before_run=0,
                          emergency_cap=60, business_day='2026-09-06', hold='CLEARED')
        def rpc(operation, payload):
            self.rpc_calls.append((operation, copy.deepcopy(payload)))
            authority = dict(self.frame, consumer_boot_id=payload['consumer_boot_id'])
            return dict(payload, ok=True, authority=authority)
        def popen(*args, **kwargs):
            result = SimpleNamespace(pid=123)
            self.spawn_calls.append((args, kwargs, result))
            return result
        self.ns = dict(_FLEET_EXECUTIONS={}, _FLEET_CONSUMER_BOOT_ID='current-boot',
                       supabase_client=SimpleNamespace(fleet_execution_rpc_v1=rpc),
                       runtime_artifact_path=lambda *args: Path(self.temp.name).resolve(),
                       subprocess=SimpleNamespace(Popen=popen))
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<actual-consumer-helpers>', 'exec'), self.ns)

    def test_no_policy_exact_spawn_and_zero_rpc(self):
        self.assertIsNone(self.ns['_fleet_bind_authority']('request','account','consumer',{}))
        env = {'HISTORICAL': 'unchanged'}
        child = self.ns['_fleet_spawn_or_legacy']('request', ['historical-runner'], '/fixture', env, launch_binding=self.binding)
        self.assertEqual(self.rpc_calls, [])
        self.assertEqual(self.spawn_calls[0][0], (['historical-runner'],))
        self.assertEqual(self.spawn_calls[0][1], dict(cwd='/fixture', env=env, start_new_session=True))
        self.assertIs(child, self.spawn_calls[0][2])

    def test_initial_boot_is_transmitted_before_authoritative_binding(self):
        # The Backend cannot know a newly generated consumer boot UUID until
        # the registration request transmits it. This is an unbound claim,
        # not permission to reuse an old registered execution owner.
        unbound = dict(self.frame, consumer_boot_id=None, execution_registered=False)
        session = self.ns['_fleet_bind_authority']('request','account','consumer',
                                                   {'fleet_runtime_v1':unbound})
        self.assertEqual(self.rpc_calls[0][0], 'register')
        self.assertEqual(self.rpc_calls[0][1]['consumer_boot_id'], 'current-boot')
        self.assertEqual(session.binding['consumer_boot_id'], 'current-boot')



class ConsumerBootRegistrationContractTests(unittest.TestCase):
    """Real consumer helpers against a guarded, test-only registration service.

    These tests do not certify a deployed Backend RPC implementation.
    """
    def setUp(self):
        import ast
        import copy
        import pathlib
        import tempfile
        import types
        self.copy = copy.deepcopy
        # This fixture uses synthetic PID 123, never a host process. Group tests
        # below disable this probe fixture and use actual POSIX process groups.
        from unittest.mock import patch
        self.group_probe_patch = patch('fleet_runtime_barrier.fleet_process_group_absent', return_value=True)
        self.group_probe_patch.start()
        self.addCleanup(self.group_probe_patch.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix='fleet-boot-registration-')
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(__file__).resolve().parents[1]
        module = ast.parse((root / 'account_run_request_consumer.py').read_text())
        selected = [node for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name.startswith('_fleet_')]
        self.calls = []
        self.spawns = []
        self.active = None
        self.retired = set()
        self.revision = 0
        self.allowed_boots = {'B1'}
        self.response_mutator = None
        self.frame = {
            'schema': 'FLEET_EXECUTION_AUTHORITY_V1',
            'process_generation': 'a' * 32,
            'tenant_id': 'fixture-tenant', 'account_id': 'fixture-account',
            'request_id': 'fixture-request', 'attempt_id': 'fixture-attempt',
            'consumer_instance_id': 'A', 'consumer_boot_id': None,
            'execution_registered': False,
            'revision': 0, 'attempt_revision': 0,
            'normal_effective_day_cap': 120, 'canonical_follows_before_run': 0,
            'emergency_cap': 60, 'business_day': '2026-09-06',
            'hold': 'CLEARED',
        }
        self.ns = {
            '__name__': 'fleet_boot_registration_test',
            'Any': object,
            '_FLEET_CONSUMER_BOOT_ID': 'B1',
            '_FLEET_EXECUTIONS': {},
            'runtime_artifact_path': lambda *args: pathlib.Path(self.tmp.name).resolve().joinpath(*args),
            'supabase_client': types.SimpleNamespace(fleet_execution_rpc_v1=self.rpc),
            'subprocess': types.SimpleNamespace(Popen=self.spawn),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(root / 'account_run_request_consumer.py'), 'exec'), self.ns)

    def spawn(self, *args, **kwargs):
        self.spawns.append((args, kwargs))
        raise AssertionError('registration test must not spawn')

    def rpc(self, operation, payload):
        self.calls.append((operation, self.copy(payload)))
        if operation != 'register':
            raise AssertionError('registration must not emit ACK/cancel/reconcile')
        if payload.get('registration_intent') != 'REGISTER_OR_RETRY':
            raise RuntimeError('missing_registration_intent')
        identity = (payload.get('consumer_instance_id'), payload.get('consumer_boot_id'))
        if identity[0] != 'A' or identity[1] not in self.allowed_boots:
            raise RuntimeError('unauthorized_instance_or_boot')
        if identity in self.retired:
            raise RuntimeError('retired_boot_fenced')
        if payload.get('revision') != self.revision:
            raise RuntimeError('stale_registration_revision')
        if self.active is not None and self.active != identity:
            raise RuntimeError('active_owner_collision')
        for key in ('account_id', 'request_id', 'attempt_id'):
            if payload.get(key) != self.frame[key]:
                raise RuntimeError('registration_scope_mismatch')
        self.active = identity
        authority = dict(self.copy(self.frame),
                         consumer_instance_id=identity[0], consumer_boot_id=identity[1],
                         execution_registered=True, revision=self.revision)
        result = dict(payload, ok=True, authority=authority)
        if self.response_mutator:
            self.response_mutator(result)
        return result

    def bind(self, frame=None, instance='A'):
        return self.ns['_fleet_bind_authority'](
            'fixture-request', 'fixture-account', instance,
            {'fleet_runtime_v1': self.copy(self.frame if frame is None else frame)})

    def assert_no_runtime_effects(self):
        self.assertEqual(self.spawns, [])
        self.assertTrue(all(operation == 'register' for operation, _ in self.calls))

    def test_initial_unknown_boot_registers_then_verifies(self):
        session = self.bind()
        self.assertEqual(self.active, ('A', 'B1'))
        self.assertEqual(self.calls[0][1]['consumer_instance_id'], 'A')
        self.assertEqual(self.calls[0][1]['consumer_boot_id'], 'B1')
        self.assertEqual(self.calls[0][1]['revision'], 0)
        self.assertEqual(session.binding['consumer_boot_id'], 'B1')
        self.assert_no_runtime_effects()

    def test_same_boot_retry_is_idempotent(self):
        first = self.bind()
        token = first.channel.token
        self.ns['_FLEET_EXECUTIONS'].clear()
        retry = self.bind()
        self.assertEqual(retry.channel.token, token)
        self.assertEqual(self.active, ('A', 'B1'))
        self.assertEqual(len(self.calls), 2)
        self.assert_no_runtime_effects()

    def test_late_old_boot_cannot_retake_new_owner(self):
        self.allowed_boots.add('B2')
        self.active = ('A', 'B2')
        self.retired.add(('A', 'B1'))
        with self.assertRaisesRegex(RuntimeError, 'retired_boot_fenced'):
            self.bind()
        self.assertEqual(self.active, ('A', 'B2'))
        self.assertEqual(self.ns['_FLEET_EXECUTIONS'], {})
        self.assert_no_runtime_effects()

    def test_another_instance_cannot_claim_existing_boot(self):
        self.active = ('A', 'B1')
        altered = dict(self.frame, consumer_instance_id='OTHER')
        with self.assertRaisesRegex(RuntimeError, 'unauthorized_instance_or_boot'):
            self.bind(altered, instance='OTHER')
        self.assertEqual(self.active, ('A', 'B1'))
        self.assert_no_runtime_effects()

    def test_active_owner_is_not_overwritten_by_new_boot(self):
        self.active = ('A', 'B1')
        self.allowed_boots.add('B2')
        self.ns['_FLEET_CONSUMER_BOOT_ID'] = 'B2'
        with self.assertRaisesRegex(RuntimeError, 'active_owner_collision'):
            self.bind()
        self.assertEqual(self.active, ('A', 'B1'))
        self.assert_no_runtime_effects()

    def test_registered_old_boot_is_rejected_before_rpc(self):
        registered_old = dict(self.frame, execution_registered=True, consumer_boot_id='OLD')
        with self.assertRaisesRegex(RuntimeError, 'stale_consumer_boot_authority'):
            self.bind(registered_old)
        self.assertEqual(self.calls, [])
        self.assert_no_runtime_effects()

    def test_unbound_frame_cannot_smuggle_old_identity(self):
        with self.assertRaisesRegex(RuntimeError, 'unregistered_authority_contains_boot_identity'):
            self.bind(dict(self.frame, consumer_boot_id='OLD'))
        self.assertEqual(self.calls, [])
        self.assert_no_runtime_effects()

    def test_delayed_old_boot_response_rejected_before_channel(self):
        self.response_mutator = lambda result: result.update(consumer_boot_id='OLD')
        with self.assertRaisesRegex(RuntimeError, 'misbound_registration_response'):
            self.bind()
        self.assertEqual(self.ns['_FLEET_EXECUTIONS'], {})
        import os
        self.assertEqual(os.listdir(self.tmp.name), [])
        self.assert_no_runtime_effects()

    def test_response_envelope_and_authority_require_exact_identity_revision(self):
        cases = [
            (False, 'consumer_instance_id', 'OTHER'),
            (False, 'account_id', 'OTHER'),
            (False, 'revision', 1),
            (False, 'revision', False),
            (True, 'consumer_boot_id', 'OLD'),
            (True, 'consumer_instance_id', 'OTHER'),
            (True, 'account_id', 'OTHER'),
            (True, 'tenant_id', 'OTHER'),
            (True, 'revision', 1),
            (True, 'revision', False),
            (True, 'execution_registered', False),
        ]
        for nested, key, value in cases:
            with self.subTest(nested=nested, key=key, value=value):
                def mutate(result):
                    (result['authority'] if nested else result)[key] = value
                self.response_mutator = mutate
                with self.assertRaises(RuntimeError):
                    self.bind()
                self.assertEqual(self.ns['_FLEET_EXECUTIONS'], {})
                self.assert_no_runtime_effects()

    def test_restart_new_owner_does_not_inherit_old_active_ack(self):
        first = self.bind()
        barrier = first.barrier
        owner = first.owner
        barrier.reconcile_terminal('fixture-attempt', owner,
                                   canonical_terminal=True, child_gone=True)
        barrier.apply('fixture-hold', expected_revision=0, operation='hold')
        barrier.acknowledge(owner, 1)
        self.assertEqual(barrier.snapshot()['hold'], 'ACTIVE')
        # The service authorizes a replacement only after independent old-owner
        # terminal proof; merely presenting a new client boot never does this.
        self.active = None
        self.retired.add(('A', 'B1'))
        self.allowed_boots.add('B2')
        self.revision = 1
        self.frame.update(revision=1, attempt_revision=1, hold='ACTIVE')
        self.ns['_FLEET_EXECUTIONS'].clear()
        self.ns['_FLEET_CONSUMER_BOOT_ID'] = 'B2'
        second = self.bind()
        state = second.barrier.snapshot()
        self.assertNotEqual(second.channel.token, first.channel.token)
        self.assertEqual(state['hold'], 'REQUESTED')
        self.assertIsNone(state['active_revision'])
        self.assertNotIn(owner, state['owners'])
        self.assertEqual(state['owners'][second.owner], -1)
        with self.assertRaises(RuntimeError):
            second.barrier.acknowledge(owner, 1)
        with self.assertRaises(RuntimeError):
            second.barrier.acknowledge(second.owner, 0)
        self.assertEqual(state['cap_acks'], {})
        self.assert_no_runtime_effects()

    def test_stale_revision_rejected_by_registration_service(self):
        self.revision = 1
        with self.assertRaisesRegex(RuntimeError, 'stale_registration_revision'):
            self.bind()
        self.assertIsNone(self.active)
        self.assertEqual(self.ns['_FLEET_EXECUTIONS'], {})
        self.assert_no_runtime_effects()



class ConsumerHoldAckOrderingTests(unittest.TestCase):
    def setUp(self):
        import types
        self.fixture = ConsumerBootRegistrationContractTests('test_initial_unknown_boot_registers_then_verifies')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.session = self.fixture.bind()
        self.child = types.SimpleNamespace(pid=123, poll=lambda: None)
        self.session.spawn(lambda: self.child)
        self.session.register_child(self.child)
        self.calls = []
        self.inject_during_cancel = False
        self.session.rpc = self.rpc
        self.authority = dict(self.fixture.frame, execution_registered=True,
                              consumer_boot_id='B1', revision=1, hold='REQUESTED')

    def ack(self, revision=0):
        import pathlib, os, json
        files = list(pathlib.Path(self.fixture.tmp.name).resolve().rglob('authority.json'))
        self.assertEqual(len(files), 1)
        path = files[0].parent / 'cap-ack.json'
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({
                'schema': 'FLEET_CAP_BOUNDARY_ACK_V1',
                'account_id': 'fixture-account', 'request_id': 'fixture-request',
                'owner_boot_id': 'B1', 'revision': revision,
                'boundary': 'before_candidate_selection', 'business_day': '2026-09-06',
                'canonical_consumed': 0, 'effective_day_cap': 60, 'remaining': 60,
            }, stream)

    def rpc(self, operation, payload):
        self.calls.append(operation)
        if operation == 'cancel':
            if self.inject_during_cancel:
                self.ack(0)
            return dict(payload, ok=True, request={
                'id': 'fixture-request', 'status': 'canceled',
                'cancel_requested_at': '2026-09-06T15:00:00Z'})
        if operation == 'reconcile':
            return dict(payload, ok=True, canonical_terminal=True)
        if operation in ('runtime_ack', 'cap_ack'):
            return dict(payload, ok=True)
        raise AssertionError('unexpected RPC: ' + operation)

    def supervise(self):
        return self.fixture.ns['_fleet_supervise_existing'](
            'fixture-request', 'fixture-account', 'A',
            {'id': 'fixture-request', 'fleet_runtime_v1': self.authority}, self.child)

    def assert_stale_is_deferred_only_until_safe_finish(self):
        self.assertIn('cancel', self.calls)
        self.assertNotIn('cap_ack', self.calls)
        error = getattr(self.session, 'pending_cap_ack_error', None)
        self.assertIsNotNone(error)
        self.assertEqual(str(error), 'stale_or_misbound_cap_ack')
        self.assertEqual(self.session.barrier.snapshot()['hold'], 'REQUESTED')
        self.assertIsNone(self.session.barrier.snapshot()['active_revision'])
        self.child.poll = lambda: 0
        with self.assertRaisesRegex(RuntimeError, 'stale_or_misbound_cap_ack'):
            self.fixture.ns['_fleet_finish_execution']('fixture-request')
        self.assertIn('reconcile', self.calls)
        self.assertIn('runtime_ack', self.calls)
        self.assertLess(self.calls.index('cancel'), self.calls.index('reconcile'))
        self.assertLess(self.calls.index('reconcile'), self.calls.index('runtime_ack'))
        self.assertNotIn('cap_ack', self.calls)
        self.assertEqual(self.session.barrier.snapshot()['attempts']['fixture-attempt']['state'], 'TERMINAL')

    def test_stale_before_cancel_does_not_suppress_child_stop_or_reconcile(self):
        self.ack(0)
        row = self.supervise()
        self.assertEqual(row['status'], 'canceled')
        self.assert_stale_is_deferred_only_until_safe_finish()

    def test_stale_during_cancel_does_not_suppress_child_stop_or_reconcile(self):
        self.inject_during_cancel = True
        self.assertEqual(self.supervise()['status'], 'canceled')
        self.assert_stale_is_deferred_only_until_safe_finish()

    def test_stale_after_cancel_does_not_suppress_reconciliation(self):
        self.assertEqual(self.supervise()['status'], 'canceled')
        self.ack(0)
        self.assertEqual(self.supervise()['status'], 'canceled')
        self.assert_stale_is_deferred_only_until_safe_finish()

    def test_duplicate_stale_does_not_ack_or_erase_cancel(self):
        self.ack(0)
        for _ in range(2):
            self.assertEqual(self.supervise()['status'], 'canceled')
        self.assertEqual(self.calls.count('cancel'), 2)
        self.assert_stale_is_deferred_only_until_safe_finish()

    def test_current_valid_ack_keeps_cancellation_and_fencing_order(self):
        self.ack(1)
        self.assertEqual(self.supervise()['status'], 'canceled')
        self.assertEqual(self.calls[:2], ['cancel', 'cap_ack'])
        self.assertIsNone(getattr(self.session, 'pending_cap_ack_error', None))
        self.assertEqual(self.session.barrier.snapshot()['hold'], 'REQUESTED')
        self.child.poll = lambda: 0
        self.fixture.ns['_fleet_finish_execution']('fixture-request')
        self.assertNotIn('fixture-request', self.fixture.ns['_FLEET_EXECUTIONS'])
        self.assertEqual(self.calls.count('cap_ack'), 1)

    def test_no_hold_stale_remains_immediate_fencing_failure(self):
        self.authority['hold'] = 'CLEARED'
        self.ack(0)
        with self.assertRaisesRegex(RuntimeError, 'stale_or_misbound_cap_ack'):
            self.supervise()
        self.assertEqual(self.calls, [])
        self.assertIsNone(getattr(self.session, 'pending_cap_ack_error', None))



class ConsumerJournalRecoveryContractTests(unittest.TestCase):
    """Exercise real local journal/consumer code; Backend and process proof are fixtures."""
    def setUp(self):
        import types
        self.fixture = ConsumerBootRegistrationContractTests('test_initial_unknown_boot_registers_then_verifies')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.original = self.fixture.bind()
        self.child = types.SimpleNamespace(pid=123, poll=lambda: None)
        self.original.spawn(lambda: self.child)
        self.original.register_child(self.child)
        self.calls = []
        self.authority = dict(self.fixture.frame, execution_registered=True, consumer_boot_id='B1')

    def rpc(self, operation, payload):
        self.calls.append((operation, payload))
        return dict(payload, ok=True, canonical_terminal=True)

    def restore(self):
        import pathlib
        from fleet_runtime_barrier import FleetConsumerSession
        return FleetConsumerSession(
            binding=self.original.binding, authority=self.authority,
            root=pathlib.Path(self.fixture.tmp.name).resolve() / 'fleet-control-v1',
            rpc=self.rpc)

    def proof(self, session, **overrides):
        result = {
            'binding': dict(session.binding), 'pid': 123,
            'process_group_absent': True, 'heartbeat_expiry_used': False,
        }
        result.update(overrides)
        return result

    def test_exact_orphan_terminal_proof_reconciles(self):
        restored = self.restore()
        proof = self.proof(restored)
        restored.reconcile(orphan_proof=proof)
        self.assertEqual([operation for operation, _ in self.calls], ['reconcile'])
        self.assertEqual(self.calls[0][1]['orphan_proof']['binding'], restored.binding)
        self.assertEqual(restored.barrier.snapshot()['attempts']['fixture-attempt']['state'], 'TERMINAL')

    def test_legacy_flat_orphan_identity_is_rejected(self):
        from fleet_runtime_barrier import FleetBlocked
        restored = self.restore()
        flat = dict(restored.binding, pid=123, process_group_absent=True, heartbeat_expiry_used=False)
        with self.assertRaisesRegex(FleetBlocked, 'orphan_death_not_proven'):
            restored.reconcile(orphan_proof=flat)
        self.assertEqual(self.calls, [])

    def test_restart_without_child_proof_never_acknowledges(self):
        from fleet_runtime_barrier import FleetBlocked
        restored = self.restore()
        with self.assertRaisesRegex(FleetBlocked, 'child_unknown_or_alive'):
            restored.reconcile()
        self.assertEqual(self.calls, [])
        self.assertEqual(restored.barrier.snapshot()['attempts']['fixture-attempt']['state'], 'STARTED')

    def test_heartbeat_expiry_cannot_prove_process_death(self):
        from fleet_runtime_barrier import FleetBlocked
        restored = self.restore()
        with self.assertRaisesRegex(FleetBlocked, 'orphan_death_not_proven'):
            restored.reconcile(orphan_proof=self.proof(restored, heartbeat_expiry_used=True))
        self.assertEqual(self.calls, [])

    def test_wrong_orphan_process_identity_never_reconciles(self):
        from fleet_runtime_barrier import FleetBlocked
        restored = self.restore()
        with self.assertRaisesRegex(FleetBlocked, 'orphan_death_not_proven'):
            restored.reconcile(orphan_proof=self.proof(restored, pid=456))
        self.assertEqual(self.calls, [])

    def test_journal_restart_never_replays_spawn(self):
        from fleet_runtime_barrier import FleetBlocked
        restored = self.restore()
        spawned = []
        with self.assertRaises(FleetBlocked):
            restored.spawn(lambda: spawned.append(True))
        self.assertEqual(spawned, [])

    def test_wrong_boot_or_account_proof_is_rejected(self):
        from fleet_runtime_barrier import FleetBlocked
        restored = self.restore()
        for key, value in (('consumer_boot_id', 'OLD'), ('account_id', 'OTHER'),
                           ('consumer_instance_id', 'OTHER'), ('attempt_id', 'OTHER')):
            with self.subTest(key=key):
                binding = dict(restored.binding, **{key: value})
                with self.assertRaisesRegex(FleetBlocked, 'orphan_death_not_proven'):
                    restored.reconcile(orphan_proof=self.proof(restored, binding=binding))
                self.assertEqual(self.calls, [])

    def test_live_process_group_blocks_orphan_reconciliation(self):
        from fleet_runtime_barrier import FleetBlocked
        restored = self.restore()
        with self.assertRaisesRegex(FleetBlocked, 'orphan_death_not_proven'):
            restored.reconcile(orphan_proof=self.proof(restored, process_group_absent=False))
        self.assertEqual(self.calls, [])

    def test_backend_without_terminal_proof_never_commits_local_terminal(self):
        from fleet_runtime_barrier import FleetBlocked
        restored = self.restore()
        def nonterminal(operation, payload):
            self.calls.append((operation, payload))
            return dict(payload, ok=True, canonical_terminal=False)
        restored.rpc = nonterminal
        with self.assertRaisesRegex(FleetBlocked, 'canonical_terminal_not_proven'):
            restored.reconcile(orphan_proof=self.proof(restored))
        self.assertEqual([operation for operation, _ in self.calls], ['reconcile'])
        self.assertEqual(restored.barrier.snapshot()['attempts']['fixture-attempt']['state'], 'STARTED')

    def test_duplicate_exact_terminal_proof_remains_terminal_without_runtime_ack(self):
        restored = self.restore()
        for _ in range(2):
            restored.reconcile(orphan_proof=self.proof(restored))
        self.assertEqual(restored.barrier.snapshot()['attempts']['fixture-attempt']['state'], 'TERMINAL')
        self.assertEqual([operation for operation, _ in self.calls], ['reconcile', 'reconcile'])



class PersistentRunnerChannelContractTests(unittest.TestCase):
    """Real consumer journal and runner file adapter, no external runtime state."""
    @classmethod
    def setUpClass(cls):
        import ast, pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        source = ast.parse((root / 'supabase_client.py').read_text())
        futures = [node for node in source.body
                   if isinstance(node, ast.ImportFrom) and node.module == '__future__']
        fn = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                  and node.name == 'fleet_follow_boundary_local_v1')
        cls.extracted = compile(ast.Module(body=futures + [fn], type_ignores=[]),
                                str(root / 'supabase_client.py'), 'exec')

    def setUp(self):
        import pathlib, datetime, builtins, types, os, re, json
        self.fixture = ConsumerBootRegistrationContractTests('test_initial_unknown_boot_registers_then_verifies')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.session = self.fixture.bind()
        self.root = pathlib.Path(self.fixture.tmp.name).resolve()
        self.outside = []
        self.network = []
        def artifact(*parts):
            path = self.root.joinpath(*parts)
            if self.root not in path.resolve().parents:
                self.outside.append(str(path))
                raise AssertionError('artifact outside fixture')
            return path
        class Clock:
            @classmethod
            def now(cls, tz=None):
                return datetime.datetime(2026, 9, 6, 15, tzinfo=datetime.timezone.utc)
        original_import = builtins.__import__
        def imports(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'runtime_artifacts':
                return types.SimpleNamespace(runtime_artifact_path=artifact)
            if name.split('.')[0] in ('requests', 'httpx', 'urllib', 'socket', 'supabase'):
                self.network.append(name)
                raise AssertionError('network forbidden in local boundary')
            return original_import(name, globals, locals, fromlist, level)
        self.ns = {
            '__builtins__': dict(vars(builtins), __import__=imports),
            '__name__': 'persistent_runner_fixture', 'os': os, 're': re, 'json': json,
            'datetime': Clock, 'BUSINESS_TIMEZONE': datetime.timezone.utc,
            '_FLEET_RUNNER_BOUNDARIES_V1': {},
        }
        exec(self.extracted, self.ns)
        self.authority_path = self.session.channel.path / 'authority.json'
        self.initial = json.loads(self.authority_path.read_text())
        self.last_frame = dict(self.fixture.frame, execution_registered=True, consumer_boot_id='B1')

    def consume(self, count=52):
        import os, pathlib
        from unittest.mock import patch
        original_open = os.open
        def confined_open(path, flags, *args, **kwargs):
            resolved = pathlib.Path(path).resolve()
            if resolved != self.root and self.root not in resolved.parents:
                self.outside.append(str(resolved))
                raise AssertionError('filesystem operation outside fixture')
            return original_open(path, flags, *args, **kwargs)
        with patch.dict(os.environ, self.session.environment(), clear=True), patch.object(os, 'open', confined_open):
            result = self.ns['fleet_follow_boundary_local_v1'](
                account_id='fixture-account', request_id='fixture-request', session_follows=count)
        self.assertEqual(self.outside, [])
        self.assertEqual(self.network, [])
        return result

    def restart_runner(self):
        self.ns['_FLEET_RUNNER_BOUNDARIES_V1'].clear()

    def revise(self, revision, cap):
        self.last_frame = dict(self.fixture.frame, execution_registered=True,
                               consumer_boot_id='B1', revision=revision, emergency_cap=cap)
        self.session.observe(self.last_frame)

    def interrupt_publication(self):
        from unittest.mock import patch
        original_write = self.session.channel.write
        def fail_publish(name, value):
            if name == 'authority.json':
                raise OSError('fixture: crash before publication')
            return original_write(name, value)
        with patch.object(self.session.channel, 'write', fail_publish):
            with self.assertRaisesRegex(OSError, 'crash before publication'):
                self.revise(1, 40)
        journal = self.session.channel.read('journal.json')
        self.assertEqual(journal['state']['revision'], 1)
        self.assertEqual(journal['state']['cap'], 40)
        self.assertEqual(self.session.channel.read('authority.json')['revision'], 0)

    def assert_rejected_without_ack(self):
        from fleet_runtime_barrier import FleetBlocked
        ack = self.session.channel.path / 'cap-ack.json'
        before = ack.read_bytes() if ack.exists() else None
        with self.assertRaises(FleetBlocked):
            self.consume()
        after = ack.read_bytes() if ack.exists() else None
        self.assertEqual(after, before)
        self.assertEqual(self.outside, [])
        self.assertEqual(self.network, [])

    def test_future_import_preserved_on_python39(self):
        import ast, sys
        self.assertEqual(sys.version_info[:3], (3, 9, 6))
        tree = ast.parse('from __future__ import annotations\ndef probe(x: MissingType) -> dict[str, MissingType] | None:\n    return x\n')
        namespace = {}
        exec(compile(tree, 'future-annotation-fixture', 'exec'), namespace)
        self.assertEqual(namespace['probe'](7), 7)
        self.assertIsInstance(namespace['probe'].__annotations__['return'], str)

    def test_normal_commit_and_channel_transport(self):
        result = self.consume()
        self.assertEqual((result['revision'], result['effective_day_cap'], result['remaining']), (0, 60, 8))
        self.assertEqual(self.session.channel.read('cap-ack.json')['revision'], 0)

    def test_exact_crash_window_restart_blocks_then_republishes(self):
        self.consume()
        self.interrupt_publication()
        self.restart_runner()
        self.assert_rejected_without_ack()
        self.session.republish_current_authority()
        result = self.consume()
        self.assertEqual((result['revision'], result['effective_day_cap'], result['remaining']), (1, 40, 0))

    def test_consumer_restart_recovers_from_committed_journal(self):
        from fleet_runtime_barrier import FleetConsumerSession
        self.interrupt_publication()
        recovered = FleetConsumerSession(
            binding=self.session.binding, authority=self.last_frame,
            root=self.root / 'fleet-control-v1', rpc=self.fixture.rpc)
        self.assertEqual(recovered.channel.read('authority.json')['revision'], 1)
        self.restart_runner()
        self.assertEqual(self.consume()['remaining'], 0)

    def test_same_revision_observe_retries_failed_publication(self):
        self.interrupt_publication()
        self.session.observe(self.last_frame)
        self.restart_runner()
        self.assertEqual(self.consume()['revision'], 1)

    def test_channel_below_durable_is_rejected(self):
        self.revise(1, 40)
        self.session.channel.write('authority.json', self.initial)
        self.restart_runner()
        self.assert_rejected_without_ack()

    def test_channel_ahead_of_durable_is_rejected(self):
        self.session.channel.write('authority.json', dict(self.initial, revision=9))
        self.assert_rejected_without_ack()

    def test_missing_channel_is_blocked_until_republish(self):
        self.authority_path.unlink()
        self.assert_rejected_without_ack()
        self.session.republish_current_authority()
        self.assertEqual(self.consume()['revision'], 0)

    def test_corrupt_channel_is_blocked_until_republish(self):
        self.authority_path.write_text('{')
        self.assert_rejected_without_ack()
        self.session.republish_current_authority()
        self.assertEqual(self.consume()['revision'], 0)

    def test_missing_durable_journal_never_trusts_channel(self):
        (self.session.channel.path / 'journal.json').unlink()
        self.assert_rejected_without_ack()

    def test_corrupt_durable_journal_never_trusts_channel(self):
        (self.session.channel.path / 'journal.json').write_text('{')
        self.assert_rejected_without_ack()

    def test_restart_after_successful_publish(self):
        self.revise(1, 55)
        self.consume()
        self.restart_runner()
        self.assertEqual((self.consume()['revision'], self.consume()['remaining']), (1, 3))

    def test_duplicate_republish_preserves_journal_and_transport(self):
        before = self.session.channel.read('journal.json')
        self.session.republish_current_authority()
        self.session.republish_current_authority()
        self.assertEqual(self.session.channel.read('journal.json'), before)
        self.assertEqual(self.session.channel.read('authority.json'), self.initial)

    def test_delayed_old_publication_after_current_ack_is_rejected(self):
        self.revise(1, 40)
        self.consume()
        self.session.channel.write('authority.json', self.initial)
        self.assert_rejected_without_ack()
        self.restart_runner()
        self.assert_rejected_without_ack()

    def test_same_revision_cap_tampering_is_rejected(self):
        self.session.channel.write('authority.json', dict(self.initial, emergency_cap=120))
        self.assert_rejected_without_ack()

    def test_channel_binding_and_integrity_tampering_is_rejected(self):
        for key, value in (('owner_boot_id', 'OLD'), ('account_id', 'OTHER'),
                           ('consumer_instance_id', 'OTHER'), ('integrity_version', 99),
                           ('authority_sha256', 'invalid')):
            with self.subTest(key=key):
                self.session.channel.write('authority.json', dict(self.initial, **{key: value}))
                self.assert_rejected_without_ack()

    def test_clear_cap_increase_and_decrease(self):
        self.consume()
        for revision, cap, effective, remaining in (
                (1, 80, 80, 28), (2, 40, 40, 0), (3, None, 120, 68)):
            with self.subTest(revision=revision):
                self.revise(revision, cap)
                result = self.consume()
                self.assertEqual((result['revision'], result['effective_day_cap'], result['remaining']),
                                 (revision, effective, remaining))

    def test_no_candidate_network_reads(self):
        for count in (52, 53, 54):
            self.consume(count)
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(self.network, [])

    def test_same_revision_conflict_after_consumer_restart_is_rejected(self):
        from fleet_runtime_barrier import FleetConsumerSession, FleetBlocked
        before = self.session.channel.read('authority.json')
        with self.assertRaisesRegex(FleetBlocked, 'conflicting_authority_revision'):
            FleetConsumerSession(binding=self.session.binding,
                authority=dict(self.last_frame, emergency_cap=40),
                root=self.root / 'fleet-control-v1', rpc=self.fixture.rpc)
        self.assertEqual(self.session.channel.read('authority.json'), before)



class FleetProcessGroupContractTests(unittest.TestCase):
    def setUp(self):
        import fleet_runtime_barrier
        self.real_probe = fleet_runtime_barrier.fleet_process_group_absent
        self.fixture = ConsumerBootRegistrationContractTests('test_initial_unknown_boot_registers_then_verifies')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.group_probe_patch.stop()
        self.session = self.fixture.bind()
        self.calls = []
        self.session.rpc = self.rpc
        self.processes = []
        self.addCleanup(self.cleanup_groups)

    def rpc(self, operation, payload):
        self.calls.append((operation, payload))
        return dict(payload, ok=True, canonical_terminal=True)

    def cleanup_groups(self):
        import os, signal
        for proc in self.processes:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # Darwin may return EPERM during orphan reaping. This is NOT
                # absence: cleanup still requires the real ESRCH proof below.
                pass
            proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            self.wait_absent(proc.pid)

    def wait_absent(self, pgid):
        import time
        deadline = time.monotonic() + 5
        while True:
            try:
                if self.real_probe(pgid):
                    return
            except FleetBlocked as exc:
                if str(exc) != 'process_group_quiescence_unknown':
                    raise
                # Retry observation only; never turn unknown into a PASS.
            if time.monotonic() >= deadline:
                self.fail('fixture process group did not disappear after cleanup')
            time.sleep(0.01)

    def test_cleanup_transient_unknown_requires_later_absence(self):
        from unittest.mock import Mock
        probe = Mock(side_effect=[FleetBlocked('process_group_quiescence_unknown'), True])
        self.real_probe = probe
        self.wait_absent(123)
        self.assertEqual(probe.call_count, 2)

    def test_cleanup_persistent_unknown_is_not_absence(self):
        from unittest.mock import Mock, patch
        self.real_probe = Mock(side_effect=FleetBlocked('process_group_quiescence_unknown'))
        with patch('time.monotonic', side_effect=[0, 10]):
            with self.assertRaisesRegex(AssertionError, 'did not disappear'):
                self.wait_absent(123)

    def family(self, *, leader_exit=False, ignore_term=False, grandchild=False):
        import subprocess, sys, select
        script = """import os, signal
ignore = %r
grandchild = %r
leader_exit = %r
r,w = os.pipe()
child = os.fork()
if child == 0:
    os.close(r)
    signal.signal(signal.SIGTERM, signal.SIG_IGN if ignore else signal.SIG_DFL)
    grand = os.fork() if grandchild else -1
    if grand == 0:
        while True:
            signal.pause()
    os.write(w, (str(os.getpid()) + ',' + str(grand)).encode())
    os.close(w)
    while True:
        signal.pause()
os.close(w)
ready = os.read(r, 128)
os.close(r)
print(ready.decode(), flush=True)
if leader_exit:
    os._exit(0)
while True:
    signal.pause()
""" % (ignore_term, grandchild, leader_exit)
        proc = self.session.spawn(lambda: subprocess.Popen(
            [sys.executable, '-I', '-B', '-c', script],
            start_new_session=True, stdout=subprocess.PIPE, text=True))
        self.processes.append(proc)
        self.session.register_child(proc)
        self.assertTrue(select.select([proc.stdout], [], [], 5)[0], 'fixture readiness timeout')
        descendants = [int(value) for value in proc.stdout.readline().strip().split(',')]
        if leader_exit:
            proc.wait(timeout=5)
        return proc, [pid for pid in descendants if pid > 0]

    def single_exited(self):
        import subprocess, sys
        proc = self.session.spawn(lambda: subprocess.Popen(
            [sys.executable, '-I', '-B', '-c', 'pass'], start_new_session=True))
        self.processes.append(proc)
        self.session.register_child(proc)
        proc.wait(timeout=5)
        self.wait_absent(proc.pid)
        return proc

    def hold(self):
        self.authority = dict(self.fixture.frame, execution_registered=True,
                              consumer_boot_id='B1', revision=1, hold='REQUESTED')
        self.session.observe(self.authority)

    def assert_no_terminal_or_ack(self):
        self.assertEqual(self.calls, [])
        self.assertEqual(self.session.barrier.snapshot()['hold'], 'REQUESTED')

    def test_leader_exit_with_descendant_blocks_then_group_death_allows_ack(self):
        import os, signal
        from fleet_runtime_barrier import FleetBlocked
        proc, descendants = self.family(leader_exit=True)
        self.hold()
        self.assertEqual(proc.poll(), 0)
        self.assertEqual(os.getpgid(descendants[0]), proc.pid)
        self.assertFalse(self.session.process_group_quiescent())
        self.assertFalse(self.fixture.ns['_fleet_child_fully_exited']('fixture-request', proc))
        with self.assertRaisesRegex(FleetBlocked, 'process_group_still_alive'):
            self.session.reconcile()
        self.assert_no_terminal_or_ack()
        os.killpg(proc.pid, signal.SIGKILL)
        self.wait_absent(proc.pid)
        self.session.reconcile()
        self.assertEqual([op for op, _ in self.calls], ['reconcile', 'runtime_ack'])
        self.assertEqual(self.session.barrier.snapshot()['hold'], 'ACTIVE')

    def test_leader_without_descendants_normal_terminal(self):
        self.single_exited()
        self.hold()
        self.session.reconcile()
        self.assertEqual([op for op, _ in self.calls], ['reconcile', 'runtime_ack'])

    def test_sigterm_whole_group_and_inherited_grandchild(self):
        import os, signal
        proc, descendants = self.family(grandchild=True)
        self.hold()
        for pid in descendants:
            self.assertEqual(os.getpgid(pid), proc.pid)
            self.assertEqual(os.getsid(pid), proc.pid)
        code = self.session.terminate_process_group(graceful_timeout_seconds=1)
        self.assertEqual(code, -signal.SIGTERM)
        self.assertTrue(self.session.process_group_quiescent())
        self.assertEqual(self.calls, [])
        self.session.reconcile()
        self.assertEqual([op for op, _ in self.calls], ['reconcile', 'runtime_ack'])

    def test_sigterm_survivor_blocks_ack(self):
        import os, signal
        from fleet_runtime_barrier import FleetBlocked
        proc, _ = self.family(ignore_term=True)
        self.hold()
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
        self.assertFalse(self.session.process_group_quiescent())
        with self.assertRaisesRegex(FleetBlocked, 'process_group_still_alive'):
            self.session.reconcile()
        self.assert_no_terminal_or_ack()

    def test_existing_escalation_policy_kills_remaining_group(self):
        import os, signal
        from unittest.mock import patch
        proc, _ = self.family(ignore_term=True)
        self.hold()
        signals = []
        original = os.killpg
        def record(pgid, sig):
            if sig:
                signals.append((pgid, sig))
            return original(pgid, sig)
        with patch.object(os, 'killpg', record):
            code = self.session.terminate_process_group(graceful_timeout_seconds=1)
        self.assertEqual(code, -signal.SIGTERM)
        self.assertEqual(signals, [(proc.pid, signal.SIGTERM), (proc.pid, signal.SIGKILL)])
        self.assertTrue(self.session.process_group_quiescent())
        self.session.reconcile()
        self.assertEqual([op for op, _ in self.calls], ['reconcile', 'runtime_ack'])

    def test_absent_group_reconciliation_is_idempotent(self):
        self.single_exited()
        self.hold()
        self.session.reconcile()
        self.session.reconcile()
        self.assertEqual([op for op, _ in self.calls], ['reconcile', 'runtime_ack', 'reconcile'])

    def test_stale_boot_group_record_rejected(self):
        from fleet_runtime_barrier import FleetBlocked
        self.single_exited()
        self.hold()
        state = self.session.barrier.snapshot()
        state['attempts']['fixture-attempt']['process_group']['consumer_boot_id'] = 'OLD'
        self.session.barrier._save(state)
        with self.assertRaisesRegex(FleetBlocked, 'missing_or_misbound_process_group'):
            self.session.reconcile()
        self.assert_no_terminal_or_ack()

    def test_restart_cannot_trust_claimed_absence_while_group_alive(self):
        import os, signal
        from fleet_runtime_barrier import FleetConsumerSession, FleetBlocked
        proc, _ = self.family(leader_exit=True)
        self.hold()
        recovered = FleetConsumerSession(binding=self.session.binding, authority=self.authority,
            root=self.fixture.ns['runtime_artifact_path']('fleet-control-v1'), rpc=self.rpc)
        proof = dict(binding=recovered.binding, pid=proc.pid,
                     process_group_absent=True, heartbeat_expiry_used=False)
        with self.assertRaisesRegex(FleetBlocked, 'process_group_still_alive'):
            recovered.reconcile(orphan_proof=proof)
        self.assertEqual(self.calls, [])
        os.killpg(proc.pid, signal.SIGKILL)
        self.wait_absent(proc.pid)
        recovered.reconcile(orphan_proof=proof)
        self.assertEqual([op for op, _ in self.calls], ['reconcile', 'runtime_ack'])

    def test_restart_does_not_signal_using_only_stored_numeric_group(self):
        from fleet_runtime_barrier import FleetConsumerSession, FleetBlocked
        self.single_exited()
        self.hold()
        recovered = FleetConsumerSession(binding=self.session.binding, authority=self.authority,
            root=self.fixture.ns['runtime_artifact_path']('fleet-control-v1'), rpc=self.rpc)
        with self.assertRaisesRegex(FleetBlocked, 'group_termination_requires_owned_child'):
            recovered.terminate_process_group()
        self.assertEqual(self.calls, [])

    def test_esrch_only_is_positive_quiescence_proof(self):
        import errno, os
        from unittest.mock import patch
        from fleet_runtime_barrier import FleetBlocked
        with patch.object(os, 'killpg', side_effect=ProcessLookupError(errno.ESRCH, 'fixture')):
            self.assertTrue(self.real_probe(999999))
        with patch.object(os, 'killpg', return_value=None):
            self.assertFalse(self.real_probe(999999))
        for code in (errno.EPERM, errno.EIO, errno.EINVAL):
            with self.subTest(errno=code), patch.object(os, 'killpg', side_effect=OSError(code, 'fixture')):
                with self.assertRaisesRegex(FleetBlocked, 'process_group_quiescence_unknown'):
                    self.real_probe(999999)

    def test_process_group_identity_is_bound_and_persisted(self):
        proc = self.single_exited()
        record = self.session.channel.read('journal.json')['state']['attempts']['fixture-attempt']['process_group']
        for key, value in self.session.binding.items():
            self.assertEqual(record[key], value)
        self.assertEqual(record['process_group_id'], proc.pid)
        self.assertEqual(record['fleet_revision'], 0)
        self.assertEqual(record['leader_pid'], proc.pid)

    def test_unknown_group_is_not_inferred_from_pid(self):
        from fleet_runtime_barrier import FleetBlocked
        self.single_exited()
        self.hold()
        state = self.session.barrier.snapshot()
        del state['attempts']['fixture-attempt']['process_group']
        self.session.barrier._save(state)
        with self.assertRaisesRegex(FleetBlocked, 'missing_or_misbound_process_group'):
            self.session.reconcile()
        self.assert_no_terminal_or_ack()

    def test_historical_termination_without_fleet_keeps_term_wait_contract(self):
        import ast, pathlib, signal, subprocess, types
        root = pathlib.Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'account_run_request_consumer.py').read_text())
        selected = [node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == '__future__']
        selected += [next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_terminate_subprocess')]
        ns = dict(self.fixture.ns, signal=signal, subprocess=subprocess)
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'historical-termination', 'exec'), ns)
        calls = []
        proc = types.SimpleNamespace(
            send_signal=lambda sig: calls.append(('signal', sig)),
            wait=lambda timeout: calls.append(('wait', timeout)) or 7,
            kill=lambda: calls.append(('kill',)))
        self.assertEqual(ns['_terminate_subprocess'](proc), 7)
        self.assertEqual(calls, [('signal', signal.SIGTERM), ('wait', 30.0)])


if __name__ == '__main__': unittest.main()


class PostSigtermReapOrderingTests(unittest.TestCase):
    def exercise(self, after_reap_error=None):
        import errno, os, types
        from fleet_runtime_barrier import FleetConsumerSession
        from unittest.mock import Mock, patch
        events = []
        group = dict(process_group_id=999999)
        child = Mock(pid=999999)
        child.poll.return_value = None
        def wait(timeout):
            events.append('reap')
            return -15
        child.wait.side_effect = wait
        session = types.SimpleNamespace(_mutex=__import__('threading').RLock(),
            child=child, _registered_process_group=lambda: dict(group))
        def probe(pgid, sig):
            if sig:
                events.append('term')
                return
            if 'term' not in events:
                return
            self.assertIn('reap', events, 'group probed before canonical leader reap')
            events.append('probe')
            error = (after_reap_error.pop(0) if isinstance(after_reap_error, list)
                     and after_reap_error else after_reap_error)
            if error:
                raise OSError(error, 'fixture unknown')
            raise ProcessLookupError(errno.ESRCH, 'fixture proven absent')
        with patch.object(os, 'killpg', side_effect=probe):
            if after_reap_error and not isinstance(after_reap_error, list):
                with self.assertRaisesRegex(FleetBlocked, 'process_group_quiescence_unknown'):
                    FleetConsumerSession.terminate_process_group(session, graceful_timeout_seconds=1)
            else:
                self.assertEqual(FleetConsumerSession.terminate_process_group(session,
                    graceful_timeout_seconds=1), -15)
        self.assertLess(events.index('reap'), events.index('probe'))

    def test_reap_precedes_post_term_group_probe(self):
        self.exercise()

    def test_permission_denied_after_reap_still_fails_closed(self):
        import errno
        self.exercise(errno.EPERM)

    def test_transient_permission_denied_requires_later_esrch(self):
        import errno
        self.exercise([errno.EPERM, None])


class FleetLegacyTransitionV1Tests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        self.f = ConsumerBootRegistrationContractTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.session = self.f.bind()
        self.binding = dict(self.session.binding)
        self.root = Path(self.f.tmp.name).resolve() / 'transition'

    def new_child(self):
        from types import SimpleNamespace
        from fleet_runtime_barrier import fleet_spawn_isolated_v1
        return fleet_spawn_isolated_v1(
            lambda: SimpleNamespace(pid=123, poll=lambda: 0),
            binding=self.binding, root=self.root)

    def late_session(self, child, hold='REQUESTED'):
        from fleet_runtime_barrier import FleetConsumerSession
        authority = dict(self.f.frame, **self.binding)
        authority.update(execution_registered=True, hold=hold, revision=1)
        evidence = getattr(child, '_fleet_isolated_launch_v1', None)
        if evidence is not None:
            authority['process_generation'] = evidence[1]['process_group']['process_generation']
        def rpc(operation, payload):
            self.f.calls.append((operation, payload))
            return dict(payload, ok=True, canonical_terminal=True)
        return FleetConsumerSession(binding=self.binding, authority=authority,
                                    root=self.root / 'late', rpc=rpc, child=child)

    def test_new_unmanaged_launch_has_durable_generation_without_fabricated_revision(self):
        child = self.new_child()
        channel, receipt = child._fleet_isolated_launch_v1
        self.assertEqual(channel.read('journal.json'), receipt)
        self.assertEqual(receipt['process_group']['isolation_version'], 'isolated_process_group_v1')
        self.assertEqual(len(receipt['process_group']['process_generation']), 32)
        self.assertIsNone(receipt['process_group']['fleet_revision'])
        self.assertEqual(len(self.f.calls), 1)  # only fixture enrollment

    def test_generations_are_unique(self):
        a = self.new_child()._fleet_isolated_launch_v1[1]
        b = self.new_child()._fleet_isolated_launch_v1[1]
        self.assertNotEqual(a['process_generation'], b['process_generation'])

    def test_unmanaged_isolated_runner_can_enroll_with_exact_identity(self):
        child = self.new_child()
        session = self.late_session(child)
        session.register_late_child_isolation(child)
        self.assertFalse(session.is_legacy_unisolated())
        self.assertEqual(session._registered_process_group()['process_group_id'], 123)
        session.reconcile()
        self.assertEqual(session.barrier.snapshot()['hold'], 'ACTIVE')
        self.assertTrue(any(op == 'runtime_ack' for op, _ in self.f.calls))

    def test_legacy_hold_never_signals_or_acks_even_after_leader_exit(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from fleet_runtime_barrier import FleetBlocked
        child = SimpleNamespace(pid=123, poll=lambda: 0)
        session = self.late_session(child)
        session.register_late_child_isolation(child)
        with patch('os.killpg', side_effect=AssertionError('legacy collective signal forbidden')):
            with self.assertRaisesRegex(FleetBlocked, 'legacy_unisolated_execution'):
                session.reconcile()
        state = session.barrier.snapshot()
        self.assertEqual(state['hold'], 'REQUESTED')
        self.assertIsNone(state['active_revision'])
        self.assertEqual(state['convergence_reason'], 'legacy_unisolated_execution')
        self.assertFalse(any(op == 'runtime_ack' for op, _ in self.f.calls))

    def test_legacy_flag_survives_session_restart(self):
        from types import SimpleNamespace
        from fleet_runtime_barrier import FleetConsumerSession, FleetBlocked
        child = SimpleNamespace(pid=123, poll=lambda: 0)
        session = self.late_session(child)
        session.register_late_child_isolation(child)
        restored = FleetConsumerSession(binding=self.binding, authority=session._authority,
                                       root=self.root / 'late', rpc=session.rpc)
        self.assertTrue(restored.is_legacy_unisolated())
        with self.assertRaisesRegex(FleetBlocked, 'legacy_unisolated_execution'):
            restored.reconcile()

    def test_tampered_receipt_is_rejected(self):
        from fleet_runtime_barrier import FleetBlocked
        child = self.new_child()
        channel, receipt = child._fleet_isolated_launch_v1
        channel.write('journal.json', dict(receipt, state='STARTING'))
        session = self.late_session(child)
        with self.assertRaisesRegex(FleetBlocked, 'invalid_isolated_launch_evidence'):
            session.register_late_child_isolation(child)

    def test_other_attempt_cannot_adopt_isolated_receipt(self):
        from fleet_runtime_barrier import FleetConsumerSession, FleetBlocked
        child = self.new_child()
        binding = dict(self.binding, attempt_id='different-attempt')
        authority = dict(self.f.frame, **binding)
        authority.update(execution_registered=True)
        session = FleetConsumerSession(binding=binding, authority=authority,
                                       root=self.root / 'other', rpc=self.session.rpc, child=child)
        with self.assertRaisesRegex(FleetBlocked, 'misbound_isolated_launch_evidence'):
            session.register_late_child_isolation(child)

    def test_owned_group_identity_survives_restart_without_signal_authority(self):
        from fleet_runtime_barrier import FleetConsumerSession, FleetBlocked
        child = self.new_child()
        session = self.late_session(child)
        session.register_late_child_isolation(child)
        restored = FleetConsumerSession(binding=self.binding, authority=session._authority,
                                       root=self.root / 'late', rpc=session.rpc)
        self.assertEqual(restored._registered_process_group(), session._registered_process_group())
        with self.assertRaisesRegex(FleetBlocked, 'group_termination_requires_owned_child'):
            restored.terminate_process_group()

    def test_actual_unmanaged_spawn_has_own_session_and_original_exit_result(self):
        import os
        import subprocess
        import sys
        from types import SimpleNamespace
        self.f.ns['_FLEET_EXECUTIONS'].clear()
        self.f.ns['subprocess'] = subprocess
        before = len(self.f.calls)
        child = self.f.ns['_fleet_spawn_or_legacy'](
            self.binding['request_id'], [sys.executable, '-I', '-B', '-c',
            'import os; print(os.getpid(), os.getpgrp(), os.getsid(0)); raise SystemExit(7)'],
            self.root.parent, dict(os.environ), launch_binding=self.binding,
            popen_options={'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE, 'text': True})
        try:
            stdout, stderr = child.communicate(timeout=5)
            pid, pgid, sid = map(int, stdout.split())
            self.assertEqual((pid, pgid, sid), (child.pid, child.pid, child.pid))
            self.assertNotEqual(pgid, os.getpgrp())
            self.assertEqual(child.returncode, 7)
            self.assertEqual(len(self.f.calls), before)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_managed_launch_generation_is_required(self):
        from types import SimpleNamespace
        from fleet_runtime_barrier import FleetBlocked
        child = self.session.spawn(lambda: SimpleNamespace(pid=123))
        self.session.register_child(child)
        group = self.session._registered_process_group()
        self.assertEqual(group['isolation_version'], 'isolated_process_group_v1')
        state = self.session.barrier.snapshot()
        del state['attempts'][self.binding['attempt_id']]['process_group']['process_generation']
        self.session.barrier._save(state)
        with self.assertRaisesRegex(FleetBlocked, 'missing_or_misbound_process_generation'):
            self.session._registered_process_group()

class ExplicitSuccessorRegistrationContractTests(unittest.TestCase):
    """Consumer-side fencing only; does not certify Backend RPC acceptance."""

    def setUp(self):
        import ast
        import copy
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        import fleet_runtime_barrier
        self.copy = copy.deepcopy
        source = Path(__file__).resolve().parents[1] / 'account_run_request_consumer.py'
        parsed = ast.parse(source.read_text(encoding='utf-8'))
        nodes = [node for node in parsed.body
                 if isinstance(node, ast.ImportFrom) and node.module == '__future__'
                 or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name.startswith('_fleet_')]
        self.calls = []
        self.binding = dict(account_id='fixture-account', request_id='fixture-request',
                            attempt_id='fixture-attempt', consumer_instance_id='A',
                            consumer_boot_id='B2')
        previous = dict(self.binding, consumer_boot_id='B1')
        self.frame = dict(previous, schema='FLEET_EXECUTION_AUTHORITY_V1',
                          tenant_id='fixture-tenant', execution_registered=True,
                          revision=7, attempt_revision=7, owner_generation=1,
                          hold_state='REQUESTED', normal_cap=120,
                          canonical_baseline=0, emergency_cap=60,
                          business_day='2026-09-06')
        self.frame['succession'] = dict(
            schema='FLEET_OWNER_SUCCESSION_V1', previous_binding=previous,
            previous_generation=1, old_owner_reconciled=True,
            old_attempts_open=0, old_process_group_quiescent=True,
            process_group_id=12345, process_generation='a' * 32,
            terminal_evidence_sha256='b' * 64)
        self.response_mutator = lambda response: response
        def rpc(operation, payload):
            self.calls.append((operation, self.copy(payload)))
            authority = dict(self.frame, **self.binding)
            authority['owner_generation'] = 2
            return self.response_mutator(dict(self.binding, ok=True, revision=7,
                                             owner_generation=2, authority=authority))
        self.ns = dict(_FLEET_CONSUMER_BOOT_ID='B2', _FLEET_EXECUTIONS={},
                       supabase_client=SimpleNamespace(fleet_execution_rpc_v1=rpc),
                       runtime_artifact_path=lambda *parts: Path('/unused-fixture-path'),
                       Any=object)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<successor-harness>', 'exec'), self.ns)
        self.absence = patch.object(fleet_runtime_barrier, 'fleet_process_group_absent', return_value=True).start()
        self.session = patch.object(fleet_runtime_barrier, 'FleetConsumerSession').start()
        self.addCleanup(patch.stopall)

    def bind(self):
        return self.ns['_fleet_bind_authority'](
            'fixture-request', 'fixture-account', 'A', {'fleet_runtime_v1': self.frame})

    def assert_local_rejection(self):
        from fleet_runtime_barrier import FleetBlocked
        with self.assertRaises(FleetBlocked):
            self.bind()
        self.assertEqual(self.calls, [])
        self.session.assert_not_called()

    def test_missing_successor_proof_rejected_before_rpc(self):
        del self.frame['succession']
        self.assert_local_rejection()

    def test_unreconciled_previous_owner_rejected(self):
        self.frame['succession']['old_owner_reconciled'] = False
        self.assert_local_rejection()

    def test_open_previous_attempt_rejected(self):
        self.frame['succession']['old_attempts_open'] = 1
        self.assert_local_rejection()

    def test_alive_process_group_rejected(self):
        self.absence.return_value = False
        self.assert_local_rejection()

    def test_misbound_previous_owner_rejected(self):
        self.frame['succession']['previous_binding']['consumer_boot_id'] = 'unrelated'
        self.assert_local_rejection()

    def test_proven_successor_sends_exact_intent_and_binding(self):
        self.bind()
        self.assertEqual(len(self.calls), 1)
        operation, payload = self.calls[0]
        self.assertEqual(operation, 'register')
        self.assertEqual(payload['registration_intent'], 'REGISTER_SUCCESSOR')
        self.assertEqual(payload['revision'], 7)
        for key, value in self.binding.items():
            self.assertEqual(payload[key], value)
        self.assertIs(payload['succession']['observed_process_group_absent'], True)
        self.session.assert_called_once()

    def test_previous_boot_response_cannot_start_session(self):
        from fleet_runtime_barrier import FleetBlocked
        def mutate(response):
            response['consumer_boot_id'] = 'B1'
            return response
        self.response_mutator = mutate
        with self.assertRaises(FleetBlocked):
            self.bind()
        self.session.assert_not_called()

    def test_wrong_successor_generation_cannot_start_session(self):
        from fleet_runtime_barrier import FleetBlocked
        def mutate(response):
            response['owner_generation'] = 1
            return response
        self.response_mutator = mutate
        with self.assertRaises(FleetBlocked):
            self.bind()
        self.session.assert_not_called()

    def test_wrong_authority_generation_cannot_start_session(self):
        from fleet_runtime_barrier import FleetBlocked
        def mutate(response):
            response['authority']['owner_generation'] = 1
            return response
        self.response_mutator = mutate
        with self.assertRaises(FleetBlocked):
            self.bind()
        self.session.assert_not_called()

    def test_old_revision_response_cannot_start_session(self):
        from fleet_runtime_barrier import FleetBlocked
        def mutate(response):
            response['revision'] = 6
            return response
        self.response_mutator = mutate
        with self.assertRaises(FleetBlocked):
            self.bind()
        self.session.assert_not_called()


class BackendProcessGenerationAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.f = ConsumerBootRegistrationContractTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.session = self.f.bind()

    def spawned(self):
        from types import SimpleNamespace
        child = self.session.spawn(lambda: SimpleNamespace(pid=123, poll=lambda: 0))
        self.session.register_child(child)
        return self.session._registered_process_group()

    def test_spawn_uses_backend_generation_without_local_uuid(self):
        from unittest.mock import patch
        with patch('uuid.uuid4', side_effect=AssertionError('must not regenerate')):
            group = self.spawned()
        self.assertEqual(group['process_generation'], 'a' * 32)

    def test_restart_preserves_persisted_generation(self):
        group = self.spawned()
        self.f.ns['_FLEET_EXECUTIONS'].clear()
        restored = self.f.bind()
        self.assertEqual(restored._registered_process_group(), group)

    def test_restart_rejects_changed_backend_generation(self):
        from fleet_runtime_barrier import FleetBlocked
        self.spawned()
        self.f.ns['_FLEET_EXECUTIONS'].clear()
        self.f.response_mutator = lambda result: result['authority'].update(process_generation='b' * 32)
        with self.assertRaisesRegex(FleetBlocked, 'process_generation_authority_mismatch'):
            self.f.bind()

    def test_revision_increment_cannot_replace_generation(self):
        from fleet_runtime_barrier import FleetBlocked
        frame = dict(self.session._authority, revision=1, process_generation='b' * 32)
        before = self.session.barrier.snapshot()
        with self.assertRaisesRegex(FleetBlocked, 'process_generation_authority_mismatch'):
            self.session.observe(frame)
        self.assertEqual(self.session.barrier.snapshot(), before)

    def test_missing_authoritative_generation_fails_closed(self):
        from fleet_runtime_barrier import FleetBlocked
        frame = dict(self.session._authority)
        del frame['process_generation']
        with self.assertRaisesRegex(FleetBlocked, 'missing_or_misbound_process_generation'):
            self.session.observe(frame)

    def test_wrong_process_group_generation_rejected_before_rpc(self):
        from fleet_runtime_barrier import FleetBlocked
        self.spawned()
        state = self.session.barrier.snapshot()
        state['attempts'][self.session.binding['attempt_id']]['process_group']['process_generation'] = 'b' * 32
        self.session.barrier._save(state)
        before = len(self.f.calls)
        with self.assertRaisesRegex(FleetBlocked, 'process_generation_authority_mismatch'):
            self.session.reconcile()
        self.assertEqual(len(self.f.calls), before)

    def test_missing_terminal_generation_rejected_before_rpc(self):
        from fleet_runtime_barrier import FleetBlocked
        self.spawned()
        state = self.session.barrier.snapshot()
        del state['attempts'][self.session.binding['attempt_id']]['process_group']['process_generation']
        self.session.barrier._save(state)
        before = len(self.f.calls)
        with self.assertRaisesRegex(FleetBlocked, 'missing_or_misbound_process_generation'):
            self.session.reconcile()
        self.assertEqual(len(self.f.calls), before)

    def test_cross_attempt_process_group_replay_rejected(self):
        from fleet_runtime_barrier import FleetBlocked
        self.spawned()
        state = self.session.barrier.snapshot()
        state['attempts'][self.session.binding['attempt_id']]['process_group']['attempt_id'] = 'other-attempt'
        self.session.barrier._save(state)
        with self.assertRaisesRegex(FleetBlocked, 'missing_or_misbound_process_group'):
            self.session.reconcile()

    def test_new_attempt_accepts_its_own_backend_generation(self):
        from pathlib import Path
        from types import SimpleNamespace
        from fleet_runtime_barrier import FleetConsumerSession
        binding = dict(self.session.binding, attempt_id='new-attempt', request_id='new-request')
        authority = dict(self.session._authority, **binding)
        authority['process_generation'] = 'b' * 32
        other = FleetConsumerSession(binding=binding, authority=authority,
            root=Path(self.f.tmp.name).resolve() / 'new-attempt', rpc=self.f.rpc)
        child = other.spawn(lambda: SimpleNamespace(pid=124, poll=lambda: 0))
        other.register_child(child)
        self.assertEqual(other._registered_process_group()['process_generation'], 'b' * 32)
        self.assertEqual(self.session._authority['process_generation'], 'a' * 32)
