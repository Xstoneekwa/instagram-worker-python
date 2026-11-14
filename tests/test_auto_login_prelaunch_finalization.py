"""Synthetic pre-device finalization and signed-parent differential checks."""
from copy import deepcopy
from pathlib import Path
import ast
import subprocess
import unittest
from unittest.mock import patch

import account_run_request_consumer as consumer
import auto_restart_device_lock as locks

ROOT = Path(__file__).resolve().parents[1]
PARENT = "d32e2dfbb9528f1c7e2a902a0c36cf327c614546"
ID = lambda n: "00000000-0000-4000-8000-%012d" % n
ACCOUNT, REQUEST, DEVICE, INSTANCE, LEASE = (ID(n) for n in range(1,6))
REASON = "assigned_instagram_app_instance_mismatch"


def fixture():
    binding = dict(account_id=ACCOUNT,request_id=REQUEST,device_id=DEVICE,app_instance_id=INSTANCE,
        worker_id="pending-request:"+REQUEST,lease_id=LEASE,generation=LEASE,lease_seconds=900)
    request = dict(id=REQUEST,account_id=ACCOUNT,requested_run_type="login_provisioning",status="claimed",
        run_id=None,started_at=None,metadata_safe=dict(device_lock_binding=binding,binding_version="auto_login_app_instance_v1",
            assignment_id=ID(6),device_id=DEVICE,app_instance_id=INSTANCE,package_name="com.instagram.testclone",clone_index=1))
    terminal = dict(deepcopy(request),status="blocked",error_code=REASON,completed_at="2026-01-01T00:00:01Z")
    receipt = dict(ok=True,released=True,**{k:binding[k] for k in ("device_id","worker_id","request_id","account_id","lease_id","generation")})
    return request, terminal, receipt


class PrelaunchLeaseFinalizationTests(unittest.TestCase):
    def invoke(self, request=None, terminal=None, receipt=None, **kwargs):
        original, current, result = fixture()
        original = original if request is None else request
        current = current if terminal is None else terminal
        result = result if receipt is None else receipt
        with patch.object(consumer,"get_account_run_request",return_value=current) as read, patch.object(
                consumer,"release_device_lock_for_request",return_value=result) as release, patch.object(consumer,"log"):
            value = consumer._finalize_unstarted_login_lease(request=original,request_id=REQUEST,account_id=ACCOUNT,
                run_type=kwargs.get("run_type","login_provisioning"),reason_code=REASON,phase=kwargs.get("phase","request_binding"))
        return value, read, release

    def test_exact_prelaunch_failure_releases_only_captured_pending_generation(self):
        for phase in ("request","request_binding","package_runtime_contract"):
            with self.subTest(phase=phase):
                result, read, release = self.invoke(phase=phase)
                self.assertTrue(result["ok"])
                read.assert_called_once_with(REQUEST,bounded_pre_device=True)
                release.assert_called_once_with(request_id=REQUEST,worker_id="pending-request:"+REQUEST,
                    device_id=DEVICE,account_id=ACCOUNT,expected_lease_id=LEASE)

    def test_both_login_types_use_same_exact_finalizer(self):
        req,row,receipt=fixture()
        req["requested_run_type"]=row["requested_run_type"]="login_email_code_resume"
        self.assertTrue(self.invoke(req,row,receipt,run_type="login_email_code_resume")[0]["ok"])

    def test_non_prelaunch_or_business_failure_never_attempts_release(self):
        for kw in (dict(phase="verify_identity"),dict(phase="open_instagram"),dict(run_type="account_session"),dict(run_type="outreach_session")):
            with self.subTest(kw=kw):
                result,read,release=self.invoke(**kw)
                self.assertFalse(result["applicable"])
                read.assert_not_called();release.assert_not_called()

    def test_started_or_run_linked_original_never_releases(self):
        for field,value in (("started_at","2026-01-01T00:00:00Z"),("run_id",ID(9)),("account_id",ID(9)),("id",ID(9))):
            req,_,_=fixture();req[field]=value
            with self.subTest(field=field):
                result,read,release=self.invoke(request=req)
                self.assertFalse(result["ok"]);read.assert_not_called();release.assert_not_called()

    def test_terminal_evidence_must_be_exact_complete_and_unstarted(self):
        changes=(dict(status="starting"),dict(status="running"),dict(status="completed"),dict(run_id=ID(9)),
            dict(started_at="2026-01-01T00:00:00Z"),dict(id=ID(9)),dict(account_id=ID(9)),dict(error_code="other_reason"),
            dict(requested_run_type="account_session"),dict(completed_at=None))
        for fields in changes:
            _,row,_=fixture();row.update(fields)
            with self.subTest(fields=fields):
                result,_,release=self.invoke(terminal=row)
                self.assertFalse(result["ok"]);release.assert_not_called()

    def test_missing_terminal_fields_or_missing_row_is_unknown(self):
        for key in ("run_id","started_at","metadata_safe"):
            _,row,_=fixture();del row[key]
            result,_,release=self.invoke(terminal=row)
            self.assertFalse(result["ok"]);release.assert_not_called()
        result,_,release=self.invoke(terminal={})
        self.assertFalse(result["ok"]);release.assert_not_called()

    def test_frozen_binding_changes_cannot_adopt_a_new_lease_or_identity(self):
        for field,value in (("device_id",ID(9)),("account_id",ID(9)),("request_id",ID(9)),("worker_id","successor"),
                            ("lease_id",ID(9)),("generation",ID(9)),("app_instance_id",ID(9))):
            _,row,_=fixture();row["metadata_safe"]["device_lock_binding"][field]=value
            with self.subTest(field=field):
                result,_,release=self.invoke(terminal=row)
                self.assertFalse(result["ok"]);release.assert_not_called()
        for field,value in (("package_name","com.instagram.android"),("clone_index",0),("assignment_id",ID(9))):
            _,row,_=fixture();row["metadata_safe"][field]=value
            result,_,release=self.invoke(terminal=row)
            self.assertFalse(result["ok"]);release.assert_not_called()

    def test_missing_or_transferred_captured_owner_is_not_cleaned(self):
        req,row,_=fixture();req["metadata_safe"]["device_lock_binding"]["worker_id"]="already-transferred"
        result,read,release=self.invoke(request=req,terminal=row)
        self.assertFalse(result["ok"]);read.assert_not_called();release.assert_not_called()
        req["metadata_safe"].pop("device_lock_binding")
        self.assertFalse(self.invoke(request=req)[0]["ok"])

    def test_unproved_or_foreign_release_never_reports_success(self):
        for fields in (dict(ok=False),dict(released=False),dict(generation=ID(9)),dict(lease_id=ID(9)),dict(worker_id="successor"),dict(request_id=ID(9)),dict(account_id=ID(9)),dict(device_id=ID(9))):
            _,_,receipt=fixture();receipt.update(fields)
            result,_,release=self.invoke(receipt=receipt)
            self.assertFalse(result["ok"]);self.assertFalse(result["released"])
            self.assertEqual(release.call_count,1)

    def test_lost_readback_never_calls_release_and_keeps_unknown(self):
        req,_,_=fixture()
        with patch.object(consumer,"get_account_run_request",side_effect=TimeoutError),patch.object(
                consumer,"release_device_lock_for_request") as release,patch.object(consumer,"log"):
            result=consumer._finalize_unstarted_login_lease(request=req,request_id=REQUEST,account_id=ACCOUNT,
                run_type="login_provisioning",reason_code=REASON,phase="request_binding")
        self.assertFalse(result["ok"]);release.assert_not_called()

    def test_lost_release_response_uses_existing_exact_receipt_without_second_post(self):
        req,row,receipt=fixture();calls=[]
        def rpc(name,params,**kw):
            calls.append((name,params,kw))
            if name=="auto_restart_release_device_lock":raise TimeoutError("lost reply")
            self.assertEqual(name,"fleet_device_lock_operation_receipt_v1")
            self.assertEqual(params["p_expected"]["p_expected_lease_id"],LEASE)
            return dict(receipt,reconciled=True)
        with patch.object(consumer,"get_account_run_request",return_value=row),patch.object(locks.supabase_client,"call_rpc_once",side_effect=rpc):
            result=consumer._finalize_unstarted_login_lease(request=req,request_id=REQUEST,account_id=ACCOUNT,
                run_type="login_provisioning",reason_code=REASON,phase="request_binding")
        self.assertTrue(result["ok"]);self.assertTrue(result["reconciled"])
        self.assertEqual([c[0] for c in calls],["auto_restart_release_device_lock","fleet_device_lock_operation_receipt_v1"])

    def test_failure_publication_keeps_first_cause_even_when_cleanup_unknown(self):
        req,_,_=fixture()
        for cleanup in (dict(applicable=True,ok=False,released=False,reason="prelaunch_terminal_request_not_proven"),
                        dict(applicable=True,ok=True,released=True,reconciled=False)):
            with patch.object(consumer,"_finalize_unstarted_login_lease",return_value=cleanup),patch.object(
                    consumer,"_publish_run_failure_incident") as publish,patch.object(consumer,"log"):
                consumer._publish_auto_login_dispatch_failure(request=req,request_id=REQUEST,account_id=ACCOUNT,
                    run_type="login_provisioning",reason_code=REASON,phase="request_binding")
            publish.assert_called_once()
            values=publish.call_args.kwargs
            self.assertIsNone(values["run_id"])
            self.assertEqual(values["worker_summary"]["reason_code"],REASON)
            self.assertEqual(values["worker_summary"]["phase"],"request_binding")
            self.assertEqual(values["worker_summary"]["prelaunch_lease_cleanup"],cleanup)
            self.assertEqual(values["request_snapshot"]["error_code"],REASON)


class SignedParentDifferentialTests(unittest.TestCase):
    def parent(self,name):
        return subprocess.check_output(["git","-C",str(ROOT),"show",PARENT+":"+name],stderr=subprocess.PIPE).decode()

    def test_only_two_existing_consumer_functions_and_one_cleanup_helper_change(self):
        old=ast.parse(self.parent("account_run_request_consumer.py"))
        new=ast.parse((ROOT/"account_run_request_consumer.py").read_text())
        allowed={"_validate_assignment","_publish_auto_login_dispatch_failure","_finalize_unstarted_login_lease"}
        def preserved(tree):
            return [ast.dump(n) for n in tree.body if not(isinstance(n,ast.FunctionDef) and n.name in allowed)]
        self.assertEqual(preserved(new),preserved(old))
        added={n.name for n in new.body if isinstance(n,ast.FunctionDef)}-{n.name for n in old.body if isinstance(n,ast.FunctionDef)}
        self.assertEqual(added,{"_finalize_unstarted_login_lease"})

    def test_only_existing_login_pool_entries_change_in_resolver(self):
        old=ast.parse(self.parent("assignment_dispatch_resolver.py"))
        new=ast.parse((ROOT/"assignment_dispatch_resolver.py").read_text())
        def get(tree):return next(n for n in tree.body if isinstance(n,ast.AnnAssign) and getattr(n.target,"id",None)=="SUPPORTED_RUN_ASSIGNMENT_TYPES")
        before,after=ast.literal_eval(get(old).value),ast.literal_eval(get(new).value)
        for key in ("login_provisioning","login_email_code_resume"):
            self.assertEqual(after[key],{"full_cycle","outreach_only"});after[key]=before[key]
        self.assertEqual(after,before)
        get(new).value=get(old).value
        self.assertEqual(ast.dump(new),ast.dump(old))

    def test_cleanup_has_no_spawn_navigation_or_completion_authority(self):
        tree=ast.parse((ROOT/"account_run_request_consumer.py").read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_finalize_unstarted_login_lease")
        calls={n.func.id for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        self.assertEqual(calls & {"release_device_lock","release_device_lock_for_request","transfer_device_lock","renew_device_lock"},{"release_device_lock_for_request"})
        self.assertTrue(calls.isdisjoint({"_safe_complete_account_run_request","complete_account_run_request","_build_runner_command","_fleet_spawn_or_legacy"}))
        self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in {"Popen","run","tap","click"} for n in ast.walk(node)))


if __name__ == "__main__":
    unittest.main()
