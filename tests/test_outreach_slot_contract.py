import ast
import unittest
from pathlib import Path
from datetime import datetime, timezone
from outreach_slot_contract import SlotBudget, SlotBlocked

class Clock:
    def __init__(self): self.now=datetime(2026,9,15,8,tzinfo=timezone.utc).timestamp(); self.mono=0
    def advance(self,s): self.now+=s;self.mono+=s

class SlotTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.calls=[]
        self.ctx=dict(slot_id='device:2026-09-15:16',slot_index=16,slot_start='2026-09-15T08:00:00Z',slot_end='2026-09-15T08:40:00Z',account_id='a',assignment_id='b',device_id='d',app_instance_id='i',adb_serial='SYNTHETIC',package_name='com.instagram.android',request_id='r',root_request_id='r',attempt_id=1,target_dm=30,sent_confirmed_in_slot=10,measured_bounds=dict(evidence_id='SYNTHETIC',unit_ms=60000,cleanup_ms=10000))
        self.ctx['execution_policy'] = dict(authority='operator_db_v2',approval_id='test-only-approval',
            approved_by='test-operator',approval_reason='unit-test fixture, not field evidence',
            account_id='a',device_id='d',assignment_id='b',package_name='com.instagram.android',
            sender_identity='test-only',instagram_version='test-only',cleanup_grace_ms=10000,
            approved_at='2026-09-15T07:00:00Z',expires_at='2026-09-15T09:00:00Z',
            mode='normal',measurement_source='outreach_real',run_type='outreach_session',
            sample_criteria='operator_reviewed_success_only',sample_count=1,
            max_age_seconds=86400,sample_count_justification='test only',evidence_request_ids=['test-only'],
            observed_at='2026-09-15T06:00:00Z',unit_ms=60000,cleanup_ms=10000)
    def budget(self,**changes):
        c={**self.ctx,**changes}
        c['claimed_jobs_in_slot'] = changes.get('claimed_jobs_in_slot', c['sent_confirmed_in_slot'])
        def rpc(name,payload):
            self.calls.append((name,payload))
            return {**c,'id':'job'}
        return SlotBudget(c,wall=lambda:self.clock.now,monotonic=lambda:self.clock.mono,rpc=rpc)
    def test_restart_20_minutes_keeps_end_and_remaining_target(self):
        self.clock.advance(1200);b=self.budget();self.assertEqual(b.remaining_ms(),1200000);self.assertEqual(b.refresh(),20)
    def test_five_minutes_does_not_require_all_quota_to_fit(self):
        self.clock.advance(2100);b=self.budget(sent_confirmed_in_slot=22);b.check(unit=True);self.assertIsNotNone(b.claim());self.assertEqual(b.remaining_ms(),300000)
    def test_expired_no_claim_or_send(self):
        b=self.budget();self.clock.advance(2400)
        for call in (b.claim,lambda:b.before_send('job')):
            with self.assertRaisesRegex(SlotBlocked,'slot_deadline'):call()
        self.assertEqual(self.calls,[])
    def test_rpc_latency_no_send_permission_after_end(self):
        b=self.budget();self.clock.advance(2300)
        def rpc(*args):self.clock.advance(101);return {'id':'job'}
        b.rpc=rpc
        with self.assertRaisesRegex(SlotBlocked,'slot_deadline'):b.before_send('job')
    def test_clock_rollback_does_not_extend(self):
        b=self.budget();self.clock.mono=2400;self.clock.now-=100
        self.assertEqual(b.remaining_ms(),0)
    def test_missing_measurements_fail_closed(self):
        with self.assertRaisesRegex(SlotBlocked,'measurements_required'):self.budget(execution_policy={}).claim()
    def test_next_unit_not_safe(self):
        b=self.budget();self.clock.advance(2390)
        with self.assertRaisesRegex(SlotBlocked,'insufficient_next_unit'):b.claim()
    def test_missing_binding_and_wrong_package(self):
        for k in ('account_id','assignment_id','device_id','app_instance_id','adb_serial','slot_end'):
            with self.subTest(k=k),self.assertRaises(SlotBlocked):self.budget(**{k:''})
        with self.assertRaises(SlotBlocked):self.budget(package_name='com.instagram.androie')
    def test_target_persists_and_partial_valid(self):
        b=self.budget(sent_confirmed_in_slot=25);self.assertEqual(b.refresh(),5)
        self.clock.advance(2400);self.assertEqual(b.sent,25);self.assertEqual(b.target-b.sent,5)
    def test_fresh_binding_mismatch(self):
        for k in ('device_id','assignment_id','package_name','slot_end'):
            b=self.budget();b.rpc=lambda *args:{**self.ctx,k:'wrong'}
            with self.subTest(k=k),self.assertRaisesRegex(SlotBlocked,'context_changed'):b.refresh()
    def test_at_target_no_claim(self):
        self.assertIsNone(self.budget(sent_confirmed_in_slot=30).claim())
        self.assertEqual(len(self.calls),1)
    def test_send_marker_rpc_before_return(self):
        self.budget().before_send('job');self.assertEqual(self.calls[0][1]['p_action'],'before_send')
    def test_actual_dispatch_validator_missing_assignment(self):
        source=Path('account_run_request_consumer.py').read_text();tree=ast.parse(source)
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_validate_assignment')
        from typing import Any
        from types import SimpleNamespace
        ns={'DispatcherConfig':object,'Any':Any,'resolve_account_assignment_runtime_context':lambda *a,**k:{'assignment_found':False,'reason':'assignment_type_incompatible'}}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])),'<actual validator>','exec'),ns)
        # Preserve current legacy configuration semantics; managed slot binding
        # is additionally proved by _handle_claimed_request before child start.
        result=ns['_validate_assignment']('a','account_session',SimpleNamespace(require_assignment=True,enforce_assignment_window=False))
        self.assertFalse(result[0])

if __name__=='__main__':unittest.main()

class ActualSenderTests(unittest.TestCase):
    setUp = SlotTests.setUp
    budget = SlotTests.budget
    def run_sender(self,outcomes,durations):
        from unittest.mock import patch
        import dm_sender_engine as sender
        import outreach_slot_contract as contract
        b=self.budget(); queue=[{'id':str(i),'recipient_username':'synthetic_'+str(i),'dm_type':'outreach'} for i in range(len(outcomes))]
        claims=[];executed=[];sent=[10]
        def rpc(name,payload):
            if name=='outreach_slot_context_v1':return {**self.ctx,'sent_confirmed_in_slot':sent[0], 'claimed_jobs_in_slot':10+len(claims)}
            claims.append(self.clock.now)
            return queue.pop(0) if queue else None
        b.rpc=rpc
        def execute(_d,job,**kw):
            i=int(job['id']);executed.append(i);self.clock.advance(durations[i])
            if outcomes[i]=='sent':sent[0]+=1
            return {'outcome':outcomes[i]}
        with (patch.object(contract,'active',return_value=b),patch.object(sender,'resolve_dm_sender_real_send_enabled_for_type',return_value=(True,'test')),
             patch.object(sender,'_resolve_reserved_by',return_value='synthetic'),patch.object(sender,'_resolve_dm_sender_only_job_id',return_value=('','test')),
             patch.object(sender.config,'OUTREACH_HARD_MAX_PER_SESSION',30),
             patch.object(sender.supabase_client,'get_account_dm_settings',return_value={'outreach_per_session_limit':30,'outreach_per_day_limit':30,'total_dm_per_day_limit':30}),
             patch.object(sender.supabase_client,'get_account_dm_counter_today',return_value={'counter_date':sender.supabase_client.sast_business_day_window()[0],'outreach_sent_count':10,'total_dm_sent_count':10}),
             patch.object(sender.account_protection_lists,'is_interaction_blocked',return_value=False),
             patch.object(sender,'execute_dm_job_real_send',side_effect=execute),patch.object(sender,'_dm_sender_session_should_abort',return_value=False),patch.object(sender,'log')):
            result=sender.run_dm_sender_send(object(),account_id='a',dm_type='outreach',max_jobs=30)
        return result,claims,executed,sent[0]
    def test_actual_sender_no_second_claim_after_deadline(self):
        (code,summary),claims,executed,_=self.run_sender(['sent','sent'],[2401,1])
        self.assertEqual(executed,[0]);self.assertEqual(len(claims),1)
        self.assertEqual(code,0);self.assertEqual(summary['stop_reason'],'slot_deadline')
    def test_actual_sender_skipped_failed_are_not_confirmed_sends(self):
        (_,summary),_,executed,sent=self.run_sender(['skipped','failed_retry','sent'],[1,1,1])
        self.assertEqual(len(executed),3);self.assertEqual(sent,11)
        self.assertEqual(summary['jobs_sent_count'],1)


def terminal_receipt(context, *, sent=25, reason='slot_deadline', cleanup=True, run_id='run', ambiguous=0):
    from outreach_terminal_receipt import IDENTITY, SCHEMA
    state = ('TECHNICAL_FAILURE' if not cleanup or reason in ('technical_failure','cleanup_failure','dispatcher_watchdog') else
             'UNSAFE_STOP' if ambiguous or reason in ('unsafe_stop','human_manual_stop') else
             'TARGET_REACHED' if sent == context['target_dm'] else
             'SLOT_DEADLINE_PARTIAL' if reason in ('slot_deadline','outreach_insufficient_next_unit_budget') else 'COMPLETED_PARTIAL')
    return {**{k:context[k] for k in IDENTITY}, 'schema_version':SCHEMA, 'run_id':run_id,
            'attempt_id':context['attempt_id'], 'target_dm':context['target_dm'],
            'sent_confirmed_in_slot':sent, 'remaining_target':context['target_dm']-sent,
            'partial_completion':sent<context['target_dm'], 'ambiguous_send_count':ambiguous,
            'cleanup_ok':cleanup, 'stop_reason':reason, 'terminal_class':state,
            'business_failure':not cleanup or ambiguous>0 or reason in ('unsafe_stop','technical_failure','cleanup_failure','dispatcher_watchdog'),
            'persisted_at':context['slot_end']}
