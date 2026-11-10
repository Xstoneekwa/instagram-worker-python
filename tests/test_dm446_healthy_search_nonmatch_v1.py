"""Healthy nonmatch business decision: actual existing readers, synthetic UI/clock only."""
from __future__ import annotations
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock,patch
import hashlib,json,xml.etree.ElementTree as ET
import instagram_navigation as nav
import dm_sender_engine as engine
import run_latency_observability_v1 as latency
import outreach_slot_contract as slots
import unfollow_hybrid_strategy as search
from test_dm446_search_budget_invite_restore import Clock
from test_outreach_search_absence_v1 import recorder_buffer
from test_dm446_worker1083_residual_performance import RestoreDevice
F=json.loads((Path(__file__).parent/'fixtures/dm446_healthy_search_nonmatch_v1.json').read_text());P=F['package'];Q=F['query']
class Device(RestoreDevice):
    def __init__(self,frames=None,*,cost=.2):
        super().__init__(F['xml']['suggestion']);self.clock=Clock();self.activity='.activity.MainTabActivity';self.stage='search';self.frames=frames or [F['xml']['suggestion']];self.index=0;self.cost=cost;self.mutate=None
    def dump_hierarchy(self,**kw):
        self.calls.append('dump_hierarchy');self.clock.now+=self.cost
        f=self.frames[min(self.index,len(self.frames)-1)];self.index+=1
        if self.mutate:self.mutate(self)
        if isinstance(f,Exception):raise f
        return f(self) if callable(f) else f
    def app_current(self):
        self.calls.append('app_current');self.clock.now+=self.cost
        return dict(package=P,activity=self.activity)
class HealthyNonmatchTests(TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close);self.d=Device()
        self.job=dict(id='synthetic-job',account_id='synthetic-account',campaign_id='synthetic-campaign',dm_type='outreach',recipient_username=Q,status='reserved',attempts=0,max_attempts=3,reserved_by='synthetic-request',reserved_at='synthetic-reservation',sent_at=None,message_body='Synthetic long draft '+('😀🧪x'*280),metadata=dict(origin='CLIENT_LIST',managed_campaign=True,client_id='synthetic-client',campaign_revision=1,outreach_request_id='synthetic-request',outreach_slot_id='synthetic-slot',outreach_send_state='before_send'))
        self.context=dict(account_id=self.job['account_id'],run_id='synthetic-run',request_id='synthetic-request',device_id='synthetic-device',app_instance_id='synthetic-instance',serial=self.d.serial,package=P,version_name='446.0.0.49.77',version_code=385211304)
        self.buf=recorder_buffer();self.buf.update(context=self.context,job_id=self.job['id'])
        self.slot=SimpleNamespace(context=dict(managed_campaign=True,account_id=self.job['account_id'],client_id='synthetic-client',campaign_id=self.job['campaign_id'],campaign_revision=1,request_id='synthetic-request',slot_id='synthetic-slot',device_id='synthetic-device',app_instance_id='synthetic-instance',adb_serial=self.d.serial,package_name=P),device_verified=True,finished=False,stop_reason=None,remaining_ms=lambda:60000,check=Mock())
        self.metric=dict(account_id=self.job['account_id'],run_id='synthetic-run',job_id=self.job['id'],ui_generation=2)
        for m in (nav,engine,search):self.stack.enter_context(patch.object(m,'time',self.d.clock));self.stack.enter_context(patch.object(m,'log'))
        self.stack.enter_context(patch.object(nav._dm_retention,'_composer_buffer',self.buf))
        self.stack.enter_context(patch.object(latency,'current_context',side_effect=lambda:dict(self.metric)))
        self.stack.enter_context(patch.object(slots,'active',side_effect=lambda:self.slot))
        self.retype=self.stack.enter_context(patch.object(nav,'type_search',side_effect=AssertionError('NO_RETYPE_NEEDED')))
    def observe(self,kind='suggestion',*,frames=None,cost=None,deadline=12):
        self.d.frames=frames or [F['xml'][kind]];self.d.index=0
        if cost is not None:self.d.cost=cost
        out={};nav.begin_outreach_search_restoration(self.d,self.job,out,account_id=self.job['account_id'],sender='synthetic-sender',run_id='synthetic-run')
        start=self.d.clock.now;exact=nav._observe_outreach_missing_search(self.d,Q,outcome=out,_shared_deadline=start+deadline-1e-8)
        return exact,out,self.d.clock.now-start
    def test_suggestions_are_terminal_only_after_full_qualified_stabilization(self):
        exact,out,elapsed=self.observe();self.assertFalse(exact);self.assertEqual(out['state'],'not_found_in_search');self.assertGreaterEqual(out['proof'].stable_duration_ms,3500);self.assertGreaterEqual(elapsed,3.5);self.assertLess(elapsed,12);self.retype.assert_not_called()
        self.assertEqual(len(out['proof'].samples),2);self.assertEqual([r['label'] for r in self.buf['native_xml']][-2:],['outreach_search_nonmatch_terminal_1','outreach_search_nonmatch_terminal_2'])
    def test_empty_and_multiple_neighbours_are_distinct_healthy_terminal_shapes(self):
        for kind in ('empty','neighbours'):
            with self.subTest(kind=kind):
                exact,out,_=self.observe(kind);self.assertFalse(exact);self.assertEqual(out['state'],'not_found_in_search');self.assertTrue(out['proof'].samples)
    def test_exact_profile_wins_immediately_and_is_never_nonmatch(self):
        exact,out,_=self.observe('exact');self.assertTrue(exact);self.assertNotIn('proof',out)
    def test_late_exact_during_stabilization_is_preserved(self):
        exact,out,_=self.observe(frames=[F['xml']['suggestion']]*5+[F['xml']['exact']]*2);self.assertTrue(exact);self.assertNotIn('proof',out)
    def test_single_late_exact_sighting_vetoes_terminal_absence(self):
        exact,out,_=self.observe(frames=[F['xml']['suggestion']]*3+[F['xml']['exact']]+[F['xml']['suggestion']]*10);self.assertFalse(exact);self.assertNotIn('proof',out)
    def test_loading_blank_invalid_XML_and_IO_never_terminalize(self):
        for frame in (F['xml']['loading'],F['xml']['blank'],'<broken',RuntimeError('synthetic-IO')):
            with self.subTest(frame=str(frame)[:25]):
                exact,out,_=self.observe(frames=[frame]);self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(out['state'],'unknown')
    def test_inflight_deadline_overrun_is_unknown_even_with_nonmatch_XML(self):
        exact,out,_=self.observe(cost=13);self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(out['reason'],'search_observation_deadline_expired')
    def test_insufficient_window_never_uses_early_stable_verdict_as_authority(self):
        exact,out,_=self.observe(deadline=2);self.assertFalse(exact);self.assertNotIn('proof',out)
    def test_wrong_activity_or_package_cannot_mint_business_proof(self):
        self.d.activity='foreign.Activity';exact,out,_=self.observe();self.assertFalse(exact);self.assertNotIn('proof',out)
    def test_populated_unknown_popup_consent_or_challenge_fails_closed(self):
        for text in ('Confirm you are human','Choose if we process your data for ads','Restriction','Unknown sheet'):
            root=ET.fromstring(F['xml']['suggestion']);container=next(n for n in root.iter() if n.get('resource-id')==P+':id/modal_container');ET.SubElement(container,'node',{'package':P,'class':'android.app.Dialog','text':text})
            exact,out,_=self.observe(frames=[ET.tostring(root,encoding='unicode')]);self.assertFalse(exact);self.assertNotIn('proof',out)
    def test_no_results_label_with_canonical_exact_row_is_contradictory_not_terminal(self):
        root=ET.fromstring(F['xml']['exact']);rows=next(n for n in root.iter() if n.get('resource-id')==P+':id/recycler_view');ET.SubElement(rows,'node',{'package':P,'class':'android.widget.TextView','text':'No results'})
        exact,out,_=self.observe(frames=[ET.tostring(root,encoding='unicode')]);self.assertFalse(exact);self.assertNotIn('proof',out)
    def test_generation_drift_during_observation_invalidates_old_samples(self):
        self.d.mutate=lambda d:self.metric.update(ui_generation=3) if d.index==3 else None
        exact,out,_=self.observe();self.assertFalse(exact);self.assertNotIn('proof',out)
    def test_recorder_refusal_cannot_become_terminal(self):
        with patch.object(nav._dm_retention,'capture_xml'):
            exact,out,_=self.observe();self.assertFalse(exact);self.assertNotIn('proof',out)
    def complete(self,out):
        def ack(job_id,status,**kw):return dict(self.job,status=status,skip_reason=kw['skip_reason'],next_retry_at=None,metadata={**self.job['metadata'],**kw['metadata_patch']})
        with patch.object(engine.supabase_client,'complete_dm_job',side_effect=ack) as rpc:
            row=engine._complete_confirmed_search_absence(self.d,self.job,out,account_id=self.job['account_id'],run_id='synthetic-run',pkg=P)
        return row,rpc
    def test_one_canonical_skip_with_distinct_reason_no_attempt_or_Send(self):
        _,out,_=self.observe();row,rpc=self.complete(out);self.assertEqual(row['status'],'skipped');self.assertEqual(row['skip_reason'],'not_found_in_search');self.assertEqual(row['attempts'],0);self.assertIsNone(row['sent_at']);self.assertEqual(rpc.call_count,1);self.assertFalse(rpc.call_args.kwargs['increment_attempt'])
        evidence=row['metadata']['search_nonmatch_v1'];self.assertEqual(evidence['scope'],'campaign_only');self.assertEqual(evidence['classification'],'not_found_in_search');self.assertEqual(evidence['message_sha256'],hashlib.sha256(self.job['message_body'].encode()).hexdigest());self.assertNotIn('proof',out)
        row,rpc=self.complete(out);self.assertIsNone(row);rpc.assert_not_called()
    def test_uncertain_persistence_stops_without_second_write(self):
        _,out,_=self.observe()
        with patch.object(engine.supabase_client,'complete_dm_job',return_value=None) as rpc:
            with self.assertRaises(slots.SlotBlocked):engine._complete_confirmed_search_absence(self.d,self.job,out,account_id=self.job['account_id'],run_id='synthetic-run',pkg=P)
        self.assertEqual(rpc.call_count,1);self.assertNotIn('proof',out)
    def test_stale_or_changed_job_binding_rejects_before_RPC(self):
        _,out,_=self.observe();self.job['campaign_id']='foreign'
        with patch.object(engine.supabase_client,'complete_dm_job') as rpc:
            self.assertIsNone(engine._complete_confirmed_search_absence(self.d,self.job,out,account_id=self.job['account_id'],run_id='synthetic-run',pkg=P));rpc.assert_not_called()
    def test_UI_generation_change_after_proof_prevents_persistence(self):
        _,out,_=self.observe();self.metric['ui_generation']=3;row,rpc=self.complete(out);self.assertIsNone(row);rpc.assert_not_called()
    def test_old_legacy_proof_type_and_reason_are_not_relabelled(self):
        self.assertIsNot(nav.OutreachSearchNonmatchProof,nav.OutreachSearchAbsenceProof)
    def test_foreign_package_never_mints_nonmatch(self):
        with patch.object(self.d,'app_current',return_value=dict(package='foreign.package',activity=self.d.activity)):
            exact,out,_=self.observe()
        self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(out['state'],'unknown')
    def test_exhausted_attempt_or_wrong_reservation_cannot_be_reclassified(self):
        for change in (dict(status='failed',attempts=3),dict(reserved_by='foreign'),dict(reserved_at=None),dict(max_attempts=4)):
            with self.subTest(change=change):
                original=dict(self.job);_,out,_=self.observe();self.job.update(change)
                row,rpc=self.complete(out);self.assertIsNone(row);rpc.assert_not_called();self.assertEqual(out['state'],'unknown')
                self.job.clear();self.job.update(original)
    def test_changed_serial_buffer_version_or_slot_cannot_consume_proof(self):
        for changed in ('serial','buffer','version','slot'):
            with self.subTest(changed=changed):
                _,out,_=self.observe()
                with ExitStack() as guard:
                    if changed=='serial':guard.enter_context(patch.object(self.d,'serial','foreign'))
                    elif changed=='buffer':guard.enter_context(patch.object(nav._dm_retention,'_composer_buffer',{**self.buf}))
                    elif changed=='version':guard.enter_context(patch.object(nav,'_dm_446_context',return_value=replace(nav._dm_446_context(self.d))))
                    else:guard.enter_context(patch.object(slots,'active',return_value=SimpleNamespace(**vars(self.slot))))
                    row,rpc=self.complete(out)
                self.assertIsNone(row);rpc.assert_not_called();self.assertEqual(out['state'],'unknown')
    def test_permission_veto_is_fresh_and_prevents_completion(self):
        _,out,_=self.observe();self.slot.check.side_effect=slots.SlotBlocked('synthetic_permission_revoked')
        with patch.object(engine.supabase_client,'complete_dm_job') as rpc:
            with self.assertRaises(slots.SlotBlocked):engine._complete_confirmed_search_absence(self.d,self.job,out,account_id=self.job['account_id'],run_id='synthetic-run',pkg=P)
        rpc.assert_not_called();self.assertEqual(out['state'],'unknown')
    def test_expired_proof_and_removed_admission_remain_UNKNOWN(self):
        _,out,_=self.observe();self.d.clock.now+=3.51;row,rpc=self.complete(out)
        self.assertIsNone(row);rpc.assert_not_called();self.assertEqual(out['state'],'unknown')
        self.job['metadata']['origin']='foreign';exact,out,_=self.observe()
        self.assertFalse(exact);self.assertNotIn('proof',out)
    def test_real_executor_terminalizes_once_restores_Search_and_allows_next_query(self):
        self.job['recipient_username']=Q
        def navigate(d,recipient,**kw):
            out=kw['outreach_search_outcome']
            nav.begin_outreach_search_restoration(d,kw['_search_restore_job'],out,account_id=kw['account_id'],sender=kw['account_username'],run_id=kw['run_id'])
            nav._observe_outreach_missing_search(d,Q,outcome=out,_shared_deadline=d.clock.now+12-1e-8)
            out['_search_restoration'].no_profile_entry=True
            return 'unknown',False
        def ack(job_id,status,**kw):
            return dict(self.job,status=status,skip_reason=kw['skip_reason'],next_retry_at=None,metadata={**self.job['metadata'],**kw['metadata_patch']})
        with patch.object(engine,'_navigate_to_recipient_dm_thread',side_effect=navigate),patch.object(engine.supabase_client,'mark_dm_job_running',return_value=self.job),patch.object(engine.supabase_client,'complete_dm_job',side_effect=ack) as rpc,patch.object(engine,'_check_dm_sender_permission_blocker',return_value=False),patch.object(engine,'_perform_real_welcome_dm_send',side_effect=AssertionError('NO_SEND_ON_NONMATCH')),patch.object(engine,'_complete_job_failed_retry',side_effect=AssertionError('NO_RETRY_ON_TERMINAL')):
            result=engine.execute_dm_job_real_send(self.d,self.job,settings={},account_id=self.job['account_id'],account_username='synthetic-sender',run_id='synthetic-run')
        self.assertEqual(result['outcome'],'skipped');self.assertEqual(result['skip_reason_candidate'],'not_found_in_search');self.assertEqual(rpc.call_count,1)
        self.assertTrue(engine._dm_sender_trust_global_search_ready('synthetic-sender'))
        self.metric['job_id']='synthetic-next-job';self.buf['job_id']='synthetic-next-job'
        with patch.object(engine,'_check_dm_sender_permission_blocker',return_value=False),patch.object(engine,'is_dm_thread_screen',return_value=False),patch.object(engine,'_open_search_with_recovery',side_effect=AssertionError('REDUNDANT_SEARCH_OPEN')),patch.object(engine,'type_search',return_value=True) as typed,patch.object(engine,'get_perf_snapshot',return_value={}),patch.object(engine.config,'FAST_SKIP_ACCOUNTS_TAB',True),patch.object(engine.config,'FAST_PATH_MODE',True),patch.object(engine,'tap_account_result',return_value=False):
            state,ok=engine._navigate_to_recipient_dm_thread(self.d,'synthetic_next_exact',pkg=P,account_id=self.job['account_id'],account_username='synthetic-sender',run_id='synthetic-run',dm_type='outreach',previous_username=Q,outreach_search_outcome={})
        self.assertFalse(ok);self.assertEqual(state,'unknown');self.assertEqual(typed.call_args.args[1],'synthetic_next_exact')
    def test_actual_no_row_exit_records_healthy_business_proof_and_restoration_scope(self):
        out={};nav.begin_outreach_search_restoration(self.d,self.job,out,account_id=self.job['account_id'],sender='synthetic-sender',run_id='synthetic-run')
        with patch.object(nav,'_dump_no_real_account_row_debug'),patch.object(nav,'_peek_pending_fused_fast_ime_row',return_value=None):
            nav.set_search_ui_mode('mixed_results')
            answer=nav.tap_account_result(self.d,Q,outreach_search_context=True,outreach_search_outcome=out)
        self.assertFalse(answer);self.assertEqual(out['state'],'not_found_in_search');self.assertIsInstance(out['proof'],nav.OutreachSearchNonmatchProof)
        self.assertTrue(out['_search_restoration'].no_profile_entry);self.assertTrue(out['_search_restoration'].eligible)

class HealthyNonmatchPreservationTests(TestCase):
    def test_exact_frozen_inverse_and_foreign_bytes_rejection(self):
        import test_certification_admission as pins,subprocess
        root=Path(__file__).resolve().parents[1]
        for name in ('instagram_navigation.py','dm_sender_engine.py'):
            source=(root/name).read_text();parent=subprocess.check_output(['git','show','HEAD:'+name],cwd=root)
            self.assertEqual(pins.restore_healthy_nonmatch_exact_delta(name,source).encode(),parent)
            self.assertEqual(pins.restore_healthy_nonmatch_exact_delta(name,parent.decode()).encode(),parent)
            lines=source.splitlines(keepends=True)
            for foreign in (source+'\n# foreign\n',''.join(lines[1:]),lines[0]+source,''.join(lines[1:]+lines[:1])):
                with self.assertRaises(AssertionError):pins.restore_healthy_nonmatch_exact_delta(name,foreign)
    def test_A0_Golden_and_existing_admission_test_classes_are_identical(self):
        import ast,subprocess
        root=Path(__file__).resolve().parents[1]
        name='unfollow_hybrid_strategy.py'
        self.assertEqual((root/name).read_bytes(),subprocess.check_output(['git','show','HEAD:'+name],cwd=root))
        name='tests/test_certification_admission.py';old=subprocess.check_output(['git','show','HEAD:'+name],cwd=root).decode();now=(root/name).read_text()
        def classes(source):return {n.name:ast.get_source_segment(source,n) for n in ast.parse(source).body if isinstance(n,ast.ClassDef)}
        self.assertEqual(classes(now),classes(old))
