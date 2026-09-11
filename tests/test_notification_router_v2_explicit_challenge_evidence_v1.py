from __future__ import annotations

from pathlib import Path
import re
import unittest

from runtime_incident_matrix import classify_terminal_run_failure


MIGRATION = Path(__file__).parents[1] / "supabase/migrations/20260912090000_notification_router_v2_explicit_challenge_evidence_v1.sql"


class NotificationRouterExplicitChallengeEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_generic_identity_verification_failure_cannot_route_as_challenge(self) -> None:
        challenge_branch = re.search(
            r"elsif explicit_challenge then\s+routed_type := 'auto_login\.challenge'",
            self.sql,
            re.IGNORECASE,
        )
        self.assertIsNotNone(challenge_branch)
        self.assertNotIn("(challenge|checkpoint|verification)", self.sql)
        self.assertIn("elsif combined_reason ~ '(identity|verification|what_happened|connectivity|loading)'", self.sql)
        self.assertRegex(
            self.sql,
            r"notification_router_general_incident_v2\(\)[\s\S]*verification\|mismatch\|wrong_account\|what_happened\|connectivity\|loading",
        )

    def test_actual_challenge_and_checkpoint_keep_explicit_route(self) -> None:
        self.assertIn("(challenge|checkpoint)", self.sql)
        for reason in ("login_challenge", "checkpoint"):
            with self.subTest(reason=reason):
                decision = classify_terminal_run_failure(
                    exit_code=75,
                    performance_summary={
                        "reason": reason,
                        "account_identity_failure_reason": reason,
                    },
                )
                self.assertEqual(decision.incident_type, "account_login_required")

    def test_identity_outcomes_remain_distinct_before_router(self) -> None:
        unverifiable = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "actual_logged_in_username_not_detected",
            },
        )
        mismatch = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "active_instagram_account_mismatch",
            },
        )
        loading = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "instagram_connectivity_unverified",
            },
        )
        what_happened = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={
                "reason": "active_instagram_account_mismatch",
                "account_identity_failure_reason": "own_profile_navigation_what_happened_screen",
            },
        )
        self.assertEqual(unverifiable.incident_type, "run_identity_verification_failed")
        self.assertEqual(mismatch.incident_type, "active_instagram_account_mismatch")
        self.assertEqual(loading.incident_type, "run_identity_verification_failed")
        self.assertEqual(what_happened.incident_type, "run_identity_verification_failed")

    def test_restriction_and_login_required_keep_truthful_incidents(self) -> None:
        restriction = classify_terminal_run_failure(
            exit_code=76,
            performance_summary={"reason": "instagram_account_restriction_messages_disabled"},
        )
        login = classify_terminal_run_failure(
            exit_code=75,
            performance_summary={"reason": "login_required"},
        )
        self.assertEqual(restriction.incident_type, "instagram_account_restriction")
        self.assertEqual(login.incident_type, "account_login_required")

    def test_function_keeps_security_definer_acl_and_fixed_search_path(self) -> None:
        self.assertRegex(self.sql, r"security definer\s+set search_path = ''")
        self.assertIn(
            "revoke all on function public.notification_router_auto_login_failure_v2() from public, anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.notification_router_auto_login_failure_v2() to service_role;",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
