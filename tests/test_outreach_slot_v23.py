import unittest
import tempfile
import os
from types import SimpleNamespace
from unittest.mock import patch, Mock
import account_run_request_consumer as consumer
import outreach_terminal_receipt as authority
import test_outreach_slot_contract as fixtures
from test_outreach_slot_v2 import actual_function
import outreach_slot_contract


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.SlotTests();f.setUp()
        self.ctx={**f.ctx,'request_id':'00000000-0000-4000-8000-000000000001',
                  'root_request_id':'00000000-0000-4000-8000-000000000002','attempt_id':2}
        self.receipt=fixtures.terminal_receipt(self.ctx)
        self.request={'id':self.ctx['request_id'],'run_id':'run','account_id':'a',
                      'requested_run_type':'outreach_session','status':'running',
                      'metadata_safe':{'outreach_slot_v1':self.ctx}}

    def observe(self, receipt, *, run_status='completed', request=None, write_raises=False):
        request={**(self.request if request is None else request), 'created_at':'2026-09-15T08:00:00+00:00'}
        calls=[]
        def rpc(name,payload,**kwargs):
            calls.append((name,payload))
            if name=='read_outreach_terminal_v2':
                if isinstance(receipt,Exception):raise receipt
                return receipt
            if name=='complete_account_run_request':
                if write_raises:raise TimeoutError('completion response lost')
                return {**request,'status':payload['p_status']}
            raise AssertionError('BUSINESS RPC FORBIDDEN: '+name)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'RECONCILIATION_STATE_DIR': directory}), patch.object(consumer.supabase_client,'_request_json',side_effect=[[request],[request],[{'id':'run','status':run_status}]]),patch.object(consumer.supabase_client,'call_rpc_once',side_effect=rpc),patch.object(consumer,'get_account_run_request',return_value=request),patch.object(consumer,'log'),patch.object(authority,'validate_terminal_receipt',wraps=authority.validate_terminal_receipt) as validate:
            result=consumer._reconcile_managed_requests_with_terminal_runs(SimpleNamespace(worker_id='review'))
        return result,calls,validate.call_count

    def assertDeferred(self, receipt):
        result,calls,_=self.observe(receipt)
        self.assertEqual(result['terminalized'],0)
        self.assertEqual([n for n,p in calls],['read_outreach_terminal_v2'])
        self.assertEqual(self.request['status'],'running')

    def test_missing_incomplete_malformed_and_unavailable_defer(self):
        for receipt in (None,{},False,[],{'cleanup_ok':True},TimeoutError('read timeout'),ConnectionError('offline')):
            with self.subTest(receipt=receipt):self.assertDeferred(receipt)

    def test_foreign_identity_and_previous_future_attempt_defer(self):
        for key in ('request_id','slot_id','run_id','account_id','root_request_id','assignment_id','device_id','app_instance_id','package_name'):
            with self.subTest(key=key):self.assertDeferred({**self.receipt,key:'foreign'})
        for attempt in (1,3):self.assertDeferred({**self.receipt,'attempt_id':attempt})

    def test_incoherent_counters_cleanup_or_nonterminal_defer(self):
        for change in ({'remaining_target':4},{'cleanup_ok':False},{'business_failure':None},
                       {'terminal_class':'running'},{'persisted_at':None},{'target_dm':99}):
            with self.subTest(change=change):self.assertDeferred({**self.receipt,**change})

    def test_valid_exact_receipt_completes_using_same_authority(self):
        result,calls,validated=self.observe(self.receipt)
        self.assertEqual(result['terminalized'],1);self.assertEqual(validated,1)
        self.assertEqual([n for n,p in calls],['read_outreach_terminal_v2','complete_account_run_request'])
        self.assertEqual(calls[-1][1]['p_status'],'completed')

    def test_valid_failure_receipt_is_not_success(self):
        for reason,cleanup,ambiguous in (('technical_failure',True,0),('cleanup_failure',False,0),('slot_deadline',True,1)):
            receipt=fixtures.terminal_receipt(self.ctx,reason=reason,cleanup=cleanup,ambiguous=ambiguous)
            result,calls,_=self.observe(receipt)
            self.assertEqual(calls[-1][1]['p_status'],'failed')

    def test_missing_then_visible_and_foreign_then_exact(self):
        for first in (None,{**self.receipt,'attempt_id':1}):
            self.assertDeferred(first)
            result,calls,_=self.observe(self.receipt)
            self.assertEqual(result['terminalized'],1)
            self.assertEqual(calls[-1][1]['p_status'],'completed')

    def test_receipt_before_run_terminal_never_completes_early(self):
        for status in ('running','starting','pending'):
            result,calls,validated=self.observe(self.receipt,run_status=status)
            self.assertEqual((result['terminalized'],calls,validated),(0,[],0))
        self.assertEqual(self.observe(self.receipt)[0]['terminalized'],1)

    def test_failed_run_cannot_be_promoted_by_success_receipt(self):
        _,calls,_=self.observe(self.receipt,run_status='failed')
        self.assertEqual(calls[-1][1]['p_status'],'failed')

    def test_duplicate_snapshot_is_idempotent_no_receipt_or_counter_writes(self):
        first=self.observe(self.receipt)
        second=self.observe(self.receipt)
        self.assertEqual(first,second)
        self.assertEqual(self.receipt['sent_confirmed_in_slot'],25)
        self.assertEqual(first[1][-1][1]['p_status'],'completed')
        # Only the existing idempotent status RPC repeats; no finish/claim/send RPC.
        self.assertEqual([n for n,p in first[1]],['read_outreach_terminal_v2','complete_account_run_request'])

    def test_completion_write_timeout_remains_retryable(self):
        result,_,_=self.observe(self.receipt,write_raises=True)
        self.assertEqual(result['terminalized'],0)
        self.assertEqual(self.observe(self.receipt)[0]['terminalized'],1)

    def test_missing_metadata_is_not_legacy_success(self):
        for metadata in (None,{},'malformed'):
            result,calls,_=self.observe(self.receipt,request={**self.request,'metadata_safe':metadata})
            self.assertEqual(result['terminalized'],0)
            self.assertFalse(any(n=='complete_account_run_request' for n,p in calls))

    def test_full_cycle_keeps_legacy_terminal_mapping_without_receipt(self):
        request={**self.request,'requested_run_type':'account_session','metadata_safe':{}}
        for state,expected in (('completed','completed'),('failed','failed'),('stopped','canceled'),('blocked','blocked')):
            _,calls,validated=self.observe(None,run_status=state,request=request)
            self.assertEqual(validated,0)
            self.assertEqual([n for n,p in calls],['complete_account_run_request'])
            self.assertEqual(calls[0][1]['p_status'],expected)

    def test_runner_pending_finally_write_obeys_actual_receipt_validation(self):
        for receipt,expected in ((None,'failed'),({**self.receipt,'attempt_id':1},'failed'),(self.receipt,'completed')):
            f=fixtures.SlotTests();f.setUp();budget=f.budget(**self.ctx)
            budget.stop_reason='slot_deadline';budget.rpc=Mock(return_value=receipt)
            env={'set_wait_event_callback':Mock(),'time':SimpleNamespace(monotonic=lambda:0),
                 'log':Mock(),'_CURRENT_RUN_ID':'run','_CURRENT_RUN_REQUEST_ID':self.request['id'],
                 '_run_control_dispatcher_worker_id':lambda:'review',
                 '_PENDING_TERMINAL_RUN_STATUS':{'run_id':'run','status':'completed','totals':{},'performance_summary':{}},
                 '_update_run_status_safe':lambda **kwargs:kwargs['status']}
            env['_outreach_cleanup_and_finish']=actual_function('runner.py','_outreach_cleanup_and_finish',env)
            env['_terminalize_run_request_after_cleanup']=actual_function('runner.py','_terminalize_run_request_after_cleanup',env)
            fn=actual_function('runner.py','_return_with_cleanup',env)
            with patch.object(outreach_slot_contract,'active',return_value=budget),patch('device.shell',return_value=(0,'','')),patch('cooperative_business_stop.StopContext.from_env',return_value=None),patch('account_run_control.complete_account_run_request',return_value={}) as complete:
                code=fn(SimpleNamespace(press=Mock()),0)
            self.assertEqual(complete.call_args.args[2],expected)
            self.assertEqual(code,0 if expected=='completed' else 1)
