from __future__ import annotations

import unittest

from auto_login_failure_contract import (
    AUTO_LOGIN_REASON_PHASES,
    FALLBACK_ERROR_CODE,
    FORBIDDEN_PERSISTED_CODE_PATTERN,
    SENSITIVE_REASON_OVERRIDES,
    normalize_auto_login_failure,
    persisted_error_code_is_safe,
)


class AutoLoginFailureContractTest(unittest.TestCase):
    def test_sensitive_internal_reason_maps_to_safe_projectable_code(self) -> None:
        contract = normalize_auto_login_failure("password_field_not_found")

        self.assertEqual(contract.persisted_error_code, "credential_input_field_unavailable")
        self.assertEqual(contract.phase, "login_form")
        self.assertFalse(contract.retryable)
        self.assertNotIn("internal_worker_reason", contract.incident_metadata())

    def test_reason_becomes_retryable_only_after_detector_correction(self) -> None:
        contract = normalize_auto_login_failure(
            "password_field_not_found",
            correction_deployed=True,
        )

        self.assertTrue(contract.retryable)

    def test_unknown_reason_uses_safe_fallback(self) -> None:
        contract = normalize_auto_login_failure("some_new_worker_failure")

        self.assertEqual(contract.persisted_error_code, FALLBACK_ERROR_CODE)
        self.assertFalse(contract.retryable)

    def test_noncanonical_unknown_reason_cannot_break_terminalization_contract(self) -> None:
        contract = normalize_auto_login_failure(
            "Unexpected worker text with spaces!",
            phase="bad phase/value",
        )

        self.assertEqual(contract.persisted_error_code, FALLBACK_ERROR_CODE)
        self.assertEqual(contract.internal_worker_reason, FALLBACK_ERROR_CODE)
        self.assertEqual(contract.phase, "unknown")

    def test_known_safe_reason_remains_stable(self) -> None:
        contract = normalize_auto_login_failure("device_lock_failed")

        self.assertEqual(contract.persisted_error_code, "device_lock_failed")
        self.assertEqual(contract.phase, "device_lock")

    def test_wrong_active_account_is_not_degraded_to_unclassified(self) -> None:
        contract = normalize_auto_login_failure("wrong_active_account_requires_admin_review")

        self.assertEqual(
            contract.persisted_error_code,
            "wrong_active_account_requires_admin_review",
        )
        self.assertEqual(contract.phase, "identity_verification")

    def test_instagram_wrong_password_maps_to_specific_secret_safe_terminal_code(self) -> None:
        contract = normalize_auto_login_failure("instagram_wrong_password")

        self.assertEqual(contract.persisted_error_code, "instagram_credentials_rejected")
        self.assertEqual(contract.phase, "submit_credentials")
        self.assertFalse(contract.retryable)
        self.assertIn("secure writer", contract.recommended_action)
        self.assertNotIn("password", str(contract.incident_metadata()).lower())

    def test_app_instance_mismatch_notification_states_no_credentials_entered(self) -> None:
        contract = normalize_auto_login_failure("assigned_instagram_app_instance_mismatch")

        self.assertEqual(contract.persisted_error_code, "assigned_instagram_app_instance_mismatch")
        self.assertEqual(contract.phase, "open_instagram")
        self.assertEqual(
            contract.operator_message,
            "L’application Instagram assignée n’a pas été ouverte. "
            "Aucune donnée de connexion n’a été saisie.",
        )
        self.assertFalse(contract.retryable)

    def test_every_known_persisted_code_satisfies_sql_constraint(self) -> None:
        internal_reasons = set(AUTO_LOGIN_REASON_PHASES) | set(SENSITIVE_REASON_OVERRIDES)
        for reason in sorted(internal_reasons):
            with self.subTest(reason=reason):
                code = normalize_auto_login_failure(reason).persisted_error_code
                self.assertTrue(persisted_error_code_is_safe(code))
                self.assertIsNone(FORBIDDEN_PERSISTED_CODE_PATTERN.search(code))

    def test_internal_reason_is_retained_only_on_server_contract(self) -> None:
        contract = normalize_auto_login_failure("password_field_not_found")

        self.assertEqual(contract.internal_worker_reason, "password_field_not_found")
        self.assertNotIn("password", str(contract.incident_metadata()).lower())


if __name__ == "__main__":
    unittest.main()
