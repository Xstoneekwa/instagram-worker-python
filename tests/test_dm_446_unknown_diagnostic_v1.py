"""Only the new same-read 446 diagnostic; no device/backend/action."""
import ast
import copy
import hashlib
import subprocess
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import instagram_navigation as nav
from tests.test_dm_446_readers import Clock, Device, fixture, TARGET, P

ROOT=Path(__file__).resolve().parents[1]
BASE='52cde3146d647fd2c500d045aec1277b509afa80'
BASE_SOURCE=subprocess.check_output(['git','show',BASE+':instagram_navigation.py'],cwd=ROOT,text=True)
TREE=ast.parse((ROOT/'instagram_navigation.py').read_text())
from tests.test_certification_admission import restore_ordinary_recorder_delta
PRE_RECORDER_TREE=ast.parse(restore_ordinary_recorder_delta('instagram_navigation.py', (ROOT/'instagram_navigation.py').read_text()))
OLD=ast.parse(BASE_SOURCE)
def function(tree,name):return copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name))
def previous_state():
    env=dict(nav.__dict__)
    exec(compile(ast.Module(body=[function(OLD,'_dm_446_thread_state')],type_ignores=[]),'signed-baseline','exec'),env)
    return env
def event(snap):
    # Execute the actual unchanged event location, no recreation of runtime flow.
    f=function(TREE,'open_dm_thread_from_profile')
    call=next(n for n in ast.walk(f) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='log' and len(n.args)>1 and isinstance(n.args[1],ast.Constant) and n.args[1].value=='dm_thread_state_detected')
    calls=[]
    env=dict(snap=snap,username=TARGET,thread_state=snap.get('thread_state','unknown'),dm_thread_detect_ms=2.5,message_click_attempts=1,log=lambda *a,**kw:calls.append((a,kw)))
    exec(compile(ast.Expression(call),'actual-event','eval'),env)
    return calls[0][1]

class SameReadDiagnosticTests(unittest.TestCase):
    def compare(self,xml):
        snapshots=[]
        for current in [False,True]:
            clock=Clock();d=Device(xml,clock=clock)
            env=previous_state()
            with patch.object(nav,'time',clock),patch('dm_446_observation_retention.begin',return_value=None):
                if current:state,s=nav._dm_446_thread_state(d,TARGET,P,2.5)
                else:
                    env['time']=clock
                    state,s=env['_dm_446_thread_state'](d,TARGET,P,2.5)
            snapshots.append((state,s,d.reads,d.currents,d.dimensions,clock.now))
        before,after=snapshots
        clean={k:v for k,v in after[1].items() if k not in ('diagnostic_446','observation_reason','observation_exception_class')}
        self.assertEqual(before[0],after[0]);self.assertEqual(before[1],clean)
        self.assertEqual(before[2:],after[2:])
        self.assertFalse(any(k in after[1] for k in ('xml','foreground_before','foreground_after','exception_message')))
        return after
    def test_unknown_and_positive_paths_keep_classification_reads_and_budgets(self):
        for n in ('B_42_compose_result_open','B_44_new_keyboard_back','B_37_new_thread_search','B_30_existing_thread'):
            with self.subTest(layout=n):
                state,s,*_=self.compare(fixture(n))
                e=event(s)
                self.assertEqual(e['classification_reason'],s['reason'])
                self.assertEqual(e['observation_reason'],'observed')
                self.assertEqual(e['number_of_reads'],s['number_of_reads'])
                self.assertEqual(e['composer_signal'],s['composer_signal'])
                self.assertIsNone(e['observation_exception_class'])
    def test_actual_observation_exception_keeps_refusal_and_safe_stage(self):
        state,s,*_=self.compare(RuntimeError('PRIVATE_MESSAGE_NOT_TO_EXPORT'))
        self.assertEqual(state,'unknown');self.assertEqual(s['reason'],'dm_observation_unproved')
        e=event(s)
        self.assertEqual(e['observation_reason'],'dump_failed')
        self.assertEqual(e['observation_exception_class'],'RuntimeError')
        self.assertNotIn('PRIVATE_MESSAGE',str(e))
    def test_event_rejects_arbitrary_reason_exception_text_and_raw_payload(self):
        e=event(dict(diagnostic_446=True,reason='SECRET_TOKEN',observation_reason='private XML',composer_signal='SECRET_COMPOSER',
                     observation_exception_class='SecretCredentialError',xml='<private/>',
                     number_of_reads=999999,positive_empty_observations=-1,composer_visible=True))
        self.assertEqual((e['classification_reason'],e['observation_reason'],e['observation_exception_class']),('unreported','unreported','OtherError'))
        self.assertEqual((e['number_of_reads'],e['positive_empty_observations']),(10000,0))
        self.assertNotIn('SECRET',str(e));self.assertNotIn('xml',e)
    def test_legacy_event_is_exactly_unchanged_and_has_no_device_access(self):
        self.assertEqual(event({}),dict(username=TARGET,thread_state='unknown',dm_thread_detect_ms=2.5,message_click_attempts=1))
    def test_truth_and_all_unrelated_navigation_functions_are_byte_identical(self):
        for n in OLD.body:
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name not in ('_dm_446_thread_state','open_dm_thread_from_profile'):
                self.assertEqual(ast.dump(n),ast.dump(function(PRE_RECORDER_TREE,n.name)),n.name)
        truth=ROOT/'dm_thread_truth.py'
        self.assertEqual(hashlib.sha256(truth.read_bytes()).hexdigest(),'fdcda43c896ea2d89e9abe3e984ff3980efb62d640186f32ad4424c67707b5b2')
    def test_exact_navigation_inverse_and_mutation_refusals(self):
        from tests.test_certification_admission import restore_exact_product_delta
        source=(ROOT/'instagram_navigation.py').read_text()
        restored=restore_exact_product_delta('instagram_navigation.py',source)
        self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(),'850d6a101bcc80f80f167b2a818d4f0ca99857482c442f02aed37af202d817e9')
        for bad in (source+'\n',source.replace('diagnostic_446=True','diagnostic_446=False',1)):
            with self.assertRaises(AssertionError):restore_exact_product_delta('instagram_navigation.py',bad)
    def test_only_observation_event_changed_in_open_thread(self):
        old=function(OLD,'open_dm_thread_from_profile');new=function(TREE,'open_dm_thread_from_profile')
        def normalize(f):
            for n in ast.walk(f):
                if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='log' and len(n.args)>1 and isinstance(n.args[1],ast.Constant) and n.args[1].value=='dm_thread_state_detected':
                    n.keywords=[k for k in n.keywords if k.arg is not None]
            return ast.dump(f)
        self.assertEqual(normalize(old),normalize(new))

if __name__=='__main__':unittest.main()

