from __future__ import annotations

import unittest
from unittest.mock import patch

import login_challenge_runtime as runtime


class LoginChallengeRuntimeTests(unittest.TestCase):
    def test_publish_login_challenge_incident_uses_safe_metadata(self) -> None:
        with patch.object(runtime, "publish_account_incident", return_value={"published": True}) as publish:
            runtime.publish_login_challenge_pending_incident(
                account_id="11111111-1111-4111-8111-111111111111",
                expected_username="cinema_catchup",
                run_id="run-1",
                challenge_type="email",
                screen_type="email_code_challenge",
                reason="email_verification_code_required",
                dashboard_action_type="enter_email_verification_code",
                masked_email_present=True,
            )
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["incident_type"], "email_verification_code_required")
        self.assertEqual(kwargs["metadata"]["dashboard_action_type"], "enter_email_verification_code")
        self.assertNotIn("verification_code", kwargs["metadata"])

    def test_publish_login_package_mismatch_incident_uses_blocking_safe_metadata(self) -> None:
        with patch.object(runtime, "publish_account_incident", return_value={"published": True}) as publish:
            runtime.publish_login_package_mismatch_incident(
                account_id="11111111-1111-4111-8111-111111111111",
                expected_username="i_m_your_traker",
                expected_package_name="com.instagram.androie",
                actual_foreground_package="com.instagram.android",
                run_id="run-1",
                run_type="login_provisioning",
                device_id="device-1",
                expected_app_instance_id="7637db9a-3581-4099-8068-d5eb1ed86f96",
                adb_serial_masked="RFGL***VCKE",
            )

        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["incident_type"], "login_package_mismatch")
        self.assertEqual(kwargs["severity"], "critical")
        self.assertEqual(kwargs["safe_client_message"], "Automation paused for account safety.")
        metadata = kwargs["metadata"]
        self.assertEqual(metadata["expected_package_name"], "com.instagram.androie")
        self.assertEqual(metadata["actual_foreground_package"], "com.instagram.android")
        self.assertTrue(metadata["blocking_campaign"])
        self.assertTrue(metadata["requires_operator_action"])
        self.assertFalse(metadata["requires_client_action"])
        rendered = str(metadata).lower()
        self.assertNotIn("password", rendered)
        self.assertNotIn("secret_ref", rendered)
        self.assertNotIn("vault", rendered)

    def test_sync_login_package_mismatch_dashboard_action_is_admin_blocking(self) -> None:
        with patch.object(runtime, "upsert_login_challenge_dashboard_action", return_value={"published": True}) as upsert:
            runtime.sync_login_package_mismatch_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                expected_package_name="com.instagram.androie",
                actual_foreground_package="com.instagram.android",
                run_id="run-1",
            )

        kwargs = upsert.call_args.kwargs
        self.assertEqual(kwargs["action_type"], "review_login_package_mismatch")
        self.assertTrue(kwargs["human_review_required"])
        self.assertEqual(kwargs["metadata"]["reason"], "expected_package_mismatch")

    def test_consume_verification_code_for_worker_delegates_rpc(self) -> None:
        with patch.object(runtime, "call_rpc", return_value={"ok": True, "verification_code": "123456"}) as rpc:
            out = runtime.consume_verification_code_for_worker(
                action_id="22222222-2222-4222-8222-222222222222",
                account_id="11111111-1111-4111-8111-111111111111",
                run_id="run-1",
            )
        self.assertTrue(out["ok"])
        rpc.assert_called_once()

    def test_sync_verification_action_reopens_pending_on_still_required(self) -> None:
        with patch.object(runtime, "reopen_verification_action_pending", return_value={"updated": True}) as reopen:
            out = runtime.sync_verification_action_after_email_code_resume(
                action_id="22222222-2222-4222-8222-222222222222",
                account_id="11111111-1111-4111-8111-111111111111",
                run_id="run-1",
                ok=False,
                final_outcome="verification_pending",
                failure_reason="verification_code_still_required",
                screen_type="email_code_challenge",
            )
        self.assertTrue(out["updated"])
        reopen.assert_called_once()

    def test_sync_verification_action_keeps_running_on_stale_still_required_with_post_login_progress(self) -> None:
        with patch.object(runtime, "reopen_verification_action_pending") as reopen, patch.object(
            runtime, "_merge_dashboard_action_metadata", return_value={"updated": True}
        ) as merge:
            out = runtime.sync_verification_action_after_email_code_resume(
                action_id="22222222-2222-4222-8222-222222222222",
                account_id="11111111-1111-4111-8111-111111111111",
                run_id="run-1",
                ok=False,
                final_outcome="verification_pending",
                failure_reason="verification_code_still_required",
                screen_type="email_code_challenge",
                safe_metadata={
                    "save_login_info_prompt_detected": True,
                    "post_submit_screens": ["email_code_challenge_stale", "save_login_info_prompt"],
                },
            )
        self.assertTrue(out["updated"])
        reopen.assert_not_called()
        merge.assert_called_once()
        self.assertEqual(merge.call_args.kwargs["metadata"]["resume_status"], "running")

    def test_sync_verification_action_keeps_running_on_post_submit_finalization_pending(self) -> None:
        with patch.object(runtime, "reopen_verification_action_pending") as reopen, patch.object(
            runtime, "_merge_dashboard_action_metadata", return_value={"updated": True}
        ) as merge:
            out = runtime.sync_verification_action_after_email_code_resume(
                action_id="22222222-2222-4222-8222-222222222222",
                account_id="11111111-1111-4111-8111-111111111111",
                run_id="run-1",
                ok=False,
                final_outcome="post_submit_finalization_pending",
                failure_reason="post_submit_finalization_pending",
                screen_type="email_code_challenge",
            )
        self.assertTrue(out["updated"])
        reopen.assert_not_called()
        self.assertEqual(merge.call_args.kwargs["metadata"]["resume_status"], "running")

    def test_mark_verification_action_resume_queued_patches_metadata(self) -> None:
        with patch.object(runtime, "_merge_dashboard_action_metadata", return_value={"id": "action-1"}) as merge:
            runtime.mark_verification_action_resume_queued(
                action_id="22222222-2222-4222-8222-222222222222",
                account_id="11111111-1111-4111-8111-111111111111",
                run_request_id="33333333-3333-4333-8333-333333333333",
                submission_id="sub-1",
            )
        merge.assert_called_once()
        self.assertEqual(merge.call_args.kwargs["metadata"]["resume_status"], "queued")

    def test_patch_dashboard_action_uses_prefer_representation(self) -> None:
        with patch.object(runtime, "_request_json", return_value=[{"id": "action-1"}]) as request_json:
            runtime._patch_dashboard_action(
                action_id="22222222-2222-4222-8222-222222222222",
                account_id="11111111-1111-4111-8111-111111111111",
                body={"status": "pending"},
            )
        self.assertTrue(request_json.call_args.kwargs.get("prefer_representation"))
