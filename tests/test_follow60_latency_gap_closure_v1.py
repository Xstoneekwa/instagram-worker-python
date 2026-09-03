from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest import mock

import instagram_navigation as nav


class DiagnosticProbeEliminationTests(unittest.TestCase):
    def test_diagnostic_log_issues_zero_device_reads_or_gestures(self):
        device = mock.MagicMock()
        with mock.patch.object(nav, '_followers_current_pkg_activity', side_effect=AssertionError('redundant probe')) as probe, \
             mock.patch.object(nav, 'log') as log:
            for _ in range(100):
                nav._followers_log_scroll_or_swipe_about_to_run(device, source_function='forward', reason='resume')
        probe.assert_not_called()
        self.assertEqual(device.mock_calls, [])
        self.assertEqual(log.call_count, 100)
        self.assertIsNone(log.call_args.kwargs['current_package'])
        self.assertIsNone(log.call_args.kwargs['current_activity'])
        self.assertEqual(log.call_args.kwargs['foreground_observation'], 'not_sampled_diagnostic_only')

    def test_logging_after_navigation_cannot_republish_old_proof(self):
        device = mock.MagicMock()
        with mock.patch.object(nav, 'log') as log:
            for package in ('com.instagram.android', 'com.android.launcher', 'other.clone'):
                device.app_current.return_value = {'package': package, 'activity': 'changed'}
                nav._followers_log_scroll_or_swipe_about_to_run(device, source_function='forward', reason='resume')
                self.assertIsNone(log.call_args.kwargs['current_package'])
                self.assertFalse(log.call_args.kwargs['foreground_device_probe'])
        device.app_current.assert_not_called()

    def test_last_safety_foreground_guard_still_reads_device(self):
        device = mock.MagicMock()
        device.app_current.return_value = {'package': 'com.android.launcher', 'activity': 'Launcher'}
        with mock.patch.object(nav, 'log'):
            result = nav._followers_current_pkg_activity(device)
        device.app_current.assert_called_once()
        self.assertEqual(result['current_package'], 'com.android.launcher')

    def test_post_tap_lock_still_blocks_scroll_independently_of_log(self):
        with mock.patch.object(nav, 'POST_TAP_FOLLOWERS_DETECTION_IN_PROGRESS', True), mock.patch.object(nav, 'log'):
            self.assertTrue(nav._followers_abort_scroll_if_post_tap_lock('resume'))

    def test_observability_and_safety_are_not_interchangeable(self):
        tree = ast.parse(Path(nav.__file__).read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == '_followers_log_scroll_or_swipe_about_to_run')
        calls = [ast.unparse(n.func) for n in ast.walk(function) if isinstance(n, ast.Call)]
        self.assertEqual(calls, ['log'])
        self.assertFalse(any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(function)))

    def test_runner_and_return_ct_share_same_terminal_like_binding(self):
        root = Path(nav.__file__).parent
        tree = ast.parse((root / 'runner.py').read_text())
        gates = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == 'like_terminal_safe' and isinstance(value, ast.Compare):
                        gates.append(ast.unparse(value))
        self.assertEqual(len(gates), 1)
        self.assertIn('_post_follow_like_terminal_binding', gates[0])


if __name__ == '__main__':
    unittest.main()
