from pathlib import Path
import json,copy,hashlib,itertools,xml.etree.ElementTree as ET,unittest
from unittest.mock import patch
import dm_ui_446_surface as s,dm_thread_truth as truth,instagram_navigation as nav
from test_dm_446_readers import Device,Clock,P,Selector
F=json.loads((Path(__file__).parent/'fixtures/dm_446_current_surface.json').read_text())
class CurrentSurfaceTests(unittest.TestCase):
 def test_janecandika_business_chat_sticker_shape_v1_v6(self):
  j=F['janecandika']; x=j['xml']; ctx=tuple(j['context']); target=j['target']
  self.assertEqual(s.qualify(ET.fromstring(x),s.P,target,ctx)['variant'],'thread')
  self.assertEqual(truth.classify_dm_history(x,s.P,target,surface_context=ctx,profile_identity=target)[0],'empty_new_thread')
  cases=[('wrong_recipient',x,'wrong','unknown'),('no_target',x,'','unknown')]
  r=ET.fromstring(x); ml=next(n for n in r.iter('node') if s.role(n)=='message_list'); msg=ET.SubElement(ml,'node',{'resource-id':s.P+':id/message_content','class':'android.view.View','package':s.P,'bounds':'[100,1200][800,1300]'}); ET.SubElement(msg,'node',{'resource-id':s.P+':id/direct_text_message_text_view','class':'android.widget.TextView','package':s.P,'text':'existing message','bounds':'[110,1210][790,1290]'})
  cases.append(('real_history',ET.tostring(r,encoding='unicode'),target,'existing_thread'))
  r=ET.fromstring(x); r.append(ET.Element('node',{'package':'com.android.systemui','class':'android.widget.FrameLayout','bounds':'[0,100][1080,2205]','visible-to-user':'true','clickable':'false','enabled':'true'})); cases.append(('overlay',ET.tostring(r,encoding='unicode'),target,'unknown'))
  r=ET.fromstring(x); next(n for n in r.iter('node') if s.role(n)=='row_thread_composer_edittext').set('visible-to-user','false'); cases.append(('missing_composer',ET.tostring(r,encoding='unicode'),target,'unknown'))
  for name,xml,target_i,expected in cases:
   with self.subTest(case=name): self.assertEqual(truth.classify_dm_history(xml,s.P,target_i,surface_context=ctx)[0],expected)
 def test_janecandika_reader_excludes_only_qualified_sticker_tray(self):
  j=F['janecandika']; clock=Clock(); d=Device(j['xml'],clock=clock)
  with patch.object(nav,'time',clock),patch.object(nav,'log',lambda *a,**kw:None): state,proof=nav.detect_dm_thread_state(d,j['target'],pkg=s.P,_profile_identity=dict(device=d,context=nav._dm_446_context(d),username=j['target'],transition=True))
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
  self.assertEqual(out['variant'],'thread')
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
                     'existing_thread' if kind=='known_history' else ('empty_new_thread' if kind=='unknown_intro' else 'unknown'))
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
  self.assertEqual(out['variant'],'thread')
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
    self.assertEqual(truth.classify_dm_history(xml,s.P,target,surface_context=context)[0],'existing_thread' if kind=='known_history' else ('empty_new_thread' if kind=='unknown_intro' else 'unknown'))
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

class FilledComposerSelector(Selector):
 def click(self):
  self.d.focus_clicks+=1
 def set_text(self,value):
  self.d.typed.append(value)
  root=ET.fromstring(F['personal_mutual_sticker_filled']['xml'])
  next(n for n in root.iter('node') if s.role(n)=='row_thread_composer_edittext').set('text',value)
  self.d.xmls=[ET.tostring(root,encoding='unicode')]
  self.d.last=self.d.xmls[0]
 def get_text(self):
  self.d.text_reads+=1
  if isinstance(self.d.readback,Exception):raise self.d.readback
  return super().get_text() if self.d.readback is None else self.d.readback

class FilledComposerDevice(Device):
 """Native-derived geometry, controlled transport only; never Android evidence."""
 def __init__(self,xml=None):
  super().__init__(xml or F['personal_mutual_sticker_empty']['xml'])
  self.typed=[];self.focus_clicks=0;self.text_reads=0;self.readback=None
 def __call__(self,**kw):return FilledComposerSelector(self,kw)

class FilledComposerTests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock()
  self.t=patch.object(nav,'time',self.clock);self.t.start();self.addCleanup(self.t.stop)
  self.log=patch.object(nav,'log',lambda *a,**kw:None);self.log.start();self.addCleanup(self.log.stop)
  self.no_send=patch.object(nav,'send_dm_safe',side_effect=AssertionError('Send forbidden in offline qualification'))
  self.no_send.start();self.addCleanup(self.no_send.stop)
 @staticmethod
 def text(length,multiline,emojis):
  clusters=('🙂','❤️','👋🏽','👩🏽‍💻','🇿🇦','👨‍👩‍👧‍👦','e\u0301')
  # Do not split emoji sequences to manufacture the desired code-point length.
  seed='Test '+(' '.join(clusters) if emojis else 'texte générique')
  if multiline:seed+='\n\nSuite\n'
  else:seed+=' '
  value=seed
  while len(value)+len(seed)<length:value+=seed
  return value+'A'*(length-len(value)-1)+'Z'
 def typed(self,text):
  d=FilledComposerDevice()
  ok,meta=nav.type_dm_draft_only(d,text,P,force_method='set_text')
  self.assertTrue(ok);self.assertEqual(d.typed,[text])
  self.assertEqual(meta['method'],'set_text')
  self.assertEqual(meta['actual_text_len_after_set_text'],len(text))
  self.assertFalse(meta['fastime_used']);self.assertEqual(d.focus_clicks,1)
  return d
 def test_native_filled_surface_exact_provenance_and_real_finder(self):
  f=F['personal_mutual_sticker_filled'];ctx=tuple(f['context'])
  self.assertEqual(hashlib.sha256(f['xml'].encode()).hexdigest(),f['sha256'])
  self.assertEqual(f['native_source_sha256'],'f0d0b78785e65f6e11280232fb1b528c9f0053b1acd001477f1bfeb6b4cbd757')
  result=s.qualify(ET.fromstring(f['xml']),P,f['target'],ctx)
  self.assertIsNotNone(result);self.assertFalse(result['empty_surface'])
  d=FilledComposerDevice(f['xml']);ed=nav._dm_find_focus_composer(d)
  self.assertIsNotNone(ed);self.assertEqual(ed.get_text(),f['controlled_text'])
  self.assertTrue(nav.verify_dm_draft_text(d,f['controlled_text']))
  self.assertNotEqual(truth.classify_dm_history(f['xml'],P,f['target'],surface_context=ctx)[0],'empty_new_thread')
 def test_full_set_text_and_real_verify_length_unicode_matrix(self):
  # Only text is parameterized. This is not max-height/scrolling native proof.
  for length,multiline,emojis in itertools.product((45,200,500,800,900),(False,True),(False,True)):
   with self.subTest(length=length,multiline=multiline,emojis=emojis):
    value=self.text(length,multiline,emojis)
    self.assertEqual(len(value),length);self.assertEqual(chr(10) in value,multiline);self.assertNotIn(chr(92)+'n',value)
    if emojis:self.assertTrue(all(x in value for x in ('🙂','❤️','👋🏽','👩🏽‍💻','🇿🇦','👨‍👩‍👧‍👦')))
    d=self.typed(value)
    self.assertEqual(nav.read_dm_composer_text(d),value)
    self.assertTrue(nav.verify_dm_draft_text(d,value))
    self.assertEqual(d.typed,[value])
 def test_real_verify_rejects_partial_or_changed_readback_at_every_length(self):
  for length,multiline,emojis in itertools.product((45,200,500,800,900),(False,True),(False,True)):
   expected=self.text(length,multiline,emojis);d=self.typed(expected)
   bad={'empty':'','prefix_only':expected[:length//2],'suffix_only':expected[length//2:],
        'last_missing':expected[:-1],'middle_missing':expected[:length//2]+expected[length//2+1:],
        'wrong_same_length':expected[:-1]+'Y','extra_nonspace':expected+'Q'}
   if emojis:
    bad.update(emoji_missing=expected.replace('🙂','',1),VS16_missing=expected.replace('❤️','❤',1),
               skin_modifier_missing=expected.replace('👋🏽','👋',1),ZWJ_missing=expected.replace('👩🏽‍💻','👩🏽💻',1),
               half_flag=expected.replace('🇿🇦','🇿',1),replacement=expected.replace('🙂','\ufffd',1),
               wrong_emoji=expected.replace('🙂','😢',1),renormalized=expected.replace('e\u0301','é',1))
   for kind,value in bad.items():
    with self.subTest(length=length,multiline=multiline,emojis=emojis,kind=kind):
     d.readback=value;self.assertFalse(nav.verify_dm_draft_text(d,expected))
   d.readback=RuntimeError('controlled_read_failure')
   self.assertFalse(nav.verify_dm_draft_text(d,expected));self.assertEqual(d.typed,[expected])
 def test_native_growth_has_no_fixed_height_or_length_rule(self):
  f=F['personal_mutual_sticker_filled'];empty=F['personal_mutual_sticker_empty']
  a=s.qualify(ET.fromstring(empty['xml']),P,empty['target'],tuple(empty['context']))
  b=s.qualify(ET.fromstring(f['xml']),P,f['target'],tuple(f['context']))
  self.assertIsNotNone(a);self.assertIsNotNone(b)
  self.assertGreater(b['bounds']['bottom']-b['bounds']['top'],a['bounds']['bottom']-a['bounds']['top'])
  # No invented positive long or scrolling geometry: actual native states only.
  root=ET.fromstring(f['xml'])
  next(n for n in root.iter('node') if s.role(n)=='row_thread_composer_edittext').set('scrollable','true')
  self.assertIsNone(s.qualify(root,P,f['target'],tuple(f['context'])))
 def test_exact_intro_layouts_do_not_exempt_real_or_unknown_occlusions(self):
  f=F['personal_mutual_sticker_filled'];ctx=tuple(f['context'])
  kinds=('unknown_row_over_editor','unknown_intro_child','intro_text_over_editor',
         'intro_clickable','intro_focusable','changed_intro_long_click','unknown_intro_label',
         'foreign_overlay','duplicate_editor','hidden_editor','disabled_editor','wrong_target')
  for kind in kinds:
   root=ET.fromstring(f['xml']);app=next(n for n in root if n.get('package')==P)
   ml=s.one(app,'message_list');ed=s.one(app,'row_thread_composer_edittext')
   intro=ml[0];target=f['target']
   if kind=='unknown_row_over_editor':
    ET.SubElement(ml,'node',{'package':P,'class':'android.view.View','bounds':ed.get('bounds'),'visible-to-user':'true','clickable':'false'})
   elif kind=='unknown_intro_child':
    ET.SubElement(intro,'node',{'package':P,'class':'android.view.View','bounds':ed.get('bounds'),'visible-to-user':'true','clickable':'false'})
   elif kind=='intro_text_over_editor':
    next(n for n in intro.iter('node') if n.get('text')).set('bounds',ed.get('bounds'))
   elif kind=='intro_clickable':intro.set('clickable','true')
   elif kind=='intro_focusable':intro.set('focusable','true')
   elif kind=='changed_intro_long_click':intro.set('long-clickable','false')
   elif kind=='unknown_intro_label':
    next(n for n in intro.iter('node') if n.get('text')=='You follow each other on Instagram').set('text','Unknown relationship')
   elif kind=='foreign_overlay':
    root.append(ET.Element('node',{'package':'com.example.overlay','class':'android.view.View','bounds':ed.get('bounds'),'visible-to-user':'true'}))
   elif kind=='duplicate_editor':
    s.one(app,'composer_content_container').append(copy.deepcopy(ed))
   elif kind=='hidden_editor':ed.set('visible-to-user','false')
   elif kind=='disabled_editor':ed.set('enabled','false')
   elif kind=='wrong_target':target='another_target'
   with self.subTest(kind=kind):
    if kind=='unknown_intro_label':
     self.assertIsNotNone(s.qualify(root,P,target,ctx))
     self.assertTrue(nav.verify_dm_draft_text(FilledComposerDevice(ET.tostring(root,encoding='unicode')),f['controlled_text']))
     continue
    self.assertIsNone(s.qualify(root,P,target,ctx))
    if kind!='wrong_target':
     d=FilledComposerDevice(ET.tostring(root,encoding='unicode'))
     self.assertFalse(nav.verify_dm_draft_text(d,f['controlled_text']))
     self.assertEqual(d.text_reads,0)
 def test_filled_surface_identity_stays_generic_and_bound(self):
  f=F['personal_mutual_sticker_filled']
  for name,handle in [('Generic Person','different_target.9'),('Another Person','other_target_2')]:
   x=f['xml'].replace('Surface Fixture User',name).replace(f['target'],handle)
   out=s.qualify(ET.fromstring(x),P,handle,tuple(f['context']))
   self.assertEqual(out['identity'],(name,handle))
   self.assertIsNone(s.qualify(ET.fromstring(x),P,'wrong_target',tuple(f['context'])))
 def test_live_selector_geometry_and_foreground_refusals_preserved(self):
  f=F['personal_mutual_sticker_filled'];root=ET.fromstring(f['xml'])
  d=FilledComposerDevice(f['xml'])
  next(n for n in root.iter('node') if s.role(n)=='row_thread_composer_edittext').set('bounds','[171,1798][891,2127]')
  d.live=ET.tostring(root,encoding='unicode')
  self.assertFalse(nav.verify_dm_draft_text(d,f['controlled_text']));self.assertEqual(d.text_reads,0)
  d=FilledComposerDevice(f['xml']);d.foregrounds=[dict(package='com.example.other',activity='Other')]
  self.assertFalse(nav.verify_dm_draft_text(d,f['controlled_text']));self.assertEqual(d.text_reads,0)

class FilledMirroredHeaderTests(unittest.TestCase):
 # Reuse only the existing transport setup; do not inherit/replay its suites.
 setUp=FilledComposerTests.setUp
 @staticmethod
 def filled():return F['personal_mutual_sticker_filled_mirrored_header']
 def test_native_derived_fixture_and_actual_finder(self):
  f=self.filled();ctx=tuple(f['context'])
  self.assertEqual(f['native_source_sha256'],'b171d2e938631bdaf0a244b25b6492828ff77d016e08022630858cda256847d6')
  self.assertEqual(hashlib.sha256(f['xml'].encode()).hexdigest(),f['sha256'])
  self.assertEqual(len(f['controlled_text']),45)
  root=ET.fromstring(f['xml']);title=s.one(root,'header_title')
  self.assertEqual(title.get('content-desc'),title.get('text'))
  self.assertEqual(s.one(root,'row_thread_composer_send_button_container').get('long-clickable'),'false')
  result=s.qualify(root,P,f['target'],ctx)
  self.assertEqual(result['variant'],'thread')
  self.assertEqual(result['identity'],('Surface Fixture User',f['target']))
  self.assertFalse(result['empty_surface'])
  d=FilledComposerDevice(f['xml']);editor=nav._dm_find_focus_composer(d)
  self.assertIsNotNone(editor);self.assertEqual(editor.count,1)
  self.assertEqual(editor.get_text(),f['controlled_text'])
  self.assertTrue(nav.verify_dm_draft_text(d,f['controlled_text']))
 def test_empty_and_filled_keep_both_header_variants(self):
  from dm_ui_446_evidence import composer_evidence
  cases=(('personal_mutual_sticker_empty',True),
         ('personal_mutual_sticker_mirrored_header',True),
         ('personal_mutual_sticker_filled',False),
         ('personal_mutual_sticker_filled_mirrored_header',False))
  for key,empty in cases:
   f=F[key];ctx=tuple(f['context'])
   with self.subTest(surface=key):
    out=s.qualify(ET.fromstring(f['xml']),P,f['target'],ctx)
    self.assertIsNotNone(out);self.assertEqual(out['empty_surface'],empty)
    self.assertTrue(composer_evidence(f['xml'],P,display_size=ctx[2:],surface_context=ctx)['visible'])
    state=truth.classify_dm_history(f['xml'],P,f['target'],surface_context=ctx)[0]
    if empty:self.assertEqual(state,'empty_new_thread')
    else:
     self.assertNotEqual(state,'empty_new_thread')
     self.assertTrue(nav.verify_dm_draft_text(FilledComposerDevice(f['xml']),f['controlled_text']))
 def test_description_and_recipient_remain_exact(self):
  from dm_ui_446_evidence import composer_evidence
  for key in ('personal_mutual_sticker_mirrored_header','personal_mutual_sticker_filled_mirrored_header'):
   f=F[key];ctx=tuple(f['context'])
   for description in ('Different display name','Surface Fixture User plus'):
    root=ET.fromstring(f['xml']);s.one(root,'header_title').set('content-desc',description)
    with self.subTest(surface=key,description=description):
     self.assertIsNone(s.qualify(root,P,f['target'],ctx))
     self.assertFalse(composer_evidence(ET.tostring(root,encoding='unicode'),P,display_size=ctx[2:],surface_context=ctx)['visible'])
   self.assertIsNone(s.qualify(ET.fromstring(f['xml']),P,'wrong_recipient',ctx))
   self.assertEqual(truth.classify_dm_history(f['xml'],P,'',surface_context=ctx)[0],'unknown')
 def test_missing_ambiguous_and_occluded_editor_still_refused(self):
  f=self.filled();ctx=tuple(f['context'])
  for kind in ('missing','duplicate','foreign_overlay','unknown_row_over_editor','intro_text_over_editor','hidden','disabled','send_long_clickable'):
   root=ET.fromstring(f['xml']);editor=s.one(root,'row_thread_composer_edittext')
   if kind=='missing':
    parent=next(n for n in root.iter('node') if editor in list(n));parent.remove(editor)
   elif kind=='duplicate':s.one(root,'composer_content_container').append(copy.deepcopy(editor))
   elif kind=='foreign_overlay':
    root.append(ET.Element('node',{'package':'com.example.overlay','class':'android.view.View','bounds':editor.get('bounds'),'visible-to-user':'true'}))
   elif kind=='unknown_row_over_editor':
    ET.SubElement(s.one(root,'message_list'),'node',{'package':P,'class':'android.view.View','bounds':editor.get('bounds'),'visible-to-user':'true','clickable':'false'})
   elif kind=='intro_text_over_editor':
    next(n for n in s.one(root,'message_list').iter('node') if n.get('text')).set('bounds',editor.get('bounds'))
   elif kind=='hidden':editor.set('visible-to-user','false')
   elif kind=='disabled':editor.set('enabled','false')
   else:s.one(root,'row_thread_composer_send_button_container').set('long-clickable','true')
   with self.subTest(case=kind):
    self.assertIsNone(s.qualify(root,P,f['target'],ctx))
    d=FilledComposerDevice(ET.tostring(root,encoding='unicode'))
    self.assertFalse(nav.verify_dm_draft_text(d,f['controlled_text']))
    self.assertEqual(d.text_reads,0)
 def test_same_45_set_text_read_verify_sequence_without_send(self):
  f=self.filled();expected=f['controlled_text']
  # The existing selector transport now returns the observed filled variant.
  with patch.dict(F,{'personal_mutual_sticker_filled':f}):
   d=FilledComposerDevice(F['personal_mutual_sticker_mirrored_header']['xml'])
   ok,meta=nav.type_dm_draft_only(d,expected,P,force_method='set_text')
   self.assertTrue(ok);self.assertEqual(meta['method'],'set_text')
   self.assertFalse(meta['fastime_used']);self.assertEqual(meta['actual_text_len_after_set_text'],45)
   self.assertEqual(d.typed,[expected]);self.assertEqual(d.focus_clicks,1)
   self.assertEqual(nav.read_dm_composer_text(d),expected)
   self.assertTrue(nav.verify_dm_draft_text(d,expected))
   self.assertEqual(d.typed,[expected])
 def test_full_text_equality_stays_required_after_admission(self):
  f=self.filled();expected=f['controlled_text'];d=FilledComposerDevice(f['xml'])
  self.assertTrue(nav.verify_dm_draft_text(d,expected))
  for actual in ('',expected[:20],expected[:-1],expected[:-1]+'X',expected+'Q'):
   with self.subTest(readback_length=len(actual)):
    d.readback=actual;self.assertFalse(nav.verify_dm_draft_text(d,expected))
  d.readback=RuntimeError('controlled_read_failure')
  self.assertFalse(nav.verify_dm_draft_text(d,expected))
 def test_dynamic_names_and_handles_remain_bound(self):
  for key in ('personal_mutual_sticker_mirrored_header','personal_mutual_sticker_filled_mirrored_header'):
   f=F[key];ctx=tuple(f['context'])
   for name,handle in (('Another Person','other_target.3'),('Élodie Fixture','fixture_target_4')):
    xml=f['xml'].replace('Surface Fixture User',name).replace(f['target'],handle)
    with self.subTest(surface=key,name=name,handle=handle):
     result=s.qualify(ET.fromstring(xml),P,handle,ctx)
     self.assertEqual(result['identity'],(name,handle))
     self.assertIsNone(s.qualify(ET.fromstring(xml),P,'wrong_recipient',ctx))

if __name__=='__main__':unittest.main()

class StructuralOptionCTests(unittest.TestCase):
 def corpus(self):return F['option_c_native_corpus']
 def test_canonical_family_and_native_baseline_eight(self):
  from dm_ui_446_evidence import composer_evidence
  self.assertEqual(set(s.SCHEMAS),{'thread'})
  for f in self.corpus():
   with self.subTest(case=f['label']):
    self.assertEqual(hashlib.sha256(f['xml'].encode()).hexdigest(),f['sha256'])
    ctx=tuple(f['context']);root=ET.fromstring(f['xml'])
    q=s.qualify(root,P,f['target'],ctx)
    state=truth.classify_dm_history(f['xml'],P,f['target'],surface_context=ctx)[0]
    if f['label']=='bradley_empty':
     self.assertIsNotNone(q);self.assertIsNotNone(q['intro'])
     self.assertIsNone(q['identity']);self.assertEqual(state,'unknown')
     self.assertTrue(composer_evidence(f['xml'],P,display_size=ctx[2:],surface_context=ctx)['visible'])
     # Explicit synthetic projection, never a claim of native profile evidence.
     self.assertEqual(truth.classify_dm_history(f['xml'],P,f['target'],surface_context=ctx,profile_identity=f['target'])[0],'empty_new_thread')
    elif f['label'] in ('personal_empty','oct08_empty'):
     self.assertIsNotNone(q);self.assertEqual(state,'empty_new_thread')
    else:
     self.assertNotEqual(state,'empty_new_thread')
     if f['label']=='personal_filled':self.assertIsNotNone(q)
 def test_cosmetic_leaves_language_count_order_never_choose_identity(self):
  for f in [self.corpus()[0],self.corpus()[1],F['personal_mutual_sticker_filled']]:
   ctx=tuple(f['context']);base=ET.fromstring(f['xml'])
   for words in (['Une présentation variable','199 abonnés','Compte récent'],
                 ['紹介','説明','別の順序','0','❤️'],['Un seul descriptif']):
    root=copy.deepcopy(base);body=s.one(root,'message_list')[0][0]
    leaf=copy.deepcopy(body[1]);body[:]=[body[0]]
    for text in words:
     n=copy.deepcopy(leaf);n.set('text',text);body.append(n)
    # Synthetic text/count changes within retained geometry, not new UI evidence.
    out=s.qualify(root,P,f['target'],ctx)
    with self.subTest(case=f.get('label','filled'),words=len(words)):
     self.assertIsNotNone(out);self.assertIsNotNone(out['intro'])
     xml=ET.tostring(root,encoding='unicode')
     state=truth.classify_dm_history(xml,P,f['target'],surface_context=ctx,profile_identity=f['target'])[0]
     self.assertEqual(state,'empty_new_thread' if out['empty_surface'] else 'unknown')
 def test_card_delimitation_rejects_accessible_or_unexpected_descendants(self):
  f=self.corpus()[0];ctx=tuple(f['context'])
  for kind in ('clickable_text','long_clickable_text','focusable_text','content_description',
               'nested_reply','media','unknown_view','hidden_leaf','foreign_leaf','second_card'):
   root=ET.fromstring(f['xml']);ml=s.one(root,'message_list');body=ml[0][0];leaf=body[1]
   if kind in ('clickable_text','long_clickable_text','focusable_text'):
    leaf.set({'clickable_text':'clickable','long_clickable_text':'long-clickable','focusable_text':'focusable'}[kind],'true')
   elif kind=='content_description':leaf.set('content-desc','Reply from another account')
   elif kind=='nested_reply':leaf.append(copy.deepcopy(leaf))
   elif kind=='media':leaf.set('class','android.widget.ImageView')
   elif kind=='unknown_view':leaf.set('class','android.view.View')
   elif kind=='hidden_leaf':leaf.set('visible-to-user','false')
   elif kind=='foreign_leaf':leaf.set('package','other.package')
   else:ml.append(copy.deepcopy(ml[0]))
   xml=ET.tostring(root,encoding='unicode')
   with self.subTest(case=kind):
    self.assertNotEqual(truth.classify_dm_history(xml,P,f['target'],surface_context=ctx,profile_identity=f['target'])[0],'empty_new_thread')
 def test_business_public_name_and_header_conflicts(self):
  f=self.corpus()[0];ctx=tuple(f['context'])
  root=ET.fromstring(f['xml']);s.one(root,'header_title').set('text','Shared Public Name');s.one(root,'header_title').set('content-desc','Shared Public Name')
  x=ET.tostring(root,encoding='unicode')
  self.assertIsNotNone(s.qualify(root,P,None,ctx))
  for proof in (None,'','other_recipient'):
   self.assertEqual(truth.classify_dm_history(x,P,f['target'],surface_context=ctx,profile_identity=proof)[0],'unknown')
  self.assertEqual(truth.classify_dm_history(x,P,f['target'],surface_context=ctx,profile_identity=f['target'])[0],'empty_new_thread')
  sub=s.one(root,'header_subtitle');sub.set('content-desc','contradictory_username')
  self.assertEqual(truth.classify_dm_history(ET.tostring(root,encoding='unicode'),P,f['target'],surface_context=ctx,profile_identity=f['target'])[0],'unknown')
 def test_23_case_corpus_expectations_are_candidate_results(self):
  from test_dm_446_readers import DATA
  from dm_ui_446_evidence import composer_evidence
  cases=[(f['label'],f['xml'],f['target'],f['baseline_history']) for f in self.corpus()]
  names=('dm_truth/B_30_existing_thread','dm_truth_a1/accessible_reply_in_intro',
         'dm_truth_a3/message_list_unknown_semantics','dm_ui_phase_b1/display_name_collision',
         'dm_ui_phase_b1/contradictory_header_channels','dm_truth_a1/untitled_dialog',
         'dm_truth_a1/hidden_ancestor','dm_truth_a2/H2_nested_overlay_message_composer_bar',
         'dm_ui_phase_b1/anonymous_overlay_inside_history','dm_truth_a1/unexpected_node')
  for name in names:
   cases.append((name,DATA[name]['xml'],'reginanubway' if name.endswith('B_30_existing_thread') else 'vincentfavrephotographe',
                 'existing_thread' if name.endswith('B_30_existing_thread') else 'unknown'))
  for kind in ('wrong_recipient','missing_editor','duplicate_editor','editor_occluded','false_thread_search_header'):
   root=ET.fromstring(self.corpus()[1]['xml']);ed=s.one(root,'row_thread_composer_edittext');target='fixture_target'
   parent=next(n for n in root.iter() if ed in list(n))
   if kind=='wrong_recipient':target='wrong_recipient'
   elif kind=='missing_editor':parent.remove(ed)
   elif kind=='duplicate_editor':parent.append(copy.deepcopy(ed))
   elif kind=='editor_occluded':s.one(root,'message_list').append(copy.deepcopy(ed))
   else:
    s.one(root,'header_title').set('text','Search');s.one(root,'header_title').set('content-desc','Search')
   cases.append((kind,ET.tostring(root,encoding='unicode'),target,'unknown'))
  self.assertEqual(len(cases),23)
  for name,x,target,expected in cases:
   with self.subTest(case=name):
    self.assertEqual(truth.classify_dm_history(x,P,target,surface_context=tuple(F['context']))[0],expected)
 def test_empty_and_filled_offline_exact_45_use_unchanged_methods(self):
  d=FilledComposerDevice()
  text='Hello diagnostic000 !\n\nTest Message \n\nRegards'
  self.assertEqual(len(text),45)
  with patch.object(nav,'log',lambda *a,**kw:None),patch.object(nav,'send_dm_safe',side_effect=AssertionError('no Send')):
   ok,meta=nav.type_dm_draft_only(d,text,P,force_method='set_text')
   self.assertTrue(ok);self.assertEqual(nav.read_dm_composer_text(d),text)
   self.assertTrue(nav.verify_dm_draft_text(d,text));self.assertEqual(d.typed,[text])


class CompactOptionCGenericTests(unittest.TestCase):
 def cases(self):return F['compact_option_c_native_structures']
 def xml(self,root):return ET.tostring(root,encoding='unicode')
 def state(self,root,target,ctx):
  return truth.classify_dm_history(self.xml(root),P,target,surface_context=ctx)[0]
 def test_both_exact_native_and_controlled_equivalents(self):
  from dm_ui_446_evidence import composer_evidence
  import subprocess
  parent=json.loads(subprocess.check_output(['git','-C',str(Path(s.__file__).parent),'show',
      '347ebd11fd2503fea2aa2769ef1265a780c770a9:dm_ui_446_surface_shapes.json'],text=True))
  for f in self.cases():
   raw=Path(f['native_path']).read_bytes()
   self.assertEqual(hashlib.sha256(raw).hexdigest(),f['native_sha256'])
   self.assertEqual(hashlib.sha256(f['xml'].encode()).hexdigest(),f['sha256'])
   for label,x in (('exact_native',raw.decode()),('controlled',f['xml'])):
    root=ET.fromstring(x);ctx=tuple(f['context']);target=s.one(root,'header_subtitle').get('content-desc')
    with self.subTest(case=f['label'],input=label):
     with patch.object(s,'SCHEMAS',parent):self.assertIsNone(s.qualify(root,P,target,ctx))
     q=s.qualify(root,P,target,ctx)
     self.assertIsNotNone(q);self.assertEqual(q['identity'][1],target)
     self.assertIsNotNone(q['intro']);self.assertTrue(q['empty_surface'])
     self.assertTrue(composer_evidence(x,P,display_size=ctx[2:],surface_context=ctx)['visible'])
     self.assertEqual(self.state(root,target,ctx),'empty_new_thread')
 def test_disabled_compact_title_and_long_click_combinations(self):
  for f in self.cases():
   for mirrored,long_click in itertools.product((False,True),('false','true')):
    root=ET.fromstring(f['xml']);ctx=tuple(f['context']);title=s.one(root,'header_title')
    title.set('content-desc',title.get('text') if mirrored else '')
    send=s.one(root,'row_thread_composer_send_button_container');send.set('long-clickable',long_click)
    with self.subTest(case=f['label'],mirrored=mirrored,long_click=long_click):
     self.assertIsNotNone(s.qualify(root,P,f['target'],ctx))
     self.assertEqual(self.state(root,f['target'],ctx),'empty_new_thread')
     send.set('enabled','true')
     # Only the previously accepted empty-description/long=true shape may admit enabled Send.
     if mirrored or long_click=='false':self.assertIsNone(s.qualify(root,P,f['target'],ctx))
     self.assertNotEqual(self.state(root,f['target'],ctx),'empty_new_thread')
 def test_variable_intro_language_count_order_remain_cosmetic(self):
  for f in self.cases():
   ctx=tuple(f['context'])
   for words in (['Une ligne'],['紹介','Compte récent','50 abonnés','共通'],['Private promotion','0 posts','Suivi mutuel']):
    for order in (words,list(reversed(words))):
     root=ET.fromstring(f['xml']);body=s.one(root,'message_list')[0][0]
     sample=copy.deepcopy(body[1]);body[:]=[body[0]]
     for text in order:
      n=copy.deepcopy(sample);n.set('text',text);body.append(n)
     with self.subTest(case=f['label'],words=order):
      q=s.qualify(root,P,f['target'],ctx)
      self.assertIsNotNone(q);self.assertIsNotNone(q['intro'])
      self.assertEqual(q['identity'][1],f['target'])
      self.assertEqual(self.state(root,f['target'],ctx),'empty_new_thread')
 def test_exact_handle_not_public_name_and_profile_continuity_stays_required(self):
  for f in self.cases():
   ctx=tuple(f['context']);root=ET.fromstring(f['xml']);title=s.one(root,'header_title')
   title.set('text','Shared Public Name');title.set('content-desc','Shared Public Name')
   for wrong in ('different_target','Shared Public Name',''):
    with self.subTest(case=f['label'],wrong=wrong):
     self.assertEqual(self.state(root,wrong,ctx),'unknown')
   self.assertEqual(self.state(root,f['target'],ctx),'empty_new_thread')
   # A generic subtitle is never an exact username; the existing continuity gate remains mandatory.
   s.one(root,'header_subtitle').set('content-desc','Business chat')
   q=s.qualify(root,P,None,ctx);self.assertIsNotNone(q);self.assertIsNone(q['identity'])
   self.assertEqual(self.state(root,f['target'],ctx),'unknown')
   clock=Clock();d=Device(self.xml(root),clock=clock)
   with patch.object(nav,'time',clock),patch.object(nav,'log',lambda *a,**kw:None):
    state,_=nav.detect_dm_thread_state(d,f['target'],pkg=P,
      _profile_identity=dict(device=d,context=nav._dm_446_context(d),username=f['target'],transition=False))
   self.assertEqual(state,'unknown')
 def test_real_bubble_in_or_outside_card_never_empty(self):
  for f in self.cases():
   ctx=tuple(f['context'])
   for location in ('outside','inside'):
    root=ET.fromstring(f['xml']);ml=s.one(root,'message_list')
    parent=ml if location=='outside' else ml[0][0]
    msg=ET.SubElement(parent,'node',{'resource-id':P+':id/message_content','class':'android.view.View','package':P,'bounds':'[100,1200][800,1300]'})
    ET.SubElement(msg,'node',{'resource-id':P+':id/direct_text_message_text_view','class':'android.widget.TextView','package':P,'text':'Actual existing message','bounds':'[110,1210][790,1290]'})
    with self.subTest(case=f['label'],location=location):
     self.assertIsNotNone(s.qualify(root,P,f['target'],ctx))
     self.assertEqual(self.state(root,f['target'],ctx),'existing_thread')
 def test_intro_boundary_accessibility_and_unknown_rows_never_empty(self):
  for f in self.cases():
   ctx=tuple(f['context'])
   for kind in ('clickable','long-clickable','focusable','description','child','unknown_class','hidden','foreign','second_card','outside_text'):
    root=ET.fromstring(f['xml']);ml=s.one(root,'message_list');leaf=ml[0][0][1]
    if kind in ('clickable','long-clickable','focusable'):leaf.set(kind,'true')
    elif kind=='description':leaf.set('content-desc','Unexpected accessible descendant')
    elif kind=='child':leaf.append(copy.deepcopy(leaf))
    elif kind=='unknown_class':leaf.set('class','android.view.View')
    elif kind=='hidden':leaf.set('visible-to-user','false')
    elif kind=='foreign':leaf.set('package','foreign')
    elif kind=='second_card':ml.append(copy.deepcopy(ml[0]))
    else:ml.append(copy.deepcopy(leaf))
    with self.subTest(case=f['label'],kind=kind):
     self.assertNotEqual(self.state(root,f['target'],ctx),'empty_new_thread')
 def test_composer_identity_geometry_and_shape_fail_closed(self):
  from dm_ui_446_evidence import composer_evidence
  for f in self.cases():
   ctx=tuple(f['context'])
   for kind in ('description_conflict','missing','duplicate','hidden','disabled','occlusion','foreign_window','wrong_version','false_thread','unknown_structure','send_focusable'):
    root=ET.fromstring(f['xml']);ed=s.one(root,'row_thread_composer_edittext');context=ctx
    parent=next(n for n in root.iter() if ed in list(n))
    if kind=='description_conflict':s.one(root,'header_title').set('content-desc','Wrong public title')
    elif kind=='missing':parent.remove(ed)
    elif kind=='duplicate':parent.append(copy.deepcopy(ed))
    elif kind=='hidden':ed.set('visible-to-user','false')
    elif kind=='disabled':ed.set('enabled','false')
    elif kind=='occlusion':
     ET.SubElement(s.one(root,'message_list'),'node',{'package':P,'class':'android.view.View','bounds':ed.get('bounds'),'visible-to-user':'true','clickable':'false'})
    elif kind=='foreign_window':root.append(ET.Element('node',{'package':'overlay','class':'android.view.View','bounds':'[0,100][1080,2205]'}))
    elif kind=='wrong_version':context=('372.0.0.48.60',377611059,1080,2340)
    elif kind=='false_thread':
     s.one(root,'header_title').set('text','Search');s.one(root,'header_title').set('content-desc','Search')
    elif kind=='unknown_structure':parent.append(copy.deepcopy(s.one(root,'header_title')))
    else:s.one(root,'row_thread_composer_send_button_container').set('focusable','false')
    with self.subTest(case=f['label'],kind=kind):
     self.assertIsNone(s.qualify(root,P,f['target'],context))
     self.assertFalse(composer_evidence(self.xml(root),P,display_size=ctx[2:],surface_context=context)['visible'])
     self.assertEqual(self.state(root,f['target'],context),'unknown')
 def test_nonempty_draft_is_not_empty_history_and_a0_probe_not_bypassed(self):
  for f in self.cases():
   root=ET.fromstring(f['xml']);ctx=tuple(f['context'])
   s.one(root,'row_thread_composer_edittext').set('text','Existing unsent draft')
   q=s.qualify(root,P,f['target'],ctx);self.assertIsNotNone(q);self.assertFalse(q['empty_surface'])
   self.assertEqual(self.state(root,f['target'],ctx),'unknown')
   for label,xmls in (('stable',f['xml']),('second_unknown',[f['xml'],'<hierarchy/>'])):
    clock=Clock();d=Device(xmls,clock=clock)
    with patch.object(nav,'time',clock),patch.object(nav,'log',lambda *a,**kw:None):
     state,proof=nav.detect_dm_thread_state(d,f['target'],pkg=P)
    with self.subTest(case=f['label'],probe=label):
     self.assertEqual(state,'empty_new_thread' if label=='stable' else 'unknown')
     self.assertGreaterEqual(d.reads,2)
     if label=='stable':
      self.assertEqual(proof['reason'],'a0_history_probe_completed')
      self.assertGreaterEqual(clock.now,0.8)


