from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import unittest

import runner
from target_availability_runtime import (
    FOLLOW_SUMMARY_CONTRACT_V1,
    canonical_follow_summary_contract,
    observation_from_rotation_summary,
)


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
TARGET_ID = "44444444-4444-4444-8444-444444444444"
RUN_ID = "run-natural-one"
USERNAME = "target.one"


def produce(
    detection=None,
    *,
    account_id=ACCOUNT_ID,
    run_id=RUN_ID,
    target_id=TARGET_ID,
    source_target_id=TARGET_ID,
    username=USERNAME,
    session_committed=False,
    follow_verified=False,
    follow_source_target_id=TARGET_ID,
    observed_at=NOW,
):
    return runner._target_availability_existing_proof_fields(
        detection,
        account_id=account_id,
        run_id=run_id,
        target_id=target_id,
        source_target_id=source_target_id,
        expected_username=username,
        session_committed=session_committed,
        follow_verified_persisted=follow_verified,
        follow_verified_source_target_id=follow_source_target_id,
        observed_at=observed_at,
    )


def consume(summary, **updates):
    kwargs = {
        "expected_account_id": ACCOUNT_ID,
        "expected_target_id": TARGET_ID,
        "expected_username": USERNAME,
        "expected_run_id": RUN_ID,
        "now": NOW,
    }
    kwargs.update(updates)
    return canonical_follow_summary_contract(summary, **kwargs)


def observe(summary, **updates):
    kwargs = {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "account_id": ACCOUNT_ID,
        "target_id": TARGET_ID,
        "username": USERNAME,
        "run_id": RUN_ID,
        "target_index": 0,
        "summary": summary,
        "observed_at": NOW,
    }
    kwargs.update(updates)
    return observation_from_rotation_summary(**kwargs)


class TargetAvailabilitySummaryContractV1Tests(unittest.TestCase):
    def exact_surface(self, **updates):
        detection = {
            "is_followers_list": True,
            "action_bar_title": USERNAME,
            "visible_header_texts": ["Followers"],
        }
        detection.update(updates)
        return produce(detection)

    def test_01_successful_target_profile_proof_transported(self):
        contract = consume(self.exact_surface())
        self.assertTrue(contract["profile_found"])
        self.assertEqual(contract["lookup_result"], "found")

    def test_02_exact_observed_username_transported(self):
        contract = consume(self.exact_surface(action_bar_title="@Target.One"))
        self.assertEqual(contract["observed_username"], USERNAME)
        self.assertEqual(contract["identity_confidence"], "medium")

    def test_03_followers_surface_proof_transported(self):
        contract = consume(self.exact_surface())
        self.assertEqual(contract["followers_surface"], "normal")
        self.assertTrue(contract["followers_surface_entered"])

    def test_04_verified_follow_evidence_reused_for_availability_only(self):
        contract = consume(produce(follow_verified=True))
        self.assertTrue(contract["profile_found"])
        self.assertTrue(contract["follow_verified_persisted"])
        self.assertIsNone(contract["observed_username"])
        self.assertEqual(contract["identity_confidence"], "unknown")

    def test_05_wrong_target_binding_rejected(self):
        self.assertEqual(produce(source_target_id="wrong-target", follow_verified=True), {})
        wrong_follow = produce(
            follow_verified=True,
            follow_source_target_id="wrong-target",
        )
        self.assertEqual(wrong_follow, {})

    def test_06_stale_evidence_rejected(self):
        stale = self.exact_surface()
        stale["proof_observed_at"] = (NOW - timedelta(hours=25)).isoformat()
        self.assertEqual(consume(stale), {})

    def test_07_ambiguous_username_rejected(self):
        ambiguous = produce(
            {
                "is_followers_list": True,
                "action_bar_title": "different.target",
                "visible_header_texts": ["Followers"],
            }
        )
        self.assertEqual(ambiguous, {})

    def test_08_exact_username_without_stable_id_is_medium_only(self):
        item = observe(self.exact_surface())
        self.assertIsNone(item.observed_stable_platform_user_id)
        self.assertEqual(item.observed_username, USERNAME)
        self.assertEqual(item.identity_confidence, "medium")

    def test_09_no_stable_id_never_becomes_high_confidence(self):
        item = observe(self.exact_surface())
        self.assertNotEqual(item.identity_confidence, "high")

    def test_10_first_not_found_is_reversible_evidence_only(self):
        summary = {
            "proof_contract_version": FOLLOW_SUMMARY_CONTRACT_V1,
            "proof_account_id": ACCOUNT_ID,
            "proof_run_id": RUN_ID,
            "proof_target_id": TARGET_ID,
            "proof_source_target_id": TARGET_ID,
            "target_binding_proved": True,
            "not_found_proved": True,
            "proof_source": "existing_profile_resolution_not_found",
            "proof_observed_at": NOW.isoformat(),
        }
        item = observe(summary)
        self.assertFalse(item.profile_found)
        self.assertEqual(item.lookup_result, "not_found")
        self.assertNotIn("archive", item.reason_codes)

    def test_11_account_challenge_is_not_target_unavailable(self):
        item = observe(
            {
                "session_state": "restricted",
                "follow_stop_reason": "account_challenge",
                "profile_found": False,
                "lookup_result": "not_found",
            }
        )
        self.assertIsNone(item.profile_found)
        self.assertEqual(item.lookup_result, "unknown")

    def test_12_follow_revert_is_not_verified_follow(self):
        summary = produce(None, follow_verified=False)
        self.assertEqual(summary, {})
        self.assertEqual(consume({"follow_revert": True, "follows_completed_count": 1}), {})

    def test_13_skipped_candidate_does_not_fabricate_availability(self):
        raw = {
            "skipped_tap": True,
            "follows_completed_count": 1,
            "follow_session_outcome": "already_connected_safe_skipped",
        }
        self.assertEqual(consume(raw), {})

    def test_14_no_post_path_preserves_existing_surface_proof(self):
        summary = self.exact_surface()
        summary.update({"post_count": 0, "post_follow_outcome": "no_posts"})
        self.assertTrue(consume(summary)["profile_found"])

    def test_15_collaborative_post_path_preserves_existing_surface_proof(self):
        summary = self.exact_surface()
        summary.update({"collaborative_post": True, "collaborative_identity_safe": True})
        self.assertEqual(consume(summary)["observed_username"], USERNAME)

    def test_16_resume_path_preserves_existing_surface_proof(self):
        summary = self.exact_surface()
        summary.update({"resume_used": True, "resume_depth": 4})
        self.assertEqual(consume(summary)["followers_surface"], "normal")

    def test_17_multi_account_and_tenant_scope_isolation(self):
        summary = self.exact_surface()
        self.assertEqual(
            consume(
                summary,
                expected_account_id="55555555-5555-4555-8555-555555555555",
            ),
            {},
        )
        item = observe(
            summary,
            tenant_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        self.assertEqual(item.tenant_id, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertEqual(item.account_id, ACCOUNT_ID)

    def test_18_absent_summary_fields_fail_closed(self):
        self.assertEqual(consume({}), {})
        item = observe({"profile_found": True, "observed_username": USERNAME})
        self.assertIsNone(item.profile_found)
        self.assertIsNone(item.observed_username)

    def test_producer_is_pure_and_adds_no_device_probe_or_sleep(self):
        source = inspect.getsource(runner._target_availability_existing_proof_fields)
        for forbidden in ("detect_followers", "screenshot(", "dump_hierarchy", "time.sleep", "d."):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
