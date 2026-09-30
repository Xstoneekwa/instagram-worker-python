"""Safety review contracts; failing gates deliberately expose incomplete patch paths."""
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import urlparse, parse_qs
import runner
import supabase_client
from terminal_summary_projection import reconcile_terminal_progress
from runtime_incident_matrix import classify_terminal_run_failure, build_run_failure_incident_payload
from account_session_resume_engine import build_account_session_resume_plan

A = '00000000-0000-4000-8000-000000000001'
R = '00000000-0000-4000-8000-000000000002'
OLD = '00000000-0000-4000-8000-000000000003'
OTHER = '00000000-0000-4000-8000-000000000004'

class TerminalPatchReview(unittest.TestCase):
    def test_current_run_only_and_repeat_projection_readonly(self):
        # Same session / old attempt has a different canonical run, as the
        # canonical totals SQL is run-scoped, not a business-session sum.
        rows = []
        for aid, rid, count, kind, status in [
            (A, OLD, 50, 'follow_verified_persisted_v1', 'success'),
            (A, R, 68, 'follow_verified_persisted_v1', 'success'),
            (OTHER, R, 7, 'follow_verified_persisted_v1', 'success'),
            (A, R, 8, 'post_like_success', 'success'),
            (A, R, 9, 'follow_verified_persisted_v1', 'failed'),
        ]:
            rows += [dict(account_id=aid, run_id=rid, username=f'{rid}_{i}',
                          event_type=kind, event_status=status) for i in range(count)]
        rows.append({**rows[50], 'username': '@' + rows[50]['username'].upper()})
        original = json.dumps(rows, sort_keys=True)
        def fake_http(req, **kwargs):
            self.assertEqual(req.method, 'GET')
            query = parse_qs(urlparse(req.full_url).query)
            self.assertEqual(query['account_id'], ['eq.' + A])
            self.assertEqual(query['run_id'], ['eq.' + R])
            self.assertEqual(query['event_status'], ['in.(success,partial)'])
            self.assertEqual(query['event_type'], ['in.(follow_verified,follow_verified_persisted_v1)'])
            selected = [x for x in rows if x['account_id'] == query['account_id'][0][3:]
                and x['run_id'] == query['run_id'][0][3:]
                and x['event_status'] in query['event_status'][0][4:-1].split(',')
                and x['event_type'] in query['event_type'][0][4:-1].split(',')]
            response = MagicMock()
            response.__enter__.return_value.headers = {'Content-Range': f'0-{len(selected)-1}/{len(selected)}'}
            response.__enter__.return_value.read.return_value = json.dumps(selected).encode()
            return response
        out = dict(account_id=A, run_id=R, follow_quota_target=120,
                   reason='preventive_follow_restriction')
        with patch.object(supabase_client, '_base_url', return_value='https://example.invalid'), \
             patch.object(supabase_client, '_service_key', return_value='test-placeholder'), \
             patch.object(supabase_client.request, 'urlopen', side_effect=fake_http) as http:
            keys = []
            for _ in range(3):
                out = reconcile_terminal_progress(out, read_count=supabase_client.count_persisted_run_follows)
                self.assertEqual(out['follows_completed_count'], 68)
                self.assertEqual(out['follow_quota_remaining'], 52)
                decision = classify_terminal_run_failure(exit_code=76, performance_summary=out)
                incident = build_run_failure_incident_payload(decision, account_id=A,
                    account_username=None, run_id=R, run_request_id='request', run_type='account_session')
                self.assertEqual(incident['metadata']['follows_completed_count'], 68)
                keys.append(incident['dedupe_key'])
            self.assertEqual(http.call_count, 3)
            self.assertEqual(len(set(keys)), 1)
        self.assertEqual(json.dumps(rows, sort_keys=True), original)

    def test_resume_attempt_allowance_is_remaining_not_business_session_sum(self):
        from account_session_orchestrator import _authorized_resume_follow_quota
        # Existing validated resume carries 120 - 68, not the original 120.
        target = _authorized_resume_follow_quota({'phases_to_run': {'follow': True},
                                                'quota_remaining': {'follow': 52}})
        self.assertEqual(target, 52)
        out = reconcile_terminal_progress(dict(account_id=A, run_id=R,
            follow_quota_target=target), read_count=Mock(return_value=9))
        self.assertEqual(out['follows_completed_count'], 9)
        self.assertEqual(out['follow_quota_remaining'], 43)

    def test_crash_cause_independent_of_progress_and_historical_markers(self):
        out = reconcile_terminal_progress(dict(account_id=A, run_id=R,
            root_failure_code='technical_failure', follow_quota_target=120,
            history={'reason': 'preventive_follow_restriction'}, unsafe_markers=['restriction']),
            read_count=Mock(return_value=68))
        decision = classify_terminal_run_failure(exit_code=1, performance_summary=out)
        self.assertEqual(decision.incident_type, 'run_worker_failure')
        self.assertEqual(decision.reason_code, 'technical_failure')
        self.assertEqual(out['follows_completed_count'], 68)

    def test_real_runner_technical_exception_must_publish_terminal_progress(self):
        tree = ast.parse(Path(runner.__file__).read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        handler = next(h for n in ast.walk(main) if isinstance(n, ast.Try) for h in n.handlers
            if any(isinstance(x, ast.Name) and x.id == 'PreDeviceControlPlaneUnavailable'
                   for x in ast.walk(h)))
        # Execute the actual outer exception handler, with no main/device startup.
        fn = ast.FunctionDef(name='review_boundary', args=ast.arguments(posonlyargs=[], args=[],
            kwonlyargs=[], kw_defaults=[], defaults=[]), decorator_list=[], body=[ast.Try(
                body=[ast.Raise(exc=ast.Name(id='review_crash', ctx=ast.Load()), cause=None)],
                handlers=[handler], orelse=[], finalbody=[])])
        env = dict(vars(runner)); env['review_crash'] = RuntimeError('technical failure')
        env['dm_certification'] = __import__('dm_certification_admission')
        self.assertFalse(env['dm_certification'].is_present())
        env['_CURRENT_RUN_ID'] = R; env['_CURRENT_ACCOUNT_ID'] = A
        terminal = Mock(); env['_update_run_status_safe'] = terminal
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<real-handler>', 'exec'), env)
        with patch.object(supabase_client, 'count_persisted_run_follows', return_value=68):
            with self.assertRaises(RuntimeError):
                env['review_boundary']()
        self.assertTrue(terminal.called, 'Technical exception bypasses terminal reporting; persisted progress cannot reach the new projection')

    def test_known_safe_stop_must_not_be_worker_failure(self):
        for reason, code in [('follow_review_popup_unhandled', 99),
                             ('post_follow_return_ct_failed', 97),
                             ('already_connected_safe_stop', 98),
                             ('host_storage_critical', 78)]:
            with self.subTest(reason=reason):
                decision = classify_terminal_run_failure(exit_code=code, run_type='account_session',
                    performance_summary={'follow_stop_reason': reason, **(
                        {'session_termination_class': 'partial_safe_stopped'} if code in (97, 98)
                        else {'session_termination_class': 'non_recoverable_failure'})})
                self.assertNotEqual(decision.incident_type, 'run_worker_failure')

    def test_explicit_supported_safety_and_manual_mappings(self):
        for reason, expected in [('preventive_follow_restriction', 'instagram_account_restriction'),
            ('instagram_action_rate_limit', 'instagram_account_restriction'),
            ('instagram_human_confirmation_required', 'instagram_human_confirmation_required'),
            ('active_instagram_account_mismatch', 'active_instagram_account_mismatch'),
            ('device_offline', 'run_device_unavailable')]:
            with self.subTest(reason=reason):
                out = reconcile_terminal_progress(dict(account_id=A, run_id=R,
                    reason=reason, follow_quota_target=120), read_count=Mock(return_value=68))
                decision = classify_terminal_run_failure(exit_code=1, performance_summary=out)
                self.assertEqual(decision.incident_type, expected)
                self.assertEqual(out['follows_completed_count'], 68)
                self.assertFalse(build_account_session_resume_plan(out)['restart_allowed'])
        self.assertFalse(classify_terminal_run_failure(exit_code=143, run_status='stopped',
            performance_summary={'reason': 'manual_stop'}).should_publish)

if __name__ == '__main__':
    unittest.main()
