"""Offline Search-absence proof and canonical terminal-completion boundaries."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import time
import ast
import subprocess
from pathlib import Path

import instagram_navigation as nav
import dm_sender_engine as engine
from outreach_slot_contract import SlotBlocked

PKG = "com.instagram.android"
QUERY = "missing_exact_"
def xml(*, query=QUERY, label="No results", row="", extra="", marker_package=PKG):
    rows = ('<node clickable="true" bounds="[0,100][500,200]"><node package="'+PKG+
            '" resource-id="'+PKG+':id/row_search_user_username" text="'+row+
            '" bounds="[20,110][300,180]"/></node>') if row else ""
    return ('<hierarchy><node package="'+PKG+'" class="android.widget.EditText" resource-id="'+PKG+
            ':id/action_bar_search_edit_text" text="'+query+'" bounds="[0,0][500,80]"/>'+
            (('<node package="'+marker_package+'" text="'+label+'" bounds="[0,120][500,180]"/>') if label else "")+
            rows+extra+"</hierarchy>")

class Device:
    def __init__(self, frames=None, package=PKG, activity="com.instagram.mainactivity.MainActivity"):
        self.frames=list(frames or [xml()]);self.index=0;self.reads=0
        self.current={"package":package,"activity":activity}
    def dump_hierarchy(self, **kwargs):
        self.reads+=1
        result=self.frames[min(self.index,len(self.frames)-1)];self.index+=1
        if isinstance(result,Exception):raise result
        return result
    def app_current(self):return dict(self.current)
    def __getattr__(self,name):raise AssertionError("unexpected physical operation: "+name)

def observe(d):
    outcome={}
    with patch.object(nav.time,"sleep"),patch.object(nav,"type_search",return_value=True) as retype:
        exact=nav._observe_outreach_missing_search(d,QUERY,outcome=outcome)
    return exact,outcome,retype

def job():
    return {"id":"job","account_id":"account","campaign_id":"campaign","dm_type":"outreach",
            "recipient_username":QUERY,"sent_at":None,"attempts":2,"metadata":{
                "origin":"CLIENT_LIST","managed_campaign":True,"client_id":"client","campaign_revision":1,
                "outreach_request_id":"request","outreach_slot_id":"slot","outreach_send_state":"before_send"}}
def slot(j):
    return SimpleNamespace(context={"managed_campaign":True,"account_id":j["account_id"],
        "campaign_id":j["campaign_id"],"client_id":j["metadata"]["client_id"],"campaign_revision":1,
        "request_id":"request","slot_id":"slot"},check=Mock())
def ack(j, **kwargs):
    return {**j,"status":"skipped","skip_reason":"search_no_results_confirmed","next_retry_at":None,
            "metadata":{**j["metadata"],**kwargs["metadata_patch"]}}

class SearchAbsenceProofTests(TestCase):
    def test_two_positive_rounds_require_one_exact_retype_and_four_fresh_reads(self):
        d=Device();exact,out,retype=observe(d)
        self.assertFalse(exact);self.assertEqual(out["reason"],"search_no_results_confirmed")
        self.assertEqual(out["state"],"confirmed_absent")
        p=out["proof"];self.assertIs(p.generation,out);self.assertEqual(p.device_identity,id(d))
        self.assertEqual(p.query,QUERY);self.assertEqual(d.reads,4)
        self.assertEqual([len(r) for r in p.rounds],[2,2])
        retype.assert_called_once_with(d,QUERY,trusted_search_surface=True)

    def test_exact_result_is_handed_back_for_normal_rescan_not_tapped(self):
        d=Device([xml(label="",row=QUERY)])
        exact,out,retype=observe(d);self.assertTrue(exact);self.assertNotIn("proof",out)
        retype.assert_not_called();self.assertEqual(d.reads,2)

    def test_result_appearing_after_retype_never_becomes_absent(self):
        d=Device([xml(),xml(),xml(label="",row=QUERY)])
        exact,out,retype=observe(d);self.assertTrue(exact);self.assertNotIn("proof",out)
        retype.assert_called_once()

    def test_loading_is_bounded_and_never_terminal(self):
        d=Device([xml(label="")]);exact,out,retype=observe(d)
        self.assertFalse(exact);self.assertNotIn("proof",out)
        self.assertEqual(out["state"],"transient")
        self.assertLessEqual(d.reads,20);retype.assert_called_once()

    def test_neighbour_rows_without_empty_state_are_not_absence(self):
        d=Device([xml(label="",row="nearby_exact_")]);exact,out,retype=observe(d)
        self.assertFalse(exact);self.assertNotIn("proof",out);retype.assert_not_called()

    def test_unsafe_or_contradictory_surface_never_terminalizes(self):
        cases=[
            Device([xml(query="different")]),Device(package="other.package"),
            Device(activity="OtherActivity"),Device([xml(row=QUERY)]),
            Device([xml(row="neighbour")]),Device([xml(marker_package="other.package")]),
            Device([xml(extra='<node class="android.app.Dialog"/>')]),
            Device([xml(extra='<node package="com.android.permissioncontroller"/>')]),
            Device([xml(extra='<node class="android.widget.ProgressBar"/>')]),
            Device(["<broken"]),Device([RuntimeError("device unavailable")]),
        ]
        for d in cases:
            with self.subTest(frames=d.frames,foreground=d.current):
                exact,out,retype=observe(d)
                self.assertFalse(exact);self.assertNotIn("proof",out);self.assertLessEqual(retype.call_count,1)
                self.assertLessEqual(d.reads,20)

    def test_one_positive_round_followed_by_loading_remains_unknown(self):
        exact,out,retype=observe(Device([xml(),xml(),xml(label="")]))
        self.assertFalse(exact);self.assertNotIn("proof",out);retype.assert_called_once()

    def test_retype_failure_never_confirms_absence(self):
        d=Device();out={}
        with patch.object(nav.time,"sleep"),patch.object(nav,"type_search",return_value=False):
            self.assertFalse(nav._observe_outreach_missing_search(d,QUERY,outcome=out))
        self.assertNotIn("proof",out);self.assertEqual(out["reason"],"search_retype_failed")

class SearchAbsenceCompletionTests(TestCase):
    def ready(self):
        d=Device();_,out,_=observe(d);j=job();return d,out,j,slot(j)
    def complete(self,d,out,j,s):
        return engine._complete_confirmed_search_absence(d,j,out,account_id=j["account_id"],run_id="run",pkg=PKG)

    def test_canonical_skip_keeps_unknown_history_attempts_and_no_send(self):
        d,out,j,s=self.ready()
        def complete(_id,status,**kw):
            self.assertEqual((_id,status),(j["id"],"skipped"));return ack(j,**kw)
        with patch("outreach_slot_contract.active",return_value=s),patch.object(engine.supabase_client,"complete_dm_job",side_effect=complete) as rpc:
            row=self.complete(d,out,j,s)
            self.assertEqual(row["attempts"],2);self.assertEqual(row["status"],"skipped")
            kw=rpc.call_args.kwargs;self.assertIs(kw["increment_attempt"],False)
            self.assertIsNone(kw["retry_delay_seconds"]);self.assertEqual(kw["metadata_patch"]["thread_state"],"unknown")
            evidence=kw["metadata_patch"]["search_absence_v1"]
            self.assertEqual(evidence["recipient"],QUERY);self.assertTrue(evidence["before_send"])
            self.assertEqual(evidence["campaign_id"],j["campaign_id"])
            self.assertIsNone(self.complete(d,out,j,s));rpc.assert_called_once()
        s.check.assert_called_once()

    def test_foreign_stale_or_wrong_generation_proof_is_not_persisted(self):
        mutations={
            "old":lambda p:replace(p,observed_at_monotonic=time.monotonic()-10),
            "future":lambda p:replace(p,observed_at_monotonic=time.monotonic()+10),
            "query":lambda p:replace(p,query="other"),
            "package":lambda p:replace(p,package="other"),
            "device":lambda p:replace(p,device_identity=0),
            "generation":lambda p:replace(p,generation={}),
            "rounds":lambda p:replace(p,rounds=(("a"*64,"a"*64),)),
        }
        for name,mutate in mutations.items():
            d,out,j,s=self.ready();out["proof"]=mutate(out["proof"])
            with self.subTest(name=name),patch("outreach_slot_contract.active",return_value=s),patch.object(engine.supabase_client,"complete_dm_job") as rpc:
                self.assertIsNone(self.complete(d,out,j,s));rpc.assert_not_called()

    def test_wrong_job_or_slot_and_send_possible_never_skip(self):
        for change in ("no_slot","client","campaign","account","revision","request","send_possible","blocked","welcome","origin"):
            d,out,j,s=self.ready()
            if change=="no_slot":s=None
            elif change=="send_possible":j["metadata"]["outreach_send_state"]="send_possible"
            elif change=="blocked":j["metadata"]["automatic_retry_blocked"]=True
            elif change=="welcome":j["dm_type"]="welcome"
            elif change=="origin":j["metadata"]["origin"]="AI_AGENT"
            elif change=="client":s.context["client_id"]="foreign"
            elif change=="campaign":s.context["campaign_id"]="foreign"
            elif change=="account":s.context["account_id"]="foreign"
            elif change=="request":s.context["request_id"]="foreign"
            else:s.context["campaign_revision"]=2
            with self.subTest(change=change),patch("outreach_slot_contract.active",return_value=s),patch.object(engine.supabase_client,"complete_dm_job") as rpc:
                self.assertIsNone(self.complete(d,out,j,s));rpc.assert_not_called()

    def test_unconfirmed_ack_stops_without_retrying_the_mutation(self):
        for value in (None,{},{"id":"foreign","status":"skipped"},{"id":"job","status":"sent"}):
            d,out,j,s=self.ready()
            with self.subTest(ack=value),patch("outreach_slot_contract.active",return_value=s),patch.object(engine.supabase_client,"complete_dm_job",return_value=value) as rpc:
                with self.assertRaisesRegex(SlotBlocked,"outreach_search_absence_completion_unconfirmed"):
                    self.complete(d,out,j,s)
                rpc.assert_called_once()

    def test_slot_stop_prevents_completion(self):
        d,out,j,s=self.ready();s.check.side_effect=SlotBlocked("human_manual_stop")
        with patch("outreach_slot_contract.active",return_value=s),patch.object(engine.supabase_client,"complete_dm_job") as rpc:
            with self.assertRaisesRegex(SlotBlocked,"human_manual_stop"):self.complete(d,out,j,s)
            rpc.assert_not_called()


class SearchAbsenceScopeTests(TestCase):
    def test_only_signed_navigation_and_completion_nodes_change_on_cf069(self):
        root=Path(__file__).resolve().parents[1]
        scopes={
            "instagram_navigation.py":({"tap_account_result"},{"OutreachSearchAbsenceProof","_observe_outreach_missing_search"}),
            "dm_sender_engine.py":({"_navigate_to_recipient_dm_thread","execute_dm_job_real_send"},{"_complete_confirmed_search_absence"}),
        }
        for name,(changed,added) in scopes.items():
            parent=subprocess.check_output(["git","show","cf069a3fc0a3e1bf3fd5806f8ff88c6fea11626e:"+name],cwd=root,text=True)
            current=(root/name).read_text()
            nodes=lambda s:{n.name:ast.dump(n) for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
            a,b=nodes(parent),nodes(current)
            self.assertEqual(set(b)-set(a),added);self.assertFalse(set(a)-set(b))
            self.assertEqual({n for n in a if a[n]!=b[n]},changed)
            def rest(s):
                result=[]
                for n in ast.parse(s).body:
                    if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in changed|added:continue
                    if isinstance(n,ast.Import) and [a.name for a in n.names]==["math"] and name=="dm_sender_engine.py":continue
                    if isinstance(n,ast.ImportFrom) and n.module=="instagram_navigation":
                        n.names=[a for a in n.names if a.name!="OutreachSearchAbsenceProof"]
                    result.append(ast.dump(n))
                return result
            self.assertEqual(rest(parent),rest(current))

    def test_original_search_classifier_and_prior_inverse_records_are_byte_preserved(self):
        root=Path(__file__).resolve().parents[1]
        for name in ("unfollow_hybrid_strategy.py","tests/test_certification_admission.py",
                     "outreach_slot_contract.py","run_latency_observability_v1.py","dm_thread_truth.py"):
            parent=subprocess.check_output(["git","show","cf069a3fc0a3e1bf3fd5806f8ff88c6fea11626e:"+name],cwd=root)
            self.assertEqual((root/name).read_bytes(),parent)
