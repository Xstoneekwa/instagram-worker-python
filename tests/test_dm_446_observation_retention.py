from tests.test_certification_admission import restore_exact_product_delta
"""Exact retention through actual DM reader; offline transport fixtures only."""
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import dm_446_observation_retention as retention
import dm_certification_admission as admission
from certification_admission import CertificationStop
from tests import test_dm_446_readers as fixtures
from tests.test_dm_certification_admission import claims, binding


ROOT = Path(__file__).resolve().parents[1]
BASE = 'd52abe9f5c28c53a1c06d973f2ff64d257c6cb58'


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root/'logs').mkdir()
        self.c = claims()
        self.b = binding(self.c)
        self.session = dict(claims=self.c, binding=self.b, pid=os.getpid())
        self.clock = fixtures.Clock()
        for p in (patch.dict(os.environ, PHONEFARM_RUNTIME_ROOT=str(self.root)),
                  patch.object(admission, '_CONFIGURED', True),
                  patch.object(admission, '_SESSION', self.session),
                  patch.object(fixtures.nav, 'time', self.clock)):
            p.start(); self.addCleanup(p.stop)

    def xml(self, name='B_37_new_thread_search'):
        return fixtures.fixture(name).replace('vincentfavrephotographe', self.c['recipient_username'])

    def run_read(self, xmls, **kw):
        d = fixtures.Device(xmls, clock=self.clock)
        state, snap = fixtures.nav.detect_dm_thread_state(d, self.c['recipient_username'], pkg=fixtures.P, **kw)
        return state, snap, d

    def receipts(self):
        return [(p, json.loads(p.read_text())) for p in self.root.rglob('receipt.json')]

    def test_exact_existing_empty_unknown_and_same_call_reason(self):
        for name, expected in [('B_30b_thread_stable', 'existing_thread'),
                               ('B_37_new_thread_search', 'empty_new_thread'),
                               ('B_42_compose_result_open', 'unknown')]:
            with self.subTest(name=name):
                xml = self.xml(name)
                state, snap, d = self.run_read(xml)
                self.assertEqual(state, expected)
                p, receipt = self.receipts()[-1] if len(self.receipts()) == 1 else next(
                    (p, r) for p, r in self.receipts()
                    if r['observations'][0]['xml_sha256'] == hashlib.sha256(xml.encode()).hexdigest())
                self.assertEqual(len(receipt['observations']), d.reads)
                self.assertEqual(receipt['runtime']['reason'], snap['reason'])
                for row in receipt['observations']:
                    self.assertEqual((p.parent/row['xml_path']).read_bytes(), xml.encode())
                    self.assertEqual(row['xml_sha256'], hashlib.sha256(xml.encode()).hexdigest())
                    self.assertIsNotNone(row['classifier'])
                    self.assertEqual(row['foreground_before'], row['foreground_after'])
                self.assertEqual(receipt['context']['run_id'], self.b['run_id'])
                self.assertEqual(receipt['context']['request_id'], self.b['request_id'])
                self.assertEqual(receipt['context']['version_name'], '446.0.0.49.77')

    def test_dump_failure_null_safe_class(self):
        state, snap, d = self.run_read(RuntimeError('secret-token-do-not-persist'))
        p, r = self.receipts()[0]; row = r['observations'][0]
        self.assertEqual(state, 'unknown')
        self.assertEqual(row['observation_reason'], 'dump_failed')
        self.assertEqual(row['exception_class'], 'RuntimeError')
        self.assertIsNone(row['xml_path']); self.assertFalse(row['dump_returned'])
        self.assertNotIn('secret-token', p.read_text())

    def test_foreground_before_invalid_no_dump(self):
        d = fixtures.Device(self.xml()); d.foregrounds = [dict(package='foreign', activity='bad')]
        state, _ = fixtures.nav.detect_dm_thread_state(d, self.c['recipient_username'], pkg=fixtures.P)
        row = self.receipts()[0][1]['observations'][0]
        self.assertEqual(state, 'unknown'); self.assertEqual(d.reads, 0)
        self.assertEqual(row['observation_reason'], 'foreground_before_invalid')

    def test_foreground_after_rejected_xml_retained(self):
        d = fixtures.Device(self.xml())
        before = dict(package=fixtures.P, activity=d.activity)
        d.foregrounds = [before, dict(package='foreign', activity='other')]
        state, _ = fixtures.nav.detect_dm_thread_state(d, self.c['recipient_username'], pkg=fixtures.P)
        p, r = self.receipts()[0]; row = r['observations'][0]
        self.assertEqual(state, 'unknown'); self.assertIsNone(row['classifier'])
        self.assertEqual(row['observation_reason'], 'foreground_after_changed')
        self.assertEqual((p.parent/row['xml_path']).read_text(), self.xml())

    def test_context_changed_after_dump(self):
        d = fixtures.Device(self.xml())
        d.after_read = lambda dev: setattr(dev, '_instagram_search_observed_version', None)
        state, _ = fixtures.nav.detect_dm_thread_state(d, self.c['recipient_username'], pkg=fixtures.P)
        self.assertEqual(state, 'unknown')
        row = self.receipts()[0][1]['observations'][0]
        self.assertEqual(row['observation_reason'], 'version_context_invalid')
        self.assertIsNotNone(row['xml_path'])

    def test_malformed_and_recipient_mismatch_no_empty(self):
        for xml in ['<broken', self.xml().replace(self.c['recipient_username'], 'other_handle')]:
            state, _, _ = self.run_read(xml)
            self.assertEqual(state, 'unknown')
        for p, r in self.receipts():
            self.assertEqual(r['observations'][0]['classifier']['state'], 'unknown')
            self.assertIsNotNone(r['observations'][0]['xml_path'])

    def test_qualified_semantic_change_retained_without_byte_equality_gate(self):
        first=self.xml();second=first.replace('Vincent Favre','Vincent Other')
        state,snap,d=self.run_read([first,second])
        self.assertEqual(state,'empty_new_thread')
        self.assertEqual(snap['reason'],'a0_history_probe_completed')
        p,r=self.receipts()[0]
        self.assertEqual(len(r['observations']),d.reads)
        self.assertEqual([x['index'] for x in r['observations']],list(range(1,d.reads+1)))
        self.assertTrue(all(x['classifier']['state']=='empty_new_thread' for x in r['observations']))
        self.assertNotEqual(r['observations'][0]['xml_sha256'],r['observations'][1]['xml_sha256'])
        self.assertGreaterEqual(self.clock.now,0.8)

    def test_budget_reason_does_not_replace_classifier(self):
        d = fixtures.Device(self.xml(), clock=self.clock); d.cost = 3
        state, _ = fixtures.nav.detect_dm_thread_state(d, self.c['recipient_username'], pkg=fixtures.P)
        r = self.receipts()[0][1]
        self.assertEqual(state, 'unknown')
        self.assertEqual(r['runtime']['reason'], 'dm_composer_budget_exhausted')
        self.assertEqual(r['observations'][0]['classifier']['reason'], 'positive_empty_structure')

    def test_composer_rejection_retains_pure_truth_separately(self):
        xml = self.xml().replace('android.widget.EditText', 'android.widget.TextView')
        state, _, _ = self.run_read(xml)
        row = self.receipts()[0][1]['observations'][0]
        self.assertEqual(state, 'unknown'); self.assertIsNotNone(row['classifier'])
        self.assertEqual(row['observation_reason'], 'composer_unproved')

    def test_modes_and_secret_projection(self):
        self.run_read(self.xml())
        for p in (self.root/'logs/dm446-exact-observations').rglob('*'):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700 if p.is_dir() else 0o600)
        p, _ = self.receipts()[0]
        for forbidden in (self.b['lease_id'], self.c['message_body'], 'message_sha256', 'launcher_sha256'):
            self.assertNotIn(forbidden, p.read_text())

    def test_storage_failure_escapes_exception_recovery(self):
        with patch.object(retention, '_write', side_effect=OSError('secret')):
            with self.assertRaises(CertificationStop) as caught:
                self.run_read(self.xml())
        self.assertEqual(str(caught.exception), 'dm_exact_observation_persistence_failed')
        self.assertFalse(isinstance(caught.exception, Exception))

    def test_symlink_private_path_and_unsafe_mode_rejected(self):
        p = self.root/'logs/dm446-exact-observations'; target=self.root/'target'; target.mkdir()
        p.symlink_to(target, target_is_directory=True)
        with self.assertRaises(CertificationStop): self.run_read(self.xml())
        self.assertEqual(list(target.iterdir()), [])
        p.unlink(); p.mkdir(mode=0o755)
        with self.assertRaises(CertificationStop): self.run_read(self.xml())

    def test_exclusive_file_no_overwrite_or_symlink(self):
        fd=os.open(str(self.root), os.O_RDONLY|os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        retention._write(fd, 'sample', b'first')
        with self.assertRaises(FileExistsError): retention._write(fd, 'sample', b'second')
        (self.root/'link').symlink_to(self.root/'sample')
        with self.assertRaises(OSError): retention._write(fd, 'link', b'second')
        self.assertEqual((self.root/'sample').read_bytes(), b'first')

    def test_unsafe_writable_ancestor_rejected(self):
        (self.root/'logs').chmod(0o777)
        with self.assertRaises(CertificationStop): self.run_read(self.xml())
        self.assertEqual(self.receipts(), [])

    def test_foreign_owned_ancestor_rejected(self):
        actual = retention.os.fstat
        def foreign(fd):
            result = actual(fd)
            values = list(result); values[4] = os.getuid() + 10000
            return os.stat_result(values)
        with patch.object(retention.os, 'fstat', side_effect=foreign):
            with self.assertRaises(CertificationStop): self.run_read(self.xml())
        self.assertEqual(self.receipts(), [])

    def test_runtime_symlink_ancestor_rejected(self):
        alias = self.root/'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with patch.dict(os.environ, PHONEFARM_RUNTIME_ROOT=str(alias)):
            with self.assertRaises(CertificationStop): self.run_read(self.xml())
        self.assertEqual(self.receipts(), [])

    def test_invalid_present_never_falls_back(self):
        with patch.object(admission, '_SESSION', None):
            with self.assertRaises(CertificationStop): self.run_read(self.xml())

    def test_normal_production_no_storage(self):
        with patch.object(admission, 'is_present', return_value=False), patch.object(retention, '_root', side_effect=AssertionError):
            state, _, _ = self.run_read(self.xml())
        self.assertEqual(state, 'empty_new_thread'); self.assertEqual(self.receipts(), [])

    def test_baseline_truth_preserved_except_removed_byte_equality_gate(self):
        source=subprocess.check_output(['git','show',BASE+':instagram_navigation.py'],cwd=ROOT,text=True)
        tree=ast.parse(source);env=dict(vars(fixtures.nav))
        names={'_dm_446_observation','_dm_446_thread_state'}
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<baseline>','exec'),env)
        for xmls in [self.xml(),'<bad',self.xml('B_30b_thread_stable')]:
            with self.subTest(kind=str(xmls)[:20]):
                oldclock=fixtures.Clock();oldclock.now=0.;env['time']=oldclock;self.clock.now=0.
                a=fixtures.Device(xmls,clock=oldclock);b=fixtures.Device(xmls,clock=self.clock)
                old=env['_dm_446_thread_state'](a,self.c['recipient_username'],fixtures.P,None)
                new=fixtures.nav._dm_446_thread_state(b,self.c['recipient_username'],fixtures.P,None)
                self.assertEqual(old[0],new[0])
                if new[0]=='empty_new_thread':
                    self.assertEqual(new[1]['reason'],'a0_history_probe_completed')
                    self.assertGreaterEqual(self.clock.now,0.8)
                else:self.assertEqual((a.reads,a.currents,a.dimensions),(b.reads,b.currents,b.dimensions))

    def test_frozen_truth_and_unchanged_non_dm_functions(self):
        certified='ef22d47efa627b59553d95f862d71e13ca9d5213'
        truth_sha256='fdcda43c896ea2d89e9abe3e984ff3980efb62d640186f32ad4424c67707b5b2'
        certified_truth=subprocess.check_output(['git','show',certified+':dm_thread_truth.py'],cwd=ROOT)
        self.assertEqual(hashlib.sha256(certified_truth).hexdigest(),truth_sha256)
        self.assertEqual(hashlib.sha256((ROOT/'dm_thread_truth.py').read_bytes()).hexdigest(),truth_sha256)
        from test_dm_446_multisurface import MultisurfaceTests
        MultisurfaceTests().test_scope_ast_and_truth_predicates_unchanged()
        old=ast.parse(subprocess.check_output(['git','show',BASE+':instagram_navigation.py'],cwd=ROOT,text=True))
        new=ast.parse(restore_exact_product_delta('instagram_navigation.py',(ROOT/'instagram_navigation.py').read_text()))
        a={n.name:ast.dump(n) for n in old.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        b={n.name:ast.dump(n) for n in new.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        certified_nav=ast.parse(subprocess.check_output(['git','show',certified+':instagram_navigation.py'],cwd=ROOT,text=True))
        certified_functions={n.name:ast.dump(n) for n in certified_nav.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        for name in a.keys()-{'_dm_446_observation','_dm_446_thread_state','_dm_446_composer','_dm_outbound_expected_message_match_mode'}:
            expected=certified_functions[name] if name=='_dm_detect_history_signal' else a[name]
            self.assertEqual(expected,b[name],name)




class PersistentScopeFixture:
    """Existing ordinary context and transport fixtures; no permit or physical IO."""
    def setUp(self):
        import types
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root/'logs').mkdir(mode=0o700)
        self.scope = dict(retention._CERTIFICATION_SCOPE)
        self.run_id = 'a0000000-0000-4000-8000-000000000001'
        self.request_id = 'b0000000-0000-4000-8000-000000000001'
        self.context = dict(self.scope, run_id=self.run_id, request_id=self.request_id,
                            worker_sha='a'*40)
        self.slot_context = dict(self.scope, request_id=self.request_id,
            package_name=self.scope['package'], adb_serial=self.scope['serial'], managed_campaign=True)
        self.trace = []
        self.slot = types.SimpleNamespace(context=self.slot_context, stop_reason=None,
            remaining_ms=lambda: 100000,
            before_send=lambda job: self.trace.append(('before_send',job)))
        self.clock = fixtures.Clock()
        self.clock.now = 0.
        self.logs = []
        for p in (patch.dict(os.environ, PHONEFARM_RUNTIME_ROOT=str(self.root),
                              DM446_CERTIFICATION_RECORDER_ENABLED='true'),
                  patch.object(retention, '_composer_buffer', None),
                  patch.object(admission, 'is_present', return_value=False),
                  patch('run_latency_observability_v1.current_context', side_effect=lambda: dict(self.context)),
                  patch('outreach_slot_contract.active', side_effect=lambda: self.slot),
                  patch('logs.log', side_effect=lambda *a, **kw: self.logs.append((a,kw))),
                  patch.object(fixtures.nav, 'time', self.clock),
                  patch.object(fixtures.nav, 'log', lambda *a, **kw: None)):
            p.start(); self.addCleanup(p.stop)

    def device(self, xml=None, *, version=446):
        import instagram_ui_446_compat as compat
        d=fixtures.Device(xml or fixtures.fixture('B_37_new_thread_search'),version=version,clock=self.clock)
        d.serial=self.scope['serial']
        name,code=compat.VERSION if version==446 else ('372.0.0.48.60',377611059)
        d._instagram_search_observed_version=(id(d),d.serial,fixtures.P,name,code)
        compat.retain_context(d,serial=d.serial,package=fixtures.P,
                             version_name=name,version_code=code,unsafe=False)
        return d

    def session(self,d):
        return retention.certification_session(d,account_id=self.scope['account_id'],run_id=self.run_id)

    def receipts(self):
        return [(p,json.loads(p.read_bytes())) for p in self.root.rglob('receipt.json')]

    def reject_xml(self):
        return fixtures.fixture('B_37_new_thread_search').replace(
            'android.widget.EditText','android.widget.TextView')


class PersistentCertificationTests(PersistentScopeFixture, unittest.TestCase):
    def test_initial_success_and_first_failure_keep_same_native_xml_privately(self):
        xml=fixtures.fixture('B_37_new_thread_search')
        for i,body in enumerate((xml,self.reject_xml()),1):
            self.context['run_id']=self.run_id='a0000000-0000-4000-8000-%012d'%i
            d=self.device(body)
            with self.session(d):
                state,snap=fixtures.nav._dm_446_thread_state(d,fixtures.TARGET,fixtures.P,2.5)
            p,r=self.receipts()[-1] if i==1 else next((p,r) for p,r in self.receipts() if r['context']['run_id']==self.run_id)
            self.assertEqual(state,'empty_new_thread' if i==1 else 'unknown')
            rows=r['thread_records'][0]['observations']
            self.assertEqual(len(rows),d.reads)
            for row in rows:
                self.assertEqual((p.parent/row['xml_path']).read_bytes(),body.encode())
                self.assertEqual(row['xml_sha256'],hashlib.sha256(body.encode()).hexdigest())
                self.assertEqual(row['foreground_before'],row['foreground_after'])
            self.assertEqual(r['context']['worker_sha'],'a'*40)
            self.assertEqual(r['context']['request_id'],self.request_id)
            self.assertNotIn('permit_id',r['context'])
        for p in (self.root/'logs/dm446-exact-observations').rglob('*'):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o700 if p.is_dir() else 0o600)

    def test_same_switch_survives_distinct_runs_and_valid_worker_change(self):
        for i,worker in enumerate(('a'*40,'b'*40),1):
            self.context.update(run_id='a0000000-0000-4000-8000-%012d'%i,
                                request_id='b0000000-0000-4000-8000-%012d'%i,
                                worker_sha=worker)
            self.run_id=self.context['run_id'];self.slot_context['request_id']=self.context['request_id']
            with self.session(self.device()):
                self.assertIsNotNone(retention._ordinary_buffer())
                retention.capture_event('success',result=True)
            self.assertIsNone(retention._composer_buffer)
            self.assertEqual(os.environ[retention._CERTIFICATION_ENV],'true')
        self.assertEqual({r['context']['worker_sha'] for _,r in self.receipts()},{'a'*40,'b'*40})
        self.assertEqual(len(self.receipts()),2)

    def test_default_and_production_cutover_off_do_not_retain(self):
        for value in (None,'false'):
            with patch.dict(os.environ):
                if value is None:os.environ.pop(retention._CERTIFICATION_ENV,None)
                else:os.environ[retention._CERTIFICATION_ENV]=value
                with patch.object(retention,'_root',side_effect=AssertionError('no disk IO')):
                    with self.session(self.device()):
                        self.assertIsNone(retention._ordinary_buffer())
                        retention.capture_event('not_retained',actual_text='PRIVATE')
                        result=fixtures.nav._dm_446_thread_state(self.device(self.reject_xml()),fixtures.TARGET,fixtures.P,2.5)
                        self.assertEqual(result[0],'unknown')
        self.assertEqual(self.receipts(),[])

    def test_scope_excludes_other_accounts_devices_instances_versions_and_pipelines(self):
        cases=[('account_id','foreign'),('device_id','foreign'),('app_instance_id','foreign'),
               ('request_id','foreign'),('worker_sha','unproved')]
        for field,value in cases:
            with self.subTest(field=field):
                target=self.slot_context if field in ('device_id','app_instance_id') else self.context
                with patch.dict(target,{field:value}):
                    with self.session(self.device()):
                        self.assertIsNone(retention._ordinary_buffer())
        with self.session(self.device(version=372)):
            self.assertIsNone(retention._ordinary_buffer())
        with patch('outreach_slot_contract.active',return_value=None):
            with self.session(self.device()):
                self.assertIsNone(retention._ordinary_buffer())
        self.assertEqual(self.receipts(),[])

    def test_historical_canary_is_not_activated_or_reinterpreted(self):
        with patch.object(admission,'is_present',return_value=True), patch.object(
                retention,'activate_composer',side_effect=AssertionError('no canary activation')):
            with self.session(self.device()):
                self.assertIsNone(retention._ordinary_buffer())
        self.assertEqual(self.receipts(),[])

    def test_storage_error_preserves_original_return_and_exception(self):
        sentinel=ValueError('PRIMARY_NATIVE_REASON')
        for failed_step in ('_root','_write','_receipt_fields'):
            for error in (None,sentinel):
                with patch.object(retention,failed_step,side_effect=OSError('PRIVATE_DISK_DETAIL')):
                    try:
                        with self.session(self.device()):
                            retention.capture_event('native_result',failure=error is not None,
                                                    actual_text='PRIVATE_TEXT')
                            if error is not None:raise error
                            result=17
                    except ValueError as got:
                        self.assertIs(got,sentinel)
                    else:self.assertEqual(result,17)
                self.assertIsNone(retention._composer_buffer)
        self.assertEqual(self.receipts(),[])
        self.assertNotIn('PRIVATE_TEXT',str(self.logs))
        self.assertNotIn('PRIVATE_DISK_DETAIL',str(self.logs))
        self.assertTrue(any(k.get('status')=='UNPROVEN' for _,k in self.logs))

    def test_context_and_general_log_failures_preserve_business_result(self):
        sentinel=RuntimeError('ORIGINAL_FAILURE')
        for error in (None,sentinel):
            with patch.object(retention,'_scope_context',side_effect=ValueError('PRIVATE_CONTEXT')), \
                 patch('logs.log',side_effect=OSError('PRIVATE_LOG')):
                try:
                    with self.session(self.device()):
                        self.assertIsNone(retention._ordinary_buffer())
                        if error is not None:raise error
                        result=23
                except RuntimeError as got:self.assertIs(got,sentinel)
                else:self.assertEqual(result,23)
        self.assertEqual(self.receipts(),[])

    def test_volume_limit_keeps_existing_open_evidence_and_never_blocks(self):
        with self.session(self.device()):
            retention.capture_event('first',result=True)
        p,_=self.receipts()[0];before=p.read_bytes()
        self.context['run_id']=self.run_id='a0000000-0000-4000-8000-000000000002'
        with patch.object(retention,'_MAX_CERTIFICATION_DISK_BYTES',1):
            with self.session(self.device()):
                retention.capture_event('second',result=True)
                result='business_return'
        self.assertEqual(result,'business_return')
        self.assertEqual(len(self.receipts()),1)
        self.assertEqual(p.read_bytes(),before)
        self.assertTrue(any(k.get('status')=='UNPROVEN_VOLUME_LIMIT' for _,k in self.logs))

    def test_hashes_and_private_text_metadata_without_lease_or_message_leak(self):
        text='Private diagnostic 🙂\nSecond line'
        with self.session(self.device()):
            retention.bind_job('job','recipient')
            retention.capture_event('exact',actual_text=text,expected_text=text,
                lease_id='PRIVATE_LEASE',message_body='PRIVATE_BODY',result=True)
        p,r=self.receipts()[0];field=r['events'][-1]['fields']
        self.assertEqual(field['actual_text']['sha256'],hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(field['actual_text']['utf16_units'],len(text.encode('utf-16-le'))//2)
        for private in (text,'PRIVATE_LEASE','PRIVATE_BODY'):
            self.assertNotIn(private,p.read_text());self.assertNotIn(private,str(self.logs))

    def test_event_bound_keeps_first_failure_and_no_in_path_write(self):
        with patch.object(retention,'_MAX_CERTIFICATION_EVENTS',2):
            with self.session(self.device()):
                with patch.object(retention,'_write',side_effect=AssertionError('UI path disk IO')) as write:
                    retention.capture_event('positive1',result=True)
                    retention.capture_event('positive2',result=True)
                    retention.capture_event('fatal',failure=True,branch='FIELD_ABSENT')
                    retention.capture_event('fatal',failure=True,branch='MUST_NOT_REPLACE_FIRST')
                    write.assert_not_called()
        _,r=self.receipts()[0]
        fatal=[x for x in r['events'] if x['stage']=='fatal']
        self.assertEqual(len(fatal),1)
        self.assertEqual(fatal[0]['fields']['branch'],'FIELD_ABSENT')
        self.assertEqual(r['evidence_status'],'UNPROVEN')

    def test_existing_native_xml_keeps_first_and_last_without_new_dump(self):
        d=self.device()
        with self.session(d):
            for xml in ('<first/>','<middle/>','<last/>'):
                retention.capture_xml('already_read',xml)
        self.assertEqual(d.reads,0)
        p,r=self.receipts()[0]
        self.assertEqual([(p.parent/x['xml_path']).read_text() for x in r['native_observations']],
                         ['<first/>','<last/>'])

    def test_already_returned_backend_completion_is_selected_without_rpc(self):
        with self.session(self.device()):
            retention.bind_job('job','recipient')
            retention.capture_backend_completion('job',dict(id='job',status='sent',sent_at='native-time',
                                                           message_body='PRIVATE',lease_id='LEASE'),outcome='sent')
        _,r=self.receipts()[0]
        field=next(x['fields'] for x in r['events'] if x['stage']=='backend_completion')
        self.assertEqual(field['returned_row'],dict(id='job',status='sent',sent_at='native-time',attempts=None,max_attempts=None))
        self.assertNotIn('PRIVATE',str(r));self.assertNotIn('LEASE',str(r))


if __name__ == '__main__': unittest.main()
