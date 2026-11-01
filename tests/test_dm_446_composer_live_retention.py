"""Signed observability-only differential; no backend or phone."""
import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import instagram_navigation as nav
import dm_446_observation_retention as retention
from test_dm_446_readers import Device, Selector, P as PKG, fixture
BASELINE = 'def _dm_446_composer(d, package):\n    """Exact current selector, never a generic EditText fallback or a tap."""\n    try:\n        _xml, evidence = _dm_446_observation(d, package)\n        if not evidence["visible"]:\n            return False, evidence["reason"], None, None\n        rid = package + ":id/row_thread_composer_edittext"\n        selector = d(resourceId=rid)\n        nodes = selector.all()\n        if len(nodes) != 1:\n            return False, "dm_composer_not_unique", None, None\n        info = nodes[0].info or {}\n        if (info.get("resourceName") != rid\n                or info.get("className") != "android.widget.EditText"\n                or info.get("enabled") is not True\n                or info.get("clickable") is not True\n                or info.get("focusable") is not True\n                or info.get("bounds") != evidence["bounds"]):\n            return False, "dm_composer_live_mismatch", None, None\n        # Live resolution is not atomic Android compare-and-act. Re-read the\n        # context, including ancestry/occlusion, before returning any control.\n        _current_xml, current = _dm_446_observation(d, package)\n        if current != evidence:\n            return False, "dm_composer_context_changed", None, None\n        return True, "contextual_composer:" + evidence["context"], evidence["bounds"], nodes[0]\n    except Exception:\n        return False, "dm_composer_observation_unproved", None, None\n'
BASELINE_SHA256 = 'a2dfbe6f0d56cd6c6bf3c8f9da13395e21b47ded86abbfa61c3a024b11d819d9'
space=dict(nav.__dict__)
exec(compile(BASELINE,'<signed baseline composer>','exec'),space)
baseline=space['_dm_446_composer']
CAPS=json.loads((Path(__file__).parent/'fixtures/dm_446_multisurface.json').read_text())

class CountDevice(Device):
    def __init__(self, xml):
        super().__init__(xml);self.events=[];self.edit={};self.mode='';self.failure=None
    def dump_hierarchy(self,*args,**kwargs):
        self.events.append('dump')
        if self.failure=='dump':raise RuntimeError('dump')
        return super().dump_hierarchy(*args,**kwargs)
    def app_current(self,*args,**kwargs):
        self.events.append('app_current')
        if self.failure=='app_current':raise RuntimeError('foreground')
        return super().app_current(*args,**kwargs)
    def window_size(self,*args,**kwargs):
        self.events.append('window_size')
        if self.failure=='window_size':raise RuntimeError('window')
        return super().window_size(*args,**kwargs)
    def __call__(self,**kw):
        self.events.append(('selector',kw));d=self;sel=Selector(self,kw)
        if self.failure=='selector':raise RuntimeError('selector')
        class Selection:
            def _nodes(self):
                nodes=sel.all()
                if d.mode=='zero': return []
                if d.mode=='duplicate': return nodes+nodes
                return nodes
            def _info(self):
                nodes=self._nodes()
                if not nodes: return {}
                d.events.append('info')
                if d.failure=='info': raise RuntimeError('info')
                v=dict(nodes[0].info)
                v.update(d.edit)
                if d.mode=='missing': v.pop('resourceName',None)
                for key in d.missing_fields: v.pop(key,None)
                return v
            @property
            def count(self):
                d.events.append('count')
                if d.failure in ('count','all'): raise RuntimeError('count')
                return len(self._nodes())
            @property
            def info(self):
                return self._info()
            def all(self):
                d.events.append('all')
                if d.failure=='all': raise RuntimeError('all')
                nodes=self._nodes()
                class N:
                    def __init__(self,n):self.n=n
                    @property
                    def info(self):
                        d.events.append('info')
                        if d.failure=='info': raise RuntimeError('info')
                        v=dict(self.n.info);v.update(d.edit)
                        if d.mode=='missing':v.pop('resourceName',None)
                        for key in d.missing_fields:v.pop(key,None)
                        return v
                return [N(n) for n in nodes]
        return Selection()

def buffer():
    from threading import get_ident
    return dict(context=dict(package=PKG,run_id='run',execution_id='execution'),owner=get_ident(),
                records=[],xml_bytes=0,incomplete=False,invocations=0)

class ComposerRetentionTests(unittest.TestCase):
    def tearDown(self):retention._composer_buffer=None
    def compare(self,xml=None,edit=None,mode='',failure=None,active=True,missing_fields=()):
        results=[];calls=[];b=buffer()
        for fun in (baseline,nav._dm_446_composer):
            d=CountDevice(xml or CAPS['business']['xml']);d.edit=edit or {};d.mode=mode;d.failure=failure;d.missing_fields=set(missing_fields)
            retention._composer_buffer=b if active and fun is nav._dm_446_composer else None
            r=fun(d,PKG);results.append((*r[:3],r[3] is not None));calls.append(d.events)
        if failure != 'count':
            if results[0][0] and results[1][0]:
                self.assertEqual(results[0][2:],results[1][2:])
                self.assertTrue(results[0][1].startswith('contextual_composer:'))
                self.assertIn('resource_id_exact_composer_pkg', results[1][1])
                self.assertIn(results[0][1].split(':', 1)[1], results[1][1])
            else:
                self.assertEqual(results[0],results[1])
        if active and any(isinstance(x, tuple) and x[0] == 'selector' for x in calls[1]):
            if failure not in ('dump','app_current','window_size','selector'):
                self.assertIn('count', calls[1])
            if results[1][0]:
                self.assertIn('info', calls[1])
        return b,results[1]
    def test_baseline_seal(self):self.assertEqual(hashlib.sha256(BASELINE.encode()).hexdigest(),BASELINE_SHA256)
    def test_multisurface_and_keyboard_equivalence(self):
        for xml in [CAPS['business']['xml'],CAPS['community']['xml'],fixture('B_43_new_composer_focus'),fixture('B_44_new_keyboard_back')]:
            with self.subTest(surface=hashlib.sha256(xml.encode()).hexdigest()):
                b,r=self.compare(xml);self.assertTrue(r[0]);self.assertEqual(b['records'][0].receipt()['first_failed_predicate'],'NONE')
    def test_live_mismatches_exact_order(self):
        for key,val,reason in [('resourceName','', 'resource_name_mismatch'),('className','android.view.View','class_mismatch'),('enabled',False,'enabled_mismatch'),('clickable',False,'clickable_mismatch'),('focusable',None,'focusable_mismatch'),('bounds',{},'bounds_mismatch')]:
            with self.subTest(key=key):
                b,r=self.compare(edit={key:val});self.assertEqual(r[1],'dm_composer_live_mismatch');self.assertEqual(b['records'][0].receipt()['first_failed_predicate'],reason)
    def test_exact_selector_comparison_retained(self):
        b, _ = self.compare()
        receipt = b['records'][0].receipt()
        self.assertEqual(receipt['final_selector_comparison'], 'MATCH')
        self.assertIsNone(receipt['first_failed_field'])
        self.assertEqual([x['field'] for x in receipt['comparison_fields']],
                         ['resourceName', 'className', 'enabled', 'clickable', 'focusable', 'bounds'])
        self.assertTrue(all(x['equal'] for x in receipt['comparison_fields']))

    def test_each_selector_field_absence_is_retained(self):
        for field in ('resourceName', 'className', 'enabled', 'clickable', 'focusable', 'bounds'):
            with self.subTest(field=field):
                b, _ = self.compare(missing_fields=(field,))
                receipt = b['records'][0].receipt()
                self.assertEqual(receipt['final_selector_comparison'], 'FIELD_ABSENT')
                self.assertEqual(receipt['first_failed_field'], field)
                self.assertFalse(next(x for x in receipt['comparison_fields'] if x['field'] == field)['equal'])

    def test_selector_comparison_classifications(self):
        b, _ = self.compare(mode='zero')
        self.assertEqual(b['records'][0].receipt()['final_selector_comparison'], 'SELECTOR_ABSENT')
        b, _ = self.compare(mode='duplicate')
        self.assertEqual(b['records'][0].receipt()['final_selector_comparison'], 'SELECTOR_AMBIGUOUS')

    def test_missing_is_not_null(self):
        a,_=self.compare(mode='missing');b,_=self.compare(edit={'resourceName':None})
        self.assertNotIn('resourceName',a['records'][0].receipt()['live_present_fields'])
        self.assertIn('resourceName',b['records'][0].receipt()['live_present_fields'])
    def test_selector_count(self):
        for mode,count in [('zero',0),('duplicate',2)]:
            b,_=self.compare(mode=mode);r=b['records'][0].receipt();self.assertEqual(r['selector_count'],count);self.assertEqual(r['first_failed_predicate'],'selector_not_unique')
    def test_exceptions_keep_exact_io_and_results(self):
        for failure in ('dump','app_current','window_size','selector','count','info'):
            with self.subTest(failure=failure):
                b,r=self.compare(failure=failure);self.assertFalse(r[0]);self.assertEqual(r[1],'dm_composer_observation_unproved')
    def test_second_observation_rejected(self):
        b,r=self.compare([CAPS['business']['xml'],'<hierarchy/>']);self.assertEqual(b['records'][0].receipt()['first_failed_predicate'],'second_observation_unqualified')
    def test_inactive_no_io(self):
        with patch.object(retention,'_root',side_effect=AssertionError('disk')):
            b,r=self.compare(active=False);self.assertTrue(r[0]);self.assertFalse(b['records'])
    def test_safe_info_whitelist_and_no_uiobject(self):
        b,r=self.compare(edit={'text':'secret','contentDescription':'secret','token':'secret'})
        rec=b['records'][0];self.assertEqual(len(rec.result),3);self.assertNotIn('secret',json.dumps(rec.receipt()))
    def test_wrong_package_and_thread_not_recorded(self):
        b=buffer();retention._composer_buffer=b
        self.assertIsNone(retention.begin_composer('foreign'));self.assertTrue(b['incomplete'])
        b['owner']=-1;self.assertIsNone(retention.begin_composer(PKG))
    def test_invocation_and_memory_limits(self):
        b=buffer();retention._composer_buffer=b
        for _ in range(retention._MAX_COMPOSER_INVOCATIONS):self.assertIsNotNone(retention.begin_composer(PKG))
        self.assertIsNone(retention.begin_composer(PKG));self.assertTrue(b['incomplete'])
        b=buffer();r=retention.ComposerRecord(b);r.rows=[{'xml':'x'*100}]
        with patch.object(retention,'_MAX_COMPOSER_XML_BYTES',20):r.complete((False,'reason',None,None))
        self.assertTrue(b['incomplete']);self.assertFalse(b['records'])
    def test_deferred_private_exact_xml_and_clear(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as tmp:
            root=Path(tmp);(root/'logs').mkdir()
            with patch.dict(os.environ,{'PHONEFARM_RUNTIME_ROOT':tmp}),patch.object(retention,'_write',wraps=retention._write) as write:
                b,r=self.compare();write.assert_not_called();self.assertEqual(retention.flush_composer(),'RETAINED')
            receipts=list(root.rglob('receipt.json'));self.assertEqual(len(receipts),1)
            data=json.loads(receipts[0].read_text());self.assertEqual(data['context']['run_id'],'run')
            for row in data['records'][0]['observations']:
                p=receipts[0].parent/row['xml_path'];self.assertEqual(p.stat().st_mode&0o777,0o600);self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),row['xml_sha256']);self.assertEqual(p.read_bytes(),CAPS['business']['xml'].encode('utf-8'))
            self.assertIsNone(retention._composer_buffer);self.assertEqual(retention.flush_composer(),'INACTIVE')
    def test_writer_failure_clears_and_no_overwrite(self):
        self.compare()
        with patch.object(retention,'_root',side_effect=OSError()):self.assertEqual(retention.flush_composer(),'UNPROVEN')
        self.assertIsNone(retention._composer_buffer)
        with tempfile.TemporaryDirectory(dir='/private/tmp') as tmp:
            fd=os.open(tmp,os.O_RDONLY|os.O_DIRECTORY)
            try:
                retention._write(fd,'same',b'a')
                with self.assertRaises(FileExistsError):retention._write(fd,'same',b'b')
                os.symlink('same',str(Path(tmp)/'link'))
                with self.assertRaises(FileExistsError):retention._write(fd,'link',b'c')
                self.assertEqual((Path(tmp)/'same').read_bytes(),b'a')
            finally:os.close(fd)
    def test_activation_requires_valid_signed_context(self):
        from certification_admission import CertificationStop
        with patch.object(retention,'begin',return_value=None):
            with self.assertRaises(CertificationStop):retention.activate_composer(PKG,'sunetbassonmua')
        self.assertIsNone(retention._composer_buffer)

    def test_real_context_validation_and_run_binding(self):
        import dm_certification_admission as admission
        from tests.test_dm_certification_admission import claims, binding
        from certification_admission import CertificationStop
        c=claims();b=binding(c);session=dict(claims=c,binding=b,pid=os.getpid())
        with patch.object(admission,'_CONFIGURED',True),patch.object(admission,'_SESSION',session):
            retention.activate_composer(c['package'],c['recipient_username'])
            self.assertEqual(retention._composer_buffer['context']['run_id'],b['run_id'])
            self.assertEqual(retention._composer_buffer['context']['request_id'],b['request_id'])
            self.assertNotIn('lease_id',retention._composer_buffer['context'])
            with self.assertRaises(CertificationStop):retention.activate_composer(c['package'],c['recipient_username'])
            retention._composer_buffer=None
            with self.assertRaises(CertificationStop):retention.activate_composer(c['package'],'foreign')
            self.assertIsNone(retention._composer_buffer)
    def test_second_qualified_drift_equivalent(self):
        observation=nav._dm_446_observation
        def drift(d,p,**kw):
            xml,e=observation(d,p,**kw)
            if d.reads==2:e=dict(e,focused=not e['focused'])
            return xml,e
        with patch.object(nav,'_dm_446_observation',side_effect=drift),patch.dict(space,_dm_446_observation=drift):
            b,r=self.compare();self.assertEqual(r[1],'dm_composer_context_changed');self.assertEqual(b['records'][0].receipt()['first_failed_predicate'],'second_observation_drift')
    def test_unproven_overflow_receipt_and_new_run_isolation(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as tmp:
            (Path(tmp)/'logs').mkdir()
            with patch.dict(os.environ,PHONEFARM_RUNTIME_ROOT=tmp):
                b,_=self.compare();b['incomplete']=True
                self.assertEqual(retention.flush_composer(),'UNPROVEN')
                b2,_=self.compare();b2['context']['run_id']='other-run'
                self.assertEqual(retention.flush_composer(),'RETAINED')
            receipts=[json.loads(p.read_text()) for p in Path(tmp).rglob('receipt.json')]
            self.assertEqual({r['context']['run_id'] for r in receipts},{'run','other-run'})
            self.assertEqual({r['evidence_status'] for r in receipts},{'RETAINED','UNPROVEN'})
    def test_no_hash_write_during_helper(self):
        with patch.object(retention.hashlib,'sha256',side_effect=AssertionError('early hash')),patch.object(retention,'_root',side_effect=AssertionError('early IO')):
            b,r=self.compare();self.assertTrue(r[0]);self.assertEqual(len(b['records']),1)


# Persistent ordinary certification exercises the installed functions on the
# existing offline transport fixtures. Never call a real runner or device.
import subprocess
ROOT=Path(__file__).resolve().parents[1]
from tests.test_dm_446_observation_retention import PersistentScopeFixture
from tests import test_dm_446_readers as _persistent_fixtures
from tests.test_certification_admission import restore_persistent_certification_recorder_delta


class _TextDevice(_persistent_fixtures.Device):
    def __init__(self,xml,**kw):
        super().__init__(xml,**kw);self.calls=[];self.text_value='';self.get_error=None
    def __call__(self,**kw):
        self.calls.append(('selector',kw))
        original=_persistent_fixtures.Selector(self,kw);d=self
        class Field:
            @property
            def count(self):
                d.calls.append('count');return original.count
            @property
            def info(self):
                d.calls.append('info');return original.info
            def all(self):return original.all()
            def exists(self,**kwargs):return original.exists(**kwargs)
            def wait(self,**kwargs):return original.wait(**kwargs)
            def click(self):d.calls.append('focus')
            def set_text(self,text):
                import xml.etree.ElementTree as ET
                d.calls.append(('set_text',len(text)));d.text_value=text
                root=ET.fromstring(d.last)
                _persistent_fixtures.node(root,'row_thread_composer_edittext').set('text',text)
                d.last=ET.tostring(root,encoding='unicode');d.xmls=[d.last];d.live=None
            def get_text(self):
                d.calls.append('get_text')
                if d.get_error is not None:raise d.get_error
                return d.text_value
        return Field()


class PersistentPipelineTests(PersistentScopeFixture,unittest.TestCase):
    def text_device(self,xml=None):
        import instagram_ui_446_compat as compat
        d=_TextDevice(xml or _persistent_fixtures.fixture('B_37_new_thread_search'),clock=self.clock)
        d.serial=self.scope['serial']
        d._instagram_search_observed_version=(id(d),d.serial,PKG,*compat.VERSION)
        compat.retain_context(d,serial=d.serial,package=PKG,
                             version_name=compat.VERSION[0],version_code=compat.VERSION[1],unsafe=False)
        return d

    def test_actual_typing_read_verify_order_and_device_calls_equal_on_off(self):
        text='Hello diagnostic000 !\n\nTest Message \n\nRegards'
        runs=[]
        for on in ('false','true'):
            d=self.text_device();self.clock.now=0.
            with patch.dict(os.environ,DM446_CERTIFICATION_RECORDER_ENABLED=on):
                with self.session(d):
                    retention.bind_job('job','recipient')
                    with retention.phase('focus'):
                        editor=nav._dm_find_focus_composer(d);editor.click()
                    with retention.phase('typing'):
                        typed=nav.type_dm_draft_only(d,text,PKG,force_method='set_text',composer=None)
                    with retention.phase('posttyping'):
                        readback=nav.read_dm_composer_text(d)
                    verified=nav.verify_dm_draft_text(d,text)
                    runs.append((typed,readback,verified,list(d.calls),d.reads,d.currents,d.dimensions))
        self.assertEqual(runs[0],runs[1]);self.assertTrue(runs[1][2])
        p,r=self.receipts()[0]
        self.assertTrue(any('posttyping' in x['phase'] for x in r['records']))
        self.assertTrue(any('focus' in x['phase'] for x in r['records']))
        verify=next(x for x in r['events'] if x['stage']=='draft_verify')
        self.assertEqual(verify['fields']['branch'],'TEXT_EXACT')
        self.assertEqual(verify['fields']['actual_text']['characters'],45)
        for record in r['records']:
            for row in record['observations']:
                self.assertEqual(hashlib.sha256((p.parent/row['xml_path']).read_bytes()).hexdigest(),row['xml_sha256'])

    def test_verify_native_subbranches_are_distinct_and_business_equivalent(self):
        import types
        expected='Private expected 🙂\nSecond line'
        cases=[('FIELD_ABSENT',None,None,False,0),
               ('GET_TEXT_EXCEPTION','unused',RuntimeError('PRIVATE_EXCEPTION'),False,1),
               ('TEXT_EMPTY','',None,False,1),
               ('TEXT_DIFFERENT','private_other',None,False,1),
               ('TEXT_EXACT',expected,None,True,1)]
        # Exact pre-change function for the differential.
        source=subprocess.check_output(['git','show','0a44138f7e0a3447446e7b0c14561067fdebf041:instagram_navigation.py'],cwd=ROOT,text=True)
        tree=ast.parse(source);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='verify_dm_draft_text')
        for i,(branch,value,error,answer,calls) in enumerate(cases,1):
            self.context['run_id']=self.run_id='a0000000-0000-4000-8000-%012d'%i
            invocations=[]
            def get():
                invocations.append('get_text')
                if error:raise error
                return value
            field=None if value is None else types.SimpleNamespace(get_text=get)
            env=dict(nav.__dict__);env['_dm_find_focus_composer']=lambda _:field
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'<exact0a4verify>','exec'),env)
            old=env['verify_dm_draft_text'](None,expected);old_calls=list(invocations);invocations.clear()
            with self.session(self.device()),patch.object(nav,'_dm_find_focus_composer',return_value=field):
                got=nav.verify_dm_draft_text(None,expected)
            self.assertEqual((old,old_calls),(got,invocations));self.assertEqual(got,answer)
            self.assertEqual(len(invocations),calls)
            _,r=next((p,r) for p,r in self.receipts() if r['context']['run_id']==self.run_id)
            row=next(x for x in r['events'] if x['stage']=='draft_verify')
            self.assertEqual(row['fields']['branch'],branch)
            self.assertEqual(row['fields']['get_text_called'],bool(calls))
            if error:self.assertEqual(row['fields']['exception_class'],'RuntimeError')
            self.assertNotIn('PRIVATE_EXCEPTION',str(r))

    def test_read_finder_exception_getter_exception_and_empty_are_not_collapsed(self):
        import types
        cases=[('FIELD_ABSENT',None,None),
               ('FINDER_EXCEPTION',None,ValueError('private_finder')),
               ('GET_TEXT_EXCEPTION',RuntimeError('private_read'),None),
               ('TEXT_EMPTY','',None),('TEXT_READ','full text 🙂',None)]
        for i,(branch,value,finder_error) in enumerate(cases,30):
            self.context['run_id']=self.run_id='a0000000-0000-4000-8000-%012d'%i
            def get():
                if isinstance(value,Exception):raise value
                return value
            field=None if value is None else types.SimpleNamespace(get_text=get)
            with self.session(self.device()),patch.object(nav,'_dm_find_focus_composer',
                    side_effect=finder_error,return_value=field):
                got=nav.read_dm_composer_text(None)
            self.assertEqual(got,'full text 🙂' if branch=='TEXT_READ' else '')
            _,r=next((p,r) for p,r in self.receipts() if r['context']['run_id']==self.run_id)
            self.assertEqual(next(x for x in r['events'] if x['stage']=='draft_read')['fields']['branch'],branch)

    def test_exact_text_verification_still_rejects_partial_or_changed_unicode(self):
        import types
        expected='Full multiline 🙂\nSecond line 👋🏽'
        for i,value in enumerate((expected,expected[:-1],expected+'x',expected.replace('🙂','x')),50):
            self.context['run_id']=self.run_id='a0000000-0000-4000-8000-%012d'%i
            with self.session(self.device()),patch.object(nav,'_dm_find_focus_composer',
                    return_value=types.SimpleNamespace(get_text=lambda:value)):
                self.assertEqual(nav.verify_dm_draft_text(None,expected),value==expected)

    def test_transient_long_clickable_reference_is_recorded_not_admitted(self):
        import xml.etree.ElementTree as ET
        f=json.loads((ROOT/'tests/fixtures/dm_446_current_surface.json').read_bytes())['personal_mutual_sticker_filled']
        before=ET.fromstring(f['xml'])
        _persistent_fixtures.node(before,'row_thread_composer_send_button_container').set('long-clickable','true')
        transient=ET.tostring(before,encoding='unicode')
        # The external qualification binds the native pair; this tracked case
        # changes only its observed attribute on the existing sanitized fixture.
        d=self.device([transient,f['xml'],f['xml']])
        with self.session(d):
            failed=nav._dm_446_composer(d,PKG)
            accepted=nav._dm_446_composer(d,PKG)
        self.assertFalse(failed[0]);self.assertTrue(accepted[0])
        p,r=self.receipts()[0]
        records=r['records'];self.assertEqual({x['composer_result'] for x in records},{False,True})
        first=next(x for x in records if not x['composer_result'])
        row=first['observations'][0]
        self.assertEqual((p.parent/row['xml_path']).read_text(),transient)

    def test_sender_order_outcome_and_send_count_equal_exact_parent(self):
        import dm_sender_engine as engine
        parent=subprocess.check_output(['git','show','0a44138f7e0a3447446e7b0c14561067fdebf041:dm_sender_engine.py'],cwd=ROOT,text=True)
        name='_perform_real_welcome_dm_send'
        oldnode=next(x for x in ast.parse(parent).body if isinstance(x,ast.FunctionDef) and x.name==name)
        newnode=next(x for x in ast.parse((ROOT/'dm_sender_engine.py').read_text()).body if isinstance(x,ast.FunctionDef) and x.name==name)
        for verified in (False,True):
            answers=[]
            for index,node in enumerate((oldnode,newnode)):
                calls=[];reads=iter(('', 'Complete draft'))
                def trace(label,result):
                    def call(*a,**kw):calls.append(label);return result
                    return call
                env=dict(engine.__dict__)
                env.update(log=lambda *a,**kw:None,
                    dm_thread_shows_outgoing_message=trace('duplicate',False),
                    _resolve_dm_text_composer=trace('focus',(object(),None)),
                    verify_dm_composer_safe=trace('qualify',(True,'ok')),
                    read_dm_composer_text=lambda *a:(calls.append('read') or next(reads)),
                    type_dm_draft_only=trace('type',(True,{'method':'set_text'})),
                    verify_dm_draft_text=trace('verify',verified),
                    send_dm_safe=trace('send',{'sent':True,'send_tapped':True}))
                import __future__
                exec(compile(ast.Module(body=[node],type_ignores=[]),'<parent-or-candidate-sender>',
                             'exec',flags=__future__.annotations.compiler_flag),env)
                with self.session(self.device()):
                    result=env[name](None,username='recipient',message_body='Complete draft',
                        thread_state='empty_new_thread',pkg=PKG,dm_type='outreach',post_send_nav='sender_owned')
                answers.append((result,calls))
            self.assertEqual(answers[0],answers[1])
            self.assertEqual(answers[1][1].count('send'),1 if verified else 0)
            self.assertEqual(answers[1][1],['duplicate','focus','qualify','read','type','read','verify']+(['send'] if verified else []))

    def test_existing_send_tap_and_pre_send_boundary_order_no_extra_calls(self):
        import types
        calls=[]
        button=types.SimpleNamespace(click=lambda:calls.append('click'))
        original_boundary=self.slot.before_send
        self.slot.before_send=lambda job:calls.append('boundary')
        signature=dict(message_marker_count=0,expected_text_present=False)
        with self.session(self.device()),patch.object(nav.config,'ENABLE_REAL_DM_SEND',True),patch.object(
                nav,'_dm_find_focus_composer',return_value=object()),patch.object(
                nav,'read_dm_composer_text',return_value='exact'),patch.object(
                nav,'wait_for_dm_send_button_after_draft',return_value=(button,'ok',{'send_button_candidate_count':1})),patch.object(
                nav,'_dm_screen_size_for_dm',return_value=(1080,2340)),patch.object(
                nav,'_dm_composer_bounds_u2',return_value={}),patch.object(
                nav,'_dm_thread_message_signature',return_value=signature),patch.object(
                nav,'_dm_capture_send_debug_artifact',return_value={}),patch.object(
                nav,'guard_instagram_action_rate_limit',side_effect=lambda *a,**kw:calls.append('guard')),patch.object(
                nav,'_dm_verify_outbound_message_after_send',return_value=(True,'A0_OUTBOUND_PROOF',signature)):
            result=nav.send_dm_safe(None,'recipient','exact','empty_new_thread',job_id='job')
        self.slot.before_send=original_boundary
        self.assertTrue(result['sent'])
        self.assertEqual(calls,['guard','boundary','click','guard'])
        _,r=self.receipts()[0];stages={x['stage'] for x in r['events']}
        self.assertTrue({'send_button_qualification','before_send_boundary','send_tap_started',
                         'send_tap_returned','outbound_verification'}<=stages)

    def test_existing_child_environment_keeps_switch_without_per_run_activation(self):
        from device import runner_subprocess_env
        with patch('device.resolve_adb_path',return_value='/existing/adb'):
            env=runner_subprocess_env({'PATH':'/existing','DM446_CERTIFICATION_RECORDER_ENABLED':'true'})
        self.assertEqual(env['DM446_CERTIFICATION_RECORDER_ENABLED'],'true')
        self.assertEqual(env['ADB_PATH'],'/existing/adb')

    def test_wrong_or_ambiguous_live_selector_keeps_original_refusal_and_receipt(self):
        for i,mode in enumerate(('zero','duplicate','missing'),80):
            self.context['run_id']=self.run_id='a0000000-0000-4000-8000-%012d'%i
            d=CountDevice(CAPS['business']['xml']);d.mode=mode;d.missing_fields=set()
            # Existing admitted version fixture. Scope is tested separately, not forged into a canary.
            with patch.object(retention,'_scope_context',return_value=dict(
                    self.scope,run_id=self.run_id,request_id=self.request_id,
                    execution_id=self.request_id,worker_sha='a'*40)):
                with self.session(d):result=nav._dm_446_composer(d,PKG)
            self.assertFalse(result[0])
            _,r=next((p,r) for p,r in self.receipts() if r['context']['run_id']==self.run_id)
            self.assertFalse(r['records'][0]['composer_result'])
            self.assertTrue(r['records'][0]['first_failed_predicate'])


if __name__=='__main__':unittest.main()
