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
from test_outreach_slot_v22 import native_precision_fixture


class NativeTerminalFinalizationTests(unittest.TestCase):
    def setUp(self):
        native = native_precision_fixture()
        self.ctx, self.receipt = native['context'], native['receipt']
        self.request = {'id': self.ctx['request_id'], 'run_id': self.receipt['run_id'],
                        'account_id': self.ctx['account_id'], 'status': 'running',
                        'requested_run_type': 'outreach_session',
                        'metadata_safe': {'outreach_slot_v1': self.ctx}}

    def finish(self, receipt, summary=None):
        from reconciliation_scan import parse_reconciliation_timestamp
        budget = outreach_slot_contract.SlotBudget(
            self.ctx, wall=lambda: parse_reconciliation_timestamp(self.ctx['slot_start']).timestamp()+1,
            monotonic=lambda: 0, rpc=Mock(return_value=receipt))
        if isinstance(receipt, Exception):
            budget.rpc.side_effect = receipt
        published = []
        def publish(**kwargs):
            published.append(kwargs)
            return kwargs['status']
        env = {'set_wait_event_callback': Mock(), 'time': SimpleNamespace(monotonic=lambda: 0),
               'log': Mock(), '_CURRENT_RUN_ID': self.receipt['run_id'],
               '_CURRENT_RUN_REQUEST_ID': self.request['id'],
               '_run_control_dispatcher_worker_id': lambda: 'test-only',
               '_PENDING_TERMINAL_RUN_STATUS': {'run_id': self.receipt['run_id'],
                   'status': 'completed', 'totals': {'dm': 2}, 'performance_summary': summary or {}},
               '_update_run_status_safe': publish}
        for name in ('_outreach_cleanup_and_finish', '_terminalize_run_request_after_cleanup',
                     '_return_with_cleanup'):
            env[name] = actual_function('runner.py', name, env)
        device = SimpleNamespace(press=Mock())
        with patch.object(outreach_slot_contract, 'active', return_value=budget), \
                patch('device.shell', return_value=(0, '', '')) as shell, \
                patch('cooperative_business_stop.StopContext.from_env', return_value=None), \
                patch('account_run_control.complete_account_run_request', return_value={}) as complete:
            code = env['_return_with_cleanup'](device, 0)
            # Repeated cleanup never repeats the finish RPC or device actions.
            again = env['_outreach_cleanup_and_finish'](device, code)
        self.assertEqual(again, code)
        budget.rpc.assert_called_once()
        self.assertEqual(budget.rpc.call_args.args[0], 'finish_outreach_slot_v2')
        shell.assert_called_once()
        device.press.assert_called_once_with('home')
        self.assertEqual(len(published), 1)
        self.assertEqual(complete.call_args.args[2], published[0]['status'])
        self.assertEqual(published[0]['totals'], {'dm': 2})
        return code, published[0], budget

    def dispatch(self, receipt, persisted, code, canceled=False):
        request = {**self.request, 'status': 'canceled' if canceled else 'running'}
        row = {'id': self.receipt['run_id'], 'account_id': self.ctx['account_id'],
               'status': persisted['status'], 'performance_summary': persisted['performance_summary']}
        with patch.object(consumer, 'get_account_run_request', return_value=request), \
                patch.object(consumer.supabase_client, 'call_rpc_once', return_value=receipt) as rpc, \
                patch.object(consumer.supabase_client, 'load_run_row', return_value=row), \
                patch.object(consumer, '_safe_complete_account_run_request', return_value={}) as complete, \
                patch.object(consumer, '_reconcile_linked_run'), patch.object(consumer, '_audit'), \
                patch.object(consumer, '_publish_run_failure_incident') as incident, \
                patch.object(consumer, '_safe_login_provisioner_summary_for_audit', return_value={}), \
                patch.object(consumer, 'log'):
            consumer._finalize_manual_run_after_subprocess(
                SimpleNamespace(worker_id='test-only'), request_id=request['id'],
                account_id=request['account_id'], exit_code=code)
        self.assertEqual(rpc.call_args_list[0].args[0], 'read_outreach_terminal_v2')
        return complete, incident

    def test_native_receipt_completes_runner_and_dispatcher_without_incident(self):
        code, row, budget = self.finish(self.receipt)
        self.assertEqual((code, row['status'], budget.finished), (0, 'completed', True))
        self.assertEqual(row['performance_summary']['outreach_slot_terminal'], self.receipt)
        self.assertNotIn('outreach_terminal_receipt_error', row['performance_summary'])
        complete, incident = self.dispatch(self.receipt, row, code)
        self.assertEqual(complete.call_args.args[2], 'completed')
        incident.assert_not_called()

    def test_invalid_receipt_reason_reaches_unchanged_dispatcher(self):
        for changes, field in (({'persisted_at': 'invalid'}, 'persisted_at'),
                               ({'account_id': 'foreign'}, 'account_id'),
                               ({'remaining_target': 27}, 'counters')):
            receipt = {**self.receipt, **changes}
            with self.subTest(field=field):
                code, row, budget = self.finish(receipt)
                self.assertEqual((code, row['status'], budget.finished), (1, 'failed', False))
                summary = row['performance_summary']
                self.assertEqual(summary['reason_code'], 'outreach_terminal_receipt_invalid')
                self.assertEqual(summary['outreach_terminal_receipt_error'],
                                 'outreach_terminal_receipt_invalid:' + field)
                complete, incident = self.dispatch(receipt, row, code)
                self.assertEqual(complete.call_args.args[2], 'failed')
                self.assertEqual(complete.call_args.kwargs['error_code'], summary['reason_code'])
                incident.assert_called_once()
                self.assertEqual(incident.call_args.kwargs['worker_summary']
                                 ['outreach_terminal_receipt_error'],
                                 'outreach_terminal_receipt_invalid:' + field)

    def test_prior_native_cause_wins_and_terminal_error_remains(self):
        prior = {'outreach_session_summary': {'first_causal_reason': 'send_unverified_quarantined'}}
        code, row, _ = self.finish({**self.receipt, 'persisted_at': 'invalid'}, prior)
        summary = row['performance_summary']
        self.assertEqual(summary['reason_code'], 'send_unverified_quarantined')
        self.assertEqual(summary['secondary_failure_code'], 'outreach_terminal_receipt_invalid')
        self.assertEqual(summary['outreach_terminal_receipt_error'],
                         'outreach_terminal_receipt_invalid:persisted_at')
        complete, _ = self.dispatch({}, row, code)
        self.assertEqual(complete.call_args.kwargs['error_code'], 'send_unverified_quarantined')
        self.assertEqual(prior, {'outreach_session_summary':
                                {'first_causal_reason': 'send_unverified_quarantined'}})

    def test_generic_fallback_does_not_hide_typed_terminal_failure(self):
        for reason in ('unknown', 'worker_exit_nonzero', 'outreach_failed'):
            with self.subTest(reason=reason):
                code, row, _ = self.finish({}, {'reason': reason})
                self.assertEqual(code, 1)
                self.assertEqual(row['performance_summary']['reason_code'],
                                 'outreach_terminal_receipt_invalid')

    def test_existing_cleanup_failure_is_preserved_as_first_cause(self):
        _, row, _ = self.finish({}, {'reason_code': 'cleanup_failure'})
        self.assertEqual(row['performance_summary']['reason_code'], 'cleanup_failure')
        self.assertEqual(row['performance_summary']['secondary_failure_code'],
                         'outreach_terminal_receipt_invalid')

    def test_unavailable_rpc_is_not_invented_as_receipt_validation(self):
        code, row, budget = self.finish(TimeoutError('response unavailable'))
        self.assertEqual((code, row['status'], budget.finished), (1, 'failed', False))
        self.assertNotIn('outreach_terminal_receipt_error', row['performance_summary'])

    def test_owner_cancellation_cannot_become_success(self):
        code, row, _ = self.finish(self.receipt)
        complete, incident = self.dispatch(self.receipt, row, code, canceled=True)
        self.assertEqual(complete.call_args.args[2], 'canceled')
        incident.assert_not_called()


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
