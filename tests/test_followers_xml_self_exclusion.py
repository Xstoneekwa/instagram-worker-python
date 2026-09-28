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
   with self.subTest(path=name):self.assertEqual((ROOT/name).read_bytes(),subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT))

# Exact retained C28 XML, compressed for portable replay without external files.
REAL_446_XML_ZLIB='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'

if __name__=='__main__':unittest.main()

