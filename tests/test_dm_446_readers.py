"""Portable actual-reader regression; no device/backend or send mocks."""
from __future__ import annotations
import ast
import hashlib
import json
import re
import subprocess
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import instagram_navigation as nav
import instagram_ui_446_compat as compat
import dm_ui_446_evidence as ui
import dm_thread_truth as truth
from run_latency_observability_v1 import _TimedProxy

ROOT=Path(__file__).resolve().parents[1]
DATA=json.loads((ROOT/'tests/fixtures/dm_446_readers.json').read_text())
P='com.instagram.android'
TARGET='vincentfavrephotographe'
BASE='ea69818bfb3445dba3459ea56156d3c6206031c5'

def fixture(name): return DATA['dm_truth/'+name]['xml']
def node(root,suffix): return next(n for n in root.iter('node') if n.get('resource-id','').endswith(':id/'+suffix))
def mutate(xml,change):
    root=ET.fromstring(xml);change(root);return ET.tostring(root,encoding='unicode')

class Clock:
    now=0.
    def monotonic(self):return self.now
    def perf_counter(self):return self.now
    def sleep(self,s):self.now+=s

class Node:
    def __init__(self,n):self.n=n
    @property
    def info(self):
        a=self.n.attrib;v=list(map(int,re.findall(r'-?\d+',a.get('bounds',''))))
        return dict(resourceName=a.get('resource-id',''),className=a.get('class',''),
                    text=a.get('text',''),contentDescription=a.get('content-desc',''),
                    bounds=dict(zip(('left','top','right','bottom'),v)),
                    **{k:a.get(k)=='true' for k in ('enabled','clickable','focusable')})
    def get_text(self):return self.n.get('text','')

class Selector:
    def __init__(self,d,kw):self.d=d;self.kw=kw
    def all(self):
        out=[];mapping={'resourceId':'resource-id','resourceIdMatches':'resource-id','className':'class','textContains':'text'}
        for n in ET.fromstring(self.d.live or self.d.last).iter('node'):
            good=True
            for k,v in self.kw.items():
                if k not in mapping:raise AssertionError('unsupported selector '+k)
                s=n.get(mapping[k],'')
                good &= bool(re.match(v,s)) if k.endswith('Matches') else (v in s if k=='textContains' else s==v)
            if good:out.append(Node(n))
        return out
    @property
    def count(self):return len(self.all())

    @property
    def info(self):return self.all()[0].info if self.all() else {}
    def exists(self,**kw):return bool(self.all())
    def wait(self,**kw):return bool(self.all())
    def get_text(self):return self.all()[0].get_text() if self.all() else ''

class Device:
    def __init__(self,xmls,*,version=446,package=P,clock=None):
        self.xmls=xmls if isinstance(xmls,list) else [xmls];self.last=self.xmls[0]
        self.live=None;self.reads=0;self.currents=0;self.dimensions=0;self.serial='offline-serial'
        self.package=package;self.activity='com.instagram.modal.ModalActivity';self.foregrounds=[]
        self.clock=clock;self.cost=0.;self.after_read=None
        if version:
            name,code=compat.VERSION if version==446 else ('372.0.0.48.60',377611059)
            self._instagram_search_observed_version=(id(self),self.serial,package,name,code)
            compat.retain_context(self,serial=self.serial,package=package,version_name=name,version_code=code,unsafe=False)
    def __call__(self,**kw):return Selector(self,kw)
    def app_current(self):
        self.currents+=1
        return self.foregrounds.pop(0) if self.foregrounds else dict(package=self.package,activity=self.activity)
    def window_size(self):self.dimensions+=1;return (1080,2340)
    def dump_hierarchy(self):
        value=self.xmls[min(self.reads,len(self.xmls)-1)];self.reads+=1
        if self.clock:self.clock.now+=self.cost
        if self.after_read:self.after_read(self)
        if isinstance(value,Exception):raise value
        self.last=value;return value
    def click(self,*a,**kw):raise AssertionError('UI action forbidden')
    def press(self,*a,**kw):raise AssertionError('UI action forbidden')

class DM446Tests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.p=patch.object(nav,'time',self.clock);self.p.start();self.addCleanup(self.p.stop)
        self.q=patch.object(nav,'log',lambda *a,**kw:None);self.q.start();self.addCleanup(self.q.stop)
    def state(self,d,username=TARGET,**kw):return nav.detect_dm_thread_state(d,username,pkg=d.package,**kw)
    def test_portable_fixture_integrity(self):
        for key,data in DATA.items():
            with self.subTest(key=key):self.assertEqual(hashlib.sha256(data['xml'].encode()).hexdigest(),data['sha256'])
    def test_frozen_history_bytes(self):
        self.assertEqual(hashlib.sha256((ROOT/'dm_thread_truth.py').read_bytes()).hexdigest(),'fdcda43c896ea2d89e9abe3e984ff3980efb62d640186f32ad4424c67707b5b2')
        from test_dm_446_multisurface import MultisurfaceTests
        MultisurfaceTests().test_scope_ast_and_truth_predicates_unchanged()
    def test_real_existing_compose_bubbles_never_empty(self):
        for name in ('B_30_existing_thread','B_30b_thread_stable','B_33_existing_thread_recheck'):
            with self.subTest(name=name):
                d=Device(fixture(name));state,s=self.state(d)
                self.assertEqual(state,'existing_thread');self.assertTrue(s['has_history']);self.assertEqual(d.reads,1)
    def test_positive_empty_completes_a0_history_probe(self):
        for name in ('B_37_new_thread_search','B_62_profile_message'):
            with self.subTest(name=name):
                self.clock.now=0.;d=Device(fixture(name),clock=self.clock)
                state,s=self.state(d)
                self.assertEqual(state,'empty_new_thread')
                self.assertEqual(s['reason'],'a0_history_probe_completed')
                self.assertGreaterEqual(self.clock.now,0.8)
                self.assertEqual(s['positive_empty_observations'],d.reads)
                self.assertEqual((d.currents,d.dimensions),(2*d.reads,d.reads))
    def test_composer_does_not_turn_b42_b43_b44_into_empty(self):
        for name in ('B_42_compose_result_open','B_43_new_composer_focus','B_44_new_keyboard_back'):
            with self.subTest(name=name):
                d=Device(fixture(name));self.assertTrue(nav._dm_detect_composer_signal(d,current_pkg=P,timeout_s=2.5)[0])
                self.assertEqual(self.state(d)[0],'unknown')
    def test_recipient_mismatch_and_empty_target_deny_empty(self):
        for target in ('other','',' '):
            with self.subTest(target=target):self.assertEqual(self.state(Device(fixture('B_37_new_thread_search')),target)[0],'unknown')
    def test_second_observation_failure_or_unknown_or_existing(self):
        a=fixture('B_37_new_thread_search')
        for b,expected in [(RuntimeError(),'unknown'),('<hierarchy/>','unknown'),(fixture('B_30_existing_thread'),'existing_thread'),(fixture('B_44_new_keyboard_back'),'unknown')]:
            with self.subTest(expected=expected,b=str(b)[:30]):self.assertEqual(self.state(Device([a,b]))[0],expected)
    def test_changed_qualified_display_name_preserves_recipient_truth(self):
        a=fixture('B_37_new_thread_search');b=a.replace('Vincent Favre','Another Display Name')
        self.assertNotEqual(a,b)
        state,s=self.state(Device([a,b]))
        self.assertEqual(state,'empty_new_thread')
        self.assertEqual(s['reason'],'a0_history_probe_completed')
        self.assertEqual(self.state(Device([a,b.replace(TARGET,'wrong_recipient')]))[0],'unknown')
    def test_late_positive_reply_not_accepted(self):
        d=Device(fixture('B_37_new_thread_search'),clock=self.clock);d.cost=3
        self.assertEqual(self.state(d)[0],'unknown');self.assertEqual(d.reads,1)
    def test_zero_budget_does_not_observe(self):
        d=Device(fixture('B_37_new_thread_search'));self.assertEqual(self.state(d,composer_timeout_s=0)[0],'unknown');self.assertEqual(d.reads,0)
    def test_composer_timeout_does_not_accept_late_result(self):
        d=Device(fixture('B_43_new_composer_focus'),clock=self.clock);d.cost=3
        self.assertFalse(nav._dm_detect_composer_signal(d,current_pkg=P,timeout_s=2.5)[0])
    def test_context_revoke_wrong_version_identity_package_and_proxy(self):
        changes=[lambda d:setattr(d,compat.CONTEXT_KEY,None),lambda d:setattr(d,'serial','other'),lambda d:setattr(d,'_instagram_search_observed_version',(id(d),d.serial,P,'447',1)),lambda d:delattr(d,compat.CONTEXT_KEY)]
        for change in changes:
            d=Device(fixture('B_37_new_thread_search'));change(d)
            with self.subTest(change=change):self.assertEqual(self.state(d)[0],'unknown');self.assertEqual(d.reads,0)
        d=Device(fixture('B_37_new_thread_search'))
        self.assertEqual(nav.detect_dm_thread_state(d,TARGET,pkg='foreign')[0],'unknown');self.assertEqual(d.reads,0)
        proxy=_TimedProxy(d,kind='device',emit=None,methods=frozenset())
        self.assertEqual(nav.detect_dm_thread_state(proxy,TARGET,pkg=P)[0],'empty_new_thread')
        nested=_TimedProxy(proxy,kind='device',emit=None,methods=frozenset())
        self.assertEqual(nav.detect_dm_thread_state(nested,TARGET,pkg=P)[0],'unknown')
    def test_revoked_during_dump_and_foreground_drift(self):
        d=Device(fixture('B_37_new_thread_search'));d.after_read=lambda x:setattr(x,compat.CONTEXT_KEY,None)
        self.assertEqual(self.state(d)[0],'unknown')
        for activity in ('','UnqualifiedOverlayActivity'):
            d=Device(fixture('B_37_new_thread_search'));d.activity=activity;self.assertEqual(self.state(d)[0],'unknown');self.assertEqual(d.reads,0)
        d=Device(fixture('B_37_new_thread_search'));d.foregrounds=[dict(package=P,activity=d.activity),dict(package='foreign',activity=d.activity)]
        self.assertEqual(self.state(d)[0],'unknown')
    def test_composer_false_positive_matrix(self):
        xml=fixture('B_43_new_composer_focus')
        changes=[('rid_search',lambda r:node(r,ui.COMPOSER).set('resource-id',P+':id/search_edit_text')),
            ('dm_search',lambda r:node(r,ui.COMPOSER).set('resource-id',P+':id/search_bar_real')),
            ('profile_search',lambda r:node(r,ui.COMPOSER).set('resource-id',P+':id/action_bar_search_edit_text')),
            ('meta_ai',lambda r:node(r,ui.COMPOSER).set('hint','Ask Meta AI')),
            ('generic',lambda r:node(r,ui.COMPOSER).set('resource-id','')),
            ('parent',lambda r:node(r,'composer_content_container').set('resource-id',P+':id/recipients_container')),
            ('class',lambda r:node(r,ui.COMPOSER).set('class','android.widget.Button')),
            ('foreign',lambda r:node(r,ui.COMPOSER).set('package','foreign')),
            ('hidden',lambda r:node(r,ui.COMPOSER).set('visible-to-user','false')),
            ('disabled',lambda r:node(r,ui.COMPOSER).set('enabled','false'))]
        for label,fn in changes:
            with self.subTest(label=label):
                d=Device(mutate(xml,fn));self.assertFalse(nav._dm_detect_composer_signal(d,current_pkg=P,timeout_s=2.5)[0]);self.assertIsNone(nav._dm_find_focus_composer(d))
    def test_review_occlusion_and_viewport_negatives(self):
        for name in ('offscreen_whole_window','anonymous_overlay_inside_history'):
            d=Device(DATA['dm_ui_phase_b1/'+name]['xml'])
            with self.subTest(name=name):self.assertIsNone(nav._dm_find_focus_composer(d))
    def test_live_selector_mismatch_duplicate_and_replacement(self):
        xml=fixture('B_43_new_composer_focus')
        for mutate_live in (lambda r:node(r,ui.COMPOSER).set('enabled','false'),lambda r:node(r,ui.COMPOSER).set('bounds','[0,0][1,1]'),lambda r:r.append(ET.fromstring(ET.tostring(node(r,ui.COMPOSER))))):
            d=Device(xml);d.live=mutate(xml,mutate_live);self.assertIsNone(nav._dm_find_focus_composer(d))
        d=Device([xml,'<hierarchy/>']);self.assertIsNone(nav._dm_find_focus_composer(d))
    def test_focus_positive_and_real_viewport(self):
        for name in ('B_43_new_composer_focus','B_44_new_keyboard_back'):
            with self.subTest(name=name):
                d=Device(fixture(name));el=nav._dm_find_focus_composer(d);self.assertIsNotNone(el);self.assertEqual(el.info['resourceName'],P+':id/'+ui.COMPOSER);self.assertEqual(d.reads,2)
    def test_ime_reduced_viewport_and_threshold_neighbors(self):
        for top in (1169,1168,1167,1166,1165,900,700):
            def change(root):
                content=node(root,'direct_thread_content_below_action_bar');ratio=(top-416)/(1168-416)
                for n in content.iter():
                    b=ui._rect(n)
                    if b:n.set('bounds',f'[{b[0]},{416+round((b[1]-416)*ratio)}][{b[2]},{416+round((b[3]-416)*ratio)}]')
                frame=node(root,'thread_fragment_container');b=ui._rect(frame);frame.set('bounds',f'[{b[0]},{b[1]}][{b[2]},{max(b[3],ui._rect(content)[3])}]')
            with self.subTest(top=top):self.assertTrue(nav._dm_detect_composer_signal(Device(mutate(fixture('B_43_new_composer_focus'),change)),current_pkg=P,timeout_s=2.5)[0])
        # Geometry copied from historical ADBKeyboard qualification, synthetic446.
        def shrink(root):
            for n in node(root,'direct_thread_content_below_action_bar').iter():
                b=ui._rect(n)
                if b:
                    ratio=(2205-55-416)/(2205-416)
                    n.set('bounds',f'[{b[0]},{416+round((b[1]-416)*ratio)}][{b[2]},{416+round((b[3]-416)*ratio)}]')
        self.assertIsNotNone(nav._dm_find_focus_composer(Device(mutate(fixture('B_44_new_keyboard_back'),shrink))))
    def test_372_actual_reader_results_and_no_new_dumps(self):
        for name,expected in [('A_24_existing_thread','existing_thread'),('A_25_composer_focus','existing_thread'),('A_30_new_thread_search','empty_new_thread'),('A_35_compose_result_open','empty_new_thread'),('A_45_profile_message','empty_new_thread')]:
            for version in (372,None):
                with self.subTest(name=name,version=version):
                    d=Device(fixture(name),version=version,package='com.instagram.androig');self.assertEqual(self.state(d)[0],expected);self.assertEqual(d.reads,0)
    def test_no_business_or_other_navigation_ast_change(self):
        old=ast.parse(subprocess.check_output(['git','-C',str(ROOT),'show',BASE+':instagram_navigation.py'],text=True))
        new=ast.parse((ROOT/'instagram_navigation.py').read_text())
        a={n.name:n for n in old.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))};b={n.name:n for n in new.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        allowed={'_dm_find_focus_composer','_dm_detect_composer_signal','_dm_detect_history_signal','detect_dm_thread_state','_dm_outbound_expected_message_match_mode'}
        for k in a:
            if k not in allowed:
                with self.subTest(function=k):self.assertEqual(ast.dump(a[k]),ast.dump(b[k]))

if __name__=='__main__':unittest.main()
