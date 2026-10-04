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
            def all(self):
                d.events.append('all')
                if d.failure=='all':raise RuntimeError('all')
                nodes=sel.all()
                if d.mode=='zero':return []
                if d.mode=='duplicate':nodes+=nodes
                class N:
                    def __init__(self,n):self.n=n
                    @property
                    def info(self):
                        d.events.append('info')
                        if d.failure=='info':raise RuntimeError('info')
                        v=self.n.info;v.update(d.edit)
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
        self.assertEqual(results[0],results[1]);self.assertEqual(calls[0],calls[1])
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
        for failure in ('dump','app_current','window_size','selector','all','info'):
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

if __name__=='__main__':unittest.main()
