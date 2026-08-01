"""Tests for Run Control dispatcher."""

from __future__ import annotations

import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import account_run_request_consumer as consumer
from tests.follow60_generic_fixtures import TEST_CANARY_ACCOUNT_ID, bound_control

TEST_REQUEST_ID = "00000000-0000-4000-8000-000000000101"
TEST_ACCOUNT_ID = "00000000-0000-4000-8000-000000000201"
TEST_RUN_ID = "00000000-0000-4000-8000-000000000301"


class AccountRunRequestConsumerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.package_runtime_contract = patch.object(
            consumer,
            "_load_package_runtime_contract",
            return_value=(True, "ready", {"ok": True, "reason": "ready"}),
        )
        self.package_runtime_contract.start()
        self.addCleanup(self.package_runtime_contract.stop)

    def test_account_session_deadline_env_prefers_real_scheduler_deadline(self) -> None:
        out = consumer._account_session_deadline_env(
            {
                "scheduled_session_start": "2026-07-25T16:00:00+00:00",
                "scheduled_session_ends_at": "2026-07-25T22:00:00+00:00",
            },
            {},
        )

        self.assertEqual(out["SCHEDULED_SESSION_START"], "2026-07-25T16:00:00+00:00")
        self.assertEqual(out["SCHEDULED_SESSION_END"], "2026-07-25T22:00:00+00:00")
        self.assertEqual(out["BUSINESS_ACTION_DEADLINE"], "2026-07-25T21:50:00Z")

    def test_account_session_deadline_env_is_empty_without_scheduler_window(self) -> None:
        self.assertEqual(consumer._account_session_deadline_env({}, {}), {})

    def test_dispatcher_config_uses_explicit_stable_host_machine(self) -> None:
        with patch.dict(
            consumer.os.environ,
            {
                "RUN_CONTROL_DISPATCHER_WORKER_ID": "run-dispatcher:mac-admin-01",
                "RUN_CONTROL_DISPATCHER_HOST_MACHINE": "stable-physical-host.local",
            },
            clear=False,
        ):
            cfg = consumer.load_dispatcher_config()
        self.assertEqual(cfg.worker_id, "run-dispatcher:mac-admin-01")
        self.assertEqual(cfg.host_machine, "stable-physical-host.local")

    def test_dispatcher_heartbeat_publishes_canonical_and_observed_host(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:mac-admin-01",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=True,
            enforce_assignment_window=True,
            host_machine="stable-physical-host.local",
        )
        with (
            patch.object(consumer.socket, "gethostname", return_value="mutable-dhcp-name.local"),
            patch.object(consumer.runtime_heartbeat, "heartbeat_worker") as heartbeat,
        ):
            consumer._heartbeat(cfg)
        self.assertEqual(heartbeat.call_args.kwargs["host_machine"], "stable-physical-host.local")
        self.assertEqual(
            heartbeat.call_args.kwargs["metadata"]["observed_socket_hostname"],
            "mutable-dhcp-name.local",
        )

    def test_startup_tick_guard_sets_cadence_without_calling_tick(self) -> None:
        with (
            patch.object(
                consumer,
                "consume_auto_restart_startup_tick_skip_once",
                return_value={"consumed": True, "cleanup_ok": True},
            ),
            patch.object(consumer, "run_auto_restart_dispatcher_tick") as tick_call,
            patch.object(consumer, "log") as log_mock,
        ):
            initial = consumer.initialize_auto_restart_tick_state(
                worker_id="run-dispatcher:test",
                now_monotonic=100.0,
            )

        self.assertEqual(initial, 100.0)
        tick_call.assert_not_called()
        log_mock.assert_called_once_with(
            "info",
            "auto_restart_startup_tick_skipped",
            worker_id="run-dispatcher:test",
            one_shot=True,
            token_consumed=True,
            token_cleanup_ok=True,
        )
        self.assertFalse(
            consumer.should_run_auto_restart_tick(
                last_tick_monotonic=initial,
                now_monotonic=159.0,
            )
        )
        self.assertTrue(
            consumer.should_run_auto_restart_tick(
                last_tick_monotonic=initial,
                now_monotonic=160.0,
            )
        )

    def test_restart_without_guard_restores_immediate_tick_eligibility(self) -> None:
        with patch.object(
            consumer,
            "consume_auto_restart_startup_tick_skip_once",
            return_value={"consumed": False, "reason": "guard_absent"},
        ):
            initial = consumer.initialize_auto_restart_tick_state(
                worker_id="run-dispatcher:test",
                now_monotonic=100.0,
            )
        self.assertEqual(initial, 0.0)
        self.assertTrue(
            consumer.should_run_auto_restart_tick(
                last_tick_monotonic=initial,
                now_monotonic=100.0,
            )
        )

    def test_invalid_guard_is_logged_and_does_not_disable_future_ticks(self) -> None:
        with (
            patch.object(
                consumer,
                "consume_auto_restart_startup_tick_skip_once",
                return_value={"consumed": False, "reason": "guard_path_not_absolute"},
            ),
            patch.object(consumer, "log") as log_mock,
        ):
            initial = consumer.initialize_auto_restart_tick_state(
                worker_id="run-dispatcher:test",
                now_monotonic=100.0,
            )
        self.assertEqual(initial, 0.0)
        log_mock.assert_called_once_with(
            "warning",
            "auto_restart_startup_tick_skip_guard_invalid",
            worker_id="run-dispatcher:test",
            reason="guard_path_not_absolute",
        )

    def test_all_login_ui_runs_are_device_bound(self) -> None:
        self.assertTrue(consumer._is_device_bound_run_type("login_provisioning"))
        self.assertTrue(consumer._is_device_bound_run_type("login_email_code_resume"))
        self.assertTrue(consumer._is_device_bound_run_type("login_orphan_challenge_recovery"))
        self.assertFalse(consumer._is_device_bound_run_type("unknown"))

    def test_auto_login_failure_before_dispatcher_claim_is_published_structured(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "login_provisioning",
            "metadata_safe": {
                "binding_version": "auto_login_app_instance_v1",
                "assignment_id": "assignment-1",
                "device_id": "device-1",
                "app_instance_id": "app-instance-1",
                "package_name": "com.instagram.androie",
                "clone_index": 1,
            },
            "status": "claimed",
        }
        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(False, "auto_login_not_ready")),
            patch.object(consumer, "_safe_complete_account_run_request"),
            patch.object(consumer, "_audit"),
            patch.object(consumer, "_publish_auto_login_dispatch_failure") as publish,
            patch.object(consumer, "mark_account_run_request_starting") as starting,
        ):
            consumer._handle_claimed_request(cfg, request)

        starting.assert_not_called()
        publish.assert_called_once_with(
            request=request,
            request_id=TEST_REQUEST_ID,
            account_id=TEST_ACCOUNT_ID,
            run_type="login_provisioning",
            reason_code="auto_login_not_ready",
            phase="request",
        )

    def test_auto_login_failure_after_claim_before_worker_start_is_published_structured(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=True,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "login_provisioning",
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": False,
            "reason": "assignment_missing",
            "device_id": None,
            "app_instance_id": None,
        }
        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(consumer, "_safe_complete_account_run_request"),
            patch.object(consumer, "_audit"),
            patch.object(consumer, "_publish_auto_login_dispatch_failure") as publish,
            patch.object(consumer.subprocess, "Popen") as popen,
        ):
            consumer._handle_claimed_request(cfg, request)

        popen.assert_not_called()
        publish.assert_called_once_with(
            request=request,
            request_id=TEST_REQUEST_ID,
            account_id=TEST_ACCOUNT_ID,
            run_type="login_provisioning",
            reason_code="assignment_missing",
            phase="request",
            device_id=None,
            app_instance_id=None,
        )

    def test_account_session_exit_zero_requires_terminal_phase_contract(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "requested_run_type": "account_session",
            "status": "running",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(
                consumer.supabase_client,
                "load_run_row",
                return_value={"performance_summary": {"phase_terminal_contract": {"ok": False}}},
            ),
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_audit") as audit,
            patch.object(consumer, "_publish_run_failure_incident") as publish,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=0,
            )

        self.assertEqual(complete.call_args.args[2], "failed")
        self.assertEqual(complete.call_args.kwargs["error_code"], "account_session_phase_not_terminal")
        self.assertEqual(reconcile.call_args.kwargs["terminal_status"], "failed")
        self.assertEqual(audit.call_args.kwargs["action_type"], "account_session_terminal_contract_failed")
        publish.assert_called_once()

    def test_account_session_exit_zero_completes_with_terminal_phase_contract(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "requested_run_type": "account_session",
            "status": "running",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(
                consumer.supabase_client,
                "load_run_row",
                return_value={"performance_summary": {"phase_terminal_contract": {"ok": True}}},
            ),
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_audit"),
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=0,
            )

        self.assertEqual(complete.call_args.args[2], "completed")
        self.assertEqual(reconcile.call_args.kwargs["terminal_status"], "completed")

    def test_canceled_account_session_terminalizes_resume_plan_even_on_exit_zero(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "requested_run_type": "account_session",
            "status": "canceled",
            "cancel_requested_at": "2026-07-27T19:45:44Z",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run") as reconcile,
            patch.object(consumer, "_audit"),
            patch(
                "account_session_resume_plan_store.record_end_of_session"
            ) as record_end,
            patch.object(consumer.supabase_client, "load_run_row") as load_run,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=0,
            )

        self.assertEqual(complete.call_args.args[2], "canceled")
        self.assertEqual(reconcile.call_args.kwargs["terminal_status"], "canceled")
        record_end.assert_called_once_with(
            run_id=TEST_RUN_ID,
            session_plan={
                "restart_allowed": False,
                "restart_block_reason": "operator_canceled",
                "terminal_reason_code": "operator_canceled",
            },
            session_status="success",
        )
        load_run.assert_not_called()

    def test_load_dispatcher_config_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            cfg = consumer.load_dispatcher_config()
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.health_only)
        self.assertFalse(cfg.launch_enabled)
        self.assertEqual(cfg.max_concurrent_subprocesses, 4)

    def test_dispatch_loop_submits_multiple_requests_up_to_capacity(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
            max_concurrent_subprocesses=2,
        )
        first: Future[None] = Future()
        second: Future[None] = Future()
        executor = MagicMock()
        executor.submit.side_effect = [first, second]
        active: set[Future[None]] = set()
        request_one = {"id": TEST_REQUEST_ID, "account_id": TEST_ACCOUNT_ID}
        request_two = {
            "id": "00000000-0000-4000-8000-000000000102",
            "account_id": "00000000-0000-4000-8000-000000000202",
        }

        self.assertTrue(
            consumer._submit_dispatch_task_if_capacity(executor, active, cfg, request_one)
        )
        self.assertTrue(
            consumer._submit_dispatch_task_if_capacity(executor, active, cfg, request_two)
        )
        self.assertFalse(
            consumer._submit_dispatch_task_if_capacity(executor, active, cfg, request_two)
        )
        self.assertEqual(len(active), 2)

        first.set_result(None)
        consumer._collect_completed_dispatch_tasks(active)
        self.assertEqual(active, {second})
        executor.submit.assert_called_with(
            consumer._handle_claimed_request,
            cfg,
            request_two,
        )

    def test_stop_request_is_scoped_to_its_own_subprocess(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        canceled_proc = MagicMock()
        canceled_proc.poll.return_value = None
        other_proc = MagicMock()
        other_proc.poll.return_value = 0

        with (
            patch.object(
                consumer,
                "get_account_run_request",
                return_value={"status": "running", "cancel_requested_at": "2026-07-15T00:00:00Z"},
            ),
            patch.object(consumer, "_terminate_subprocess", return_value=143) as terminate,
        ):
            canceled = consumer._wait_for_subprocess(
                cfg,
                canceled_proc,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
            )
            other = consumer._wait_for_subprocess(
                cfg,
                other_proc,
                request_id="00000000-0000-4000-8000-000000000102",
                account_id="00000000-0000-4000-8000-000000000202",
            )

        self.assertEqual(canceled, (143, False))
        self.assertEqual(other, (0, False))
        terminate.assert_called_once_with(canceled_proc)

    def test_follow_60s_canary_manual_stop_gets_extended_verified_follow_flush_grace(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        proc = MagicMock()
        proc.poll.return_value = None
        with (
            patch.object(
                consumer,
                "get_account_run_request",
                return_value={
                    "status": "running",
                    "cancel_requested_at": "2026-07-31T00:00:00Z",
                },
            ),
            patch.object(consumer, "_follow60_control_applies", return_value=True),
            patch.object(consumer, "_terminate_subprocess", return_value=143) as terminate,
        ):
            result = consumer._wait_for_subprocess(
                cfg,
                proc,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_CANARY_ACCOUNT_ID,
            )

        self.assertEqual(result, (143, False))
        terminate.assert_called_once_with(proc, graceful_timeout_seconds=90.0)

    def test_wait_for_subprocess_keeps_ownership_on_control_plane_read_failure(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        proc = MagicMock()
        proc.poll.side_effect = [None, 0]
        with (
            patch.object(
                consumer,
                "get_account_run_request",
                side_effect=RuntimeError("supabase_dns_failed"),
            ),
            patch.object(consumer.time, "sleep"),
            patch.object(consumer, "log") as log_mock,
        ):
            result = consumer._wait_for_subprocess(
                cfg,
                proc,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
            )

        self.assertEqual(result, (0, False))
        self.assertTrue(
            any(
                call.args[1] == "manual_run_control_plane_read_failed_while_child_active"
                for call in log_mock.call_args_list
            )
        )

    def test_reconcile_requests_with_terminal_runs_closes_only_terminal_links(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        active_run_id = "00000000-0000-4000-8000-000000000302"
        requests = [
            {
                "id": TEST_REQUEST_ID,
                "account_id": TEST_ACCOUNT_ID,
                "status": "running",
                "run_id": TEST_RUN_ID,
                "requested_run_type": "account_session",
            },
            {
                "id": "00000000-0000-4000-8000-000000000102",
                "account_id": "00000000-0000-4000-8000-000000000202",
                "status": "running",
                "run_id": active_run_id,
                "requested_run_type": "account_session",
            },
        ]
        runs = [
            {"id": TEST_RUN_ID, "status": "completed"},
            {"id": active_run_id, "status": "running"},
        ]
        with (
            patch.object(
                consumer.supabase_client,
                "_request_json",
                side_effect=[requests, runs],
            ),
            patch.object(
                consumer,
                "_safe_complete_account_run_request",
                return_value={"status": "completed"},
            ) as complete,
        ):
            result = consumer.reconcile_requests_with_terminal_runs(cfg)

        self.assertEqual(result, {"ok": True, "observed": 2, "terminalized": 1})
        complete.assert_called_once_with(
            TEST_REQUEST_ID,
            "run-dispatcher:test",
            "completed",
            error_code=None,
            error_message_safe=None,
        )

    def test_dispatcher_is_healthy_false_when_disabled(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=False,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        self.assertFalse(consumer.dispatcher_is_healthy(cfg))

    def test_dispatcher_is_healthy_true_for_fresh_heartbeat(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with patch.object(
            consumer.supabase_client,
            "_request_json",
            return_value=[{"worker_id": "run-dispatcher:test", "status": "idle", "last_seen_at": fresh}],
        ):
            self.assertTrue(consumer.dispatcher_is_healthy(cfg))

    def test_run_once_launch_enabled_idle_skips_complete(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer, "reclaim_stale_account_run_requests", return_value=0),
            patch.object(consumer, "claim_next_account_run_request", return_value=None),
            patch.object(consumer, "complete_account_run_request") as complete,
        ):
            result = consumer.run_once(cfg)
        self.assertEqual(result["mode"], "idle")
        complete.assert_not_called()

    def test_run_once_launch_enabled_invalid_claim_row_is_idle_no_complete(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer, "reclaim_stale_account_run_requests", return_value=0),
            patch.object(
                consumer,
                "claim_next_account_run_request",
                return_value={"status": "claimed"},
            ),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_handle_claimed_request") as handle,
        ):
            result = consumer.run_once(cfg)
        self.assertEqual(result["mode"], "idle")
        self.assertEqual(result.get("reason"), "invalid_claim_row")
        complete.assert_not_called()
        handle.assert_not_called()

    def test_handle_claimed_request_invalid_row_does_not_complete(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with patch.object(consumer, "complete_account_run_request") as complete:
            consumer._handle_claimed_request(cfg, {"status": "claimed"})
        complete.assert_not_called()

    def test_launch_preflight_blocks_when_active_queue_present(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.dict("os.environ", {"RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE": "false"}, clear=False),
            patch.object(
                consumer,
                "summarize_active_account_run_requests",
                return_value={
                    "active_count": 1,
                    "requests": [
                        {
                            "request_id": TEST_REQUEST_ID,
                            "account_id": TEST_ACCOUNT_ID,
                            "status": "queued",
                            "requested_run_type": "account_session",
                            "created_at": "2026-06-09T10:00:00+00:00",
                        }
                    ],
                    "read_failed": False,
                    "error": None,
                },
            ),
        ):
            preflight = consumer.evaluate_launch_mode_startup_preflight(cfg)
        self.assertFalse(preflight["ok"])
        self.assertEqual(preflight["reason"], "active_queue_present")
        self.assertEqual(preflight["active_count"], 1)

    def test_launch_preflight_allows_empty_queue(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with patch.object(
            consumer,
            "summarize_active_account_run_requests",
            return_value={"active_count": 0, "requests": [], "read_failed": False, "error": None},
        ):
            preflight = consumer.evaluate_launch_mode_startup_preflight(cfg)
        self.assertTrue(preflight["ok"])
        self.assertEqual(preflight["reason"], "ready")

    def test_launch_preflight_allows_health_only_with_active_queue(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with patch.object(
            consumer,
            "summarize_active_account_run_requests",
            return_value={
                "active_count": 2,
                "requests": [],
                "read_failed": False,
                "error": None,
            },
        ):
            preflight = consumer.evaluate_launch_mode_startup_preflight(cfg)
        self.assertTrue(preflight["ok"])
        self.assertEqual(preflight["mode"], "health_only")

    def test_run_forever_exits_when_launch_preflight_blocks(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "load_dispatcher_config", return_value=cfg),
            patch.object(
                consumer,
                "evaluate_launch_mode_startup_preflight",
                return_value={"ok": False, "reason": "active_queue_present", "active_count": 1, "requests": []},
            ),
            patch.object(consumer, "_heartbeat") as heartbeat,
        ):
            code = consumer.run_forever(cfg)
        self.assertEqual(code, 3)
        heartbeat.assert_not_called()

    def test_run_once_health_only_skips_claim(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "_heartbeat") as heartbeat,
            patch.object(consumer, "reclaim_stale_account_run_requests", return_value=0) as reclaim,
            patch.object(consumer, "claim_next_account_run_request") as claim,
        ):
            result = consumer.run_once(cfg)
        self.assertEqual(result["mode"], "health_only")
        heartbeat.assert_called_once()
        reclaim.assert_called_once()
        claim.assert_not_called()

    def test_handle_claimed_request_passes_assignment_device_serial_to_runner(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=True,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "account_session",
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": "assignment-1",
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "slot_kind": "full_cycle_6h",
            "device_id": "device-1",
            "clone_id": None,
            "app_instance_id": "app-instance-1",
            "app_instance_index": 1,
            "device_kind": "emulator",
            "adb_serial": "emulator-5554",
            "package_name": "com.instagram.androif",
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_resolved",
            "run_type": "account_session",
        }

        class FakeProc:
            def poll(self) -> int:
                return 0

        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(
                consumer,
                "evaluate_queued_run_commercial_policy",
                return_value=(True, None, {}),
            ),
            patch.object(
                consumer,
                "_load_account_protection_snapshot",
                return_value=(
                    True,
                    "ready",
                    '{"ok":true}',
                    {
                        "protection_lists_source": "canonical_v1",
                        "blacklist_count": 0,
                        "unfollow_whitelist_count": 0,
                    },
                ),
            ),
            patch.object(consumer, "transfer_device_lock", return_value={"transferred": True}),
            patch.object(consumer, "renew_device_lock", return_value={"renewed": True}),
            patch.object(consumer, "release_device_lock", return_value={"released": True}),
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer.subprocess, "Popen", return_value=FakeProc()) as popen,
            patch.object(consumer, "_finalize_manual_run_after_subprocess"),
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        cmd = popen.call_args.args[0]
        self.assertIn("--device-serial", cmd)
        self.assertEqual(cmd[cmd.index("--device-serial") + 1], "emulator-5554")
        self.assertIn("--package-name", cmd)
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.androif")
        self.assertIn("--expected-app-instance-id", cmd)
        self.assertEqual(cmd[cmd.index("--expected-app-instance-id") + 1], "app-instance-1")

    def test_build_account_session_runner_command_includes_clone_package(self) -> None:
        cmd = consumer._build_runner_command(
            TEST_ACCOUNT_ID,
            "account_session",
            TEST_REQUEST_ID,
            device_serial="RFGL145LZHE",
            package_name="com.instagram.androif",
            app_instance_id="59f82a36-155c-4073-9765-28dac46b56ff",
        )
        self.assertIn("runner.py", cmd[1])
        self.assertEqual(cmd[cmd.index("--device-serial") + 1], "RFGL145LZHE")
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.androif")
        self.assertEqual(
            cmd[cmd.index("--expected-app-instance-id") + 1],
            "59f82a36-155c-4073-9765-28dac46b56ff",
        )

    def test_build_account_session_runner_command_omits_package_when_unresolved(self) -> None:
        cmd = consumer._build_runner_command(
            TEST_ACCOUNT_ID,
            "account_session",
            TEST_REQUEST_ID,
            device_serial="RFGL145LZHE",
            package_name=None,
            app_instance_id=None,
        )
        self.assertNotIn("--package-name", cmd)
        self.assertNotIn("--expected-app-instance-id", cmd)

    def test_login_subprocess_receives_runner_env_with_adb_path(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="worker-1",
            poll_seconds=1.0,
            lease_seconds=60,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=120,
            require_assignment=True,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "login_provisioning",
            "metadata_safe": {
                "binding_version": "auto_login_app_instance_v1",
                "assignment_id": "assignment-1",
                "device_id": "device-1",
                "app_instance_id": "app-instance-1",
                "package_name": "com.instagram.androie",
                "clone_index": 1,
            },
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": "assignment-1",
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "slot_kind": "full_cycle_6h",
            "device_id": "device-1",
            "clone_id": None,
            "app_instance_id": "app-instance-1",
            "app_instance_index": 1,
            "device_kind": "physical_phone",
            "adb_serial": "RFGL145VCKE",
            "package_name": "com.instagram.androie",
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_resolved",
            "run_type": "login_provisioning",
        }

        class FakeProc:
            def poll(self) -> int:
                return 0

        fake_env = {"PATH": "/usr/bin", "ADB_PATH": "/tmp/platform-tools/adb"}
        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(
                consumer,
                "evaluate_queued_run_commercial_policy",
                return_value=(True, None, {}),
            ),
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer, "transfer_device_lock", return_value={"transferred": True}) as transfer_lock,
            patch.object(consumer, "renew_device_lock", return_value={"renewed": True}) as renew_lock,
            patch.object(consumer, "release_device_lock", return_value={"released": True}) as release_lock,
            patch.object(consumer, "runner_subprocess_env", return_value=fake_env),
            patch.object(consumer, "_create_and_link_login_run", return_value=TEST_RUN_ID),
            patch.object(consumer, "_load_expected_username", return_value="cinema_catchup"),
            patch.object(consumer.subprocess, "Popen", return_value=FakeProc()) as popen,
            patch.object(consumer, "_finalize_manual_run_after_subprocess"),
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        self.assertEqual(popen.call_args.kwargs.get("env"), fake_env)
        self.assertEqual(fake_env["ADB_PATH"], "/tmp/platform-tools/adb")
        self.assertEqual(fake_env["LOGIN_PROVISIONER_PUBLISH_ENABLED"], "true")
        transfer_lock.assert_called_once_with(
            device_id="device-1",
            request_id=TEST_REQUEST_ID,
            new_worker_id="worker-1",
        )
        renew_lock.assert_called_once_with(
            device_id="device-1",
            worker_id="worker-1",
            request_id=TEST_REQUEST_ID,
        )
        release_lock.assert_called_once_with(
            device_id="device-1",
            worker_id="worker-1",
            request_id=TEST_REQUEST_ID,
        )

    def test_build_login_email_code_resume_command(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="cinema_catchup"):
            cmd = consumer._build_runner_command(
                TEST_ACCOUNT_ID,
                "login_email_code_resume",
                TEST_REQUEST_ID,
                device_serial="RFGL145VCKE",
                package_name="com.instagram.androie",
                app_instance_id="7637db9a-3581-4099-8068-d5eb1ed86f96",
                metadata_safe={"action_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
            )
        self.assertIn("-m", cmd)
        self.assertIn("instagram_login_provisioner_cli", cmd)
        self.assertIn("--resume-email-code-from-action", cmd)
        self.assertIn("--verification-action-id", cmd)
        self.assertEqual(cmd[cmd.index("--verification-action-id") + 1], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.androie")
        self.assertEqual(cmd[cmd.index("--expected-app-instance-id") + 1], "7637db9a-3581-4099-8068-d5eb1ed86f96")
        self.assertIn("--publish", cmd)
        self.assertNotIn("--no-publish", cmd)
        self.assertNotIn("verification_code", " ".join(cmd))

    def test_build_login_provisioning_command(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="cinema_catchup"):
            cmd = consumer._build_runner_command(
                TEST_ACCOUNT_ID,
                "login_provisioning",
                TEST_REQUEST_ID,
                device_serial="RFGL145VCKE",
                package_name="com.instagram.androie",
                app_instance_id="7637db9a-3581-4099-8068-d5eb1ed86f96",
            )
        self.assertIn("historical_auto_login_07ee_adapter", cmd)
        self.assertNotIn("instagram_login_provisioner_cli", cmd)
        self.assertEqual(cmd[cmd.index("--request-id") + 1], TEST_REQUEST_ID)
        self.assertNotIn("--resume-email-code-from-action", cmd)
        self.assertIn("--publish", cmd)
        self.assertNotIn("--no-publish", cmd)
        self.assertIn("--expected-username", cmd)
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.androie")
        self.assertEqual(cmd[cmd.index("--expected-app-instance-id") + 1], "7637db9a-3581-4099-8068-d5eb1ed86f96")

    def test_login_provisioner_env_enables_controlled_publish(self) -> None:
        with patch.object(consumer, "runner_subprocess_env", return_value={"PATH": "/usr/bin"}):
            env = consumer._login_provisioner_env()
        self.assertEqual(env["LOGIN_PROVISIONER_PUBLISH_ENABLED"], "true")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_handle_claimed_request_blocks_assignment_device_without_serial(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "account_session",
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": False,
            "assignment_id": "assignment-1",
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "device_id": "device-1",
            "app_instance_id": "app-instance-1",
            "adb_serial": None,
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_device_missing_adb_serial",
            "run_type": "account_session",
        }

        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(
                consumer,
                "evaluate_queued_run_commercial_policy",
                return_value=(True, None, {}),
            ),
            patch.object(consumer.subprocess, "Popen") as popen,
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        popen.assert_not_called()
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs["error_code"], "assignment_device_missing_adb_serial")

    def test_handle_claimed_request_blocks_login_run_without_device_serial(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_email_code_resume"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "login_email_code_resume",
            "metadata_safe": {
                "action_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "binding_version": "auto_login_app_instance_v1",
                "assignment_id": "assignment-1",
                "device_id": "device-1",
                "app_instance_id": "app-instance-1",
                "package_name": "com.instagram.androie",
                "clone_index": 1,
            },
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": "assignment-1",
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "device_id": "device-1",
            "app_instance_id": "app-instance-1",
            "app_instance_index": 1,
            "package_name": "com.instagram.androie",
            "adb_serial": "",
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_resolved",
            "run_type": "login_email_code_resume",
        }

        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(
                consumer,
                "evaluate_queued_run_commercial_policy",
                return_value=(True, None, {}),
            ),
            patch.object(consumer, "transfer_device_lock", return_value={"transferred": True}),
            patch.object(consumer, "renew_device_lock", return_value={"renewed": True}),
            patch.object(consumer, "release_device_lock", return_value={"released": True}) as release_lock,
            patch.object(consumer.subprocess, "Popen") as popen,
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_audit"),
            patch.object(consumer, "_publish_auto_login_dispatch_failure") as publish_failure,
        ):
            consumer._handle_claimed_request(cfg, request)

        popen.assert_not_called()
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs["error_code"], "login_device_serial_required")
        self.assertEqual(publish_failure.call_args.kwargs["phase"], "open_instagram")
        self.assertEqual(publish_failure.call_args.kwargs["reason_code"], "login_device_serial_required")
        release_lock.assert_called_once_with(
            device_id="device-1",
            worker_id="run-dispatcher:test",
            request_id=TEST_REQUEST_ID,
        )

    def test_finalize_subprocess_nonzero_reconciles_linked_run(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "status": "running",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "failed")
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.kwargs["terminal_status"], "failed")
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_failed")

    def test_historical_login_provisioning_bypasses_modern_verification_pause(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "requested_run_type": "login_provisioning",
            "status": "running",
        }
        summary = {
            "run_id": TEST_RUN_ID,
            "final_outcome": "verification_pending",
            "dashboard_action_type": "enter_email_verification_code",
            "final_login_status": "verification_pending",
            "final_provisioning_status": "login_verification_pending",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary) as modern_summary,
            patch.object(consumer, "_terminalize_auto_login_failure") as modern_terminalization,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "failed")
        self.assertEqual(complete.call_args.kwargs["error_code"], "worker_exit_nonzero")
        reconcile.assert_called_once()
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_failed")
        modern_summary.assert_not_called()
        modern_terminalization.assert_not_called()

    def test_historical_login_provisioning_bypasses_modern_orphan_classification(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "requested_run_type": "login_provisioning",
            "status": "running",
        }
        summary = {
            "run_id": TEST_RUN_ID,
            "final_outcome": "blocked",
            "reason": "orphan_challenge_provenance_weak",
            "failure_reason": "pre_input_challenge_orphan",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary) as modern_summary,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "failed")
        self.assertEqual(complete.call_args.kwargs["error_code"], "worker_exit_nonzero")
        reconcile.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_failed")
        modern_summary.assert_not_called()

    def test_finalize_subprocess_real_failure_still_marks_failed(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "status": "running",
        }
        summary = {
            "run_id": TEST_RUN_ID,
            "final_outcome": "wrong_app_package",
            "submit_executed": False,
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary),
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "failed")
        reconcile.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_failed")

    def test_historical_login_provisioning_does_not_use_modern_failure_summary(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "requested_run_type": "login_provisioning",
            "status": "running",
        }
        summary = {"run_id": TEST_RUN_ID, "final_outcome": "wrong_app_package", "submit_executed": False}
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}),
            patch.object(consumer, "_terminalize_auto_login_failure") as modern_terminalization,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary) as modern_summary,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        self.assertEqual(complete.call_args.args[2], "failed")
        self.assertEqual(complete.call_args.kwargs["error_code"], "worker_exit_nonzero")
        self.assertEqual(audit.call_args.kwargs["payload"], {"request_id": TEST_REQUEST_ID, "exit_code": 1})
        modern_terminalization.assert_not_called()
        modern_summary.assert_not_called()

    def test_historical_login_provisioning_bypasses_modern_failure_contract_and_incident(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "requested_run_type": "login_provisioning",
            "status": "running",
            "device_id": "device-1",
            "app_instance_id": "instance-1",
        }
        summary = {
            "run_id": TEST_RUN_ID,
            "final_outcome": "mismatch",
            "reason": "wrong_suggested_account_requires_admin_review",
            "submit_executed": False,
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}),
            patch.object(consumer, "_terminalize_auto_login_failure") as terminalize,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary) as modern_summary,
            patch.object(consumer, "_publish_run_failure_incident") as publish,
            patch.object(consumer, "_audit"),
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
                request_snapshot=request,
            )
        self.assertEqual(complete.call_args.args[2], "failed")
        self.assertEqual(complete.call_args.kwargs["error_code"], "worker_exit_nonzero")
        terminalize.assert_not_called()
        modern_summary.assert_not_called()
        publish.assert_not_called()

    def test_email_resume_keeps_modern_atomic_terminalization_before_notification(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_email_code_resume"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "requested_run_type": "login_email_code_resume",
            "status": "running",
        }
        summary = {"failure_reason": "password_field_not_found", "phase": "login_form"}
        call_order: list[str] = []

        def terminalize(**_kwargs):
            call_order.append("terminalized")
            return (
                {
                    "request_status": "failed",
                    "run_status": "failed",
                    "persisted_error_code": "credential_input_field_unavailable",
                    "reason": "terminalized",
                },
                {
                    "domain": "auto_login",
                    "reason_code": "credential_input_field_unavailable",
                    "phase": "login_form",
                },
            )

        def publish(**_kwargs):
            call_order.append("notification_failed")
            raise RuntimeError("notification transport unavailable")

        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary),
            patch.object(consumer, "_terminalize_auto_login_failure", side_effect=terminalize),
            patch.object(consumer, "_publish_run_failure_incident", side_effect=publish),
            patch.object(consumer, "_audit"),
        ):
            with self.assertRaisesRegex(RuntimeError, "notification transport unavailable"):
                consumer._finalize_manual_run_after_subprocess(
                    cfg,
                    request_id=TEST_REQUEST_ID,
                    account_id=TEST_ACCOUNT_ID,
                    exit_code=1,
                    request_snapshot=request,
                )

        self.assertEqual(call_order, ["terminalized", "notification_failed"])

    def test_finalize_subprocess_without_linked_run_skips_reconcile_patch(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(
                consumer,
                "get_account_run_request",
                return_value={"id": TEST_REQUEST_ID, "status": "running"},
            ),
            patch.object(consumer, "complete_account_run_request"),
            patch.object(
                consumer,
                "reconcile_linked_ig_run_terminal",
                return_value={"reconciled": False, "reason": "no_run_id"},
            ) as reconcile,
            patch.object(consumer, "_audit"),
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id="req-2",
                account_id="acct-1",
                exit_code=1,
            )
        reconcile.assert_called_once()
        self.assertIsNone(reconcile.call_args.kwargs.get("run_id"))

    def test_follow60_terminal_summary_precedes_single_atomic_terminal_patch(self) -> None:
        call_order: list[str] = []

        def request_json(method, endpoint, **_kwargs):
            if endpoint == "ig_interaction_events":
                call_order.append("canonical_events_read")
                return [
                    {
                        "id": "event-follow",
                        "event_type": "follow_verified_persisted_v1",
                        "event_status": "success",
                        "username": "candidate",
                        "payload": {},
                        "stage_idempotency_key": None,
                    },
                    {
                        "id": "event-like",
                        "event_type": "post_like_success",
                        "event_status": "success",
                        "username": "candidate",
                        "payload": {"liked_count": 1},
                        "stage_idempotency_key": "action:like_verified",
                    },
                ]
            if endpoint == "ig_runs":
                call_order.append("active_run_read")
                return [
                    {
                        "id": TEST_RUN_ID,
                        "account_id": TEST_CANARY_ACCOUNT_ID,
                        "status": "running",
                    }
                ]
            raise AssertionError((method, endpoint))

        def update_run_status(_run_id, status, totals, summary):
            call_order.append("atomic_terminal_patch")
            self.assertEqual(status, "stopped")
            self.assertEqual(totals["total"], 2)
            self.assertEqual(summary["session_counters"]["follows"], 1)
            self.assertTrue(summary["terminalized_after_worker_exit"])

        with (
            patch.object(consumer, "_follow60_control_applies", return_value=True),
            patch.object(consumer.supabase_client, "_request_json", side_effect=request_json),
            patch.object(consumer.supabase_client, "update_run_status", side_effect=update_run_status),
            patch.object(consumer, "reconcile_linked_ig_run_terminal") as generic_reconcile,
            patch.object(consumer, "_audit"),
        ):
            result = consumer._reconcile_linked_run(
                account_id=TEST_CANARY_ACCOUNT_ID,
                run_id=TEST_RUN_ID,
                terminal_status="canceled",
                request_id=TEST_REQUEST_ID,
                exit_code=-15,
            )

        self.assertTrue(result["reconciled"])
        self.assertEqual(
            call_order,
            ["canonical_events_read", "active_run_read", "atomic_terminal_patch"],
        )
        generic_reconcile.assert_not_called()

    def test_wait_for_subprocess_terminates_on_cancel_request(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )

        class FakeProc:
            def __init__(self) -> None:
                self.terminated = False

            def poll(self) -> int | None:
                return None

            def send_signal(self, _signal: int) -> None:
                self.terminated = True

            def wait(self, timeout: int | None = None) -> int:
                return -15

            def kill(self) -> None:
                raise AssertionError("kill should not be needed after SIGTERM")

        proc = FakeProc()
        with (
            patch.object(
                consumer,
                "get_account_run_request",
                return_value={
                    "id": TEST_REQUEST_ID,
                    "status": "running",
                    "cancel_requested_at": "now",
                },
            ),
            patch.object(consumer, "log"),
        ):
            exit_code, timed_out = consumer._wait_for_subprocess(
                cfg,
                proc,  # type: ignore[arg-type]
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
            )
        self.assertTrue(proc.terminated)
        self.assertEqual(exit_code, -15)
        self.assertFalse(timed_out)

    def test_build_orphan_recovery_command_uses_recovery_cli(self) -> None:
        cmd = consumer._build_orphan_recovery_command(
            TEST_ACCOUNT_ID,
            TEST_REQUEST_ID,
            device_serial="RFGL145LZHE",
            package_name="com.instagram.androie",
            app_instance_id="clone-1",
            assignment_id="assignment-1",
            credentials_version=1,
        )
        self.assertIn("login_orphan_challenge_recovery_cli", cmd)
        self.assertIn("--json", cmd)
        self.assertIn(TEST_ACCOUNT_ID, cmd)


if __name__ == "__main__":
    unittest.main()
