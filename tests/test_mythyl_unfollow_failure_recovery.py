from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import account_run_request_consumer as consumer
import account_session_orchestrator as session_orchestrator
import incident_notifications
import unfollow_session_orchestrator as unfollow_orchestrator
from account_session_reliability_schema import (
    build_admin_reliability_snapshot,
    build_escalation_event,
)
from account_session_resume_engine import build_account_session_resume_plan
from runtime_incident_matrix import (
    classify_recoverable_python_retry,
    classify_terminal_run_failure,
)


MYTHYL_FAILURE = "logs.log() got multiple values for keyword argument 'stop_reason'"


def _real_unfollow_failure() -> dict:
    return {
        "enabled": True,
        "executed": True,
        "status": "failed_exception",
        "failure_reason": MYTHYL_FAILURE,
        "real_max_actions": 120,
        "real_max_actions_effective": 120,
        "unfollow_actions_sent": 0,
        "unfollow_actions_verified": 0,
        "unfollow_results_persisted_count": 0,
    }


def _mythyl_summary() -> dict:
    return {
        "account_id": "0d299d1e-46ee-49d2-8a84-4f928f2bb182",
        "account_username": "mythyl_fitness",
        "run_id": "0f37f0fb-0204-4295-87aa-d14ee6d1d82e",
        "session_status": "failed",
        "session_termination_class": "partial_resumable",
        "restart_eligibility": "eligible",
        "welcome_enabled": False,
        "welcome_phase_status": "not_planned",
        "follow_phase_status": "completed",
        "unfollow_phase_status": "failed",
        "follow_quota_target": 40,
        "follows_completed_count": 40,
        "follow_quota_remaining": 0,
        "mandatory_unfollow_executed": False,
        "root_failure_code": "unfollow_runtime_exception",
        "failure_phase": "unfollow",
        "failure_module": "unfollow_session_orchestrator",
        "failure_function": "_run_real_unfollow_multi_loop",
        "specific_failure_reason": MYTHYL_FAILURE,
        "failure_category": "recoverable_python_runtime_failure",
        "failure_signature": "python:unfollow:duplicate_stop_reason",
        "business_session_id": "0f37f0fb-0204-4295-87aa-d14ee6d1d82e",
        "attempt_id": 1,
        "retry_index": 0,
        "follow_to_unfollow_real": _real_unfollow_failure(),
    }


class MythylUnfollowFailureRecoveryTest(unittest.TestCase):
    def test_one_recoverable_cta_failure_cannot_be_configured_to_stop_the_phase(self) -> None:
        with patch.object(
            unfollow_orchestrator.config,
            "UNFOLLOW_SESSION_MAX_RECOVERABLE_ACTION_FAILURES",
            0,
            create=True,
        ):
            self.assertEqual(unfollow_orchestrator._max_recoverable_action_failures(), 1)

    def test_recoverable_cta_path_uses_bounded_surface_recovery_and_partial_exit(self) -> None:
        source = inspect.getsource(unfollow_orchestrator._run_real_unfollow_multi_loop)
        self.assertIn("recover_following_viewport(", source)
        self.assertIn("coverage_tracker.mark_candidate_retryable(target_key)", source)
        self.assertIn('"success_real_unfollow_multi_partial_exhausted"', source)
        self.assertNotIn(
            'is_recoverable_action_sheet_failure(sheet, return_ok=return_ok)',
            source,
        )
    def test_749800d_duplicate_stop_reason_regression_is_removed(self) -> None:
        source = inspect.getsource(unfollow_orchestrator._run_real_unfollow_multi_loop)
        event_block = source.split('"unfollow_ui_coverage_viewport_decision"', 1)[1].split(
            "if coverage_decision.action", 1
        )[0]
        self.assertIn("**coverage_tracker.summary()", event_block)
        self.assertNotIn("stop_reason=coverage_decision.stop_reason", event_block)

    def test_exact_mythyl_failure_is_recoverable_after_safe_unfollow_entry(self) -> None:
        termination = session_orchestrator._session_termination_class(
            session_status="failed",
            follow_phase_executed=True,
            follow_exit_code=0,
            follow_quota_remaining=0,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real=_real_unfollow_failure(),
            follow_phase_skipped_reason=None,
            transition_reason="welcome_disabled_bypass",
            follow_session_outcome="global_follow_cap_reached",
        )
        self.assertEqual(termination, "partial_resumable")

    def test_unrelated_unfollow_exception_is_not_automatically_resumable(self) -> None:
        real = _real_unfollow_failure()
        real["failure_reason"] = "unexpected unrelated crash"
        termination = session_orchestrator._session_termination_class(
            session_status="failed",
            follow_phase_executed=True,
            follow_exit_code=0,
            follow_quota_remaining=0,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real=real,
            follow_phase_skipped_reason=None,
            transition_reason="welcome_disabled_bypass",
            follow_session_outcome="global_follow_cap_reached",
        )
        self.assertEqual(termination, "non_recoverable_failure")

    def test_resume_plan_preserves_remaining_unfollow_quota(self) -> None:
        plan = build_account_session_resume_plan(_mythyl_summary())
        self.assertTrue(plan["restart_allowed"])
        self.assertEqual(plan["restart_block_reason"], "")
        self.assertEqual(plan["phases_to_run"], {"welcome": False, "follow": False, "unfollow": True})
        self.assertEqual(plan["quota_targets"]["unfollow"], 120)
        self.assertEqual(plan["quota_remaining"]["unfollow"], 120)

    def test_unfollow_only_resume_keeps_follow_closed_on_next_partial_checkpoint(self) -> None:
        plan = build_account_session_resume_plan(
            {
                "session_termination_class": "partial_resumable",
                "restart_eligibility": "eligible",
                "business_session_id": "j-automatise-business-session",
                "welcome_enabled": False,
                "follow_phase_status": "skipped_cleanly",
                "current_attempt_phases_to_run": {
                    "welcome": False,
                    "follow": False,
                    "unfollow": True,
                },
                "mandatory_unfollow_executed": True,
                "unfollow_outcome": {
                    "phase_status": "partial_resumable",
                    "planned_candidate_count": 27,
                    "persisted_count": 24,
                    "remaining_count": 3,
                    "last_safe_checkpoint": "following_list_after_scroll",
                    "resume_recommended": True,
                },
            }
        )

        self.assertTrue(plan["restart_allowed"])
        self.assertEqual(plan["restart_block_reason"], "")
        self.assertEqual(
            plan["phases_to_run"],
            {"welcome": False, "follow": False, "unfollow": True},
        )
        self.assertEqual(plan["quota_remaining"]["follow"], 0)
        self.assertEqual(plan["quota_remaining"]["unfollow"], 3)

    def test_recoverable_failure_does_not_require_human_review(self) -> None:
        summary = _mythyl_summary()
        plan = build_account_session_resume_plan(summary)
        snapshot = build_admin_reliability_snapshot(summary, plan)
        self.assertNotIn("mandatory_unfollow_missing", snapshot["badges"])
        self.assertNotIn("needs_human_review", snapshot["badges"])
        event = build_escalation_event(summary, plan)
        self.assertIsNotNone(event)
        self.assertEqual(
            event["event_type"],
            "recoverable_python_failure_restart_1_scheduled",
        )
        self.assertEqual(event["severity"], "info")

    def test_initial_failure_schedules_restart_one_silently(self) -> None:
        summary = _mythyl_summary()
        decision = classify_recoverable_python_retry(
            performance_summary=summary,
            request_metadata={},
            run_id=summary["run_id"],
            cleanup_completed=True,
            lock_released=True,
        )
        self.assertTrue(decision.applies)
        self.assertTrue(decision.restart_allowed)
        self.assertFalse(decision.notify_informational)
        self.assertEqual(
            decision.event_type,
            "recoverable_python_failure_restart_1_scheduled",
        )

    def test_first_retry_failure_schedules_restart_two_silently(self) -> None:
        summary = _mythyl_summary()
        decision = classify_recoverable_python_retry(
            performance_summary=summary,
            request_metadata={
                "business_session_id": summary["business_session_id"],
                "attempt_id": 2,
                "retry_index": 1,
                "prior_run_id": summary["run_id"],
            },
            run_id="retry-run-1",
            cleanup_completed=True,
            lock_released=True,
        )
        self.assertTrue(decision.restart_allowed)
        self.assertFalse(decision.notify_informational)
        self.assertEqual(
            decision.event_type,
            "recoverable_python_failure_restart_2_scheduled",
        )

    def test_second_retry_failure_exhausts_without_third_request(self) -> None:
        summary = _mythyl_summary()
        decision = classify_recoverable_python_retry(
            performance_summary=summary,
            request_metadata={
                "business_session_id": summary["business_session_id"],
                "attempt_id": 3,
                "retry_index": 2,
                "prior_run_id": "retry-run-1",
            },
            run_id="retry-run-2",
            cleanup_completed=True,
            lock_released=True,
        )
        self.assertFalse(decision.restart_allowed)
        self.assertTrue(decision.retries_exhausted)
        self.assertTrue(decision.notify_informational)
        self.assertIsNone(decision.next_attempt_id)
        self.assertIsNone(decision.next_retry_index)
        self.assertEqual(
            decision.event_type,
            "recoverable_python_bug_retries_exhausted",
        )

    def test_cleanup_uncertain_and_unsafe_marker_block_silent_retry(self) -> None:
        summary = _mythyl_summary()
        cleanup = classify_recoverable_python_retry(
            performance_summary=summary,
            run_id=summary["run_id"],
            cleanup_completed=False,
            lock_released=True,
        )
        self.assertFalse(cleanup.restart_allowed)
        self.assertEqual(cleanup.block_reason, "cleanup_uncertain")
        unsafe_summary = {**summary, "unsafe_markers": ["challenge"]}
        unsafe = classify_recoverable_python_retry(
            performance_summary=unsafe_summary,
            run_id=summary["run_id"],
            cleanup_completed=True,
            lock_released=True,
        )
        self.assertFalse(unsafe.restart_allowed)
        self.assertIn("challenge", unsafe.block_reason)

    def test_resume_counter_is_two_retries_three_total_attempts(self) -> None:
        initial = build_account_session_resume_plan(_mythyl_summary())
        self.assertEqual(initial["current_attempt_id"], 1)
        self.assertEqual(initial["next_attempt_id"], 2)
        self.assertEqual(initial["next_retry_index"], 1)
        self.assertEqual(initial["auto_restart_max_retries_after_initial_failure"], 2)
        self.assertEqual(initial["total_attempts_allowed"], 3)
        second_retry_summary = {**_mythyl_summary(), "attempt_id": 3, "retry_index": 2}
        exhausted = build_account_session_resume_plan(
            second_retry_summary,
            settings={"current_attempt_id": 3},
        )
        self.assertFalse(exhausted["restart_allowed"])
        self.assertEqual(exhausted["restart_block_reason"], "auto_restart_retries_exhausted")
        self.assertIsNone(exhausted["next_attempt_id"])
        self.assertIsNone(exhausted["next_retry_index"])

    def test_informational_message_is_exact_and_contains_no_operator_action(self) -> None:
        payload = incident_notifications.build_python_retries_exhausted_notification(
            account_username="mythyl_fitness",
            run_id="0f37f0fb-0204-4295-87aa-d14ee6d1d82e",
            business_session_id="0f37f0fb-0204-4295-87aa-d14ee6d1d82e",
            phase="unfollow",
            root_failure_code="unfollow_runtime_exception",
            failure_signature="python:unfollow:duplicate_stop_reason",
            quota_remaining={"unfollow": 120},
            cleanup_completed=True,
        )
        self.assertEqual(payload["title"], "[INFO] Python runtime retries exhausted")
        expected_lines = [
            "[INFO] Python runtime retries exhausted",
            "Le run de mythyl_fitness a rencontré un bug Python interne.",
            "Deux relances automatiques ont été effectuées sans succès.",
            "Aucun challenge, aucune restriction et aucun risque Instagram n’ont été détectés.",
            "Aucune action sur le compte Instagram, aucun patch manuel du compte et aucune review opérateur ne sont nécessaires.",
            "La campagne n’est pas bloquée pour une raison de sécurité.",
            "L’événement est transmis à l’équipe technique à titre informatif.",
        ]
        self.assertEqual(payload["text"].splitlines()[:7], expected_lines)
        self.assertIn("Relances effectuées: 2/2", payload["text"])
        self.assertNotIn("Operator action required", payload["text"])
        self.assertNotIn("Campaign blocked", payload["text"])
        self.assertNotIn("Review the internal details before resuming", payload["text"])
        self.assertNotIn("Account at risk", payload["text"])

    def test_consumer_suppresses_incident_and_notification_before_exhaustion(self) -> None:
        run_id = _mythyl_summary()["run_id"]
        run_row = {"id": run_id, "status": "failed", "performance_summary": _mythyl_summary()}
        with (
            patch.object(consumer.supabase_client, "load_run_row", return_value=run_row),
            patch("account_session_resume_plan_store.record_automatic_retry_terminal_state", return_value={"persisted": True}),
            patch.object(consumer.runtime_incidents, "publish_account_incident") as publish,
            patch.object(
                consumer.incident_notifications,
                "dispatch_python_retries_exhausted_notification",
            ) as notify,
        ):
            consumer._publish_run_failure_incident(
                request_id="7d3b4a4d-0000-4e0f-8a53-51e529a0a001",
                account_id=_mythyl_summary()["account_id"],
                run_id=run_id,
                run_type="account_session",
                exit_code=1,
                timed_out=False,
                canceled=False,
                request_metadata={},
                cleanup_completed=True,
                lock_released=True,
            )
        publish.assert_not_called()
        notify.assert_not_called()

    def test_consumer_notifies_once_only_after_second_retry_failure(self) -> None:
        summary = {**_mythyl_summary(), "attempt_id": 3, "retry_index": 2}
        run_id = "2f37f0fb-0204-4295-87aa-d14ee6d1d82e"
        run_row = {"id": run_id, "status": "failed", "performance_summary": summary}
        metadata = {
            "business_session_id": summary["business_session_id"],
            "attempt_id": 3,
            "retry_index": 2,
            "prior_run_id": "1f37f0fb-0204-4295-87aa-d14ee6d1d82e",
        }
        with (
            patch.object(consumer.supabase_client, "load_run_row", return_value=run_row),
            patch("account_session_resume_plan_store.record_automatic_retry_terminal_state", return_value={"persisted": True}),
            patch.object(consumer.supabase_client, "insert_runtime_event") as event,
            patch.object(consumer.supabase_client, "get_account_username", return_value="mythyl_fitness"),
            patch.object(
                consumer.incident_notifications,
                "dispatch_python_retries_exhausted_notification",
            ) as notify,
            patch.object(consumer.runtime_incidents, "publish_account_incident") as publish,
        ):
            consumer._publish_run_failure_incident(
                request_id="8d3b4a4d-0000-4e0f-8a53-51e529a0a002",
                account_id=summary["account_id"],
                run_id=run_id,
                run_type="account_session",
                exit_code=1,
                timed_out=False,
                canceled=False,
                request_metadata=metadata,
                cleanup_completed=True,
                lock_released=True,
            )
        publish.assert_not_called()
        event.assert_called_once()
        self.assertEqual(
            event.call_args.args[0]["event_type"],
            "recoverable_python_bug_retries_exhausted",
        )
        notify.assert_called_once()

    def test_successful_second_retry_finishes_without_notification(self) -> None:
        metadata = {
            "source": "auto_restart_tick",
            "auto_restart": True,
            "failure_category": "recoverable_python_runtime_failure",
            "business_session_id": _mythyl_summary()["business_session_id"],
            "attempt_id": 3,
            "retry_index": 2,
            "previous_run_id": "retry-run-1",
        }
        latest = {
            "id": "request-2",
            "run_id": "retry-run-2",
            "requested_run_type": "account_session",
            "status": "claimed",
            "metadata_safe": metadata,
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=latest),
            patch.object(
                consumer.supabase_client,
                "load_run_row",
                return_value={"performance_summary": {"phase_terminal_contract": {"ok": True}}},
            ),
            patch.object(consumer, "_safe_complete_account_run_request"),
            patch.object(consumer, "_reconcile_linked_run"),
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value={}),
            patch.object(consumer, "_audit"),
            patch(
                "account_session_resume_plan_store.mark_automatic_retry_success"
            ) as mark_success,
            patch.object(
                consumer.incident_notifications,
                "dispatch_python_retries_exhausted_notification",
            ) as notify,
            patch.object(consumer.runtime_incidents, "publish_account_incident") as publish,
        ):
            consumer._finalize_manual_run_after_subprocess(
                SimpleNamespace(worker_id="dispatcher-test"),
                request_id="request-2",
                account_id=_mythyl_summary()["account_id"],
                exit_code=0,
                cleanup_completed=True,
                lock_released=True,
            )
        mark_success.assert_called_once_with(
            run_id="retry-run-2",
            request_metadata=metadata,
        )
        notify.assert_not_called()
        publish.assert_not_called()

    def test_exhausted_notification_delivery_is_atomically_deduplicated(self) -> None:
        rows_by_key: dict[str, dict] = {}

        def fake_request(method, table, *, query=None, body=None, **kwargs):
            self.assertEqual(table, "auto_restart_decisions")
            query = dict(query or {})
            if method == "GET":
                key = str(query.get("idempotency_key") or "").removeprefix("eq.")
                return [rows_by_key[key]] if key in rows_by_key else []
            if method == "POST":
                key = str((body or {}).get("idempotency_key") or "")
                if key in rows_by_key:
                    return []
                row = {"id": "notification-decision-1", **dict(body or {})}
                rows_by_key[key] = row
                return [row]
            if method == "PATCH":
                return []
            self.fail(f"unexpected method: {method}")

        kwargs = {
            "account_id": _mythyl_summary()["account_id"],
            "account_username": "mythyl_fitness",
            "run_id": "retry-run-2",
            "request_id": "request-2",
            "business_session_id": _mythyl_summary()["business_session_id"],
            "phase": "unfollow",
            "root_failure_code": "unfollow_runtime_exception",
            "failure_signature": "python:unfollow:duplicate_stop_reason",
            "quota_remaining": {"unfollow": 120},
            "cleanup_completed": True,
        }
        with (
            patch.object(incident_notifications, "_notifications_enabled", return_value=True),
            patch.object(incident_notifications, "_dry_run_enabled", return_value=False),
            patch.object(incident_notifications, "_load_retry_proof", return_value={1, 2}),
            patch.object(
                incident_notifications,
                "resolve_dispatch_channels",
                return_value=(["slack"], {"slack"}),
            ),
            patch.object(
                incident_notifications.supabase_client,
                "_request_json",
                side_effect=fake_request,
            ),
            patch.object(
                incident_notifications,
                "send_notification_webhook",
                return_value={"ok": True, "response_status": 200},
            ) as send,
        ):
            first = incident_notifications.dispatch_python_retries_exhausted_notification(**kwargs)
            second = incident_notifications.dispatch_python_retries_exhausted_notification(**kwargs)
        self.assertEqual(first["sent_count"], 1)
        self.assertEqual(second["sent_count"], 0)
        self.assertEqual(second["skipped_count"], 1)
        send.assert_called_once()

    def test_unsafe_marker_during_retry_uses_security_incident_path(self) -> None:
        summary = {
            **_mythyl_summary(),
            "attempt_id": 2,
            "retry_index": 1,
            "unsafe_markers": ["challenge"],
        }
        run_id = "retry-run-1"
        with (
            patch.object(
                consumer.supabase_client,
                "load_run_row",
                return_value={"id": run_id, "status": "failed", "performance_summary": summary},
            ),
            patch.object(consumer.supabase_client, "get_account_username", return_value="mythyl_fitness"),
            patch.object(
                consumer.runtime_incidents,
                "publish_account_incident",
                return_value={"incident_id": "incident-security"},
            ) as publish,
            patch.object(consumer, "_upsert_operator_review_action", return_value={}),
            patch.object(consumer, "_audit"),
            patch("account_session_resume_plan_store.record_terminal_failure"),
            patch.object(
                consumer.incident_notifications,
                "dispatch_python_retries_exhausted_notification",
            ) as technical_notify,
        ):
            consumer._publish_run_failure_incident(
                request_id="request-1",
                account_id=summary["account_id"],
                run_id=run_id,
                run_type="account_session",
                exit_code=1,
                timed_out=False,
                canceled=False,
                request_metadata={
                    "business_session_id": summary["business_session_id"],
                    "attempt_id": 2,
                    "retry_index": 1,
                    "prior_run_id": summary["run_id"],
                },
                cleanup_completed=True,
                lock_released=True,
            )
        publish.assert_called_once()
        technical_notify.assert_not_called()

    def test_specific_failure_reason_is_redacted_for_notifications(self) -> None:
        summary = _mythyl_summary()
        summary["restart_allowed"] = True
        summary["specific_failure_reason"] = "token=secret /Users/admin/private.xml <node>raw</node>"
        decision = classify_terminal_run_failure(
            exit_code=1,
            run_status="failed",
            performance_summary=summary,
        )
        safe = str(decision.metadata_safe)
        self.assertNotIn("secret", safe)
        self.assertNotIn("/Users/admin", safe)
        self.assertNotIn("<node>", safe)


if __name__ == "__main__":
    unittest.main()
