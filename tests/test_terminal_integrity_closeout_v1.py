import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import runner
import supabase_client
import account_run_request_consumer as consumer
import account_session_resume_plan_store as store
from account_session_reliability_schema import build_admin_reliability_snapshot
from account_session_resume_engine import build_account_session_resume_plan
from terminal_summary_projection import project_terminal_summary
from runtime_incident_matrix import classify_terminal_run_failure

A='00000000-0000-4000-8000-000000000001'
R='00000000-0000-4000-8000-000000000002'

class CloseoutTests(unittest.TestCase):
    def test_real_handler_preserves_exception_traceback_progress_and_technical_family(self):
        main=next(n for n in ast.parse(Path(runner.__file__).read_text()).body
                  if isinstance(n,ast.FunctionDef) and n.name=='main')
        handler=next(h for n in ast.walk(main) if isinstance(n,ast.Try) for h in n.handlers
                     if any(isinstance(x,ast.Name) and x.id=='PreDeviceControlPlaneUnavailable' for x in ast.walk(h)))
        fn=ast.FunctionDef(name='boundary',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),
            decorator_list=[],body=[ast.Try(body=[ast.Raise(exc=ast.Name(id='failure',ctx=ast.Load()),cause=None)],handlers=[handler],orelse=[],finalbody=[])])
        for count in (68,0):
            failure=RuntimeError('technical failure'); failure.summary={'follow_quota_target':120,
                'history':{'reason':'preventive_follow_restriction'}}
            env=dict(vars(runner),failure=failure,_CURRENT_ACCOUNT_ID=A,_CURRENT_RUN_ID=R)
            exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'<actual-technical-handler>','exec'),env)
            with patch.object(runner,'_DEFER_TERMINAL_RUN_STATUS_UNTIL_CLEANUP',True), \
                 patch.object(runner,'_RUNNER_SESSION_CLEANUP_COMPLETE',False), \
                 patch.object(runner,'_pending_deferred_follow_action_log_count',return_value=0), \
                 patch.object(runner,'_timed_safe_supabase_call',return_value={'_supabase_call_ok':True}) as write, \
                 patch.object(runner,'_orf_publish_event'),patch.object(runner,'_orf_heartbeat_worker'), \
                 patch.object(supabase_client,'count_persisted_run_follows',return_value=count) as reader:
                try:
                    env['boundary']()
                except RuntimeError as caught:
                    self.assertIs(caught,failure)
                    frames=[];tb=caught.__traceback__
                    while tb is not None:
                        frames.append(tb.tb_frame.f_code.co_name);tb=tb.tb_next
                    self.assertIn('boundary',frames)
                else:
                    self.fail('Original technical exception was swallowed')
            reader.assert_called_once_with(account_id=A,run_id=R); write.assert_called_once()
            summary=write.call_args.kwargs['performance_summary']
            self.assertEqual(summary['follows_completed_count'],count)
            self.assertEqual(summary['follow_quota_remaining'],120-count)
            self.assertEqual(classify_terminal_run_failure(exit_code=1,performance_summary=summary).incident_type,'run_worker_failure')
            self.assertNotIn('restriction_evidence',summary)
            self.assertEqual(build_admin_reliability_snapshot(summary,{})['follows_completed_count'],count)

    def test_checkpoint_precedence_and_real_mismatch(self):
        for reason,kind in [('checkpoint','account_login_required'),('login_challenge','account_login_required'),
                            ('instagram_human_confirmation_required','instagram_human_confirmation_required'),
                            ('active_instagram_account_mismatch','active_instagram_account_mismatch')]:
            summary={'reason':reason,'account_identity_failure_reason':reason}
            decision=classify_terminal_run_failure(exit_code=75,performance_summary=summary)
            self.assertEqual(decision.incident_type,kind)
            self.assertEqual(decision.reason_code,reason)
            self.assertTrue(decision.blocking_campaign)
            self.assertFalse(build_account_session_resume_plan(summary)['restart_allowed'])
        unknown=classify_terminal_run_failure(exit_code=75,performance_summary={'account_identity_failure_reason':'identity_probe_failed'})
        self.assertEqual(unknown.incident_type,'run_identity_verification_failed')

    def test_required_safety_is_contextual_not_exit_only(self):
        for reason,cls,kind in [('follow_review_popup_unhandled','non_recoverable_failure','runtime_unknown_blocked'),
            ('post_follow_return_ct_failed','partial_safe_stopped','runtime_unknown_blocked'),
            ('already_connected_safe_stop','partial_safe_stopped','runtime_unknown_blocked'),
            ('host_storage_critical','non_recoverable_failure','HOST_STORAGE_CRITICAL')]:
            s=project_terminal_summary({'reason':reason,'session_termination_class':cls},persisted_follow_count=68)
            for code in (1,96,97,98,99):
                d=classify_terminal_run_failure(exit_code=code,performance_summary=s)
                self.assertEqual(d.incident_type,kind);self.assertEqual(d.reason_code,reason)
                self.assertTrue(d.blocking_campaign);self.assertTrue(d.requires_operator_review)
                self.assertEqual(s['session_termination_class'],cls)
                self.assertNotIn('restriction_evidence',s)
            self.assertFalse(build_account_session_resume_plan(s)['restart_allowed'])
        for code in (96,97,98,99):
            self.assertEqual(classify_terminal_run_failure(exit_code=code,performance_summary={}).incident_type,'run_worker_failure')
        self.assertEqual(classify_terminal_run_failure(exit_code=97,performance_summary={'reason':'post_follow_return_ct_failed'}).incident_type,'run_worker_failure')

    def test_process_death_uses_existing_canonical_total_and_persisted_quota_only(self):
        for target in (120,None):
            plan={'plan':{'quota_targets':{'follow':target}}}
            row={'id':R,'account_id':A,'status':'failed','total_follow':68,
                 'performance_summary':{'terminal_reconciliation_source':'canonical_event_reconciliation'}}
            with patch.object(store,'load_resume_plan',return_value=plan), \
                 patch.object(supabase_client,'_request_json',return_value=[]) as write, \
                 patch.object(supabase_client,'count_persisted_run_follows') as recount:
                summary,existing=consumer._project_missing_worker_terminal_summary(run_row=row,account_id=A,run_id=R)
                self.assertEqual(summary['follows_completed_count'],68)
                if target:self.assertEqual(summary['follow_quota_remaining'],52)
                else:self.assertNotIn('follow_quota_remaining',summary)
                self.assertIs(existing,plan);recount.assert_not_called();write.assert_called_once()
                repeated,_=consumer._project_missing_worker_terminal_summary(run_row={**row,'performance_summary':summary},account_id=A,run_id=R)
                self.assertEqual(repeated,summary);write.assert_called_once()
            self.assertEqual(classify_terminal_run_failure(exit_code=-9,performance_summary=summary).incident_type,'run_worker_failure')
            with patch.object(store,'load_resume_plan') as read, patch.object(supabase_client,'load_run_row') as read_run, \
                 patch.object(store,'_patch_plan_by_run_id',return_value={'persisted':True}) as update:
                store.record_terminal_failure(run_id=R,incident_type='run_worker_failure',reason_code='worker_exit_nonzero',
                    terminal_summary=summary,existing_resume_plan=existing)
            read.assert_not_called();read_run.assert_not_called()
            self.assertEqual(update.call_args.args[1]['plan']['quota_done']['follow'],68)

    def test_process_death_does_not_trust_unreconciled_or_other_run_totals(self):
        for row in ({'id':R,'account_id':A,'total_follow':68,'performance_summary':{}},
                    {'id':'other','account_id':A,'total_follow':68,'performance_summary':{'terminal_reconciliation_source':'canonical_event_reconciliation'}}):
            with patch.object(store,'load_resume_plan') as read,patch.object(supabase_client,'_request_json') as write:
                summary,_=consumer._project_missing_worker_terminal_summary(run_row=row,account_id=A,run_id=R)
            read.assert_not_called();write.assert_not_called();self.assertNotIn('follows_completed_count',summary)

if __name__=='__main__':unittest.main()
