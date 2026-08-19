from __future__ import annotations

import unittest
from pathlib import Path

from worker_runtime_identity import WorkerRuntimeIdentity


class P0CRuntimeIdentityHotfixV1Test(unittest.TestCase):
    def test_real_identity_contract_exposes_worker_sha_not_full_sha(self) -> None:
        identity = WorkerRuntimeIdentity(
            runtime_root="/release", worker_sha="a" * 40, source="test"
        )
        self.assertEqual(identity.worker_sha, "a" * 40)
        self.assertFalse(hasattr(identity, "full_sha"))

    def test_protected_path_has_no_full_sha_access(self) -> None:
        source = (Path(__file__).parents[1] / "runner.py").read_text(encoding="utf-8")
        self.assertNotIn("_CERTIFIED_RUNTIME_IDENTITY.full_sha", source)
        self.assertIn("_resolve_active_worker_release_sha() or None", source)

    def test_recovery_batch_is_ordered_before_normal_ct_scan(self) -> None:
        source = (Path(__file__).parents[1] / "runner.py").read_text(encoding="utf-8")
        recovery = source.index("_process_follow_candidate_recovery_batch(", 10000)
        normal_scan = source.index('"target_scan_started"', recovery)
        self.assertLess(recovery, normal_scan)
        self.assertIn('"follow_recovery_like_already_liked_skip"', source)
        self.assertIn('"already_following_external_or_unattributed"', source)

    def test_recovery_queue_failure_is_fail_closed_before_normal_scan(self) -> None:
        source = (Path(__file__).parents[1] / "runner.py").read_text(encoding="utf-8")
        claim_failure = source.index('"follow_candidate_recovery_claim_failed"')
        failure_block = source[claim_failure : claim_failure + 700]
        self.assertIn("safe_to_continue_ui=False", failure_block)
        self.assertIn("return False, 0", failure_block)

    def test_requested_after_worker_tap_is_terminal_without_credit(self) -> None:
        source = (Path(__file__).parents[1] / "runner.py").read_text(encoding="utf-8")
        marker = source.index('state_after == "requested"')
        block = source[marker : marker + 1500]
        self.assertIn('outcome="follow_requested_after_worker_tap_uncredited"', block)
        self.assertIn("follow_counter_delta=0", block)
        self.assertIn("duplicate_retry_suppressed=True", block)


if __name__ == "__main__":
    unittest.main()
