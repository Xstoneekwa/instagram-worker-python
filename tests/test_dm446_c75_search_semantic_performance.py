"""Actual bounded Search/composer/restore paths, offline transport only."""
from __future__ import annotations
import ast
import collections
import dataclasses
import hashlib
import json
import subprocess
import unittest
from types import SimpleNamespace
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import instagram_navigation as nav
import dm_sender_engine as engine
import unfollow_hybrid_strategy as search
import run_latency_observability_v1 as obs
import test_outreach_search_absence_v1 as absence
import test_dm446_search_budget_invite_restore as budget
import test_dm446_chronometric_effective_performance as chrono
import test_dm446_worker1083_residual_performance as residual

ROOT=Path(__file__).resolve().parents[1]
PARENT='c75b354ceff50828b0bb6ced225cc43cb6ca34d5'

def parent_source(name):
    return subprocess.check_output(['git','show',PARENT+':'+name],cwd=ROOT,text=True)

def functions(module,file,names,parent=False):
    source=parent_source(file) if parent else (ROOT/file).read_text()
    ns=dict(module.__dict__)
    nodes=[n for n in ast.parse(source).body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<exact-c75>' if parent else '<candidate>','exec'),ns)
    return ns

class SharedSearchTests(unittest.TestCase):
    def execute(self,frames=None,*,cost=.79,retype_cost=0.,deadline=12.,activity='com.instagram.mainactivity.InstagramMainActivity',capture=True,mutate=None,qualified=False):
        clock=budget.Clock();d=budget.SearchDevice(clock,frames,cost=cost,activity=activity)
        if qualified:
            import instagram_ui_446_compat as compat
            d.serial='offline-search-serial';d._instagram_search_observed_version=(id(d),d.serial,absence.PKG,*compat.VERSION)
            compat.retain_context(d,serial=d.serial,package=absence.PKG,version_name=compat.VERSION[0],version_code=compat.VERSION[1],unsafe=False)
        out={};buf=absence.recorder_buffer();retypes=[]
        def retype(*a,**k):retypes.append(clock.now);clock.now+=retype_cost;return True
        if mutate:mutate(d,clock)
        with ExitStack() as s:
            for m in (nav,search):
                s.enter_context(patch.object(m,'time',clock));s.enter_context(patch.object(m,'log'))
            s.enter_context(patch.object(nav._dm_retention,'_composer_buffer',buf))
            s.enter_context(patch.object(nav,'type_search',side_effect=retype))
            if not capture:s.enter_context(patch.object(nav._dm_retention,'capture_xml'))
            exact=nav._observe_outreach_missing_search(d,absence.QUERY,outcome=out,_shared_deadline=10.+deadline)
        return exact,out,d,clock,buf,retypes

    def test_two_positive_rounds_share_deadline_and_one_retype_including_io(self):
        exact,out,d,c,buf,r=self.execute(retype_cost=4.)
        self.assertFalse(exact);self.assertEqual(out['state'],'confirmed_absent')
        self.assertEqual([len(x) for x in out['proof'].rounds],[2,2]);self.assertEqual(len(r),1)
        self.assertEqual(d.reads,4);self.assertLess(c.now-10,12.)
        self.assertEqual(buf['native_xml'][0]['xml'],absence.xml())

    def test_delayed_exact_can_be_stably_proved_after_old_local_budget(self):
        frames=[absence.suggestion_xml()]*4+[absence.xml(label='',row=absence.QUERY)]*2
        exact,out,d,c,buf,r=self.execute(frames)
        self.assertTrue(exact);self.assertGreater(c.now-10,3.5);self.assertLess(c.now-10,12)
        self.assertEqual(d.reads,6);self.assertNotIn('proof',out);self.assertEqual(r,[])

    def test_stable_suggestions_are_not_positive_absence_or_a_retype_authority(self):
        exact,out,d,c,buf,r=self.execute([absence.suggestion_xml()],cost=.1)
        self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(out['state'],'unknown')
        self.assertGreater(d.reads,6);self.assertLessEqual(c.now-10,12.1);self.assertEqual(r,[])

    def test_neighbour_profiles_never_authorize_absence(self):
        exact,out,d,c,buf,r=self.execute([absence.xml(label='',row='different_profile')],cost=.1)
        self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(r,[])

    def test_loading_stays_nonterminal_and_bounded(self):
        exact,out,d,c,buf,r=self.execute([absence.xml(label='',extra='<node class="android.widget.ProgressBar"/>')])
        self.assertFalse(exact);self.assertNotIn('proof',out);self.assertLessEqual(len(r),1)
        self.assertLessEqual(c.now-10,12.8)

    def test_late_dump_is_retained_and_no_foreground_call_or_retype_follows(self):
        exact,out,d,c,buf,r=self.execute(cost=31.)
        self.assertFalse(exact);self.assertEqual(d.calls,['dump_hierarchy']);self.assertEqual(r,[])
        self.assertEqual(out['first_failure']['reason'],'search_observation_deadline_expired')
        self.assertTrue(out['first_failure']['xml_retained']);self.assertNotIn('proof',out)

    def test_expiry_during_only_retype_does_not_restart_budget(self):
        exact,out,d,c,buf,r=self.execute(retype_cost=12.)
        self.assertFalse(exact);self.assertEqual(len(r),1);self.assertEqual(d.reads,2)
        self.assertNotIn('proof',out);self.assertEqual(out['reason'],'search_observation_deadline_expired')

    def test_first_exception_preserves_exact_xml_and_typed_reason(self):
        exact,out,d,c,buf,r=self.execute(['<broken'])
        self.assertFalse(exact);self.assertEqual(d.reads,1);self.assertEqual(r,[])
        self.assertEqual(out['first_failure']['exception_class'],'ParseError')
        self.assertEqual(buf['native_xml'][0]['xml'],'<broken')

    def test_no_retention_ack_cannot_authorize_absence(self):
        exact,out,d,c,buf,r=self.execute(capture=False)
        self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(r,[])

    def test_wrong_package_query_or_activity_refuses_first_observation(self):
        for frames,change in [([absence.xml(query='foreign')],None),
                              ([absence.xml()],lambda d,c:d.current.update(package='foreign')),
                              ([absence.xml()],lambda d,c:d.current.update(activity='ModalActivity'))]:
            with self.subTest(frames=frames,change=change):
                exact,out,d,c,buf,r=self.execute(frames,mutate=change)
                self.assertFalse(exact);self.assertNotIn('proof',out);self.assertEqual(d.reads,1);self.assertEqual(r,[])

    def test_invalid_deadlines_refuse_without_io(self):
        for value in [True,float('nan'),float('inf'),23.]:
            d=budget.SearchDevice(budget.Clock())
            with patch.object(nav,'time',d.clock),self.assertRaises(ValueError):
                nav._observe_outreach_missing_search(d,absence.QUERY,outcome={},_shared_deadline=value)
            self.assertEqual(d.reads,0)

    def test_shared_budget_preserves_both_exact_MainTab_forms_and_refuses_unbound(self):
        for activity in ['.activity.MainTabActivity','com.instagram.android.activity.MainTabActivity']:
            exact,out,d,c,buf,r=self.execute(activity=activity,qualified=True)
            self.assertFalse(exact);self.assertEqual(out['state'],'confirmed_absent');self.assertEqual(len(r),1)
            exact,out,d,c,buf,r=self.execute(activity=activity)
            self.assertFalse(exact);self.assertEqual(out['reason'],'search_activity_unproved');self.assertEqual(d.reads,1)

    def test_default_unfollow_matches_exact_c75_calls_and_semantics(self):
        for frame in [absence.suggestion_xml(),absence.xml(),absence.xml(label='',row=absence.QUERY)]:
            results=[]
            for parent in (True,False):
                c=budget.Clock();d=budget.SearchDevice(c,[frame],cost=.1)
                ns=functions(search,'unfollow_hybrid_strategy.py',{'_wait_for_exact_search_result'},parent)
                ns.update(time=c,log=lambda *a,**k:None,_utc_now_iso=lambda:'offline-fixed-time')
                result=ns['_wait_for_exact_search_result'](d,absence.QUERY,elapsed_budget_s=3.5)
                results.append((result,d.calls,c.sleeps))
            self.assertEqual(results[0],results[1])

class EarlyNavigationTests(unittest.TestCase):
    def execute(self,parent=False,*,mode='mixed_results',qualified=True,fused=False,window_cost=0.,late_rescan=False):
        c=budget.Clock();d=budget.SearchDevice(c,[absence.suggestion_xml()],cost=.2)
        calls=[];out={}
        def dimensions():calls.append('window_size');c.now+=window_cost;return 1080,2340
        d.window_size=dimensions
        def hot(*a,**k):calls.append('hot');c.now+=.1;return None,None
        def scan(*a,**k):
            calls.append('rescan');c.now+=13. if late_rescan else .1
            return object() if late_rescan else None
        def retry(fn,*,timeout_s,**kw):
            end=c.now+timeout_s
            while c.now<end:
                result=fn()
                if result:return result
                c.sleep(.1)
            return None
        ns=functions(nav,'instagram_navigation.py',{'tap_account_result'},parent)
        ns.update(time=c,log=lambda *a,**k:None,_follow_ct_search_active=lambda **k:False,
                  _dm_446_context=lambda d:SimpleNamespace(package=absence.PKG) if qualified else None,
                  get_search_ui_mode=lambda:mode,_peek_pending_fused_fast_ime_row=lambda u:object() if fused else None,
                  _clear_pending_fused_fast_ime_row=lambda:calls.append('clear_fused'),
                  find_first_row_search_username_hot=hot,find_real_account_text_element=scan,
                  retry_until_jitter=retry,_dump_no_real_account_row_debug=lambda *a:calls.append('debug'))
        if late_rescan:
            ns['_observe_outreach_missing_search']=lambda *a,**k:True
        with ExitStack() as s:
            for m in (nav,search):
                s.enter_context(patch.object(m,'time',c));s.enter_context(patch.object(m,'log'))
            s.enter_context(patch.object(nav._dm_retention,'_composer_buffer',absence.recorder_buffer()))
            s.enter_context(patch.object(nav,'type_search',side_effect=AssertionError('unexpected_retype')))
            result=ns['tap_account_result'](d,absence.QUERY,outreach_search_context=True,outreach_search_outcome=out)
        return result,out,d,c,calls

    def test_real_navigation_uses_one_hot_probe_then_semantic_observation(self):
        old=self.execute(True);new=self.execute()
        self.assertFalse(old[0]);self.assertFalse(new[0])
        self.assertGreater(old[4].count('hot'),10);self.assertEqual(new[4].count('hot'),1)
        self.assertGreater(old[4].count('rescan'),0);self.assertEqual(new[4].count('rescan'),0)
        self.assertNotIn('proof',new[1]);self.assertLessEqual(new[3].now-10,12.3)

    def test_accounts_tab_and_stale_fused_hint_cannot_restart_a_selector_loop(self):
        for mode in ['mixed_results','accounts_tab']:
            result,out,d,c,calls=self.execute(mode=mode,fused=True)
            self.assertFalse(result);self.assertEqual(calls.count('hot'),1);self.assertEqual(calls.count('clear_fused'),1)
            self.assertNotIn('proof',out)

    def test_io_exhausting_budget_prevents_any_next_probe(self):
        result,out,d,c,calls=self.execute(window_cost=13.)
        self.assertFalse(result);self.assertEqual(calls,['window_size']);self.assertEqual(d.reads,0)
        self.assertEqual(out['reason'],'search_observation_deadline_expired')

    def test_late_exact_rescan_never_authorizes_a_tap(self):
        result,out,d,c,calls=self.execute(late_rescan=True)
        self.assertFalse(result);self.assertEqual(calls.count('hot'),1);self.assertEqual(calls.count('rescan'),1)
        self.assertEqual(out['reason'],'search_observation_deadline_expired');self.assertNotIn('proof',out)

    def test_unqualified_legacy_navigation_keeps_c75_call_order(self):
        old=self.execute(True,qualified=False);new=self.execute(qualified=False)
        self.assertEqual(old[4],new[4]);self.assertEqual(old[2].calls,new[2].calls);self.assertEqual(old[1],new[1])

class ImmediateComposerTests(unittest.TestCase):
    def setUp(self):
        self.h=chrono.EffectivePerformanceTests('runTest');self.h.setUp();self.addCleanup(self.h.doCleanups)

    def binding(self):
        b=absence.recorder_buffer();b.update(job_id='job',context=dict(package=chrono.P,account_id='account',run_id='run'),records=[],invocations=0)
        return b

    def qualified(self):
        d=chrono.Transport();d.clock=self.h.clock
        return d,obs.instrument_device(d,observe_properties=True)

    def issue(self,d):
        read={};ok,reason=nav.verify_dm_composer_safe(d,chrono.P,_pretyping_readback=read)
        self.assertTrue(ok,reason);self.assertTrue(read['read_succeeded'])
        return read['_immediate_composer']

    def test_actual_sender_keeps_four_reads_gate_and_one_tap_with_fewer_qualifications(self):
        results=[]
        for reuse in (False,True):
            with patch.object(nav._dm_retention,'_composer_buffer',self.binding()), \
                 obs.dm446_span('offline',identity=dict(run_id='run',job_id='job',account_id='account')):
                with ExitStack() as s:
                    if not reuse:s.enter_context(patch.object(nav,'_consume_outreach446_pretyping_composer',return_value=None))
                    result=self.h.run_sender(instrumented=True)
            self.assertTrue(result['result'][0]);self.assertEqual(result['calls'].count('get_text'),4)
            self.assertEqual(result['calls'].count('tap'),1)
            self.assertLess(result['calls'].index('fresh_gate'),result['calls'].index('tap'))
            results.append(result)
        old,new=results
        self.assertEqual(old['strict']-new['strict'],1)
        self.assertEqual(old['xml']-new['xml'],2)
        self.assertEqual(old['app_current']-new['app_current'],4)
        self.assertEqual(old['window_size']-new['window_size'],2)
        self.assertEqual(collections.Counter(old['calls'])-collections.Counter(new['calls']),collections.Counter({'count':1,'info':1}))

    def test_packet_is_single_use_and_bound_to_current_real_reader(self):
        raw,d=self.qualified()
        with patch.object(nav._dm_retention,'_composer_buffer',self.binding()), \
             obs.dm446_span('offline',identity=dict(run_id='run',job_id='job',account_id='account')):
            packet=self.issue(d);reads=raw.reads
            self.assertIs(nav._consume_outreach446_pretyping_composer(d,chrono.P,packet),packet.composer)
            self.assertIsNone(nav._consume_outreach446_pretyping_composer(d,chrono.P,packet))
            self.assertEqual(raw.reads,reads)

    def test_stale_foreign_changed_or_mutated_contexts_invalidate_and_fall_back(self):
        cases=['age','device','package','job','buffer','generation','context','epoch','forged']
        for case in cases:
            with self.subTest(case=case),ExitStack() as s:
                raw,d=self.qualified();buf=self.binding()
                s.enter_context(patch.object(nav._dm_retention,'_composer_buffer',buf))
                s.enter_context(obs.dm446_span('offline',identity=dict(run_id='run',job_id='job',account_id='account')))
                packet=self.issue(d);pkg=chrono.P;subject=d
                if case=='age':self.h.clock.now+=.251
                if case=='device':subject=self.qualified()[1]
                if case=='package':pkg='foreign'
                if case=='job':buf['job_id']='foreign'
                if case=='buffer':nav._dm_retention._composer_buffer=self.binding()
                if case=='generation':obs._state().dm446_generation+=1
                if case=='context':s.enter_context(patch.object(nav,'_dm_446_context',return_value=None))
                if case=='epoch':s.enter_context(patch.object(nav.ui_continuity_v1,'require_epoch',side_effect=ValueError('changed')))
                if case=='forged':packet=dataclasses.replace(packet)
                self.assertIsNone(nav._consume_outreach446_pretyping_composer(subject,pkg,packet))

    def test_typing_stale_packet_runs_real_finder_and_preserves_integral_readback(self):
        raw,d=self.qualified()
        with patch.object(nav._dm_retention,'_composer_buffer',self.binding()), \
             obs.dm446_span('offline',identity=dict(run_id='run',job_id='job',account_id='account')):
            packet=self.issue(d);self.h.clock.now+=.251
            with patch.object(nav,'_dm_find_focus_composer',wraps=nav._dm_find_focus_composer) as finder:
                ok,detail=nav.type_dm_draft_only(d,chrono.LONG,chrono.P,_outreach_446_readback=True,_outreach_446_pretyping=packet)
            self.assertTrue(ok,detail);self.assertGreater(finder.call_count,0);self.assertEqual(raw.text,chrono.LONG)

    def test_partial_read_exception_wrong_identity_or_gate_never_tap(self):
        for kwargs in [dict(partial=True),dict(read_error=True),dict(identity=False,kind='welcome'),dict(gate_blocked=True)]:
            with self.subTest(kwargs=kwargs),patch.object(nav._dm_retention,'_composer_buffer',self.binding()), \
                 obs.dm446_span('offline',identity=dict(run_id='run',job_id='job',account_id='account')):
                r=self.h.run_sender(instrumented=True,**kwargs)
                self.assertNotIn('tap',r['calls'])

class RestoreOrderingTests(unittest.TestCase):
    def execute(self,parent=False,*,version=446,proof=True,permission=False):
        d=residual.RestoreDevice(residual.portable_xml(residual.NATIVE[0]),version)
        ns=functions(engine,'dm_sender_engine.py',{'_safe_teardown_navigation','prepare_dm_sender_global_search_surface'},parent)
        def check(*a,**k):d.calls.append('permission_veto');return permission
        def profile(*a,**k):d.calls.append('exact_profile');return d.stage=='profile'
        def tap(*a,**k):d.to_profile();return True,'exact_action_bar'
        ns.update(_check_dm_sender_permission_blocker=check,verify_profile=profile,
                  tap_instagram_action_bar_back_button=tap,time=d.clock,log=lambda *a,**k:None,
                  outreach_446_restoration_thread_proved=nav.outreach_446_restoration_thread_proved if proof else lambda *a,**k:False)
        with patch.object(nav,'time',d.clock),patch.object(nav,'log'),patch.object(nav._dm_retention,'capture_xml'):
            ns['_safe_teardown_navigation'](d,'fixture_recipient',pkg=chrono.P,account_username='emitter',prefer_back_stack_to_search=True)
        return d,engine._get_dm_sender_post_job_restore()

    def test_current_profile_precedes_negative_search_preserving_gestures_and_final_proof(self):
        old,os=self.execute(True);new,ns=self.execute()
        self.assertEqual(old.stage,new.stage);self.assertEqual(new.stage,'search')
        self.assertEqual([c for c in old.calls if c.endswith('_back')],[c for c in new.calls if c.endswith('_back')])
        self.assertEqual(new.calls.count('permission_veto'),1)
        self.assertEqual(old.calls.count('wait')-new.calls.count('wait'),7)
        self.assertEqual(old.calls.count('app_current')-new.calls.count('app_current'),1)
        self.assertEqual({k:v for k,v in os.items() if not k.endswith('_ms')},{k:v for k,v in ns.items() if not k.endswith('_ms')})

    def test_372_unversioned_and_unproved_thread_keep_exact_c75_sequence(self):
        for kwargs in [dict(version=372),dict(version=0),dict(proof=False)]:
            old,os=self.execute(True,**kwargs);new,ns=self.execute(**kwargs)
            self.assertEqual(old.calls,new.calls);self.assertEqual(os,ns)

    def test_permission_veto_precedes_any_observation_or_gesture(self):
        d,state=self.execute(permission=True)
        self.assertEqual(d.calls,['permission_veto']);self.assertEqual(state['post_job_restore_final_reason'],'permission_blocker')

class NativeCostFixtureTests(unittest.TestCase):
    def test_exact_new_inverse_restores_full_parent_and_rejects_foreign_bytes(self):
        import base64,zlib
        from tests import test_certification_admission as pins
        rows=json.loads(zlib.decompress(base64.b64decode(pins._C75_EARLY_SEARCH_EXACT_INVERSE)))
        for name,row in rows.items():
            raw=(ROOT/name).read_text();parent=parent_source(name)
            self.assertEqual(pins.restore_c75_early_search_exact_delta(name,raw),parent)
            self.assertEqual(pins.restore_c75_early_search_exact_delta(name,parent),parent)
            h=next(h for h in row['hunks'] if h['new']);at=h['offset']
            bad=[raw+'\n',raw[:at]+raw[at+len(h['new']):],raw[:at]+h['new']+raw[at:],raw[:at]+'#foreign\n'+raw[at:],raw[:at]+raw[at+len(h['new']):]+h['new']]
            for source in bad:
                with self.assertRaises(AssertionError):pins.restore_c75_early_search_exact_delta(name,source)

    def test_all_prior_inverse_constants_and_pure_classifier_are_unchanged(self):
        def constants(src):
            return {n.targets[0].id:ast.dump(n.value,include_attributes=False) for n in ast.parse(src).body
                    if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and 'EXACT_INVERSE' in n.targets[0].id}
        old=constants(parent_source('tests/test_certification_admission.py'));new=constants((ROOT/'tests/test_certification_admission.py').read_text())
        self.assertTrue(old)
        retired = '_PERSISTENT_CERTIFICATION_EXACT_INVERSE'
        self.assertEqual(set(old) - set(new), {retired})
        self.assertTrue(all(new[k] == v for k, v in old.items() if k != retired))
        # The only retired byte-serialized inverse now references local immutable
        # objects. Preserve the same input/output hashes and exact parent source.
        def persistent(src):
            node = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign)
                        and isinstance(n.targets[0], ast.Name) and n.targets[0].id == retired)
            return json.loads(zlib.decompress(base64.b64decode(ast.literal_eval(node.value))))
        import base64, zlib
        previous = persistent(parent_source('tests/test_certification_admission.py'))
        from tests import test_certification_admission as pins
        self.assertEqual(set(previous), set(pins._PERSISTENT_CERTIFICATION_EXACT_REFERENCES))
        for name, before in previous.items():
            current = pins._PERSISTENT_CERTIFICATION_EXACT_REFERENCES[name]
            self.assertEqual({k: current[k] for k in ('base_commit','base_sha256','candidate_sha256')},
                             {k: before[k] for k in ('base_commit','base_sha256','candidate_sha256')})
            oid = subprocess.check_output(['git','rev-parse',before['base_commit'] + ':' + name],cwd=ROOT,text=True).strip()
            self.assertEqual(current['parent_blob_oid'], oid)
        def classify(src):return ast.dump(next(n for n in ast.parse(src).body if isinstance(n,ast.FunctionDef) and n.name=='classify_search_surface_xml'),include_attributes=False)
        self.assertEqual(classify(parent_source('unfollow_hybrid_strategy.py')),classify((ROOT/'unfollow_hybrid_strategy.py').read_text()))

    def test_fixture_is_historical_cost_not_candidate_physical_gain(self):
        f=json.loads((ROOT/'tests/fixtures/dm446_c75_native_search_costs_v1.json').read_text())
        self.assertEqual(f['physical_gain_seconds'],None);self.assertFalse(f['target_30_dm_40_min_certified'])
        self.assertEqual(f['removed_calls_per_eligible_send'],18)
        self.assertEqual(f['removed_xml_dumps_per_eligible_send'],2)
        self.assertEqual(f['four_integral_text_reads_preserved'],4)

if __name__=='__main__':unittest.main()
