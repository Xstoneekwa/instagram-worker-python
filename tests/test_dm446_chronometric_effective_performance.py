"""Differential actual paths with offline transport; no device, RUN or RPC."""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import os
import subprocess
import types
import unittest
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import dm_sender_engine as engine
import instagram_navigation as nav
import run_latency_observability_v1 as obs
import outreach_slot_contract as slot_contract
from test_dm_446_readers import Device, Selector, Clock, P, mutate, node
from test_dm_446_history_outbound_longtext import ProofHarness

ROOT = Path(__file__).resolve().parents[1]
PARENT = "ea7e6016c4ceb84c8f119996e1e42777a2142b03"
COMPACT = json.loads((ROOT/"tests/fixtures/dm_446_current_surface.json").read_text())["compact_filled_family"]
LONG = json.loads((ROOT/"tests/fixtures/dm_446_history_outbound_longtext.json").read_text())["expected_text"]
TRACE = json.loads((ROOT/"tests/fixtures/dm446_chronometric_native_segments_v1.json").read_text())

def parent_space(module, file, names):
    source = subprocess.check_output(["git","show",PARENT+":"+file],cwd=ROOT,text=True)
    selected = [n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in names]
    space = dict(module.__dict__)
    exec(compile(ast.Module(body=selected,type_ignores=[]),"<exact-ea7-parent>","exec"),space)
    return space

class LocalClock(Clock):
    def monotonic_ns(self): return int(self.now*1e9)

class Editor(Selector):
    @property
    def count(self):
        self.d.calls.append('count');return super().count
    @property
    def info(self):
        self.d.calls.append('info');return super().info
    def click(self):
        self.d.calls.append("editor_focus")
    def set_text(self, value):
        self.d.calls.append("set_text")
        self.d.text = value
        self.d.stage = "filled"
    def clear_text(self):
        self.d.calls.append("clear_text")
        self.d.text = ""
    def get_text(self):
        self.d.calls.append("get_text")
        if self.d.read_error:
            raise RuntimeError("offline_read_error")
        if self.d.on_get:
            self.d.on_get(self.d)
        return self.d.text[:7] if self.d.partial and self.d.stage=="filled" else self.d.text

class Transport(Device):
    def __init__(self, surface=None, version=446):
        self.surface = surface or COMPACT[0]
        super().__init__(self.surface["stages"]["empty"]["xml"],version=version)
        self.calls=[];self.stage="empty";self.text="";self.partial=False
        self.read_error=False;self.on_get=None;self.cost=1.
    def __call__(self, **kw):return Editor(self,kw)
    def dump_hierarchy(self, **kw):
        self.xmls=[self.surface["stages"][self.stage]["xml"] if self.stage!="post" else "<hierarchy/>"]
        self.last=self.xmls[0]
        return super().dump_hierarchy()
    def press(self, *a, **kw):self.calls.append("back")
    def window_size(self):return super().window_size()

class EffectivePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.clock=LocalClock();self.clock.now=0.;obs.reset_for_tests()
        for module in (nav,engine):
            self.stack.enter_context(patch.object(module,"time",self.clock))
            self.stack.enter_context(patch.object(module,"log",lambda *a,**k:None))
        for key,value in (("DM_VERIFY_TYPED_TEXT",True),("DM_DRAFT_TYPING_ENABLED",True),
                          ("ENABLE_REAL_DM_SEND",False),("FAST_IME","")):
            self.stack.enter_context(patch.object(engine.config,key,value))
        self.stack.enter_context(patch.object(nav,"verify_app_foreground",return_value=True))
        self.stack.enter_context(patch.object(nav,"is_lightweight_search_screen",return_value=False))
        self.stack.enter_context(patch.object(nav,"_try_dismiss_keyboard_light",return_value=None))
        self.stack.enter_context(patch.object(engine,"_check_dm_sender_permission_blocker",return_value=False))
        self.stack.enter_context(patch.object(engine,"WELCOME_COMPOSER_FAST_PATH_RUNTIME_ENABLED",False))

    def run_sender(self, parent=False, *, surface=None, version=446, kind="outreach",
                   partial=False, read_error=False, duplicate=False, identity=True,
                   verified=True, proof_len=0, restore_owned=True, gate_blocked=False,
                   changed_context=False):
        d=Transport(surface,version);d.clock=self.clock;d.partial=partial;d.read_error=read_error
        self.clock.now=0.;events=[];calls=d.calls
        if changed_context:
            d.on_get=lambda device:setattr(device,"serial","foreign-serial")
        fn=engine._perform_real_welcome_dm_send
        ns=dict(nav.__dict__)
        if parent:
            ns=parent_space(nav,"instagram_navigation.py",
                {"verify_dm_composer_safe","type_dm_draft_only","send_dm_safe"})
            es=parent_space(engine,"dm_sender_engine.py",{"_perform_real_welcome_dm_send"})
            fn=es["_perform_real_welcome_dm_send"]
        else:
            es=dict(engine.__dict__)
            node_fn=next(n for n in ast.parse((ROOT/"dm_sender_engine.py").read_text()).body
                         if isinstance(n,ast.FunctionDef) and n.name=="_perform_real_welcome_dm_send")
            exec(compile(ast.Module(body=[node_fn],type_ignores=[]),"<candidate>","exec"),es)
            fn=es["_perform_real_welcome_dm_send"]
        class Button:
            def click(self):
                calls.append("tap");d.stage="post"
        def gate(job):
            calls.append("fresh_gate")
            if gate_blocked:raise slot_contract.SlotBlocked("permission_revoked")
        slot=types.SimpleNamespace(before_send=gate)
        def proof(*a,**kw):
            calls.append("native_outbound_proof")
            return verified,"outbound_bubble_after_tap" if verified else "outbound_bubble_not_verified",dict(
                outbound_expected_text_present=verified,composer_text_len=proof_len,
                expected_text_present=False,message_marker_count=0)
        es.update({name:ns[name] for name in ("verify_dm_composer_safe","type_dm_draft_only","send_dm_safe")})
        es.update(dm_thread_shows_outgoing_message=lambda *a:duplicate,
                  verify_welcome_dm_thread_recipient_exact=lambda *a:(identity,"exact" if identity else "wrong_account","fixture"))
        def profile(*a,**kw):calls.append("legacy_profile_probe");return True
        with ExitStack() as stack:
            stack.enter_context(patch.object(slot_contract,"active",return_value=slot))
            stack.enter_context(patch.object(nav,"_dm_capture_send_debug_artifact",side_effect=lambda *a,**kw:{}))
            stack.enter_context(patch.object(nav,"guard_instagram_action_rate_limit",side_effect=lambda *a,**kw:calls.append("platform_guard")))
            stack.enter_context(patch.object(nav,"wait_for_dm_send_button_after_draft",return_value=(Button(),"ok",{})))
            stack.enter_context(patch.object(nav,"_dm_thread_message_signature",side_effect=lambda *a,**kw:(calls.append("baseline") or dict(message_marker_count=0,expected_text_present=False))))
            stack.enter_context(patch.object(nav,"_dm_verify_outbound_message_after_send",side_effect=proof))
            stack.enter_context(patch.object(nav,"return_to_profile_from_dm",side_effect=profile))
            stack.enter_context(patch.object(engine._dm_retention,"capture_event",side_effect=lambda stage,**kw:events.append((stage,kw))))
            original=nav._dm_446_composer
            strict=stack.enter_context(patch.object(nav,"_dm_446_composer",wraps=original))
            # Parent cloned functions keep their dependency namespace; patch only
            # unchanged transport/proof boundaries exactly as on the candidate.
            for name in ("_dm_capture_send_debug_artifact","guard_instagram_action_rate_limit",
                         "wait_for_dm_send_button_after_draft","_dm_thread_message_signature",
                         "_dm_verify_outbound_message_after_send"):
                ns[name]=getattr(nav,name)
            result=None;error=None
            try:
                result=fn(d,username="fixture_recipient",message_body=LONG,thread_state="empty_new_thread",
                    pkg=P,dm_type=kind,thread_snapshot=dict(composer_visible=True,composer_signal="resource_id_exact_composer_pkg"),
                    **({"sender_restoration_owned":restore_owned} if not parent else {}))
            except slot_contract.SlotBlocked as exc:error=str(exc)
            count=strict.call_count
        return dict(result=result,error=error,calls=calls,strict=count,xml=d.reads,
                    app_current=d.currents,window_size=d.dimensions,events=events,clock=self.clock.now)

    def test_three_immediate_reads_and_obsolete_finalizer_are_removed(self):
        old,new=self.run_sender(True),self.run_sender()
        self.assertTrue(old["result"][0]);self.assertTrue(new["result"][0])
        self.assertEqual((old["strict"],new["strict"]),(10,5))
        self.assertEqual(old["xml"]-new["xml"],8)
        self.assertEqual(old['app_current']-new['app_current'],16)
        self.assertEqual(old['window_size']-new['window_size'],8)
        self.assertEqual(old['calls'].count('info')-new['calls'].count('info'),3)
        self.assertEqual(old['calls'].count('count')-new['calls'].count('count'),3)
        for call,n in (("get_text",4),("set_text",1),("tap",1),("baseline",1),("fresh_gate",1),("native_outbound_proof",1),("platform_guard",2)):
            self.assertEqual(new["calls"].count(call),n,call)
        self.assertEqual(old['calls'].count('get_text'),new['calls'].count('get_text'))
        self.assertEqual(old["calls"].count("legacy_profile_probe"),1)
        self.assertNotIn("legacy_profile_probe",new["calls"])
        fin=new["result"][1]["post_finalize"]
        self.assertTrue(fin["navigation_deferred_to_sender"])
        self.assertTrue(fin["post_send_fast_finalize_used"])
        self.assertFalse(fin["back_to_profile_ok"]);self.assertFalse(fin["back_to_search_ok"])
        self.assertTrue(next(f for s,f in new["events"] if s=="draft_verify")["result"])
        self.assertTrue(next(f for s,f in new["events"] if s=="typing_readback")["original_editor_used"])

    def test_measured_fixture_and_count_gains_are_not_a_physical_gain(self):
        self.assertAlmostEqual(sum(x["obsolete_finalize_ms"] for x in TRACE["sent_candidates"]),55712.51)
        self.assertEqual(TRACE["physical_gain_certified"],False)
        old,new=self.run_sender(True),self.run_sender()
        self.assertEqual(3*(old["strict"]-new["strict"]),15)
        self.assertEqual(3*(old["xml"]-new["xml"]),24)
        self.assertAlmostEqual(old["clock"]-new["clock"],8.) # one synthetic unit/dump, NOT field seconds

    def test_compact_native_forms_all_keep_exact_long_emojis(self):
        self.assertEqual((len(LONG),len(LONG.encode("utf-16-le"))//2),(803,810))
        for surface in COMPACT:
            with self.subTest(form=surface["label"]):
                result=self.run_sender(surface=surface)
                self.assertTrue(result["result"][0])
                draft=next(f for s,f in result["events"] if s=="draft_verify")
                self.assertTrue(draft["result"]);self.assertEqual(draft["actual_text"],LONG)

    def test_partial_exception_and_changed_context_never_tap(self):
        for kw in (dict(partial=True),dict(read_error=True),dict(changed_context=True)):
            with self.subTest(case=kw):
                result=self.run_sender(**kw)
                self.assertFalse(result["result"][0]);self.assertNotIn("tap",result["calls"])

    def test_fresh_backend_gate_remains_before_the_only_tap(self):
        result=self.run_sender(gate_blocked=True)
        self.assertEqual(result["error"],"permission_revoked")
        self.assertIn("fresh_gate",result["calls"]);self.assertNotIn("tap",result["calls"])

    def test_weak_nonempty_or_unavailable_postproof_keeps_old_cleanup(self):
        for proof_len in (None,False,1,803):
            with self.subTest(composer_len=proof_len):
                result=self.run_sender(proof_len=proof_len)
                self.assertTrue(result["result"][0])
                self.assertIn("legacy_profile_probe",result["calls"])
                self.assertNotIn("navigation_deferred_to_sender",result["result"][1]["post_finalize"])

    def test_unverified_send_cannot_enable_deferred_cleanup(self):
        result=self.run_sender(verified=False)
        self.assertFalse(result["result"][0])
        self.assertNotIn("navigation_deferred_to_sender",result["result"][1].get("post_finalize",{}))
        self.assertEqual(result["calls"].count("tap"),1)

    def test_no_owned_restore_keeps_legacy_post_send_cleanup(self):
        result=self.run_sender(restore_owned=False)
        self.assertTrue(result["result"][0]);self.assertIn("legacy_profile_probe",result["calls"])

    def test_wrong_welcome_identity_and_duplicate_never_tap(self):
        for kw in (dict(identity=False,kind="welcome"),dict(duplicate=True)):
            result=self.run_sender(**kw);self.assertNotIn("tap",result["calls"])

    def test_legacy_372_welcome_and_unversioned_sequences_are_preserved(self):
        for version,kind in ((372,"outreach"),(0,"outreach"),(446,"welcome"),(372,"welcome")):
            with self.subTest(version=version,kind=kind):
                old,new=self.run_sender(True,version=version,kind=kind),self.run_sender(version=version,kind=kind)
                self.assertEqual(old["result"],new["result"])
                self.assertEqual(old["calls"],new["calls"])
                self.assertEqual((old["xml"],old["strict"]),(new["xml"],new["strict"]))

    def test_pretyping_reader_refuses_changed_foreign_or_revoked_context(self):
        for change in (lambda d:setattr(d,"serial","foreign"),
                       lambda d:setattr(d,"_instagram_446_observed_context",None)):
            d=Transport();d.on_get=change;state={}
            ok,_=nav.verify_dm_composer_safe(d,P,_pretyping_readback=state)
            self.assertFalse(ok);self.assertFalse(state["read_succeeded"])

    def test_context_changed_after_read_does_not_claim_the_getter_was_not_called(self):
        d=Transport();d.on_get=lambda x:setattr(x,'serial','foreign');events=[]
        with patch.object(nav._dm_retention,'capture_event',side_effect=lambda stage,**kw:events.append((stage,kw))):
            self.assertFalse(nav.verify_dm_composer_safe(d,P,_pretyping_readback={})[0])
        event=next(v for stage,v in events if stage=='draft_read')
        self.assertEqual(event['branch'],'CONTEXT_UNPROVED');self.assertTrue(event['get_text_called'])

    def test_opt_in_proxy_preserves_real_446_context_and_reader_call_count(self):
        for surface in COMPACT:
            raw=Device(surface['stages']['filled']['xml'])
            d=obs.instrument_device(raw,observe_properties=True)
            with obs.dm446_span('candidate',identity=dict(run_id='r',job_id='j')):
                result={}
                self.assertTrue(nav.verify_dm_draft_text(d,surface['controlled_text'],observation=result))
            self.assertEqual((raw.reads,raw.currents,raw.dimensions),(2,4,2))
            self.assertTrue(result['read_succeeded'])

    def test_absent_duplicate_occluded_and_wrong_foreground_do_not_admit(self):
        base=COMPACT[0]["stages"]["filled"]["xml"]
        variants=[mutate(base,lambda r:node(r,"row_thread_composer_edittext").set("resource-id",P+":id/wrong")),
                  mutate(base,lambda r:r.append(copy.deepcopy(node(r,"row_thread_composer_edittext")))),
                  mutate(base,lambda r:node(r,"row_thread_composer_edittext").set("enabled","false"))]
        for xml in variants:
            d=Device(xml);state={}
            self.assertFalse(nav.verify_dm_composer_safe(d,P,_pretyping_readback=state)[0])
        with patch.object(nav,"verify_app_foreground",return_value=False):
            self.assertFalse(nav.verify_dm_composer_safe(Transport(),P,_pretyping_readback={})[0])

    def test_fused_reader_full_text_exception_and_true_empty_are_distinct(self):
        for value in (LONG,"",None):
            d=Transport();d.text=value;state={}
            self.assertTrue(nav.verify_dm_composer_safe(d,P,_pretyping_readback=state)[0])
            self.assertTrue(state["read_succeeded"]);self.assertEqual(state["actual_text"],str(value or ""))
        d=Transport();d.read_error=True;state={}
        self.assertFalse(nav.verify_dm_composer_safe(d,P,_pretyping_readback=state)[0])
        self.assertFalse(state["read_succeeded"]);self.assertNotIn("actual_text",state)

class PersistenceAndRestorationTests(unittest.TestCase):
    setUp = EffectivePerformanceTests.setUp
    # End-to-end engine state/persistence order with fake backend transport only.
    def execute(self, *, state="empty_new_thread", restore_ok=True, uncertain=False):
        d=Transport();order=[];row=dict(id="job-1",recipient_username="fixture_recipient",dm_type="outreach",message_body=LONG)
        snap=dict(composer_visible=True,composer_signal="resource_id_exact_composer_pkg")
        def finish(job,status,**kw):
            order.append(("persist",status))
            return dict(row,status=status,sent_at="persisted" if status=="sent" else None,metadata=kw.get("metadata_patch",{}))
        def restore(*a,**kw):
            order.append(("restore",restore_ok))
            engine._set_dm_sender_post_job_restore(post_job_restore_final_mode="previous_search_results_reused" if restore_ok else "restore_failed")
        send=dict(sent=not uncertain,send_tapped=True,post_send_strong_empty_composer_proved=True,
                  post_send_signal_reason="outbound_bubble_after_tap",post_send_signal_method="outbound_bubble",
                  post_send_secondary_signal_reason="skipped_strong_outbound_verified")
        def perform(*a,**kw):
            self.assertTrue(kw["sender_restoration_owned"])
            order.append(("send_proof",not uncertain))
            return (not uncertain),send,"send_unverified" if uncertain else None
        with ExitStack() as st:
            st.enter_context(patch.object(slot_contract,"active",return_value=None))
            st.enter_context(patch.object(engine.supabase_client,"mark_dm_job_running",return_value=row))
            st.enter_context(patch.object(engine.supabase_client,"complete_dm_job",side_effect=finish))
            st.enter_context(patch.object(engine,"_navigate_to_recipient_dm_thread",return_value=(state,state!='unknown')))
            st.enter_context(patch.object(engine,"_get_dm_sender_nav_timings",return_value={}))
            st.enter_context(patch.object(engine,"get_last_dm_thread_classify_snapshot",return_value=snap))
            st.enter_context(patch.object(engine,"_perform_real_welcome_dm_send",side_effect=perform))
            st.enter_context(patch.object(engine,"_safe_teardown_navigation",side_effect=restore))
            if uncertain:
                def quarantine(*a,**kw):
                    order.append(("quarantine",True))
                    return dict(row,status="failed",metadata=dict(automatic_retry_blocked=True)),"send_unverified"
                st.enter_context(patch.object(engine,"_complete_job_send_unverified_quarantine",side_effect=quarantine))
            result=engine.execute_dm_job_real_send(d,row,settings={},account_id="account",run_id="run")
        return result,order

    def test_persistence_precedes_the_one_restore(self):
        result,order=self.execute()
        self.assertEqual(order,[("send_proof",True),("persist","sent"),("restore",True)])
        self.assertEqual(result["final_job_status"],"sent")
    def test_restore_failure_cannot_repeat_persistence_or_resend(self):
        result,order=self.execute(restore_ok=False)
        self.assertEqual(order,[("send_proof",True),("persist","sent"),("restore",False)])
        self.assertEqual(result["final_job_status"],"sent")
    def test_definitive_existing_thread_skip_never_invokes_send(self):
        result,order=self.execute(state="existing_thread")
        self.assertEqual(order,[("persist","skipped"),("restore",True)])
        self.assertEqual(result["final_job_status"],"skipped")
    def test_unknown_is_not_a_definitive_skip_or_send(self):
        result,order=self.execute(state="unknown")
        self.assertNotIn(("persist","skipped"),order);self.assertNotIn(("send_proof",True),order)
    def test_uncertain_send_is_quarantined_without_sent_persistence(self):
        result,order=self.execute(uncertain=True)
        self.assertEqual(order,[("send_proof",False),("quarantine",True),("restore",True)])
        self.assertEqual(result["final_job_status"],"failed")

@unittest.skipUnless(os.environ.get("DM446_CHRONOMETRIC_PRIVATE_INPUTS"),"private native certification supplied separately")
class CurrentNativeRunTests(ProofHarness,unittest.TestCase):
    def test_current_original_xmls_and_recorded_full_getter_are_qualified(self):
        inputs=json.loads(Path(os.environ["DM446_CHRONOMETRIC_PRIVATE_INPUTS"]).read_text())
        for field in ('receipt','expected_text_source'):
            self.assertEqual(hashlib.sha256(Path(inputs[field]['path']).read_bytes()).hexdigest(),inputs[field]['sha256'])
        msg=json.loads(Path(inputs["expected_text_source"]["path"]).read_text())["expected_text"]
        self.assertEqual(hashlib.sha256(msg.encode()).hexdigest(),inputs["expected_sha256"])
        packet=json.loads(Path(inputs["receipt"]["path"]).read_text())
        base=Path(inputs["receipt"]["path"]).parent
        for rec in packet["records"]:
            if rec["phase"].endswith("draft_verify"):
                for observation in rec["observations"]:
                    path=base/observation["xml_path"];raw=path.read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(),observation["xml_sha256"])
                    d=Device(raw.decode())
                    # XML exports UTF16 units; native get_text event proves the
                    # original full Unicode text read at this exact boundary.
                    with patch.object(Selector,"get_text",return_value=msg):
                        self.assertTrue(nav.verify_dm_draft_text(d,msg))
                    self.assertEqual(d.reads,2)
        for thread in packet["thread_records"]:
            rows=thread["observations"]
            self.assertTrue(rows)
        for job in {x["job_id"] for x in packet["native_observations"]}:
            rows=[x for x in packet["native_observations"] if x["job_id"]==job]
            before=next(x for x in rows if x["label"]=="thread_signature" and x["phase"].endswith("pre_send_signature"))
            after=next(x for x in rows if x["label"]=="thread_signature" and x["phase"].endswith("post_send_verification"))
            binding=next(x for x in packet["events"] if x["job_id"]==job and x["stage"]=="job_binding")
            self.assertTrue(self.verify((base/after["xml_path"]).read_text(),
                pre=(base/before["xml_path"]).read_text(),expected=msg,target=binding["fields"]["recipient_username"])[0])

