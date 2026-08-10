from __future__ import annotations

import unittest

from auto_login_failure_contract import normalize_auto_login_failure
from runtime_incident_matrix import classify_terminal_run_failure


class HumanAssistedAutoLoginContractTests(unittest.TestCase):
    def test_unknown_ui_is_fail_closed_and_requires_human_assistance(self) -> None:
        contract = normalize_auto_login_failure("unknown_login_screen", phase="detect_surface")

        self.assertEqual(contract.internal_worker_reason, "unknown_login_screen")
        self.assertEqual(contract.persisted_error_code, "unclassified_auto_login_failure")
        self.assertFalse(contract.retryable)
        self.assertEqual(
            contract.operator_message,
            "The Instagram login could not be completed automatically and requires human intervention.",
        )

        decision = classify_terminal_run_failure(
            exit_code=1,
            timed_out=False,
            run_status="failed",
            canceled=False,
            performance_summary={
                "domain": "auto_login",
                "reason_code": "unknown_login_screen",
                "phase": "detect_surface",
            },
            run_type="login_provisioning",
        )
        self.assertTrue(decision.should_publish)
        self.assertTrue(decision.requires_operator_review)
        self.assertTrue(decision.blocking_campaign)
        self.assertTrue(decision.notify_channels)
        self.assertEqual(decision.incident_type, "auto_login_failed")
        self.assertNotIn("password", str(decision.metadata_safe).lower())
        self.assertNotIn("otp", str(decision.metadata_safe).lower())
        self.assertNotIn("token", str(decision.metadata_safe).lower())


if __name__ == "__main__":
    unittest.main()
