"""Exact retained XML, adversarial surface grammar and real-reader parity. Offline only."""
from __future__ import annotations
import ast,copy,hashlib,json,subprocess,types,unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
import dm_thread_truth as truth
import dm_ui_446_surface as surface
import dm_ui_446_evidence as ui
import instagram_navigation as nav
from test_dm_446_readers import Device,Clock,DATA,fixture,P,TARGET
ROOT=Path(__file__).resolve().parents[1]
BASE='37beae42d2ab3f30aa54f02960da81c642d0d1b1'
CTX=('446.0.0.49.77',385211304,1080,2340)
CAPTURES=json.loads((ROOT/'tests/fixtures/dm_446_multisurface.json').read_text())
def baseline(name):return subprocess.check_output(['git','-C',str(ROOT),'show',BASE+':'+name],text=True)
def old_truth():
 m=types.ModuleType('historical_truth');exec(compile(baseline('dm_thread_truth.py'),'<baseline_truth>','exec'),m.__dict__);return m

def node(root,suffix):return next(n for n in root.iter('node') if n.get('resource-id','').split(':id/')[-1]==suffix)
class MultisurfaceTests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock();self.clock.now=0.;p=patch.object(nav,'time',self.clock);p.start();self.addCleanup(p.stop)
  p=patch.object(nav,'log',lambda *a,**kw:None);p.start();self.addCleanup(p.stop)
 def read(self,xmls,target,alter=None):
  d=Device(xmls,clock=self.clock)
  if alter:alter(d)
  out=nav.detect_dm_thread_state(d,target,pkg=P)
  return out,d
 def reject(self,root,target,ctx=CTX):
  self.assertNotEqual(truth.classify_dm_history(ET.tostring(root,encoding='unicode'),P,target,surface_context=ctx)[0],'empty_new_thread')
 def test_exact_capture_hashes_and_positive_intro(self):
  for name,c in CAPTURES.items():
   with self.subTest(name=name):
    self.assertEqual(hashlib.sha256(c['xml'].encode()).hexdigest(),c['sha256'])
    self.assertEqual(truth.classify_dm_history(c['xml'],P,c['target'])[0],'unknown')
    self.assertEqual(truth.classify_dm_history(c['xml'],P,c['target'],surface_context=CTX)[0],'empty_new_thread')
 def test_actual_reader_completes_a0_history_probe(self):
  for name,c in CAPTURES.items():
   with self.subTest(name=name):
    self.clock.now=0.;(state,proof),d=self.read(c['xml'],c['target'])
    self.assertEqual(state,'empty_new_thread')
    self.assertEqual(proof['reason'],'a0_history_probe_completed')
    self.assertGreaterEqual(self.clock.now,0.8)
    self.assertEqual(proof['positive_empty_observations'],d.reads)
    self.assertEqual((d.currents,d.dimensions),(2*d.reads,d.reads))
 def test_all_node_class_role_bounds_package_visibility_mutations(self):
  for name,c in CAPTURES.items():
   r=ET.fromstring(c['xml'])
   for i,n in enumerate(r.iter('node')):
    for field,value in [('class','unknown.View'),('resource-id','unknown:id/node'),('bounds','[0,0][0,0]'),('package','foreign'),('visible-to-user','false')]:
     with self.subTest(name=name,node=i,field=field):
      z=copy.deepcopy(r);list(z.iter('node'))[i].set(field,value)
      # Descendants of admitted status/navigation chrome are volatile and
      # non-authoritative; business nodes and root structural nodes stay strict.
      original=list(r.iter('node'))[i]
      if original.get('package') == 'com.android.systemui' and original not in r:
       self.assertEqual(truth.classify_dm_history(ET.tostring(z,encoding='unicode'),P,c['target'],surface_context=CTX)[0],'empty_new_thread')
      else:
       self.reject(z,c['target'])
 def test_invalid_context_and_wrong_recipient(self):
  for name,c in CAPTURES.items():
   r=ET.fromstring(c['xml'])
   for ctx in (None,('372.0.0.48.60',377611059,1080,2340),('446.0.0.49.77',385211305,1080,2340),('446.0.0.49.77',True,1080,2340),('446.0.0.49.77',385211304,1080,2300),{'surface_ok':True}):
    with self.subTest(name=name,ctx=ctx):self.reject(r,c['target'],ctx)
   self.reject(r,'wrong_recipient')
 def test_draft_send_enabled_duplicate_missing_and_cross_family(self):
  for name,c in CAPTURES.items():
   for kind in ('draft','enabled_send','duplicate_app','duplicate_intro','missing_intro','cross_hint'):
    with self.subTest(name=name,kind=kind):
     r=ET.fromstring(c['xml'])
     if kind=='draft':node(r,'row_thread_composer_edittext').set('text','typed draft')
     elif kind=='enabled_send':node(r,'row_thread_composer_send_button_container').set('enabled','true')
     elif kind=='duplicate_app':r.append(copy.deepcopy(r[0]))
     elif kind=='duplicate_intro':ml=node(r,'message_list');ml.append(copy.deepcopy(ml[0]))
     elif kind=='missing_intro':node(r,'message_list').clear()
     else:node(r,'row_thread_composer_edittext').set('hint','Try /silent ..' if name=='community' else 'Message…')
     self.reject(r,c['target'])
     if kind=='draft':self.assertIsNotNone(surface.qualify(r,P,c['target'],CTX))
 def test_view_community_is_not_empty_authority(self):
  c=CAPTURES['community']
  for kind in ('wrong_parent','generic_button','changed_label','partial','duplicate','wrong_header','overlay'):
   with self.subTest(kind=kind):
    r=ET.fromstring(c['xml']);button=node(r,'igds_button');label=next(n for n in button if n.get('text')=='View community')
    if kind=='wrong_parent':button.set('resource-id','generic')
    elif kind=='generic_button':button.set('class','android.widget.Button')
    elif kind=='changed_label':label.set('text','Community')
    elif kind=='partial':button.remove(list(button)[-1])
    elif kind=='duplicate':button.append(copy.deepcopy(label))
    elif kind=='wrong_header':node(r,'header_subtitle').set('content-desc','other')
    else:r.append(ET.Element('node',{'package':'com.android.systemui','bounds':'[0,100][1080,2205]','class':'android.view.View'}))
    self.reject(r,c['target'])
 def test_known_sibling_cannot_expand_over_composer(self):
  for name,c in CAPTURES.items():
   for role in ('direct_thread_header','direct_thread_business_chat_action_bar','new_friend_bump_sticker_banner_container'):
    r=ET.fromstring(c['xml']);n=surface.one(r,role)
    if n is None:continue
    b=surface.rect(n);n.set('bounds',f'[{b[0]},{b[1]}][{b[2]},2205]')
    with self.subTest(name=name,role=role):
     self.reject(r,c['target'])
     self.assertFalse(ui.composer_evidence(ET.tostring(r,encoding='unicode'),P,display_size=(1080,2340),surface_context=CTX)['visible'])
 def test_positive_history_preserved_on_each_surface(self):
  for name,c in CAPTURES.items():
   r=ET.fromstring(c['xml']);ml=node(r,'message_list')
   msg=ET.SubElement(ml,'node',{'resource-id':P+':id/message_content','class':'android.view.View','package':P,'bounds':'[100,1200][800,1300]'})
   ET.SubElement(msg,'node',{'resource-id':P+':id/direct_text_message_text_view','class':'android.widget.TextView','package':P,'text':'existing message','bounds':'[110,1210][790,1290]'})
   self.assertEqual(truth.classify_dm_history(ET.tostring(r,encoding='unicode'),P,c['target'],surface_context=CTX)[0],'existing_thread')
 def test_actual_reader_freshness_and_identity_fail_closed(self):
  for name,c in CAPTURES.items():
   x=c['xml'];target=c['target']
   for kind,xmls,t,alter in [
    ('wrong_target',x,'wrong',None),('no_target',x,'',None),('unknown',[x,'<hierarchy/>'],target,None),
    ('exception',[x,RuntimeError()],target,None),('changed_recipient',[x,x.replace(target,'wrong_recipient')],target,None),
    ('late',x,target,lambda d:setattr(d,'cost',3)),
    ('revoked',x,target,lambda d:setattr(d,'after_read',lambda z:setattr(z,'_instagram_446_observed_context',None))),
    ('activity',x,target,lambda d:setattr(d,'activity','UnqualifiedOverlayActivity')),
    ('foreground',x,target,lambda d:setattr(d,'foregrounds',[dict(package=P,activity=d.activity),dict(package='foreign',activity=d.activity)]))]:
    with self.subTest(name=name,kind=kind):
     self.clock.now=0.;out,d=self.read(xmls,t,alter);self.assertEqual(out[0],'unknown')
   self.clock.now=0.;out,d=self.read([x,fixture('B_30_existing_thread')],target);self.assertEqual(out[0],'existing_thread')
 def test_actual_selector_resolution_and_replacement(self):
  for name,c in CAPTURES.items():
   d=Device(c['xml']);self.assertTrue(nav._dm_446_composer(d,P)[0]);self.assertEqual((d.reads,d.currents,d.dimensions),(2,4,2))
   d=Device([c['xml'],'<hierarchy/>']);self.assertFalse(nav._dm_446_composer(d,P)[0])
 def test_fresh_qualification_has_no_cache(self):
  c=CAPTURES['community'];r=ET.fromstring(c['xml']);self.assertIsNotNone(surface.qualify(r,P,c['target'],CTX))
  r[0].set('package','foreign');self.assertIsNone(surface.qualify(r,P,c['target'],CTX))
 def test_historical_truth_exact_differential(self):
  old=old_truth()
  for name,c in DATA.items():
   package=ET.fromstring(c['xml'])[0].get('package')
   for ctx in (None,CTX):
    with self.subTest(name=name,ctx=ctx):self.assertEqual(old.classify_dm_history(c['xml'],package,TARGET),truth.classify_dm_history(c['xml'],package,TARGET,surface_context=ctx))
 def test_historical_reader_truth_and_a0_phase_timing(self):
  tree=ast.parse(baseline('instagram_navigation.py'));env=dict(vars(nav));names={'_dm_446_observation','_dm_446_thread_state','_dm_446_composer'}
  exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<baseline_nav>','exec'),env)
  for name,c in DATA.items():
   with self.subTest(name=name):
    oldclock=Clock();oldclock.now=0.;env['time']=oldclock;self.clock.now=0.
    a=Device(c['xml'],clock=oldclock);b=Device(c['xml'],clock=self.clock)
    old=env['_dm_446_thread_state'](a,TARGET,P,None);new=nav._dm_446_thread_state(b,TARGET,P,None)
    self.assertEqual(old[0],new[0])
    if new[0]=='empty_new_thread':
     self.assertEqual(new[1]['reason'],'a0_history_probe_completed')
     self.assertGreaterEqual(self.clock.now,0.8)
    else:self.assertEqual((a.reads,a.currents,a.dimensions),(b.reads,b.currents,b.dimensions))
 def test_scope_ast_and_truth_predicates_unchanged(self):
  def functions(s):return {n.name:n for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
  for filename,allowed in [('dm_thread_truth.py',{'classify_dm_history'}),('dm_ui_446_evidence.py',{'composer_evidence'}),('instagram_navigation.py',{'_dm_446_observation','_dm_446_thread_state','_dm_446_composer','_dm_detect_history_signal','_dm_outbound_expected_message_match_mode'})]:
   old=functions(baseline(filename));new=functions((ROOT/filename).read_text());self.assertEqual(set(old),set(new))
   self.assertEqual({n for n in old if ast.dump(old[n])!=ast.dump(new[n])},allowed)

  # Approved composer instrumentation may retain evidence, never alter decisions.
  composer=functions((ROOT/'instagram_navigation.py').read_text())['_dm_446_composer']
  class StripApprovedComposerEvidence(ast.NodeTransformer):
   def visit_FunctionDef(self,node):
    node=self.generic_visit(node)
    if node.name=='_dm_446_composer':
     node.body=[n for n in node.body if not (isinstance(n,ast.ImportFrom) and n.module=='dm_446_observation_retention') and not (isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('record','result'))]
    return node
   def visit_If(self,node):
    if ast.dump(node.test)==ast.dump(ast.parse('record is not None',mode='eval').body):return None
    return self.generic_visit(node)
   def visit_Try(self,node):
    # Only the exact evidence-only finally is removable.
    expected=ast.parse('if record is not None: record.complete(result)').body
    if ast.dump(ast.Module(body=node.finalbody,type_ignores=[]))==ast.dump(ast.Module(body=expected,type_ignores=[])):node.finalbody=[]
    return self.generic_visit(node)
   def visit_Assign(self,node):
    if len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='result' and isinstance(node.value,ast.Tuple):return ast.Return(value=node.value)
    return self.generic_visit(node)
   def visit_Return(self,node):
    if isinstance(node.value,ast.Name) and node.value.id=='result':return None
    return self.generic_visit(node)
   def visit_Call(self,node):
    if isinstance(node.func,ast.Name) and node.func.id=='_dm_446_observation':
     expected=ast.parse('f(**({"_record": record.observation()} if record is not None else {}))',mode='eval').body.keywords[0]
     node.keywords=[k for k in node.keywords if ast.dump(k)!=ast.dump(expected)]
    return self.generic_visit(node)
  normalized=StripApprovedComposerEvidence().visit(composer)
  # The approved API seam changes syntax from UiObject.all() to the installed
  # UiObject.count/info properties. Assert the same semantic contract rather
  # than freezing the obsolete AST spelling.
  calls=[n for n in ast.walk(composer) if isinstance(n,ast.Call)]
  attrs=[n.func.attr for n in calls if isinstance(n.func,ast.Attribute)]
  self.assertNotIn('all',attrs)
  self.assertIn('count', [n.attr for n in ast.walk(composer) if isinstance(n,ast.Attribute)])
  self.assertIn('info', [n.attr for n in ast.walk(composer) if isinstance(n,ast.Attribute)])
  constants={n.value for n in ast.walk(composer) if isinstance(n,ast.Constant) and isinstance(n.value,str)}
  self.assertIn(':id/row_thread_composer_edittext', ''.join(sorted(constants)))
  for field in ('resourceName','className','enabled','clickable','focusable','bounds'):
   self.assertIn(field, constants)
  self.assertEqual({n for n in old if ast.dump(old[n])!=ast.dump(new[n])},allowed)
  from test_dm_446_composer_live_retention import ComposerRetentionTests
  # Same actual reads/results for every live mismatch, exception and valid surface.
  for name in ('test_multisurface_and_keyboard_equivalence','test_live_mismatches_exact_order','test_exceptions_keep_exact_io_and_results','test_second_qualified_drift_equivalent','test_inactive_no_io'):
   check=ComposerRetentionTests(name)
   try:getattr(check,name)()
   finally:check.tearDown()
  # Revert only the reviewed seam; entire historical classifier must then match.
  s=(ROOT/'dm_thread_truth.py').read_text()
  s=s.replace('expected_username: str | None = None, *, surface_context=None)','expected_username: str | None = None)')
  s=s.replace('    extension = None\n    if surface_context is not None:\n        from dm_ui_446_surface import qualify\n        extension = qualify(root, package, expected_username, surface_context)\n\n','')
  s=s.replace('        if extension and row is extension[\'intro\']:\n            return "compose_recipient_intro"\n','')
  s=s.replace("extension['identity'] if extension and rows[0] is extension['intro'] else _empty_intro_identity(rows[0], package)",'_empty_intro_identity(rows[0], package)')
  s=s.replace("'' if extension and extension['empty_surface'] and rows[0] is extension['intro'] else _empty_surface_reason(root, message_list, package, identity)",'_empty_surface_reason(root, message_list, package, identity)')
  s=s.replace("    generic_business_header = (extension is not None\n                               and extension.get('variant') == 'business_chat_sticker_empty'\n                               and expected_username is not None\n                               and norm(display_name) == norm(expected_username)\n                               and norm(handle) == norm(expected_username))\n    recipient_header = ((bool(header_values) and norm(handle) in header_values\n                        and all(v in {norm(display_name), norm(handle)} for v in header_values))\n                        or generic_business_header)\n", "    recipient_header = (bool(header_values) and norm(handle) in header_values\n                        and all(v in {norm(display_name), norm(handle)} for v in header_values))\n")
  self.assertEqual(ast.dump(ast.parse(s)),ast.dump(ast.parse(baseline('dm_thread_truth.py'))))
if __name__=='__main__':unittest.main()
