from __future__ import annotations

import io
import json
import socket
import unittest
from contextlib import ExitStack
from urllib import error
from unittest.mock import patch

import instagram_account_status_publisher as publisher


ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d"
TOKEN = "internal-token-not-real"
API_URL = "https://example.supabase.co/functions/v1/instagram-account-status"


class FakeResponse:
    def __init__(self, body: dict, status: int = 200) -> None:
        self.body = json.dumps(body).encode("utf-8")
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class InstagramAccountStatusPublisherTest(unittest.TestCase):
    def _enabled_config(self, *, fail_open: bool = True) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            patch.object(publisher.config, "INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED", True, create=True)
        )
        stack.enter_context(patch.object(publisher.config, "INSTAGRAM_ACCOUNT_STATUS_API_URL", API_URL, create=True))
        stack.enter_context(
            patch.object(
                publisher.config,
                "INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN",
                TOKEN,
                create=True,
            )
        )
        stack.enter_context(patch.object(publisher.config, "INSTAGRAM_ACCOUNT_STATUS_FAIL_OPEN", fail_open, create=True))
        stack.enter_context(
            patch.object(
                publisher.config,
                "INSTAGRAM_ACCOUNT_STATUS_TIMEOUT_SECONDS",
                1.0,
                create=True,
            )
        )
        return stack

    def test_flag_off_returns_disabled_without_http(self) -> None:
        with (
            patch.object(publisher.config, "INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED", False, create=True),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="connected",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "disabled")
        urlopen.assert_not_called()

    def test_missing_url_or_token_returns_not_configured_without_http(self) -> None:
        with (
            patch.object(publisher.config, "INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED", True, create=True),
            patch.object(publisher.config, "INSTAGRAM_ACCOUNT_STATUS_API_URL", "", create=True),
            patch.object(publisher.config, "INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN", "", create=True),
            patch.object(publisher.config, "INSTAGRAM_ACCOUNT_STATUS_FAIL_OPEN", True, create=True),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="connected",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "not_configured")
        urlopen.assert_not_called()

    def test_success_post_has_authorization_header_and_safe_payload(self) -> None:
        calls = []

        def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            calls.append((req, timeout))
            return FakeResponse({"ok": True, "account_id": ACCOUNT_ID})

        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen", side_effect=fake_urlopen),
            patch.object(publisher, "log") as log,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="connected",
                reauth_required=False,
                reason="login_connected",
                metadata={"stage": "login_check"},
            )

        self.assertTrue(out["published"])
        req, timeout = calls[0]
        self.assertEqual(timeout, 1.0)
        self.assertEqual(req.get_header("Authorization"), f"Bearer {TOKEN}")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["action"], "update_status")
        self.assertEqual(body["account_id"], ACCOUNT_ID)
        self.assertEqual(body["login_status"], "connected")
        self.assertFalse(body["reauth_required"])
        self.assertEqual(body["metadata"]["source"], "python_status_publisher")
        log_payload = json.dumps(log.call_args.kwargs)
        self.assertNotIn(TOKEN, log_payload)
        self.assertNotIn("Authorization", log_payload)

    def test_payload_omits_none_fields(self) -> None:
        calls = []

        def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            calls.append(req)
            return FakeResponse({"ok": True})

        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen", side_effect=fake_urlopen),
        ):
            publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="needs_2fa",
                provisioning_status=None,
                onboarding_status=None,
                reauth_required=None,
            )
        body = json.loads(calls[0].data.decode("utf-8"))
        self.assertEqual(body["login_status"], "needs_2fa")
        self.assertNotIn("provisioning_status", body)
        self.assertNotIn("onboarding_status", body)
        self.assertNotIn("reauth_required", body)

    def test_metadata_source_caller_is_preserved(self) -> None:
        calls = []

        def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            calls.append(req)
            return FakeResponse({"ok": True})

        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen", side_effect=fake_urlopen),
        ):
            publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="checkpoint",
                metadata={"source": "worker", "stage": "login_check"},
            )
        body = json.loads(calls[0].data.decode("utf-8"))
        self.assertEqual(body["metadata"]["source"], "worker")
        self.assertEqual(body["metadata"]["stage"], "login_check")

    def test_forbidden_metadata_password_rejected_without_http(self) -> None:
        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="failed",
                metadata={"password": "secret"},
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "forbidden_metadata")
        urlopen.assert_not_called()

    def test_forbidden_metadata_secret_ref_rejected_without_http(self) -> None:
        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="failed",
                metadata={"nested": {"secret_ref": "supabase_vault://x"}},
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "forbidden_metadata")
        urlopen.assert_not_called()

    def test_metadata_non_dict_rejected_without_http(self) -> None:
        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="failed",
                metadata=["not", "object"],  # type: ignore[arg-type]
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "metadata_must_be_object")
        urlopen.assert_not_called()

    def test_reason_too_long_rejected_without_http(self) -> None:
        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="failed",
                reason="x" * 501,
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "reason_too_long")
        urlopen.assert_not_called()

    def test_external_request_id_invalid_rejected_without_http(self) -> None:
        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="failed",
                external_request_id="bad id with spaces",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "external_request_id_invalid")
        urlopen.assert_not_called()

    def test_http_200_parse_json_returns_published_true(self) -> None:
        with (
            self._enabled_config(),
            patch.object(
                publisher.request,
                "urlopen",
                return_value=FakeResponse({"ok": True, "actions_upserted": []}),
            ),
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="needs_2fa",
            )
        self.assertTrue(out["published"])
        self.assertEqual(out["status_code"], 200)
        self.assertTrue(out["response"]["ok"])

    def test_http_400_fail_open_returns_status_code(self) -> None:
        http_error = error.HTTPError(API_URL, 400, "Bad Request", {}, io.BytesIO(b'{"error":"bad"}'))
        with (
            self._enabled_config(fail_open=True),
            patch.object(publisher.request, "urlopen", side_effect=http_error),
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="bad_status",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "http_error")
        self.assertEqual(out["status_code"], 400)
        self.assertNotIn(TOKEN, json.dumps(out))

    def test_timeout_fail_open_returns_timeout(self) -> None:
        with (
            self._enabled_config(fail_open=True),
            patch.object(publisher.request, "urlopen", side_effect=socket.timeout("timed out")),
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                login_status="connected",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "timeout")

    def test_reauth_required_only_is_valid_status_payload(self) -> None:
        calls = []

        def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            calls.append(req)
            return FakeResponse({"ok": True})

        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen", side_effect=fake_urlopen),
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                reauth_required=True,
                reauth_reason="credentials_invalid",
            )
        self.assertTrue(out["published"])
        body = json.loads(calls[0].data.decode("utf-8"))
        self.assertTrue(body["reauth_required"])
        self.assertEqual(body["reauth_reason"], "credentials_invalid")

    def test_fail_open_false_http_error_raises_controlled_error(self) -> None:
        http_error = error.HTTPError(API_URL, 500, "Server Error", {}, io.BytesIO(b"{}"))
        with (
            self._enabled_config(fail_open=False),
            patch.object(publisher.request, "urlopen", side_effect=http_error),
        ):
            with self.assertRaises(publisher.InstagramAccountStatusPublishError) as ctx:
                publisher.publish_instagram_account_status(
                    ACCOUNT_ID,
                    login_status="connected",
                )
        text = str(ctx.exception)
        self.assertIn("http_error", text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("Authorization", text)

    def test_invalid_uuid_rejected(self) -> None:
        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                "not-a-uuid",
                login_status="connected",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "account_id_invalid")
        urlopen.assert_not_called()

    def test_no_status_fields_rejected_without_http(self) -> None:
        with (
            self._enabled_config(),
            patch.object(publisher.request, "urlopen") as urlopen,
        ):
            out = publisher.publish_instagram_account_status(
                ACCOUNT_ID,
                metadata={"source": "worker"},
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "no_status_fields")
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
