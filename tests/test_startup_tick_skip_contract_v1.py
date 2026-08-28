"""Offline real-startup regression: all network, queue and service effects mocked."""
import ast
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import account_run_request_consumer as consumer
import auto_restart_dispatcher_tick as tick
import phonefarm_runtime_control as runtime

ROOT = Path(__file__).resolve().parents[1]
MARKER = b'phonefarm-auto-restart-startup-skip-v1\n'


class StartupSkipContractTests(unittest.TestCase):
    def exercise_startup(self, directory, *, marker=True, reliability=True, delay=0):
        """Execute run_forever, not an imitation; stop after two natural intervals."""
        token = Path(directory) / 'auto-restart-skip-startup-tick.once'
        if marker is not None:
            token.write_bytes(MARKER if marker is True else marker)
        clock = [100.0]
        signals = {}
        observed = []
        cfg = consumer.DispatcherConfig(
            enabled=True, health_only=False, launch_enabled=True,
            worker_id='run-dispatcher:offline', poll_seconds=5.0,
            lease_seconds=120, heartbeat_seconds=20,
            allowed_run_types=['account_session'], test_account_ids=set(),
            subprocess_timeout_seconds=7200, require_assignment=False,
            enforce_assignment_window=False,
        )
        breaker = Mock()
        breaker.snapshot = SimpleNamespace(dispatch_allowed=False)
        def healthy():
            clock[0] += delay
            breaker.snapshot = SimpleNamespace(dispatch_allowed=True, state='HEALTHY', generation=1)
            return breaker.snapshot, True
        breaker.record_probe_success.side_effect = healthy
        breaker.probe_due.return_value = True
        def sleep(seconds):
            clock[0] += seconds
            if clock[0] >= 225 + delay:
                signals[signal.SIGTERM](signal.SIGTERM, None)
        def record_tick(**kwargs):
            observed.append((clock[0], token.exists()))
            return {'ok': True}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {
                'RUN_CONTROL_DISPATCHER_RUN_DIR': str(directory),
                tick.AUTO_RESTART_STARTUP_SKIP_ONCE_FILE_ENV: str(token),
            }))
            stack.enter_context(patch.object(consumer, 'log'))
            stack.enter_context(patch.object(consumer.time, 'monotonic', side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(consumer.time, 'sleep', side_effect=sleep))
            stack.enter_context(patch.object(consumer.signal, 'signal', side_effect=lambda sig, fn: signals.update({sig: fn})))
            stack.enter_context(patch.object(consumer, '_dispatcher_storage_gate', return_value=(True, {})))
            stack.enter_context(patch.object(consumer, '_control_plane_reliability_v1_enabled', return_value=reliability))
            stack.enter_context(patch.object(consumer.control_plane_health, 'CircuitBreaker', return_value=breaker))
            probe = stack.enter_context(patch.object(consumer.supabase_client, 'probe_control_plane_v1'))
            stack.enter_context(patch.object(consumer.orphan_run_reconciliation, 'reconcile_orphaned_active_runs'))
            stack.enter_context(patch.object(consumer, 'reconcile_requests_with_terminal_runs'))
            stack.enter_context(patch.object(consumer, 'evaluate_launch_mode_startup_preflight_with_retries', return_value={'ok': True}))
            stack.enter_context(patch.object(consumer.follow_persistence_receipt_replay, 'replay_verified_receipts', return_value={'ok': True}))
            stack.enter_context(patch.object(consumer.deferred_projection_outbox, 'drain'))
            stack.enter_context(patch.object(consumer, '_heartbeat'))
            claim = stack.enter_context(patch.object(consumer, '_claim_next_dispatch_request', return_value=(None, {})))
            stack.enter_context(patch.object(consumer, 'run_auto_restart_dispatcher_tick', side_effect=record_tick))
            code = consumer.run_forever(cfg)
        return code, observed, token, probe.call_count, claim.call_count

    def test_valid_skip_precedes_recovery_tick_then_natural_cadence(self):
        with tempfile.TemporaryDirectory() as d:
            code, observed, token, _, _ = self.exercise_startup(d)
            self.assertEqual(code, 0)
            self.assertFalse(token.exists())
            self.assertEqual(observed, [(160.0, False), (220.0, False)])

    def test_long_startup_does_not_expire_skip_before_loop(self):
        with tempfile.TemporaryDirectory() as d:
            code, observed, _, _, _ = self.exercise_startup(d, delay=180)
            self.assertEqual(code, 0)
            self.assertEqual(observed, [(340.0, False), (400.0, False)])

    def test_restart_consumed_token_is_not_reused(self):
        with tempfile.TemporaryDirectory() as d:
            self.exercise_startup(d)
            _, observed, _, _, _ = self.exercise_startup(d, marker=None)
            self.assertEqual(observed, [(100.0, False), (160.0, False), (220.0, False)])

    def test_no_skip_preserves_startup_with_and_without_reliability(self):
        for enabled in (True, False):
            with self.subTest(reliability=enabled), tempfile.TemporaryDirectory() as d:
                _, observed, _, _, _ = self.exercise_startup(d, marker=None, reliability=enabled)
                self.assertEqual(observed, [(100.0, False), (160.0, False), (220.0, False)])

    def test_skip_without_reliability_also_suppresses_initial_tick(self):
        with tempfile.TemporaryDirectory() as d:
            _, observed, _, _, _ = self.exercise_startup(d, reliability=False)
            self.assertEqual(observed, [(160.0, False), (220.0, False)])

    def test_malformed_or_obsolete_marker_blocks_before_probe_claim_or_tick(self):
        for content in (b'', b'phonefarm-auto-restart-startup-skip-v0\n', b'x' * 1024):
            with self.subTest(content=content[:40]), tempfile.TemporaryDirectory() as d:
                code, observed, token, probes, claims = self.exercise_startup(d, marker=content)
                self.assertEqual(code, 5)
                self.assertEqual((observed, probes, claims), ([], 0, 0))
                self.assertEqual(token.read_bytes(), content)

    def test_repeated_initialization_uses_same_startup_decision(self):
        for result, expected in (({'consumed': True, 'cleanup_ok': True}, 100),
                                 ({'consumed': False, 'reason': 'guard_absent'}, 0)):
            state = {}
            with patch.object(consumer, 'consume_auto_restart_startup_tick_skip_once', return_value=result) as consume, patch.object(consumer, 'log'):
                self.assertEqual(consumer.initialize_auto_restart_tick_state(worker_id='offline', now_monotonic=100, startup_state=state), expected)
                self.assertEqual(consumer.initialize_auto_restart_tick_state(worker_id='offline', now_monotonic=200, startup_state=state), expected)
                consume.assert_called_once_with()

    def test_cleanup_error_blocks_startup(self):
        with patch.object(consumer, 'consume_auto_restart_startup_tick_skip_once', return_value={'consumed': True, 'cleanup_ok': False}):
            with self.assertRaisesRegex(RuntimeError, 'cleanup_failed'):
                consumer.initialize_auto_restart_tick_state(worker_id='offline')

    def test_shared_runtime_path_and_no_other_service_consumption(self):
        source = (ROOT / 'phonefarm_runtime_control.py').read_text()
        self.assertIn('env["RUN_CONTROL_DISPATCHER_RUN_DIR"] = str(paths.run_dir / "run-control-dispatcher")', source)
        wrapper = (ROOT / 'scripts/run_control_dispatcher_service.sh').read_text()
        self.assertIn('export RUN_CONTROL_DISPATCHER_RUN_DIR="$RUN_DIR"', wrapper)
        self.assertIn('export AUTO_RESTART_SKIP_STARTUP_TICK_ONCE_FILE="$AUTO_RESTART_STARTUP_SKIP_ONCE_FILE"', wrapper)
        self.assertIn('startup_tick_skip_release_local_path_forbidden', wrapper)
        for name in ('device_heartbeat_service.sh', 'incident_notifier_service.sh'):
            path = ROOT / 'scripts' / name
            self.assertTrue(path.exists(), name)
            self.assertNotIn('consume_auto_restart_startup_tick_skip_once', path.read_text())
            self.assertNotIn('_prepare_auto_restart_startup_skip_once', path.read_text())

    def test_canonical_wrapper_arm_and_consumer_share_one_file(self):
        with tempfile.TemporaryDirectory() as d:
            paths = runtime.RuntimePaths(Path(d)/'current', Path(d)/'releases', Path(d)/'runtime', Path(d)/'legacy')
            root = runtime.RuntimeRoot(True, 'valid', '/offline', '/offline', 'a' * 40)
            with patch.dict(os.environ, {}, clear=True):
                env = runtime._component_env(paths, root, 'dispatcher')
            run_dir = Path(env['RUN_CONTROL_DISPATCHER_RUN_DIR'])
            token = run_dir / 'auto-restart-skip-startup-tick.once'
            wrapper = (ROOT / 'scripts/run_control_dispatcher_service.sh').read_text()
            # Execute ONLY the pure arm function in a temporary directory.
            # No wrapper entrypoint, service command, restart or tick is run.
            arm = wrapper.split('_prepare_auto_restart_startup_skip_once() {', 1)[1].split('\n}\n', 1)[0]
            script = 'set -eu\n_prepare_auto_restart_startup_skip_once() {' + arm + '\n}\n_prepare_auto_restart_startup_skip_once\n'
            env['AUTO_RESTART_STARTUP_SKIP_ONCE_FILE'] = str(token)
            subprocess.run(['/bin/bash', '-c', script], env=env, check=True, capture_output=True)
            self.assertEqual(token.read_bytes(), tick.AUTO_RESTART_STARTUP_SKIP_MARKER)
            env[tick.AUTO_RESTART_STARTUP_SKIP_ONCE_FILE_ENV] = str(token)
            with patch.dict(os.environ, env, clear=True):
                self.assertTrue(tick.consume_auto_restart_startup_tick_skip_once()['consumed'])
                self.assertEqual(tick.consume_auto_restart_startup_tick_skip_once()['reason'], 'guard_absent')
            for command in ('restart', 'resume'):
                branch = wrapper.split('\n  ' + command + ')', 1)[1].split('\n    ;;', 1)[0]
                self.assertEqual(branch.count('_prepare_auto_restart_startup_skip_once'), 1)
                self.assertLess(branch.index('_assert_deployment_zero_gate'), branch.index('_prepare_auto_restart_startup_skip_once'))

    def test_aged_pending_marker_is_one_shot_not_a_recurrent_delay(self):
        # V1 has no expiry or generation payload. Do not invent a TTL. An old
        # still-pending valid marker suppresses once and cannot poison restart.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'pending'
            path.write_bytes(tick.AUTO_RESTART_STARTUP_SKIP_MARKER)
            os.utime(path, (1, 1))
            self.assertTrue(tick.consume_auto_restart_startup_tick_skip_once(str(path))['consumed'])
            self.assertEqual(tick.consume_auto_restart_startup_tick_skip_once(str(path))['reason'], 'guard_absent')

    def test_replacement_between_stat_and_claim_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'pending'
            path.write_bytes(tick.AUTO_RESTART_STARTUP_SKIP_MARKER)
            replace = os.replace
            def race(source, dest):
                alternate = Path(d) / 'alternate'
                alternate.write_bytes(tick.AUTO_RESTART_STARTUP_SKIP_MARKER)
                replace(alternate, source)
                replace(source, dest)
            with patch.object(tick.os, 'replace', side_effect=race):
                result = tick.consume_auto_restart_startup_tick_skip_once(str(path))
            self.assertFalse(result['consumed'])
            self.assertEqual(result['reason'], 'guard_changed_during_consume')

    def test_two_simultaneous_claimants_consume_exactly_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'pending'
            path.write_bytes(MARKER)
            barrier = threading.Barrier(2)
            replace = os.replace
            def simultaneous(source, dest):
                barrier.wait(timeout=5)
                replace(source, dest)
            with patch.object(tick.os, 'replace', side_effect=simultaneous), ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(tick.consume_auto_restart_startup_tick_skip_once, str(path)) for _ in range(2)]
                results = [f.result(timeout=10) for f in futures]
            self.assertEqual(sum(bool(r['consumed']) for r in results), 1)
            self.assertFalse(path.exists())

    def test_runtime_cannot_select_an_alternate_token(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'different.once'
            path.write_bytes(tick.AUTO_RESTART_STARTUP_SKIP_MARKER)
            with patch.dict(os.environ, {'RUN_CONTROL_DISPATCHER_RUN_DIR': d, tick.AUTO_RESTART_STARTUP_SKIP_ONCE_FILE_ENV: str(path)}):
                self.assertEqual(tick.consume_auto_restart_startup_tick_skip_once()['reason'], 'guard_shared_path_mismatch')
            self.assertTrue(path.exists())

    def test_symlink_is_never_consumed(self):
        with tempfile.TemporaryDirectory() as d:
            original = Path(d) / 'original'
            original.write_bytes(tick.AUTO_RESTART_STARTUP_SKIP_MARKER)
            link = Path(d) / 'link'
            link.symlink_to(original)
            self.assertEqual(tick.consume_auto_restart_startup_tick_skip_once(str(link))['reason'], 'guard_not_regular_file')
            self.assertTrue(original.exists())

    def test_claimed_leftover_is_not_a_pending_token(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'pending'
            leftover = Path(str(path) + '.consumed.123')
            leftover.write_bytes(tick.AUTO_RESTART_STARTUP_SKIP_MARKER)
            self.assertEqual(tick.consume_auto_restart_startup_tick_skip_once(str(path))['reason'], 'guard_absent')
            self.assertTrue(leftover.exists())

    def test_every_startup_tick_is_dominated_by_shared_initialization(self):
        tree = ast.parse((ROOT / 'account_run_request_consumer.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_forever')
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        initializers = [n for n in calls if n.func.id == 'initialize_auto_restart_tick_state']
        self.assertEqual(len(initializers), 1)
        self.assertTrue(all(initializers[0].lineno < n.lineno for n in calls if n.func.id == 'run_auto_restart_dispatcher_tick'))
        body = ast.get_source_segment((ROOT / 'account_run_request_consumer.py').read_text(), fn)
        self.assertNotIn('recovery_tick_monotonic or initialize', body)
        self.assertIn('if became_healthy and not skip_startup_tick:', body)
