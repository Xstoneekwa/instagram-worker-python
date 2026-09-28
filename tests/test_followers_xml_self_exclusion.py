"""Offline execution of the actual picker and exact identity propagation."""
import ast
import __future__
import copy
from pathlib import Path
import subprocess
import sys
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
R=ast.parse((ROOT/'runner.py').read_text())
O=ast.parse((ROOT/'account_session_orchestrator.py').read_text())
BASE='c948ce002554338f81f8496b2a6477f3076e5e00'
def fn(tree,name):return next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
def execute(nodes,env):exec(compile(ast.fix_missing_locations(ast.Module(body=copy.deepcopy(nodes),type_ignores=[])),'<actual-source>','exec',flags=__future__.annotations.compiler_flag),env)
def row(name,cta='unknown'):return dict(username=name,row_cta_xml_class=cta,row_cta_xml_text='' if cta=='unknown' else cta,already_seen_runtime=False)
class Tracker:
 def __init__(self):self.skips=[];self.picks=0
 def begin_visible_window(self,rows):pass
 def note_visible_skip(self,r):self.skips.append(r)
 def note_actionable_pick(self):self.picks+=1

def pick(rows,identity='nab_youss',checkpoint=False):
 t=Tracker();logs=[];seen=[]
 env=dict(visual_loop_state={},_expl_v1=t,_followers_snapshot_viewport_exhausted=lambda rows:False,
 _ct_checkpoint_enabled=lambda:checkpoint,_ct_checkpoint_should_fast_skip_visible_candidate=lambda ck,c,**kw:(seen.append(c['username']) or False,''),
 ct_checkpoint={},_RUNTIME_FOLLOWED_USERNAMES=set(),_RUNTIME_SEEN_FOLLOWER_USERNAMES=set(),_RUNTIME_SKIPPED_USERNAMES=set(),
 _ac_blocked_visual_ids=lambda:set(),source_profile_username='pierrearnaud.destremau',src_key='pierrearnaud.destremau',
 log=lambda *a,**kw:logs.append((a,kw)),_eng_log=lambda *a,**kw:None)
 execute([fn(R,'_norm_ig_handle'),fn(R,'_first_eligible_follower_pick')],env)
 env['_verified_current_account_key']=env['_norm_ig_handle'](identity or '')
 return env['_first_eligible_follower_pick'](rows),t,logs,seen


# Exact signed certification-only delta. Navigation and business bytes remain
# under the original historical equality assertion below.
CERTIFICATION_TWO_SOURCE_SHA256 = '7f483adc3c7bc740e908df7bad388c4999fc1eddbd474950c4621fa8a45a66f6'
CERTIFICATION_TWO_BASE_SHA256 = '1b6dbb69c0dc3e23bd21e71e3e80033a6cf140738da7c7b953f641152a08fea4'
CERTIFICATION_TWO_EXACT_HUNKS = [("import os\nimport re\nimport tempfile\nimport time\nimport math\nfrom threading import RLock\n\nIDENTITY_UNPROVED = 'certification_candidate_identity_unproved_pre_transport'\n", "import os\nimport re\nimport tempfile\nfrom threading import RLock\n\nIDENTITY_UNPROVED = 'certification_candidate_identity_unproved_pre_transport'\n"), ('    """\n    def __init__(self, directory, binding):\n        self.binding = json.loads(_bytes(binding))\n        self.limit = binding.get(\'max_candidate_admissions\', 1)\n        if type(self.limit) is not int or self.limit not in (1, 2):\n            raise CertificationStop(\'PERMIT_INVALID\')\n        self.pid = os.getpid()\n        self.fd = None\n        self.mutex = RLock()\n', '    """\n    def __init__(self, directory, binding):\n        self.binding = json.loads(_bytes(binding))\n        self.pid = os.getpid()\n        self.fd = None\n        self.mutex = RLock()\n'), ("                os.fsync(fd)\n                self._sync_parent()\n                self._write({'binding': self.binding, 'state': 'AVAILABLE', 'candidate_key': None})\n            if not created and self.limit == 2:\n                raise CertificationStop('PERMIT_RESTART_FORBIDDEN')\n            self._read()\n        except (OSError, ValueError, TypeError, CertificationStop) as exc:\n            self.close()\n", "                os.fsync(fd)\n                self._sync_parent()\n                self._write({'binding': self.binding, 'state': 'AVAILABLE', 'candidate_key': None})\n            self._read()\n        except (OSError, ValueError, TypeError, CertificationStop) as exc:\n            self.close()\n"), ("                raise CertificationStop('PERMIT_INVALID')\n            if value['state'] == 'CONSUMED_BOUND' and not isinstance(value.get('candidate_key'), list):\n                raise CertificationStop('PERMIT_INVALID')\n            if self.limit == 2 and value.get('candidate_key') is not None:\n                slots = value.get('slots')\n                if not isinstance(slots, list) or not 1 <= len(slots) <= 2 or value['candidate_key'] != slots[-1]['key']:\n                    raise CertificationStop('PERMIT_INVALID')\n                if len({slot['key'][3] for slot in slots}) != len(slots):\n                    raise CertificationStop('PERMIT_INVALID')\n            return value\n        except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:\n            raise CertificationStop('PERMIT_INVALID') from exc\n\n    def admit(self, key):\n", "                raise CertificationStop('PERMIT_INVALID')\n            if value['state'] == 'CONSUMED_BOUND' and not isinstance(value.get('candidate_key'), list):\n                raise CertificationStop('PERMIT_INVALID')\n            return value\n        except (OSError, ValueError, TypeError, AttributeError) as exc:\n            raise CertificationStop('PERMIT_INVALID') from exc\n\n    def admit(self, key):\n"), ("            raise CertificationStop('PERMIT_ALREADY_TERMINAL')\n        if value['state'] == 'AVAILABLE':\n            value.update(state='CONSUMED_BOUND', candidate_key=list(key))\n            if self.limit == 2:\n                value['slots'] = [{'key': list(key), 'admitted_at_monotonic': time.monotonic()}]\n            self._write(value)\n            return 'FIRST_ADMISSION_GRANTED'\n        if value['candidate_key'] == list(key):\n            return 'SAME_CANDIDATE_REENTRY_GRANTED'\n        if self.limit == 2:\n            slots = value['slots']\n            if any(slot['key'][3] == key[3] for slot in slots):\n                self.terminal('PRIOR_CANDIDATE_REENTRY_BLOCKED')\n                raise CertificationStop('PRIOR_CANDIDATE_REENTRY_BLOCKED')\n            if len(slots) == 2:\n                self.terminal('THIRD_CANDIDATE_BLOCKED')\n                raise CertificationStop('THIRD_CANDIDATE_BLOCKED')\n            try:\n                cycles = read_certification_cycles(self.binding, slots)\n            except Exception as exc:\n                self.terminal('PREVIOUS_CYCLE_UNPROVED')\n                raise CertificationStop('PREVIOUS_CYCLE_UNPROVED') from exc\n            slots[0]['completed_action_id'] = cycles[0].scope.action_id\n            slots[0]['completed_receipt_hashes'] = {k:v.payload_hash for k,v in cycles[0].receipts.items()}\n            slots.append({'key':list(key), 'admitted_at_monotonic':time.monotonic()})\n            value.update(candidate_key=list(key), slots=slots)\n            self._write(value)\n            return 'SECOND_ADMISSION_GRANTED'\n        self.terminal('SECOND_CANDIDATE_BLOCKED')\n        raise CertificationStop('SECOND_CANDIDATE_BLOCKED')\n\n", "            raise CertificationStop('PERMIT_ALREADY_TERMINAL')\n        if value['state'] == 'AVAILABLE':\n            value.update(state='CONSUMED_BOUND', candidate_key=list(key))\n            self._write(value)\n            return 'FIRST_ADMISSION_GRANTED'\n        if value['candidate_key'] == list(key):\n            return 'SAME_CANDIDATE_REENTRY_GRANTED'\n        self.terminal('SECOND_CANDIDATE_BLOCKED')\n        raise CertificationStop('SECOND_CANDIDATE_BLOCKED')\n\n"), ("        required = {'permit_id', 'nonce', 'candidate_sha', 'account_id', 'serial', 'package',\n                    'device_id', 'version_name', 'certification_not_before', 'certification_expires_at',\n                    'max_candidate_admissions'}\n        if set(claims) != required or type(claims['max_candidate_admissions']) is not int or claims['max_candidate_admissions'] not in (1, 2):\n            raise CertificationStop('PERMIT_INVALID')\n        if any(type(claims[k]) is not str or not claims[k] or claims[k].strip() != claims[k]\n               for k in required - {'max_candidate_admissions'}):\n            raise CertificationStop('PERMIT_INVALID')\n        if claims['max_candidate_admissions'] == 2:\n            exact = {'account_id':'76c1dff8-d16a-40cf-83df-8138d2ca5bd4',\n                     'device_id':'ddec36ad-19d8-4f00-9f7d-a8c2d29fe540', 'serial':'RFGL62N646V',\n                     'package':'com.instagram.android', 'version_name':'446.0.0.49.77'}\n            if any(claims.get(k) != v for k,v in exact.items()):\n                raise CertificationStop('PERMIT_INVALID')\n            sql = [p for p in manifest.get('physical_canary_permits', []) if p.get('permit_id') == permit_id]\n            if len(sql) != 1 or type(sql[0].get('max_candidate_admissions')) is not int or sql[0]['max_candidate_admissions'] != 2:\n                raise CertificationStop('PERMIT_INVALID')\n            mapped = {'account':'account_id', 'device_id':'device_id', 'serial':'serial',\n                      'package':'package', 'version_name':'version_name', 'nonce':'nonce', 'candidate_sha':'candidate_sha'}\n            if any(sql[0].get(k) != claims[v] for k,v in mapped.items()) or sql[0].get('app_instance_id') != '36495109-0581-408c-baf0-d419c5caf534' or type(sql[0].get('version_code')) is not int or sql[0]['version_code'] != 385211304:\n                raise CertificationStop('PERMIT_INVALID')\n            if _utc(sql[0].get('expires_at')) != _utc(claims['certification_expires_at']):\n                raise CertificationStop('PERMIT_INVALID')\n        if claims['candidate_sha'] != manifest.get('certified_candidate_sha'):\n            raise CertificationStop('BINDING_MISMATCH')\n        expected = {'run_id', 'request_id', 'worker_id', 'lease_id', 'account_id', 'serial',\n", "        required = {'permit_id', 'nonce', 'candidate_sha', 'account_id', 'serial', 'package',\n                    'device_id', 'version_name', 'certification_not_before', 'certification_expires_at',\n                    'max_candidate_admissions'}\n        if set(claims) != required or type(claims['max_candidate_admissions']) is not int or claims['max_candidate_admissions'] != 1:\n            raise CertificationStop('PERMIT_INVALID')\n        if any(type(claims[k]) is not str or not claims[k] or claims[k].strip() != claims[k]\n               for k in required - {'max_candidate_admissions'}):\n            raise CertificationStop('PERMIT_INVALID')\n        if claims['candidate_sha'] != manifest.get('certified_candidate_sha'):\n            raise CertificationStop('BINDING_MISMATCH')\n        expected = {'run_id', 'request_id', 'worker_id', 'lease_id', 'account_id', 'serial',\n"), ("        if not _utc(claims['certification_not_before']) <= now < _utc(claims['certification_expires_at']):\n            raise CertificationStop('PERMIT_INVALID')\n        binding = dict(execution_binding, permit_id=permit_id, permit_hash=hashlib.sha256(_bytes(claims)).hexdigest())\n        if claims['max_candidate_admissions'] == 2:\n            binding['max_candidate_admissions'] = 2\n        _canonical_authority(binding)\n        # Root creation belongs to the governed launcher, never implicit issuance recovery.\n        store = _PermitStore(directory, binding)\n", "        if not _utc(claims['certification_not_before']) <= now < _utc(claims['certification_expires_at']):\n            raise CertificationStop('PERMIT_INVALID')\n        binding = dict(execution_binding, permit_id=permit_id, permit_hash=hashlib.sha256(_bytes(claims)).hexdigest())\n        _canonical_authority(binding)\n        # Root creation belongs to the governed launcher, never implicit issuance recovery.\n        store = _PermitStore(directory, binding)\n"), ('            store.terminal(reason)\n        finally:\n            store.close()\n\n\ndef read_certification_cycles(binding, slots):\n    """Bounded read-only replay of exact durable receipts; never UI truth creation.\n\n    Fresh run/request plus admission monotonic boundary exclude earlier receipts.\n    Mode2 restart is forbidden: monotonic evidence is never compared across boots.\n    """\n    import sqlite3\n    from follow60_ordering_v2_ledger_v1 import DEFAULT_PATH, LedgerScope, OrderingLedger\n    path = Path(DEFAULT_PATH)\n    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():\n        raise ValueError(\'business_ledger_missing\')\n    if not isinstance(slots, list) or not 1 <= len(slots) <= 2:\n        raise ValueError(\'slot_count_invalid\')\n    with sqlite3.connect(path.as_uri()+\'?mode=ro\',uri=True,timeout=3) as conn:\n        conn.row_factory=sqlite3.Row\n        rows=list(conn.execute(\'SELECT * FROM follow60_ordering_v2_receipts WHERE account_id=? AND run_id=? AND request_id=? ORDER BY created_at_monotonic,rowid LIMIT 25\',\n                              (binding[\'account_id\'],binding[\'run_id\'],binding[\'request_id\'])))\n    if not rows or len(rows) >= 25:\n        raise ValueError(\'ledger_missing_or_excessive\')\n    if len({r[\'action_id\'] for r in rows}) != len(slots):\n        raise ValueError(\'action_count_mismatch\')\n    keys=(\'account_id\',\'run_id\',\'request_id\',\'business_session_id\',\'target_id\',\'action_id\',\'candidate_username\',\'ordering_version\')\n    cycles=[]\n    for index,slot in enumerate(slots):\n        key=slot[\'key\']\n        if len(key)!=4 or key[:2] != [binding[\'run_id\'],binding[\'account_id\']]:\n            raise ValueError(\'slot_binding_mismatch\')\n        group=[r for r in rows if r[\'candidate_username\']==key[3]]\n        if not group:\n            raise ValueError(\'candidate_receipts_missing\')\n        scope=LedgerScope(**{k:group[0][k] for k in keys});ledger=OrderingLedger(scope)\n        floor=slot[\'admitted_at_monotonic\']\n        ceiling=slots[index+1][\'admitted_at_monotonic\'] if index+1<len(slots) else time.monotonic()\n        if not isinstance(floor,(int,float)) or isinstance(floor,bool) or not math.isfinite(floor) or not 0<floor<=ceiling:\n            raise ValueError(\'admission_time_invalid\')\n        payloads={}\n        for row in group:\n            stamp=row[\'created_at_monotonic\']\n            if not all(row[k]==getattr(scope,k) for k in keys) or row[\'stage\'] in payloads or row[\'acknowledged\'] not in (0,1):\n                raise ValueError(\'ledger_scope_or_duplicate\')\n            if not math.isfinite(stamp) or not floor<=stamp<=ceiling:\n                raise ValueError(\'stale_receipt\')\n            payload=json.loads(row[\'payload_json\']);ack=ledger.apply_receipt(row[\'stage\'],payload)\n            if ack[\'idempotency_key\']!=row[\'idempotency_key\'] or ledger.receipts[row[\'stage\']].payload_hash!=row[\'payload_hash\']:\n                raise ValueError(\'ledger_corrupt\')\n            payloads[row[\'stage\']]=payload\n        required={\'profile_certified\',\'post_opened\',\'like_verified\',\'profile_reentry_verified\',\'follow_pending\',\'follow_verified\',\'mute_posts_verified\',\'mute_stories_verified\',\'return_ct_exact\',\'cycle_complete\'}\n        if ledger.stages != required:\n            raise ValueError(\'cycle_stages_unproved\')\n        like=payloads[\'like_verified\'];follow=payloads[\'follow_verified\'];cycle=payloads[\'cycle_complete\']\n        if not (like.get(\'like_action_state\')==\'LIKE_ACTION_PERFORMED_NOW\' and like.get(\'real_tap_sent\') is True and like.get(\'fresh_like_verified\') is True):\n            raise ValueError(\'real_like_unproved\')\n        if not (follow.get(\'tap_sent\') is True and follow.get(\'follow_state_after\')==\'following\' and follow.get(\'candidate_username\')==key[3]):\n            raise ValueError(\'real_follow_unproved\')\n        if not all(payloads[s].get(\'verified\') is True for s in (\'mute_posts_verified\',\'mute_stories_verified\',\'return_ct_exact\')):\n            raise ValueError(\'verification_unproved\')\n        if cycle.get(\'critical_persistence_acknowledged\') is not True or cycle.get(\'candidate_username\')!=key[3]:\n            raise ValueError(\'cycle_persistence_unproved\')\n        if \'completed_action_id\' in slot and (slot[\'completed_action_id\']!=scope.action_id or slot[\'completed_receipt_hashes\']!={k:v.payload_hash for k,v in ledger.receipts.items()}):\n            raise ValueError(\'completed_cycle_changed\')\n        cycles.append(ledger)\n    if len({c.scope.action_id for c in cycles})!=len(slots) or len({c.scope.candidate_username for c in cycles})!=len(slots) or sum(len(c.receipts) for c in cycles)!=len(rows):\n        raise ValueError(\'cycle_scope_overlap\')\n    return cycles\n', '            store.terminal(reason)\n        finally:\n            store.close()\n')]

def restore_exact_certification_delta(source):
 import hashlib
 if hashlib.sha256(source.encode()).hexdigest()!=CERTIFICATION_TWO_SOURCE_SHA256:
  raise AssertionError('certification_source_changed')
 for approved,original in CERTIFICATION_TWO_EXACT_HUNKS:
  if source.count(approved)!=1:raise AssertionError('certification_delta_shape_changed')
  source=source.replace(approved,original,1)
 if hashlib.sha256(source.encode()).hexdigest()!=CERTIFICATION_TWO_BASE_SHA256:
  raise AssertionError('certification_baseline_not_exact')
 return source

class SelfExclusionTests(unittest.TestCase):
 def test_first(self):
  rows=[row('nab_youss'),row('next','follow')];result,t,logs,_=pick(rows)
  self.assertIs(result,rows[1]);self.assertEqual(t.skips,['source_account_self']);self.assertEqual(logs[0][1]['skip_reason'],'source_account_self')
 def test_middle(self):
  rows=[row('first'),row('nab_youss'),row('last')];self.assertIs(pick(rows)[0],rows[0]);self.assertIs(pick(rows[1:])[0],rows[2])
 def test_last(self):self.assertIsNone(pick([row('nab_youss')])[0])
 def test_normalization(self):
  for a in ['nab_youss',' @NAB_YOUSS ','@@nab_youss']:
   for b in ['NAB_YOUSS',' @nab_youss ']:
    with self.subTest(a=a,b=b):self.assertIsNone(pick([row(b)],a)[0])
 def test_unknown_nonself(self):
  x=row('other');self.assertIs(pick([x])[0],x)
 def test_follow_nonself(self):
  x=row('other','follow');self.assertIs(pick([x])[0],x)
 def test_negative_ctas(self):
  for c in ['following','message','requested','contact','follow_back']:
   with self.subTest(cta=c):self.assertIsNone(pick([row('other',c)])[0])
 def test_self_all_ctas(self):
  for c in ['unknown','follow','following','message','requested','contact']:
   with self.subTest(cta=c):self.assertEqual(pick([row('nab_youss',c)])[1].skips,['source_account_self'])
 def test_ct_distinct(self):
  result,t,_,_=pick([row('pierrearnaud.destremau'),row('next')]);self.assertEqual(result['username'],'next');self.assertEqual(t.skips,['source_profile'])
 def test_absent_identity_unchanged(self):
  for i in [None,'','   ']:
   with self.subTest(identity=i):self.assertEqual(pick([row('nab_youss')],i)[0]['username'],'nab_youss')
 def test_other_identity(self):self.assertEqual(pick([row('nab_youss')],'other')[0]['username'],'nab_youss')
 def test_near_matches(self):
  for n in ['nab_youss2','nab_yous','x_nab_youss','nab.youss']:
   with self.subTest(name=n):self.assertEqual(pick([row(n)])[0]['username'],n)
 def test_no_cross_binding(self):
  rows=[row('nab_youss'),row('neighbor','follow')];before=copy.deepcopy(rows);self.assertIs(pick(rows)[0],rows[1]);self.assertEqual(rows,before)
 def test_self_no_admission_or_transport(self):
  result,t,_,_=pick([row('nab_youss')]);self.assertIsNone(result);self.assertEqual(t.picks,0)
  branch=next(n for n in ast.walk(fn(R,'_first_eligible_follower_pick')) if isinstance(n,ast.If) and '_verified_current_account_key' in ast.unparse(n.test))
  self.assertEqual(sorted(ast.unparse(n.func) for n in ast.walk(branch) if isinstance(n,ast.Call)),sorted(['log','c.get','_expl_v1.note_visible_skip']))
  self.assertIsInstance(branch.body[-1],ast.Continue)
 def test_h30_post_resume_viewport(self):
  result,_,_,seen=pick([row('dvmarco','follow'),row('adahi_morichelli_duma')],checkpoint=True);self.assertEqual(result['username'],'dvmarco');self.assertEqual(seen,['dvmarco'])
 def test_h07_post_resume_viewport(self):
  result,_,_,seen=pick([row('vincplanet'),row('sophie_labaz')],checkpoint=True);self.assertEqual(result['username'],'vincplanet');self.assertEqual(seen,['vincplanet'])
 def test_c28_no_resume(self):
  result,_,_,seen=pick([row('nab_youss'),row('_danilo_franca_','follow'),row('rigot.vincent','follow')]);self.assertEqual(result['username'],'_danilo_franca_');self.assertEqual(seen,[])
 def test_normalizer_visual_parity(self):
  nav=ast.parse((ROOT/'instagram_navigation.py').read_text());env={};execute([fn(R,'_norm_ig_handle'),fn(nav,'_normalize_handle')],env)
  for s in ['','nab_youss',' @NAB_YOUSS ','@@nab_youss','a.b_c']:self.assertEqual(env['_norm_ig_handle'](s),env['_normalize_handle'](s))
 def test_guard_only_source_and_no_backend_substitution(self):
  main=fn(R,'_main_impl');guard=next(n for n in main.body if isinstance(n,ast.If) and 'not unfollow_session_run' in ast.unparse(n.test))
  failed=next(n for n in guard.body if isinstance(n,ast.If) and ast.unparse(n.test)=='not identity.ok');self.assertTrue(any(isinstance(n,ast.Return) for n in ast.walk(failed)))
  for actual in ['nab_youss','other','',None,False]:
   env=dict(identity=types.SimpleNamespace(actual_logged_in_username=actual),verified_current_account_username=None);execute([guard.body[-1]],env);self.assertEqual(env['verified_current_account_username'],actual or None)
  assigns=[n for n in ast.walk(main) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='verified_current_account_username' for t in n.targets)]
  self.assertEqual([ast.unparse(n.value) for n in assigns],['None','identity.actual_logged_in_username or None'])
 def test_real_propagation_calls(self):
  for owner,callee in [(fn(R,'_main_impl'),'dispatch_account_session'),(fn(R,'_main_impl'),'_run_followers_list_engine_session'),(fn(O,'dispatch_account_session'),'run_account_session'),(fn(O,'run_account_session'),'_run_follow_target_rotation')]:
   calls=[n for n in ast.walk(owner) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id==callee];self.assertEqual(len(calls),1)
   self.assertEqual([ast.unparse(k.value) for k in calls[0].keywords if k.arg=='verified_current_account_username'],['verified_current_account_username'])
  b=next(n for n in ast.walk(fn(O,'_run_follow_target_rotation')) if isinstance(n,ast.If) and ast.unparse(n.test)=='verified_current_account_username is not None')
  for a in [None,'nab_youss','other']:
   env=dict(verified_current_account_username=a,call_kwargs={'account_id':'keep'});execute([b],env)
   self.assertEqual(env['call_kwargs'],{'account_id':'keep',**({'verified_current_account_username':a} if a is not None else {})})
 def test_only_picker_and_identity_parameter_delta_in_engine(self):
  baseline=ast.parse(subprocess.check_output(['git','show',BASE+':runner.py'],cwd=ROOT,text=True));old=fn(baseline,'_run_followers_list_engine_session');new=copy.deepcopy(fn(R,'_run_followers_list_engine_session'))
  new.args.kwonlyargs.pop();new.args.kw_defaults.pop()
  new.body=[n for n in new.body if not(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_verified_current_account_key' for t in n.targets))]
  picker=fn(new,'_first_eligible_follower_pick');loop=next(n for n in picker.body if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='c')
  loop.body=[n for n in loop.body if not(isinstance(n,ast.If) and '_verified_current_account_key' in ast.unparse(n.test))]
  self.assertEqual(ast.dump(old,include_attributes=False),ast.dump(new,include_attributes=False))


 def test_real_446_xml_and_372_package_equivalent(self):
  import base64,zlib,hashlib,re,xml.etree.ElementTree as ET
  raw=zlib.decompress(base64.b64decode(REAL_446_XML_ZLIB))
  self.assertEqual(hashlib.sha256(raw).hexdigest(),'452efff719a6b01595f399a188af168a6340ca4deaef1279591d4a0e8e4186a3')
  nav=ast.parse((ROOT/'instagram_navigation.py').read_text())
  env=dict(re=re,ET=ET,_FOLLOWERS_HANDLE_RE=re.compile(r'^[a-zA-Z0-9._]{1,30}$'),log=lambda *a,**kw:None)
  execute([fn(nav,n) for n in ['_normalize_handle','_parse_follow_list_xml_bounds','_followers_bounds_center','_normalize_own_unified_row_cta_xml_text','_extract_own_unified_followers_usernames_from_hierarchy_xml']],env)
  for package in ['com.instagram.android','com.instagram.androie']:
   with self.subTest(package=package):
    # The 372 variant is explicitly synthetic; full historical XML was not retained.
    xml=raw.decode().replace('com.instagram.android',package)
    rows=env['_extract_own_unified_followers_usernames_from_hierarchy_xml'](xml,source_profile_username='pierrearnaud.destremau')
    self.assertEqual(rows[0]['username'],'nab_youss');self.assertEqual(rows[0]['row_cta_xml_class'],'unknown')
    self.assertEqual(rows[0]['row_cta_xml_text'],'');self.assertEqual(rows[1]['row_cta_xml_class'],'follow')
    self.assertEqual(pick(rows)[0]['username'],'_danilo_franca_')
 def test_protected_navigation_bytes_unchanged(self):
  for name in ['instagram_navigation.py','follow60_mainline_v2.py','account_identity_guard.py','visual_row_mapping.py','certification_admission.py','target_followers_progressive_resume_v2.py']:
   with self.subTest(path=name):
    actual=(ROOT/name).read_bytes()
    if name=='certification_admission.py':actual=restore_exact_certification_delta(actual.decode()).encode()
    self.assertEqual(actual,subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT))
 def test_certification_delta_rejects_unrelated_mutation(self):
  source=(ROOT/'certification_admission.py').read_text()
  for altered in (source+'\n# unrelated\n',source.replace("return 'DISABLED'","return 'ENABLED'",1)):
   with self.subTest(size=len(altered)),self.assertRaises(AssertionError):restore_exact_certification_delta(altered)
 def test_certification_reverse_requires_exact_hunks_and_baseline(self):
  from unittest.mock import patch
  import hashlib
  source=(ROOT/'certification_admission.py').read_text()
  altered=source+CERTIFICATION_TWO_EXACT_HUNKS[0][0]
  with patch.dict(globals(),CERTIFICATION_TWO_SOURCE_SHA256=hashlib.sha256(altered.encode()).hexdigest()):
   with self.assertRaisesRegex(AssertionError,'delta_shape'):restore_exact_certification_delta(altered)
  wrong=list(CERTIFICATION_TWO_EXACT_HUNKS);wrong[0]=(wrong[0][0],wrong[0][1]+'# wrong baseline\n')
  with patch.dict(globals(),CERTIFICATION_TWO_EXACT_HUNKS=wrong):
   with self.assertRaisesRegex(AssertionError,'baseline_not_exact'):restore_exact_certification_delta(source)

# Exact retained C28 XML, compressed for portable replay without external files.
REAL_446_XML_ZLIB='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'

if __name__=='__main__':unittest.main()

