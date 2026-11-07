from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from types import SimpleNamespace

import account_run_request_consumer as consumer
import cooperative_business_stop as stops

from cooperative_business_stop import (
    StopContext,
    acknowledge_stop,
    action_start_allowed,
    clear_context,
    follow_handoff_deadline,
    read_ack,
    read_stop,
    request_stop,
)


class StartupCancellationDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"COOPERATIVE_STOP_CONTROL_DIR": self.tmp.name}, clear=False)
        self.env.start(); self.addCleanup(self.env.stop)
        self.context = StopContext('a', 'r', 'd', 'r', generation='2026-11-07T01:11:16.826912+00:00')
        self.request = dict(id='r', account_id='a', run_id='run', status='running',
            claimed_at=self.context.generation, metadata_safe={'device_lock_binding':{
                'request_id':'r','account_id':'a','device_id':'d'}})
        self.run = dict(id='run', account_id='a', status='running')
        self.read_request = mock.Mock(side_effect=lambda *a, **k: self.request)
        self.read_run = mock.Mock(side_effect=lambda *a, **k: self.run)
        self.now = 0.0

    def decide(self, context='default', **changes):
        return stops.startup_stop_decision(self.context if context=='default' else context,
            **dict(account_id='a',request_id='r',run_id='run',device_id='d',
                read_request=self.read_request,read_run=self.read_run,
                monotonic=lambda:self.now, **changes))

    def test_positive_bound_active_and_exact_bounded_calls(self):
        self.assertEqual(self.decide().state,'ACTIVE')
        self.read_request.assert_called_once_with('r',bounded_pre_device=True)
        self.read_run.assert_called_once_with('run',request_timeout=5.0,max_retries=0)

    def test_empty_spawn_run_id_retains_token_key_and_later_link(self):
        self.assertEqual(self.context.run_id,'')
        before=self.context.key();self.assertEqual(self.decide().state,'ACTIVE')
        self.assertEqual(self.context.key(),before)
        request_stop(self.context,reason='human_manual_stop')
        self.assertEqual(self.decide().reason,'startup_cooperative_stop')

    def test_stop_token_short_circuits_both_gets(self):
        request_stop(self.context,reason='human_manual_stop')
        decision=self.decide();self.assertEqual(decision.state,'STOP')
        self.assertEqual(decision.stop_reason,'human_manual_stop')
        self.read_request.assert_not_called();self.read_run.assert_not_called()

    def test_terminal_request_short_circuits_run_read(self):
        for status in ('cancel_requested','canceled','cancelled','stopped','completed','failed','expired'):
            with self.subTest(status=status):
                self.request['status']=status
                self.assertEqual(self.decide().state,'STOP')
        self.read_run.assert_not_called()

    def test_terminal_run_stops_without_a_token(self):
        for status in ('stopped','canceled','cancelled','completed','failed','expired'):
            with self.subTest(status=status):
                self.run['status']=status
                self.assertEqual(self.decide().state,'STOP')

    def test_missing_or_foreign_context_is_unknown_without_gets(self):
        for ctx in (None,StopContext('other','r','d','r'),StopContext('a','foreign','d','r'),
                    StopContext('a','r','other','r'),StopContext('a','r','d','r',run_id='foreign')):
            with self.subTest(ctx=ctx):self.assertEqual(self.decide(context=ctx).state,'UNKNOWN')
        self.read_request.assert_not_called()

    def test_request_identity_session_device_generation_and_link_fail_closed(self):
        import copy
        good=copy.deepcopy(self.request)
        variants=[{'id':'foreign'},{'account_id':'foreign'},{'run_id':'foreign'},
            {'claimed_at':'2026-11-07T01:11:17Z'},{'claimed_at':None},
            {'metadata_safe':None},{'metadata_safe':{}},
            {'metadata_safe':{**good['metadata_safe'],'business_session_id':'foreign'}},
            {'metadata_safe':{'device_lock_binding':{'request_id':'r','account_id':'foreign','device_id':'d'}}},
            {'metadata_safe':{'device_lock_binding':{'request_id':'foreign','account_id':'a','device_id':'d'}}},
            {'metadata_safe':{'device_lock_binding':{'request_id':'r','account_id':'a','device_id':'foreign'}}},
            {'metadata_safe':{**good['metadata_safe'],'outreach_slot_v1':{'account_id':'a','device_id':'foreign'}}}]
        for change in variants:
            with self.subTest(change=change):
                self.request={**good,**change};self.assertEqual(self.decide().state,'UNKNOWN')
        self.read_run.assert_not_called()

    def test_generation_accepts_only_equivalent_strict_native_instants(self):
        for value in ('2026-11-07 01:11:16.826912+00:00','2026-11-07T03:11:16.826912+0200'):
            self.request['claimed_at']=value;self.assertEqual(self.decide().state,'ACTIVE')
        for value in ('2026-11-07T01:11:16.82691Z','2026-11-07T01:11:16.826912','bad'):
            self.request['claimed_at']=value;self.assertEqual(self.decide().state,'UNKNOWN')

    def test_no_implicit_active_for_missing_or_unknown_rows(self):
        for row in (None,[],{},'secret'):
            with self.subTest(row=row):
                self.read_request.side_effect=None;self.read_request.return_value=row
                self.assertEqual(self.decide().state,'UNKNOWN')
        self.read_request.side_effect=lambda *a,**k:self.request
        for row in (None,[],{},'secret',{'id':'foreign','account_id':'a','status':'running'},
                    {'id':'run','account_id':'foreign','status':'running'}):
            with self.subTest(run=row):
                self.read_run.side_effect=None;self.read_run.return_value=row
                self.assertEqual(self.decide().state,'UNKNOWN')

    def test_every_unrecognized_state_is_unknown(self):
        for state in ('queued','claimed','UNKNOWN','',None,True,{}):
            self.request['status']=state;self.assertEqual(self.decide().state,'UNKNOWN')
        self.request['status']='running'
        for state in ('starting','UNKNOWN','',None,False,{}):
            self.run['status']=state;self.assertEqual(self.decide().state,'UNKNOWN')

    def test_canonical_call_exception_is_bounded_unknown_without_error_payload(self):
        for reader in (self.read_request,self.read_run):
            with self.subTest(reader=reader):
                original=reader.side_effect;reader.side_effect=TimeoutError('secret-url-and-key')
                decision=self.decide();self.assertEqual(decision.reason,'startup_control_plane_unavailable')
                self.assertNotIn('secret',str(decision));reader.side_effect=original

    def test_token_delivered_during_either_get_wins(self):
        for reader,row in ((self.read_request,self.request),(self.read_run,self.run)):
            def response(*args,**kwargs):
                request_stop(self.context,reason='human_manual_stop');return row
            original=reader.side_effect;reader.side_effect=response
            self.assertEqual(self.decide().state,'STOP')
            clear_context(self.context);reader.side_effect=original

    def test_corrupt_or_foreign_exact_key_token_is_unknown(self):
        from pathlib import Path
        for payload in ('bad-json','{}', '{"schema_version":1,"binding":{}}'):
            stops._path(self.context,'intent').write_text(payload)
            self.assertEqual(self.decide().reason,'startup_stop_token_unproved')
        self.read_request.assert_not_called()

    def test_foreign_generation_token_has_no_authority_in_current_context(self):
        request_stop(StopContext('a','r','d','r',generation='other'),reason='human_manual_stop')
        self.assertEqual(self.decide().state,'ACTIVE')

    def test_full_deadline_and_no_retry_even_for_late_positive_response(self):
        def late(*args,**kwargs):self.now=10.001;return self.request
        self.read_request.side_effect=late
        self.assertEqual(self.decide().reason,'startup_stop_decision_timeout')
        self.read_request.assert_called_once();self.read_run.assert_not_called()

    def test_second_get_uses_remaining_budget_and_late_result_refused(self):
        def slow_request(*args,**kwargs):self.now=7;return self.request
        def slow_run(*args,**kwargs):self.now=10.001;return self.run
        self.read_request.side_effect=slow_request;self.read_run.side_effect=slow_run
        self.assertEqual(self.decide().state,'UNKNOWN')
        self.read_run.assert_called_once_with('run',request_timeout=3.0,max_retries=0)

    def test_long_integrity_does_not_consume_or_replace_this_fresh_boundary(self):
        self.now=61.0992
        self.assertEqual(self.decide().state,'ACTIVE')


class CooperativeBusinessStopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {"COOPERATIVE_STOP_CONTROL_DIR": self.tmp.name},
            clear=False,
        )
        self.env.start()
        self.context = StopContext(
            account_id="account-a",
            request_id="request-a",
            device_id="phone-a",
            business_session_id="session-a",
            generation="generation-a",
        )

    def tearDown(self) -> None:
        clear_context(self.context)
        self.env.stop()
        self.tmp.cleanup()

    def test_intent_is_idempotent_and_ack_is_exactly_bound(self) -> None:
        first = request_stop(self.context, reason="scheduled_business_deadline")
        second = request_stop(self.context, reason="scheduled_business_deadline")
        self.assertEqual(first, second)
        ack = acknowledge_stop(
            self.context,
            phase="follow",
            safe_boundary="before_candidate_selection",
            session_termination_class="scheduled_safe_stop",
        )
        self.assertEqual(read_ack(self.context), ack)
        other = StopContext(
            account_id="account-b",
            request_id="request-a",
            device_id="phone-a",
            business_session_id="session-a",
            generation="generation-a",
        )
        self.assertIsNone(read_stop(other))

    def test_old_session_token_cannot_stop_new_session(self) -> None:
        request_stop(self.context, reason="scheduled_business_deadline")
        new_context = StopContext(
            account_id="account-a",
            request_id="request-new",
            device_id="phone-a",
            business_session_id="session-new",
            generation="generation-new",
        )
        self.assertIsNone(read_stop(new_context))

    def test_action_start_guard_uses_bounded_remaining_time(self) -> None:
        now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        deadline = (now + timedelta(seconds=20)).isoformat()
        self.assertTrue(action_start_allowed(deadline=deadline, bounded_action_seconds=15, now=now))
        self.assertFalse(action_start_allowed(deadline=deadline, bounded_action_seconds=21, now=now))

    def test_follow_handoff_is_dynamic_and_absent_without_unfollow_work(self) -> None:
        deadline = "2026-08-18T16:00:00Z"
        self.assertIsNone(
            follow_handoff_deadline(
                business_deadline=deadline,
                eligible_unfollows=0,
                unfollow_quota_remaining=120,
                estimated_seconds_per_unfollow=15,
            )
        )
        self.assertEqual(
            follow_handoff_deadline(
                business_deadline=deadline,
                eligible_unfollows=3,
                unfollow_quota_remaining=2,
                estimated_seconds_per_unfollow=15,
                navigation_reserve_seconds=30,
                recovery_reserve_seconds=75,
            ),
            "2026-08-18T15:57:45Z",
        )

    def test_business_deadline_2d_reserves_80_actions(self) -> None:
        self.assertEqual(
            follow_handoff_deadline(
                business_deadline="2026-08-20T18:00:00Z",
                eligible_unfollows=80,
                unfollow_quota_remaining=80,
                estimated_seconds_per_unfollow=29,
                navigation_reserve_seconds=105,
                recovery_reserve_seconds=0,
                rare_path_uncertainty_seconds=480,
            ),
            "2026-08-20T17:11:35Z",
        )

    def test_business_deadline_2d_reserves_120_actions(self) -> None:
        self.assertEqual(
            follow_handoff_deadline(
                business_deadline="2026-08-20T18:00:00Z",
                eligible_unfollows=120,
                unfollow_quota_remaining=120,
                estimated_seconds_per_unfollow=29,
                navigation_reserve_seconds=105,
                recovery_reserve_seconds=0,
                rare_path_uncertainty_seconds=480,
            ),
            "2026-08-20T16:52:15Z",
        )

    def test_business_deadline_2d_zero_actionable_adds_no_reserve(self) -> None:
        self.assertIsNone(
            follow_handoff_deadline(
                business_deadline="2026-08-20T18:00:00Z",
                eligible_unfollows=0,
                unfollow_quota_remaining=120,
                estimated_seconds_per_unfollow=29,
                navigation_reserve_seconds=105,
                recovery_reserve_seconds=0,
                rare_path_uncertainty_seconds=480,
            )
        )

    def test_business_deadline_2d_rare_buffer_is_added_once(self) -> None:
        without_rare = follow_handoff_deadline(
            business_deadline="2026-08-20T18:00:00Z",
            eligible_unfollows=3,
            unfollow_quota_remaining=3,
            estimated_seconds_per_unfollow=29,
            navigation_reserve_seconds=105,
            recovery_reserve_seconds=0,
            rare_path_uncertainty_seconds=0,
        )
        with_rare = follow_handoff_deadline(
            business_deadline="2026-08-20T18:00:00Z",
            eligible_unfollows=3,
            unfollow_quota_remaining=3,
            estimated_seconds_per_unfollow=29,
            navigation_reserve_seconds=105,
            recovery_reserve_seconds=0,
            rare_path_uncertainty_seconds=480,
        )
        self.assertEqual(without_rare, "2026-08-20T17:56:48Z")
        self.assertEqual(with_rare, "2026-08-20T17:48:48Z")

    def test_scheduled_session_is_not_killed_by_fixed_7200_seconds(self) -> None:
        proc = mock.Mock()
        proc.poll.side_effect = [None, 0]
        cfg = SimpleNamespace(
            subprocess_timeout_seconds=7200,
            heartbeat_seconds=15,
            worker_id="worker-a",
        )
        with mock.patch.object(consumer, "get_account_run_request", return_value={"status": "running"}), \
             mock.patch.object(consumer.time, "monotonic", side_effect=[0.0, 8000.0, 8001.0]), \
             mock.patch.object(consumer.time, "sleep", return_value=None), \
             mock.patch.object(consumer, "_terminate_subprocess") as terminate:
            result = consumer._wait_for_subprocess(
                cfg,
                proc,
                request_id="request-a",
                account_id="account-a",
                stop_context=self.context,
                business_action_deadline="2099-08-18T15:50:00Z",
                scheduled_session_end="2099-08-18T16:00:00Z",
            )
        self.assertEqual(result, (0, False))
        terminate.assert_not_called()

    def test_preemptive_worker_ack_is_captured_before_terminal_cleanup(self) -> None:
        request_stop(
            self.context,
            reason="scheduled_business_deadline",
            deadline="2099-08-18T15:50:00Z",
        )
        acknowledge_stop(
            self.context,
            phase="follow",
            safe_boundary="before_candidate_selection",
            session_termination_class="scheduled_safe_stop",
        )
        proc = mock.Mock()
        proc.poll.return_value = 0
        cfg = SimpleNamespace(
            subprocess_timeout_seconds=7200,
            heartbeat_seconds=15,
            worker_id="worker-a",
        )
        diagnostics: dict[str, object] = {}
        result = consumer._wait_for_subprocess(
            cfg,
            proc,
            request_id="request-a",
            account_id="account-a",
            stop_context=self.context,
            business_action_deadline="2099-08-18T15:50:00Z",
            scheduled_session_end="2099-08-18T16:00:00Z",
            diagnostics=diagnostics,
        )
        self.assertEqual(result, (0, False))
        self.assertTrue(diagnostics["acknowledged"])
        self.assertEqual(
            diagnostics["originating_stop_reason"],
            "scheduled_business_deadline",
        )
        self.assertEqual(diagnostics["phase"], "follow")

    def test_hard_device_boundary_uses_watchdog_not_manual_stop(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        cfg = SimpleNamespace(
            subprocess_timeout_seconds=7200,
            heartbeat_seconds=15,
            worker_id="worker-a",
        )
        diagnostics: dict[str, object] = {}
        with mock.patch.object(consumer, "get_account_run_request", return_value={"status": "running"}), \
             mock.patch.object(consumer, "_terminate_subprocess", return_value=143) as terminate:
            result = consumer._wait_for_subprocess(
                cfg,
                proc,
                request_id="request-a",
                account_id="account-a",
                stop_context=self.context,
                business_action_deadline="2020-08-18T15:50:00Z",
                scheduled_session_end="2020-08-18T16:00:00Z",
                diagnostics=diagnostics,
            )
        self.assertEqual(result, (143, True))
        self.assertEqual(diagnostics["originating_stop_reason"], "dispatcher_watchdog")
        terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
