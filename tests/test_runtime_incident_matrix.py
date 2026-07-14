from __future__ import annotations

import unittest

from runtime_incident_matrix import (
    build_incident_dedupe_key,
    build_run_failure_incident_payload,
    classify_terminal_run_failure,
)

ACCOUNT_ID = "e9c7462b-fc0e-46c9-8d40-1e07e0f6a41b"
RUN_ID = "9e46c4a5-72c5-4b16-9f0f-96f6f2ff11aa"
REQUEST_ID = "7d3b4a4d-0000-4e0f-8a53-51e529a0a001"


class ClassifyTerminalRunFailureTest(unittest.TestCase):
    def test_identity_username_not_detected_creates_true_reason_incident(self) -> None:
        # F1: the Mythyl case must never collapse into worker_exit_nonzero.
        decision = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "actual_logged_in_username_not_detected",
            },
        )
        self.assertTrue(decision.should_publish)
        self.assertEqual(decision.incident_type, "run_identity_verification_failed")
        self.assertEqual(decision.reason_code, "actual_logged_in_username_not_detected")
        self.assertEqual(decision.severity, "critical")
        self.assertTrue(decision.notify_channels)
        self.assertTrue(decision.requires_operator_review)
        self.assertTrue(decision.blocking_campaign)
        self.assertIn("active Instagram account could not be confirmed", decision.action_required)
        self.assertIn("Human review", decision.action_required)

    def test_identity_mismatch_creates_distinct_incident(self) -> None:
        # F2: detected-but-wrong username is a distinct incident type.
        decision = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "active_instagram_account_mismatch",
            },
        )
        self.assertTrue(decision.should_publish)
        self.assertEqual(decision.incident_type, "active_instagram_account_mismatch")
        self.assertEqual(decision.reason_code, "active_instagram_account_mismatch")
        self.assertEqual(decision.severity, "critical")

    def test_structured_reason_wins_over_exit_code_fallback(self) -> None:
        # F3: a structured worker error must beat worker_exit_nonzero.
        decision = classify_terminal_run_failure(
            exit_code=1,
            performance_summary={"reason": "device_disconnected"},
        )
        self.assertTrue(decision.should_publish)
        self.assertEqual(decision.incident_type, "run_device_unavailable")
        self.assertEqual(decision.reason_code, "device_disconnected")
        self.assertNotEqual(decision.reason_code, "worker_exit_nonzero")

    def test_exit_nonzero_without_detail_falls_back_controlled(self) -> None:
        # F4: fallback only when nothing structured was transmitted.
        decision = classify_terminal_run_failure(exit_code=1, performance_summary=None)
        self.assertTrue(decision.should_publish)
        self.assertEqual(decision.incident_type, "run_worker_failure")
        self.assertEqual(decision.reason_code, "worker_exit_nonzero")
        self.assertEqual(decision.severity, "error")

    def test_manual_stop_paths_produce_no_incident(self) -> None:
        # F5: normal voluntary stops never produce incidents.
        self.assertFalse(
            classify_terminal_run_failure(exit_code=143, canceled=True).should_publish
        )
        self.assertFalse(
            classify_terminal_run_failure(exit_code=143, run_status="stopped").should_publish
        )
        self.assertFalse(
            classify_terminal_run_failure(exit_code=143).should_publish
        )
        self.assertFalse(
            classify_terminal_run_failure(exit_code=0).should_publish
        )

    def test_scheduler_gate_reasons_are_never_incidentable(self) -> None:
        # F6: normal scheduler gates never notify Slack/Discord.
        for reason in (
            "scheduler_disabled",
            "resume_plan_missing",
            "manual_only_requires_manual_trigger",
            "schedule_window_closed",
            "no_eligible_targets",
            "daily_cap_reached",
        ):
            decision = classify_terminal_run_failure(
                exit_code=1,
                performance_summary={"reason": reason},
            )
            self.assertFalse(decision.should_publish, msg=reason)
            self.assertFalse(decision.notify_channels, msg=reason)

    def test_package_unavailable_reason(self) -> None:
        decision = classify_terminal_run_failure(
            exit_code=3,
            performance_summary={"reason": "instagram_not_foreground"},
        )
        self.assertEqual(decision.incident_type, "assigned_instagram_package_unavailable")
        self.assertEqual(decision.reason_code, "instagram_not_foreground")

    def test_login_required_reason(self) -> None:
        decision = classify_terminal_run_failure(
            exit_code=1,
            performance_summary={"reason": "login_required"},
        )
        self.assertEqual(decision.incident_type, "account_login_required")
        self.assertEqual(decision.severity, "critical")

    def test_dispatcher_timeout_incident(self) -> None:
        decision = classify_terminal_run_failure(exit_code=143, timed_out=True)
        self.assertTrue(decision.should_publish)
        self.assertEqual(decision.incident_type, "run_worker_failure")
        self.assertEqual(decision.reason_code, "subprocess_timeout")

    def test_welcome_surface_failures_require_operator_review(self) -> None:
        for reason in (
            "welcome_surface_unstable",
            "followers_surface_missing_at_start",
            "recovered_snapshot_rejected",
            "followers_suggestions_boundary_revalidation_failed",
        ):
            decision = classify_terminal_run_failure(
                exit_code=1,
                performance_summary={"reason": reason},
            )
            self.assertTrue(decision.should_publish, msg=reason)
            self.assertEqual(decision.incident_type, "welcome_surface_unstable")
            self.assertEqual(decision.reason_code, reason)
            self.assertEqual(decision.severity, "critical")
            self.assertTrue(decision.requires_operator_review)
            self.assertTrue(decision.blocking_campaign)
            self.assertIn("Welcome followers-surface evidence", decision.action_required)

    def test_non_incident_control_flow_never_requires_operator_review(self) -> None:
        decision = classify_terminal_run_failure(
            exit_code=1,
            performance_summary={"reason": "schedule_window_closed"},
        )
        self.assertFalse(decision.should_publish)
        self.assertFalse(decision.requires_operator_review)
        self.assertFalse(decision.blocking_campaign)

    def test_metadata_never_contains_raw_material(self) -> None:
        decision = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "actual_logged_in_username_not_detected",
                "raw_xml": "<node>secret</node>",
                "password": "never",
                "adb_serial": "RFGL145LZHE",
            },
        )
        blob = str(decision.metadata_safe).lower()
        self.assertNotIn("raw_xml", blob)
        self.assertNotIn("<node", blob)
        self.assertNotIn("password", blob)
        self.assertNotIn("rfgl145lzhe", blob)


class DedupeAndPayloadTest(unittest.TestCase):
    def test_same_run_same_reason_shares_one_dedupe_key(self) -> None:
        # F7: same run + same reason must resolve to the same incident row.
        summary = {
            "reason": "active_instagram_account_mismatch",
            "account_identity_failure_reason": "actual_logged_in_username_not_detected",
        }
        first = classify_terminal_run_failure(exit_code=75, performance_summary=summary)
        second = classify_terminal_run_failure(exit_code=75, performance_summary=summary)
        payload_a = build_run_failure_incident_payload(
            first, account_id=ACCOUNT_ID, run_id=RUN_ID, run_request_id=REQUEST_ID
        )
        payload_b = build_run_failure_incident_payload(
            second, account_id=ACCOUNT_ID, run_id=RUN_ID, run_request_id=REQUEST_ID
        )
        self.assertEqual(payload_a["dedupe_key"], payload_b["dedupe_key"])
        self.assertEqual(
            payload_a["dedupe_key"],
            f"account:{ACCOUNT_ID}:run:{RUN_ID}:run_identity_verification_failed",
        )

    def test_reason_change_creates_distinct_dedupe_key(self) -> None:
        not_detected = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "actual_logged_in_username_not_detected",
            },
        )
        mismatch = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "active_instagram_account_mismatch",
            },
        )
        key_a = build_run_failure_incident_payload(
            not_detected, account_id=ACCOUNT_ID, run_id=RUN_ID
        )["dedupe_key"]
        key_b = build_run_failure_incident_payload(
            mismatch, account_id=ACCOUNT_ID, run_id=RUN_ID
        )["dedupe_key"]
        self.assertNotEqual(key_a, key_b)

    def test_dedupe_key_matches_guard_run_scoped_key(self) -> None:
        # The identity guard's builder and the dispatcher must share this key.
        from runtime_incidents import build_identity_mismatch_incident

        guard_payload = build_identity_mismatch_incident(
            account_id=ACCOUNT_ID,
            expected_username="mythyl_fitness",
            actual_username="someone_else",
            run_id=RUN_ID,
        )
        dispatcher_key = build_incident_dedupe_key(
            account_id=ACCOUNT_ID,
            run_ref=RUN_ID,
            incident_type="active_instagram_account_mismatch",
        )
        self.assertEqual(guard_payload["dedupe_key"], dispatcher_key)

    def test_payload_carries_request_and_run_context(self) -> None:
        decision = classify_terminal_run_failure(
            exit_code=1, performance_summary={"reason": "device_offline"}
        )
        payload = build_run_failure_incident_payload(
            decision,
            account_id=ACCOUNT_ID,
            account_username="mythyl_fitness",
            run_id=RUN_ID,
            run_request_id=REQUEST_ID,
            run_type="account_session",
        )
        self.assertEqual(payload["account_id"], ACCOUNT_ID)
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertEqual(payload["metadata"]["run_request_id"], REQUEST_ID)
        self.assertEqual(payload["metadata"]["run_type"], "account_session")
        self.assertEqual(payload["source"], "run_dispatcher")
        self.assertEqual(payload["status"], "open")


if __name__ == "__main__":
    unittest.main()
