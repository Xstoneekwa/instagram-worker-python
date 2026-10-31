from pathlib import Path
import json,copy,hashlib,itertools,xml.etree.ElementTree as ET,unittest
from unittest.mock import patch
import dm_ui_446_surface as s,dm_thread_truth as truth,instagram_navigation as nav
from test_dm_446_readers import Device,Clock,P
F=json.loads((Path(__file__).parent/'fixtures/dm_446_current_surface.json').read_text())
class CurrentSurfaceTests(unittest.TestCase):
 def test_janecandika_business_chat_sticker_shape_v1_v6(self):
  j=F['janecandika']; x=j['xml']; ctx=tuple(j['context']); target=j['target']
  self.assertEqual(s.qualify(ET.fromstring(x),s.P,target,ctx)['variant'],'business_chat_sticker_empty')
  self.assertEqual(truth.classify_dm_history(x,s.P,target,surface_context=ctx)[0],'empty_new_thread')
  cases=[('wrong_recipient',x,'wrong','unknown'),('no_target',x,'','unknown')]
  r=ET.fromstring(x); ml=next(n for n in r.iter('node') if s.role(n)=='message_list'); msg=ET.SubElement(ml,'node',{'resource-id':s.P+':id/message_content','class':'android.view.View','package':s.P,'bounds':'[100,1200][800,1300]'}); ET.SubElement(msg,'node',{'resource-id':s.P+':id/direct_text_message_text_view','class':'android.widget.TextView','package':s.P,'text':'existing message','bounds':'[110,1210][790,1290]'})
  cases.append(('real_history',ET.tostring(r,encoding='unicode'),target,'existing_thread'))
  r=ET.fromstring(x); r.append(ET.Element('node',{'package':'com.android.systemui','class':'android.widget.FrameLayout','bounds':'[0,100][1080,2205]','visible-to-user':'true','clickable':'false','enabled':'true'})); cases.append(('overlay',ET.tostring(r,encoding='unicode'),target,'unknown'))
  r=ET.fromstring(x); next(n for n in r.iter('node') if s.role(n)=='row_thread_composer_edittext').set('visible-to-user','false'); cases.append(('missing_composer',ET.tostring(r,encoding='unicode'),target,'unknown'))
  for name,xml,target_i,expected in cases:
   with self.subTest(case=name): self.assertEqual(truth.classify_dm_history(xml,s.P,target_i,surface_context=ctx)[0],expected)
 def test_janecandika_reader_excludes_only_qualified_sticker_tray(self):
  j=F['janecandika']; clock=Clock(); d=Device(j['xml'],clock=clock)
  with patch.object(nav,'time',clock),patch.object(nav,'log',lambda *a,**kw:None): state,proof=nav.detect_dm_thread_state(d,j['target'],pkg=s.P)
  self.assertEqual(state,'empty_new_thread'); self.assertEqual(proof['reason'],'a0_history_probe_completed')
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
 def test_personal_mutual_sticker_empty_exact_observation(self):
  from dm_ui_446_evidence import composer_evidence
  f=F['personal_mutual_sticker_empty']; x=f['xml']; ctx=tuple(f['context'])
  self.assertEqual(hashlib.sha256(x.encode('utf-8')).hexdigest(),f['sha256'])
  self.assertEqual(f['native_source_sha256'],'5de3ef242720909e8e6dee92fa2ccf5788fa099e787554fcdb530642952fff46')
  out=s.qualify(ET.fromstring(x),s.P,f['target'],ctx)
  self.assertEqual(out['variant'],'personal_mutual_sticker_empty')
  self.assertTrue(out['empty_surface'])
  self.assertEqual(out['identity'],('Surface Fixture User',f['target']))
  self.assertTrue(composer_evidence(x,s.P,display_size=ctx[2:],surface_context=ctx)['visible'])
  state,proof=truth.classify_dm_history(x,s.P,f['target'],surface_context=ctx)
  self.assertEqual(state,'empty_new_thread')
  self.assertEqual(proof['reason'],'positive_empty_structure')
 def test_personal_mutual_sticker_refusals_and_positive_history(self):
  from dm_ui_446_evidence import composer_evidence
  f=F['personal_mutual_sticker_empty']; original=ET.fromstring(f['xml']); ctx=tuple(f['context'])
  kinds=('wrong_target','wrong_header','generic_business_subtitle','wrong_version','draft',
         'composer_hidden','composer_disabled','hint_mismatch','duplicate_composer',
         'unknown_intro','unknown_row','list_over_editor','foreign_window','rotated','known_history')
  for kind in kinds:
   root=copy.deepcopy(original); target=f['target']; context=ctx
   def node(role):return next(n for n in root.iter('node') if s.role(n)==role)
   if kind=='wrong_target':target='different_recipient'
   elif kind=='wrong_header':node('header_subtitle').set('content-desc','different_recipient')
   elif kind=='generic_business_subtitle':node('header_subtitle').set('content-desc','Business chat')
   elif kind=='wrong_version':context=('372.0.0.48.60',377611059,1080,2340)
   elif kind=='draft':node('row_thread_composer_edittext').set('text','Unsent draft')
   elif kind=='composer_hidden':node('row_thread_composer_edittext').set('visible-to-user','false')
   elif kind=='composer_disabled':node('row_thread_composer_edittext').set('enabled','false')
   elif kind=='hint_mismatch':node('row_thread_composer_edittext').set('hint','Unrecognized placeholder')
   elif kind=='duplicate_composer':node('row_thread_composer_container').append(copy.deepcopy(node('row_thread_composer_edittext')))
   elif kind=='unknown_intro':
    next(n for n in node('message_list').iter('node') if n.get('text')=='You follow each other on Instagram').set('text','Unrecognized relationship education')
   elif kind=='unknown_row':
    ET.SubElement(node('message_list'),'node',{'package':s.P,'class':'android.widget.TextView','text':'Unknown content','bounds':'[100,1200][800,1300]'})
   elif kind=='list_over_editor':
    ET.SubElement(node('message_list'),'node',{'package':s.P,'class':'android.view.View','visible-to-user':'true','bounds':node('row_thread_composer_edittext').get('bounds')})
   elif kind=='foreign_window':
    root.append(ET.Element('node',{'package':'com.example.overlay','class':'android.widget.FrameLayout','bounds':'[0,100][1080,2205]','visible-to-user':'true','enabled':'true','clickable':'false'}))
   elif kind=='rotated':root.set('rotation','1')
   elif kind=='known_history':
    msg=ET.SubElement(node('message_list'),'node',{'resource-id':s.P+':id/message_content','class':'android.view.View','package':s.P,'bounds':'[100,1200][800,1300]'})
    ET.SubElement(msg,'node',{'resource-id':s.P+':id/direct_text_message_text_view','class':'android.widget.TextView','package':s.P,'text':'Existing message','bounds':'[110,1210][790,1290]'})
   xml=ET.tostring(root,encoding='unicode')
   with self.subTest(case=kind):
    self.assertEqual(truth.classify_dm_history(xml,s.P,target,surface_context=context)[0],
                     'existing_thread' if kind=='known_history' else 'unknown')
    if kind in ('composer_hidden','composer_disabled','duplicate_composer','foreign_window','list_over_editor','rotated','wrong_version'):
     self.assertFalse(composer_evidence(xml,s.P,display_size=ctx[2:],surface_context=context)['visible'])
 def test_personal_mutual_sticker_keeps_a0_probe_and_recipient_gate(self):
  f=F['personal_mutual_sticker_empty']; x=f['xml']
  for case,xmls,target in [('stable',x,f['target']),('no_target',x,''),('wrong_target',x,'different_recipient'),('second_unknown',[x,'<hierarchy/>'],f['target'])]:
   clock=Clock();clock.now=0;d=Device(xmls,clock=clock)
   with patch.object(nav,'time',clock),patch.object(nav,'log',lambda *a,**kw:None):state,proof=nav.detect_dm_thread_state(d,target,pkg=P)
   with self.subTest(case=case):
    self.assertEqual(state,'empty_new_thread' if case=='stable' else 'unknown')
    self.assertEqual((d.currents,d.dimensions),(2*d.reads,d.reads))
    if case=='stable':
     self.assertEqual(proof['reason'],'a0_history_probe_completed')
     self.assertGreaterEqual(clock.now,0.8)
     self.assertEqual(proof['positive_empty_observations'],d.reads)
    else:self.assertEqual(d.reads,2 if case=='second_unknown' else 1)

 def test_personal_mirrored_header_exact_retained_observation(self):
  from dm_ui_446_evidence import composer_evidence
  f=F['personal_mutual_sticker_mirrored_header'];x=f['xml'];ctx=tuple(f['context'])
  self.assertEqual(hashlib.sha256(x.encode('utf-8')).hexdigest(),f['sha256'])
  self.assertEqual(f['native_source_sha256'],'5d70b6ea423f760fc103e0e34f5fece2d4efd8e274c5031fc74d6a62d14d20c7')
  out=s.qualify(ET.fromstring(x),s.P,f['target'],ctx)
  self.assertEqual(out['variant'],'personal_mutual_sticker_empty')
  self.assertEqual(out['identity'],('Surface Fixture User',f['target']))
  self.assertTrue(out['empty_surface'])
  self.assertTrue(composer_evidence(x,s.P,display_size=ctx[2:],surface_context=ctx)['visible'])
  state,proof=truth.classify_dm_history(x,s.P,f['target'],surface_context=ctx)
  self.assertEqual((state,proof['reason']),('empty_new_thread','positive_empty_structure'))
 def test_personal_mirrored_header_dynamic_identity_remains_bound(self):
  f=F['personal_mutual_sticker_mirrored_header'];ctx=tuple(f['context'])
  for display,handle in [('Another Display Name','another_handle.7'),('Dynamic Person','independent_target_2')]:
   x=f['xml'].replace('Surface Fixture User',display).replace(f['target'],handle)
   with self.subTest(display=display,handle=handle):
    out=s.qualify(ET.fromstring(x),s.P,handle,ctx)
    self.assertEqual(out['identity'],(display,handle))
    self.assertEqual(truth.classify_dm_history(x,s.P,handle,surface_context=ctx)[0],'empty_new_thread')
    self.assertIsNone(s.qualify(ET.fromstring(x),s.P,'wrong_recipient',ctx))
    self.assertEqual(truth.classify_dm_history(x,s.P,'',surface_context=ctx)[0],'unknown')
    root=ET.fromstring(x)
    next(n for n in root.iter('node') if s.role(n)=='header_title').set('content-desc','Different display name')
    self.assertIsNone(s.qualify(root,s.P,handle,ctx))
 def test_personal_mirrored_header_refusals_and_positive_history(self):
  from dm_ui_446_evidence import composer_evidence
  f=F['personal_mutual_sticker_mirrored_header'];ctx=tuple(f['context'])
  for kind in ('wrong_target','wrong_subtitle','wrong_description','unknown_intro','unknown_row',
               'missing_composer','duplicate_composer','composer_hidden','composer_disabled',
               'list_over_editor','foreign_overlay','draft','wrong_version','known_history'):
   root=ET.fromstring(f['xml']);target=f['target'];context=ctx
   def node(role):return next(n for n in root.iter('node') if s.role(n)==role)
   if kind=='wrong_target':target='different_recipient'
   elif kind=='wrong_subtitle':node('header_subtitle').set('content-desc','different_recipient')
   elif kind=='wrong_description':node('header_title').set('content-desc','Unrelated description')
   elif kind=='unknown_intro':
    next(n for n in node('message_list').iter('node') if n.get('text')=='You follow each other on Instagram').set('text','Unrecognized relationship education')
   elif kind=='unknown_row':ET.SubElement(node('message_list'),'node',{'package':s.P,'class':'android.widget.TextView','text':'Unknown content','bounds':'[100,1200][800,1300]'})
   elif kind=='missing_composer':
    ed=node('row_thread_composer_edittext');next(p for p in root.iter() if ed in list(p)).remove(ed)
   elif kind=='duplicate_composer':node('row_thread_composer_container').append(copy.deepcopy(node('row_thread_composer_edittext')))
   elif kind=='composer_hidden':node('row_thread_composer_edittext').set('visible-to-user','false')
   elif kind=='composer_disabled':node('row_thread_composer_edittext').set('enabled','false')
   elif kind=='list_over_editor':ET.SubElement(node('message_list'),'node',{'package':s.P,'class':'android.view.View','visible-to-user':'true','bounds':node('row_thread_composer_edittext').get('bounds')})
   elif kind=='foreign_overlay':root.append(ET.Element('node',{'package':'com.example.overlay','class':'android.widget.FrameLayout','bounds':'[0,100][1080,2205]','visible-to-user':'true','enabled':'true','clickable':'false'}))
   elif kind=='draft':node('row_thread_composer_edittext').set('text','Unsent draft')
   elif kind=='wrong_version':context=('372.0.0.48.60',377611059,1080,2340)
   elif kind=='known_history':
    msg=ET.SubElement(node('message_list'),'node',{'resource-id':s.P+':id/message_content','class':'android.view.View','package':s.P,'bounds':'[100,1200][800,1300]'})
    ET.SubElement(msg,'node',{'resource-id':s.P+':id/direct_text_message_text_view','class':'android.widget.TextView','package':s.P,'text':'Existing message','bounds':'[110,1210][790,1290]'})
   xml=ET.tostring(root,encoding='unicode')
   with self.subTest(case=kind):
    self.assertEqual(truth.classify_dm_history(xml,s.P,target,surface_context=context)[0],'existing_thread' if kind=='known_history' else 'unknown')
    if kind in ('missing_composer','duplicate_composer','composer_hidden','composer_disabled','list_over_editor','foreign_overlay','wrong_version'):
     self.assertFalse(composer_evidence(xml,s.P,display_size=ctx[2:],surface_context=context)['visible'])
 def test_personal_mirrored_header_keeps_a0_probe_and_recipient_gate(self):
  f=F['personal_mutual_sticker_mirrored_header'];x=f['xml']
  for case,xmls,target in [('stable',x,f['target']),('no_target',x,''),('wrong_target',x,'different_recipient'),('second_unknown',[x,'<hierarchy/>'],f['target'])]:
   clock=Clock();clock.now=0;d=Device(xmls,clock=clock)
   with patch.object(nav,'time',clock),patch.object(nav,'log',lambda *a,**kw:None):state,proof=nav.detect_dm_thread_state(d,target,pkg=P)
   with self.subTest(case=case):
    self.assertEqual(state,'empty_new_thread' if case=='stable' else 'unknown')
    self.assertEqual((d.currents,d.dimensions),(2*d.reads,d.reads))
    if case=='stable':
     self.assertEqual(proof['reason'],'a0_history_probe_completed')
     self.assertGreaterEqual(clock.now,0.8)
     self.assertEqual(proof['positive_empty_observations'],d.reads)
    else:self.assertEqual(d.reads,2 if case=='second_unknown' else 1)
if __name__=='__main__':unittest.main()
