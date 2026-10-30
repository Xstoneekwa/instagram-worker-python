"""Finite observed 446 surface grammar; no history decision or UI action."""
from pathlib import Path
import json,re,xml.etree.ElementTree as ET
SCHEMAS=json.loads(Path(__file__).with_name('dm_ui_446_surface_shapes.json').read_text())
P='com.instagram.android';VERSION=('446.0.0.49.77',385211304)
def role(n):return n.get('resource-id','').split(':id/')[-1]
def rect(n):
 m=re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',n.get('bounds',''))
 if not m:return None
 b=tuple(map(int,m.groups()));return b if b[0]<b[2] and b[1]<b[3] else None
def fits(a,b):return bool(a and b and b[0]<=a[0]<a[2]<=b[2] and b[1]<=a[1]<a[3]<=b[3])
def one(root,r):
 ns=[n for n in root.iter('node') if n.get('resource-id')==P+':id/'+r];return ns[0] if len(ns)==1 else None
def match(n,s,values,outer=None,opaque_list=False,allow_no_tray=False):
 tag,attrs,children=s;b=rect(n)
 if n.tag!=tag or not b or (outer and not fits(b,outer)):return False
 actual={k:v for k,v in n.attrib.items() if k not in ('index','drawing-order','bounds')}
 if set(actual)!=set(attrs):return False
 for k,v in attrs.items():
  a=actual[k]
  if v=='$bool':
   if a not in ('true','false'):return False
  elif v=='$draft':pass # widget identity only; never empty-draft or Send authority
  elif v=='$clock':
   if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',a):return False
  elif v=='$stats':
   if not re.fullmatch(r'[\d.,KMkm]+ followers · [\d.,KMkm]+ posts',a):return False
  elif v.startswith('$'):
   if a!=values.get(v[1:]):return False
  elif a!=v:return False
 if role(n)=='clock' and n.get('text')!=n.get('content-desc'):return False
 if opaque_list and role(n)=='message_list':return True
 if (allow_no_tray and role(n)=='message_composer_reply_bar_container'
     and len(children)==2 and len(n)==1
     and role(n[0])=='row_thread_composer_container'):
  return match(n[0],children[1],values,b,opaque_list,allow_no_tray)
 return len(n)==len(children) and all(match(c,t,values,b,opaque_list,allow_no_tray) for c,t in zip(n,children))
def qualify(root,package,target,context):
 if type(context) is not tuple or len(context)!=4 or context[:2]!=VERSION or any(type(v)is not int for v in context[1:]) or context[2:]!=(1080,2340) or package!=P:return None
 if root.tag!='hierarchy' or root.attrib!={'rotation':'0'} or len(root) not in (3,4) or (root.text or '').strip():return None
 apps=[n for n in root if n.get('package')==P]
 if len(apps)!=1 or rect(apps[0])!=(0,0,1080,2340):return None
 app=apps[0];title=one(app,'header_title');sub=one(app,'header_subtitle');ml=one(app,'message_list');ed=one(app,'row_thread_composer_edittext');view=one(app,'direct_thread_content_below_action_bar');row=one(app,'row_thread_composer_container')
 if any(n is None for n in (title,sub,ml,ed,view,row)):return None
 handle=sub.get('content-desc','');name=title.get('text','')
 generic_business = handle == 'Business chat'
 if generic_business:
  if target is not None and name.strip().casefold() != target.strip().casefold():return None
 elif not re.fullmatch(r'[A-Za-z0-9_.]+',handle):
  return None
 if not name.strip() or name.strip().lower() in ('search','meta ai','search or ask meta ai') or (target is not None and not generic_business and target!=handle):return None
 values={'name':name,'handle':handle}
 # Qualified status/nav windows may be in either observed order; no other root.
 systems=[n for n in root if n is not app]
 if len(root)==4:
  extras=[n for n in systems if n.get('package')!='com.android.systemui']
  if len(extras)!=1 or len(systems)!=3:return None
  keyboard=extras[0];kb=rect(keyboard)
  if (keyboard.get('package')!='com.android.adbkeyboard'
      or keyboard.get('class')!='android.widget.FrameLayout'
      or kb!=(0,2150,1080,2340)
      or not rect(ed) or not rect(view)
      or (kb[0]<rect(ed)[2] and rect(ed)[0]<kb[2] and kb[1]<rect(ed)[3] and rect(ed)[1]<kb[3])
      or (kb[0]<rect(view)[2] and rect(view)[0]<kb[2] and kb[1]<rect(view)[3] and rect(view)[1]<kb[3])):return None
  filtered=ET.Element('hierarchy',root.attrib);filtered.extend(n for n in root if n is not keyboard)
  root=filtered;systems=[n for n in root if n is not app]
 # SystemUI is chrome, not DM business truth.  Only the two structural
 # siblings in the reserved status/navigation bands are admissible; their
 # volatile children (clock, notifications, radio/battery text and order)
 # are deliberately non-authoritative. Any extra root, foreign package,
 # geometry outside a band, or viewport overlay remains fail-closed.
 if len(systems)!=2:return None
 bands={rect(n) for n in systems}
 if bands!={(0,0,1080,100),(0,2205,1080,2340)}:return None
 if any(n.get('package')!='com.android.systemui' or n.get('visible-to-user')=='false'
        or n.get('clickable')!='false' or n.get('enabled')!='true'
        or n.get('class')!='android.widget.FrameLayout'
        or (rect(n)==(0,0,1080,100) and n.get('resource-id')!='')
        or (rect(n)==(0,2205,1080,2340) and n.get('resource-id')!='com.android.systemui:id/navigation_bar_frame')
        or not rect(n) for n in systems):return None
 eb=rect(ed);vb=rect(view);rb=rect(row)
 navigation_top=min(rect(node)[1] for node in systems if rect(node)[1]>100)
 if not fits(eb,rb) or not fits(rb,vb) or vb[1]<100 or vb[3]>navigation_top or rb[3]!=vb[3]:return None
 # Header/action-bar and sticker education are siblings, never composer hosts.
 # Parent containment alone cannot prove they remain outside the editor.
 header=one(app,'direct_thread_header');bar=one(app,'direct_thread_business_chat_action_bar');banner=one(app,'new_friend_bump_sticker_banner_container')
 if header is None or not rect(header) or rect(header)[3]>vb[1]:return None
 if bar is not None and (not rect(bar) or rect(bar)[3]>vb[1]):return None
 if banner is not None and (not rect(banner) or rect(banner)[3]>rb[1]):return None
 # Existing F4 rule: any visible list descendant crossing editor blocks composer.
 for n in ml.iter('node'):
  if n is ml:continue
  b=rect(n)
  if n.get('package')!=P or not b or 'dialog' in n.get('class','').lower():return None
  if n.get('visible-to-user')!='false' and b[0]<eb[2] and eb[0]<b[2] and b[1]<eb[3] and eb[1]<b[3]:return None
 matches=[k for k,s in SCHEMAS.items() if any(match(app,t,values,opaque_list=True,allow_no_tray=True) for t in [s['app']]+s.get('app_alternates',[]))]
 if len(matches)!=1:return None
 variant=matches[0];intro=[n for n in ml if match(n,SCHEMAS[variant]['intro'],values,rect(ml))]
 identity_handle = target if generic_business else handle
 send_button=one(app,'row_thread_composer_send_button_container')
 empty_surface = ed.get('text')==ed.get('hint') and ed.get('hint') in SCHEMAS[variant].get('empty_hints',[SCHEMAS[variant]['hint']]) and ((send_button is None and variant in ('business_chat_sticker_empty','personal_mutual_sticker_empty')) or (send_button is not None and send_button.get('enabled')=='false'))
 return dict(variant=variant,identity=(name,identity_handle),intro=intro[0] if len(intro)==1 else None,empty_surface=empty_surface,bounds=dict(zip(('left','top','right','bottom'),eb)),app_viewport=vb,window_bounds=rect(app),focused=ed.get('focused')=='true')
