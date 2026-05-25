from __future__ import annotations

import unittest
from unittest.mock import patch

import account_identity_guard as guard
import runtime_incidents


class AccountIdentityGuardIncidentsTest(unittest.TestCase):
    def _mismatch_extract(self):
        return (
            "transfers",
            "action_bar_title",
            {
                "action_bar_title": "transfers",
                "candidate_texts": [
                    {"username": "transfers", "method": "action_bar_title", "rank": 0}
                ],
                "hierarchy_xml_len": 1234,
            },
        )

    def _verify(self, *, expected: str = "cinema_catchup"):
        return guard.verify_active_instagram_account_matches_expected(
            object(),
            expected_account_username=expected,
            expected_instagram_user_id="expected-stable-id",
            account_id="42c625c2-e761-4100-8a9d-7ae1373de97d",
            run_type="supabase_account_run",
            run_id="00000000-0000-4000-8000-000000000123",
            stage="runner_account_identity_preflight",
        )

    def test_mismatch_incidents_disabled_result_unchanged(self) -> None:
        with (
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(guard, "_dump_hierarchy", return_value="<xml />"),
            patch.object(guard, "_extract_own_profile_username_from_hierarchy", return_value=self._mismatch_extract()),
            patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", False, create=True),
            patch.object(runtime_incidents.supabase_client, "upsert_account_incident") as upsert,
        ):
            result = self._verify()

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, guard.ACCOUNT_IDENTITY_MISMATCH_REASON)
        self.assertEqual(result.actual_logged_in_username, "transfers")
        upsert.assert_not_called()

    def test_mismatch_publish_success_payload(self) -> None:
        with (
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(guard, "_dump_hierarchy", return_value="<xml />"),
            patch.object(guard, "_extract_own_profile_username_from_hierarchy", return_value=self._mismatch_extract()),
            patch.object(
                runtime_incidents,
                "publish_account_incident",
                return_value={"published": True, "reason": "published"},
            ) as publish,
        ):
            result = self._verify()

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, guard.ACCOUNT_IDENTITY_MISMATCH_REASON)
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["incident_type"], "active_instagram_account_mismatch")
        self.assertEqual(
            kwargs["dedupe_key"],
            "account:42c625c2-e761-4100-8a9d-7ae1373de97d:identity:mismatch:transfers",
        )
        self.assertEqual(kwargs["severity"], "critical")
        self.assertEqual(kwargs["metadata"]["expected_account_username"], "cinema_catchup")
        self.assertEqual(kwargs["metadata"]["actual_logged_in_username"], "transfers")
        self.assertEqual(
            kwargs["metadata"]["verification_method"],
            "own_profile_username_exact_mismatch:action_bar_title",
        )

    def test_mismatch_publish_raises_result_unchanged(self) -> None:
        with (
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(guard, "_dump_hierarchy", return_value="<xml />"),
            patch.object(guard, "_extract_own_profile_username_from_hierarchy", return_value=self._mismatch_extract()),
            patch.object(
                runtime_incidents,
                "publish_account_incident",
                side_effect=RuntimeError("db down"),
            ),
        ):
            result = self._verify()

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, guard.ACCOUNT_IDENTITY_MISMATCH_REASON)
        self.assertEqual(result.actual_logged_in_username, "transfers")

    def test_identity_check_ok_does_not_publish(self) -> None:
        with (
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(guard, "_dump_hierarchy", return_value="<xml />"),
            patch.object(
                guard,
                "_extract_own_profile_username_from_hierarchy",
                return_value=("cinema_catchup", "action_bar_title", {"hierarchy_xml_len": 10}),
            ),
            patch.object(runtime_incidents, "publish_account_incident") as publish,
        ):
            result = self._verify()

        self.assertTrue(result.ok)
        publish.assert_not_called()

    def test_expected_account_username_missing_does_not_publish(self) -> None:
        with patch.object(runtime_incidents, "publish_account_incident") as publish:
            result = self._verify(expected="")

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "expected_account_username_missing")
        publish.assert_not_called()

    def test_own_profile_open_failed_does_not_publish(self) -> None:
        with (
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=False),
            patch.object(runtime_incidents, "publish_account_incident") as publish,
        ):
            result = self._verify()

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "own_profile_open_failed")
        publish.assert_not_called()

    def test_actual_logged_in_username_not_detected_does_not_publish(self) -> None:
        with (
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(guard, "_dump_hierarchy", return_value="<xml />"),
            patch.object(
                guard,
                "_extract_own_profile_username_from_hierarchy",
                return_value=("", "own_profile_username_not_found", {"hierarchy_xml_len": 10}),
            ),
            patch.object(runtime_incidents, "publish_account_incident") as publish,
        ):
            result = self._verify()

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "actual_logged_in_username_not_detected")
        publish.assert_not_called()

    def test_builder_account_id_absent_uses_unknown(self) -> None:
        payload = runtime_incidents.build_identity_mismatch_incident(
            account_id=None,
            expected_username="cinema_catchup",
            actual_username="transfers",
        )

        self.assertEqual(
            payload["dedupe_key"],
            "account:unknown:identity:mismatch:transfers",
        )


if __name__ == "__main__":
    unittest.main()
