import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import outreach_slot_contract as contract
from outreach_terminal_receipt import validate_terminal_receipt, receipt_allows_success, InvalidTerminalReceipt
import test_outreach_slot_contract as fixtures
from test_outreach_slot_v2 import actual_function
import account_run_request_consumer as consumer


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SlotTests(); self.f.setUp()
        self.ctx = {**self.f.ctx, 'attempt_id': 2}
        self.good = fixtures.terminal_receipt(self.ctx)

    def validate(self, receipt):
        return validate_terminal_receipt(receipt, self.ctx, run_id='run')

    def process(self, receipt, cleanup=True):
        budget = self.f.budget(**self.ctx)
        budget.stop_reason = 'slot_deadline'
        budget.rpc = Mock(return_value=receipt)
        env = {'set_wait_event_callback':Mock(), 'time':SimpleNamespace(monotonic=lambda:0),
               'log':Mock(), '_CURRENT_RUN_ID':'run'}
        fn = actual_function('runner.py', '_outreach_cleanup_and_finish', env)
        with patch.object(contract, 'active', return_value=budget), patch('device.shell', return_value=(0 if cleanup else 1,'','')), patch('cooperative_business_stop.StopContext.from_env', return_value=None):
            code = fn(SimpleNamespace(press=Mock()), 0)
        request = {'id':'r','account_id':'a','run_id':'run','requested_run_type':'outreach_session',
                   'status':'running','metadata_safe':{'outreach_slot_v1':self.ctx}}
        with patch.object(consumer,'get_account_run_request',return_value=request), patch.object(consumer.supabase_client,'call_rpc_once',return_value=receipt), patch.object(consumer,'_safe_complete_account_run_request') as complete, patch.object(consumer,'_reconcile_linked_run'), patch.object(consumer,'_audit'), patch.object(consumer,'_publish_run_failure_incident'), patch.object(consumer,'_safe_login_provisioner_summary_for_audit',return_value={}):
            # Independent dispatcher check: even a falsely successful child must fail.
            consumer._finalize_manual_run_after_subprocess(SimpleNamespace(worker_id='w'),request_id='r',account_id='a',exit_code=0)
        return code, complete.call_args.args[2], budget

    def assertRejected(self, receipt):
        with self.assertRaises(InvalidTerminalReceipt): self.validate(receipt)
        code, status, budget = self.process(receipt)
        self.assertEqual((code,status,budget.finished),(1,'failed',False))

    def test_exact_valid_partial_and_target(self):
        for sent,reason in ((25,'slot_deadline'),(30,'target_reached'),(25,'no_safe_work')):
            receipt=fixtures.terminal_receipt(self.ctx,sent=sent,reason=reason)
            self.assertTrue(receipt_allows_success(self.validate(receipt)))
            self.assertEqual(self.process(receipt)[:2],(0,'completed'))

    def test_initial_attempt_root_is_canonical_request_id(self):
        context={**self.ctx,'attempt_id':1}
        receipt=fixtures.terminal_receipt(context)
        context.pop('root_request_id')
        self.assertEqual(validate_terminal_receipt(receipt,context,run_id='run'),receipt)
        with self.assertRaises(InvalidTerminalReceipt):
            validate_terminal_receipt({**receipt,'root_request_id':'foreign'},context,run_id='run')

    def test_foreign_identity_matrix(self):
        for key in ('request_id','slot_id','run_id','account_id','root_request_id','assignment_id','device_id','app_instance_id','package_name'):
            with self.subTest(key=key): self.assertRejected({**self.good,key:'foreign'})
        for attempt in (1,3):
            with self.subTest(attempt=attempt): self.assertRejected({**self.good,'attempt_id':attempt})

    def test_every_required_field_missing_null_empty_or_bad_type(self):
        for key in self.good:
            missing=dict(self.good);missing.pop(key)
            with self.subTest(key=key,kind='missing'):self.assertRejected(missing)
            for bad in (None,'',[],{}):
                with self.subTest(key=key,bad=bad):self.assertRejected({**self.good,key:bad})

    def test_malformed_receipt(self):
        for bad in (None,False,[],42,'receipt'):
            with self.subTest(bad=bad):self.assertRejected(bad)

    def test_nonterminal_or_unknown_state(self):
        for state in ('running','reserved','pending','retrying','unknown','partial','TERMINAL_RECEIPT_FAILURE'):
            with self.subTest(state=state):self.assertRejected({**self.good,'terminal_class':state})

    def test_counter_coherence(self):
        for change in ({'sent_confirmed_in_slot':-1},{'sent_confirmed_in_slot':31},
                       {'remaining_target':-1},{'remaining_target':4},{'target_dm':31},
                       {'target_dm':True},{'attempt_id':True},{'sent_confirmed_in_slot':25.0},
                       {'partial_completion':False},{'ambiguous_send_count':-1},
                       {'terminal_class':'TARGET_REACHED'},{'stop_reason':'target_reached'}):
            with self.subTest(change=change):self.assertRejected({**self.good,**change})

    def test_cleanup_and_business_flags_cannot_mask_failure(self):
        for change in ({'cleanup_ok':False},{'cleanup_ok':1},{'business_failure':None},
                       {'business_failure':'false'},{'stop_reason':'technical_failure'},
                       {'ambiguous_send_count':1},{'stop_reason':'cleanup_failure'}):
            with self.subTest(change=change):self.assertRejected({**self.good,**change})
        # An old success acknowledgement cannot contradict actual cleanup failure.
        self.assertEqual(self.process(self.good,cleanup=False)[0],1)

    def test_valid_failure_receipts_never_allow_business_success(self):
        for reason,cleanup,ambiguous in (('technical_failure',True,0),('cleanup_failure',False,0),
                                         ('unsafe_stop',True,0),('human_manual_stop',True,0),
                                         ('slot_deadline',True,1),('dispatcher_watchdog',False,0)):
            receipt=fixtures.terminal_receipt(self.ctx,reason=reason,cleanup=cleanup,ambiguous=ambiguous)
            self.assertFalse(receipt_allows_success(self.validate(receipt)))
            self.assertEqual(self.process(receipt,cleanup=cleanup)[:2],(1,'failed'))

    def test_persistence_confirmation(self):
        for stamp in ('yesterday','2026-09-15T08:40:00','2020-01-01T00:00:00Z'):
            with self.subTest(stamp=stamp):self.assertRejected({**self.good,'persisted_at':stamp})

    def test_duplicate_receipt_no_second_rpc_or_counter_increment(self):
        _,_,budget=self.process(self.good)
        for _ in range(2):
            self.assertEqual(budget.finish(cleanup_ok=True,cleanup_ms=1,run_id='run'),self.good)
        budget.rpc.assert_called_once();self.assertEqual(budget.sent,25)
        with self.assertRaises(InvalidTerminalReceipt):budget.finish(cleanup_ok=True,cleanup_ms=1,run_id='foreign')

    def test_independent_review_minimal_receipt_rejected(self):
        self.assertRejected({'slot_id':self.ctx['slot_id'],'sent_confirmed_in_slot':25,'cleanup_ok':True})

    def test_invalid_receipt_does_not_reopen_business_after_end(self):
        _,_,budget=self.process({})
        self.f.clock.advance(2400)
        for fn in (budget.claim,lambda:budget.before_send('job')):
            with self.assertRaises(contract.SlotBlocked):fn()
        budget.rpc.assert_called_once()
