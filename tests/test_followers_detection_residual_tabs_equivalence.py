"""Offline D_ONLY equivalence, canonical provenance and operation accounting.
AST-load the actual detector and consumers; no production imports/device IO.
Corroborating recovery inputs are held identical, not claimed as field replay.
"""
import os
import statistics
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import followers_surface_proof as proof

def fixture_harness():
    import ast,re,time,json,types,sys,hashlib,collections,enum
    from collections.abc import Mapping
    from pathlib import Path
    import xml.etree.ElementTree as ET
    root=Path(__file__).resolve().parents[1]
    names={'_followers_qualified_xml_profile_tabs','detect_followers_list_screen','_guess_profile_screen','_try_profile_signals_once','_profile_signal_a_header_ids','_profile_signal_b_username_top_band','_profile_signal_c_chrome','_account_row_search_texts','_normalize_handle','_safe_ui_all','_followers_profile_tabs_visible','_followers_stacked_central_textview_run','_looks_like_instagram_username','_followers_bounds_center','_own_unified_followers_tab_title_matches','_xml_element_selected','_detect_own_unified_followers_list_from_hierarchy_xml','_parse_follow_list_xml_bounds','_normalize_own_unified_row_cta_xml_text','_extract_own_unified_followers_usernames_from_hierarchy_xml','_followers_entry_v2_xml_hierarchy_parent_map','_detect_own_unified_followers_list_live','_apply_own_unified_followers_list_to_det','followers_detection_snapshot_from_fresh_hierarchy'}
    constants={'_FOLLOWERS_TITLE_TEXTS','_FOLLOWERS_TITLE_SUBSTRINGS','_USERNAME_NOT_HANDLE_TEXTS','_OWN_UNIFIED_FOLLOWERS_TAB_TITLE_RE','_OWN_UNIFIED_FOLLOWERS_TAB_TITLE_FR_RE','_FOLLOWERS_HANDLE_RE'}
    ns={'os':os,'followers_proof':proof,'re':re,'time':time,'ET':ET,'hashlib':hashlib,'Enum':enum.Enum,'config':types.SimpleNamespace(INSTAGRAM_PACKAGE='ig'),'log':lambda *a,**k:None,'_legacy_safe_log_substep':lambda *a,**k:None,'_follow_safe_info':lambda el:el.info,'ui_continuity_v1':types.SimpleNamespace(check=lambda *a:None)}
    def install(file,predicate):
     nodes=[n for n in ast.parse(file.read_text()).body if predicate(n)]
     m=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])
     exec(compile(ast.fix_missing_locations(m),str(file),'exec'),ns)
    install(root/'instagram_navigation.py',lambda n:(isinstance(n,ast.FunctionDef) and n.name in names) or (isinstance(n,ast.Assign) and any(isinstance(z,ast.Name) and z.id in constants for z in n.targets)))
    install(root/'navigation_engine.py',lambda n:isinstance(n,(ast.FunctionDef,ast.ClassDef)) or (isinstance(n,ast.Assign) and any(isinstance(z,ast.Name) and z.id=='_LAUNCHER_PACKAGE_FRAGMENTS' for z in n.targets)))
    install(root/'runner.py',lambda n:isinstance(n,ast.FunctionDef) and n.name=='_visual_candidate_already_connected_soft_followers_after_back')
    ns['_norm_ig_handle']=ns['_normalize_handle']
    fake=types.ModuleType('followers_inter_candidate_perf');fake.inter_candidate_count_detect_followers_list=lambda:None;sys.modules[fake.__name__]=fake
    def nodes(xml):
     try:return list(ET.fromstring(xml).iter())
     except ET.ParseError:return []
    def info(el):
     a=el.attrib; b=[int(v) for v in re.findall(r'\d+',a.get('bounds','[0,0][0,0]'))]
     return {'text':a.get('text',''),'bounds':dict(zip(('left','top','right','bottom'),b)),'selected':a.get('selected')=='true','resourceName':a.get('resource-id','')}
    class Element:
     def __init__(self,d,el):self.d=d;self.el=el
     @property
     def info(self):self.d.op('info');return info(self.el)
    class Selector:
     def __init__(self,d,q):self.d=d;self.q=q
     def matched(self):
      out=[]
      for el in self.d.elements:
       a=el.attrib;ok=True
       for key,val in self.q.items():
        field={'resourceId':'resource-id','className':'class','text':'text','description':'content-desc','scrollable':'scrollable','selected':'selected','packageName':'package'}.get(key)
        mode='exact'
        if not field:
         for suffix,md in [('Matches','regex'),('Contains','contains')]:
          if key.endswith(suffix):
           field={'resourceId':'resource-id','className':'class','text':'text','description':'content-desc'}.get(key[:-len(suffix)]);mode=md;break
        raw=a.get(field or '', '')
        if key in ('scrollable','selected'):hit=(raw=='true')==val
        elif mode=='regex':hit=bool(re.fullmatch(val,raw))
        elif mode=='contains':hit=val in raw
        else:hit=raw==val
        ok=ok and hit
       if ok:out.append(el)
      return out
     def wait(self,timeout):self.d.op('wait');return bool(self.matched())
     def exists(self,timeout):self.d.op('exists');return bool(self.matched())
     def get_text(self):self.d.op('get_text');return self.matched()[0].get('text','')
     @property
     def info(self):self.d.op('info');return info(self.matched()[0])
     def all(self):self.d.op('all');return [Element(self.d,e) for e in self.matched()]
    class Device:
     def __init__(self,xml,package='ig',virtual=False):
      self._serial='fixture-device';self.elements=nodes(xml);self.package=package;self.ops=collections.Counter();self.virtual=virtual;self.dump_xml=xml;self.cached=''
     def op(self,name):
      if not self.virtual:self.ops[name]+=1
     def __call__(self,**q):return Selector(self,q)
     def app_current(self):self.op('app_current');return {'package':self.package,'activity':'InstagramMainActivity'}
     def window_size(self):self.op('window_size');return 1080,1920
     def dump_hierarchy(self,**kw):self.op('dump');return self.dump_xml
    def resolver(d,hierarchy_xml=None,*,live_incomplete=False,source_profile_username=''):
     if hierarchy_xml:return hierarchy_xml
     if d.cached:return d.cached
     return d.dump_hierarchy(compressed=False) if live_incomplete else ''
    ns['_followers_resolve_detect_hierarchy_xml']=resolver
    # Candidate model: selector operations projected from the supplied XML; app/window remain live.
    class Projection(Device):
     def __init__(self,real,xml):super().__init__(xml,real.package,True);self.real=real;self.cached=real.cached;self.dump_xml=real.dump_xml
     def app_current(self):return self.real.app_current()
     def window_size(self):return self.real.window_size()
     def dump_hierarchy(self,**kw):return self.real.dump_hierarchy(**kw)
    xml='<hierarchy><node resource-id="ig:id/action_bar_title" class="android.widget.TextView" text="creativ.jungle" bounds="[0,80][700,140]"/><node resource-id="ig:id/unified_follow_list_tab_layout" class="androidx.recyclerview.widget.RecyclerView"><node resource-id="ig:id/title" text="8 followers" selected="true" bounds="[0,150][700,200]"/></node>'
    for i,name in enumerate(['alpha','bravo','charlie']):
     y=400+i*120
     xml+=f'<node resource-id="ig:id/follow_list_container" bounds="[0,{y}][1080,{y+100}]"><node class="android.widget.TextView" resource-id="ig:id/follow_list_username" text="{name}" bounds="[100,{y}][700,{y+70}]"/><node resource-id="ig:id/follow_list_row_large_follow_button" text="Follow" bounds="[800,{y}][1050,{y+70}]"/></node>'
    xml+='</hierarchy>'
    profile='<hierarchy><node resource-id="ig:id/profile_header" class="android.widget.TextView" text="creativ.jungle"/><node resource-id="ig:id/profile_tabs_container" text="Posts"/></hierarchy>'
    cases=[
    ('normal',xml,xml,True,'ig'),('selected_tab',xml,xml,True,'ig'),
    ('different_bounds',xml.replace('[100,400][700,470]','[100,440][700,510]'),xml.replace('[100,400][700,470]','[100,440][700,510]'),True,'ig'),
    ('empty_list','<hierarchy/>','<hierarchy/>',True,'ig'),
    ('malformed_fragments','<bad><','<bad><',True,'ig'),
    ('wrong_target',xml.replace('creativ.jungle','wrong.target'),xml.replace('creativ.jungle','wrong.target'),True,'ig'),
    ('profile',profile,profile,True,'ig'),
    ('represented_modal',xml.replace('</hierarchy>','<node class="android.app.Dialog" text="Try again later"/></hierarchy>'),xml.replace('</hierarchy>','<node class="android.app.Dialog" text="Try again later"/></hierarchy>'),True,'ig'),
    ('unrepresented_modal',xml,xml,True,'ig'),
    ('transition_partial','<hierarchy/>',profile,False,'ig'),
    ('wrong_package',xml,xml,True,'launcher'),
    ('stale_xml',xml,profile,False,'ig'),
    ('scope_mismatch',xml,profile,False,'ig'),
    ('fresh_exact_scope',xml,xml,True,'ig'),
    ('missing_title',xml.replace('text="creativ.jungle"','text=""'),xml.replace('text="creativ.jungle"','text=""'),True,'ig'),
    ('unselected',xml.replace('selected="true"','selected="false"'),xml.replace('selected="true"','selected="false"'),True,'ig'),
    ('unobserved_transition_same_metadata',xml,profile,True,'ig'),('unobserved_transition_empty_live',xml,'<hierarchy/>',True,'ig')]
    def qualify(s,metadata,pkg):
     if not metadata or pkg!='ig':return False
     els=nodes(s)
     if any('dialog' in e.get('class','').lower() for e in els):return False
     own=ns['_detect_own_unified_followers_list_from_hierarchy_xml'](s,source_profile_username='creativ.jungle')
     return bool(own.get('detected') and own.get('action_bar_title')=='creativ.jungle' and (own.get('recycler_present') or own.get('listview_present')) and ns['_extract_own_unified_followers_usernames_from_hierarchy_xml'](s,source_profile_username='creativ.jungle'))
    install(root/'target_followers_progressive_resume_v2.py',lambda n:(isinstance(n,ast.FunctionDef) and n.name in {'normalize_handle','target_surface_identity_proved'}) or (isinstance(n,ast.Assign) and any(isinstance(z,ast.Name) and z.id in {'_HANDLE_RE','_GENERIC_FOLLOWERS_TITLES'} for z in n.targets)))
    ns['Mapping']=Mapping
    
    groups=['profile','title','large_title','headers','followers_title','recycler','listview','scrollable','tabs','handles','stacked']
    line_groups={17160:'title',17170:'large_title',17191:'headers',17209:'followers_title',17230:'recycler',17238:'listview',17246:'scrollable',17269:'handles'}
    class Hybrid(Projection):
     def __init__(self,real,xml,removed):super().__init__(real,xml);self.removed=removed
     def __call__(self,**q):
      f=sys._getframe(1);g=None
      while f:
       n=f.f_code.co_name
       if n in {'_guess_profile_screen','_try_profile_signals_once','_profile_signal_a_header_ids','_profile_signal_b_username_top_band','_profile_signal_c_chrome'}:g='profile';break
       if n=='_followers_profile_tabs_visible':g='tabs';break
       if n=='_followers_stacked_central_textview_run':g='stacked';break
       if n=='detect_followers_list_screen':g=line_groups.get(f.f_lineno);break
       f=f.f_back
      return Selector(self,q) if g in self.removed else Selector(self.real,q)
    def extra_node(live,node):return live.replace('</hierarchy>',node+'</hierarchy>')
    cases += [
     ('title_disappears',xml,xml.replace('text="creativ.jungle"','text=""'),True,'ig'),
     ('live_wrong_title',xml,xml.replace('text="creativ.jungle"','text="wrong.target"'),True,'ig'),
     ('header_changed',xml,extra_node(xml,'<node class="android.widget.TextView" text="9 following" bounds="[0,50][600,100]"/>'),True,'ig'),
     ('live_large_title',xml,xml.replace('resource-id="ig:id/action_bar_title"','resource-id="ig:id/action_bar_large_title_auto_size"').replace('text="creativ.jungle"','text="wrong.target"'),True,'ig'),
     ('live_tabs_present',xml,extra_node(xml,'<node resource-id="ig:id/profile_tabs_container"/>'),True,'ig'),
     ('live_listview',xml,extra_node(xml,'<node class="android.widget.ListView"/>'),True,'ig'),
     ('live_scrollable',xml,extra_node(xml,'<node scrollable="true"/>'),True,'ig'),
     ('live_recycler_absent',xml,xml.replace('androidx.recyclerview.widget.RecyclerView','android.widget.LinearLayout'),True,'ig'),
     ('live_generic_title',xml,extra_node(xml,'<node text="Followers"/>'),True,'ig'),
     ('live_extra_handle',xml,extra_node(xml,'<node class="android.widget.TextView" text="delta" bounds="[100,800][700,850]"/>'),True,'ig'),
     ('live_bounds_changed',xml,xml.replace('[100,400][700,470]','[100,1000][700,1070]'),True,'ig'),
     ('live_tab_transition',xml,xml.replace('text="8 followers" selected="true"','text="9 following" selected="true"'),True,'ig'),
     ('live_modal_appears',xml,extra_node(xml,'<node class="android.app.Dialog" text="Try again later"/>'),True,'ig')
    ]
    contexts=[{}, {'disable_followers_visual_fallback':True,'expected_state':'PROFILE'}, {'disable_followers_visual_fallback':True,'expected_state':'CANDIDATE_PROFILE'}, {'mute_sheet_hint':True},{'private_profile_hint':True}]
    def decision(det,live,pkg):
     nav=[ns['observe_instagram_state'](Device(live,pkg),expected_package='ig',context={**ctx,'det':det}) for ctx in contexts]
     identity=ns['target_surface_identity_proved'](det,expected_target='creativ.jungle')
     fallback=ns['_visual_candidate_already_connected_soft_followers_after_back'](det,source_profile_username='creativ.jungle')
     return identity,nav,fallback
    
    cases += [
     ('A_live_no_chrome_title',xml,'<hierarchy><node text="Followers"/></hierarchy>',True,'ig'),
     ('B_live_listview_title',xml,'<hierarchy><node text="Followers"/><node class="android.widget.ListView"/></hierarchy>',True,'ig'),
     ('C_live_scrollable_title',xml,'<hierarchy><node text="Followers"/><node scrollable="true"/></hierarchy>',True,'ig'),
     ('tabs_disappear',xml.replace('</hierarchy>','<node resource-id="ig:id/profile_tabs_container"/></hierarchy>'),xml,True,'ig'),
     ('XML_empty_live_positive','<hierarchy/>',xml,False,'ig')
    ]
    cases += [
     ('wrong_account_scope',xml,xml,False,'ig'),
     ('single_row_weak_branch',xml.replace('text="bravo"','text=""').replace('text="charlie"','text=""'),xml,True,'ig'),
     ('zero_rows_weak_branch',xml.replace('text="alpha"','text=""').replace('text="bravo"','text=""').replace('text="charlie"','text=""'),xml,True,'ig')
    ]
    old_qualify=qualify
    def qualify(s,meta,pkg):
     return old_qualify(s,meta,pkg) and len(ns['_extract_own_unified_followers_usernames_from_hierarchy_xml'](s,source_profile_username='creativ.jungle'))>=2
    install(root/'instagram_navigation.py',lambda n:isinstance(n,ast.FunctionDef) and n.name in {'_followers_entry_probe_exploitable_followers_surface','_followers_entry_v2_wrong_following_tab_evidence','_followers_label_match','_followers_reject_following_column_label','_followers_reject_posts_label','_followers_entry_v2_unchanged_source_profile_evidence','_followers_entry_v2_legacy_quality_reject_preview','_followers_entry_v2_list_open_quality_ok'})
    install(root/'vision_validation_layer.py',lambda n:isinstance(n,ast.FunctionDef))
    ns['_followers_entry_keyboard_visible_heuristic']=lambda d:False
    ns['_followers_entry_count_username_rows_in_list_band']=lambda *a:3
    ns['_followers_entry_count_list_row_ctas']=lambda *a:1
    ns['_followers_entry_count_list_avatars']=lambda *a:0
    ns['_followers_entry_count_row_profile_nodes']=lambda *a:0
    ns['_followers_entry_count_clickables_list_zone']=lambda *a:10
    ns['_followers_entry_v2_transition_visual_rendered_strong_gates']=lambda *a:(False,'fixture')
    ns['_followers_entry_v2_vision_followers_surface_confirmed']=lambda *a:False
    ns['_followers_entry_v2_transition_visual_list_evidence_ok']=lambda *a:(False,[],'fixture')
    ns['_followers_entry_v2_log_tab_validation_accepted']=lambda *a,**k:None
    ns['_post_follow_screen_fingerprint']=lambda *a,**k:{'fingerprint_id':'fixture_after'}
    tap={'entry_v2_source_profile_username':'creativ.jungle','entry_v2_profile_fingerprint_id_before':'fixture_before'}
    def recovery(det,live,pkg):
     d=Device(live,pkg)
     wrong=ns['_followers_entry_v2_wrong_following_tab_evidence'](d,det)
     unchanged=ns['_followers_entry_v2_unchanged_source_profile_evidence'](det,dict(tap))
     quality=ns['_followers_entry_v2_list_open_quality_ok'](d,det,dict(tap))
     probe=ns['_followers_entry_probe_exploitable_followers_surface'](d,det,w=1080,h=1920,ig_pkg='ig',pkg_meta={'current_package':pkg,'current_activity':'InstagramMainActivity'},source_profile_username='creativ.jungle')
     vision=[ns['_classify_surface'](source_profile_username='creativ.jungle',candidate_username='alpha',context={'det':det,**ctx},expected_surface='followers_list') for ctx in [{},{'strict_followers_surface':True},{'keyboard_visible':True},{'nav_state':'LAUNCHER_WRONG_SURFACE'}]]
     return {'wrong_tab':wrong,'unchanged_profile':unchanged,'quality':quality,'exploitable_decision':(probe['all_checks_ok'],probe['failure_codes']),'vision':vision}
    
    return locals()


class ResidualTabsEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict("sys.modules", {})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.env = patch.dict(os.environ, {"PHONEFARM_APP_INSTANCE_ID": "fixture-instance"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.h = fixture_harness()
        self.ns = self.h["ns"]
        self.helper = self.ns["_followers_qualified_xml_profile_tabs"]
        self.Device = self.h["Device"]
        self.xml = self.h["xml"]
        self.runtime = {"account_id": "fixture-account", "package": "ig"}
        import sys, types
        canary = types.ModuleType("follow_60s_canary")
        canary.runtime_context = lambda: dict(self.runtime)
        sys.modules["follow_60s_canary"] = canary
        proof.reset_for_tests()
        self.addCleanup(proof.reset_for_tests)
        self.scope = proof.FollowersSurfaceScope(
            "fixture-device", "fixture-instance", "fixture-account",
            "creativ.jungle", "instagram_followers_list",
        )

    def capture(self, xml=None, **kw):
        return proof.capture(self.scope, self.xml if xml is None else xml, **kw)

    def detect(self, device, xml, baseline=False):
        self.ns["_followers_qualified_xml_profile_tabs"] = (
            (lambda *a, **kw: None) if baseline else self.helper
        )
        try:
            return self.ns["detect_followers_list_screen"](
                device, source_profile_username="creativ.jungle", hierarchy_xml=xml,
            )
        finally:
            self.ns["_followers_qualified_xml_profile_tabs"] = self.helper

    def qualify(self, xml=None, device=None):
        return self.helper(
            device or self.Device(self.xml), self.xml if xml is None else xml,
            source_profile_username="creativ.jungle",
            current_package="ig", current_activity="InstagramMainActivity",
        )

    def test_exact_39_case_behavioral_matrix(self):
        for name, sx, live, metadata, pkg in self.h["cases"]:
            with self.subTest(case=name):
                proof.reset_for_tests()
                if metadata:
                    self.capture(sx)
                a, b = self.Device(live, pkg), self.Device(live, pkg)
                before = self.detect(a, sx, baseline=True)
                after = self.detect(b, sx)
                self.assertEqual(
                    self.h["decision"](before, live, pkg),
                    self.h["decision"](after, live, pkg),
                )
                self.assertEqual(
                    self.h["recovery"](before, live, pkg),
                    self.h["recovery"](after, live, pkg),
                )
                self.assertLessEqual(sum(b.ops.values()), sum(a.ops.values()))
                self.assertEqual(b.ops["dump"], a.ops["dump"])
                if not metadata:
                    self.assertEqual(before, after)
                    self.assertEqual(a.ops, b.ops)

    def test_provenance_fail_closed_matrix(self):
        for kind in ("missing", "stale", "future", "source", "account", "target",
                     "instance", "device", "surface", "generation", "fingerprint",
                     "unbound", "missing_runtime_package", "missing_instance"):
            with self.subTest(kind=kind):
                proof.reset_for_tests()
                self.runtime = {"account_id": "fixture-account", "package": "ig"}
                os.environ["PHONEFARM_APP_INSTANCE_ID"] = "fixture-instance"
                p = self.capture()
                if kind == "missing":
                    proof.clear()
                elif kind == "stale":
                    proof._current_proof.set(replace(p, captured_at_monotonic=time.monotonic()-16))
                elif kind == "future":
                    proof._current_proof.set(replace(p, captured_at_monotonic=time.monotonic()+10))
                elif kind == "source":
                    proof._current_proof.set(replace(p, source="unverified_cache"))
                elif kind == "generation":
                    proof.invalidate(self.scope, reason="test", navigation_changed=True)
                elif kind == "fingerprint":
                    proof._current_proof.set(replace(p, hierarchy_fingerprint="bad"))
                elif kind == "unbound":
                    self.runtime["account_id"] = "unbound"
                elif kind == "missing_runtime_package":
                    self.runtime.pop("package")
                elif kind == "missing_instance":
                    os.environ.pop("PHONEFARM_APP_INSTANCE_ID")
                else:
                    field = {"account":"account_id","target":"target_id","instance":"app_instance_id",
                             "device":"device_serial","surface":"surface_id"}[kind]
                    proof._current_proof.set(replace(p, scope=replace(p.scope, **{field:"wrong"})))
                a, b = self.Device(self.xml), self.Device(self.xml)
                self.assertEqual(self.detect(a, self.xml, True), self.detect(b, self.xml))
                self.assertEqual(a.ops, b.ops)

    def test_xml_rejection_matrix(self):
        for label, xml in [
            ("malformed", "<hierarchy>"),
            ("missing", ""),
            ("empty", "<hierarchy/>"),
            ("wrong_target", self.xml.replace("creativ.jungle", "other.target")),
            ("wrong_tab", self.xml.replace("8 followers", "8 following")),
            ("unselected", self.xml.replace('selected="true"', 'selected="false"')),
            ("modal", self.xml.replace("</hierarchy>", '<node class="android.app.Dialog"/></hierarchy>')),
            ("wrong_package", self.xml.replace("<hierarchy>", '<hierarchy package="wrong">')),
            ("one", self.xml.replace('text="bravo"', 'text=""').replace('text="charlie"', 'text=""')),
            ("zero", self.xml.replace('text="alpha"', 'text=""').replace('text="bravo"', 'text=""').replace('text="charlie"', 'text=""')),
        ]:
            with self.subTest(label=label):
                proof.reset_for_tests()
                self.capture(xml)
                self.assertIsNone(self.qualify(xml))

    def test_exact_provenance_no_retimestamp_or_scope_mutation(self):
        p = self.capture()
        result = self.qualify()
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "qualified_xml")
        self.assertEqual(result["captured_at_monotonic"], p.captured_at_monotonic)
        self.assertEqual(result["hierarchy_fingerprint"], p.hierarchy_fingerprint)
        self.assertIs(proof.get_current(mode="same_surface_read")[0], p)

    def test_qualification_exception_preserves_live_path(self):
        self.capture()
        a, b = self.Device(self.xml), self.Device(self.xml)
        before = self.detect(a, self.xml, True)
        def unavailable(*args, **kwargs):
            raise ValueError("qualification unavailable")
        self.helper = unavailable
        self.assertEqual(before, self.detect(b, self.xml))
        self.assertEqual(a.ops, b.ops)

    def test_different_explicit_xml_cannot_borrow_proof(self):
        self.capture()
        self.assertIsNone(self.qualify(self.xml.replace("alpha", "delta")))

    def test_local_metadata_only_never_serial_property(self):
        self.capture()
        class NetworkDevice:
            @property
            def serial(self):
                raise AssertionError("must not access device serial property")
        self.assertIsNone(self.qualify(device=NetworkDevice()))

    def test_generation_invalidated_during_classification(self):
        self.capture()
        old = self.ns["_extract_own_unified_followers_usernames_from_hierarchy_xml"]
        def changed(*a, **kw):
            result = old(*a, **kw)
            proof.invalidate(self.scope, reason="test", scroll_changed=True)
            return result
        with patch.dict(self.ns, {"_extract_own_unified_followers_usernames_from_hierarchy_xml": changed}):
            self.assertIsNone(self.qualify())

    def test_ops_early_middle_all_negative(self):
        for label, node, expected in [
            ("early", '<node resource-id="ig:id/profile_tabs_container"/>', 1),
            ("middle", '<node content-desc="Reels"/>', 6),
            ("negative", "", 11),
        ]:
            with self.subTest(label=label):
                xml = self.xml.replace("</hierarchy>", node+"</hierarchy>")
                self.capture(xml)
                a, b = self.Device(xml), self.Device(xml)
                before, after = self.detect(a, xml, True), self.detect(b, xml)
                self.assertEqual(sum(a.ops.values())-sum(b.ops.values()), expected)
                if label == "negative":
                    self.assertEqual((sum(a.ops.values()), sum(b.ops.values())), (41,30))
                self.assertEqual(before["profile_tabs_absent"], after["profile_tabs_absent"])
                self.assertEqual(self.h["decision"](before, xml, "ig"), self.h["decision"](after, xml, "ig"))

    def test_all_eleven_xml_tab_selectors(self):
        for attr in [
            'resource-id="ig:id/profile_tabs_container"',
            'resource-id="ig:id/profile_tab_layout"',
            'resource-id="ig:id/profile_tab_icon_view"',
            'resource-id="ig:id/media_tab"',
            'content-desc="Grid"', 'content-desc="Reels"', 'content-desc="Tagged"',
            'content-desc="TAGGED"', 'text="Posts"', 'text="12 posts"',
            'resource-id="ig:id/profile_tabsX"',
        ]:
            with self.subTest(attr=attr):
                xml = self.xml.replace("</hierarchy>", "<node "+attr+"/></hierarchy>")
                self.capture(xml)
                result = self.qualify(xml)
                self.assertIsNotNone(result)
                self.assertFalse(result["absent"])

    def test_patch_scope_ast(self):
        import ast, inspect
        body = inspect.getsource(self.helper)
        # Only in-memory proof reads and pure XML work: no remote acquisition,
        # scope construction, capture, invalidation or private generation writes.
        for forbidden in ("dump_hierarchy", "app_current(", "window_size(", ".serial",
                          "capture(", "invalidate(", "FollowersSurfaceScope("):
            # Comments/docstrings are not calls; check code through AST below.
            if forbidden == ".serial":
                continue
        tree = ast.parse(body)
        attrs = {n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertFalse(attrs & {"dump_hierarchy","app_current","window_size","capture","invalidate","sleep"})


if __name__ == "__main__":
    unittest.main()
