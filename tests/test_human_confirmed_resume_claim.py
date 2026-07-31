from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import account_session_resume_plan_store as store
from auto_restart_runtime import (
    is_human_confirmed_resume_request,
    validate_auto_restart_request_at_claim,
)

ACCOUNT_ID = "e9c7462b-fc0e-46c9-8d40-1e07e0f6a41b"
ORIGINAL_RUN_ID = "9e46c4a5-72c5-4b16-9f0f-96f6f2ff11aa"
RESUME_PLAN_ID = "5b2f77aa-2222-4222-8222-bbbbbbbbbbbb"
INCIDENT_ID = "1a2b3c4d-3333-4333-8333-cccccccccccc"


def _metadata(**overrides) -> dict:
    base = {
        "auto_restart": True,
        "source": "auto_restart_tick",
        "recovery_mode": "human_confirmed_resume",
        "resume_plan_id": RESUME_PLAN_ID,
        "original_run_id": ORIGINAL_RUN_ID,
        "incident_id": INCIDENT_ID,
        "resume_plan_version": 2,
        "resume_plan_schema": "AUTO_RESTART_RESUME_PLAN_V2",
        "resume_plan": {
            "schema": "AUTO_RESTART_RESUME_PLAN_V2",
            "resume_plan_version": 2,
            "plan_version": 2,
            "account_id": ACCOUNT_ID,
            "package_contract_ready": True,
            "phase_order": ["welcome", "follow", "unfollow"],
            "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
            "quota_remaining": {"welcome": 0, "follow": 10, "unfollow": 0, "outreach": 0},
            "retry_generation": 1,
        },
    }
    base.update(overrides)
    return base


def _plan_row(**overrides) -> dict:
    future_end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    base = {
        "id": RESUME_PLAN_ID,
        "run_id": ORIGINAL_RUN_ID,
        "account_id": ACCOUNT_ID,
        "resume_state": "resume_requested",
        "scheduled_window_end": future_end,
    }
    base.update(overrides)
    return base


class HumanConfirmedResumeClaimTest(unittest.TestCase):
    def test_detects_recovery_mode(self) -> None:
        self.assertTrue(is_human_confirmed_resume_request(_metadata()))
        self.assertFalse(
            is_human_confirmed_resume_request(
                {"auto_restart": True, "source": "auto_restart_tick"}
            )
        )

    def test_valid_authorized_resume_passes_with_policy(self) -> None:
        with patch.object(store, "load_resume_plan", return_value=_plan_row()):
            ok, reason, policy = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=_metadata(),
            )
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertIsNotNone(policy)
        self.assertEqual(policy["recovery_mode"], "human_confirmed_resume")
        self.assertEqual(policy["prior_run_id"], ORIGINAL_RUN_ID)
        self.assertEqual(policy["incident_id"], INCIDENT_ID)
        self.assertEqual(
            policy["phases_to_run"], {"welcome": False, "follow": True, "unfollow": False}
        )
        self.assertEqual(policy["quota_remaining"]["follow"], 10)
        self.assertEqual(policy["retry_generation"], 1)

    def test_canonical_request_attempt_is_propagated_for_checkpoint_provenance(self) -> None:
        meta = _metadata(attempt_id=2, retry_index=1)
        meta["resume_plan"]["attempt_id"] = 1
        with patch.object(store, "load_resume_plan", return_value=_plan_row()):
            ok, reason, policy = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=meta,
            )
        self.assertTrue(ok, reason)
        self.assertIsNotNone(policy)
        self.assertEqual(policy["attempt_id"], 2)
        self.assertEqual(policy["retry_index"], 1)
        self.assertEqual(policy["request_metadata"]["attempt_id"], 2)
        self.assertEqual(policy["request_metadata"]["retry_index"], 1)

    def test_invalid_present_attempt_contract_fails_closed(self) -> None:
        with patch.object(store, "load_resume_plan", return_value=_plan_row()):
            ok, reason, policy = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=_metadata(attempt_id=2, retry_index=0),
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "resume_plan_invalid")
        self.assertIsNone(policy)

    def test_zero_action_restriction_preflight_passes_only_with_exact_contract(self) -> None:
        meta = _metadata(restriction_preflight_only=True)
        meta["resume_plan"] = {
            "schema": "AUTO_RESTART_RESUME_PLAN_V2",
            "resume_plan_version": 2,
            "plan_version": 2,
            "resume_kind": "instagram_restriction_preflight",
            "restriction_preflight_only": True,
            "account_id": ACCOUNT_ID,
            "incident_id": INCIDENT_ID,
            "phase_order": ["welcome", "follow", "unfollow"],
            "phases_to_run": {"welcome": False, "follow": False, "unfollow": False},
            "quota_remaining": {
                "welcome": 0,
                "follow": 0,
                "unfollow": 0,
                "outreach": 0,
                "total": 0,
            },
        }
        with patch.object(store, "load_resume_plan", return_value=_plan_row()):
            ok, reason, policy = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=meta,
            )
        self.assertTrue(ok, reason)
        self.assertIsNotNone(policy)
        self.assertTrue(policy["restriction_preflight_only"])
        self.assertFalse(any(policy["phases_to_run"].values()))

    def test_restriction_preflight_with_business_phase_fails_closed(self) -> None:
        meta = _metadata(restriction_preflight_only=True)
        meta["resume_plan"].update(
            {
                "resume_kind": "instagram_restriction_preflight",
                "restriction_preflight_only": True,
                "incident_id": INCIDENT_ID,
            }
        )
        with patch.object(store, "load_resume_plan", return_value=_plan_row()):
            ok, reason, policy = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=meta,
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "restriction_preflight_contract_invalid")
        self.assertIsNone(policy)

    def test_missing_frozen_plan_is_invalid(self) -> None:
        with patch.object(store, "load_resume_plan", return_value=_plan_row()):
            ok, reason, policy = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=_metadata(resume_plan=None),
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "resume_plan_invalid")
        self.assertIsNone(policy)

    def test_zero_quota_enabled_phase_is_blocked(self) -> None:
        meta = _metadata()
        meta["resume_plan"]["quota_remaining"]["follow"] = 0
        with patch.object(store, "load_resume_plan", return_value=_plan_row()):
            ok, reason, _ = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=meta,
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "quota_inconsistency_blocked")

    def test_missing_authorization_links_are_invalid(self) -> None:
        for missing in ("resume_plan_id", "original_run_id", "incident_id"):
            meta = _metadata()
            meta.pop(missing)
            ok, reason, policy = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=meta,
            )
            self.assertFalse(ok, missing)
            self.assertEqual(reason, "resume_plan_invalid", missing)
            self.assertIsNone(policy, missing)

    def test_plan_not_marked_resume_requested_is_consumed(self) -> None:
        with patch.object(
            store,
            "load_resume_plan",
            return_value=_plan_row(resume_state="awaiting_human_resume_authorization"),
        ):
            ok, reason, _ = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=_metadata(),
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "resume_authorization_consumed")

    def test_window_expired_is_refused(self) -> None:
        past_end = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with patch.object(
            store,
            "load_resume_plan",
            return_value=_plan_row(scheduled_window_end=past_end),
        ):
            ok, reason, _ = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=_metadata(),
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "resume_authorization_expired")

    def test_account_mismatch_is_invalid(self) -> None:
        with patch.object(
            store,
            "load_resume_plan",
            return_value=_plan_row(account_id="00000000-0000-4000-8000-000000000000"),
        ):
            ok, reason, _ = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=_metadata(),
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "resume_plan_invalid")

    def test_plan_load_failure_refuses_resume(self) -> None:
        with patch.object(
            store, "load_resume_plan", side_effect=RuntimeError("network down")
        ):
            ok, reason, _ = validate_auto_restart_request_at_claim(
                account_id=ACCOUNT_ID,
                metadata=_metadata(),
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "resume_plan_invalid")


class RecoveryResumeFailureEnrichmentTest(unittest.TestCase):
    """A failed human-confirmed resume must enrich the ORIGINAL incident."""

    REQUEST_ID = "7d3b4a4d-0000-4e0f-8a53-51e529a0a001"
    RESUME_RUN_ID = "ab46c4a5-72c5-4b16-9f0f-96f6f2ff22bb"

    def test_failed_resume_enriches_original_incident_and_flags_reintervention(self) -> None:
        import account_run_request_consumer as consumer

        run_row = {
            "id": self.RESUME_RUN_ID,
            "status": "failed",
            "performance_summary": {
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "actual_logged_in_username_not_detected",
                "run_type": "account_session",
            },
        }
        with (
            patch.object(consumer.supabase_client, "load_run_row", return_value=run_row),
            patch.object(
                consumer.supabase_client, "get_account_username", return_value="mythyl_fitness"
            ),
            patch.object(
                consumer.runtime_incidents,
                "publish_account_incident",
                return_value={"published": True, "incident_id": INCIDENT_ID, "occurrence_count": 2},
            ) as publish,
            patch.object(store, "mark_resume_outcome") as mark_outcome,
            patch.object(consumer, "_update_incident_recovery_state") as update_state,
        ):
            consumer._publish_run_failure_incident(
                request_id=self.REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=self.RESUME_RUN_ID,
                run_type="account_session",
                exit_code=75,
                timed_out=False,
                canceled=False,
                request_metadata={
                    "auto_restart": True,
                    "source": "auto_restart_tick",
                    "recovery_mode": "human_confirmed_resume",
                    "original_run_id": ORIGINAL_RUN_ID,
                    "incident_id": INCIDENT_ID,
                    "resume_plan_id": RESUME_PLAN_ID,
                },
            )
        kwargs = publish.call_args.kwargs
        # Same dedupe key as the original incident: enrichment, not duplication.
        self.assertEqual(
            kwargs["dedupe_key"],
            f"account:{ACCOUNT_ID}:run:{ORIGINAL_RUN_ID}:run_identity_verification_failed",
        )
        self.assertEqual(kwargs["run_id"], ORIGINAL_RUN_ID)
        self.assertEqual(kwargs["metadata"]["resume_run_id"], self.RESUME_RUN_ID)
        mark_outcome.assert_called_once_with(
            original_run_id=ORIGINAL_RUN_ID,
            succeeded=False,
            reason_code="actual_logged_in_username_not_detected",
        )
        update_state.assert_called_once()
        self.assertEqual(update_state.call_args.kwargs["state"], "reintervention_required")

    def test_terminal_failure_records_plan_state_for_normal_runs(self) -> None:
        import account_run_request_consumer as consumer

        run_row = {
            "id": ORIGINAL_RUN_ID,
            "status": "failed",
            "performance_summary": {
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "actual_logged_in_username_not_detected",
                "run_type": "account_session",
            },
        }
        with (
            patch.object(consumer.supabase_client, "load_run_row", return_value=run_row),
            patch.object(
                consumer.supabase_client, "get_account_username", return_value="mythyl_fitness"
            ),
            patch.object(
                consumer.runtime_incidents,
                "publish_account_incident",
                return_value={"published": True, "incident_id": INCIDENT_ID, "occurrence_count": 1},
            ),
            patch.object(store, "record_terminal_failure") as record_terminal,
        ):
            consumer._publish_run_failure_incident(
                request_id=self.REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=ORIGINAL_RUN_ID,
                run_type="account_session",
                exit_code=75,
                timed_out=False,
                canceled=False,
                request_metadata={},
            )
        record_terminal.assert_called_once_with(
            run_id=ORIGINAL_RUN_ID,
            incident_type="run_identity_verification_failed",
            reason_code="actual_logged_in_username_not_detected",
            incident_id=INCIDENT_ID,
        )


if __name__ == "__main__":
    unittest.main()
