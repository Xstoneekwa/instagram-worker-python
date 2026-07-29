from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from target_availability_observation import (
    TARGET_AVAILABILITY_REASON_CODES,
    TargetAvailabilityBudgets,
    TargetAvailabilityObservationScope,
    build_target_availability_observation,
)


class TargetAvailabilityObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = TargetAvailabilityObservationScope(
            tenant_id="tenant-one",
            account_id="account-one",
            target_id="target-one",
            normalized_username="@Target.One",
            stable_platform_user_id="ig-100",
        )
        self.when = datetime(2026, 7, 29, 8, 30, tzinfo=timezone.utc)

    def build(self, **changes):
        values = {
            "scope": self.scope,
            "event_key": "run-1:followers-entry:1",
            "observed_at": self.when,
            "reason_codes": ["target_profile_found"],
            "lookup_result": "found",
            "profile_found": True,
            "followers_surface": "normal",
            "network_state": "healthy",
            "session_state": "healthy",
            "ui_evidence_quality": "high",
            "run_id": "run-one",
        }
        values.update(changes)
        return build_target_availability_observation(**values)

    def test_normalizes_and_serializes_short_observation(self) -> None:
        observation = self.build(
            observed_username="@TARGET.ONE",
            observed_stable_platform_user_id="ig-100",
            verified_badge=True,
            reason_codes=["target_verified_status_detected", "target_profile_found"],
        )
        payload = json.loads(observation.to_json())
        self.assertEqual(payload["schema_version"], "target-availability-observation-v1")
        self.assertEqual(payload["requested_username"], "target.one")
        self.assertEqual(payload["normalized_username"], "target.one")
        self.assertEqual(payload["searched_username"], "target.one")
        self.assertEqual(payload["observed_username"], "target.one")
        self.assertEqual(payload["observed_stable_platform_user_id"], "ig-100")
        self.assertEqual(payload["reason_codes"], sorted(payload["reason_codes"]))
        self.assertNotIn("password", payload)

    def test_explicit_identity_context_stage_and_terrain_signals(self) -> None:
        observation = self.build(
            request_id="request-one",
            instance_id="worker-one",
            device_key="device-one",
            worker_version="release-one",
            observation_stage="followers_pagination",
            identity_source="accessibility_node",
            identity_confidence="medium",
            username_lookup_started=True,
            username_lookup_completed=True,
            profile_ambiguous=True,
            followers_surface_entered=True,
            followers_surface_entry_failed=True,
            pagination_stalled=True,
            recovery_attempted=True,
            ui_ambiguous=True,
            network_ambiguous=True,
            session_ambiguous=True,
            source_profile_mismatch=True,
            identity_conflict=True,
            reason_codes=[
                "target_username_lookup_completed",
                "target_profile_ambiguous",
                "target_followers_surface_entered",
                "target_followers_entry_failed",
                "target_pagination_stalled",
                "target_recovery_attempted",
                "target_ui_ambiguity",
                "target_network_ambiguity",
                "target_session_ambiguity",
                "target_source_profile_mismatch",
                "target_identity_conflict",
            ],
        )
        payload = observation.to_dict()
        self.assertEqual(payload["request_id"], "request-one")
        self.assertEqual(payload["instance_id"], "worker-one")
        self.assertEqual(payload["device_id"], "device-one")
        self.assertEqual(payload["worker_release"], "release-one")
        self.assertEqual(payload["observation_stage"], "followers_pagination")
        self.assertEqual(payload["identity_source"], "accessibility_node")
        self.assertEqual(payload["identity_confidence"], "medium")
        for signal in (
            "username_lookup_started",
            "username_lookup_completed",
            "profile_ambiguous",
            "followers_surface_entered",
            "followers_surface_entry_failed",
            "pagination_stalled",
            "recovery_attempted",
            "ui_ambiguous",
            "network_ambiguous",
            "session_ambiguous",
            "source_profile_mismatch",
            "identity_conflict",
        ):
            self.assertTrue(payload[signal])

    def test_idempotency_is_stable_for_the_same_runtime_event(self) -> None:
        first = self.build()
        second = self.build(observed_at=datetime(2026, 7, 29, 8, 31, tzinfo=timezone.utc))
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        self.assertEqual(first.observation_id, second.observation_id)

    def test_different_run_or_event_produces_a_different_idempotency_key(self) -> None:
        first = self.build()
        self.assertNotEqual(first.idempotency_key, self.build(run_id="run-two").idempotency_key)
        self.assertNotEqual(first.idempotency_key, self.build(event_key="run-1:followers-entry:2").idempotency_key)

    def test_verified_badge_is_only_an_observation(self) -> None:
        observation = self.build(
            verified_badge=True,
            reason_codes=["target_verified_status_detected"],
        )
        payload = observation.to_dict()
        self.assertTrue(payload["verified_badge"])
        for forbidden in ("archive", "replace", "rename", "notify", "email", "activate"):
            self.assertNotIn(forbidden, json.dumps(payload).lower())

    def test_terminal_followers_surface_can_be_recorded_without_a_decision(self) -> None:
        observation = self.build(
            verified_badge=True,
            followers_surface="terminally_limited",
            accessible_profiles_count=48,
            terminal_end_detected=True,
            repeated_first_profiles_detected=True,
            reason_codes=[
                "target_verified_status_detected",
                "target_followers_surface_terminally_limited",
                "target_repeated_first_profiles_detected",
            ],
        )
        self.assertTrue(observation.terminal_end_detected)
        self.assertEqual(observation.accessible_profiles_count, 48)

    def test_retry_timeout_recovery_and_ambiguity_are_explicit(self) -> None:
        observation = self.build(
            lookup_result="failed",
            profile_found=None,
            followers_surface="unknown",
            retry_count=1,
            retry_budget_exhausted=True,
            navigation_timeout=True,
            recovery_outcome="failed",
            ui_evidence_quality="low",
            network_state="degraded",
            reason_codes=[
                "target_navigation_retry_budget_exhausted",
                "target_navigation_timeout",
                "target_recovery_failed",
                "target_ui_ambiguity",
                "target_network_ambiguity",
            ],
        )
        self.assertTrue(observation.retry_budget_exhausted)
        self.assertEqual(observation.recovery_outcome, "failed")

    def test_budget_defaults_are_bounded_and_do_not_touch_runtime_config(self) -> None:
        budgets = TargetAvailabilityBudgets()
        self.assertEqual(budgets.retry_budget_count, 1)
        self.assertLessEqual(budgets.navigation_budget_ms, budgets.availability_budget_ms)
        with self.assertRaisesRegex(ValueError, "retry_budget_out_of_bounds"):
            TargetAvailabilityBudgets(retry_budget_count=3)

    def test_rejects_unknown_reasons_and_non_serializable_evidence(self) -> None:
        self.assertIn("target_profile_not_found", TARGET_AVAILABILITY_REASON_CODES)
        with self.assertRaisesRegex(ValueError, "reason_codes_invalid"):
            self.build(reason_codes=["archive_target_now"])
        with self.assertRaises(TypeError):
            self.build(evidence_safe={"bad": object()})
        with self.assertRaisesRegex(ValueError, "sensitive_evidence_key_forbidden"):
            self.build(evidence_safe={"nested": {"service_role_key": "never"}})
        with self.assertRaisesRegex(ValueError, "evidence_safe_too_large"):
            self.build(evidence_safe={"note": "x" * 5_000})
        with self.assertRaisesRegex(ValueError, "identity_source_invalid"):
            self.build(identity_source="guessed_from_username")
        with self.assertRaisesRegex(ValueError, "observation_stage_invalid"):
            self.build(observation_stage="archive_target")

    def test_scope_is_required_and_account_isolation_changes_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "account_id_required"):
            TargetAvailabilityObservationScope(
                tenant_id="tenant-one",
                account_id="",
                target_id="target-one",
                normalized_username="target.one",
            )
        other_scope = TargetAvailabilityObservationScope(
            tenant_id="tenant-one",
            account_id="account-two",
            target_id="target-one",
            normalized_username="target.one",
        )
        self.assertNotEqual(self.build().idempotency_key, self.build(scope=other_scope).idempotency_key)

    def test_naive_timestamp_and_invalid_username_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "observed_at_timezone_required"):
            self.build(observed_at=datetime(2026, 7, 29, 8, 30))
        with self.assertRaisesRegex(ValueError, "invalid_target_username"):
            TargetAvailabilityObservationScope(
                tenant_id="tenant-one",
                account_id="account-one",
                target_id="target-one",
                normalized_username="invalid username",
            )

    def test_contract_has_no_navigation_or_supabase_dependency(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contract = (root / "target_availability_observation.py").read_text(encoding="utf-8")
        self.assertNotIn("supabase_client", contract)
        self.assertNotIn("uiautomator2", contract)
        self.assertNotIn("instagram_navigation", contract)


if __name__ == "__main__":
    unittest.main()
