"""Offline Outreach result routing through the existing A0 DM primitives."""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import patch

import dm_sender_engine as engine


class _NoPhysicalDevice:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"physical device access forbidden: {name}")


class OutreachSendUnverifiedQuarantineTest(unittest.TestCase):
    def _execute(
        self,
        send_result: tuple[bool, dict, str | None],
        *,
        dm_type: str = "outreach",
        navigation: tuple[str, bool] = ("empty_new_thread", True),
        origin: str | None = None,
    ) -> tuple[dict, object, object, object, object]:
        job = {
            "id": "offline-job",
            "account_id": "offline-account",
            "recipient_username": "controlled_recipient_",
            "dm_type": dm_type,
            "message_body": "offline draft",
            "metadata": {"retained_source_reference": "offline-source"},
        }
        if origin is not None:
            job["metadata"]["origin"] = origin
        original_job = deepcopy(job)

        def complete(job_id: str, status: str, **kwargs: object) -> dict:
            self.assertEqual(job_id, job["id"])
            return {
                **job,
                "status": status,
                "metadata": {**job["metadata"], **kwargs.get("metadata_patch", {})},
            }

        with ExitStack() as stack:
            stack.enter_context(patch.object(engine, "log"))
            stack.enter_context(patch.object(engine, "_get_dm_sender_nav_timings", return_value={}))
            stack.enter_context(patch.object(engine, "get_last_dm_thread_classify_snapshot", return_value={}))
            stack.enter_context(patch.object(engine, "_navigate_to_recipient_dm_thread", return_value=navigation))
            stack.enter_context(patch.object(engine, "_evaluate_welcome_sendability", return_value=(True, None)))
            stack.enter_context(patch.object(engine, "_evaluate_outreach_sendability", return_value=(True, None)))
            stack.enter_context(patch.object(engine.config, "DM_SENDER_FAILED_RETRY_DELAY_SECONDS", 300))
            running = stack.enter_context(patch.object(engine.supabase_client, "mark_dm_job_running", return_value=True))
            completed = stack.enter_context(patch.object(engine.supabase_client, "complete_dm_job", side_effect=complete))
            send = stack.enter_context(patch.object(engine, "_perform_real_welcome_dm_send", return_value=send_result))
            quarantine = stack.enter_context(patch.object(
                engine, "_complete_job_send_unverified_quarantine",
                wraps=engine._complete_job_send_unverified_quarantine,
            ))
            teardown = stack.enter_context(patch.object(engine, "_safe_teardown_navigation"))

            result = engine.execute_dm_job_real_send(
                _NoPhysicalDevice(), job, settings={}, account_id=job["account_id"],
                account_username="offline_sender", run_id="offline-run",
                skip_post_job_restore=True, jobs_total=1,
            )

        running.assert_called_once_with(job["id"])
        teardown.assert_not_called()
        self.assertEqual(job, original_job)
        self.assertEqual(result["recipient_username"], "controlled_recipient_")
        return result, completed, send, quarantine, job

    def _assert_quarantined(self, send_result: tuple[bool, dict, str | None]) -> None:
        result, complete, send, quarantine, _job = self._execute(send_result)
        self.assertEqual(result["outcome"], "send_unverified_quarantined")
        self.assertEqual(result["final_job_status"], "failed")
        send.assert_called_once()
        quarantine.assert_called_once()
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args, ("offline-job", "failed"))
        kwargs = complete.call_args.kwargs
        self.assertIsNone(kwargs["retry_delay_seconds"])
        self.assertTrue(kwargs["increment_attempt"])
        metadata = kwargs["metadata_patch"]
        self.assertTrue(metadata["automatic_retry_blocked"])
        self.assertTrue(metadata["manual_reconciliation_required"])
        self.assertEqual(metadata["send_verification_status"], "unverified")

    def test_tapped_send_unverified_is_terminal_without_retry(self) -> None:
        self._assert_quarantined((False, {
            "send_tapped": True, "sent": False, "reason": "send_unverified",
        }, "send_unverified"))

    def test_unverified_failure_reason_without_tap_marker_is_terminal(self) -> None:
        self._assert_quarantined((False, {}, "send_unverified"))

    def test_unverified_result_reason_without_failure_reason_is_terminal(self) -> None:
        self._assert_quarantined((False, {
            "sent": False, "reason": "send_unverified",
        }, None))

    def test_attempted_send_preserves_first_failure_and_tap_evidence(self) -> None:
        result, complete, send, quarantine, _job = self._execute((False, {
            "send_tapped": True, "sent": False, "reason": "outbound_not_observed",
        }, "outbound_not_observed"))
        self.assertEqual(result["outcome"], "send_unverified_quarantined")
        self.assertEqual(complete.call_args.kwargs["last_error"], "outbound_not_observed")
        self.assertTrue(complete.call_args.kwargs["metadata_patch"]["send_out"]["send_tapped"])
        send.assert_called_once()
        quarantine.assert_called_once()

    def test_attempted_send_without_reason_is_quarantined(self) -> None:
        self._assert_quarantined((False, {"send_tapped": True, "sent": False}, None))

    def test_failure_before_send_preserves_existing_retry_contract(self) -> None:
        result, complete, send, quarantine, _job = self._execute((False, {
            "send_tapped": False, "sent": False, "reason": "send_button_missing",
        }, "send_button_missing"))
        self.assertEqual(result["outcome"], "failed_retry")
        self.assertEqual(complete.call_args.kwargs["retry_delay_seconds"], 300)
        self.assertNotIn("automatic_retry_blocked", complete.call_args.kwargs["metadata_patch"])
        send.assert_called_once()
        quarantine.assert_not_called()

    def test_navigation_failure_does_not_enter_send(self) -> None:
        result, complete, send, quarantine, _job = self._execute(
            (False, {}, None), navigation=("unknown", False),
        )
        self.assertEqual(result["outcome"], "failed_retry")
        self.assertEqual(complete.call_args.kwargs["last_error"], "navigation_failed")
        send.assert_not_called()
        quarantine.assert_not_called()

    def test_confirmed_send_preserves_canonical_completion(self) -> None:
        for failure_reason in (None, "post_finalize_partial"):
            with self.subTest(failure_reason=failure_reason):
                result, complete, send, quarantine, _job = self._execute((True, {
                    "send_tapped": True, "sent": True,
                }, failure_reason))
                self.assertEqual(result["outcome"], "sent")
                self.assertEqual(result["final_job_status"], "sent")
                complete.assert_called_once()
                self.assertEqual(complete.call_args.args, ("offline-job", "sent"))
                self.assertNotIn("retry_delay_seconds", complete.call_args.kwargs)
                self.assertEqual(
                    complete.call_args.kwargs["metadata_patch"]["post_finalize_partial"],
                    failure_reason == "post_finalize_partial",
                )
                send.assert_called_once()
                quarantine.assert_not_called()

    def test_legacy_welcome_branch_is_unchanged(self) -> None:
        result, complete, send, quarantine, _job = self._execute((False, {
            "send_tapped": True, "sent": False, "reason": "send_unverified",
        }, "send_unverified"), dm_type="welcome")
        self.assertEqual(result["outcome"], "failed_retry")
        self.assertEqual(complete.call_args.kwargs["retry_delay_seconds"], 300)
        self.assertNotIn("send_tapped", complete.call_args.kwargs["metadata_patch"]["send_out"])
        send.assert_called_once()
        quarantine.assert_not_called()

    def test_non_outreach_branch_is_not_expanded(self) -> None:
        result, complete, send, quarantine, _job = self._execute(
            (False, {"send_tapped": True}, "send_unverified"), dm_type="other",
        )
        self.assertEqual(result["outcome"], "failed_retry")
        self.assertEqual(complete.call_args.kwargs["retry_delay_seconds"], 300)
        send.assert_called_once()
        quarantine.assert_not_called()

    def test_origin_metadata_does_not_select_another_execution_engine(self) -> None:
        for origin in ("CLIENT_LIST", "AI_AGENT"):
            with self.subTest(origin=origin):
                result, _complete, send, quarantine, job = self._execute((False, {
                    "send_tapped": True, "sent": False, "reason": "send_unverified",
                }, "send_unverified"), origin=origin)
                self.assertEqual(result["outcome"], "send_unverified_quarantined")
                self.assertEqual(result["job"]["metadata"]["origin"], origin)
                self.assertEqual(job["metadata"]["retained_source_reference"], "offline-source")
                send.assert_called_once()
                quarantine.assert_called_once()


if __name__ == "__main__":
    unittest.main()
