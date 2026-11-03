"""Actual affected functions, deterministic offline transport; never a device or backend."""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import subprocess
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import dm_sender_engine as engine
import instagram_navigation as nav
from test_dm_446_readers import Device, Clock, P, mutate, node

ROOT = Path(__file__).resolve().parents[1]
PARENT = "374db14f323cd36da02b0121619972d0f1648250"
NATIVE = json.loads((ROOT/"tests/fixtures/dm_446_current_surface.json").read_text())["compact_filled_family"]
TIMINGS = json.loads((ROOT/"tests/fixtures/dm446_latency_segments_v1.json").read_text())

def function(name, parent=False):
    source = (subprocess.check_output(["git", "show", PARENT+":dm_sender_engine.py"], cwd=ROOT, text=True)
              if parent else (ROOT/"dm_sender_engine.py").read_text())
    fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    space = dict(engine.__dict__)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<parent-or-candidate>", "exec"), space)
    return space[name]

class TargetedLatencyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = Clock()
        for module in (engine, nav):
            self.stack.enter_context(patch.object(module, "time", self.clock))
            self.stack.enter_context(patch.object(module, "log", lambda *a, **k: None))
        self.stack.enter_context(patch.object(engine.config, "DM_VERIFY_TYPED_TEXT", True))
        self.stack.enter_context(patch.object(engine.config, "DM_DRAFT_TYPING_ENABLED", True))

    def sender(self, parent=False, *, version=446, dm_type="outreach", got="Complete draft",
               read_error=False, absent=False, safe=True, duplicate=False, verify_enabled=True, identity=True):
        fn = function("_perform_real_welcome_dm_send", parent)
        d = Device(NATIVE[0]["stages"]["empty"]["xml"], version=version)
        calls, transitions, events = [], [], []
        self.clock.now = 0.
        def call(label, value, cost=0):
            def invoke(*a, **k):
                calls.append(label); self.clock.now += cost
                return value
            return invoke
        def get_text():
            calls.append("get_text")
            if read_error: raise RuntimeError("read_error")
            return got
        editor = types.SimpleNamespace(get_text=get_text)
        def reader(*a):
            count = calls.count("diagnostic_or_pretyping")
            calls.append("diagnostic_or_pretyping")
            self.clock.now += 4.
            return "" if count == 0 else (got or "")
        env = fn.__globals__
        env.update(
            log=lambda *a, **k: None,
            dm_thread_shows_outgoing_message=call("duplicate", duplicate),
            _resolve_dm_text_composer=call("focus", (object(), None)),
            verify_dm_composer_safe=call("composer", (safe, "qualified")),
            read_dm_composer_text=reader,
            type_dm_draft_only=call("type", (True, {"method":"set_text"})),
            verify_dm_draft_text=nav.verify_dm_draft_text,
            verify_welcome_dm_thread_recipient_exact=call("identity", (identity, "exact" if identity else "wrong_account", "recipient")),
            send_dm_safe=call("send", {"sent": True, "send_tapped": True}),
        )
        with patch.object(nav, "_dm_find_focus_composer", call("strict_finder", None if absent else editor, 4.)), \
             patch.object(engine.config, "DM_VERIFY_TYPED_TEXT", verify_enabled), \
             patch.object(engine._dm_retention, "capture_event", side_effect=lambda stage, **fields: events.append((stage,fields))):
            answer = fn(d, username="recipient", message_body="Complete draft", thread_state="empty_new_thread",
                        pkg=P, dm_type=dm_type, post_send_nav="sender_owned",
                        state_transition=lambda *args: transitions.append(args))
        return answer, calls, transitions, self.clock.now, events

    def test_parent_candidate_exact_positive_saves_one_read_not_one_verification(self):
        old, new = self.sender(True), self.sender()
        self.assertEqual(old[0], new[0]); self.assertEqual(old[2], new[2])
        self.assertEqual(old[1].count("diagnostic_or_pretyping"), 2)
        self.assertEqual(new[1].count("diagnostic_or_pretyping"), 1)
        self.assertEqual(old[1].count("strict_finder"), 1)
        self.assertEqual(new[1].count("strict_finder"), 1)
        self.assertEqual(new[1].count("send"), 1)
        self.assertEqual(old[3]-new[3], 4.)  # deterministic cost, NOT a physical gain
        event = next(f for s,f in new[4] if s=="posttyping_read_result")
        self.assertEqual(event["source"], "draft_verify")
        self.assertTrue(event["read_succeeded"])
        self.assertEqual(event["actual_text"], "Complete draft")

    def test_failed_verification_never_reaches_send_or_exact_state(self):
        cases = [dict(got=""),dict(got=None),dict(got="Complete"),dict(got="Different draft"),
                 dict(read_error=True),dict(absent=True)]
        for case in cases:
            with self.subTest(case=case):
                old, new = self.sender(True, **case), self.sender(**case)
                self.assertEqual(old[0], new[0]); self.assertEqual(old[2], new[2])
                self.assertEqual(new[0][2], "draft_verify_failed")
                self.assertNotIn("send", new[1]); self.assertNotIn(("draft_exact","draft_readback_exact"), new[2])
                event=next(f for s,f in new[4] if s=="posttyping_read_result")
                if case.get("read_error") or case.get("absent"):
                    self.assertNotIn("actual_text", event);self.assertFalse(event["read_succeeded"])

    def test_unsafe_composer_duplicate_and_disabled_verification_keep_parent_behavior(self):
        for case in (dict(safe=False),dict(duplicate=True),dict(verify_enabled=False)):
            with self.subTest(case=case):
                old,new=self.sender(True,**case),self.sender(**case)
                self.assertEqual(old[:3],new[:3])
                self.assertEqual(old[3],new[3])

    def test_welcome_372_and_unversioned_keep_original_read_sequence(self):
        for version,kind in ((372,"outreach"),(0,"outreach"),(446,"welcome"),(372,"welcome")):
            with self.subTest(version=version,kind=kind):
                old,new=self.sender(True,version=version,dm_type=kind),self.sender(version=version,dm_type=kind)
                self.assertEqual(old[:4],new[:4])

    def test_legacy_welcome_wrong_identity_still_refuses_before_send(self):
        old,new=self.sender(True,identity=False,dm_type="welcome"),self.sender(identity=False,dm_type="welcome")
        self.assertEqual(old[0],new[0]);self.assertNotIn("send",new[1])
        self.assertIn("identity",new[1])

    def test_changed_device_package_generation_or_revoked_context_cannot_reuse_editor(self):
        import instagram_ui_446_compat as compat
        surface=NATIVE[0];xml=surface["stages"]["filled"]["xml"]
        changes=[lambda d:setattr(d,"serial","different"),
                 lambda d:setattr(d,"_instagram_search_observed_version",(id(d),d.serial,"foreign",*compat.VERSION)),
                 lambda d:setattr(d,"_instagram_search_observed_version",(id(d),d.serial,P,"447",1)),
                 lambda d:setattr(d,compat.CONTEXT_KEY,None)]
        for change in changes:
            d=Device(xml);d.after_read=change;observation={}
            self.assertFalse(nav.verify_dm_draft_text(d,surface["controlled_text"],observation=observation))
            self.assertFalse(observation["read_succeeded"])

    def test_metadata_clears_prior_value_on_missing_or_exception(self):
        for editor,branch in ((None,"FIELD_ABSENT"),
            (types.SimpleNamespace(get_text=lambda:(_ for _ in ()).throw(ValueError("bad"))),"GET_TEXT_EXCEPTION")):
            observation={"actual_text":"Complete draft","read_succeeded":True}
            with patch.object(nav,"_dm_find_focus_composer",return_value=editor):
                self.assertFalse(nav.verify_dm_draft_text(None,"Complete draft",observation=observation))
            self.assertEqual(observation["branch"],branch);self.assertNotIn("actual_text",observation)
            self.assertFalse(observation["read_succeeded"])

    def test_native_compact_both_lengths_keep_two_observation_strict_reader(self):
        for surface in NATIVE:
            for stage in ("first_filled","filled","verify"):
                with self.subTest(surface=surface["label"],stage=stage):
                    d=Device(surface["stages"][stage]["xml"])
                    observation={}
                    self.assertTrue(nav.verify_dm_draft_text(d,surface["controlled_text"],observation=observation))
                    self.assertEqual(observation["actual_text"],surface["controlled_text"])
                    self.assertEqual(len(observation["actual_text"]),surface["length"])
                    self.assertEqual(d.reads,2)

    def test_native_wrong_empty_partial_text_fail_despite_valid_surface(self):
        surface=NATIVE[0]
        for expected in ("wrong", surface["controlled_text"]+"x"):
            d=Device(surface["stages"]["filled"]["xml"]);observation={}
            self.assertFalse(nav.verify_dm_draft_text(d,expected,observation=observation))
            self.assertEqual(observation["branch"],"TEXT_DIFFERENT")

    def test_native_missing_duplicate_occluded_changed_context_and_wrong_foreground_refuse(self):
        surface=NATIVE[0];xml=surface["stages"]["filled"]["xml"]
        import xml.etree.ElementTree as ET
        variants=[
            mutate(xml,lambda r:node(r,"row_thread_composer_edittext").set("resource-id",P+":id/wrong")),
            mutate(xml,lambda r:r.append(copy.deepcopy(node(r,"row_thread_composer_edittext")))),
            mutate(xml,lambda r:node(r,"row_thread_composer_edittext").set("enabled","false")),
            [xml,"<hierarchy/>"],
        ]
        for value in variants:
            with self.subTest(kind=str(type(value))):
                d=Device(value);observation={}
                self.assertFalse(nav.verify_dm_draft_text(d,surface["controlled_text"],observation=observation))
                self.assertFalse(observation["read_succeeded"])
        d=Device(xml);d.foregrounds=[{"package":"foreign","activity":"foreign"}]
        observation={}
        self.assertFalse(nav.verify_dm_draft_text(d,surface["controlled_text"],observation=observation))
        self.assertFalse(observation["read_succeeded"])

    def focus(self,parent=False,version=446,kind="outreach",caller="real_send",failure=False):
        fn=function("_resolve_dm_text_composer",parent);calls=[]
        def focus():
            calls.append("click")
            if failure: raise RuntimeError("focus")
        ed=types.SimpleNamespace(info={"bounds":{"left":0}},click=focus)
        def find(*a):calls.append("finder");self.clock.now+=4.;return ed
        fn.__globals__.update(log=lambda *a,**k:None,
            _check_dm_sender_permission_blocker=lambda *a,**k:False,
            _dm_find_focus_composer=find,_dm_audit_non_text_action_candidates=lambda *a,**k:None)
        d=Device(NATIVE[0]["stages"]["empty"]["xml"],version=version);self.clock.now=0.
        result=fn(d,pkg=P,username="recipient",caller=caller,dm_type=kind)
        return result,calls,self.clock.now
    def test_only_actual_446_outreach_caller_omits_unused_post_focus_lookup(self):
        old,new=self.focus(True),self.focus()
        self.assertIsNone(old[0][1]);self.assertIsNone(new[0][1])
        self.assertEqual(old[1],["finder","click","finder"]);self.assertEqual(new[1],["finder","click"])
        self.assertAlmostEqual(old[2]-new[2],4.)
        for kwargs in (dict(version=372),dict(version=0),dict(kind="welcome"),dict(caller="diagnostic"),dict(failure=True)):
            old,new=self.focus(True,**kwargs),self.focus(**kwargs)
            self.assertEqual(old[0][1],new[0][1]);self.assertEqual(old[1:],new[1:])

    def restore(self,parent=False,version=446,thread=True,search_verified=True):
        fn=function("prepare_dm_sender_global_search_surface",parent);calls=[];self.clock.now=0.
        def call(name,result,cost=0):
            def f(*a,**kw):calls.append(name);self.clock.now+=cost;return result
            return f
        searches=iter([False,True])
        def search(*a):calls.append("search");return next(searches,True)
        fn.__globals__.update(log=lambda *a,**k:None,invalidate_search_surface_cache=lambda *a:None,
            _set_dm_sender_post_job_restore=lambda **k:None,_record_dm_sender_post_job_restore_attempt=lambda **k:None,
            _check_dm_sender_permission_blocker=lambda *a,**k:False,
            is_dm_thread_screen=call("thread",thread,2.),_dm_sender_composer_visible_quick=call("composer",True,4.),
            is_lightweight_search_screen=search,
            tap_instagram_action_bar_back_button=call("back",(True,"exact")),
            _verify_dm_sender_global_search_surface=call("verify_search",(search_verified,"fixture")),
            verify_profile=call("verify_profile",True),return_to_search_from_profile=call("return_search",True),
            _mark_dm_sender_global_search_ready=call("ready",None))
        d=Device(NATIVE[0]["stages"]["empty"]["xml"],version=version)
        result=fn(d,account_username="account",context="dm_sender_post_job",
                  last_recipient_username="recipient",prefer_back_stack_to_search=True)
        return result,calls,self.clock.now
    def test_restore_redundant_probes_removed_but_exact_search_and_action_preserved(self):
        old,new=self.restore(True),self.restore()
        self.assertTrue(old[0]);self.assertEqual(old[0],new[0])
        self.assertEqual(old[1].count("thread"),2);self.assertEqual(new[1].count("thread"),1)
        self.assertEqual(old[1].count("composer"),1);self.assertNotIn("composer",new[1])
        self.assertEqual([x for x in old[1] if x not in ("thread","composer")],
                         [x for x in new[1] if x not in ("thread","composer")])
        self.assertAlmostEqual(old[2]-new[2],6.)
    def test_restore_unknown_thread_still_checks_composer_and_failed_search_uses_profile(self):
        for case in (dict(thread=False),dict(search_verified=False)):
            old,new=self.restore(True,**case),self.restore(**case)
            self.assertEqual(old[0],new[0])
            for label in ("back","verify_search","verify_profile","return_search"):
                self.assertEqual(old[1].count(label),new[1].count(label))
            if not case.get("thread",True):self.assertIn("composer",new[1])
            if not case.get("search_verified",True):self.assertIn("verify_profile",new[1])
    def test_restore_372_and_unversioned_reads_unchanged(self):
        for version in (372,0):self.assertEqual(self.restore(True,version),self.restore(False,version))

    def test_no_changes_to_send_typing_a0_or_sleep_calls(self):
        allowed={
            "dm_sender_engine.py":{"_resolve_dm_text_composer","_perform_real_welcome_dm_send","prepare_dm_sender_global_search_surface"},
            "instagram_navigation.py":{"verify_dm_draft_text"},
        }
        for file,names in allowed.items():
            before=subprocess.check_output(["git","show",PARENT+":"+file],cwd=ROOT,text=True)
            after=(ROOT/file).read_text()
            functions=lambda s:{n.name:ast.dump(n) for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
            a,b=functions(before),functions(after)
            self.assertEqual(set(a),set(b))
            self.assertEqual({n for n in a if a[n]!=b[n]},names)
            sleep_calls=lambda s:[ast.dump(n) for n in ast.walk(ast.parse(s)) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="sleep"]
            self.assertEqual(sleep_calls(before),sleep_calls(after))
    def test_historical_latency_fixture_is_not_after_measurement(self):
        self.assertFalse(TIMINGS["physical_after_available"])
        self.assertEqual(TIMINGS["run_seconds"],326.389203)
        self.assertEqual(sum(r["xml_observations"] for r in TIMINGS["posttyping_roles"]),13)
        self.assertEqual(sum(r["xml_observations"] for r in TIMINGS["posttyping_roles"] if r["retained"]),11)

if __name__=="__main__":unittest.main()

