from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from unittest.mock import Mock

from instagram_credentials_runtime_access import (
    REDACTED,
    SecretValue,
    contains_forbidden_secret_keys,
    credential_result_safe_dict,
    get_instagram_credentials_for_login,
    parse_vault_secret_for_login,
    redact_credentials_payload,
    revealed_value_blocked_for_injection,
    vault_secret_shape_audit,
)


ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d"
VAULT_ID = "11111111-2222-4333-8444-555555555555"
SECRET_REF = f"supabase_vault://{VAULT_ID}"

ALLOWED_CREDENTIAL_DIAGNOSTIC_TOKENS = (
    "injectable_password_only",
    "secret_value_safe_for_injection",
    "vault_secret_has_password_key",
    "vault_secret_payload_missing_password",
    "vault_secret_password_invalid",
    "secret_provider",
    "supabase_vault",
    "password_secret_invalid",
    "password_secret_missing",
)


def _scrub_allowed_credential_diagnostic_tokens(rendered: str) -> str:
    scrubbed = str(rendered or "")
    for token in ALLOWED_CREDENTIAL_DIAGNOSTIC_TOKENS:
        scrubbed = scrubbed.replace(token, "")
    return scrubbed


def _active_credentials(**overrides):
    row = {
        "account_id": ACCOUNT_ID,
        "provider": "instagram",
        "username": "cinema_catchup",
        "secret_ref": SECRET_REF,
        "credentials_version": 3,
        "status": "active",
        "reauth_required": True,
    }
    row.update(overrides)
    return row


class InstagramCredentialsRuntimeAccessTest(unittest.TestCase):
    def test_success_active_credentials_returns_secret_value(self) -> None:
        lookup = Mock(return_value=_active_credentials())
        reader = Mock(return_value="fake-password-for-unit-tests")

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=lookup,
            secret_reader=reader,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.username, "cinema_catchup")
        self.assertIsInstance(result.password, SecretValue)
        self.assertEqual(result.password.reveal_for_login_executor(), "fake-password-for-unit-tests")
        self.assertEqual(result.credentials_version, 3)
        self.assertEqual(result.credentials_status, "active")
        self.assertTrue(result.reauth_required)

    def test_secret_value_str_and_repr_are_redacted(self) -> None:
        secret = SecretValue("fake-password-for-unit-tests")

        self.assertEqual(str(secret), REDACTED)
        self.assertEqual(repr(secret), REDACTED)

    def test_safe_dict_does_not_include_password(self) -> None:
        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=Mock(return_value="fake-password-for-unit-tests"),
        )

        safe = credential_result_safe_dict(result)
        rendered = json.dumps(safe, sort_keys=True)

        self.assertNotIn("fake-password-for-unit-tests", rendered)
        scrubbed = _scrub_allowed_credential_diagnostic_tokens(rendered.lower())
        self.assertNotIn("password", scrubbed)
        self.assertNotIn("secret", scrubbed)
        self.assertNotIn("vault", scrubbed)

    def test_safe_dict_does_not_include_secret_ref(self) -> None:
        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=Mock(return_value="fake-password-for-unit-tests"),
        )

        rendered = json.dumps(credential_result_safe_dict(result), sort_keys=True)

        self.assertNotIn("secret_ref", rendered)
        self.assertNotIn(SECRET_REF, rendered)

    def test_invalid_account_id_rejected(self) -> None:
        lookup = Mock()
        reader = Mock()

        result = get_instagram_credentials_for_login(
            account_id="not-a-uuid",
            credentials_lookup=lookup,
            secret_reader=reader,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "invalid_account_id")
        lookup.assert_not_called()
        reader.assert_not_called()

    def test_missing_credentials_returns_safe_failure(self) -> None:
        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=None),
            secret_reader=Mock(return_value="fake-password-for-unit-tests"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "credentials_not_found")

    def test_inactive_revoked_or_superseded_credentials_rejected(self) -> None:
        for status in ("revoked", "superseded", "inactive"):
            with self.subTest(status=status):
                reader = Mock(return_value="fake-password-for-unit-tests")

                result = get_instagram_credentials_for_login(
                    account_id=ACCOUNT_ID,
                    credentials_lookup=Mock(return_value=_active_credentials(status=status)),
                    secret_reader=reader,
                )

                self.assertFalse(result.ok)
                self.assertEqual(result.failure_reason, "credentials_not_active")
                reader.assert_not_called()

    def test_missing_username_rejected(self) -> None:
        reader = Mock(return_value="fake-password-for-unit-tests")

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials(username="")),
            secret_reader=reader,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "credentials_username_missing")
        reader.assert_not_called()

    def test_missing_secret_ref_rejected(self) -> None:
        reader = Mock(return_value="fake-password-for-unit-tests")

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials(secret_ref="")),
            secret_reader=reader,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "credentials_secret_ref_missing")
        reader.assert_not_called()

    def test_secret_reader_exception_returns_safe_failure(self) -> None:
        reader = Mock(side_effect=RuntimeError("vault down"))

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=reader,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "secret_reader_failed")
        self.assertNotIn("vault down", json.dumps(credential_result_safe_dict(result)))

    def test_empty_secret_rejected(self) -> None:
        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=Mock(return_value=""),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "vault_secret_empty")

    def test_vault_json_secret_extracts_password_only(self) -> None:
        payload = json.dumps(
            {
                "password": "secret-only",
                "account_id": ACCOUNT_ID,
                "credentials_version": 1000,
                "created_at": "2026-05-27T00:00:00Z",
            }
        )

        parsed = parse_vault_secret_for_login(payload)

        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.password, "secret-only")
        self.assertTrue(parsed.vault_secret_is_json)
        self.assertTrue(parsed.vault_secret_has_password_key)
        self.assertTrue(parsed.extracted_password_valid)
        self.assertTrue(parsed.secret_value_safe_for_injection)

    def test_vault_json_without_password_rejected(self) -> None:
        payload = json.dumps(
            {
                "account_id": ACCOUNT_ID,
                "credentials_version": 1000,
                "created_at": "2026-05-27T00:00:00Z",
            }
        )

        parsed = parse_vault_secret_for_login(payload)

        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.failure_reason, "vault_secret_payload_missing_password")

    def test_vault_json_invalid_password_rejected(self) -> None:
        payload = json.dumps(
            {
                "password": "",
                "account_id": ACCOUNT_ID,
                "credentials_version": 1000,
            }
        )

        parsed = parse_vault_secret_for_login(payload)

        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.failure_reason, "vault_secret_password_invalid")

    def test_legacy_plain_password_accepted_when_not_payload_shaped(self) -> None:
        parsed = parse_vault_secret_for_login("fake-password-for-unit-tests")

        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.password, "fake-password-for-unit-tests")
        self.assertFalse(parsed.vault_secret_is_json)

    def test_opaque_password_character_sequence_is_preserved(self) -> None:
        synthetic_passwords = (
            " password",
            "password ",
            " password ",
            "pa ss word",
            "mot-de-passe-é§🔐",
            "!@#$%^&*()_+-=[];':,.<>/?\\|`~",
        )
        for password in synthetic_passwords:
            plain = parse_vault_secret_for_login(password)
            envelope = parse_vault_secret_for_login(json.dumps({"password": password}))
            self.assertTrue(plain.ok)
            self.assertTrue(envelope.ok)
            self.assertEqual(plain.password, password)
            self.assertEqual(envelope.password, password)

    def test_non_string_secret_is_not_coerced(self) -> None:
        parsed = parse_vault_secret_for_login(123456)  # type: ignore[arg-type]

        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.failure_reason, "vault_secret_password_invalid")

    def test_json_vault_secret_via_get_instagram_credentials(self) -> None:
        payload = json.dumps(
            {
                "password": "secret-only",
                "account_id": ACCOUNT_ID,
                "credentials_version": 1000,
                "created_at": "2026-05-27T00:00:00Z",
            }
        )

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=Mock(return_value=payload),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.password.reveal_for_login_executor(), "secret-only")

    def test_vault_shape_audit_never_returns_secret_value(self) -> None:
        payload = json.dumps(
            {
                "password": "secret-only",
                "account_id": ACCOUNT_ID,
                "credentials_version": 1000,
            }
        )
        audit = vault_secret_shape_audit(payload)
        rendered = json.dumps(audit, sort_keys=True)

        self.assertTrue(audit["vault_secret_is_json"])
        self.assertTrue(audit["vault_secret_has_password_key"])
        self.assertTrue(audit["extracted_password_valid"])
        self.assertTrue(audit["secret_value_safe_for_injection"])
        self.assertNotIn("secret-only", rendered)

    def test_revealed_value_blocked_for_payload_markers(self) -> None:
        payload = (
            '{"password":"[REDACTED]","account_id":"00000000-0000-4000-8000-000000000000",'
            '"credentials_version":1000}'
        )

        self.assertTrue(revealed_value_blocked_for_injection(payload))

    def test_provider_unsupported_rejected_before_lookup(self) -> None:
        lookup = Mock()
        reader = Mock()

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            provider="facebook",
            credentials_lookup=lookup,
            secret_reader=reader,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "unsupported_provider")
        lookup.assert_not_called()
        reader.assert_not_called()

    def test_forbidden_keys_redacted(self) -> None:
        redacted = redact_credentials_payload(
            {
                "safe": "ok",
                "password": "fake-password-for-unit-tests",
                "nested": {"token": "abc", "value": "kept"},
            }
        )

        self.assertEqual(redacted, {"safe": "ok", "nested": {"value": "kept"}})

    def test_raw_password_in_metadata_rejected_before_secret_reader(self) -> None:
        reader = Mock(return_value="fake-password-for-unit-tests")

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials(metadata={"password": "bad"})),
            secret_reader=reader,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "credentials_payload_forbidden")
        reader.assert_not_called()

    def test_json_serialization_helper_never_leaks_password(self) -> None:
        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=Mock(return_value="fake-password-for-unit-tests"),
        )

        rendered = json.dumps(credential_result_safe_dict(result), sort_keys=True)

        self.assertNotIn("fake-password-for-unit-tests", rendered)
        self.assertNotIn(str(result.password.reveal_for_login_executor()), rendered)

    def test_plain_dataclass_serialization_does_not_json_encode_secret(self) -> None:
        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=Mock(return_value="fake-password-for-unit-tests"),
        )

        with self.assertRaises(TypeError):
            json.dumps(asdict(result))

    def test_no_vault_uuid_leak_in_safe_metadata(self) -> None:
        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=Mock(return_value="fake-password-for-unit-tests"),
        )

        rendered = json.dumps(result.safe_metadata, sort_keys=True)

        self.assertNotIn(VAULT_ID, rendered)
        self.assertNotIn(SECRET_REF, rendered)
        self.assertEqual(result.safe_metadata.get("secret_provider"), "supabase_vault")

    def test_no_token_or_service_role_leak(self) -> None:
        redacted = redact_credentials_payload(
            {
                "authorization": "Bearer abc",
                "service_role": "secret",
                "safe": "value",
            }
        )

        self.assertEqual(redacted, {"safe": "value"})

    def test_no_device_id_leak(self) -> None:
        redacted = redact_credentials_payload(
            {
                "adb_serial": "emulator-5554",
                "device_udid": "device-123",
                "account_id": ACCOUNT_ID,
            }
        )

        self.assertEqual(redacted, {"account_id": ACCOUNT_ID})

    def test_lookup_called_once(self) -> None:
        lookup = Mock(return_value=_active_credentials())

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=lookup,
            secret_reader=Mock(return_value="fake-password-for-unit-tests"),
        )

        self.assertTrue(result.ok)
        lookup.assert_called_once_with(ACCOUNT_ID, "instagram")

    def test_secret_reader_called_only_after_metadata_validation_passes(self) -> None:
        valid_reader = Mock(return_value="fake-password-for-unit-tests")
        valid = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=valid_reader,
        )
        invalid_reader = Mock(return_value="fake-password-for-unit-tests")
        invalid = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials(metadata={"token": "bad"})),
            secret_reader=invalid_reader,
        )

        self.assertTrue(valid.ok)
        valid_reader.assert_called_once_with(SECRET_REF)
        self.assertFalse(invalid.ok)
        invalid_reader.assert_not_called()

    def test_contains_forbidden_secret_keys_allows_secret_ref_only_when_requested(self) -> None:
        payload = {"secret_ref": SECRET_REF, "metadata": {"safe": True}}

        self.assertTrue(contains_forbidden_secret_keys(payload))
        self.assertFalse(contains_forbidden_secret_keys(payload, allowed_keys={"secret_ref"}))


if __name__ == "__main__":
    unittest.main()
