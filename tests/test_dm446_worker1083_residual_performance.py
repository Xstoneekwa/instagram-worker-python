"""Parent/candidate differential and strict-native proof regressions, offline only."""
from __future__ import annotations
import ast
import collections
import hashlib
import json
import subprocess
import types
import unittest
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import dm_sender_engine as engine
import instagram_navigation as nav
from test_dm_446_readers import Device, Selector, Clock, P, mutate, node
from test_dm446_chronometric_effective_performance import EffectivePerformanceTests as _Harness, Transport, LONG
ROOT=Path(__file__).resolve().parents[1]
PARENT='1083a465e1c965b6f2e187226f882e9f4d1af880'
NATIVE=json.loads((ROOT/'tests/fixtures/dm_446_current_surface.json').read_text())['compact_filled_family']

def portable_xml(surface):
    xml=surface['stages']['empty']['xml']
    observed=nav._welcome_dm_thread_subtitle_from_hierarchy(xml)
    return xml.replace(observed,'fixture_recipient')

def parent_functions(names):
    text=subprocess.check_output(['git','show',PARENT+':dm_sender_engine.py'],cwd=ROOT,text=True)
    selected=[n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name in names]
    ns=dict(engine.__dict__)
    exec(compile(ast.Module(body=selected,type_ignores=[]),'<signed1083>', 'exec'),ns)
    return ns

class CountSelector(Selector):
    def _cost(self,name):
        self.d.calls.append(name);self.d.clock.now+=.1
    def exists(self,**kw):self._cost('exists');return super().exists(**kw)
    def wait(self,**kw):self._cost('wait');return super().wait(**kw)
    @property
    def info(self):self._cost('info');return super().info
    @property
    def count(self):self._cost('count');return super().count
    def get_text(self):self._cost('get_text');return super().get_text()

class RestoreDevice(Device):
    def __init__(self,xml,version=446):
        super().__init__(xml,version=version)
        self.clock=Clock();self.clock.now=1.;self.stage='thread';self.calls=[]
        self.profile_xml='<hierarchy><node package="'+P+'" class="android.widget.FrameLayout" bounds="[0,0][1080,2340]"/></hierarchy>'
        self.search_xml=('<hierarchy><node package="'+P+'" class="android.widget.FrameLayout" bounds="[0,0][1080,2340]">'
            '<node package="'+P+'" resource-id="'+P+':id/action_bar_search_edit_text" class="android.widget.EditText" bounds="[50,100][1000,200]" enabled="true" clickable="true" focusable="true" text=""/>'
            '<node package="'+P+'" resource-id="'+P+':id/search_tab" class="android.widget.FrameLayout" selected="true" bounds="[200,2100][350,2200]" enabled="true"/>'
            '</node></hierarchy>')
    def __call__(self,**kw):return CountSelector(self,kw)
    def app_current(self):self.calls.append('app_current');self.clock.now+=.1;return super().app_current()
    def window_size(self):self.calls.append('window_size');self.clock.now+=.1;return super().window_size()
    def dump_hierarchy(self):self.calls.append('dump_hierarchy');self.clock.now+=.1;return super().dump_hierarchy()
    def to_profile(self):
        if self.stage!='thread':raise AssertionError('wrong_surface_navigation')
        self.calls.append('thread_back');self.clock.now+=.1;self.stage='profile';self.last=self.profile_xml;self.xmls=[self.last];self.reads=0
    def press(self,key):
        if self.stage!='profile' or key!='back':raise AssertionError('wrong_surface_navigation')
        self.calls.append('profile_back');self.clock.now+=.1;self.stage='search';self.last=self.search_xml;self.xmls=[self.last];self.reads=0

class ResidualRestoreTests(unittest.TestCase):
    def run_restore(self,parent=False,*,version=446,helper=True,permission=False):
        surface=NATIVE[0];d=RestoreDevice(portable_xml(surface),version)
        ns=parent_functions({'_safe_teardown_navigation','prepare_dm_sender_global_search_surface'}) if parent else dict(engine.__dict__)
        calls=d.calls
        def check(*a,**kw):calls.append('permission_veto');return permission
        def profile(*a,**kw):
            calls.append('exact_profile')
            return d.stage=='profile'
        def tap(*a,**kw):d.to_profile();return True,'exact_action_bar'
        ns.update(_check_dm_sender_permission_blocker=check,verify_profile=profile,tap_instagram_action_bar_back_button=tap,
                  log=lambda *a,**k:None,time=d.clock)
        if not parent:
            source=(ROOT/'dm_sender_engine.py').read_text()
            nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in {'_safe_teardown_navigation','prepare_dm_sender_global_search_surface'}]
            exec(compile(ast.Module(body=nodes,type_ignores=[]),'<candidate>','exec'),ns)
        real=nav.outreach_446_restoration_thread_proved
        ns['outreach_446_restoration_thread_proved']=real if helper else lambda *a:False
        with ExitStack() as stack:
            stack.enter_context(patch.object(nav,'time',d.clock))
            stack.enter_context(patch.object(nav,'log',lambda *a,**k:None))
            stack.enter_context(patch.object(engine,'log',lambda *a,**k:None))
            stack.enter_context(patch.object(nav._dm_retention,'capture_xml',lambda *a,**k:None))
            # These dependencies are unchanged foreground/follower/modal/search readers.
            ns['_safe_teardown_navigation'](d,'fixture_recipient',pkg=P,account_username='emitter',prefer_back_stack_to_search=True)
        return d,engine._get_dm_sender_post_job_restore()

    def test_real_restore_positive_keeps_gestures_and_current_search_proof_with_fewer_calls(self):
        old,os=self.run_restore(True);new,ns=self.run_restore()
        self.assertEqual(old.stage,new.stage);self.assertEqual(new.stage,'search')
        self.assertEqual({k:v for k,v in os.items() if not k.endswith('_ms')},
                         {k:v for k,v in ns.items() if not k.endswith('_ms')})
        self.assertEqual(old.calls.count('permission_veto'),2)
        self.assertEqual(new.calls.count('permission_veto'),1)
        self.assertEqual([c for c in old.calls if c in ['thread_back','profile_back']],[c for c in new.calls if c in ['thread_back','profile_back']])
        self.assertEqual(new.calls.count('dump_hierarchy'),old.calls.count('dump_hierarchy'))
        self.assertLess(new.calls.count('wait'),old.calls.count('wait'))
        self.assertLess(len(new.calls),len(old.calls))

    def test_legacy372_and_unversioned_restore_sequence_unchanged(self):
        for version in [0,372]:
            with self.subTest(version=version):
                old,os=self.run_restore(True,version=version);new,ns=self.run_restore(version=version)
                self.assertEqual(old.calls,new.calls);self.assertEqual(os,ns)

    def test_remaining_fresh_permission_veto_stops_before_surface_read_or_gesture(self):
        new,state=self.run_restore(permission=True)
        self.assertEqual(new.calls,['permission_veto'])
        self.assertEqual(state['post_job_restore_final_mode'],'restore_failed')
        self.assertEqual(state['post_job_restore_final_reason'],'permission_blocker')

    def test_unsupported_current_thread_hint_keeps_existing_full_restoration(self):
        old,os=self.run_restore(True);new,ns=self.run_restore(helper=False)
        self.assertEqual(new.stage,'search')
        self.assertEqual(os,ns)
        self.assertEqual(old.calls.count('wait'),new.calls.count('wait'))

    def test_real_remaining_media_permission_guard_aborts_without_any_gesture(self):
        d=RestoreDevice(portable_xml(NATIVE[0]))
        with patch.object(engine,'detect_unexpected_android_media_permission_dialog',return_value=(True,'permission_controller')), \
             patch.object(engine,'log'),patch.object(engine,'_DM_SENDER_SESSION_ABORT_PERMISSION',False):
            engine._safe_teardown_navigation(d,'fixture_recipient',pkg=P,account_username='emitter',prefer_back_stack_to_search=True)
            self.assertTrue(engine._DM_SENDER_SESSION_ABORT_PERMISSION)
            self.assertEqual(d.calls,[])
            self.assertEqual(engine._get_dm_sender_post_job_restore()['post_job_restore_final_reason'],'permission_blocker')

class CurrentThreadProofTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(nav,'log',lambda *a,**k:None))
        self.capture=self.stack.enter_context(patch.object(nav._dm_retention,'capture_xml'))

    def proof(self,d,username='fixture_recipient'):
        return nav.outreach_446_restoration_thread_proved(d,username,P)

    def test_all_portable_qualified_shapes_use_one_existing_observation_and_no_action(self):
        for surface in NATIVE:
            with self.subTest(shape=surface['label']):
                d=Device(portable_xml(surface))
                self.assertTrue(self.proof(d))
                self.assertEqual((d.reads,d.currents,d.dimensions),(1,2,1))

    def test_wrong_exact_recipient_has_no_shortcut(self):
        d=Device(portable_xml(NATIVE[0]))
        self.assertFalse(self.proof(d,'different_recipient'))

    def test_unknown_overlay_modal_consent_and_challenge_never_prove_thread(self):
        base=portable_xml(NATIVE[0])
        for label in ['unknown','Choose if we process your data for ads','Confirm you are human','Log in','Restriction']:
            def add(root):
                ET.SubElement(root,'node',{'package':P,'class':'android.app.Dialog','text':label,'bounds':'[0,0][1080,2340]'})
            d=Device(mutate(base,add))
            self.assertFalse(self.proof(d))
        self.assertFalse(self.proof(Device('<hierarchy/>')))

    def test_changed_foreground_or_version_context_refuses_without_action(self):
        base=portable_xml(NATIVE[0])
        for change in ['foreground','serial','revoked','version']:
            with self.subTest(change=change):
                d=Device(base)
                if change=='foreground':d.foregrounds=[dict(package=P,activity=d.activity),dict(package='foreign',activity=d.activity)]
                if change=='serial':d.after_read=lambda dev:setattr(dev,'serial','foreign')
                if change=='revoked':d.after_read=lambda dev:setattr(dev,'_instagram_446_observed_context',None)
                if change=='version':d.after_read=lambda dev:setattr(dev,'_instagram_search_observed_version',(id(dev),dev.serial,P,'447',1))
                self.assertFalse(self.proof(d))

    def test_legacy_context_adds_no_observation(self):
        for v in [0,372]:
            d=Device(NATIVE[0]['stages']['empty']['xml'],version=v)
            self.assertFalse(self.proof(d))
            self.assertEqual((d.reads,d.currents,d.dimensions),(0,0,0))

    def test_private_original_after_send_xmls_still_prove_exact_current_thread(self):
        fixture=json.loads((ROOT/'tests/fixtures/dm446_worker1083_native_costs_v1.json').read_text())
        found=0
        for row in fixture['native_post_send']:
            path=Path(row['path'])
            if not path.is_file():continue
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row['sha256'])
            # Target comes from that actual past RUN receipt; never a future candidate.
            receipt=json.loads(Path(fixture['native_receipt']['path']).read_text())
            binding=next(e for e in receipt['events'] if e['job_id']==row['job_id'] and e['stage']=='job_binding')
            target=binding['fields']['recipient_username']
            d=Device(path.read_text())
            self.assertTrue(self.proof(d,target));found+=1
        if not found:self.skipTest('Private native originals unavailable; portable shape/negative tests remain mandatory')
        self.assertEqual(found,3)

class FocusAndReportingTests(unittest.TestCase):
    setUp = _Harness.setUp
    run_sender = _Harness.run_sender
    def test_single_remote_info_property_at_focus_and_legacy_defaults(self):
        class Editor:
            def __init__(self):self.reads=0
            @property
            def info(self):self.reads+=1;return {'bounds':{'left':1,'top':2,'right':3,'bottom':4}}
            def click(self):pass
        for version,kind,want in [(446,'outreach',1),(372,'outreach',2),(0,'outreach',2),(446,'welcome',2)]:
            d=Transport(version=version);ed=Editor()
            with patch.object(engine,'_dm_find_focus_composer',return_value=ed):
                got,err=engine._resolve_dm_text_composer(d,pkg=P,username='fixture_recipient',caller='real_send',dm_type=kind,thread_state='empty_new_thread',thread_snapshot={'composer_visible':True,'composer_signal':'resource_id_exact_composer_pkg'})
            self.assertIsNone(err);self.assertIs(got,ed);self.assertEqual(ed.reads,want)

    def test_actual_strong_deferral_sets_existing_fast_flag_without_claiming_restoration(self):
        result=self.run_sender()
        self.assertTrue(result['result'][0])
        fin=result['result'][1]['post_finalize']
        self.assertTrue(fin['post_send_fast_finalize_used'])
        self.assertTrue(fin['navigation_deferred_to_sender'])
        self.assertFalse(fin['back_to_profile_ok']);self.assertFalse(fin['back_to_search_ok'])

    def test_weak_proof_and_no_owned_restore_never_claim_strong_deferral(self):
        for kw in [dict(proof_len=1),dict(proof_len=None),dict(verified=False),dict(restore_owned=False)]:
            result=self.run_sender(**kw)
            self.assertFalse(result['result'][1].get('post_finalize',{}).get('navigation_deferred_to_sender',False))

class SearchProbeTests(unittest.TestCase):
    def execute(self,parent=False,version=446):
        ns=parent_functions({'_navigate_to_recipient_dm_thread'})
        if not parent:
            fn=next(x for x in ast.parse((ROOT/'dm_sender_engine.py').read_text()).body if isinstance(x,ast.FunctionDef) and x.name=='_navigate_to_recipient_dm_thread')
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'<candidate>','exec'),ns)
        d=Device(NATIVE[0]['stages']['empty']['xml'],version=version);calls=[];logs=[]
        ns.update(log=lambda *a,**k:logs.append((a,k)),_check_dm_sender_permission_blocker=lambda *a,**k:False,
            is_dm_thread_screen=lambda *a:False,_dm_sender_trust_global_search_ready=lambda *a:True,
            _wait_search_edittext=lambda *a:(calls.append('diagnostic_editor') or object()),
            type_search=lambda *a,**k:(calls.append('actual_type_search_current_resolver') or False))
        result=ns['_navigate_to_recipient_dm_thread'](d,'fixture_recipient',pkg=P,account_id='account',run_id='run',account_username='emitter',dm_type='outreach')
        return result,calls,logs

    def test_only_non_authorizing_probe_is_removed_real_fresh_typing_resolver_still_runs(self):
        old,oc,ol=self.execute(True);new,nc,nl=self.execute()
        self.assertEqual(old,new);self.assertEqual(oc,['diagnostic_editor','actual_type_search_current_resolver'])
        self.assertEqual(nc,['actual_type_search_current_resolver'])
        event=next(v for a,v in nl if a[1]=='dm_sender_search_field_ready')
        self.assertIsNone(event['search_field_ready'])
        self.assertEqual(event['observation'],'deferred_to_type_search_current_resolver')

    def test_legacy_search_probe_and_actual_resolver_sequence_preserved(self):
        for version in [0,372]:
            self.assertEqual(self.execute(True,version)[:2],self.execute(False,version)[:2])

class ResidualExactInverseTests(unittest.TestCase):
    def test_exact_full_parent_is_restored_and_foreign_missing_moved_duplicate_refused(self):
        import base64,zlib
        import test_certification_admission as pins
        records=json.loads(zlib.decompress(base64.b64decode(pins._RESIDUAL_WORKER1083_EXACT_INVERSE)))
        for name,row in records.items():
            source=(ROOT/name).read_text()
            parent=subprocess.check_output(['git','show',PARENT+':'+name],cwd=ROOT,text=True)
            self.assertEqual(pins.restore_worker1083_residual_exact_delta(name,source),parent)
            self.assertEqual(pins.restore_worker1083_residual_exact_delta(name,parent),parent)
            h=next(x for x in row['hunks'] if x['new']);at,new=h['offset'],h['new']
            for bad in (source+'\n',source[:at]+source[at+1:],
                        source[:at]+new+source[at:],source[:at]+source[at+len(new):]+new):
                if bad!=source:
                    with self.assertRaises(AssertionError):
                        pins.restore_worker1083_residual_exact_delta(name,bad)

    def test_only_scoped_runtime_functions_changed_and_old_inverse_records_preserved(self):
        import test_certification_admission as pins
        functions=lambda s:{n.name:ast.dump(n) for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        expected={
          'dm_sender_engine.py':({'prepare_dm_sender_global_search_surface','_navigate_to_recipient_dm_thread',
             '_safe_teardown_navigation','_resolve_dm_text_composer','_perform_real_welcome_dm_send'},set()),
          'instagram_navigation.py':(set(),{'outreach_446_restoration_thread_proved'})}
        for name,(modified,added) in expected.items():
            current=(ROOT/name).read_text()
            parent=pins.restore_worker1083_residual_exact_delta(name,current)
            a,b=functions(parent),functions(current)
            self.assertEqual(set(b)-set(a),added);self.assertEqual(set(a)-set(b),set())
            self.assertEqual({k for k in a if a[k]!=b[k]},modified)
        previous=subprocess.check_output(['git','show',PARENT+':tests/test_certification_admission.py'],cwd=ROOT,text=True)
        def constants(s):
            return {n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(s).body
                if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)
                and n.targets[0].id.endswith('_EXACT_INVERSE')}
        now=constants((ROOT/'tests/test_certification_admission.py').read_text())
        for key,value in constants(previous).items():self.assertEqual(now[key],value,key)

del _Harness  # unittest must not collect imported parent test classes as aliases.

