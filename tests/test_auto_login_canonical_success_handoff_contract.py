from __future__ import annotations

import inspect
import unittest

import instagram_login_provisioner_orchestrator as orchestrator


EXPECTED_VARIANTS = {
    "direct_login",
    "already_connected",
    "continue_as_candidate",
    "wrong_account_replacement",
    "use_another_profile",
    "returned_login_form",
    "credential_form",
    "samsung_password_prompt",
    "save_login_info",
    "location_services",
    "setup_new_device",
    "email",
    "sms",
    "whatsapp",
    "authenticator",
}


class CanonicalPostAuthSuccessHandoffContractTests(unittest.TestCase):
    def test_all_known_login_variants_are_registered_for_one_handoff(self) -> None:
        self.assertEqual(
            set(orchestrator.CANONICAL_POST_AUTH_SUCCESS_VARIANTS),
            EXPECTED_VARIANTS,
        )

    def test_connected_terminals_cannot_use_the_retired_parallel_gate(self) -> None:
        source = inspect.getsource(orchestrator.run_login_provisioning_flow)
        self.assertNotIn("def _connected_identity_gate", source)
        self.assertNotIn("_connected_identity_gate(", source)
        self.assertEqual(source.count("def _canonical_post_auth_success_handoff"), 1)
        # Every currently implemented authenticated-success branch converges
        # here; changing this count forces a deliberate contract-test update.
        self.assertEqual(source.count("_canonical_post_auth_success_handoff("), 9)

    def test_no_literal_connected_state_exists_outside_canonical_identity_success(self) -> None:
        source = inspect.getsource(orchestrator.run_login_provisioning_flow)
        self.assertNotIn('final_login_status="connected"', source)
        self.assertIn('"connected_only_after_identity": True', source)
        self.assertIn('"client_finalization_eligible": True', source)

    def test_every_known_variant_has_the_same_fail_closed_identity_contract(self) -> None:
        calls: list[str] = []

        def verifier(_device: object, **kwargs: object) -> dict[str, object]:
            calls.append(str(kwargs.get("stage") or ""))
            return {
                "ok": True,
                "expected_account_username": "Expected.User",
                "actual_logged_in_username": "@expected.user",
                "profile_opened": True,
                "verification_method": "test_exact_profile",
            }

        for variant in sorted(EXPECTED_VARIANTS):
            metadata, failure = orchestrator._canonical_post_auth_identity_handoff(
                object(),
                verifier=verifier,
                account_id="account-id",
                expected_username="expected.user",
                expected_package_name="com.instagram.clone",
                run_id="run-id",
                run_type="login_provisioning",
                extra_metadata={"post_auth_success_variant": variant},
            )
            self.assertEqual(failure, "", variant)
            self.assertTrue(metadata["canonical_post_auth_handoff_reached"], variant)
            self.assertEqual(metadata["post_auth_handoff_stage"], "identity_guard", variant)
            self.assertTrue(metadata["expected_identity_verified"], variant)
            self.assertTrue(metadata["profile_opened"], variant)
            self.assertEqual(metadata["post_auth_success_variant"], variant)
            self.assertTrue(
                orchestrator._connected_status_publishable(
                    ok=True,
                    completed=True,
                    final_outcome="connected",
                    final_login_status="connected",
                    account_id="account-id",
                    extra_metadata={
                        **metadata,
                        "selected_route": "already_connected_expected",
                    },
                ),
                variant,
            )

        self.assertEqual(calls, ["login_provisioning_post_login_identity"] * len(EXPECTED_VARIANTS))

    def test_no_variant_can_convert_home_to_connected_when_identity_is_unknown(self) -> None:
        def verifier(_device: object, **_kwargs: object) -> dict[str, object]:
            return {
                "ok": False,
                "expected_account_username": "expected.user",
                "actual_logged_in_username": "",
                "profile_opened": False,
                "failure_reason": "own_profile_open_failed",
            }

        for variant in sorted(EXPECTED_VARIANTS):
            metadata, failure = orchestrator._canonical_post_auth_identity_handoff(
                object(),
                verifier=verifier,
                account_id="account-id",
                expected_username="expected.user",
                expected_package_name="com.instagram.clone",
                run_id="run-id",
                run_type="login_provisioning",
                extra_metadata={"post_auth_success_variant": variant},
            )
            self.assertEqual(failure, "own_profile_open_failed", variant)
            self.assertFalse(metadata["expected_identity_verified"], variant)
            self.assertFalse(metadata["profile_opened"], variant)
            self.assertFalse(
                orchestrator._connected_status_publishable(
                    ok=True,
                    completed=True,
                    final_outcome="connected",
                    final_login_status="connected",
                    account_id="account-id",
                    extra_metadata={
                        **metadata,
                        "selected_route": "already_connected_expected",
                    },
                ),
                variant,
            )


if __name__ == "__main__":
    unittest.main()
