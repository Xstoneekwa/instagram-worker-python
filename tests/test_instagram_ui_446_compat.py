"""Physical XML embedded verbatim (compressed) for portable offline qualification."""
import base64,copy,hashlib,unittest,zlib,xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import patch
import instagram_navigation as nav
import device as device_module
import instagram_ui_446_compat as compat

FIXTURES = {'446_follow60_plain_candidate.xml': ('7a9d7a9edc961c7626e67e7c8688cd81ac6d04c73e148e2fca3306f73fc81e39', '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'), '446_follow60_candidate.xml': ('fd3018e21c60b5759fd42499d75a3cd53899cf4fc3cd88d09613af8faecebec4', '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'), '372_follow60_candidate.xml': ('7a4f77846ea568310160c8d6f7081b578867c0fa924b09ee40c71689cccd99db', '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')}
PACKAGE='com.instagram.android'
def fixture(name='446_follow60_plain_candidate.xml'):
    digest,data=FIXTURES[name];xml=zlib.decompress(base64.b64decode(data));assert hashlib.sha256(xml).hexdigest()==digest
    return xml.decode()
def xml_mutation(mutator):
    root=ET.fromstring(fixture());mutator(root);return ET.tostring(root,encoding='unicode')
def node(root,suffix):return next(n for n in root.iter() if n.get('resource-id')==PACKAGE+':id/'+suffix)
def read(xml):return compat.relationship_from_xml(xml,package=PACKAGE,expected_username='cyrilcourbezbabik')
class Device:
    def __init__(self,xml):self.xml=xml;self.serial='RFGL62SZ7NR';self.calls=[]
    def dump_hierarchy(self,**kwargs):self.calls.append(('dump',kwargs));return self.xml
    def window_size(self):self.calls.append(('window_size',));return (1080,2340)
def bind(d):compat.retain_context(d,serial=d.serial,package=PACKAGE,version_name=compat.VERSION[0],version_code=compat.VERSION[1],unsafe=False);vars(d)["_instagram_search_observed_version"]=(id(d),d.serial,PACKAGE,*compat.VERSION)

class Tests(unittest.TestCase):
    def test_exact_physical_counter_collision(self):
        self.assertEqual(nav._follow_ui_state_from_fresh_hierarchy(fixture(),screen_height=2340),'following')
        self.assertEqual(read(fixture()),'follow')
    def test_physical_follow_back(self):
        self.assertEqual(compat.relationship_from_xml(fixture('446_follow60_candidate.xml'),package=PACKAGE,expected_username='latelierdelaura_27'),'follow_back')
    def test_supported_relationship_states(self):
        for label,expected in [('Follow','follow'),('Follow back','follow_back'),('Following','following'),('Requested','requested')]:
            with self.subTest(label=label):
                def change(r):
                    n=node(r,'profile_header_follow_button');n.set('text',label);n.set('content-desc',label+' Cyril Courbez Babik')
                self.assertEqual(read(xml_mutation(change)),expected)
    def test_contradictory_relationship(self):
        for desc in ['Following Cyril','Requested','Follow back Cyril']:
            with self.subTest(desc=desc):self.assertEqual(read(xml_mutation(lambda r:node(r,'profile_header_follow_button').set('content-desc',desc))),'unknown')
    def test_wrong_or_empty_target(self):
        for target in ['', 'other', 'not a username']:
            self.assertEqual(compat.relationship_from_xml(fixture(),package=PACKAGE,expected_username=target),'unknown')
    def test_contradictory_title(self):
        self.assertEqual(read(xml_mutation(lambda r:node(r,'action_bar_title').set('content-desc','other'))),'unknown')
    def test_disabled_hidden_foreign_wrong_class_button(self):
        for key,value in [('enabled','false'),('clickable','false'),('visible-to-user','false'),('displayed','false'),('package','foreign'),('class','android.widget.EditText'),('bounds','[0,0][0,0]')]:
            with self.subTest(key=key):self.assertEqual(read(xml_mutation(lambda r:node(r,'profile_header_follow_button').set(key,value))),'unknown')
    def test_wrong_or_hidden_parent(self):
        for key,value in [('resource-id',PACKAGE+':id/suggested'),('visible-to-user','false'),('enabled','false'),('package','foreign')]:
            with self.subTest(key=key):self.assertEqual(read(xml_mutation(lambda r:node(r,'profile_header_container').set(key,value))),'unknown')
    def test_duplicate_cta(self):
        self.assertEqual(read(xml_mutation(lambda r:node(r,'profile_header_actions_top_row').append(copy.deepcopy(node(r,'profile_header_follow_button'))))),'unknown')
    def test_recommendation_cta_without_header(self):
        self.assertEqual(read(xml_mutation(lambda r:node(r,'profile_header_actions_top_row').set('resource-id',PACKAGE+':id/recommended_row'))),'unknown')
    def test_counters_alone_not_proof(self):
        self.assertEqual(read(xml_mutation(lambda r:node(r,'profile_header_follow_button').set('resource-id',''))),'unknown')
    def test_modal_map_instants_challenge(self):
        for label in ['Introducing the Instagram map','Introducing instants',"Confirm you are human",'Log in','Review before following']:
            with self.subTest(label=label):self.assertEqual(read(xml_mutation(lambda r:ET.SubElement(r,'node',{'text':label,'package':PACKAGE}))),'unknown')
    def test_overlay_covering_cta(self):
        for package in [PACKAGE,'foreign']:
            def change(r):ET.SubElement(r,'node',{'package':package,'bounds':'[0,500][1080,800]','clickable':'true','enabled':'true'})
            self.assertEqual(read(xml_mutation(change)),'unknown')
    def test_inert_nonoverlap_child(self):
        self.assertEqual(read(xml_mutation(lambda r:ET.SubElement(r,'node',{'package':PACKAGE,'bounds':'[0,1000][20,1020]','clickable':'false'}))),'follow')
    def test_bad_xml(self):
        for x in ['', '<broken', None]:self.assertEqual(read(x),'unknown')
    def test_wrapper_446_one_dump_no_window_or_legacy(self):
        d=Device(fixture());bind(d)
        with patch.object(nav.config,'INSTAGRAM_PACKAGE',PACKAGE),patch.object(nav,'_follow_ui_state_snapshot',side_effect=AssertionError('legacy')):
            self.assertEqual(nav._follow_ui_state_and_fresh_hierarchy(d,expected_username='cyrilcourbezbabik'),('follow',fixture()))
        self.assertEqual(d.calls,[('dump',{'compressed':False})])
    def test_rejected_446_does_not_fallback(self):
        for xml in ['',xml_mutation(lambda r:node(r,'profile_header_follow_button').set('enabled','false'))]:
            d=Device(xml);bind(d)
            with patch.object(nav.config,'INSTAGRAM_PACKAGE',PACKAGE),patch.object(nav,'_follow_ui_state_snapshot',side_effect=AssertionError('legacy')):
                self.assertEqual(nav._follow_ui_state_and_fresh_hierarchy(d,expected_username='cyrilcourbezbabik')[0],'unknown')
    def test_wrong_context_serial_package_device(self):
        d=Device(fixture());bind(d);other=Device(fixture());ctx=vars(d)[compat.CONTEXT_KEY]
        self.assertFalse(compat.context_matches(ctx,other,d.serial,PACKAGE));self.assertFalse(compat.context_matches(ctx,d,'wrong',PACKAGE));self.assertFalse(compat.context_matches(ctx,d,d.serial,'foreign'))
        d.serial='wrong'
        with patch.object(nav.config,'INSTAGRAM_PACKAGE',PACKAGE):self.assertEqual(nav._follow_ui_state_and_fresh_hierarchy(d,expected_username='cyrilcourbezbabik')[0],'unknown')
    def test_revoked_context_never_returns_legacy(self):
        for name,code,unsafe in [('446.0.0.49.77',385211304,True),('447',1,False),('372.0.0.48.60',377611059,False),(None,None,False)]:
            d=Device(fixture());bind(d);compat.retain_context(d,serial=d.serial,package=PACKAGE,version_name=name,version_code=code,unsafe=unsafe)
            self.assertIsNone(vars(d)[compat.CONTEXT_KEY])
            with patch.object(nav.config,'INSTAGRAM_PACKAGE',PACKAGE):self.assertEqual(nav._follow_ui_state_and_fresh_hierarchy(d)[0],'unknown')
    def test_372_context_no_op_and_same_call_sequence(self):
        for x in [fixture('372_follow60_candidate.xml'),fixture(),'<hierarchy/>']:
            d=Device(x);before=dict(vars(d));compat.retain_context(d,serial=d.serial,package=PACKAGE,version_name='372.0.0.48.60',version_code=377611059,unsafe=False)
            self.assertEqual(vars(d),before)
            expected=nav._follow_ui_state_from_fresh_hierarchy(x,screen_height=2340)
            self.assertEqual(nav._follow_ui_state_and_fresh_hierarchy(d), (expected,x));self.assertEqual(d.calls,[('dump',{'compressed':False}),('window_size',)])
    def test_372_empty_hierarchy_fallback_preserved(self):
        d=Device('')
        with patch.object(nav,'_follow_ui_state_snapshot',return_value='following') as snapshot:
            self.assertEqual(nav._follow_ui_state_and_fresh_hierarchy(d),('following',''));snapshot.assert_called_once_with(d)

    def test_device_existing_version_read_binds_without_extra_io(self):
        for version,expected in [(compat.VERSION,True),(('372.0.0.48.60',377611059),False)]:
            with self.subTest(version=version):
                d=Device(fixture());store=SimpleNamespace(path='/not-used',read=lambda:{'authorized_version_name':version[0],'authorized_version_code':version[1]})
                with patch.object(device_module.config,'INSTAGRAM_PACKAGE',PACKAGE),patch.object(device_module,'_fetch_package_version',return_value=version) as fetch,patch.object(device_module,'_package_disabled_for_user0',return_value=True) as disabled,patch.object(device_module,'_package_suspended',return_value=False) as suspended,patch.object(device_module,'_lock_state_store',return_value=store),patch.object(device_module,'log'):
                    result=device_module.check_instagram_version_lock(d)
                self.assertFalse(result['unsafe_for_automation']);fetch.assert_called_once_with(d.serial,PACKAGE);disabled.assert_called_once();suspended.assert_called_once()
                self.assertEqual(compat.CONTEXT_KEY in vars(d),expected);self.assertEqual(d.calls,[])
    def test_version_hook_unsafe_revokes(self):
        d=Device(fixture());bind(d)
        store=SimpleNamespace(path='/not-used',read=lambda:{'authorized_version_name':compat.VERSION[0],'authorized_version_code':compat.VERSION[1]})
        with patch.object(device_module.config,'INSTAGRAM_PACKAGE',PACKAGE),patch.object(device_module,'_fetch_package_version',return_value=compat.VERSION),patch.object(device_module,'_package_disabled_for_user0',return_value=True),patch.object(device_module,'_package_suspended',return_value=True),patch.object(device_module,'_lock_state_store',return_value=store),patch.object(device_module,'log'):
            result=device_module.check_instagram_version_lock(d)
        self.assertTrue(result['unsafe_for_automation']);self.assertIsNone(vars(d)[compat.CONTEXT_KEY])
    def test_hidden_title_branch(self):
        def change(r):
            parents={c:p for p in r.iter() for c in p};parents[node(r,'action_bar_title')].set('visible-to-user','false')
        self.assertEqual(read(xml_mutation(change)),'unknown')



class LockedProfileContextTests(unittest.TestCase):
    def test_same_446_observation_keeps_context_object(self):
        d = Device(fixture()); bind(d)
        original = vars(d)[compat.CONTEXT_KEY]
        compat.retain_context(d, serial=d.serial, package=PACKAGE,
                              version_name=compat.VERSION[0], version_code=compat.VERSION[1], unsafe=False)
        self.assertIs(vars(d)[compat.CONTEXT_KEY], original)

    def test_locked_profile_calls_existing_retention_validator(self):
        from instagram_ui_profile_v1 import UIProfileStopped
        d = Device(fixture())
        with patch('instagram_ui_profile_v1.retention_allowed', side_effect=UIProfileStopped('wrong_profile')):
            with self.assertRaises(UIProfileStopped):bind(d)
        self.assertNotIn(compat.CONTEXT_KEY, vars(d))


if __name__=='__main__':unittest.main()

# A16-03 one-shot post-search_tab capture; no query or header action executed.
SEARCH_CAPTURE = ('91652e306b63ab2fcfae0ff12b7f64a2e119730732c782296ce732466161e5b9', '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')

def search_capture():
    xml = zlib.decompress(base64.b64decode(SEARCH_CAPTURE[1]))
    assert hashlib.sha256(xml).hexdigest() == SEARCH_CAPTURE[0]
    return xml.decode()


def search_mutate(xml, suffix, key, value):
    root = ET.fromstring(xml)
    node(root, suffix).set(key, value)
    return ET.tostring(root, encoding='unicode')


class SearchSelector:
    def __init__(self, d, path): self.d, self.path = d, path
    def child(self, **selector): return SearchSelector(self.d, self.path + [selector])
    def nodes(self):
        tree = ET.fromstring(self.d.live)
        found = [n for n in tree.iter('node') if n.get('resource-id') == self.path[0]['resourceId']]
        for selector in self.path[1:]:
            found = [n for p in found for n in p if n.get('resource-id') == selector['resourceId']]
        return found
    @property
    def count(self):
        self.d.calls.append('count')
        return len(self.nodes())
    @property
    def info(self):
        self.d.calls.append('info')
        n = self.nodes()[0]; b = compat._bounds(n)
        return dict(resourceName=n.get('resource-id'), className=n.get('class'), text=n.get('text',''),
                    contentDescription=n.get('content-desc',''), bounds=dict(zip(('left','top','right','bottom'),b)),
                    **{k:n.get(k)=='true' for k in ('enabled','clickable','selected','focused')})
    def click(self):
        self.d.calls.append('click'); self.d.clicked.append(self.nodes()[0].get('resource-id'))


class SearchDevice:
    def __init__(self, snapshot=None, live=None):
        self.xml = snapshot or search_capture(); self.live = live or self.xml
        self.serial = 'RFGL62N646V'; self.calls = []; self.clicked = []
    def dump_hierarchy(self, **kwargs): self.calls.append('dump'); return self.xml
    def __call__(self, **selector): return SearchSelector(self, [selector])


class SearchEntryTests(unittest.TestCase):
    def test_exact_physical_button_not_editable(self):
        proof = compat.explore_search_entry_from_xml(search_capture(), package=PACKAGE)
        self.assertIsNotNone(proof)
        self.assertEqual(proof['action_bar_search_edit_text']['class'], 'android.widget.Button')
        self.assertFalse(any(n.get('class')=='android.widget.EditText' for n in ET.fromstring(search_capture()).iter()))

    def test_scoped_live_action_and_io_bound(self):
        d=SearchDevice();bind(d)
        self.assertTrue(nav._open_446_explore_search_entry(d, PACKAGE))
        self.assertEqual(d.clicked, [PACKAGE+':id/action_bar_search_edit_text'])
        self.assertEqual(d.calls, ['dump','count','count','info','info','click'])

    def test_372_noop_zero_io(self):
        d=SearchDevice();compat.retain_context(d,serial=d.serial,package=PACKAGE,version_name='372.0.0.48.60',version_code=377611059,unsafe=False)
        vars(d)['_instagram_search_observed_version']=(id(d),d.serial,PACKAGE,'372.0.0.48.60',377611059)
        self.assertTrue(nav._open_446_explore_search_entry(d, PACKAGE));self.assertEqual(d.calls,[])

    def test_revoked_wrong_context_zero_io(self):
        for kind in ('revoked','serial','package','object','version'):
            d=SearchDevice();bind(d);pkg=PACKAGE
            if kind=='revoked':vars(d)[compat.CONTEXT_KEY]=None
            if kind=='serial':d.serial='wrong'
            if kind=='package':pkg='foreign'
            if kind=='object':vars(d)[compat.CONTEXT_KEY]=vars(SearchDevice()).get(compat.CONTEXT_KEY)
            if kind=='version':vars(d)[compat.CONTEXT_KEY]=compat.ObservedContext(d.serial,PACKAGE,('447',1),id(d))
            self.assertFalse(nav._open_446_explore_search_entry(d,pkg));self.assertEqual(d.calls,[])

    def test_structural_negatives(self):
        cases=[('action_bar_search_edit_text',k,v) for k,v in [('class','android.widget.EditText'),('text','Not now'),('content-desc','other'),('enabled','false'),('clickable','false'),('focused','true'),('visible-to-user','false'),('package','foreign'),('bounds','[0,0][0,0]')]]
        cases += [('explore_action_bar_container',k,v) for k,v in [('resource-id',PACKAGE+':id/unrelated'),('visible-to-user','false'),('enabled','false'),('package','foreign')]]
        cases += [('search_tab','selected','false'),('search_tab','content-desc','other')]
        for suffix,k,v in cases:
            with self.subTest(suffix=suffix,key=k):
                xml=search_mutate(search_capture(),suffix,k,v)
                self.assertIsNone(compat.explore_search_entry_from_xml(xml,package=PACKAGE))
                d=SearchDevice(xml);bind(d);self.assertFalse(nav._open_446_explore_search_entry(d,PACKAGE));self.assertEqual(d.clicked,[])

    def test_live_replacement_and_stale_bounds(self):
        for suffix,k,v in [('action_bar_search_edit_text','text','Not now'),('action_bar_search_edit_text','bounds','[35,100][929,200]'),('action_bar_search_edit_text','enabled','false'),('explore_action_bar_container','resource-id',PACKAGE+':id/notifications'),('search_tab','selected','false')]:
            d=SearchDevice(live=search_mutate(search_capture(),suffix,k,v));bind(d)
            self.assertFalse(nav._open_446_explore_search_entry(d,PACKAGE));self.assertEqual(d.clicked,[])

    def test_duplicate_and_overlay(self):
        for mode in ('duplicate','live_duplicate','foreign','modal','cover'):
            root=ET.fromstring(search_capture())
            if 'duplicate' in mode:
                node(root,'action_bar_search_hints_text_layout').append(copy.deepcopy(node(root,'action_bar_search_edit_text')))
            else:
                ET.SubElement(root,'node',{'package':'foreign' if mode=='foreign' else PACKAGE,'text':'Introducing instants' if mode=='modal' else '', 'bounds':'[0,100][1080,250]','clickable':'true','enabled':'true','visible-to-user':'true'})
            xml=ET.tostring(root,encoding='unicode');d=SearchDevice(live=xml) if mode=='live_duplicate' else SearchDevice(xml);bind(d)
            self.assertFalse(nav._open_446_explore_search_entry(d,PACKAGE));self.assertEqual(d.clicked,[])

    def test_broken_or_unrelated_xml(self):
        for x in ('<broken','<hierarchy/>',fixture()):
            self.assertIsNone(compat.explore_search_entry_from_xml(x,package=PACKAGE))

    def test_open_search_keeps_editable_postcondition(self):
        # Execute the real open_search; only its external/pre-existing probes are scripted.
        from contextlib import ExitStack
        for ready in (False,True):
            d=SearchDevice();bind(d)
            field=object() if ready else None
            with ExitStack() as stack:
                for name,value in [('should_reuse_search_surface',False),('_open_search_skip_lightweight_follow_ct_cold_bootstrap',True),('_click_search_tab_in_open_search',(True,'exact')),('is_follow_ct_search_context_active',False),('_current_foreground_package',PACKAGE)]:
                    stack.enter_context(patch.object(nav,name,return_value=value))
                stack.enter_context(patch.object(nav.config,'INSTAGRAM_PACKAGE',PACKAGE))
                stack.enter_context(patch.object(nav.time,'sleep'))
                stack.enter_context(patch.object(nav,'retry_until',return_value=field))
                stack.enter_context(patch.object(nav,'_wait_search_edittext',return_value=field))
                stack.enter_context(patch.object(nav,'log'))
                strict = stack.enter_context(patch.object(nav,'instagram_search_surface_strict_ok',return_value=(True,'qualified_test_surface')))
                # Later metric/cache validation is retained; no invented field from Button.
                result=nav.open_search(d,allow_percent_fallback=False)
            self.assertEqual(d.clicked,[PACKAGE+':id/action_bar_search_edit_text'])
            self.assertEqual(result,ready)
            self.assertEqual(strict.call_count,1 if ready else 0)

class SearchContextWiringTests(unittest.TestCase):
    def wrapped(self, d, events=None):
        from run_latency_observability_v1 import instrument_device
        with patch.dict('os.environ', {'PHONEFARM_RUN_LATENCY_OBSERVABILITY_V1':'1'}):
            return instrument_device(d, emit=lambda *args, **kw: events.append(kw) if events is not None else None)

    def test_exact_previous_helper_skips_instrumented_context(self):
        import ast, subprocess
        from pathlib import Path
        root=Path(nav.__file__).parent
        source=subprocess.check_output(['git','-C',str(root),'show',
            '027642a12152ab33662bab12748e2b2a8e9b000d:instagram_navigation.py'],text=True)
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_open_446_explore_search_entry')
        namespace={};exec(compile('from __future__ import annotations\n'+ast.get_source_segment(source,node),'old_helper','exec'),namespace)
        d=SearchDevice();bind(d);proxy=self.wrapped(d)
        self.assertTrue(namespace[node.name](proxy,PACKAGE));self.assertEqual(d.calls,[])
        self.assertNotIn(compat.CONTEXT_KEY,vars(proxy));self.assertIn(compat.CONTEXT_KEY,vars(d))

    def test_real_proxy_one_header_then_real_a0_edittext_wait(self):
        class TransitionDevice(SearchDevice):
            def __call__(self, **selector):
                if selector=={'className':'android.widget.EditText'}:
                    outer=self
                    class Editable:
                        def wait(self,timeout):
                            outer.calls.append('editable_wait')
                            return outer.clicked==[PACKAGE+':id/action_bar_search_edit_text']
                    return Editable()
                return super().__call__(**selector)
        d=TransitionDevice();bind(d);events=[];proxy=self.wrapped(d,events)
        self.assertIsNone(nav._wait_search_edittext(proxy));d.calls.clear();events.clear()
        self.assertTrue(nav._open_446_explore_search_entry(proxy,PACKAGE))
        self.assertEqual(d.calls,['dump','count','count','info','info','click'])
        self.assertEqual(d.clicked,[PACKAGE+':id/action_bar_search_edit_text'])
        self.assertIsNotNone(nav._wait_search_edittext(proxy))
        self.assertEqual([e['operation'] for e in events],['dump_hierarchy','click','wait'])
        self.assertTrue(all(e['success'] and not e['extra_phone_call'] for e in events))

    def test_proved_372_raw_and_proxy_zero_io(self):
        for wrap in (False,True):
            d=SearchDevice();vars(d)['_instagram_search_observed_version']=(id(d),d.serial,PACKAGE,'372.0.0.48.60',377611059)
            self.assertTrue(nav._open_446_explore_search_entry(self.wrapped(d) if wrap else d,PACKAGE))
            self.assertEqual(d.calls,[])

    def test_invalid_or_missing_authority_never_reads_or_clicks(self):
        for case in ('missing_version','missing_context','revoked','wrong_version','wrong_code','bool_code','serial','package','raw_identity','context_identity','observed_identity','malformed','contradictory_372'):
            with self.subTest(case=case):
                d=SearchDevice();bind(d);pkg=PACKAGE;obs=list(vars(d)['_instagram_search_observed_version'])
                if case=='missing_version':del vars(d)['_instagram_search_observed_version']
                elif case=='missing_context':del vars(d)[compat.CONTEXT_KEY]
                elif case=='revoked':vars(d)[compat.CONTEXT_KEY]=None
                elif case=='wrong_version':obs[3]='447';vars(d)['_instagram_search_observed_version']=tuple(obs)
                elif case=='wrong_code':obs[4]+=1;vars(d)['_instagram_search_observed_version']=tuple(obs)
                elif case=='bool_code':obs[4]=True;vars(d)['_instagram_search_observed_version']=tuple(obs)
                elif case=='serial':d.serial='wrong'
                elif case=='package':pkg='foreign'
                elif case=='raw_identity':other=SearchDevice();vars(other).update({k:v for k,v in vars(d).items() if k.startswith('_instagram')});d=other
                elif case=='context_identity':vars(d)[compat.CONTEXT_KEY]=compat.ObservedContext(d.serial,PACKAGE,compat.VERSION,id(d)+1)
                elif case=='observed_identity':obs[0]+=1;vars(d)['_instagram_search_observed_version']=tuple(obs)
                elif case=='malformed':vars(d)['_instagram_search_observed_version']=obs
                elif case=='contradictory_372':vars(d)['_instagram_search_observed_version']=(id(d),d.serial,PACKAGE,'372.0.0.48.60',377611059)
                self.assertFalse(nav._open_446_explore_search_entry(self.wrapped(d),pkg));self.assertEqual(d.calls,[])

    def test_unknown_nested_subclass_and_missing_proxy_owner_rejected(self):
        from run_latency_observability_v1 import _TimedProxy
        d=SearchDevice();bind(d);p=self.wrapped(d)
        class Unknown:
            def __getattr__(self,name):return getattr(d,name)
        class Sub(_TimedProxy):pass
        cases=[Unknown(),Sub(d,kind='device',emit=None,methods=frozenset()),_TimedProxy(p,kind='device',emit=None,methods=frozenset()),_TimedProxy(None,kind='device',emit=None,methods=frozenset()),_TimedProxy(d,kind='uiautomator',emit=None,methods=frozenset())]
        for item in cases:
            with self.subTest(type=type(item).__name__):
                self.assertFalse(nav._open_446_explore_search_entry(item,PACKAGE));self.assertEqual(d.calls,[])

    def test_pre_action_owner_or_context_change_blocks_click(self):
        for case in ('owner','observed','context'):
            d=SearchDevice();bind(d);proxy=self.wrapped(d);original=SearchSelector.info.fget
            def info(selector):
                result=original(selector)
                if selector.path[0]['resourceId'].endswith('/explore_action_bar'):
                    if case=='owner':object.__setattr__(proxy,'_raw',SearchDevice())
                    elif case=='observed':vars(d)['_instagram_search_observed_version']=tuple(list(vars(d)['_instagram_search_observed_version']))
                    else:vars(d)[compat.CONTEXT_KEY]=None
                return result
            with patch.object(SearchSelector,'info',property(info)):
                self.assertFalse(nav._open_446_explore_search_entry(proxy,PACKAGE))
            self.assertEqual(d.clicked,[])

    def test_version_hook_records_existing_read_and_revokes_before_failed_read(self):
        for version in [('372.0.0.48.60',377611059),compat.VERSION]:
            d=SearchDevice();store=SimpleNamespace(path='/not-used',read=lambda:{'authorized_version_name':version[0],'authorized_version_code':version[1]})
            with patch.object(device_module.config,'INSTAGRAM_PACKAGE',PACKAGE),patch.object(device_module,'_fetch_package_version',return_value=version) as fetch,patch.object(device_module,'_package_disabled_for_user0',return_value=True),patch.object(device_module,'_package_suspended',return_value=False),patch.object(device_module,'_lock_state_store',return_value=store),patch.object(device_module,'log'):
                device_module.check_instagram_version_lock(d)
            fetch.assert_called_once_with(d.serial,PACKAGE);self.assertEqual(vars(d)['_instagram_search_observed_version'],(id(d),d.serial,PACKAGE,*version));self.assertEqual(d.calls,[])
            with patch.object(device_module,'_fetch_package_version',side_effect=RuntimeError('read_failed')):
                with self.assertRaises(RuntimeError):device_module.check_instagram_version_lock(d)
            self.assertIsNone(vars(d)['_instagram_search_observed_version'])
            self.assertFalse(nav._open_446_explore_search_entry(self.wrapped(d),PACKAGE));self.assertEqual(d.calls,[])

    def test_a0_selector_wait_and_recovery_ast_unchanged(self):
        import ast,subprocess
        from pathlib import Path
        root=Path(nav.__file__).parent;old=ast.parse(subprocess.check_output(['git','-C',str(root),'show','027642a12152ab33662bab12748e2b2a8e9b000d:instagram_navigation.py'],text=True));new=ast.parse(Path(nav.__file__).read_text())
        for name in ('open_search','_wait_search_edittext','_click_search_tab_in_open_search','ensure_global_search_surface'):
            a=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name==name);b=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name==name)
            self.assertEqual(ast.dump(a),ast.dump(b),name)

class SearchTabNullEmptyTests(unittest.TestCase):
    def run_live(self, changes, *, xml_text='', target='search_tab'):
        d=SearchDevice(search_mutate(search_capture(),'search_tab','text',xml_text));bind(d)
        original=SearchSelector.info.fget
        def info(selector):
            result=original(selector)
            if result['resourceName']==PACKAGE+':id/'+target:
                result.update(changes)
            return result
        with patch.object(SearchSelector,'info',property(info)):
            ok=nav._open_446_explore_search_entry(d,PACKAGE)
        return ok,d

    def test_physical_null_and_empty_text_equal_without_extra_io(self):
        for text in (None,''):
            with self.subTest(text=text):
                ok,d=self.run_live({'text':text})
                self.assertTrue(ok)
                self.assertEqual(d.calls,['dump','count','count','info','info','click'])
                self.assertEqual(d.clicked,[PACKAGE+':id/action_bar_search_edit_text'])

    def test_nonempty_whitespace_and_arbitrary_falsy_are_not_empty(self):
        for text in ('something',' ','\t',False,0,[],{}):
            with self.subTest(text=text):
                ok,d=self.run_live({'text':text})
                self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_null_does_not_equal_nonempty_xml(self):
        ok,d=self.run_live({'text':None},xml_text='Search')
        self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_eight_other_fields_remain_strict_with_null_text(self):
        for key,value in [('resourceName',PACKAGE+':id/wrong'),('className','android.widget.Button'),
                          ('contentDescription',None),('enabled',False),('clickable',False),
                          ('selected',False),('focused',True),
                          ('bounds',{'left':649,'top':2070,'right':864,'bottom':2205})]:
            with self.subTest(field=key):
                ok,d=self.run_live({'text':None,key:value})
                self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_header_null_text_is_still_rejected(self):
        ok,d=self.run_live({'text':None},target='action_bar_search_edit_text')
        self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_normalization_disabled_by_default_and_372_zero_io(self):
        import ast
        from pathlib import Path
        fn=next(n for n in ast.parse(Path(nav.__file__).read_text()).body
                if isinstance(n,ast.FunctionDef) and n.name=='_open_446_explore_search_entry')
        comparator=next(n for n in ast.walk(fn) if isinstance(n,ast.FunctionDef) and n.name=='matches')
        ns={};exec(compile(ast.Module(body=[comparator],type_ignores=[]),'<comparator>','exec'),ns)
        d=SearchDevice();n=compat.explore_search_entry_from_xml(d.xml,package=PACKAGE)['search_tab']
        info=d(resourceId=PACKAGE+':id/search_tab').info;info['text']=None
        self.assertFalse(ns['matches'](info,n))
        self.assertTrue(ns['matches'](info,n,search_tab_text=True))
        vars(d)['_instagram_search_observed_version']=(id(d),d.serial,PACKAGE,'372.0.0.48.60',377611059)
        d.calls.clear()
        self.assertTrue(nav._open_446_explore_search_entry(d,PACKAGE));self.assertEqual(d.calls,[])

class SearchHeaderRidEchoTests(unittest.TestCase):
    def run_header(self, changes, *, snapshot=None, live=None, count=None):
        d=SearchDevice(snapshot,live);bind(d)
        original=SearchSelector.info.fget
        def info(selector):
            value=original(selector)
            if value['resourceName']==PACKAGE+':id/action_bar_search_edit_text':
                value.update(changes)
                if changes.get('_omit_rid'):
                    value.pop('resourceName');value.pop('_omit_rid')
            return value
        with patch.object(SearchSelector,'info',property(info)):
            if count is None: ok=nav._open_446_explore_search_entry(d,PACKAGE)
            else:
                with patch.object(SearchSelector,'count',property(lambda s:count)):
                    ok=nav._open_446_explore_search_entry(d,PACKAGE)
        return ok,d

    def test_exact_none_and_absent_echo_same_io(self):
        for changes in ({'resourceName':None},{'_omit_rid':True},{}):
            with self.subTest(changes=changes):
                ok,d=self.run_header(changes);self.assertTrue(ok)
                self.assertEqual(d.calls,['dump','count','count','info','info','click'])
                self.assertEqual(d.clicked,[PACKAGE+':id/action_bar_search_edit_text'])

    def test_wrong_or_falsy_echo_is_not_unavailable(self):
        for value in (PACKAGE+':id/wrong','',False,0,[],{}):
            with self.subTest(value=value):
                ok,d=self.run_header({'resourceName':value});self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_all_other_fields_strict_with_missing_echo(self):
        for key,value in [('className','android.widget.EditText'),('text',None),('text','Wrong'),
                          ('contentDescription','Wrong'),('enabled',False),('clickable',False),
                          ('selected',True),('focused',True),('bounds',{'left':35,'top':100,'right':929,'bottom':200})]:
            with self.subTest(field=key,value=value):
                ok,d=self.run_header({'resourceName':None,key:value});self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_xml_and_live_rid_and_ancestry_must_match(self):
        for suffix in ('action_bar_search_edit_text','action_bar_search_hints_text_layout',
                       'explore_action_bar_container','explore_action_bar'):
            for side in ('snapshot','live'):
                with self.subTest(suffix=suffix,side=side):
                    xml=search_mutate(search_capture(),suffix,'resource-id',PACKAGE+':id/wrong')
                    ok,d=self.run_header({'resourceName':None},**{side:xml})
                    self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_unique_live_match_required(self):
        for count in (0,2):
            ok,d=self.run_header({'resourceName':None},count=count)
            self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_selector_cannot_redirect_header_to_other_rid(self):
        original=SearchSelector.child
        def wrong(selector,**query):
            if query.get('resourceId')==PACKAGE+':id/action_bar_search_edit_text':
                query={'resourceId':PACKAGE+':id/wrong'}
            return original(selector,**query)
        with patch.object(SearchSelector,'child',wrong):
            ok,d=self.run_header({'resourceName':None})
        self.assertFalse(ok);self.assertEqual(d.clicked,[])

    def test_tab_missing_rid_still_rejected(self):
        ok,d=SearchTabNullEmptyTests().run_live({'resourceName':None})
        self.assertFalse(ok);self.assertEqual(d.clicked,[])


class FollowersActivityTests(unittest.TestCase):
    MAIN = 'com.instagram.mainactivity.InstagramMainActivity'
    TAB = '.activity.MainTabActivity'
    XML = '<hierarchy><node resource-id="follow_list_container" bounds="[400,850][900,1050]"><node resource-id="follow_list_username" text="candidate_exact" bounds="[500,900][800,1000]"/></node></hierarchy>'

    def make_device(self, package=PACKAGE, activity=TAB, version=compat.VERSION):
        class D:
            serial = 'offline_activity_device'
            def __init__(self): self.calls = []; self.xml = FollowersActivityTests.XML
            def app_current(self):
                self.calls.append('app_current'); return dict(package=package, activity=activity)
            def dump_hierarchy(self, compressed=False):
                self.calls.append('dump_hierarchy'); return self.xml
        d = D()
        if version is not None:
            vars(d)['_instagram_search_observed_version'] = (id(d), d.serial, package, *version)
            compat.retain_context(d, serial=d.serial, package=package,
                                  version_name=version[0], version_code=version[1], unsafe=False)
        return d

    def prepare(self, d, package=PACKAGE, username='candidate_exact'):
        return nav._followers_prepare_exact_row_action_token(
            d, expected_username=username, source_profile_username='source_ct', pkg=package)

    def test_exact_activity_matrix_without_io(self):
        cases = [(PACKAGE, self.MAIN, compat.VERSION, True),
                 ('com.instagram.androif', self.MAIN, ('372.0.0.48.60',377611059), True),
                 (PACKAGE, self.TAB, compat.VERSION, True),
                 (PACKAGE, 'com.instagram.android.activity.MainTabActivity', compat.VERSION, True),
                 (PACKAGE, self.TAB, ('372.0.0.48.60',377611059), False),
                 ('com.instagram.androif', self.TAB, compat.VERSION, False),
                 ('foreign.package', self.TAB, compat.VERSION, False)]
        for activity in ('MainActivity','.MainActivity','UnqualifiedOverlayActivity',
                         'foreign.activity.MainTabActivity','',None,self.MAIN+'Overlay'):
            cases.append((PACKAGE,activity,compat.VERSION,False))
        for package, activity, version, expected in cases:
            with self.subTest(package=package,activity=activity,version=version):
                d=self.make_device(package,activity,version)
                self.assertIs(nav._followers_row_activity_allowed(d,package,activity),expected)
                self.assertEqual(d.calls,[])

    def test_context_mutations_reject_without_io(self):
        mutations = [lambda d: vars(d).pop(compat.CONTEXT_KEY),
                     lambda d: vars(d).__setitem__(compat.CONTEXT_KEY,None),
                     lambda d: vars(d).pop('_instagram_search_observed_version'),
                     lambda d: vars(d).__setitem__('_instagram_search_observed_version',(id(d),d.serial,PACKAGE,'446.0.0.49.77',True)),
                     lambda d: setattr(d,'serial','other'),
                     lambda d: vars(d).__setitem__(compat.CONTEXT_KEY,compat.ObservedContext(d.serial,PACKAGE,compat.VERSION,id(d)+1))]
        for mutation in mutations:
            d=self.make_device();mutation(d)
            self.assertFalse(nav._followers_row_activity_allowed(d,PACKAGE,self.TAB));self.assertEqual(d.calls,[])
        d=self.make_device(version=None)
        self.assertFalse(nav._followers_row_activity_allowed(d,PACKAGE,self.TAB))

    def test_existing_timing_proxy_context_is_preserved(self):
        from run_latency_observability_v1 import _TimedProxy
        d=self.make_device(); proxy=_TimedProxy(d,kind='device',emit=lambda *a,**k:None,methods=set())
        self.assertTrue(nav._followers_row_activity_allowed(proxy,PACKAGE,self.TAB))
        bad=_TimedProxy(d,kind='uiautomator',emit=lambda *a,**k:None,methods=set())
        self.assertFalse(nav._followers_row_activity_allowed(bad,PACKAGE,self.TAB))
        nested=_TimedProxy(proxy,kind='device',emit=lambda *a,**k:None,methods=set())
        self.assertFalse(nav._followers_row_activity_allowed(nested,PACKAGE,self.TAB));self.assertEqual(d.calls,[])

    def test_real_row_proof_passes_with_same_io_as_372(self):
        counts=[]
        for package, activity, version in [('com.instagram.androif',self.MAIN,('372.0.0.48.60',377611059)),
                                         (PACKAGE,self.MAIN,compat.VERSION),(PACKAGE,self.TAB,compat.VERSION),
                                         (PACKAGE,'com.instagram.android.activity.MainTabActivity',compat.VERSION)]:
            nav.followers_proof.reset_for_tests();d=self.make_device(package,activity,version)
            token,meta=self.prepare(d,package)
            self.assertIsNotNone(token,meta);self.assertEqual(token.row_username,'candidate_exact')
            counts.append(d.calls)
        self.assertTrue(all(c==counts[0] for c in counts));self.assertEqual(counts[0].count('app_current'),1)
        self.assertEqual(counts[0].count('dump_hierarchy'),1)

    def test_row_identity_missing_xml_and_wrong_package_still_reject(self):
        for kind in ('missing_xml','wrong_identity','wrong_package','empty_activity'):
            with self.subTest(kind=kind):
                nav.followers_proof.reset_for_tests();d=self.make_device(activity='' if kind=='empty_activity' else self.TAB)
                if kind=='missing_xml': d.xml=''
                token,meta=self.prepare(d, 'wrong.package' if kind=='wrong_package' else PACKAGE,
                                        'other_candidate' if kind=='wrong_identity' else 'candidate_exact')
                self.assertIsNone(token,meta)

    def test_new_activity_does_not_refresh_stale_token(self):
        nav.followers_proof.reset_for_tests();d=self.make_device()
        token,meta=self.prepare(d);self.assertIsNotNone(token,meta)
        self.assertTrue(nav._followers_row_activity_allowed(d,PACKAGE,self.TAB))
        with patch.object(nav.followers_proof.time,'monotonic',return_value=token.issued_at_monotonic+2):
            stale,reason=nav.followers_proof.issue_row_action_token(token.scope, 'candidate_exact', max_age_ms=1500.0)
        self.assertIsNone(stale)
        self.assertIn('stale', reason)


class CandidateProfileActivityTests(unittest.TestCase):
    def check(self, d, *, expected='bytanyusha_', observed='bytanyusha_', package=PACKAGE, **kw):
        return nav._expected_profile_identity_boundary(
            d, expected, package, observed_username=observed, **kw)

    def test_exact_physical_profile_and_372_matrix(self):
        f=FollowersActivityTests()
        for package,activity,version in [
            ('com.instagram.androif',f.MAIN,('372.0.0.48.60',377611059)),
            (PACKAGE,f.MAIN,compat.VERSION),(PACKAGE,f.TAB,compat.VERSION),
            (PACKAGE,'com.instagram.android.activity.MainTabActivity',compat.VERSION)]:
            with self.subTest(package=package,activity=activity):
                d=f.make_device(package,activity,version)
                ok,meta=self.check(d,package=package)
                self.assertTrue(ok,meta)
                self.assertEqual(meta['identity_method'],'exact_observed_username')
                self.assertEqual(d.calls,['app_current'])

    def test_unqualified_activities_versions_and_clones_reject(self):
        f=FollowersActivityTests()
        cases=[(PACKAGE,f.TAB,('372.0.0.48.60',377611059)),
               ('com.instagram.androif',f.TAB,compat.VERSION)]
        for activity in ('MainActivity','.MainActivity','foreign.ProfileActivity',
                         'foreign.activity.MainTabActivity','UnqualifiedOverlayActivity',
                         'com.instagram.mainactivity.InstagramMainActivityOverlay','',None):
            cases.append((PACKAGE,activity,compat.VERSION))
        for package,activity,version in cases:
            with self.subTest(package=package,activity=activity):
                d=f.make_device(package,activity,version)
                ok,meta=self.check(d,package=package)
                self.assertFalse(ok,meta)
                self.assertEqual(meta['reason'],'profile_identity_activity_unproven')
                self.assertEqual(d.calls,['app_current'])

    def test_wrong_package_and_wrong_candidate_reject(self):
        for kw,reason in [({'package':'wrong.package'},'profile_identity_package_mismatch'),
                          ({'observed':'other_candidate'},'profile_identity_username_mismatch'),
                          ({'expected':'other_candidate'},'profile_identity_username_mismatch'),
                          ({'expected':''},'profile_identity_expected_username_missing')]:
            d=FollowersActivityTests().make_device()
            ok,meta=self.check(d,**kw)
            self.assertFalse(ok);self.assertEqual(meta['reason'],reason)
            self.assertEqual(d.calls,[] if not kw.get('expected',True) else ['app_current'])

    def test_missing_identity_cannot_be_replaced_by_activity(self):
        for kw in ({}, {'allow_stage_scoped_transition':True},
                   {'allow_stage_scoped_transition':True,'stage_binding_id':'old'},
                   {'stage_scoped_transition_proven':True,'stage_binding_id':'old'}):
            d=FollowersActivityTests().make_device()
            with patch.object(nav,'_profile_signal_b_username_top_band',return_value=None) as probe:
                ok,meta=self.check(d,observed='',**kw)
            self.assertFalse(ok);self.assertEqual(meta['reason'],'profile_identity_username_unproven')
            probe.assert_called_once_with(d,'bytanyusha_')
            self.assertEqual(d.calls,['app_current'])

    def test_existing_positive_identity_proofs_unchanged(self):
        d=FollowersActivityTests().make_device()
        with patch.object(nav,'_profile_signal_b_username_top_band',return_value='exact_top_band'):
            ok,meta=self.check(d,observed='')
        self.assertTrue(ok);self.assertEqual(meta['identity_method'],'exact_top_band')
        with patch.object(nav,'_profile_signal_b_username_top_band',return_value=None):
            ok,meta=self.check(d,observed='',allow_stage_scoped_transition=True,
                               stage_scoped_transition_proven=True,stage_binding_id='fresh_caller_proof')
        self.assertTrue(ok);self.assertEqual(meta['identity_method'],'stage_scoped_visual_transition')

    def test_retained_context_mismatch_cannot_prove_profile(self):
        for mutate in (lambda d:vars(d).pop(compat.CONTEXT_KEY),
                       lambda d:setattr(d,'serial','wrong'),
                       lambda d:vars(d).pop('_instagram_search_observed_version')):
            d=FollowersActivityTests().make_device();mutate(d)
            ok,meta=self.check(d);self.assertFalse(ok,meta)
            self.assertEqual(d.calls,['app_current'])

    def test_timing_proxy_reuses_same_context_without_new_io(self):
        from run_latency_observability_v1 import _TimedProxy
        for kind,allowed in [('device',True),('uiautomator',False)]:
            d=FollowersActivityTests().make_device()
            proxy=_TimedProxy(d,kind=kind,emit=lambda *a,**kw:None,methods=set())
            ok,meta=self.check(proxy);self.assertIs(ok,allowed,meta)
            self.assertEqual(d.calls,['app_current'])

    def test_dangerous_activity_negative_guard_preserved(self):
        d=FollowersActivityTests().make_device(activity='com.instagram.DirectActivity')
        ok,meta=self.check(d);self.assertFalse(ok)
        self.assertEqual(meta['reason'],'profile_identity_dangerous_activity')

    def test_stale_row_proof_not_refreshed_by_profile_identity(self):
        f=FollowersActivityTests();nav.followers_proof.reset_for_tests();d=f.make_device()
        token,meta=f.prepare(d);self.assertIsNotNone(token,meta)
        ok,meta=self.check(d,expected='candidate_exact',observed='candidate_exact');self.assertTrue(ok,meta)
        with patch.object(nav.followers_proof.time,'monotonic',return_value=token.issued_at_monotonic+2):
            stale,reason=nav.followers_proof.issue_row_action_token(token.scope,'candidate_exact',max_age_ms=1500.0)
        self.assertIsNone(stale);self.assertIn('stale',reason)
