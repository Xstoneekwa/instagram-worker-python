from __future__ import annotations

import unittest

from instagram_login_screen_router import route_login_screen


class InstagramLoginScreenRouterTest(unittest.TestCase):
    def test_continue_as_same_username_allows_continue(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="cinema_catchup",
            screen_type="continue_as_candidate",
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "continue_expected_account")
        self.assertTrue(decision.should_tap_continue)
        self.assertFalse(decision.should_escalate)
        self.assertEqual(decision.next_action, "continue_then_secure_password_step_later")
        self.assertEqual(decision.reason, "suggested_username_matches_expected")

    def test_continue_as_different_active_blocks_for_admin_review(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            suggested_username="old_account",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda _username: {"found": True, "lifecycle_status": "active"},
            clone_reuse_allowed=True,
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.decision, "block_wrong_suggested_account")
        self.assertTrue(decision.should_escalate)
        self.assertEqual(decision.publish_login_status, "mismatch")
        self.assertEqual(decision.provisioning_status, "blocked")
        self.assertEqual(decision.onboarding_status, "support_required")
        self.assertEqual(decision.dashboard_action_type, "review_account_mismatch")
        self.assertFalse(decision.should_tap_continue)
        self.assertFalse(decision.should_tap_use_another_profile)

    def test_continue_as_different_unknown_blocks_for_admin_review(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            suggested_username="unknown_account",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda _username: {"found": False, "lifecycle_status": "unknown"},
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.decision, "block_wrong_suggested_account")
        self.assertTrue(decision.should_escalate)
        self.assertEqual(decision.reason, "wrong_suggested_account_requires_admin_review")

    def test_lifecycle_status_canceled_uppercase_is_normalized(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="random_old_profile",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda _username: {"lifecycle_status": "CANCELED"},
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.decision, "use_another_profile_previous_account_stopped")

    def test_canceled_with_clone_reuse_allows_use_another_profile_override(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            suggested_username="old_account",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda _username: {"found": True, "lifecycle_status": "canceled"},
            clone_reuse_allowed=True,
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "use_another_profile_previous_account_stopped")
        self.assertTrue(decision.should_tap_use_another_profile)
        self.assertFalse(decision.should_escalate)
        self.assertEqual(decision.audit_reason, "previous_account_stopped_override")
        self.assertEqual(decision.reason, "previous_account_canceled_clone_reusable")

    def test_canceled_without_clone_reuse_blocks(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            suggested_username="old_account",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda _username: {"found": True, "lifecycle_status": "canceled"},
            clone_reuse_allowed=False,
        )

        self.assertEqual(decision.decision, "block_wrong_suggested_account")
        self.assertTrue(decision.should_escalate)
        self.assertFalse(decision.should_tap_use_another_profile)

    def test_archived_and_stopped_aliases_are_treated_as_canceled(self) -> None:
        for status in ("archived", "stopped"):
            with self.subTest(status=status):
                decision = route_login_screen(
                    expected_username="new_account",
                    suggested_username="old_account",
                    screen_type="continue_as_candidate",
                    account_lifecycle_lookup=lambda _username, s=status: {
                        "found": True,
                        "lifecycle_status": s,
                    },
                    clone_reuse_allowed=True,
                )

                self.assertEqual(decision.decision, "use_another_profile_previous_account_stopped")
                self.assertEqual(decision.audit_reason, "previous_account_stopped_override")

    def test_login_form_empty_starts_login_form_flow(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            screen_type="login_form_empty",
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "start_login_form_flow")
        self.assertTrue(decision.should_start_login_form_flow)
        self.assertFalse(decision.should_escalate)
        self.assertEqual(decision.next_action, "secure_credentials_required_later")

    def test_join_instagram_landing_uses_existing_profile_path(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            screen_type="join_instagram_landing",
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "open_existing_profile_from_join_landing")
        self.assertTrue(decision.should_tap_already_have_profile)
        self.assertFalse(decision.should_tap_continue)
        self.assertFalse(decision.should_tap_use_another_profile)
        self.assertFalse(decision.should_start_login_form_flow)
        self.assertEqual(decision.next_action, "tap_already_have_profile_then_login_form")
        self.assertEqual(decision.reason, "join_instagram_landing_existing_profile_required")

    def test_prefilled_wrong_username_starts_replace_username_flow(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="i_m_your_traker",
            screen_type="login_form_prefilled_username",
            account_lifecycle_lookup=lambda _username: {"lifecycle_status": "canceled"},
            clone_reuse_allowed=True,
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "start_login_form_flow_replace_username")
        self.assertTrue(decision.should_start_login_form_flow)
        self.assertEqual(decision.reason, "prefilled_old_username_reusable_replace")

    def test_prefilled_wrong_username_without_reusable_lifecycle_blocks(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="i_m_your_traker",
            screen_type="login_form_prefilled_username",
            account_lifecycle_lookup=lambda _username: {"lifecycle_status": "unknown"},
            clone_reuse_allowed=True,
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.decision, "block_wrong_suggested_account")
        self.assertEqual(decision.reason, "username_prefilled_mismatch_requires_review")
        self.assertTrue(decision.should_escalate)

    def test_prefilled_expected_username_starts_password_flow(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="cinema_catchup",
            screen_type="login_form_prefilled_username",
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "start_login_form_flow_prefilled_expected")
        self.assertTrue(decision.should_start_login_form_flow)

    def test_continue_password_only_expected_account_starts_login_form_flow(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="cinema_catchup",
            screen_type="continue_password_only",
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "start_login_form_flow")
        self.assertTrue(decision.should_start_login_form_flow)
        self.assertEqual(decision.next_action, "secure_password_required_later")

    def test_continue_password_only_wrong_account_blocks(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="other_profile",
            screen_type="continue_password_only",
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.decision, "block_wrong_suggested_account")
        self.assertTrue(decision.should_escalate)

    def test_account_picker_expected_present_selects_expected(self) -> None:
        decision = route_login_screen(
            expected_username="random_expected",
            screen_type="account_picker",
            available_usernames=["random_expected", "random_old_profile"],
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "select_expected_account_from_picker")
        self.assertTrue(decision.should_tap_expected_account)
        self.assertEqual(decision.target_username, "random_expected")

    def test_account_picker_expected_absent_stops_safe(self) -> None:
        decision = route_login_screen(
            expected_username="random_expected",
            screen_type="account_picker",
            available_usernames=["random_old_profile"],
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.decision, "expected_account_not_listed")
        self.assertFalse(decision.should_tap_expected_account)
        self.assertEqual(decision.dashboard_action_type, "review_account_picker_missing_expected")

    def test_account_picker_duplicate_expected_rows_are_ambiguous(self) -> None:
        decision = route_login_screen(
            expected_username="random_expected",
            screen_type="account_picker",
            available_usernames=["random_expected", "random_old_profile", "random_expected"],
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.decision, "ambiguous_expected_account_row")
        self.assertFalse(decision.should_tap_expected_account)

    def test_account_picker_missing_expected_username_stops_safe(self) -> None:
        decision = route_login_screen(
            expected_username="",
            screen_type="account_picker",
            available_usernames=["random_expected"],
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.decision, "expected_username_missing")

    def test_active_profile_expected_account_is_connected(self) -> None:
        decision = route_login_screen(
            expected_username="random_expected",
            suggested_username="random_expected",
            screen_type="active_account_profile",
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "connected_expected_account")
        self.assertFalse(decision.should_recover_old_logged_in_account)

    def test_active_profile_canceled_reusable_allows_recovery(self) -> None:
        decision = route_login_screen(
            expected_username="random_expected",
            suggested_username="random_old_profile",
            screen_type="active_account_profile",
            account_lifecycle_lookup=lambda _username: {"lifecycle_status": "canceled"},
            clone_reuse_allowed=True,
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision, "recover_old_logged_in_account")
        self.assertTrue(decision.should_recover_old_logged_in_account)

    def test_active_profile_active_blocks_recovery(self) -> None:
        decision = route_login_screen(
            expected_username="random_expected",
            suggested_username="random_old_profile",
            screen_type="active_account_profile",
            account_lifecycle_lookup=lambda _username: {"lifecycle_status": "active"},
            clone_reuse_allowed=True,
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.decision, "block_wrong_active_account")
        self.assertEqual(decision.dashboard_action_type, "review_logged_in_account_mismatch")

    def test_unknown_screen_has_no_action(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            screen_type="unknown",
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.decision, "unknown_no_action")
        self.assertFalse(decision.should_tap_continue)
        self.assertFalse(decision.should_tap_use_another_profile)
        self.assertFalse(decision.should_start_login_form_flow)
        self.assertFalse(decision.should_escalate)

    def test_username_normalization_is_case_insensitive_and_strips_at(self) -> None:
        decision = route_login_screen(
            expected_username="@Cinema_Catchup",
            suggested_username="cinema_catchup",
            screen_type="continue_as_candidate",
        )

        self.assertEqual(decision.normalized_expected_username, "cinema_catchup")
        self.assertEqual(decision.normalized_suggested_username, "cinema_catchup")
        self.assertTrue(decision.should_tap_continue)

    def test_metadata_is_safe(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            suggested_username="old_account",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda _username: {"found": True, "lifecycle_status": "active"},
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.metadata["source"], "login_screen_router")
        self.assertEqual(decision.metadata["screen_type"], "continue_as_candidate")
        self.assertEqual(decision.metadata["decision"], "block_wrong_suggested_account")
        for key in (
            "password",
            "secret_ref",
            "vault",
            "xml",
            "screenshot",
            "adb_serial",
            "device_udid",
            "service_role",
            "cookie",
        ):
            self.assertNotIn(key, decision.metadata)

    def test_lookup_exception_fails_safe_to_admin_review(self) -> None:
        def raise_lookup(_username: str) -> dict:
            raise RuntimeError("db down")

        decision = route_login_screen(
            expected_username="new_account",
            suggested_username="old_account",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=raise_lookup,
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.decision, "block_wrong_suggested_account")
        self.assertTrue(decision.should_escalate)
        self.assertEqual(
            decision.reason,
            "lifecycle_lookup_failed_wrong_suggested_account_requires_admin_review",
        )

    def test_i_m_your_traker_canceled_case_allows_use_another_profile(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            suggested_username="i_m_your_traker",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda username: {
                "found": username == "i_m_your_traker",
                "lifecycle_status": "canceled",
                "account_id": "old-account-id",
            },
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.normalized_suggested_username, "i_m_your_traker")
        self.assertEqual(decision.decision, "use_another_profile_previous_account_stopped")
        self.assertEqual(decision.audit_reason, "previous_account_stopped_override")
        self.assertFalse(decision.should_escalate)

    def test_cinema_catchup_expected_i_m_your_traker_canceled_allows_use_another_profile(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="i_m_your_traker",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda username: {
                "found": username == "i_m_your_traker",
                "lifecycle_status": "canceled",
            },
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.decision, "use_another_profile_previous_account_stopped")
        self.assertTrue(decision.should_tap_use_another_profile)
        self.assertFalse(decision.should_escalate)

    def test_cinema_catchup_expected_i_m_your_traker_active_blocks(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="i_m_your_traker",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda _username: {"lifecycle_status": "active"},
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.decision, "block_wrong_suggested_account")
        self.assertEqual(decision.publish_login_status, "mismatch")
        self.assertEqual(decision.dashboard_action_type, "review_account_mismatch")

    def test_i_m_your_traker_expected_allows_continue(self) -> None:
        decision = route_login_screen(
            expected_username="i_m_your_traker",
            suggested_username="i_m_your_traker",
            screen_type="continue_as_candidate",
        )

        self.assertEqual(decision.decision, "continue_expected_account")
        self.assertTrue(decision.should_tap_continue)

    def test_random_old_profile_canceled_clone_reuse_allows_use_another_profile(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="random_old_profile",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda username: {
                "found": username == "random_old_profile",
                "lifecycle_status": "canceled",
            },
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.normalized_suggested_username, "random_old_profile")
        self.assertEqual(decision.decision, "use_another_profile_previous_account_stopped")
        self.assertTrue(decision.should_tap_use_another_profile)

    def test_random_old_profile_active_blocks_wrong_account(self) -> None:
        decision = route_login_screen(
            expected_username="cinema_catchup",
            suggested_username="random_old_profile",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda username: {
                "found": username == "random_old_profile",
                "lifecycle_status": "active",
            },
            clone_reuse_allowed=True,
        )

        self.assertEqual(decision.decision, "block_wrong_suggested_account")
        self.assertEqual(decision.publish_login_status, "mismatch")
        self.assertTrue(decision.should_escalate)

    def test_random_old_profile_expected_allows_continue(self) -> None:
        decision = route_login_screen(
            expected_username="random_old_profile",
            suggested_username="random_old_profile",
            screen_type="continue_as_candidate",
        )

        self.assertEqual(decision.decision, "continue_expected_account")
        self.assertTrue(decision.should_tap_continue)


if __name__ == "__main__":
    unittest.main()
