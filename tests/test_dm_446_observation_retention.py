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



class OrdinaryRefusalRetentionTests(unittest.TestCase):
    """Only the five owner-requested ordinary-recorder behaviors, offline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root/'logs').mkdir(mode=0o700)
        self.context = dict(run_id='a0000000-0000-4000-8000-000000000001',
                            request_id='b0000000-0000-4000-8000-000000000001',
                            worker_sha='5e79a58170c1c69f2f1571a7b8f91272e35d801d',
                            account_id='c0000000-0000-4000-8000-000000000001',
                            device_id='d0000000-0000-4000-8000-000000000001',
                            app_instance_id='e0000000-0000-4000-8000-000000000001',
                            message_body='PRIVATE_MESSAGE', lease_id='PRIVATE_LEASE',
                            source_profile_username='PRIVATE_HANDLE')
        self.events = []
        for p in (patch.dict(os.environ, PHONEFARM_RUNTIME_ROOT=str(self.root)),
                  patch.object(admission, 'is_present', return_value=False),
                  patch.object(retention, '_ordinary_refusal_attempt', None),
                  patch('run_latency_observability_v1.current_context',
                        side_effect=lambda: dict(self.context)),
                  patch.object(fixtures.nav, 'log',
                               side_effect=lambda *a, **kw: self.events.append((a, kw)))):
            p.start()
            self.addCleanup(p.stop)

    def rejected_xml(self):
        return fixtures.fixture('B_37_new_thread_search').replace(
            'android.widget.EditText', 'android.widget.TextView')

    def read(self, xml, *, enabled=True):
        clock = fixtures.Clock()
        clock.now = 0.
        d = fixtures.Device(xml, clock=clock)
        with patch.object(fixtures.nav, 'time', clock):
            if enabled:
                result = fixtures.nav._dm_446_thread_state(d, fixtures.TARGET, fixtures.P, 2.5)
            else:
                with patch.object(retention, 'retain_first_ordinary_refusal',
                                  return_value={'status': 'NOT_APPLICABLE'}):
                    result = fixtures.nav._dm_446_thread_state(d, fixtures.TARGET, fixtures.P, 2.5)
        return result, (d.reads, d.currents, d.dimensions, clock.now)

    def receipts(self):
        return list(self.root.rglob('receipt.json'))

    def test_01_first_refusal_keeps_exact_already_read_xml_once(self):
        xml = self.rejected_xml()
        result, reads = self.read(xml)
        self.assertEqual(result[0], 'unknown')
        self.assertEqual(reads[:3], (1, 2, 1))
        receipt_path, = self.receipts()
        self.assertEqual((receipt_path.parent/'observation_01.xml').read_bytes(), xml.encode())
        # Same RUN, including another process-local invocation, cannot replace it.
        retention._ordinary_refusal_attempt = None
        self.read(xml.replace(fixtures.TARGET, 'different_private_recipient'))
        self.assertEqual(self.receipts(), [receipt_path])
        self.assertEqual((receipt_path.parent/'observation_01.xml').read_bytes(), xml.encode())

    def test_02_receipt_sha_context_private_modes_and_byte_bound(self):
        xml = self.rejected_xml()
        result, _ = self.read(xml)
        receipt_path, = self.receipts()
        r = json.loads(receipt_path.read_bytes())
        self.assertEqual(r['schema'], 'DM446_EXACT_OBSERVATION_V1')
        row, = r['observations']
        self.assertEqual(row['xml_sha256'], hashlib.sha256(xml.encode()).hexdigest())
        self.assertEqual(row['foreground_before'], row['foreground_after'])
        self.assertEqual(row['observation_reason'], 'composer_unproved')
        self.assertEqual(row['composer']['reason'], result[1]['composer_signal'])
        self.assertEqual(r['runtime']['reason'], result[1]['reason'])
        self.assertEqual(r['context']['run_id'], self.context['run_id'])
        self.assertEqual(r['context']['request_id'], self.context['request_id'])
        self.assertEqual(r['context']['worker_sha'], self.context['worker_sha'])
        self.assertEqual((r['context']['version_name'], r['context']['version_code']),
                         ('446.0.0.49.77', 385211304))
        self.assertEqual(r['context']['display_size'], [1080, 2340])
        self.assertTrue(row['observed_at'])
        from tests.test_dm_446_unknown_diagnostic_v1 import event
        self.assertEqual(event(result[1])['composer_signal'], result[1]['composer_signal'])
        self.assertNotIn('composer_signal', event({}))
        self.assertEqual(event(dict(diagnostic_446=True, composer_signal='PRIVATE_SIGNAL'))['composer_signal'], 'unreported')
        for value in ('PRIVATE_MESSAGE', 'PRIVATE_LEASE', 'PRIVATE_HANDLE'):
            self.assertNotIn(value, receipt_path.read_text())
            self.assertNotIn(value, str(self.events))
        for p in (self.root/'logs/dm446-exact-observations').rglob('*'):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),
                             0o700 if p.is_dir() else 0o600)
        self.context['run_id'] = 'a0000000-0000-4000-8000-000000000002'
        with patch.object(retention, '_MAX_COMPOSER_XML_BYTES', len(xml.encode())-1):
            self.read(xml)
        self.assertEqual(self.receipts(), [receipt_path])
        self.assertEqual(self.events[-1][1]['status'], 'UNAVAILABLE_SIZE_LIMIT')

    def test_03_accepted_composer_creates_no_evidence_or_writer_call(self):
        with patch.object(retention, '_root', side_effect=AssertionError('unnecessary IO')):
            result, _ = self.read(fixtures.fixture('B_37_new_thread_search'))
        self.assertEqual(result[0], 'empty_new_thread')
        self.assertEqual(self.receipts(), [])

    def test_04_storage_failure_preserves_original_fail_closed_result(self):
        xml = self.rejected_xml()
        before = self.read(xml, enabled=False)
        with patch.object(retention, '_write', side_effect=OSError('PRIVATE_DISK_DETAIL')) as write:
            after = self.read(xml)
            self.read(xml)
        self.assertEqual(before, after)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(after[0][0], 'unknown')
        self.assertIn('UNAVAILABLE_STORAGE', str(self.events))
        self.assertNotIn('PRIVATE_DISK_DETAIL', str(self.events))
        self.assertEqual(self.receipts(), [])

    def test_05_enabled_disabled_business_and_device_reads_are_identical(self):
        for i, xml in enumerate((self.rejected_xml(),
                                 fixtures.fixture('B_37_new_thread_search'),
                                 fixtures.fixture('B_30_existing_thread'),
                                 RuntimeError('PRIVATE_DEVICE_ERROR')), 10):
            with self.subTest(case=i):
                self.context['run_id'] = 'a0000000-0000-4000-8000-%012d' % i
                self.assertEqual(self.read(xml, enabled=False), self.read(xml))
        from tests.test_certification_admission import restore_ordinary_recorder_delta
        tree = ast.parse((ROOT/'instagram_navigation.py').read_text())
        old = ast.parse(subprocess.check_output(['git', 'show',
            '5e79a58170c1c69f2f1571a7b8f91272e35d801d:instagram_navigation.py'], cwd=ROOT, text=True))
        for name in ('_dm_446_context', '_dm_446_composer', 'detect_dm_thread_state',
                     '_dm_find_focus_composer', 'verify_dm_composer_safe',
                     'type_dm_draft_only', '_dm_outbound_expected_message_match_mode'):
            def body(t):
                return ast.dump(next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name==name))
            self.assertEqual(body(tree), body(old), name)
        for name in ('dm_thread_truth.py', 'dm_ui_446_evidence.py', 'dm_ui_446_surface.py', 'dm_sender_engine.py'):
            self.assertEqual((ROOT/name).read_bytes(), subprocess.check_output(['git', 'show',
                '5e79a58170c1c69f2f1571a7b8f91272e35d801d:'+name], cwd=ROOT))
        for name in ('instagram_navigation.py', 'dm_446_observation_retention.py'):
            source = (ROOT/name).read_text()
            base = subprocess.check_output(['git', 'show',
                '5e79a58170c1c69f2f1571a7b8f91272e35d801d:'+name], cwd=ROOT, text=True)
            self.assertEqual(restore_ordinary_recorder_delta(name, source), base)
            with self.assertRaises(AssertionError):
                restore_ordinary_recorder_delta(name, source+'\n')

if __name__ == '__main__': unittest.main()
