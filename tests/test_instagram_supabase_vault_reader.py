from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock

from instagram_credentials_runtime_access import (
    SecretValue,
    credential_result_safe_dict,
    get_instagram_credentials_for_login,
    redact_credentials_payload,
)
from instagram_supabase_vault_reader import (
    REDACTED,
    SUPABASE_VAULT_PREFIX,
    SupabaseVaultClient,
    SupabaseVaultReadError,
    build_supabase_vault_secret_reader,
    parse_supabase_vault_secret_ref,
    read_supabase_vault_secret,
)


ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d"
VAULT_ID = "11111111-2222-4333-8444-555555555555"
SECRET_REF = f"supabase_vault://{VAULT_ID}"
FAKE_PASSWORD = "fake-password-for-unit-tests"


def _active_credentials():
    return {
        "account_id": ACCOUNT_ID,
        "provider": "instagram",
        "username": "cinema_catchup",
        "secret_ref": SECRET_REF,
        "credentials_version": 4,
        "status": "active",
        "reauth_required": True,
    }


class InstagramSupabaseVaultReaderTest(unittest.TestCase):
    def test_parse_valid_supabase_vault_ref(self) -> None:
        parsed = parse_supabase_vault_secret_ref(SECRET_REF)

        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.provider, "supabase_vault")
        self.assertEqual(parsed.secret_id, VAULT_ID)
        self.assertEqual(parsed.reason, "valid_secret_ref")

    def test_reject_empty_secret_ref(self) -> None:
        parsed = parse_supabase_vault_secret_ref("")

        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.reason, "invalid_secret_ref")

    def test_reject_unsupported_provider(self) -> None:
        parsed = parse_supabase_vault_secret_ref(f"other://{VAULT_ID}")

        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.provider, "other")
        self.assertEqual(parsed.reason, "unsupported_secret_ref_provider")

    def test_reject_invalid_uuid(self) -> None:
        parsed = parse_supabase_vault_secret_ref("supabase_vault://not-a-uuid")

        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.reason, "vault_secret_id_invalid")
        self.assertEqual(parsed.secret_id, "")

    def test_safe_ref_label_is_redacted(self) -> None:
        parsed = parse_supabase_vault_secret_ref(SECRET_REF)

        self.assertEqual(parsed.safe_ref_label, f"{SUPABASE_VAULT_PREFIX}{REDACTED}")
        self.assertNotIn(VAULT_ID, parsed.safe_ref_label)

    def test_read_secret_success_returns_secret_value(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(return_value=FAKE_PASSWORD))

        secret = read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertIsInstance(secret, SecretValue)

    def test_secret_value_str_and_repr_remain_redacted(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(return_value=FAKE_PASSWORD))

        secret = read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertEqual(str(secret), REDACTED)
        self.assertEqual(repr(secret), REDACTED)

    def test_secret_value_reveal_only_explicit(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(return_value=FAKE_PASSWORD))

        secret = read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertEqual(secret.reveal_for_login_executor(), FAKE_PASSWORD)

    def test_vault_client_called_with_uuid_only_after_validation(self) -> None:
        rpc = Mock(return_value=FAKE_PASSWORD)
        client = SupabaseVaultClient(rpc_caller=rpc)

        read_supabase_vault_secret(SECRET_REF, vault_client=client)

        rpc.assert_called_once_with(
            "read_instagram_credentials_vault_secret",
            {"p_secret_id": VAULT_ID},
        )

    def test_vault_client_not_called_on_invalid_ref(self) -> None:
        rpc = Mock(return_value=FAKE_PASSWORD)
        client = SupabaseVaultClient(rpc_caller=rpc)

        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret("supabase_vault://bad", vault_client=client)

        self.assertEqual(ctx.exception.failure_reason, "vault_secret_id_invalid")
        rpc.assert_not_called()

    def test_vault_client_exception_maps_safe_failure(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(side_effect=RuntimeError("service_role token leaked")))

        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertEqual(ctx.exception.failure_reason, "vault_read_failed")
        rendered = json.dumps(ctx.exception.safe_dict(), sort_keys=True)
        self.assertNotIn("service_role token leaked", rendered)

    def test_timeout_error_maps_safe_failure(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(side_effect=TimeoutError("raw timeout")))

        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertEqual(ctx.exception.failure_reason, "vault_timeout")

    def test_empty_secret_rejected(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(return_value=""))

        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertEqual(ctx.exception.failure_reason, "vault_secret_empty")

    def test_non_string_secret_rejected(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(return_value={"unexpected": "shape"}))

        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertEqual(ctx.exception.failure_reason, "vault_secret_not_string")

    def test_no_password_in_safe_error(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(side_effect=RuntimeError(FAKE_PASSWORD)))

        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertNotIn(FAKE_PASSWORD, json.dumps(ctx.exception.safe_dict(), sort_keys=True))

    def test_no_full_secret_ref_in_safe_error(self) -> None:
        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret("bad-ref", vault_client=SupabaseVaultClient(rpc_caller=Mock()))

        rendered = json.dumps(ctx.exception.safe_dict(), sort_keys=True)
        self.assertNotIn("bad-ref", rendered)
        self.assertNotIn(SECRET_REF, rendered)

    def test_no_vault_uuid_leak(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(side_effect=RuntimeError(VAULT_ID)))

        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret(SECRET_REF, vault_client=client)

        rendered = json.dumps(ctx.exception.safe_dict(), sort_keys=True)
        self.assertNotIn(VAULT_ID, rendered)

    def test_no_authorization_or_service_role_leak(self) -> None:
        client = SupabaseVaultClient(
            rpc_caller=Mock(side_effect=RuntimeError("Authorization: Bearer service_role_key"))
        )

        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret(SECRET_REF, vault_client=client)

        rendered = json.dumps(ctx.exception.safe_dict(), sort_keys=True)
        self.assertNotIn("Authorization", rendered)
        self.assertNotIn("service_role", rendered)

    def test_integrates_with_get_instagram_credentials_for_login(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(return_value=FAKE_PASSWORD))
        reader = build_supabase_vault_secret_reader(vault_client=client)

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=reader,
        )

        self.assertTrue(result.ok)
        self.assertIsInstance(result.password, SecretValue)
        self.assertEqual(result.password.reveal_for_login_executor(), FAKE_PASSWORD)

    def test_credential_result_safe_dict_no_leak_with_reader(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(return_value=FAKE_PASSWORD))
        reader = build_supabase_vault_secret_reader(vault_client=client)

        result = get_instagram_credentials_for_login(
            account_id=ACCOUNT_ID,
            credentials_lookup=Mock(return_value=_active_credentials()),
            secret_reader=reader,
        )
        rendered = json.dumps(credential_result_safe_dict(result), sort_keys=True)

        self.assertNotIn(FAKE_PASSWORD, rendered)
        self.assertNotIn(SECRET_REF, rendered)
        self.assertNotIn(VAULT_ID, rendered)
        self.assertNotIn("supabase_vault", rendered)

    def test_reader_does_not_print_or_log_raw_secret(self) -> None:
        client = SupabaseVaultClient(rpc_caller=Mock(return_value=FAKE_PASSWORD))
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            read_supabase_vault_secret(SECRET_REF, vault_client=client)

        self.assertNotIn(FAKE_PASSWORD, stdout.getvalue())
        self.assertNotIn(FAKE_PASSWORD, stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_forbidden_keys_redacted(self) -> None:
        redacted = redact_credentials_payload(
            {
                "password": FAKE_PASSWORD,
                "secret_ref": SECRET_REF,
                "authorization": "Bearer token",
                "safe": "ok",
            }
        )

        self.assertEqual(redacted, {"safe": "ok"})

    def test_default_transport_is_fail_closed_when_not_configured(self) -> None:
        with self.assertRaises(SupabaseVaultReadError) as ctx:
            read_supabase_vault_secret(SECRET_REF)

        self.assertEqual(ctx.exception.failure_reason, "vault_transport_not_configured")


if __name__ == "__main__":
    unittest.main()
