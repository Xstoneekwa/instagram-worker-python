from __future__ import annotations

import unittest
from types import SimpleNamespace
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
    def test_instagram_action_restriction_awaits_human_authorization(self) -> None:
        import supabase_client

        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[{"id": "plan-1", "run_id": RUN_ID}],
        ) as req:
            result = store.record_terminal_failure(
                run_id=RUN_ID,
                incident_type="instagram_account_restriction",
                reason_code="instagram_action_rate_limit",
                incident_id="inc-restriction-1",
            )
        self.assertTrue(result["persisted"])
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["resume_state"], "awaiting_human_resume_authorization")
        self.assertFalse(body["restart_allowed"])
        self.assertEqual(
            body["restart_block_reason"], "awaiting_human_resume_authorization"
        )
        self.assertEqual(body["incident_id"], "inc-restriction-1")

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
    @staticmethod
    def _existing() -> dict:
        return {
            "id": "plan-1",
            "run_id": RUN_ID,
            "run_request_id": REQUEST_ID,
            "restart_allowed": False,
            "resume_state": "run_active",
            "plan": {
                "root_business_session_id": REQUEST_ID,
                "execution_attempt_no": 1,
                "retry_index": 0,
            },
        }

    def _persist_harness(self, *, ambiguous: bool = False):
        import supabase_client

        captured: dict = {}

        def load(**_kwargs):
            if "plan" not in captured:
                return self._existing()
            return {
                **self._existing(),
                "resume_state": captured["resume_state"],
                "restart_allowed": captured["restart_allowed"],
                "plan": captured["plan"],
            }

        def persist(**kwargs):
            captured.update(kwargs)
            if ambiguous:
                raise TimeoutError("lost response")
            return {"ok": True}

        return captured, patch.object(store, "load_resume_plan", side_effect=load), patch.object(
            supabase_client,
            "persist_account_session_resume_plan_v1",
            side_effect=persist,
        )

    def test_completed_session_marks_plan_completed(self) -> None:
        captured, load_patch, persist_patch = self._persist_harness()
        with load_patch, persist_patch:
            result = store.record_end_of_session(
                run_id=RUN_ID,
                session_plan={
                    "session_termination_class": "completed",
                    "restart_allowed": False,
                    "restart_block_reason": "session_completed",
                },
                session_status="success",
            )
        self.assertTrue(result["persisted"])
        self.assertEqual(captured["resume_stage"], "completed")
        self.assertEqual(captured["resume_state"], "completed")
        self.assertFalse(captured["restart_allowed"])

    def test_partial_session_maps_outcome_to_resume_requested(self) -> None:
        captured, load_patch, persist_patch = self._persist_harness()
        with load_patch, persist_patch:
            result = store.record_end_of_session(
                run_id=RUN_ID,
                session_plan={
                    "session_termination_class": "partial_resumable",
                    "restart_allowed": True,
                    "restart_block_reason": "",
                    "quota_remaining": {"total": 30, "follow": 30},
                    "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
                    "unsafe_markers": [],
                },
                session_status="success",
            )
        self.assertTrue(result["persisted"])
        self.assertEqual(captured["resume_stage"], "phases")
        self.assertEqual(captured["resume_state"], "resume_requested")
        self.assertTrue(captured["restart_allowed"])
        self.assertEqual(captured["plan"]["business_outcome"], "partial_resumable")
        self.assertEqual(captured["plan"]["auto_restart_decision"], "schedule_resume")
        self.assertEqual(captured["plan"]["root_business_session_id"], REQUEST_ID)
        self.assertEqual(captured["plan"]["execution_attempt_no"], 1)

    def test_lost_response_is_reconciled_by_exact_readback(self) -> None:
        captured, load_patch, persist_patch = self._persist_harness(ambiguous=True)
        with load_patch, persist_patch:
            result = store.record_end_of_session(
                run_id=RUN_ID,
                session_plan={
                    "session_termination_class": "partial_resumable",
                    "restart_allowed": True,
                    "restart_block_reason": "",
                    "quota_remaining": {"total": 3, "unfollow": 3},
                    "phases_to_run": {"welcome": False, "follow": False, "unfollow": True},
                    "unsafe_markers": [],
                },
                session_status="success",
            )
        self.assertTrue(result["persisted"])
        self.assertEqual(result["reason"], "confirmed_after_ambiguous_response")
        self.assertEqual(captured["resume_state"], "resume_requested")

    def test_replaying_identical_terminalization_keeps_the_same_digest(self) -> None:
        captured, load_patch, persist_patch = self._persist_harness()
        terminal_plan = {
            "session_termination_class": "partial_resumable",
            "restart_allowed": True,
            "quota_remaining": {"total": 3, "unfollow": 3},
            "phases_to_run": {"welcome": False, "follow": False, "unfollow": True},
            "unsafe_markers": [],
        }
        with load_patch, persist_patch:
            first = store.record_end_of_session(
                run_id=RUN_ID,
                session_plan=terminal_plan,
                session_status="success",
            )
            first_digest = captured["terminal_plan_digest"]
            second = store.record_end_of_session(
                run_id=RUN_ID,
                session_plan=terminal_plan,
                session_status="success",
            )
        self.assertTrue(first["persisted"])
        self.assertTrue(second["persisted"])
        self.assertEqual(captured["terminal_plan_digest"], first_digest)

    def test_unconfirmed_write_requires_reconciliation(self) -> None:
        import supabase_client

        with patch.object(store, "load_resume_plan", return_value=self._existing()), patch.object(
            supabase_client,
            "persist_account_session_resume_plan_v1",
            return_value={"ok": False, "reason": "resume_plan_contract_invalid"},
        ):
            result = store.record_end_of_session(
                run_id=RUN_ID,
                session_plan={
                    "session_termination_class": "partial_resumable",
                    "restart_allowed": True,
                    "quota_remaining": {"total": 3},
                    "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
                    "unsafe_markers": [],
                },
                session_status="success",
            )
        self.assertFalse(result["persisted"])
        self.assertEqual(result["reason"], "resume_plan_reconciliation_required")


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


class AutomaticRetryTerminalStateTest(unittest.TestCase):
    def test_resumable_retry_uses_authoritative_terminal_writer(self) -> None:
        decision = SimpleNamespace(
            business_session_id=REQUEST_ID,
            attempt_id=1,
            retry_index=0,
            next_attempt_id=2,
            next_retry_index=1,
            previous_run_id=None,
            root_failure_code="unfollow_runtime_exception",
            failure_signature="python:unfollow:test",
            failure_category="recoverable_python_runtime_failure",
            restart_allowed=True,
            block_reason="",
            event_type="recoverable_python_failure_restart_1_scheduled",
        )
        with patch.object(store, "load_resume_plan", return_value={"plan": {}}), patch.object(
            store,
            "record_end_of_session",
            return_value={"persisted": True},
        ) as authoritative:
            result = store.record_automatic_retry_terminal_state(
                run_id=RUN_ID,
                retry_decision=decision,
                cleanup_completed=True,
                lock_released=True,
                quota_remaining={"total": 30, "follow": 30},
                phases_to_run={"welcome": False, "follow": True, "unfollow": False},
            )
        self.assertTrue(result["persisted"])
        self.assertEqual(
            authoritative.call_args.kwargs["session_plan"]["session_termination_class"],
            "partial_resumable",
        )
        self.assertEqual(authoritative.call_args.kwargs["session_status"], "failed")


if __name__ == "__main__":
    unittest.main()
