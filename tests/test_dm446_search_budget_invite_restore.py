"""Real reader/restore paths, native deidentified geometry, no device/provider IO."""
from __future__ import annotations
import ast
import collections
import copy
import hashlib
import json
import subprocess
import unittest
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import instagram_navigation as nav
import dm_sender_engine as engine
import unfollow_hybrid_strategy as search
import test_dm446_worker1083_residual_performance as harness
import test_outreach_search_absence_v1 as absence
from test_dm_446_readers import Device, mutate

ROOT = Path(__file__).resolve().parents[1]
PARENT = '76f1048ab413657edc5d66191a49b26f9736a336'
F = json.loads((ROOT/'tests/fixtures/dm446_search_budget_invite_restore_v1.json').read_text())
P = F['package']

class Clock:
    def __init__(self): self.now=10.; self.sleeps=[]
    def monotonic(self): return self.now
    def perf_counter(self): return self.now
    def time(self): return 1794000000.+self.now
    def sleep(self,t): self.sleeps.append(t); self.now+=t

def parent_nodes(file, names, namespace):
    source=subprocess.check_output(['git','show',PARENT+':'+file],cwd=ROOT,text=True)
    nodes=[n for n in ast.parse(source).body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<signed-parent76>','exec'),namespace)
    return namespace

class SearchDevice(absence.Device):
    def __init__(self,clock,frames=None,*,cost=.79,activity='com.instagram.mainactivity.InstagramMainActivity'):
        super().__init__(frames,activity=activity);self.clock=clock;self.cost=cost;self.calls=[]
    def dump_hierarchy(self,**kw):
        self.calls.append('dump_hierarchy');self.clock.now+=self.cost*.4
        return super().dump_hierarchy(**kw)
    def app_current(self):
        self.calls.append('app_current');self.clock.now+=self.cost*.6
        return super().app_current()

class SearchBudgetTests(unittest.TestCase):
    def observe(self,frames=None,*,cost=.79,activity='com.instagram.mainactivity.InstagramMainActivity',capture=True,qualified=False,wrap=None):
        clock=Clock();d=SearchDevice(clock,frames,cost=cost,activity=activity)
        if qualified:
            import instagram_ui_446_compat as compat
            d.serial='offline-search-serial'
            d._instagram_search_observed_version=(id(d),d.serial,P,*compat.VERSION)
            compat.retain_context(d,serial=d.serial,package=P,version_name=compat.VERSION[0],version_code=compat.VERSION[1],unsafe=False)
        subject=wrap(d) if wrap else d
        out={};buffer=absence.recorder_buffer()
        with ExitStack() as stack:
            for m in (nav,search):stack.enter_context(patch.object(m,'time',clock));stack.enter_context(patch.object(m,'log'))
            stack.enter_context(patch.object(nav._dm_retention,'_composer_buffer',buffer))
            retype=stack.enter_context(patch.object(nav,'type_search',return_value=True))
            if not capture:stack.enter_context(patch.object(nav._dm_retention,'capture_xml'))
            exact=nav._observe_outreach_missing_search(subject,absence.QUERY,outcome=out)
        return exact,out,d,clock,buffer,retype

    def test_qualified_MainTab_reuses_local_authority_with_same_eight_IO_calls(self):
        for activity in ('.activity.MainTabActivity','com.instagram.android.activity.MainTabActivity'):
            with self.subTest(activity=activity):
                exact,out,d,clock,buf,retype=self.observe(activity=activity,qualified=True)
                self.assertFalse(exact);self.assertEqual(out['state'],'confirmed_absent')
                self.assertEqual(d.calls,['dump_hierarchy','app_current']*4)
                self.assertEqual([len(r) for r in out['proof'].rounds],[2,2])
                self.assertLessEqual(clock.now-10,7.);retype.assert_called_once()
                self.assertEqual([x['xml'] for x in buf['native_xml']],[absence.xml()]*2)

    def test_MainTab_native_shaped_suggestion_remains_unknown_under_real_elapsed_budget(self):
        exact,out,d,clock,buf,retype=self.observe([absence.suggestion_xml()],activity='.activity.MainTabActivity',qualified=True)
        self.assertFalse(exact);self.assertNotIn('proof',out);self.assertNotEqual(out['state'],'confirmed_absent')
        self.assertNotEqual(out['reason'],'search_activity_unproved')
        self.assertLessEqual(clock.now-10,3.5);self.assertLessEqual(d.reads,3)
        self.assertEqual(d.calls,['dump_hierarchy','app_current']*d.reads)
        retype.assert_not_called();self.assertEqual(buf['native_xml'][0]['xml'],absence.suggestion_xml())

    def test_exact_device_timing_proxy_only_no_forged_kind_subclass_or_nested_authority(self):
        from run_latency_observability_v1 import _TimedProxy
        def proxy(d,kind='device',cls=_TimedProxy):return cls(d,kind=kind,emit=None,methods=frozenset({'dump_hierarchy','app_current'}))
        exact,out,d,_,_,_=self.observe(activity='.activity.MainTabActivity',qualified=True,wrap=proxy)
        self.assertFalse(exact);self.assertEqual(out['state'],'confirmed_absent');self.assertEqual(d.reads,4)
        class ForeignProxy(_TimedProxy):pass
        for wrap in (lambda d:proxy(d,kind='selector'),lambda d:proxy(d,cls=ForeignProxy),lambda d:proxy(proxy(d))):
            exact,out,d,_,_,retype=self.observe(activity='.activity.MainTabActivity',qualified=True,wrap=wrap)
            self.assertFalse(exact);self.assertEqual(out['reason'],'search_activity_unproved')
            self.assertNotIn('proof',out);self.assertEqual(d.reads,1);retype.assert_not_called()

    def test_MainTab_has_no_extra_observation_after_late_or_failed_IO(self):
        for frame,cost,reason in [(absence.xml(),4.,'search_observation_stale'),
                                  (RuntimeError('private adapter error'),.79,'search_hierarchy_read_failed')]:
            exact,out,d,_,_,retype=self.observe([frame],activity='.activity.MainTabActivity',qualified=True,cost=cost)
            self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(out['reason'],reason)
            self.assertEqual(d.reads,1);retype.assert_not_called()

    def test_positive_empty_requires_two_fresh_rounds_one_retype_and_original_XML(self):
        exact,out,d,clock,buf,retype=self.observe()
        self.assertFalse(exact);self.assertEqual(out['state'],'confirmed_absent')
        self.assertEqual([len(x) for x in out['proof'].rounds],[2,2])
        self.assertEqual(d.reads,4);retype.assert_called_once()
        self.assertEqual([x['xml'] for x in buf['native_xml']],[absence.xml(),absence.xml()])
        self.assertEqual([x['label'] for x in buf['native_xml']],['outreach_search_round_1_first','outreach_search_round_2_first'])

    def test_first_refusal_and_exact_XML_survive_without_masking_or_reobservation(self):
        xml=absence.xml(query='wrong_query')
        exact,out,d,clock,buf,retype=self.observe([xml,absence.xml()])
        self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(d.reads,1)
        self.assertEqual(out['reason'],'search_query_unproved');retype.assert_not_called()
        first=out['first_failure'];self.assertEqual(first['exception_class'],'ValueError')
        self.assertTrue(first['xml_retained']);self.assertEqual(first['poll_index'],1)
        self.assertTrue(all(x['xml']==xml for x in buf['native_xml']))

    def test_native_causal_unsafe_activity_is_not_guessed_as_ModalActivity_admission(self):
        for activity in ['com.instagram.modal.ModalActivity','OtherActivity',None]:
            with self.subTest(activity=activity):
                _,out,d,_,_,retype=self.observe(activity=activity)
                self.assertEqual(out['reason'],'search_activity_unproved');self.assertEqual(d.reads,1)
                self.assertNotIn('proof',out);retype.assert_not_called()

    def test_malformed_XML_original_exception_class_is_retained_privately(self):
        _,out,d,_,buf,retype=self.observe(['<broken'])
        self.assertEqual(out['reason'],'search_hierarchy_unparseable')
        self.assertEqual(out['first_failure']['exception_class'],'ParseError')
        self.assertEqual(buf['native_xml'][0]['xml'],'<broken');self.assertEqual(d.reads,1)
        retype.assert_not_called()

    def test_device_error_has_typed_stage_without_logging_sensitive_exception_text(self):
        secret='PRIVATE_ERROR_CONTENT'
        _,out,d,_,buf,retype=self.observe([RuntimeError(secret)])
        self.assertEqual(out['reason'],'search_hierarchy_read_failed')
        self.assertEqual(out['first_failure']['exception_class'],'RuntimeError')
        self.assertNotIn(secret,json.dumps(buf['events']));self.assertEqual(buf['native_xml'],[])
        retype.assert_not_called()

    def test_no_retention_ack_means_no_absence_and_no_retype(self):
        _,out,d,_,buf,retype=self.observe(capture=False)
        self.assertEqual(out['reason'],'search_observation_retention_unproved')
        self.assertNotIn('proof',out);self.assertEqual(d.reads,1);self.assertEqual(buf['native_xml'],[])
        retype.assert_not_called()

    def test_slow_IO_counts_towards_real_budget_and_never_retypes_suggestion_only(self):
        frame=absence.xml(label='',row='nearby_recipient')
        _,out,d,clock,_,retype=self.observe([frame])
        self.assertLessEqual(clock.now-10,3.5);self.assertLessEqual(d.reads,3)
        self.assertEqual(out['reason'],'search_results_non_match_only')
        self.assertNotIn('proof',out);retype.assert_not_called()

    def test_loading_remains_transient_with_only_existing_single_retype(self):
        _,out,d,clock,_,retype=self.observe([absence.xml(label='')])
        self.assertEqual(out['state'],'transient');self.assertNotIn('proof',out)
        self.assertLessEqual(clock.now-10,7.);self.assertLessEqual(d.reads,6)
        retype.assert_called_once()

    def test_one_inflight_call_overrun_never_confirms_absence_or_starts_another_read(self):
        _,out,d,clock,_,retype=self.observe(cost=4.)
        self.assertGreater(clock.now-10,3.5);self.assertEqual(d.reads,1)
        self.assertEqual(out['reason'],'search_observation_stale');self.assertNotIn('proof',out)
        retype.assert_not_called()

    def test_exact_row_still_requires_two_stable_observations_and_fresh_caller_rescan(self):
        exact,out,d,_,_,retype=self.observe([absence.xml(label='',row=absence.QUERY)])
        self.assertTrue(exact);self.assertEqual(d.reads,2);self.assertNotIn('proof',out)
        retype.assert_not_called()

    def test_changed_continuity_aborts_without_transforming_into_absence(self):
        from ui_continuity_v1 import UiContinuityLost
        with patch.object(nav.ui_continuity_v1,'require_epoch',side_effect=UiContinuityLost({'reason':'changed'})):
            with self.assertRaises(UiContinuityLost):self.observe()

    def test_opt_in_error_propagates_once_but_default_Unfollow_exception_policy_is_unchanged(self):
        for bounded in (False,True):
            clock=Clock();d=SearchDevice(clock,[TypeError('adapter')])
            with patch.object(search,'time',clock),patch.object(search,'log'):
                if bounded:
                    with self.assertRaises(TypeError):search._wait_for_exact_search_result(d,'target',elapsed_budget_s=3.5,propagate_observation_errors=True)
                    self.assertEqual(d.reads,1)
                else:
                    result=search._wait_for_exact_search_result(d,'target')
                    self.assertEqual(result['reason'],'search_hierarchy_unparseable')
                    self.assertEqual(d.reads,20) # legacy kwargs retry is preserved only for default callers

    def test_default_Unfollow_call_order_and_results_equal_signed_parent(self):
        ns=parent_nodes('unfollow_hybrid_strategy.py',{'_wait_for_exact_search_result'},dict(search.__dict__))
        for frame in [absence.xml(),absence.xml(label='',row=absence.QUERY),absence.xml(label='')]:
            results=[]
            for old in (True,False):
                clock=Clock();d=SearchDevice(clock,[frame]);ns.update(time=clock,log=lambda *a,**k:None)
                with patch.object(search,'time',clock),patch.object(search,'log'):
                    value=(ns['_wait_for_exact_search_result'] if old else search._wait_for_exact_search_result)(d,absence.QUERY)
                for k in ('result_visible_at','result_stable_at'):value.pop(k,None)
                results.append((value,d.calls,clock.sleeps))
            self.assertEqual(results[0],results[1])

    def test_deadline_also_rejects_exact_result_returned_late(self):
        # This direct waiter calls dump only; allocate four seconds to that
        # call (the adapter tests above also include app_current's cost).
        clock=Clock();d=SearchDevice(clock,[absence.xml(label='',row=absence.QUERY)],cost=10.)
        with patch.object(search,'time',clock),patch.object(search,'log'):
            result=search._wait_for_exact_search_result(d,absence.QUERY,elapsed_budget_s=3.5)
        self.assertFalse(result['ok']);self.assertEqual(result['reason'],'search_observation_deadline_expired')
        self.assertEqual(d.reads,1)

    def test_classifier_cost_is_inside_deadline_and_no_kwargs_retry_starts_after_expiry(self):
        clock=Clock();d=SearchDevice(clock,[absence.xml(label='',row=absence.QUERY)],cost=0)
        real=search.classify_search_surface_xml
        def classify(*args):clock.now+=4.;return real(*args)
        with patch.object(search,'time',clock),patch.object(search,'classify_search_surface_xml',side_effect=classify):
            self.assertEqual(search._wait_for_exact_search_result(d,absence.QUERY,elapsed_budget_s=3.5)['reason'],
                             'search_observation_deadline_expired')
        self.assertEqual(d.reads,1)
        clock=Clock();d=SearchDevice(clock,[TypeError('unsupported')],cost=10.)
        with patch.object(search,'time',clock):
            result=search._wait_for_exact_search_result(d,absence.QUERY,elapsed_budget_s=3.5)
        self.assertFalse(result['ok']);self.assertEqual(d.reads,1)

def completion(d):
    scope=dict(managed_campaign=True,run_id='fixture-run',request_id='fixture-request',client_id='fixture-client',
               campaign_id='fixture-campaign',campaign_revision=1,account_id='fixture-account')
    job=dict(id='fixture-job',account_id=scope['account_id'],campaign_id=scope['campaign_id'],dm_type='outreach',
             recipient_username=F['recipient'],message_body=F['expected_message'],metadata=dict(
                 origin='CLIENT_LIST',managed_campaign=True,client_id=scope['client_id'],campaign_revision=1,
                 outreach_request_id=scope['request_id']))
    row={**copy.deepcopy(job),'status':'sent','sent_at':'2026-01-01T00:00:00Z'}
    return dict(job=job,canonical_row=row,run_id=scope['run_id'],device_identity=id(d),
                observed_at_monotonic=nav.time.monotonic(),continuity_epoch=nav.ui_continuity_v1.epoch()),type('Slot',(),{'context':scope})()

class InviteRestorationTests(unittest.TestCase):
    def prove(self,xml=None,*,change=None,version=446):
        d=Device(xml or F['invite_surfaces'][0]['xml'],version=version)
        c,s=completion(d)
        if change:change(c,s,d)
        with patch('outreach_slot_contract.active',return_value=s),patch.object(nav,'log'),patch.object(nav._dm_retention,'capture_xml'):
            answer=nav.outreach_446_restoration_thread_proved(d,F['recipient'],P,sent_completion=c)
        return answer,d

    def test_all_three_deidentified_native_clipped_long_emoji_geometries_prove_after_persistence(self):
        self.assertEqual(len(F['expected_message']),803)
        self.assertEqual(len(F['expected_message'].encode('utf-16-le'))//2,810)
        for row in F['invite_surfaces']:
            with self.subTest(row=row['label']):
                answer,d=self.prove(row['xml']);self.assertTrue(answer)
                self.assertEqual((d.reads,d.currents,d.dimensions),(1,2,1))

    def test_exact_unexported_message_and_Business_identity_remain_qualified(self):
        xml=F['invite_surfaces'][0]['xml']
        exported=''.join('..' if ord(c)>0xffff else c for c in F['expected_message'])
        root=ET.fromstring(xml)
        for n in root.iter():
            if n.get('text')==exported:n.set('text',F['expected_message'])
            if n.get('resource-id')==P+':id/header_title':n.set('text',F['recipient'])
            if n.get('resource-id')==P+':id/header_subtitle':n.set('content-desc','Business chat')
        self.assertTrue(self.prove(ET.tostring(root,encoding='unicode'))[0])

    def test_missing_wrong_or_uncertain_canonical_result_never_enables_Invite_shortcut(self):
        changes=[lambda c,s,d:c.update(canonical_row=None),lambda c,s,d:c['canonical_row'].update(status='pending'),
          lambda c,s,d:c['canonical_row'].update(sent_at=None),lambda c,s,d:c['canonical_row'].update(id='foreign'),
          lambda c,s,d:c['canonical_row'].update(account_id='foreign'),lambda c,s,d:c['canonical_row'].update(campaign_id='foreign'),
          lambda c,s,d:c['canonical_row'].update(message_body='partial'),lambda c,s,d:c['canonical_row']['metadata'].update(outreach_request_id='foreign'),
          lambda c,s,d:c['canonical_row']['metadata'].update(client_id='foreign'),lambda c,s,d:c['canonical_row']['metadata'].update(campaign_revision=2),
          lambda c,s,d:c.update(run_id='foreign'),lambda c,s,d:c.update(device_identity=0),
          lambda c,s,d:c.update(continuity_epoch=999),lambda c,s,d:c.update(observed_at_monotonic=nav.time.monotonic()-6),
          lambda c,s,d:c.update(observed_at_monotonic=nav.time.monotonic()+6),lambda c,s,d:s.context.update(managed_campaign=False)]
        for i,change in enumerate(changes):
            with self.subTest(change=i):self.assertFalse(self.prove(change=change)[0])

    def test_no_receipt_keeps_parent_rejection_for_disabled_thread(self):
        d=Device(F['invite_surfaces'][0]['xml'])
        self.assertFalse(nav.outreach_446_restoration_thread_proved(d,F['recipient'],P))

    def test_partial_text_wrong_direction_wrong_identity_and_unknown_Invite_labels_refuse(self):
        def partial(root):
            for n in root.iter():
                if len(n.get('text',''))>250:n.set('text',n.get('text')[:-1])
        def incoming(root):
            for n in root.iter():
                if len(n.get('text',''))>250:n.set('bounds','[10,500][800,1800]')
        def identity(root):
            for n in root.iter():
                if n.get('resource-id')==P+':id/header_subtitle':n.set('content-desc','other_recipient')
        def label(root):
            for n in root.iter():
                if n.get('text')=='Invite sent':n.set('text','Verify your account')
        for fn in (partial,incoming,identity,label):
            with self.subTest(fn=fn.__name__):self.assertFalse(self.prove(mutate(F['invite_surfaces'][0]['xml'],fn))[0])

    def test_unknown_app_modal_permission_challenge_or_root_overlay_never_shortcuts(self):
        for label in ('unknown','Consent required','Confirm you are human','Log in','Restriction'):
            def add(root):ET.SubElement(root,'node',dict(package=P,**{'class':'android.app.Dialog','bounds':'[0,0][1080,2340]','text':label}))
            self.assertFalse(self.prove(mutate(F['invite_surfaces'][0]['xml'],add))[0])
        for change in [lambda c,s,d:setattr(d,'foregrounds',[dict(package='foreign',activity=d.activity)]),
                       lambda c,s,d:setattr(d,'after_read',lambda dev:setattr(dev,'serial','foreign')),
                       lambda c,s,d:setattr(d,'after_read',lambda dev:setattr(dev,'_instagram_446_observed_context',None))]:
            self.assertFalse(self.prove(change=change)[0])

    def test_default_372_and_unversioned_context_add_no_observation_or_action(self):
        for v in (0,372):
            answer,d=self.prove(version=v);self.assertFalse(answer)
            self.assertEqual((d.reads,d.currents,d.dimensions),(0,0,0))

    def run_restore(self,row,parent=False,permission=False):
        d=harness.RestoreDevice(row['xml']);d.clock=Clock();c,s=completion(d)
        c['observed_at_monotonic']=d.clock.now
        ns=dict(engine.__dict__)
        names={'_safe_teardown_navigation','prepare_dm_sender_global_search_surface'}
        if parent:parent_nodes('dm_sender_engine.py',names,ns)
        else:
            nodes=[n for n in ast.parse((ROOT/'dm_sender_engine.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
            exec(compile(ast.Module(body=nodes,type_ignores=[]),'<candidate>','exec'),ns)
        oldnav=parent_nodes('instagram_navigation.py',{'outreach_446_restoration_thread_proved'},dict(nav.__dict__))
        def veto(*a,**k):d.calls.append('permission_veto');return permission
        def profile(*a,**k):d.calls.append('exact_profile');return d.stage=='profile'
        def back(*a,**k):d.to_profile();return True,'exact_action_bar'
        ns.update(_check_dm_sender_permission_blocker=veto,verify_profile=profile,tap_instagram_action_bar_back_button=back,
                  log=lambda *a,**k:None,time=d.clock,outreach_446_restoration_thread_proved=oldnav['outreach_446_restoration_thread_proved'] if parent else nav.outreach_446_restoration_thread_proved)
        with patch.object(nav,'time',d.clock),patch.object(nav,'log'),patch('outreach_slot_contract.active',return_value=s):
            ns['_safe_teardown_navigation'](d,F['recipient'],pkg=P,account_username='fixture_emitter',prefer_back_stack_to_search=True,
                                         **({} if parent else {'_sent_completion':c}))
        return d,engine._get_dm_sender_post_job_restore()

    def test_full_restore_differential_preserves_exact_gestures_fresh_permission_profile_search(self):
        for row in F['invite_surfaces']:
            old,os=self.run_restore(row,True);new,ns=self.run_restore(row)
            self.assertEqual(old.stage,new.stage);self.assertEqual(new.stage,'search')
            gestures=lambda d:[x for x in d.calls if x in ('thread_back','profile_back')]
            self.assertEqual(gestures(old),['thread_back','profile_back']);self.assertEqual(gestures(old),gestures(new))
            self.assertEqual(new.calls.count('permission_veto'),1)
            self.assertEqual(new.calls.count('exact_profile'),old.calls.count('exact_profile'))
            self.assertEqual(os['post_job_restore_final_mode'],ns['post_job_restore_final_mode'])
            self.assertLess(len(new.calls),len(old.calls))
            self.assertEqual(new.calls.count('dump_hierarchy'),old.calls.count('dump_hierarchy')-1)

    def test_remaining_permission_veto_happens_before_any_read_or_gesture(self):
        d,result=self.run_restore(F['invite_surfaces'][0],permission=True)
        self.assertEqual(d.calls,['permission_veto'])
        self.assertEqual(result['post_job_restore_final_reason'],'permission_blocker')

class CanonicalCompletionBindingTests(unittest.TestCase):
    def execute(self,*,ack=True,raises=False,unverified=False,navigate=True):
        d=Device(F['invite_surfaces'][0]['xml']);c,s=completion(d)
        job=c['job'];row=c['canonical_row']
        s.remaining_ms=lambda:100000;s.stop_reason=None
        with ExitStack() as stack:
            stack.enter_context(patch.object(engine,'log'))
            stack.enter_context(patch('outreach_slot_contract.active',return_value=s))
            stack.enter_context(patch.object(engine.supabase_client,'mark_dm_job_running',return_value=True))
            stack.enter_context(patch.object(engine,'_navigate_to_recipient_dm_thread',return_value=('empty_new_thread',navigate)))
            stack.enter_context(patch.object(engine,'get_last_dm_thread_classify_snapshot',return_value={}))
            stack.enter_context(patch.object(engine,'_get_dm_sender_nav_timings',return_value={}))
            stack.enter_context(patch.object(engine,'_evaluate_welcome_sendability',return_value=(True,None)))
            stack.enter_context(patch.object(engine,'_evaluate_outreach_sendability',return_value=(True,None)))
            send=stack.enter_context(patch.object(engine,'_perform_real_welcome_dm_send',return_value=(
                (False,{'send_tapped':True},'send_unverified') if unverified else (True,{},None))))
            rpc=stack.enter_context(patch.object(engine.supabase_client,'complete_dm_job',return_value=row if ack else None,
                side_effect=RuntimeError('transport') if raises else None))
            stack.enter_context(patch.object(engine,'_complete_job_send_unverified_quarantine',return_value=({'status':'failed'},'failed')))
            stack.enter_context(patch.object(engine,'_complete_job_failed_retry',return_value=({'status':'pending'},'failed_retry')))
            restore=stack.enter_context(patch.object(engine,'_safe_teardown_navigation'))
            error=None
            try:result=engine.execute_dm_job_real_send(d,job,settings={},account_id=job['account_id'],run_id=s.context['run_id'])
            except Exception as exc:error=exc;result=None
            kwargs=restore.call_args.kwargs if restore.called else {}
        return c,row,kwargs,result,error,rpc.call_count,send.call_count

    def test_only_actual_canonical_row_and_original_job_are_passed_after_sent_completion(self):
        c,row,kw,result,error,count,_=self.execute()
        self.assertIsNone(error);self.assertEqual(result['outcome'],'sent');self.assertEqual(count,1)
        bound=kw['_sent_completion'];self.assertIs(bound['canonical_row'],row)
        self.assertIs(bound['job'],c['job']);self.assertEqual(bound['run_id'],c['run_id'])

    def test_missing_or_failed_persistence_never_produces_restore_authority(self):
        for option in ({'ack':False},{'raises':True}):
            with self.subTest(option=option):
                _,_,kw,_,error,count,_=self.execute(**option)
                self.assertIsNotNone(error);self.assertEqual(count,1);self.assertNotIn('_sent_completion',kw)

    def test_unverified_Send_never_uses_Invite_shortcut_or_repeats_send(self):
        _,_,kw,result,error,count,sends=self.execute(unverified=True)
        self.assertIsNone(error);self.assertNotIn('_sent_completion',kw)
        self.assertEqual((count,sends),(0,1));self.assertNotEqual(result['outcome'],'sent')

    def test_navigation_UNKNOWN_has_no_send_or_restore_completion_authority(self):
        _,_,kw,result,error,count,sends=self.execute(navigate=False)
        self.assertIsNone(error);self.assertNotIn('_sent_completion',kw)
        self.assertEqual((count,sends),(0,0));self.assertEqual(result['outcome'],'failed_retry')


class MainTabActivityExactInverseTests(unittest.TestCase):
    def test_only_activity_predicate_changes_and_existing_local_authority_is_byte_preserved(self):
        import test_certification_admission as pins
        base=subprocess.check_output(['git','show','b0ad19da5660878e28c3728e58f5ae28fb1646ec:instagram_navigation.py'],cwd=ROOT,text=True)
        source=pins._restore_c75_early_search_if_exact_candidate('instagram_navigation.py',(ROOT/'instagram_navigation.py').read_text())
        self.assertEqual(pins.restore_main_tab_activity_exact_delta('instagram_navigation.py',source),base)
        nodes=lambda s:{n.name:ast.dump(n) for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        before,after=nodes(base),nodes(source)
        self.assertEqual(set(before),set(after))
        self.assertEqual({n for n in before if before[n]!=after[n]},{'_observe_outreach_missing_search'})
        rest=lambda s:[ast.dump(n) for n in ast.parse(s).body if not isinstance(n,ast.FunctionDef) or n.name!='_observe_outreach_missing_search']
        self.assertEqual(rest(base),rest(source))

    def test_full_source_and_exact_single_hunk_reject_foreign_missing_moved_duplicate_bytes(self):
        import base64,zlib,test_certification_admission as pins
        rows=json.loads(zlib.decompress(base64.b64decode(pins._MAIN_TAB_ACTIVITY_EXACT_INVERSE)))
        self.assertEqual(set(rows),{'instagram_navigation.py'})
        row=rows['instagram_navigation.py'];self.assertEqual(len(row['hunks']),1)
        source=(ROOT/'instagram_navigation.py').read_text();h=row['hunks'][0];at,new=h['offset'],h['new']
        for bad in (source+'\n',source[:at]+source[at+1:],source[:at]+new+source[at:],
                    source[:at]+source[at+len(new):]+new):
            with self.subTest(hash=hashlib.sha256(bad.encode()).hexdigest()):
                with self.assertRaises(AssertionError):pins.restore_main_tab_activity_exact_delta('instagram_navigation.py',bad)
        self.assertEqual(pins.restore_main_tab_activity_exact_delta('not_a_scoped_file','unchanged'),'unchanged')

    def test_prior_inverse_constants_and_all_existing_Golden_assertion_nodes_stay_intact(self):
        base=subprocess.check_output(['git','show','b0ad19da5660878e28c3728e58f5ae28fb1646ec:tests/test_certification_admission.py'],cwd=ROOT,text=True)
        current=(ROOT/'tests/test_certification_admission.py').read_text()
        constants=lambda s:{n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(s).body if isinstance(n,ast.Assign)
            and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id.endswith('_EXACT_INVERSE')}
        for name,value in constants(base).items():self.assertEqual(constants(current)[name],value,name)
        classes=lambda s:[ast.dump(n) for n in ast.parse(s).body if isinstance(n,ast.ClassDef)]
        self.assertEqual(classes(base),classes(current))

class SearchBudgetInviteExactInverseTests(unittest.TestCase):
    def test_full_parent_and_exact_hunks_are_required_foreign_missing_moved_duplicates_rejected(self):
        import base64,zlib,test_certification_admission as pins
        rows=json.loads(zlib.decompress(base64.b64decode(pins._SEARCH_BUDGET_INVITE_EXACT_INVERSE)))
        self.assertEqual(set(rows),{'instagram_navigation.py','unfollow_hybrid_strategy.py','dm_sender_engine.py'})
        for name,row in rows.items():
            source=(ROOT/name).read_text()
            parent=subprocess.check_output(['git','show',PARENT+':'+name],cwd=ROOT,text=True)
            self.assertEqual(pins.restore_search_budget_invite_exact_delta(name,source),parent)
            self.assertEqual(pins.restore_search_budget_invite_exact_delta(name,parent),parent)
            h=next(x for x in row['hunks'] if x['new']);at,new=h['offset'],h['new']
            for bad in (source+'\n',source[:at]+source[at+1:],source[:at]+new+source[at:],source[:at]+source[at+len(new):]+new):
                if bad!=source:
                    with self.assertRaises(AssertionError):pins.restore_search_budget_invite_exact_delta(name,bad)

    def test_only_scoped_runtime_functions_change_and_all_earlier_inverse_constants_preserved(self):
        import test_certification_admission as pins
        expected={'instagram_navigation.py':{'_observe_outreach_missing_search','outreach_446_restoration_thread_proved'},
                  'unfollow_hybrid_strategy.py':{'_wait_for_exact_search_result'},
                  'dm_sender_engine.py':{'prepare_dm_sender_global_search_surface','_safe_teardown_navigation','execute_dm_job_real_send'}}
        for name,changed in expected.items():
            old=subprocess.check_output(['git','show',PARENT+':'+name],cwd=ROOT,text=True)
            # Historical search-budget delta only; exact new diagnostic bytes are qualified separately.
            new=pins._restore_audited_failure_if_exact_candidate(name,(ROOT/name).read_text())
            nodes=lambda s:{n.name:ast.dump(n) for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
            a,b=nodes(old),nodes(new);self.assertEqual(set(a),set(b))
            self.assertEqual({k for k in a if a[k]!=b[k]},changed)
            rest=lambda s:[ast.dump(n) for n in ast.parse(s).body if not (isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in changed)]
            self.assertEqual(rest(old),rest(new))
        previous=subprocess.check_output(['git','show',PARENT+':tests/test_certification_admission.py'],cwd=ROOT,text=True)
        constants=lambda s:{n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(s).body if isinstance(n,ast.Assign)
            and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id.endswith('_EXACT_INVERSE')}
        actual=constants((ROOT/'tests/test_certification_admission.py').read_text())
        for key,value in constants(previous).items():self.assertEqual(actual[key],value,key)

