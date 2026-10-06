from pathlib import Path
import json,copy,hashlib,itertools,xml.etree.ElementTree as ET,unittest
from unittest.mock import patch
import dm_ui_446_surface as s,dm_thread_truth as truth,instagram_navigation as nav
from test_dm_446_readers import Device,Clock,P
F=json.loads((Path(__file__).parent/'fixtures/dm_446_current_surface.json').read_text())
class CurrentSurfaceTests(unittest.TestCase):
 def test_exact_retained_xml(self):
  self.assertEqual(hashlib.sha256(F['xml'].encode()).hexdigest(),F['sha256'])
  self.assertEqual(truth.classify_dm_history(F['xml'],s.P,F['target'],surface_context=tuple(F['context']))[0],'empty_new_thread')
 def test_matrix(self):
  root=ET.fromstring(F['xml']);ctx=tuple(F['context']);target=F['target']
  def check(name,r,empty,context=ctx,t=target):
   with self.subTest(case=name):
    result=truth.classify_dm_history(ET.tostring(r,encoding='unicode'),s.P,t,surface_context=context)
    self.assertEqual(result[0],'empty_new_thread' if empty else ('existing_thread' if name=='known_text_history' else 'unknown'))
  for i,order in enumerate(itertools.permutations(range(3))):
   r=copy.deepcopy(root);r[:]=[r[j] for j in order];check('window_order_'+str(i),r,True)
  for kind in ['wrong_target','wrong_version','duplicate_app','foreign_window','fourth_window','status_overlap','unknown_notification','wifi_unknown','header_wrong','draft','enabled_send','composer_disabled','composer_hidden','hint_only_mismatch','known_text_history']:
   r=copy.deepcopy(root);c=ctx;t=target
   def node(role):return next(n for n in r.iter('node') if s.role(n)==role)
   if kind=='wrong_target':t='other'
   elif kind=='wrong_version':c=('372.0.0.48.60',377611059,1080,2340)
   elif kind=='duplicate_app':r[0]=copy.deepcopy(r[1])
   elif kind=='foreign_window':r[0].set('package','foreign')
   elif kind=='fourth_window':r.append(copy.deepcopy(r[0]))
   elif kind=='status_overlap':r[0].set('bounds','[0,0][1080,2100]')
   elif kind=='unknown_notification':next(n for n in r.iter('node') if n.get('content-desc')=='Weather notification: ').set('content-desc','unknown')
   elif kind=='wifi_unknown':node('wifi_signal').set('content-desc','unknown')
   elif kind=='header_wrong':node('header_subtitle').set('content-desc','other')
   elif kind=='draft':node('row_thread_composer_edittext').set('text','draft')
   elif kind=='enabled_send':node('row_thread_composer_send_button_container').set('enabled','true')
   elif kind=='composer_disabled':node('row_thread_composer_edittext').set('enabled','false')
   elif kind=='composer_hidden':node('row_thread_composer_edittext').set('visible-to-user','false')
   elif kind=='hint_only_mismatch':node('row_thread_composer_edittext').set('hint','Try /silent ..')
   elif kind=='known_text_history':
    ml=node('message_list');msg=ET.SubElement(ml,'node',{'resource-id':s.P+':id/message_content','class':'android.view.View','package':s.P,'bounds':'[100,1200][800,1300]'});ET.SubElement(msg,'node',{'resource-id':s.P+':id/direct_text_message_text_view','class':'android.widget.TextView','package':s.P,'text':'existing message','bounds':'[110,1210][790,1290]'})
   # Volatile SystemUI descendants are non-authoritative chrome.
   check(kind,r,kind in {'unknown_notification','wifi_unknown'},c,t)
 def test_systemui_chrome_is_structural_and_volatile_children_are_ignored(self):
  root=ET.fromstring(F['xml']);ctx=tuple(F['context']);target=F['target']
  systems=[n for n in root if n.get('package')=='com.android.systemui']
  self.assertEqual(len(systems),2)
  # Reorder roots and mutate only volatile status contents.
  root[:]=[root[2],root[0],root[1]]
  for n in root.iter():
   if n.get('content-desc') in {'Weather notification: ', 'Wi-Fi three bars.'}:
    n.set('content-desc','arbitrary current value')
  # The proposed structural rule is exercised by the proposal module.
  self.assertIsNotNone(s.qualify(root,s.P,target,ctx))
 def test_systemui_interfering_root_remains_fail_closed(self):
  root=ET.fromstring(F['xml']);ctx=tuple(F['context']);target=F['target']
  extra=ET.Element('node',{'package':'com.android.systemui','class':'android.widget.FrameLayout','bounds':'[0,100][1080,2205]','visible-to-user':'true','clickable':'false','enabled':'true'})
  root.append(extra)
  self.assertIsNone(s.qualify(root,s.P,target,ctx))
 def test_keyboard_viewport_transition_remains_qualified(self):
  p=Path('/Users/admin/phonefarm-runtime/logs/dm446-exact-observations/5b4ac365-f400-461d-837e-e5d5e29ced73/9c8141e1-bca1-49da-97d3-0b33aff08654/composer-5d24e129a8b7466f80d47bfb87986c74/composer_003_observation_1.xml')
  root=ET.fromstring(p.read_text());ctx=tuple(F['context']);target=F['target']
  self.assertIsNotNone(s.qualify(root,s.P,target,ctx))
  next(n for n in root.iter('node') if s.role(n)=='row_thread_composer_container').set('bounds','[0,1963][1080,2206]')
  self.assertIsNone(s.qualify(root,s.P,target,ctx))
 def test_exact_post_focus_adbkeyboard_no_tray_shape_is_qualified(self):
  p=Path('/Users/admin/phonefarm-runtime/logs/dm446-exact-observations/5b4ac365-f400-461d-837e-e5d5e29ced73/9c8141e1-bca1-49da-97d3-0b33aff08654/composer-5d24e129a8b7466f80d47bfb87986c74/composer_003_observation_1.xml')
  root=ET.fromstring(p.read_text())
  self.assertIsNotNone(s.qualify(root,s.P,F['target'],tuple(F['context'])))
  bad=copy.deepcopy(root)
  next(n for n in bad if n.get('package')=='com.android.adbkeyboard').set('bounds','[0,2000][1080,2340]')
  self.assertIsNone(s.qualify(bad,s.P,F['target'],tuple(F['context'])))
  bad=copy.deepcopy(root)
  next(n for n in bad if n.get('package')=='com.android.adbkeyboard').set('package','com.example.keyboard')
  self.assertIsNone(s.qualify(bad,s.P,F['target'],tuple(F['context'])))
 def test_a0_probe_with_qualified_current_surface(self):
  x=F['xml']
  for case,xmls,target in [('stable',x,F['target']),('wrong_recipient',x,'wrong'),('second_unknown',[x,'<hierarchy/>'],F['target']),('qualified_change',[x,x.replace('rotation="0"','rotation="1"')],F['target'])]:
   with self.subTest(case=case):
    clock=Clock();clock.now=0;d=Device(xmls,clock=clock)
    with patch.object(nav,'time',clock),patch.object(nav,'log',lambda *a,**kw:None):out=nav.detect_dm_thread_state(d,target,pkg=P)
    self.assertEqual(out[0],'empty_new_thread' if case == 'stable' else 'unknown')
    self.assertEqual((d.currents,d.dimensions),(2*d.reads,d.reads))
    if case == 'stable':
     self.assertEqual(out[1]['reason'],'a0_history_probe_completed')
     self.assertGreaterEqual(clock.now,0.8)
     self.assertEqual(out[1]['positive_empty_observations'],d.reads)
    elif case == 'qualified_change': self.assertEqual(out[1]['reason'],'conversation_path_unqualified')
    else:self.assertEqual(d.reads,1 if case=='wrong_recipient' else 2)
if __name__=='__main__':unittest.main()
