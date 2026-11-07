import unittest
import json
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
import outreach_slot_contract as contract
from outreach_terminal_receipt import validate_terminal_receipt, receipt_allows_success, InvalidTerminalReceipt
import test_outreach_slot_contract as fixtures
from test_outreach_slot_v2 import actual_function
import account_run_request_consumer as consumer


def native_precision_fixture():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'outreach_terminal_native_precision_v1.json').read_text())


class NativeReceiptTimestampTests(unittest.TestCase):
    def setUp(self):
        native = native_precision_fixture()
        self.ctx, self.receipt = native['context'], native['receipt']

    def validate(self, persisted=None, start=None):
        receipt = dict(self.receipt)
        context = dict(self.ctx)
        if persisted is not None:
            receipt['persisted_at'] = persisted
        if start is not None:
            context['slot_start'] = start
        return validate_terminal_receipt(receipt, context, run_id=receipt['run_id'])

    def test_native_five_digit_receipt_is_unchanged_and_successful(self):
        self.assertEqual(self.receipt['persisted_at'], '2026-11-03T12:15:39.03672+00:00')
        self.assertEqual(self.ctx['slot_start'], '2026-11-03T12:10:00.30652+00:00')
        self.assertEqual(self.validate(), self.receipt)
        self.assertTrue(receipt_allows_success(self.validate()))
        self.assertEqual(self.receipt['sent_confirmed_in_slot'], 2)

    def test_zero_through_six_fraction_digits_and_timezone_instants(self):
        from reconciliation_scan import parse_reconciliation_timestamp
        for digits in ('', '1', '12', '123', '1234', '12345', '123456'):
            fraction = '.' + digits if digits else ''
            expected = datetime(2026, 11, 3, 12, 15, 39,
                                int((digits + '000000')[:6]), timezone.utc)
            variants = ('2026-11-03T12:15:39' + fraction + 'Z',
                        '2026-11-03 12:15:39' + fraction + '+00:00',
                        '2026-11-03T14:15:39' + fraction + '+0200',
                        '2026-11-03T09:15:39' + fraction + '-03:00')
            for stamp in variants:
                with self.subTest(stamp=stamp):
                    self.assertEqual(parse_reconciliation_timestamp(stamp), expected)
                    result = self.validate(stamp, variants[0])
                    self.assertEqual(result['persisted_at'], stamp)
                    self.assertTrue(receipt_allows_success(result))

    def test_microsecond_order_and_equal_boundary_preserved(self):
        start = '2026-11-03T12:10:00.30652+00:00'
        for stamp in ('2026-11-03T14:10:00.306520+02:00',
                      '2026-11-03T12:10:00.306521Z'):
            self.validate(stamp, start)
        with self.assertRaisesRegex(InvalidTerminalReceipt,
                                   '^outreach_terminal_receipt_invalid:persisted_at$'):
            self.validate('2026-11-03T14:10:00.306519+02:00', start)

    def test_invalid_instants_fail_closed_in_both_fields(self):
        invalid = (None, '', 123, True, [], {}, 'infinity', '2026-11-03',
                   '2026-11-03T12:15:39', '2026-02-30T00:00:00Z',
                   '2026-11-03T12:15:60Z', '2026-11-03T12:15:39+24:00',
                   '2026-11-03T12:15:39+02:60', '2026-11-03T12:15:39+00',
                   '2026-11-03T12:15:39.1234567Z')
        for field in ('persisted_at', 'slot_start'):
            for stamp in invalid:
                receipt, context = dict(self.receipt), dict(self.ctx)
                (receipt if field == 'persisted_at' else context)[field] = stamp
                with self.subTest(field=field, stamp=stamp), self.assertRaisesRegex(
                        InvalidTerminalReceipt, '^outreach_terminal_receipt_invalid:persisted_at$'):
                    validate_terminal_receipt(receipt, context, run_id=receipt['run_id'])
            receipt, context = dict(self.receipt), dict(self.ctx)
            (receipt if field == 'persisted_at' else context).pop(field)
            with self.assertRaises(InvalidTerminalReceipt):
                validate_terminal_receipt(receipt, context, run_id=receipt['run_id'])

    def test_native_precision_does_not_weaken_other_receipt_proofs(self):
        for field in ('request_id', 'run_id', 'root_request_id', 'slot_id', 'account_id',
                      'assignment_id', 'device_id', 'app_instance_id', 'package_name',
                      'client_id', 'campaign_id'):
            with self.subTest(field=field), self.assertRaises(InvalidTerminalReceipt):
                validate_terminal_receipt({**self.receipt, field: 'foreign'}, self.ctx,
                                          run_id=self.receipt['run_id'])
        for change in ({'campaign_revision': 2}, {'attempt_id': 2},
                       {'remaining_target': 27}, {'cleanup_ok': False},
                       {'business_failure': True}, {'ambiguous_send_count': 1},
                       {'terminal_class': 'TARGET_REACHED'}):
            with self.subTest(change=change), self.assertRaises(InvalidTerminalReceipt):
                validate_terminal_receipt({**self.receipt, **change}, self.ctx,
                                          run_id=self.receipt['run_id'])


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
        with patch.object(consumer,'get_account_run_request',return_value=request), patch.object(consumer.supabase_client,'call_rpc_once',return_value=receipt), patch.object(consumer,'_safe_complete_account_run_request') as complete, patch.object(consumer,'_reconcile_linked_run'), patch.object(consumer,'_audit'), patch.object(consumer,'_publish_run_failure_incident'), patch.object(consumer,'_safe_login_provisioner_summary_for_audit',return_value={}), patch.object(consumer.supabase_client,'load_run_row',return_value={}), patch.object(consumer,'log'):
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


class StartupCleanupTests(unittest.TestCase):
    def cleanup(self, *, started=False, late_stop=False, false_receipt=False):
        from cooperative_business_stop import StartupCancellationStopped, StartupStopDecision
        f=fixtures.SlotTests();f.setUp();budget=f.budget(sent_confirmed_in_slot=0)
        budget.stop_reason='human_manual_stop'
        budget.startup_phase_in_progress=True
        budget.startup_phone_mutations_started=started
        cause=dict(state='STOP',reason='startup_request_terminal',request_status='canceled')
        if not late_stop:
            budget.suppress_startup_phone_cleanup=True;budget.startup_cancellation=cause
        def refusal(**kwargs):
            budget.suppress_startup_phone_cleanup=True;budget.startup_cancellation=cause
            raise StartupCancellationStopped(StartupStopDecision('STOP','startup_request_terminal'))
        cleanup_receipt=fixtures.terminal_receipt(budget.context,sent=0,cleanup=false_receipt,
            reason='unsafe_stop')
        budget.rpc=Mock(return_value=cleanup_receipt)
        env={'set_wait_event_callback':Mock(),'time':SimpleNamespace(monotonic=lambda:0),
             'log':Mock(),'_CURRENT_RUN_ID':'run','_require_startup_cancellation_clear':Mock(side_effect=refusal),
             'StartupCancellationStopped':StartupCancellationStopped}
        fn=actual_function('runner.py','_outreach_cleanup_and_finish',env)
        device=SimpleNamespace(press=Mock())
        with patch.object(contract,'active',return_value=budget),patch('device.shell') as shell, \
             patch('cooperative_business_stop.StopContext.from_env',return_value=None):
            code=fn(device if started else None,1)
            shell.assert_not_called();device.press.assert_not_called()
            if not false_receipt:
                self.assertEqual(fn(device,1),1)
        self.assertEqual(code,1);budget.rpc.assert_called_once()
        self.assertFalse(budget.rpc.call_args.args[1]['p_cleanup_ok'])
        return budget,env

    def test_no_phone_cleanup_when_canceled_during_integrity(self):
        budget,_=self.cleanup()
        self.assertEqual(budget.startup_cleanup_disposition,'not_required_no_startup_phone_mutation')
        self.assertFalse(budget.terminal['cleanup_ok'])
        self.assertEqual(budget.terminal['sent_confirmed_in_slot'],0)

    def test_no_phone_cleanup_when_stop_was_observed_after_connection(self):
        budget,_=self.cleanup(started=True)
        self.assertEqual(budget.startup_cleanup_disposition,'not_performed_after_startup_mutation')

    def test_cancellation_delivered_before_finally_is_checked_before_cleanup(self):
        _,env=self.cleanup(started=True,late_stop=True)
        env['_require_startup_cancellation_clear'].assert_called_once_with(account_id='a',
            request_id='r',run_id='run',device_id='d',boundary='before_startup_cleanup',required=True)

    def test_a_successful_cleanup_receipt_cannot_mask_unperformed_cleanup(self):
        budget,_=self.cleanup(false_receipt=True)
        self.assertFalse(budget.finished)
