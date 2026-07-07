from __future__ import annotations

import unittest
from unittest.mock import patch

import account_session_resume_plan_store as store

ACCOUNT_ID = "e9c7462b-fc0e-46c9-8d40-1e07e0f6a41b"
RUN_ID = "9e46c4a5-72c5-4b16-9f0f-96f6f2ff11aa"
REQUEST_ID = "7d3b4a4d-0000-4e0f-8a53-51e529a0a001"
ASSIGNMENT_ID = "3f1c2a10-1111-4111-8111-aaaaaaaaaaaa"


class ResumeWindowKeyTest(unittest.TestCase):
    def test_key_uses_assignment_and_window_start(self) -> None:
        key = store.build_resume_window_key(
            assignment_id=ASSIGNMENT_ID,
            account_id=ACCOUNT_ID,
            scheduled_window_start="2026-07-07T08:00:00+00:00",
        )
        self.assertEqual(key, f"{ASSIGNMENT_ID}:2026-07-07T08:00:00+00:00")

    def test_key_falls_back_to_account_without_assignment(self) -> None:
        key = store.build_resume_window_key(
            assignment_id=None,
            account_id=ACCOUNT_ID,
            scheduled_window_start="2026-07-07T08:00:00+00:00",
        )
        self.assertEqual(key, f"{ACCOUNT_ID}:2026-07-07T08:00:00+00:00")

    def test_key_none_without_window(self) -> None:
        self.assertIsNone(
            store.build_resume_window_key(
                assignment_id=ASSIGNMENT_ID,
                account_id=ACCOUNT_ID,
                scheduled_window_start=None,
            )
        )


class CreateEarlyResumePlanTest(unittest.TestCase):
    def test_creates_preflight_plan_before_ui_actions(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[{"id": "plan-1", "run_id": RUN_ID}],
        ) as req:
            result = store.create_early_resume_plan(
                run_id=RUN_ID,
                run_request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                assignment_id=ASSIGNMENT_ID,
                device_id="dev-1",
                app_instance_id="clone-1",
                expected_username="mythyl_fitness",
                expected_package="com.instagram.clone7",
                scheduled_window_start="2026-07-07T08:00:00+00:00",
                scheduled_window_end="2026-07-07T14:00:00+00:00",
            )
        self.assertTrue(result["persisted"])
        method, table = req.call_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(table, "account_session_resume_plans")
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["run_id"], RUN_ID)
        self.assertEqual(body["run_request_id"], REQUEST_ID)
        self.assertEqual(body["account_id"], ACCOUNT_ID)
        self.assertEqual(body["expected_username"], "mythyl_fitness")
        self.assertEqual(body["expected_package"], "com.instagram.clone7")
        self.assertEqual(body["resume_stage"], "preflight")
        self.assertEqual(body["resume_state"], "run_active")
        self.assertFalse(body["restart_allowed"])
        self.assertEqual(body["restart_block_reason"], "run_in_progress")
        self.assertEqual(
            body["resume_window_key"],
            f"{ASSIGNMENT_ID}:2026-07-07T08:00:00+00:00",
        )
        # Idempotent per run: upsert on run_id conflict.
        self.assertEqual(req.call_args.kwargs["query"], {"on_conflict": "run_id"})

    def test_write_failure_never_raises(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client, "_request_json", side_effect=RuntimeError("down")
        ):
            result = store.create_early_resume_plan(
                run_id=RUN_ID,
                run_request_id=None,
                account_id=ACCOUNT_ID,
                assignment_id=None,
                device_id=None,
                app_instance_id=None,
                expected_username="u",
                expected_package=None,
                scheduled_window_start=None,
                scheduled_window_end=None,
            )
        self.assertFalse(result["persisted"])
        self.assertEqual(result["reason"], "write_failed")

    def test_missing_run_or_account_is_rejected(self) -> None:
        result = store.create_early_resume_plan(
            run_id="",
            run_request_id=None,
            account_id=ACCOUNT_ID,
            assignment_id=None,
            device_id=None,
            app_instance_id=None,
            expected_username=None,
            expected_package=None,
            scheduled_window_start=None,
            scheduled_window_end=None,
        )
        self.assertFalse(result["persisted"])
        self.assertEqual(result["reason"], "missing_run_or_account")


class RecordTerminalFailureTest(unittest.TestCase):
    def test_recoverable_incident_awaits_human_authorization(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[{"id": "plan-1", "run_id": RUN_ID}],
        ) as req:
            result = store.record_terminal_failure(
                run_id=RUN_ID,
                incident_type="run_identity_verification_failed",
                reason_code="actual_logged_in_username_not_detected",
                incident_id="inc-1",
            )
        self.assertTrue(result["persisted"])
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["resume_state"], "awaiting_human_resume_authorization")
        self.assertFalse(body["restart_allowed"])
        self.assertEqual(
            body["restart_block_reason"], "awaiting_human_resume_authorization"
        )
        self.assertEqual(
            body["terminal_reason_code"], "actual_logged_in_username_not_detected"
        )
        self.assertEqual(body["incident_id"], "inc-1")

    def test_non_recoverable_incident_is_marked_not_recoverable(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[{"id": "plan-1", "run_id": RUN_ID}],
        ) as req:
            store.record_terminal_failure(
                run_id=RUN_ID,
                incident_type="some_unknown_incident_type",
                reason_code="whatever",
            )
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["resume_state"], "not_recoverable")
        self.assertEqual(body["restart_block_reason"], "resume_plan_not_recoverable")

    def test_update_never_raises(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client, "_request_json", side_effect=RuntimeError("down")
        ):
            result = store.record_terminal_failure(
                run_id=RUN_ID,
                incident_type="run_identity_verification_failed",
                reason_code="actual_logged_in_username_not_detected",
            )
        self.assertFalse(result["persisted"])


class RecordEndOfSessionTest(unittest.TestCase):
    def test_completed_session_marks_plan_completed(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[{"id": "plan-1"}],
        ) as req:
            store.record_end_of_session(
                run_id=RUN_ID,
                session_plan={"restart_allowed": False, "restart_block_reason": "session_completed"},
                session_status="success",
            )
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["resume_stage"], "completed")
        self.assertEqual(body["resume_state"], "completed")
        self.assertFalse(body["restart_allowed"])

    def test_partial_session_keeps_verdict(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[{"id": "plan-1"}],
        ) as req:
            store.record_end_of_session(
                run_id=RUN_ID,
                session_plan={
                    "restart_allowed": True,
                    "restart_block_reason": "",
                    "quota_remaining": {"total": 5},
                },
                session_status="partial",
            )
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["resume_stage"], "phases")
        self.assertTrue(body["restart_allowed"])
        self.assertEqual(body["plan"]["quota_remaining"]["total"], 5)


class MarkResumeOutcomeTest(unittest.TestCase):
    def test_success_marks_resume_succeeded(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[{"id": "plan-1"}],
        ) as req:
            store.mark_resume_outcome(original_run_id=RUN_ID, succeeded=True)
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["resume_state"], "resume_succeeded")
        self.assertFalse(body["restart_allowed"])

    def test_failure_requires_new_intervention_without_loop(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[{"id": "plan-1"}],
        ) as req:
            store.mark_resume_outcome(
                original_run_id=RUN_ID,
                succeeded=False,
                reason_code="actual_logged_in_username_not_detected",
            )
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["resume_state"], "awaiting_human_resume_authorization")
        self.assertFalse(body["restart_allowed"])
        self.assertEqual(body["restart_block_reason"], "reintervention_required")


if __name__ == "__main__":
    unittest.main()
