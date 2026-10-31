"""Affected offline contracts only; no device, network, runtime write or Send."""
import ast
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import supabase_client
import dm_sender_engine as sender
import account_run_request_consumer as consumer
from terminal_summary_projection import project_terminal_summary, native_reason_code
from runtime_incident_matrix import classify_terminal_run_failure, build_run_failure_incident_payload
from incident_notifications import build_incident_notification_payload

ROOT = Path(__file__).parents[1]
RID = "00000000-0000-4000-8000-000000000001"
QID = "00000000-0000-4000-8000-000000000002"
AID = "00000000-0000-4000-8000-000000000003"
STAMP = "2026-10-31T00:00:01+00:00"


def request():
    return dict(id=QID,run_id=RID,account_id=AID,claimed_by="worker",
                requested_run_type="outreach_session",status="running",metadata_safe={})


class NativeReasonTests(unittest.TestCase):
    def test_exact_observation_inverse_preserves_old_pins_and_rejects_unapproved_bytes(self):
        import subprocess
        from tests.test_certification_admission import restore_consolidated_projection_delta
        for name in ("runner.py", "dm_sender_engine.py", "account_run_request_consumer.py", "supabase_client.py"):
            source = (ROOT / name).read_text()
            old = subprocess.check_output(["git", "show", "9d2c4496e50f6395e8784c351473a20981ff91d0:" + name], cwd=ROOT, text=True)
            self.assertEqual(restore_consolidated_projection_delta(name, source), old)
            for bad in (source + "\n# unknown bytes\n", "# unrelated\n" + source):
                with self.assertRaises(AssertionError): restore_consolidated_projection_delta(name, bad)

    def test_native_reason_reaches_existing_incident_and_discord(self):
        summary = project_terminal_summary({"run_type":"outreach_session",
            "outreach_session_summary":{"sender_summary":{
                "first_causal_reason":"navigation_failed"}}})
        self.assertEqual(summary["first_causal_reason"],"navigation_failed")
        decision = classify_terminal_run_failure(exit_code=1,performance_summary=summary)
        payload = build_run_failure_incident_payload(decision,account_id=AID,
            account_username="fixture",run_id=RID,run_request_id=QID,run_type="outreach_session")
        self.assertEqual(payload["reason"],"navigation_failed")
        self.assertEqual(build_incident_notification_payload(payload)["reason_code"],"navigation_failed")

    def test_first_fatal_survives_cleanup_and_explicit_native_precedence(self):
        for reason, expected in [("cleanup_failure","navigation_failed"),
                ("worker_exit_nonzero","navigation_failed"),("identity_guard_failed","identity_guard_failed")]:
            with self.subTest(reason=reason):
                out=project_terminal_summary({"first_causal_reason":reason,
                    "outreach_session_summary":{"sender_summary":{"first_causal_reason":"navigation_failed"}}})
                self.assertEqual(out["first_causal_reason"],expected)
                if reason=="cleanup_failure":self.assertEqual(out["secondary_failure_code"],reason)

    def test_unknown_and_non_outreach_unchanged(self):
        self.assertNotIn("first_causal_reason",project_terminal_summary({}))
        self.assertEqual(project_terminal_summary({"reason":"manual_stop"})["first_causal_reason"],"manual_stop")
        self.assertEqual(native_reason_code("Traceback: password=SECRET"),"")
        self.assertEqual(project_terminal_summary({"outreach_session_summary":{
            "sender_summary":{"first_causal_reason":"unsafe free text"}}}),{
            "outreach_session_summary":{"sender_summary":{"first_causal_reason":"unsafe free text"}}})

    def test_sender_keeps_first_persisted_job_failure_without_actions(self):
        jobs=[dict(id="one",recipient_username="first"),dict(id="two",recipient_username="second")]
        results=[dict(outcome="failed_retry",job={"last_error":"navigation_failed"}),
                 dict(outcome="failed_retry",job={"last_error":"cleanup_failure"})]
        with ExitStack() as stack:
            for name,value in {"resolve_dm_sender_real_send_enabled_for_type":(True,"fixture"),
                    "_resolve_reserved_by":"fixture","_resolve_dm_sender_only_job_id":("","fixture"),
                    "_dm_sender_session_should_abort":False}.items():
                stack.enter_context(patch.object(sender,name,return_value=value))
            stack.enter_context(patch("outreach_slot_contract.active",return_value=None))
            stack.enter_context(patch.object(sender.account_protection_lists,"is_interaction_blocked",return_value=False))
            execute=stack.enter_context(patch.object(sender,"execute_dm_job_real_send",side_effect=results))
            stack.enter_context(patch.object(sender,"log"))
            code,summary=sender.run_dm_sender_send(Mock(),account_id=AID,dm_type="outreach",
                prepared_jobs=jobs,settings_override={},max_jobs=2)
        self.assertEqual(code,1)
        self.assertEqual(summary["first_causal_reason"],"navigation_failed")
        self.assertEqual(summary["jobs_failed_count"],2)
        self.assertEqual(execute.call_count,2)

    def test_consumer_persists_native_terminal_code_with_existing_fallback(self):
        for reason in ("navigation_failed",None):
            row=request()
            summary={"outreach_session_summary":{"sender_summary":{"first_causal_reason":reason}}} if reason else {}
            cfg=SimpleNamespace(worker_id="worker")
            with ExitStack() as stack:
                stack.enter_context(patch.object(consumer,"get_account_run_request",return_value=row))
                stack.enter_context(patch.object(consumer,"_safe_login_provisioner_summary_for_audit",return_value={}))
                stack.enter_context(patch.object(consumer.supabase_client,"load_run_row",return_value={
                    "id":RID,"account_id":AID,"performance_summary":summary}))
                complete=stack.enter_context(patch.object(consumer,"_safe_complete_account_run_request",return_value={}))
                stack.enter_context(patch.object(consumer,"_reconcile_linked_run"))
                stack.enter_context(patch.object(consumer,"_audit"))
                publish=stack.enter_context(patch.object(consumer,"_publish_run_failure_incident"))
                consumer._finalize_manual_run_after_subprocess(cfg,request_id=QID,account_id=AID,exit_code=1)
            self.assertEqual(complete.call_args.kwargs["error_code"],reason or "worker_exit_nonzero")
            if reason:self.assertEqual(publish.call_args.kwargs["worker_summary"]["first_causal_reason"],reason)


class ForegroundTests(unittest.TestCase):
    def proof(self):
        return dict(run_id=RID,request_id=QID,worker_id="worker",foreground_package="com.instagram.android",
                    device_connected_at=STAMP,instagram_foreground_verified_at=STAMP)

    def test_one_rpc_success_needs_no_reconciliation_read(self):
        with patch.object(supabase_client,"call_rpc_once",return_value={"ok":True}) as rpc, \
             patch.object(supabase_client,"_request_json") as read:
            self.assertTrue(supabase_client.certify_account_startup_foreground_v1(
                run_id=RID,request_id=QID,worker_id="worker",foreground_package="com.instagram.android",
                timestamps={})["ok"])
        rpc.assert_called_once();read.assert_not_called()

    def test_lost_rpc_response_reconciles_exact_outreach_proof_without_replay(self):
        for mutated in [None,"run_id","request_id","worker_id","foreground_package","device_connected_at"]:
            row=request(); proof=self.proof()
            if mutated:proof[mutated]=None
            row["metadata_safe"]={"startup_evidence":proof}
            with patch.object(supabase_client,"call_rpc_once",side_effect=TimeoutError("lost")) as rpc, \
                 patch.object(supabase_client,"load_zero_work_capsule_v1",return_value=None), \
                 patch.object(supabase_client,"_request_json",return_value=[row]) as read:
                args=dict(run_id=RID,request_id=QID,worker_id="worker",foreground_package="com.instagram.android",timestamps={})
                if mutated:
                    with self.assertRaises(TimeoutError):supabase_client.certify_account_startup_foreground_v1(**args)
                else:self.assertTrue(supabase_client.certify_account_startup_foreground_v1(**args)["reconciled"])
            rpc.assert_called_once()
            self.assertEqual(read.call_args.kwargs["max_retries"],0)

    def test_existing_full_cycle_reconciliation_has_no_outreach_read(self):
        capsule=dict(run_request_id=QID,irreversible_work_state="STARTED_OR_AMBIGUOUS",
                     device_connected_at=STAMP,instagram_foreground_verified_at=STAMP)
        with patch.object(supabase_client,"call_rpc_once",side_effect=TimeoutError), \
             patch.object(supabase_client,"load_zero_work_capsule_v1",return_value=capsule), \
             patch.object(supabase_client,"_request_json") as read:
            self.assertTrue(supabase_client.certify_account_startup_foreground_v1(
                run_id=RID,request_id=QID,worker_id="worker",
                foreground_package="com.instagram.android",timestamps={})["ok"])
        read.assert_not_called()

    def test_runner_certifies_only_the_existing_physical_foreground_boundary(self):
        tree=ast.parse((ROOT/"runner.py").read_text())
        block=next(n for n in ast.walk(tree) if isinstance(n,ast.If)
            and any(isinstance(x,ast.Assign) and any(isinstance(y,ast.Name) and y.id=="active_evidence"
                for y in x.targets) for x in n.body if isinstance(x,ast.Try) for x in x.body))
        fn=ast.FunctionDef(name="boundary",args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],
            kw_defaults=[],defaults=[]),body=[block],decorator_list=[])
        for outreach,full,admitted,req,expected in [
            (True,False,"",QID,True),(False,True,RID,QID,True),
            (False,False,"",QID,False),(True,False,"","",False)]:
            cert=Mock(return_value={"ok":True})
            env=dict(account_session_run=full,admitted_run_id=admitted,outreach_session_run=outreach,
                run_request_id=req,run_id=RID,account_id=AID,startup_evidence={
                    k:v for k,v in self.proof().items() if k.endswith("_at")},
                supabase_client=SimpleNamespace(certify_account_startup_foreground_v1=cert),
                _run_control_dispatcher_worker_id=lambda:"worker",config=SimpleNamespace(INSTAGRAM_PACKAGE="com.instagram.android"),
                log=Mock(),d=Mock(),_return_with_cleanup=lambda d,code:code)
            exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),"foreground-boundary","exec"),env)
            env["boundary"]();self.assertEqual(cert.called,expected)


if __name__=="__main__":unittest.main()
