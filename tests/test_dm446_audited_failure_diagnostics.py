"""Offline authoritative failure diagnostics; no phone or Backend access."""
from __future__ import annotations
import ast
import copy
import hashlib
import subprocess
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import dm_sender_engine as engine
from outreach_slot_contract import SlotBlocked
import test_dm446_search_budget_invite_restore as budget
import test_outreach_search_absence_v1 as absence

ROOT=Path(__file__).resolve().parents[1]
PARENT='3b95204fc7dc46819b43b39a17e89b5cae5fe8b0'

def job():
    return dict(id='synthetic-job',account_id='synthetic-account',dm_type='outreach',
                recipient_username='synthetic_recipient',attempts=2,max_attempts=3,metadata={})
def terminal(j):
    return dict(j,status='failed',attempts=3,finished_at='2030-01-01T00:00:00Z',
                next_retry_at=None,sent_at=None,last_error='navigation_failed',
                metadata=dict(search_failure_reason='search_observation_deadline_expired',search_failure_state='unknown'))

class CompletionDiagnosticsTests(unittest.TestCase):
    def call(self,j,row,parent=False):
        fn=engine._complete_job_failed_retry
        if parent:
            source=subprocess.check_output(['git','show',PARENT+':dm_sender_engine.py'],cwd=ROOT,text=True)
            node=next(x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name==fn.__name__)
            ns=dict(engine.__dict__);exec(compile(ast.Module(body=[node],type_ignores=[]),'<signed-parent>','exec'),ns);fn=ns[fn.__name__]
        with patch.object(engine.supabase_client,'complete_dm_job',return_value=row) as rpc,patch.object(engine,'log') as logs:
            result=fn(j,last_error='navigation_failed',thread_state='unknown')
        rpc.assert_called_once_with(j['id'],'failed',last_error='navigation_failed',increment_attempt=True,
                                    retry_delay_seconds=300,metadata_patch={'thread_state':'unknown'})
        return result,logs

    def test_same_canonical_write_parent_false_retry_candidate_terminal(self):
        j=job();row=terminal(j);before=copy.deepcopy((j,row))
        old,_=self.call(j,row,True);new,logs=self.call(j,row)
        self.assertEqual(old,(row,'failed_retry'));self.assertEqual(new,(row,'failed_terminal'))
        self.assertEqual((j,row),before)
        self.assertEqual(logs.call_args.args[1],'dm_sender_job_failed_exhausted')
        self.assertFalse(logs.call_args.kwargs['retry_scheduled'])
        self.assertNotIn('retry_delay_seconds',logs.call_args.kwargs)

    def test_retry_still_requires_actual_pending_next_retry_and_remaining_attempt(self):
        j=job();j['attempts']=1
        row=dict(terminal(j),status='pending',attempts=2,finished_at=None,next_retry_at='2030-01-01T00:05:00Z')
        self.assertEqual(self.call(j,row)[0],(row,'failed_retry'))

    def test_ambiguous_foreign_quarantined_or_contradictory_ack_stops_once(self):
        j=job();valid=terminal(j)
        bad=[None,[],{},dict(valid,id='foreign'),dict(valid,account_id='foreign'),dict(valid,status='sent'),
             dict(valid,attempts=2),dict(valid,attempts=4,max_attempts=4),dict(valid,attempts=True),dict(valid,max_attempts='3'),
             dict(valid,next_retry_at='unexpected'),dict(valid,finished_at=None),dict(valid,sent_at='unexpected'),
             dict(valid,metadata={'automatic_retry_blocked':True}),dict(valid,metadata='foreign')]
        for row in bad:
            with self.subTest(row=row),patch.object(engine.supabase_client,'complete_dm_job',return_value=row) as rpc,patch.object(engine,'log') as logs:
                with self.assertRaisesRegex(SlotBlocked,'outreach_failure_completion_unproved'):
                    engine._complete_job_failed_retry(j,last_error='navigation_failed')
                rpc.assert_called_once();logs.assert_not_called()

    def test_welcome_legacy_ack_behavior_unchanged(self):
        j=job();j['dm_type']='welcome'
        self.assertEqual(self.call(j,None)[0],(None,'failed_retry'))

    def session(self,reason):
        j=job();row=terminal(j);row['metadata']['search_failure_reason']=reason
        with ExitStack() as st:
            st.enter_context(patch.object(engine,'log'))
            st.enter_context(patch.object(engine,'resolve_dm_sender_real_send_enabled_for_type',return_value=(True,'offline')))
            st.enter_context(patch.object(engine,'_resolve_reserved_by',return_value='offline'))
            st.enter_context(patch.object(engine,'_resolve_dm_sender_only_job_id',return_value=('','offline')))
            st.enter_context(patch('outreach_slot_contract.active',return_value=None))
            st.enter_context(patch.object(engine,'_dm_sender_session_should_abort',return_value=False))
            st.enter_context(patch.object(engine,'execute_dm_job_real_send',return_value=dict(job=row,outcome='failed_terminal',recipient_username=j['recipient_username'])))
            return engine.run_dm_sender_send(object(),account_id=j['account_id'],dm_type='outreach',run_id='offline-run',
                                              prepared_jobs=[j],settings_override={})

    def test_terminal_failure_count_and_specific_safe_reason_reach_real_session_summary(self):
        code,summary=self.session('search_observation_deadline_expired')
        self.assertEqual(code,1);self.assertEqual(summary['jobs_failed_count'],1)
        self.assertEqual(summary['jobs_sent_count'],0);self.assertEqual(summary['jobs_skipped_count'],0)
        self.assertEqual(summary['first_causal_reason'],'search_observation_deadline_expired')
        self.assertEqual(summary['generic_navigation_reason'],'navigation_failed')

    def test_sensitive_or_foreign_search_reason_never_replaces_generic_code(self):
        for reason in ['SECRET: password=data','Traceback',None,'outbound_bubble_not_new']:
            _,summary=self.session(reason)
            self.assertEqual(summary['first_causal_reason'],'navigation_failed')
            self.assertNotIn('SECRET',str(summary))

class SearchCausalDiagnosticsTests(unittest.TestCase):
    def test_deadline_retains_last_already_read_XML_no_fourth_read_or_retype(self):
        # Three .9s IO pairs + existing .35s waits: third returns too late.
        exact,out,d,clock,buf,retype=budget.SearchBudgetTests().observe([absence.suggestion_xml()],cost=.9)
        self.assertFalse(exact);self.assertEqual(out['reason'],'search_observation_deadline_expired')
        self.assertEqual(out['state'],'unknown');self.assertNotIn('proof',out)
        self.assertEqual(d.reads,3);retype.assert_not_called()
        detail=out['observation_detail'];self.assertEqual(detail['read_count'],3);self.assertEqual(detail['waiter_poll_count'],3);self.assertTrue(detail['late_result_discarded'])
        self.assertEqual(detail['budget_ms'],3500);self.assertTrue(detail['deadline_expired'])
        self.assertTrue(detail['last_xml_retained']);self.assertGreaterEqual(detail['elapsed_ms'],3500)
        self.assertEqual(detail['last_safe_semantic_reason'],'search_query_suggestion_only')
        self.assertEqual([r['label'] for r in buf['native_xml']],['outreach_search_round_1_first','outreach_search_round_1_deadline'])
        self.assertTrue(all(r['xml']==absence.suggestion_xml() for r in buf['native_xml']))
        self.assertEqual(d.calls,['dump_hierarchy','app_current']*3)

    def test_positive_empty_contract_and_first_refusal_are_preserved(self):
        _,out,d,_,buf,retype=budget.SearchBudgetTests().observe()
        self.assertEqual(out['state'],'confirmed_absent');self.assertEqual(d.reads,4)
        self.assertEqual([len(x) for x in out['proof'].rounds],[2,2]);retype.assert_called_once()
        self.assertEqual(len(buf['native_xml']),2)
        _,out,d,_,buf,retype=budget.SearchBudgetTests().observe([absence.xml(query='wrong')])
        self.assertEqual(out['first_failure']['reason'],'search_query_unproved')
        self.assertEqual(out['first_failure']['poll_index'],1);self.assertEqual(d.reads,1);retype.assert_not_called()

    def test_retention_failure_is_diagnostic_only_and_never_upgrades_unknown(self):
        _,out,d,_,buf,retype=budget.SearchBudgetTests().observe([absence.suggestion_xml()],capture=False)
        self.assertEqual(out['reason'],'search_observation_retention_unproved');self.assertNotIn('proof',out)
        self.assertEqual(d.reads,1);retype.assert_not_called()

class SignedScopePreservationTests(unittest.TestCase):
    def test_only_authorized_runtime_functions_change(self):
        expected={'instagram_navigation.py':{'_observe_outreach_missing_search'},
                  'dm_sender_engine.py':{'_complete_job_failed_retry','execute_dm_job_real_send','run_dm_sender_send'}}
        for name,changed in expected.items():
            old=subprocess.check_output(['git','show',PARENT+':'+name],cwd=ROOT,text=True);new=(ROOT/name).read_text()
            nodes=lambda s:{n.name:ast.dump(n) for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
            a,b=nodes(old),nodes(new);self.assertEqual(set(a),set(b))
            self.assertEqual({k for k in a if a[k]!=b[k]},changed)
            # No new physical/Backend calls or sleep site anywhere.
            calls=lambda s:[ast.dump(n.func) for n in ast.walk(ast.parse(s)) if isinstance(n,ast.Call)
                            and isinstance(n.func,ast.Attribute) and n.func.attr in {'sleep','complete_dm_job','app_current','dump_hierarchy','click','press','swipe'}]
            self.assertEqual(calls(old),calls(new))

class DiagnosticExactInverseTests(unittest.TestCase):
    def test_exact_parent_and_foreign_missing_moved_duplicate_hunks(self):
        import base64,json,zlib
        import test_certification_admission as pins
        rows=json.loads(zlib.decompress(base64.b64decode(pins._AUDITED_FAILURE_EXACT_INVERSE)))
        for name,row in rows.items():
            source=(ROOT/name).read_text();parent=subprocess.check_output(['git','show',PARENT+':'+name],cwd=ROOT,text=True)
            self.assertEqual(pins.restore_audited_failure_exact_delta(name,source),parent)
            self.assertEqual(pins.restore_audited_failure_exact_delta(name,parent),parent)
            h=next(h for h in row['hunks'] if h['new']);at,new=h['offset'],h['new']
            for bad in (source+'\n',source[:at]+source[at+1:],source[:at]+new+source[at:],source[:at]+source[at+len(new):]+new):
                if bad!=source:
                    with self.assertRaises(AssertionError):pins.restore_audited_failure_exact_delta(name,bad)

    def test_previous_frozen_inverse_records_are_unchanged(self):
        parent=subprocess.check_output(['git','show',PARENT+':tests/test_certification_admission.py'],cwd=ROOT,text=True)
        constants=lambda s:{n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(s).body
            if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)
            and n.targets[0].id.endswith('_EXACT_INVERSE')}
        current=constants((ROOT/'tests/test_certification_admission.py').read_text())
        for key,value in constants(parent).items():self.assertEqual(current[key],value,key)
