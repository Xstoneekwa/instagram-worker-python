"""Offline only: no DB, provider, process, device or Send execution."""
import unittest
import json
from contextlib import ExitStack
from unittest.mock import patch, Mock

from outreach_slot_contract import SlotBlocked
from tests import test_outreach_slot_contract as slot_fixtures
import outreach_session_orchestrator as orchestrator
from outreach_terminal_receipt import validate_terminal_receipt, InvalidTerminalReceipt


class CampaignBudgetTests(unittest.TestCase):
    def setUp(self):
        fixture = slot_fixtures.SlotTests()
        fixture.setUp()
        self.slot = fixture.budget(claimed_jobs_in_slot=3, sent_confirmed_in_slot=3)
        self.settings = {'outreach_per_session_limit': 5, 'outreach_per_day_limit': 30,
                         'total_dm_per_day_limit': 30}
        self.counter = {'outreach_sent_count': 3, 'total_dm_sent_count': 3}

    def test_goal30_is_distinct_from_current_session5_and_day30(self):
        with patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_SESSION', 5), \
             patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_DAY', 40):
            self.assertEqual(self.slot.target, 30)
            self.assertEqual(self.slot.remaining_claim_budget(self.settings, self.counter), 2)
            # A second attempt sees the same persisted claims, not another five.
            self.slot.claimed = 5
            self.assertEqual(self.slot.remaining_claim_budget(self.settings, self.counter), 0)
            self.assertEqual(self.settings['outreach_per_session_limit'], 5)

    def test_daily_and_shared_dm_limits_may_reduce_remaining_session_budget(self):
        self.counter.update(outreach_sent_count=29, total_dm_sent_count=29)
        self.assertEqual(self.slot.remaining_claim_budget(self.settings, self.counter), 1)
        self.counter['total_dm_sent_count'] = 30
        self.assertEqual(self.slot.remaining_claim_budget(self.settings, self.counter), 0)

    def test_skips_and_failures_do_not_count_as_confirmed_goal_but_do_not_refill_safety_budget(self):
        self.slot.claimed = 5
        self.slot.sent = 3
        self.assertEqual(self.slot.remaining_claim_budget(self.settings, self.counter), 0)
        self.assertEqual(self.slot.target - self.slot.sent, 27)

    def test_claim_budget_must_be_proved_not_guessed_or_boolean(self):
        for claimed in (None, True, '3', 2, -1):
            self.slot.claimed = claimed
            with self.subTest(claimed=claimed), self.assertRaisesRegex(SlotBlocked, 'budget_unproven'):
                self.slot.remaining_claim_budget(self.settings, self.counter)


class ManagedCampaignTests(unittest.TestCase):
    def setUp(self):
        self.context = {'account_id': 'account', 'client_id': 'client', 'campaign_id': 'campaign',
                        'campaign_revision': 2, 'managed_campaign': True}
        self.settings = {'outreach_enabled': True, 'outreach_per_session_limit': 5,
                         'outreach_per_day_limit': 30, 'total_dm_per_day_limit': 30}
        self.counter = {'counter_date': orchestrator.supabase_client.sast_business_day_window()[0],
                        'outreach_sent_count': 0, 'total_dm_sent_count': 0}
        self.job = {'id': 'job', 'account_id': 'account', 'campaign_id': 'campaign', 'dm_type': 'outreach',
                    'metadata': {'client_id': 'client', 'campaign_revision': 2}}
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(orchestrator.os.environ,
            {'OUTREACH_CAMPAIGN_CONTEXT_V1': json.dumps(self.context)}))
        for name,value in [('get_account_dm_settings', self.settings), ('get_account_dm_counter_today', self.counter)]:
            self.stack.enter_context(patch.object(orchestrator.supabase_client, name, return_value=value))
        self.stack.enter_context(patch.object(orchestrator, 'resolve_outreach_dm_real_send_enabled', return_value=(True,'fixture')))
        self.stack.enter_context(patch.object(orchestrator, 'log'))
        self.stack.enter_context(patch('outreach_slot_contract.active', return_value=None))
        self.legacy = self.stack.enter_context(patch.object(orchestrator, 'prepare_dm_sender_jobs'))
        self.requeue = self.stack.enter_context(patch.object(orchestrator.supabase_client, 'requeue_stale_outreach_dm_jobs'))

    def prepare(self, jobs):
        rpc = self.stack.enter_context(patch.object(orchestrator.supabase_client, 'call_rpc_once',
            side_effect=[self.context, jobs]))
        result = orchestrator.prepare_outreach_session(object(),account_id='account',account_username='fixture',run_id='run')
        self.assertEqual(rpc.call_args_list[1].args[0], 'claim_outreach_campaign_jobs_v1')
        self.assertEqual(rpc.call_args_list[1].args[1], {'p_account_id':'account','p_run_id':'run','p_reserved_by':'run','p_limit':5})
        self.legacy.assert_not_called();self.requeue.assert_not_called()
        return result

    def test_actual_prepare_scoped_jobs_and_empty_never_use_legacy_claim(self):
        self.assertEqual(self.prepare([self.job])['prepared_jobs'], [self.job])

    def test_actual_prepare_empty_stays_empty(self):
        self.assertEqual(self.prepare([])['session_status'], 'no_jobs')

    def test_scoped_claim_mismatch_rejected_without_fallback(self):
        for job in ({**self.job,'account_id':'foreign'}, {**self.job,'campaign_id':'foreign'},
                    {**self.job,'dm_type':'welcome'}, {**self.job,'metadata':{'client_id':'foreign','campaign_revision':2}},
                    {**self.job,'metadata':{'client_id':'client','campaign_revision':1}}):
            with self.subTest(job=job), patch.object(orchestrator.supabase_client,'call_rpc_once',side_effect=[self.context,[job]]):
                with self.assertRaisesRegex(RuntimeError,'scoped_claim_invalid'):
                    orchestrator.prepare_outreach_session(object(),account_id='account',account_username='fixture',run_id='run')
        self.legacy.assert_not_called();self.requeue.assert_not_called()

    def test_context_unavailable_or_changed_never_falls_back(self):
        for actual in (None, {}, {**self.context,'managed_campaign':False}, {**self.context,'campaign_revision':3}):
            with self.subTest(actual=actual), patch.object(orchestrator.supabase_client,'call_rpc_once',return_value=actual):
                with self.assertRaisesRegex(RuntimeError,'context_changed'):
                    orchestrator.prepare_outreach_session(object(),account_id='account',account_username='fixture',run_id='run')
        self.legacy.assert_not_called();self.requeue.assert_not_called()

    def test_incomplete_environment_cannot_establish_authority(self):
        for change in ({'client_id':None},{'campaign_id':''},{'campaign_revision':True},{'campaign_revision':0}):
            with patch.dict(orchestrator.os.environ,{'OUTREACH_CAMPAIGN_CONTEXT_V1':json.dumps({**self.context,**change})}), \
                 patch.object(orchestrator.supabase_client,'call_rpc_once') as rpc:
                with self.assertRaisesRegex(RuntimeError,'context_invalid'):
                    orchestrator._managed_campaign_context('account','run')
                rpc.assert_not_called()

    def test_legacy_absence_does_not_enable_new_lookup(self):
        with patch.dict(orchestrator.os.environ,{},clear=True), patch.object(orchestrator.supabase_client,'call_rpc_once') as rpc:
            self.assertIsNone(orchestrator._managed_campaign_context('account','run'));rpc.assert_not_called()

    def test_managed_requires_preparation_and_preserves_common_sender(self):
        with patch.object(orchestrator.supabase_client,'call_rpc_once',return_value=self.context), \
             patch.object(orchestrator,'run_dm_sender_send',return_value=(0,{})) as sender:
            with self.assertRaisesRegex(RuntimeError,'preparation_required'):
                orchestrator.run_outreach_session(object(),account_id='account',account_username='fixture',run_id='run')
            sender.assert_not_called()
            prepared={'prepared_jobs':[self.job],'settings':self.settings,'max_jobs_effective':1}
            self.assertEqual(orchestrator.run_outreach_session(object(),account_id='account',account_username='fixture',run_id='run',prepared_outreach=prepared),0)
            self.assertEqual(sender.call_args.kwargs['prepared_jobs'],[self.job])
            self.assertEqual(sender.call_args.kwargs['dm_type'],'outreach');self.requeue.assert_not_called()

    def test_prepared_foreign_tenant_rejected_before_sender(self):
        with patch.object(orchestrator.supabase_client,'call_rpc_once',return_value=self.context), \
             patch.object(orchestrator,'run_dm_sender_send') as sender:
            prepared={'prepared_jobs':[{**self.job,'metadata':{'client_id':'foreign','campaign_revision':2}}]}
            with self.assertRaisesRegex(RuntimeError,'prepared_campaign_mismatch'):
                orchestrator.run_outreach_session(object(),account_id='account',account_username='fixture',run_id='run',prepared_outreach=prepared)
            sender.assert_not_called()

    def test_terminal_receipt_binds_exact_managed_campaign(self):
        f=slot_fixtures.SlotTests();f.setUp()
        ctx={**f.ctx, 'managed_campaign':True, 'client_id':'client', 'campaign_id':'campaign','campaign_revision':2}
        receipt={**slot_fixtures.terminal_receipt(ctx),'client_id':'client','campaign_id':'campaign','campaign_revision':2}
        self.assertEqual(validate_terminal_receipt(receipt,ctx,run_id='run'),receipt)
        for field in ('client_id','campaign_id','campaign_revision'):
            for bad in (None,'foreign',True,3):
                with self.subTest(field=field,bad=bad),self.assertRaises(InvalidTerminalReceipt):
                    validate_terminal_receipt({**receipt,field:bad},ctx,run_id='run')


if __name__ == '__main__':
    unittest.main()


class OwnerUpperBoundTests(unittest.TestCase):
    def test_budget30_preserves_lower_caps_daily_and_shared_remaining(self):
        settings = dict(outreach_per_session_limit=30, outreach_per_day_limit=30, total_dm_per_day_limit=100)
        counter = dict(outreach_sent_count=0, total_dm_sent_count=0)
        with patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_SESSION', 30), patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_DAY', 30):
            self.assertEqual(orchestrator._resolve_effective_max_jobs(settings, counter)['max_jobs_effective'], 30)
            for lower in (0, 1, 5, 25):
                self.assertEqual(orchestrator._resolve_effective_max_jobs(dict(settings, outreach_per_session_limit=lower), counter)['max_jobs_effective'], lower)
            self.assertEqual(orchestrator._resolve_effective_max_jobs(settings, dict(counter,outreach_sent_count=29))['max_jobs_effective'], 1)
            self.assertEqual(orchestrator._resolve_effective_max_jobs(settings, dict(counter,total_dm_sent_count=100))['max_jobs_effective'], 0)

    def test_higher_runtime_override_cannot_raise_owner_ceiling(self):
        settings = dict(outreach_per_session_limit=80, outreach_per_day_limit=80, total_dm_per_day_limit=100)
        for limit in (30, 31, 80):
            with self.subTest(limit=limit), patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_SESSION', limit), patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_DAY', limit):
                self.assertEqual(orchestrator._resolve_effective_max_jobs(settings, {})['max_jobs_effective'], 30)
        with patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_SESSION', 5):
            self.assertEqual(orchestrator._resolve_effective_max_jobs(settings, {})['max_jobs_effective'], 5)

    def test_existing_config_override_expressions_allow_only_zero_to30(self):
        import ast
        from pathlib import Path
        source = ast.parse((Path(orchestrator.__file__).parent / 'config.py').read_text())
        names = ('OUTREACH_HARD_MAX_PER_SESSION', 'OUTREACH_HARD_MAX_PER_DAY')
        for node in source.body:
            if isinstance(node, ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in node.targets):
                for override in (None, -1, 0, 1, 5, 30, 31, 80):
                    value = eval(compile(ast.Expression(node.value), '<existing-config-expression>', 'eval'),
                        {'_env_int':lambda key, default: default if override is None else override})
                    self.assertEqual(value,30 if override is None else max(0,min(30,override)))

    def test_thirty_claims_are_persistent_across_attempts_not_refilled(self):
        fixture = slot_fixtures.SlotTests();fixture.setUp()
        slot = fixture.budget(claimed_jobs_in_slot=29, sent_confirmed_in_slot=20)
        settings=dict(outreach_per_session_limit=30,outreach_per_day_limit=30,total_dm_per_day_limit=100)
        with patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_SESSION', 30), patch.object(orchestrator.config, 'OUTREACH_HARD_MAX_PER_DAY', 30):
            self.assertEqual(slot.remaining_claim_budget(settings,dict(outreach_sent_count=20,total_dm_sent_count=20)),1)
            slot.claimed=30
            self.assertEqual(slot.remaining_claim_budget(settings,dict(outreach_sent_count=20,total_dm_sent_count=20)),0)
