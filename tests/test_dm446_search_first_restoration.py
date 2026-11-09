"""Actual Search-only restoration paths with deidentified native geometry, no device/network."""
from __future__ import annotations
import ast,copy,dataclasses,json,pickle,subprocess,unittest,xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import instagram_navigation as nav
import dm_sender_engine as engine
import run_latency_observability_v1 as latency
import outreach_slot_contract as slots
import unfollow_hybrid_strategy as search
from test_dm446_search_budget_invite_restore import Clock
from test_dm446_worker1083_residual_performance import RestoreDevice
from test_outreach_search_absence_v1 import recorder_buffer
ROOT=Path(__file__).resolve().parents[1]
F=json.loads((ROOT/'tests/fixtures/dm446_search_first_restoration_v1.json').read_text())
P=F['package'];QUERY=F['query']
class Device(RestoreDevice):
    def __init__(self,xml=F['xml']):
        super().__init__(xml);self.clock=Clock();self.activity='.activity.MainTabActivity';self.stage='search'
    def dump_hierarchy(self,**kw):
        return super().dump_hierarchy()
class SearchFirstTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.d=Device();self.job=dict(id='fixture-job',account_id='fixture-account',dm_type='outreach',
            recipient_username=QUERY,metadata=dict(outreach_request_id='fixture-request',outreach_slot_id='fixture-slot'))
        self.context=dict(account_id='fixture-account',run_id='fixture-run',request_id='fixture-request',
            device_id='fixture-device',app_instance_id='fixture-instance',serial=self.d.serial,package=P,
            version_name='446.0.0.49.77',version_code=385211304)
        self.buf=recorder_buffer();self.buf.update(context=self.context,job_id=self.job['id'])
        self.slot=SimpleNamespace(context=dict(managed_campaign=True,account_id='fixture-account',
            request_id='fixture-request',slot_id='fixture-slot',device_id='fixture-device',
            app_instance_id='fixture-instance',adb_serial=self.d.serial,package_name=P),
            device_verified=True,finished=False,stop_reason=None,remaining_ms=lambda:60000)
        self.metric=dict(account_id='fixture-account',run_id='fixture-run',job_id=self.job['id'],ui_generation=2)
        for obj,name,val in [(nav,'time',self.d.clock),(engine,'time',self.d.clock),(search,'time',self.d.clock),
                (nav._dm_retention,'_composer_buffer',self.buf)]:
            self.stack.enter_context(patch.object(obj,name,val))
        self.stack.enter_context(patch.object(latency,'current_context',side_effect=lambda:dict(self.metric)))
        self.stack.enter_context(patch.object(slots,'active',side_effect=lambda:self.slot))
        self.stack.enter_context(patch.object(nav,'log'));self.stack.enter_context(patch.object(engine,'log'))
        self.out={}
    def seed(self):
        nav.begin_outreach_search_restoration(self.d,self.job,self.out,account_id='fixture-account',sender='fixture-sender',run_id='fixture-run')
        c=self.out['_search_restoration']
        nav._observe_search_restoration_candidate(self.d,self.out,F['xml'],self.d.app_current(),QUERY)
        c.no_profile_entry=True
        self.d.calls.clear()
        return c
    def reuse(self,c,**kw):
        return nav.reuse_outreach_search_restoration(self.d,c,**dict(
            account_id='fixture-account',sender='fixture-sender',run_id='fixture-run',
            job_id=self.job['id'],query=QUERY,**kw))
    def test_native_shape_uses_one_fresh_bracketed_observation_and_no_gesture(self):
        c=self.seed();self.assertTrue(self.reuse(c))
        self.assertEqual(self.d.calls,['app_current','dump_hierarchy','app_current'])
        self.assertEqual(self.buf['native_xml'][-1]['xml'],F['xml'])
        self.assertEqual(self.buf['native_xml'][-1]['label'],'outreach_search_restore_current')
    def test_single_use_copy_forgery_and_serialization_rejected(self):
        c=self.seed()
        with self.assertRaises(TypeError):copy.copy(c)
        self.assertFalse(self.reuse(dataclasses.replace(c)));self.assertFalse(c.used)
        with self.assertRaises(TypeError):pickle.dumps(c)
        self.assertTrue(self.reuse(c));self.d.calls.clear()
        self.assertFalse(self.reuse(c));self.assertEqual(self.d.calls,[])
        self.assertFalse(self.reuse(dict(eligible=True)))
    def test_changed_contexts_reject_before_any_phone_read(self):
        changes=[lambda:setattr(self.d,'serial','other'),lambda:self.context.update(account_id='other'),
            lambda:self.context.update(run_id='other'),lambda:self.context.update(device_id='other'),
            lambda:self.metric.update(job_id='other'),lambda:self.metric.update(ui_generation=3),
            lambda:self.job.update(recipient_username='other'),lambda:self.slot.context.update(request_id='other'),
            lambda:setattr(self.slot,'device_verified',False),lambda:self.d.clock.sleep(12.01),
            lambda:setattr(self.d,'_instagram_446_observed_context',None)]
        for change in changes:
            with self.subTest(change=change):
                # Each case needs an independently minted same-process context.
                saved=(self.d.serial,dict(self.context),dict(self.metric),dict(self.job),dict(self.slot.context),
                    self.slot.device_verified,self.d.clock.now,getattr(self.d,'_instagram_446_observed_context'))
                c=self.seed();change();self.assertFalse(self.reuse(c));self.assertEqual(self.d.calls,[])
                self.d.serial=saved[0];self.context.clear();self.context.update(saved[1]);self.metric.clear();self.metric.update(saved[2])
                self.job.clear();self.job.update(saved[3]);self.slot.context.clear();self.slot.context.update(saved[4])
                self.slot.device_verified=saved[5];self.d.clock.now=saved[6];self.d._instagram_446_observed_context=saved[7]
    def test_no_profile_entry_is_required(self):
        c=self.seed();c.no_profile_entry=False
        self.assertFalse(self.reuse(c));self.assertEqual(self.d.calls,[])
    def test_foreground_change_or_stale_fresh_read_rejected(self):
        c=self.seed();self.d.foregrounds=[dict(package=P,activity=self.d.activity),dict(package='foreign',activity=self.d.activity)]
        self.assertFalse(self.reuse(c))
        c=self.seed();self.d.after_read=lambda d:d.clock.sleep(4.)
        self.assertFalse(self.reuse(c))
    def test_no_exact_retention_or_XML_IO_authority_fails_closed(self):
        for frame in ['<hierarchy/>','<broken',RuntimeError('offline-native-IO')]:
            c=self.seed();self.d.xmls=[frame];self.d.reads=0
            self.assertFalse(self.reuse(c))
        self.d.xmls=[F['xml']];self.d.reads=0;c=self.seed()
        with patch.object(nav._dm_retention,'capture_xml'):
            self.assertFalse(self.reuse(c))
    def test_any_populated_unknown_sheet_modal_or_challenge_is_not_Search(self):
        for container in ['modal_container','overlay_layout_container','bottom_sheet_camera_container']:
            root=ET.fromstring(F['xml'])
            n=next(x for x in root.iter() if x.get('resource-id')==P+':id/'+container)
            ET.SubElement(n,'node',{'package':P,'class':'android.widget.FrameLayout','text':'fixture-placeholder'})
            self.assertFalse(nav._search_restoration_surface(ET.tostring(root,encoding='unicode'),QUERY,P,self.d.activity))
        for label in ['Unknown','Confirm you are human','Choose if we process your data for ads','Log in','Restriction']:
            root=ET.fromstring(F['xml']);ET.SubElement(root,'node',{'package':P,'class':'android.app.Dialog','text':label})
            self.assertFalse(nav._search_restoration_surface(ET.tostring(root,encoding='unicode'),QUERY,P,self.d.activity))
    def test_exact_query_local_Followers_or_composer_cannot_be_reused(self):
        for rid in ['action_bar_search_edit_text','recycler_view','action_bar']:
            root=ET.fromstring(F['xml']);n=next(x for x in root.iter() if x.get('resource-id')==P+':id/'+rid)
            n.set('resource-id',P+':id/row_thread_composer_edittext')
            self.assertFalse(nav._search_restoration_surface(ET.tostring(root,encoding='unicode'),QUERY,P,self.d.activity))
        self.assertFalse(nav._search_restoration_surface(F['xml'],'foreign_query',P,self.d.activity))
    def test_exact_profile_appearing_after_deadline_is_not_suppressed_as_absence(self):
        c=self.seed();root=ET.fromstring(F['xml'])
        next(n for n in root.iter() if n.get('resource-id')==P+':id/row_search_user_username').set('text',QUERY)
        self.d.xmls=[ET.tostring(root,encoding='unicode')];self.d.reads=0
        self.assertFalse(self.reuse(c));self.assertNotIn('proof',self.out)
    def test_permission_veto_precedes_new_observation_and_preserves_fallback(self):
        c=self.seed()
        with patch.object(engine,'_check_dm_sender_permission_blocker',return_value=True):
            self.assertFalse(engine.prepare_dm_sender_global_search_surface(self.d,account_username='fixture-sender',
                context='dm_sender_post_job',last_recipient_username=QUERY,prefer_back_stack_to_search=True,
                _outreach_permission_check_owned=True,_search_restoration=dict(candidate=c,account_id='fixture-account',run_id='fixture-run',job_id=self.job['id'])))
        self.assertEqual(self.d.calls,[]);self.assertFalse(c.used)
    def restore(self,parent=False):
        c=self.seed();ns=dict(engine.__dict__)
        source=(ROOT/'dm_sender_engine.py').read_text()
        if parent:
            source=subprocess.check_output(['git','show','b9a22c34b9d9efee7ab73faf9b4bd83ebe4e219c:dm_sender_engine.py'],cwd=ROOT,text=True)
        names={'prepare_dm_sender_global_search_surface','_verify_dm_sender_global_search_surface'}
        nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<exact-source>', 'exec'),ns)
        def permission(*a,**k):self.d.calls.append('permission_veto');return False
        ns['_check_dm_sender_permission_blocker']=permission
        engine._reset_dm_sender_post_job_restore()
        extra={} if parent else dict(_search_restoration=dict(candidate=c,account_id='fixture-account',run_id='fixture-run',job_id=self.job['id']))
        answer=ns['prepare_dm_sender_global_search_surface'](self.d,account_username='fixture-sender',
            context='dm_sender_post_job',last_recipient_username=QUERY,prefer_back_stack_to_search=True,**extra)
        return answer,list(self.d.calls),engine._get_dm_sender_post_job_restore()
    def test_real_parent_candidate_differential_preserves_result_and_removes_redundant_readers(self):
        old,oc,os=self.restore(True);self.d.reads=0
        new,nc,ns=self.restore()
        self.assertTrue(old and new);self.assertEqual(os['post_job_restore_final_reason'],ns['post_job_restore_final_reason'])
        self.assertEqual(nc,['permission_veto','app_current','dump_hierarchy','app_current'])
        self.assertNotIn('window_size',nc);self.assertNotIn('wait',nc);self.assertNotIn('get_text',nc)
        self.assertLess(len(nc),len(oc));self.assertEqual(oc.count('permission_veto'),2)
    def test_observer_and_actual_no_row_exit_own_eligibility_but_never_terminalize(self):
        nav.begin_outreach_search_restoration(self.d,self.job,self.out,account_id='fixture-account',sender='fixture-sender',run_id='fixture-run')
        with patch.object(nav,'_dump_no_real_account_row_debug'),patch.object(nav,'_peek_pending_fused_fast_ime_row',return_value=None):
            nav.set_search_ui_mode('mixed_results')
            answer=nav.tap_account_result(self.d,QUERY,outreach_search_context=True,outreach_search_outcome=self.out)
        self.assertFalse(answer);self.assertEqual(self.out['state'],'unknown');self.assertNotIn('proof',self.out)
        c=self.out['_search_restoration'];self.assertTrue(c.eligible);self.assertTrue(c.no_profile_entry)
        self.d.calls.clear();self.assertTrue(self.reuse(c))
    def test_corrupt_or_unretained_observer_cannot_mint_eligibility(self):
        nav.begin_outreach_search_restoration(self.d,self.job,self.out,account_id='fixture-account',sender='fixture-sender',run_id='fixture-run')
        self.d.xmls=[RuntimeError('offline-read-error')];self.d.reads=0
        nav._observe_outreach_missing_search(self.d,QUERY,outcome=self.out,_shared_deadline=self.d.clock.now+12.)
        self.assertFalse(self.out['_search_restoration'].eligible);self.assertNotIn('proof',self.out)

    def test_execute_UNKNOWN_handoff_then_next_exact_query_keeps_business_contract(self):
        def navigate(d,recipient,**kw):
            out=kw['outreach_search_outcome']
            nav.begin_outreach_search_restoration(d,kw['_search_restore_job'],out,
                account_id=kw['account_id'],sender=kw['account_username'],run_id=kw['run_id'])
            nav._observe_search_restoration_candidate(d,out,F['xml'],d.app_current(),QUERY)
            out['_search_restoration'].no_profile_entry=True
            out.update(state='unknown',reason='search_observation_deadline_expired')
            return 'unknown',False
        self.job['attempts']=2
        with patch.object(engine,'_navigate_to_recipient_dm_thread',side_effect=navigate), \
             patch.object(engine.supabase_client,'mark_dm_job_running',return_value=self.job), \
             patch.object(engine,'_complete_job_failed_retry',return_value=({**self.job,'status':'pending'},'failed_retry')) as complete, \
             patch.object(engine,'_check_dm_sender_permission_blocker',return_value=False), \
             patch.object(engine,'_perform_real_welcome_dm_send',side_effect=AssertionError('NO_SEND_ON_UNKNOWN')):
            result=engine.execute_dm_job_real_send(self.d,self.job,settings={},account_id='fixture-account',
                account_username='fixture-sender',run_id='fixture-run')
        self.assertEqual(result['outcome'],'failed_retry');self.assertEqual(complete.call_count,1)
        self.assertEqual(self.job['attempts'],2)
        self.assertTrue(engine._dm_sender_trust_global_search_ready('fixture-sender'))
        self.metric['job_id']='fixture-next-job';self.buf['job_id']='fixture-next-job'
        with patch.object(engine,'_check_dm_sender_permission_blocker',return_value=False), \
             patch.object(engine,'is_dm_thread_screen',return_value=False), \
             patch.object(engine,'_open_search_with_recovery',side_effect=AssertionError('REDUNDANT_SEARCH_OPEN')), \
             patch.object(engine,'type_search',return_value=True) as typed, \
             patch.object(engine,'get_perf_snapshot',return_value={}), \
             patch.object(engine.config,'FAST_SKIP_ACCOUNTS_TAB',True), \
             patch.object(engine.config,'FAST_PATH_MODE',True), \
             patch.object(engine,'tap_account_result',return_value=False):
            state,ok=engine._navigate_to_recipient_dm_thread(self.d,'fixture_next_exact',pkg=P,
                account_id='fixture-account',account_username='fixture-sender',run_id='fixture-run',
                dm_type='outreach',previous_username=QUERY,outreach_search_outcome={})
        self.assertFalse(ok);self.assertEqual(state,'unknown')
        self.assertEqual(typed.call_args.args[1],'fixture_next_exact')
        self.assertEqual(typed.call_args.kwargs['previous_username'],QUERY)

    def test_unknown_inert_overlay_and_wrong_configured_IME_are_not_admitted(self):
        root=ET.fromstring(F['xml'])
        ET.SubElement(root,'node',{'package':P,'class':'android.widget.FrameLayout',
            'bounds':'[0,0][1080,2340]','clickable':'false','text':''})
        self.assertFalse(nav._search_restoration_surface(ET.tostring(root,encoding='unicode'),QUERY,P,self.d.activity))
        with patch.object(nav.config,'FAST_IME','foreign/.IME'):
            self.assertFalse(nav._search_restoration_surface(F['xml'],QUERY,P,self.d.activity))

    def test_optional_comparison_failure_cannot_change_Search_verdict(self):
        nav.begin_outreach_search_restoration(self.d,self.job,self.out,account_id='fixture-account',sender='fixture-sender',run_id='fixture-run')
        with patch.object(nav,'_search_restoration_surface',side_effect=RuntimeError('optional_comparison')):
            nav._observe_search_restoration_candidate(self.d,self.out,F['xml'],self.d.app_current(),QUERY)
        self.assertFalse(self.out['_search_restoration'].eligible)

    def test_unknown_Android_window_or_SystemUI_shade_cannot_cover_Search(self):
        for package in ('android','com.android.systemui','com.android.permissioncontroller'):
            root=ET.fromstring(F['xml'])
            ET.SubElement(root,'node',{'package':package,'class':'android.widget.FrameLayout',
                'bounds':'[0,0][1080,2340]','clickable':'false','text':''})
            self.assertFalse(nav._search_restoration_surface(ET.tostring(root,encoding='unicode'),QUERY,P,self.d.activity))

    def test_changed_generation_or_slot_during_read_invalidates_fresh_proof(self):
        c=self.seed();self.d.after_read=lambda d:self.metric.update(ui_generation=3)
        self.assertFalse(self.reuse(c))
        self.metric['ui_generation']=2;self.d.after_read=None
        c=self.seed();self.d.after_read=lambda d:setattr(self.slot,'stop_reason','fixture-stop')
        self.assertFalse(self.reuse(c))

    def test_slow_first_read_never_starts_another_IO_call(self):
        c=self.seed();original=self.d.app_current
        def slow():
            result=original();self.d.clock.sleep(3.51);return result
        with patch.object(self.d,'app_current',side_effect=slow):
            self.assertFalse(self.reuse(c))
        self.assertEqual(self.d.calls,['app_current'])

    def test_other_job_run_or_device_object_cannot_consume_proof(self):
        c=self.seed()
        self.assertFalse(nav.reuse_outreach_search_restoration(Device(),c,
            account_id='fixture-account',sender='fixture-sender',run_id='fixture-run',
            job_id=self.job['id'],query=QUERY))
        self.assertEqual(self.d.calls,[])
        for values in [dict(job_id='other'),dict(run_id='other'),dict(sender='other')]:
            c=self.seed();args=dict(account_id='fixture-account',sender='fixture-sender',
                run_id='fixture-run',job_id=self.job['id'],query=QUERY);args.update(values)
            self.assertFalse(nav.reuse_outreach_search_restoration(self.d,c,**args))
            self.assertEqual(self.d.calls,[])

class ExactParentPreservationTests(unittest.TestCase):
    def test_entire_exact_parent_restored_and_unknown_missing_duplicate_moved_bytes_rejected(self):
        import test_certification_admission as pins
        for name in ('instagram_navigation.py','dm_sender_engine.py'):
            source=(ROOT/name).read_text()
            parent=subprocess.check_output(['git','show','b9a22c34b9d9efee7ab73faf9b4bd83ebe4e219c:'+name],cwd=ROOT,text=True)
            self.assertEqual(pins.restore_search_first_restoration_exact_delta(name,source),parent)
            self.assertEqual(pins.restore_search_first_restoration_exact_delta(name,parent),parent)
            lines=source.splitlines(keepends=True)
            for foreign in [source+'\n# foreign\n',''.join(lines[1:]),lines[0]+source,''.join(lines[1:]+lines[:1])]:
                with self.assertRaises(AssertionError):pins.restore_search_first_restoration_exact_delta(name,foreign)
        with patch.object(pins.subprocess,'check_output',side_effect=AssertionError('unavailable-local-object')):
            with self.assertRaisesRegex(AssertionError,'parent_blob_unavailable'):
                pins.restore_search_first_restoration_exact_delta('instagram_navigation.py',(ROOT/'instagram_navigation.py').read_text())

    def test_unrelated_source_and_all_existing_Golden_test_nodes_are_byte_preserved(self):
        import test_certification_admission as pins
        for name in ('instagram_navigation.py','dm_sender_engine.py'):
            parent=subprocess.check_output(['git','show','b9a22c34b9d9efee7ab73faf9b4bd83ebe4e219c:'+name],cwd=ROOT,text=True)
            current=pins.restore_search_first_restoration_exact_delta(name,(ROOT/name).read_text())
            self.assertEqual(current,parent)
        name='tests/test_certification_admission.py'
        parent=subprocess.check_output(['git','show','b9a22c34b9d9efee7ab73faf9b4bd83ebe4e219c:'+name],cwd=ROOT,text=True)
        current=(ROOT/name).read_text()
        def classes(source):
            return {n.name:ast.get_source_segment(source,n) for n in ast.parse(source).body if isinstance(n,ast.ClassDef)}
        self.assertEqual(classes(parent),classes(current))

if __name__=='__main__':unittest.main()

