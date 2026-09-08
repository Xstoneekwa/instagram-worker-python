"""Terminal reporting only: no devices, credentials, live network or mutation replay."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, MagicMock, patch

import runner
from incident_notifications import build_incident_notification_payload
import supabase_client
import account_session_resume_plan_store as store
from account_session_resume_engine import build_account_session_resume_plan
from account_session_reliability_schema import build_admin_reliability_snapshot
from runtime_incident_matrix import classify_terminal_run_failure, build_run_failure_incident_payload
from terminal_summary_projection import terminal_reason, project_terminal_summary, reconcile_terminal_progress

AID = '00000000-0000-4000-8000-000000000001'
RID = '00000000-0000-4000-8000-000000000002'


class TerminalIntegrityTests(unittest.TestCase):
    def test_cases_a_b_c_and_other_quota(self):
        for reason, target, done, evidence in [
            ('preventive_follow_restriction', 120, 68, 'preventive'),
            ('preventive_follow_restriction', 120, 71, 'preventive'),
            ('preventive_follow_restriction', 80, 67, 'preventive'),
            ('instagram_action_rate_limit', 80, 64, 'confirmed_ui'),
            ('preventive_follow_restriction', 17, 9, 'preventive'),
        ]:
            with self.subTest(reason=reason, target=target, done=done):
                reader = Mock(return_value=done)
                summary = reconcile_terminal_progress({
                    'account_id': AID, 'run_id': RID, 'reason': reason,
                    'restriction_evidence': evidence, 'follow_quota_target': target,
                    'follows_completed_count': 0, 'follow_stop_reason': 'unknown',
                    'session_counters': {'follows': 0, 'likes': 5},
                }, read_count=reader)
                reader.assert_called_once_with(account_id=AID, run_id=RID)
                self.assertEqual(summary['follows_completed_count'], done)
                self.assertEqual(summary['follow_quota_remaining'], target - done)
                self.assertEqual(summary['session_counters']['likes'], 5)
                self.assertEqual(summary['first_causal_reason'], reason)
                self.assertEqual(summary['restriction_evidence'], evidence)
                decision = classify_terminal_run_failure(exit_code=76, performance_summary=summary)
                self.assertEqual(decision.incident_type, 'instagram_account_restriction')
                self.assertEqual(decision.reason_code, reason)
                payload = build_run_failure_incident_payload(decision, account_id=AID,
                    account_username=None, run_id=RID, run_request_id='request', run_type='account_session')
                self.assertEqual(payload['dedupe_key'], f'account:{AID}:instagram_action_rate_limit:v2')
                self.assertEqual(payload['reason'], 'instagram_action_rate_limit')
                self.assertTrue(payload['metadata']['incident_only_blocker_v2'])
                self.assertEqual(payload['metadata']['first_causal_reason'], reason)
                self.assertEqual(payload['metadata']['actions_completed']['follows'], done)
                notification = build_incident_notification_payload(payload)
                self.assertEqual(notification['reason_code'], reason)
                if evidence == 'preventive':
                    self.assertIn('no explicit restriction popup is claimed', notification['text'])
                else:
                    self.assertIn('explicit Instagram action-limit surface', notification['text'])
                self.assertEqual(decision.metadata_safe['follows_completed_count'], done)
                plan = build_account_session_resume_plan(summary)
                self.assertFalse(plan['restart_allowed'])
                self.assertIn('restriction', plan['unsafe_markers'])
                self.assertEqual(plan['quota_remaining']['follow'], target - done)
                snapshot = build_admin_reliability_snapshot(summary, plan)
                self.assertEqual(snapshot['follows_completed_count'], done)

    def test_d_true_worker_failure(self):
        decision = classify_terminal_run_failure(exit_code=1, performance_summary={})
        self.assertEqual(decision.incident_type, 'run_worker_failure')
        self.assertEqual(decision.reason_code, 'worker_exit_nonzero')

    def test_future_typed_safe_stop_preserves_family(self):
        decision = classify_terminal_run_failure(exit_code=81, performance_summary={
            'first_causal_reason': 'future_safety_reason',
            'incident_type': 'future_runtime_safety_incident',
            'session_termination_class': 'partial_safe_stopped',
        })
        self.assertEqual(decision.incident_type, 'future_runtime_safety_incident')
        self.assertEqual(decision.reason_code, 'future_safety_reason')
        self.assertTrue(decision.blocking_campaign)

    def test_e_manual_stop_has_no_invented_restriction(self):
        summary = project_terminal_summary({'reason': 'manual_stop'}, persisted_follow_count=4)
        self.assertNotIn('unsafe_markers', summary)
        self.assertNotIn('restriction_evidence', summary)
        decision = classify_terminal_run_failure(exit_code=143, run_status='stopped', performance_summary=summary)
        self.assertFalse(decision.should_publish)

    def test_f_reason_monotone_and_other_safety_reasons(self):
        for reason in ['preventive_follow_restriction', 'instagram_action_rate_limit',
                       'instagram_human_confirmation_required', 'active_instagram_account_mismatch',
                       'device_offline', 'manual_stop', 'future_safety_reason']:
            self.assertEqual(terminal_reason({'first_causal_reason': reason},
                {'reason_code': 'unknown', 'failure_reason': 'unknown'}), reason)
            self.assertEqual(project_terminal_summary({'first_causal_reason': reason,
                'reason': 'unknown'})['reason'], reason)
        self.assertEqual(terminal_reason({'reason': 'unknown', 'failure_reason': 'precise'}), 'precise')
        self.assertEqual(terminal_reason({}), 'unknown')

    def test_g_h_progress_authoritative_and_true_zero(self):
        for local, canonical in [(0, 9), (99, 0), (0, 0)]:
            reader = Mock(return_value=canonical)
            out = reconcile_terminal_progress({'account_id': AID, 'run_id': RID,
                'follows_completed_count': local, 'follow_quota_target': 120}, read_count=reader)
            self.assertEqual(out['follows_completed_count'], canonical)
            self.assertEqual(out['follow_quota_remaining'], 120 - canonical)
            self.assertEqual(project_terminal_summary(out, persisted_follow_count=canonical), out)
            reader.assert_called_once()

    def test_reliability_canonical_summary_overrides_stale_plan_zero(self):
        summary = project_terminal_summary({'reason': 'preventive_follow_restriction',
            'follow_quota_target': 120}, persisted_follow_count=68)
        stale = {'quota_done': {'follow': 0}, 'quota_targets': {'follow': 120},
                 'quota_remaining': {'follow': 120}}
        snapshot = build_admin_reliability_snapshot(summary, stale)
        self.assertEqual(snapshot['follows_completed_count'], 68)
        self.assertEqual(snapshot['follow_quota_remaining'], 52)
        self.assertEqual(snapshot['first_causal_reason'], 'preventive_follow_restriction')

    def test_failed_canonical_read_is_not_a_silent_zero(self):
        out = reconcile_terminal_progress({'account_id': AID, 'run_id': RID},
                                          read_count=Mock(side_effect=TimeoutError()))
        self.assertFalse(out['terminal_progress_reconciled'])
        self.assertEqual(out['terminal_progress_error'], 'TimeoutError')
        self.assertNotIn('follows_completed_count', out)

    def test_evidence_never_invents_operator_or_ui_confirmation(self):
        for evidence, expected in [(None, 'preventive'), ('confirmed_ui', 'preventive'),
                                   ('corroborated_preventive', 'corroborated_preventive')]:
            out = project_terminal_summary({'reason': 'preventive_follow_restriction',
                                            'restriction_evidence': evidence})
            self.assertEqual(out['restriction_evidence'], expected)
        self.assertNotIn('restriction_evidence', project_terminal_summary({'reason': 'instagram_action_rate_limit'}))

    def test_read_receipts_matches_canonical_distinct_count_no_replay(self):
        for rows, expected in [([{'username': '@Same'}, {'username': 'same'}, {'username': 'other'}], 2),
                               ([], 0), ([{'username': None}], 0)]:
            response = MagicMock()
            response.__enter__.return_value.headers = {'Content-Range': f'*/{len(rows)}'}
            response.__enter__.return_value.read.return_value = json.dumps(rows).encode()
            with patch.object(supabase_client, '_base_url', return_value='https://example.invalid'), \
                 patch.object(supabase_client, '_service_key', return_value='test-placeholder'), \
                 patch.object(supabase_client.request, 'urlopen', return_value=response) as req:
                self.assertEqual(supabase_client.count_persisted_run_follows(account_id=AID, run_id=RID), expected)
            req.assert_called_once()
            sent = req.call_args.args[0]
            self.assertEqual(sent.method, 'GET')
            self.assertEqual(sent.headers['Prefer'], 'count=exact')
            self.assertIn(RID, sent.full_url)
            self.assertIn('success%2Cpartial', sent.full_url)
            self.assertEqual(req.call_args.kwargs['timeout'], 3.0)
        for header in ['0-0/*', '68', '0-0/1200']:
            response.__enter__.return_value.headers = {'Content-Range': header}
            response.__enter__.return_value.read.return_value = b'[]'
            with patch.object(supabase_client, '_base_url', return_value='https://example.invalid'), \
                 patch.object(supabase_client, '_service_key', return_value='test-placeholder'), \
                 patch.object(supabase_client.request, 'urlopen', return_value=response):
                with self.assertRaises(RuntimeError):
                    supabase_client.count_persisted_run_follows(account_id=AID, run_id=RID)

    def test_runner_terminal_boundary_persists_canonical_progress_once(self):
        with patch.object(runner, '_DEFER_TERMINAL_RUN_STATUS_UNTIL_CLEANUP', False), \
             patch.object(runner, '_pending_deferred_follow_action_log_count', return_value=0), \
             patch.object(supabase_client, 'count_persisted_run_follows', return_value=68) as read, \
             patch.object(runner, '_timed_safe_supabase_call', return_value={'_supabase_call_ok': True}) as persist, \
             patch.object(runner, '_orf_publish_event'), patch.object(runner, '_orf_heartbeat_worker'), \
             patch.object(runner, 'log'):
            runner._update_run_status_safe(run_id=RID, status='failed', totals={'total': 0},
                performance_summary={'account_id': AID, 'run_id': RID,
                    'reason': 'preventive_follow_restriction', 'follow_quota_target': 120})
        read.assert_called_once()
        persist.assert_called_once()
        summary = persist.call_args.kwargs['performance_summary']
        self.assertEqual(summary['session_counters']['follows'], 68)
        self.assertEqual(summary['follow_quota_remaining'], 52)

    def test_resume_store_preserves_lineage_and_block_with_progress(self):
        summary = project_terminal_summary({'reason': 'preventive_follow_restriction',
            'follow_quota_target': 120}, persisted_follow_count=68)
        with patch.object(supabase_client, 'load_run_row', return_value={'performance_summary': summary}), \
             patch.object(store, 'load_resume_plan', return_value={'plan': {'root_business_session_id': 'root',
                'quota_remaining': {'follow': 120, 'unfollow': 80, 'total': 200}}}), \
             patch.object(store, '_patch_plan_by_run_id', return_value={'persisted': True}) as write:
            store.record_terminal_failure(run_id=RID, incident_type='instagram_account_restriction',
                                           reason_code='preventive_follow_restriction')
        write.assert_called_once()
        body = write.call_args.args[1]
        self.assertFalse(body['restart_allowed'])
        self.assertEqual(body['plan']['root_business_session_id'], 'root')
        self.assertEqual(body['plan']['quota_done']['follow'], 68)
        self.assertEqual(body['plan']['quota_targets']['follow'], 120)
        self.assertEqual(body['plan']['quota_remaining']['total'], 132)

    def test_actual_rotation_exception_enriches_quota_without_consuming_exception(self):
        import account_session_orchestrator as orchestration
        tree = ast.parse(Path(orchestration.__file__).read_text())
        boundary = next(n for n in ast.walk(tree) if isinstance(n, ast.Try)
            and len(n.body) == 1 and isinstance(n.body[0], ast.Assign)
            and any(isinstance(x, ast.Name) and x.id == 'run_followers_list_engine_session'
                    for x in ast.walk(n.body[0])) and n.handlers)
        exc = RuntimeError('safety')
        exc.summary = {'reason': 'preventive_follow_restriction'}
        engine = Mock(side_effect=exc)
        env = dict(run_followers_list_engine_session=engine, d=object(), call_kwargs={},
                   account_id=AID, run_id=RID, global_follow_goal=120)
        with self.assertRaises(RuntimeError) as raised:
            exec(compile(ast.Module(body=[boundary], type_ignores=[]), '<rotation-boundary>', 'exec'), env)
        self.assertIs(raised.exception, exc)
        engine.assert_called_once()
        self.assertEqual(exc.summary['follow_quota_target'], 120)
        self.assertEqual(exc.summary['reason'], 'preventive_follow_restriction')

    def test_actual_finally_reports_exception_reason_before_scan(self):
        # Execute the actual finally reporting block with device/cleanup paths
        # inert. This tests exception unwinding, not a hand-written mirror.
        tree = ast.parse(Path(runner.__file__).read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == '_run_followers_list_engine_session')
        final = next(n.finalbody for n in ast.walk(function) if isinstance(n, ast.Try)
                     and n.finalbody and isinstance(n.finalbody[0], ast.Assign)
                     and any(isinstance(x, ast.Name) and x.id == '_terminal_exception_summary'
                             for x in ast.walk(n.finalbody[0])))
        summary, logs = {}, []
        exc = RuntimeError('safety')
        exc.summary = {'reason': 'preventive_follow_restriction'}
        namespace = {'sys': sys, 'terminal_reason': terminal_reason,
            '_followers_session_summary': summary, '_followers_loop_finally_stop': '',
            '_followers_loop_finally_status': 'loop_exited', '_followers_loop_finally_error': '',
            'get_followers_engine_stop_reason': lambda: '', 'target_followers_resume_controller': None,
            'is_follow_target_rotation_pending': lambda **kw: False,
            '_publish_followers_session_summary': lambda **kw: summary.update(kw),
            '_emit_target_scan_completed': lambda: logs.append(terminal_reason(summary)),
            'log': lambda *args, **kw: logs.append(kw.get('stop_reason')),
            'processed': 7, 'follows_completed_count': 7, 'source_profile_username': 'source'}
        try:
            raise exc
        except RuntimeError:
            exec(compile(ast.Module(body=final, type_ignores=[]), '<actual-runner-finally>', 'exec'), namespace)
        self.assertEqual(summary['follow_stop_reason'], 'preventive_follow_restriction')
        self.assertEqual(logs, ['preventive_follow_restriction', 'preventive_follow_restriction'])


if __name__ == '__main__':
    unittest.main()
