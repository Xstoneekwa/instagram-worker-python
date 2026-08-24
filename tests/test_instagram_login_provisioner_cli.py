from __future__ import annotations

import argparse
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import instagram_login_provisioner_cli as cli
from instagram_credentials_runtime_access import SecretValue


ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d"
USERNAME = "cinema_catchup"
FAKE_PASSWORD = "fake-password-for-unit-tests"
SECRET_REF = "supabase_vault://" + "11111111-2222-4333-8444-" + "555555555555"
LOGIN_FORM_XML = '<node text="Username, email or mobile number" /><node text="Password" /><node text="Log in" />'
UNKNOWN_XML = '<node text="Instagram" />'
EMAIL_CODE_XML = (
    '<node text="Check your email" />'
    '<node text="Enter the code we sent to m*******e@hotmail.com" />'
    '<node class="android.widget.EditText" text="Enter code" editable="true" />'
    '<node text="Continue" />'
    '<node text="Try another way" />'
)


class FakeDevice:
    def __init__(self, hierarchy: str = UNKNOWN_XML, *, foreground_package: str | None = None) -> None:
        self.hierarchy = hierarchy
        self.foreground_package = foreground_package
        self.app_start = Mock()
        self.dump_calls = 0

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_calls += 1
        return self.hierarchy

    def app_current(self) -> dict:
        return {"package": self.foreground_package} if self.foreground_package is not None else {}


class ActiveCredentialSelectionContractTest(unittest.TestCase):
    def test_lookup_selects_only_latest_active_revision(self) -> None:
        with patch.object(cli, "_request_json", return_value=[{
            "credentials_version": 2,
            "status": "active",
            "secret_ref": SECRET_REF,
        }]) as request:
            selected = cli._lookup_active_instagram_credentials(ACCOUNT_ID, "instagram")

        self.assertEqual(selected["credentials_version"], 2)
        query = request.call_args.kwargs["query"]
        self.assertEqual(query["status"], "eq.active")
        self.assertEqual(query["order"], "credentials_version.desc")
        self.assertEqual(query["limit"], "1")

    def test_superseded_revision_cannot_be_returned_by_active_lookup(self) -> None:
        with patch.object(cli, "_request_json", return_value=[]):
            selected = cli._lookup_active_instagram_credentials(ACCOUNT_ID, "instagram")

        self.assertIsNone(selected)


def _args(*items: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(
        [
            "--account-id",
            ACCOUNT_ID,
            "--expected-username",
            USERNAME,
            *items,
        ]
    )


def _args_with_log(log_path: str, *items: str) -> argparse.Namespace:
    return _args("--log-jsonl", log_path, *items)


def _fake_result(**overrides):
    base = {
        "ok": False,
        "completed": False,
        "final_outcome": "unknown",
        "final_login_status": "logged_out",
        "reason": "unknown_login_screen",
        "retry_count": 0,
        "actions_taken": [],
        "published": False,
        "publish_reason": "disabled",
        "should_publish_status": False,
        "timings": {"total_ms": 1},
        "warnings": [],
        "safe_metadata": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class InstagramLoginProvisionerCliTest(unittest.TestCase):
    def test_physical_device_requires_explicit_package_name(self) -> None:
        connect = Mock(side_effect=AssertionError("device should not be connected before package preflight"))
        with tempfile.TemporaryDirectory() as tmp:
            code, summary = cli.run_cli_command(
                _args_with_log(f"{tmp}/login.jsonl", "--device-serial", "RFGL145VCKE", "--json"),
                connect_func=connect,
            )

        self.assertEqual(code, 1)
        self.assertEqual(summary["reason"], "package_name_required_for_physical_clone")
        self.assertFalse(summary["submit_executed"])
        self.assertFalse(summary["package_guard_checked"])
        connect.assert_not_called()

    def test_no_submit_does_not_read_vault_when_login_form_is_observed(self) -> None:
        device = FakeDevice(LOGIN_FORM_XML)
        credentials_lookup = Mock(return_value={"unexpected": "should_not_be_used"})
        secret_reader = Mock(side_effect=AssertionError("vault read should not happen"))

        with tempfile.TemporaryDirectory() as tmp:
            code, summary = cli.run_cli_command(
                _args_with_log(f"{tmp}/login.jsonl", "--observe-current-screen-only", "--no-submit", "--json"),
                connect_func=lambda _serial: device,
                credentials_lookup=credentials_lookup,
                secret_reader=secret_reader,
            )

        self.assertEqual(code, 0)
        self.assertEqual(summary["final_outcome"], "dry_run")
        self.assertFalse(summary["submit_executed"])
        credentials_lookup.assert_not_called()
        secret_reader.assert_not_called()

    def test_submit_refuses_unknown_screen_without_loading_credentials(self) -> None:
        device = FakeDevice(UNKNOWN_XML)
        credentials_lookup = Mock(return_value={"username": USERNAME, "password": SecretValue(FAKE_PASSWORD)})
        secret_reader = Mock(side_effect=AssertionError("vault read should not happen"))

        with tempfile.TemporaryDirectory() as tmp:
            code, summary = cli.run_cli_command(
                _args_with_log(f"{tmp}/login.jsonl", "--observe-current-screen-only", "--json"),
                connect_func=lambda _serial: device,
                credentials_lookup=credentials_lookup,
                secret_reader=secret_reader,
            )

        self.assertEqual(code, 1)
        self.assertEqual(summary["final_outcome"], "dry_run")
        self.assertFalse(summary["submit_executed"])
        self.assertEqual(summary["screen_before_submit"], "")
        credentials_lookup.assert_not_called()
        secret_reader.assert_not_called()

    def test_resume_from_action_refuses_login_form_screen_before_consuming_code(self) -> None:
        device = FakeDevice(LOGIN_FORM_XML)
        resume_flow = Mock(side_effect=AssertionError("resume flow should not run"))

        with tempfile.TemporaryDirectory() as tmp:
            code, summary = cli.run_cli_command(
                _args_with_log(
                    f"{tmp}/login.jsonl",
                    "--resume-email-code-from-action",
                    "--verification-action-id",
                    "action-id",
                    "--json",
                ),
                connect_func=lambda _serial: device,
                run_flow_func=resume_flow,
            )

        self.assertEqual(code, 1)
        self.assertEqual(summary["reason"], "resume_email_code_screen_not_active")
        self.assertEqual(summary["screen_type"], "login_form_empty")
        self.assertEqual(summary["router_decision"], "email_code_resume_preflight")
        self.assertFalse(summary["submit_executed"])
        resume_flow.assert_not_called()

    def test_resume_wrong_package_stops_before_code_state_or_consume(self) -> None:
        device = FakeDevice(EMAIL_CODE_XML, foreground_package="com.instagram.android")
        resume_flow = Mock(side_effect=AssertionError("resume flow injection should not run"))

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(cli, "_request_json", side_effect=AssertionError("code state should not be read")),
            patch.object(
                cli,
                "run_email_code_resume_flow",
                return_value=_fake_result(
                    final_outcome="wrong_app_package",
                    reason="resume_email_code_wrong_package",
                    safe_metadata={
                        "package_guard_checked": True,
                        "package_guard_mismatch": True,
                        "expected_package_name": "com.instagram.androie",
                        "actual_foreground_package": "com.instagram.android",
                        "login_package_mismatch_incident": {"published": True},
                        "login_package_mismatch_dashboard_action": {"published": True},
                        "login_package_mismatch_notifications": {"dispatched": True},
                    },
                ),
            ) as package_guard_flow,
        ):
            code, summary = cli.run_cli_command(
                _args_with_log(
                    f"{tmp}/login.jsonl",
                    "--device-serial",
                    "RFGL145VCKE",
                    "--package-name",
                    "com.instagram.androie",
                    "--resume-email-code-from-action",
                    "--verification-action-id",
                    "action-id",
                    "--json",
                ),
                connect_func=lambda _serial: device,
                run_flow_func=resume_flow,
            )

        self.assertEqual(code, 1)
        self.assertEqual(summary["reason"], "resume_email_code_wrong_package")
        self.assertEqual(summary["final_outcome"], "wrong_app_package")
        self.assertTrue(summary["package_guard_mismatch"])
        self.assertEqual(summary["actual_foreground_package"], "com.instagram.android")
        resume_flow.assert_not_called()
        package_guard_flow.assert_called_once()

    def test_resume_from_action_email_challenge_without_submission_returns_code_missing(self) -> None:
        device = FakeDevice(EMAIL_CODE_XML)
        resume_flow = Mock(side_effect=AssertionError("resume flow should not run"))

        def fake_request(_method, table, *, query=None, **_kwargs):
            if table == "account_dashboard_actions":
                return [{"id": "action-id", "status": "pending", "action_type": "enter_email_verification_code"}]
            if table == "account_verification_code_submissions":
                return []
            raise AssertionError(f"unexpected table {table}")

        with tempfile.TemporaryDirectory() as tmp, patch.object(cli, "_request_json", side_effect=fake_request):
            code, summary = cli.run_cli_command(
                _args_with_log(
                    f"{tmp}/login.jsonl",
                    "--resume-email-code-from-action",
                    "--verification-action-id",
                    "action-id",
                    "--json",
                ),
                connect_func=lambda _serial: device,
                run_flow_func=resume_flow,
            )

        self.assertEqual(code, 1)
        self.assertEqual(summary["reason"], "code_missing")
        self.assertEqual(summary["final_outcome"], "verification_pending")
        self.assertEqual(summary["screen_type"], "email_code_challenge")
        self.assertFalse(summary["submit_executed"])
        resume_flow.assert_not_called()

    def test_missing_historical_channel_does_not_default_to_email_on_whatsapp_resume(self) -> None:
        device = FakeDevice(
            '<node text="Check your WhatsApp messages" />'
            '<node text="Enter the code we sent to your WhatsApp account." />'
            '<node class="android.widget.EditText" text="Code" editable="true" />'
            '<node text="Continue" clickable="true" />'
        )
        resume_result = _fake_result(
            ok=True,
            completed=True,
            final_outcome="connected",
            reason="connected",
        )

        def fake_request(_method, table, *, query=None, **_kwargs):
            if table == "account_dashboard_actions":
                return [{
                    "id": "action-id",
                    "status": "code_submitted",
                    "action_type": "enter_email_verification_code",
                    "metadata": {"challenge_type": "unknown"},
                }]
            if table == "account_verification_code_submissions":
                return [{"id": "submission-id", "status": "code_submitted"}]
            raise AssertionError(f"unexpected table {table}")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(cli, "_request_json", side_effect=fake_request),
            patch.object(cli, "run_email_code_resume_flow", return_value=resume_result) as resume_flow,
        ):
            code, summary = cli.run_cli_command(
                _args_with_log(
                    f"{tmp}/login.jsonl",
                    "--resume-email-code-from-action",
                    "--verification-action-id",
                    "action-id",
                    "--json",
                ),
                connect_func=lambda _serial: device,
            )

        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["final_outcome"], "connected")
        resume_flow.assert_called_once()

    def test_verification_channel_normalization_covers_all_supported_channels(self) -> None:
        aliases = {
            "email_code_challenge": "email",
            "sms_code_challenge": "sms",
            "whatsapp_code_challenge": "whatsapp",
            "authenticator_app_code_challenge": "authenticator_app",
        }
        for source, expected in aliases.items():
            with self.subTest(source=source):
                self.assertEqual(cli._normalize_verification_channel(source), expected)
        self.assertEqual(cli._normalize_verification_channel("unknown"), "")

    def test_submit_uses_app_start_by_default(self) -> None:
        device = FakeDevice()
        captured: dict = {}

        def fake_flow(d, **kwargs):
            captured.update(kwargs)
            return _fake_result(
                safe_metadata={
                    "app_start_attempted": True,
                    "app_start_ok": True,
                    "screen_after_app_start": "continue_as_candidate",
                    "screen_after_app_start_initial": "unknown",
                    "screen_after_app_start_final": "continue_as_candidate",
                    "startup_observation_count": 2,
                    "startup_wait_total_ms": 1000,
                    "startup_screens": ["unknown", "continue_as_candidate"],
                    "startup_final_screen_type": "continue_as_candidate",
                    "startup_settling_used": True,
                }
            )

        with tempfile.TemporaryDirectory() as tmp:
            _code, summary = cli.run_cli_command(
                _args_with_log(f"{tmp}/login.jsonl", "--json"),
                connect_func=lambda _serial: device,
                run_flow_func=fake_flow,
            )

        self.assertTrue(captured["start_app_before_probe"])
        self.assertFalse(captured["observe_current_screen_only"])
        self.assertEqual(captured["package_name"], "com.instagram.android")
        self.assertEqual(captured["post_submit_timeout_ms"], 10000)
        self.assertTrue(summary["app_start_attempted"])
        self.assertEqual(summary["screen_after_app_start_initial"], "unknown")
        self.assertEqual(summary["screen_after_app_start_final"], "continue_as_candidate")
        self.assertEqual(summary["startup_observation_count"], 2)
        self.assertEqual(summary["startup_wait_total_ms"], 1000)
        self.assertEqual(summary["startup_screens"], ["unknown", "continue_as_candidate"])
        self.assertTrue(summary["startup_settling_used"])
        self.assertIsNone(captured["previous_account_lifecycle_lookup"])

    def test_operator_smoke_previous_account_override_is_passed_to_flow(self) -> None:
        captured: dict = {}

        def fake_flow(_d, **kwargs):
            captured.update(kwargs)
            return _fake_result(
                safe_metadata={
                    "previous_account_lifecycle": {
                        "username": "old_profile",
                        "lifecycle_status": "canceled",
                        "clone_reuse_allowed": True,
                        "source": "operator_smoke_override",
                    },
                },
                actions_taken=["route:use_another_profile_previous_account_stopped"],
            )

        with tempfile.TemporaryDirectory() as tmp:
            _code, summary = cli.run_cli_command(
                _args_with_log(
                    f"{tmp}/login.jsonl",
                    "--operator-smoke-previous-account-username",
                    "old_profile",
                    "--operator-smoke-lifecycle-status",
                    "canceled",
                    "--operator-smoke-clone-reuse-allowed",
                    "true",
                    "--json",
                ),
                connect_func=lambda _serial: FakeDevice(),
                run_flow_func=fake_flow,
            )

        lookup = captured["previous_account_lifecycle_lookup"]
        self.assertIsNotNone(lookup)
        self.assertEqual(
            lookup("old_profile", {"screen_type": "continue_as_candidate"}),
            {
                "lifecycle_status": "canceled",
                "clone_reuse_allowed": True,
                "source": "operator_smoke_override",
                "reason": "operator_smoke_override",
            },
        )
        self.assertEqual(summary["suggested_username"], "old_profile")
        self.assertEqual(summary["previous_account_lifecycle_source"], "operator_smoke_override")
        self.assertEqual(summary["previous_account_lifecycle_status"], "canceled")
        self.assertTrue(summary["clone_reuse_allowed"])
        self.assertEqual(summary["router_decision"], "use_another_profile_previous_account_stopped")

    def test_operator_smoke_logout_fallback_flag_is_passed_to_flow(self) -> None:
        captured: dict = {}

        def fake_flow(_d, **kwargs):
            captured.update(kwargs)
            return _fake_result(
                safe_metadata={
                    "central_orchestrator_used": True,
                    "central_orchestrator_version": "entry2e5p19-central-v1",
                    "selected_route": "logout_fallback",
                    "selected_route_reason": "operator_smoke_logout_fallback_allowed",
                    "recovery_path": "logout_fallback",
                    "logout_fallback_allowed": True,
                    "logout_fallback_reason": "operator_smoke_logout_fallback_allowed",
                    "screen_after_logout_final": "login_form_empty",
                }
            )

        with tempfile.TemporaryDirectory() as tmp:
            _code, summary = cli.run_cli_command(
                _args_with_log(
                    f"{tmp}/login.jsonl",
                    "--operator-smoke-active-account-username",
                    "old_profile",
                    "--operator-smoke-lifecycle-status",
                    "canceled",
                    "--operator-smoke-clone-reuse-allowed",
                    "true",
                    "--operator-smoke-allow-logout-fallback",
                    "true",
                    "--json",
                ),
                connect_func=lambda _serial: FakeDevice(),
                run_flow_func=fake_flow,
            )

        self.assertTrue(captured["operator_smoke_allow_logout_fallback"])
        self.assertEqual(summary["recovery_path"], "logout_fallback")
        self.assertTrue(summary["logout_fallback_allowed"])
        self.assertEqual(summary["preparation_flow_used"], "logout_fallback_to_login_form_empty")

    def test_logout_fallback_summary_fields_are_safe(self) -> None:
        summary = cli._safe_summary_from_result(
            _fake_result(
                actions_taken=["tap_logout", "tap_not_now", "tap_confirm_logout"],
                safe_metadata={
                    "central_orchestrator_used": True,
                    "central_orchestrator_version": "entry2e5p19-central-v1",
                    "selected_route": "logout_fallback",
                    "selected_route_reason": "operator_smoke_logout_fallback_allowed",
                    "recovery_path": "logout_fallback",
                    "logout_fallback_allowed": True,
                    "logout_fallback_reason": "operator_smoke_logout_fallback_allowed",
                    "add_existing_attempted": False,
                    "add_existing_failed_reason": "operator_smoke_logout_fallback_requested",
                    "profile_menu_opened": True,
                    "settings_opened": True,
                    "logout_button_tapped": True,
                    "save_login_info_prompt_detected": True,
                    "save_login_info_not_now_tapped": True,
                    "logout_confirmation_detected": True,
                    "logout_confirmation_tapped": True,
                    "post_logout_observation_count": 3,
                    "post_logout_screens": [
                        "save_login_info_prompt",
                        "logout_confirmation_prompt",
                        "login_form_empty",
                    ],
                    "screen_after_logout_final": "login_form_empty",
                },
            ),
            args=_args(),
            run_id="run-1",
        )

        self.assertEqual(summary["recovery_path"], "logout_fallback")
        self.assertTrue(summary["central_orchestrator_used"])
        self.assertEqual(summary["central_orchestrator_version"], "entry2e5p19-central-v1")
        self.assertEqual(summary["selected_route"], "logout_fallback")
        self.assertTrue(summary["logout_button_tapped"])
        self.assertTrue(summary["save_login_info_not_now_tapped"])
        self.assertTrue(summary["logout_confirmation_tapped"])
        self.assertEqual(summary["screen_after_logout_final"], "login_form_empty")
        self.assertEqual(summary["screen_before_submit"], "login_form_empty")

    def test_operator_smoke_lifecycle_status_is_normalized_to_lowercase(self) -> None:
        lookup = cli._build_operator_smoke_previous_account_lifecycle_lookup(
            _args(
                "--operator-smoke-previous-account-username",
                "Old_Profile",
                "--operator-smoke-lifecycle-status",
                "canceled",
                "--operator-smoke-clone-reuse-allowed",
                "true",
            )
        )

        self.assertIsNotNone(lookup)
        payload = lookup("old_profile", {"screen_type": "continue_as_candidate"})

        self.assertEqual(payload["lifecycle_status"], "canceled")
        self.assertIs(payload["clone_reuse_allowed"], True)

    def test_operator_smoke_active_account_username_alias_builds_lifecycle_lookup(self) -> None:
        lookup = cli._build_operator_smoke_previous_account_lifecycle_lookup(
            _args(
                "--operator-smoke-active-account-username",
                "Old_Profile",
                "--operator-smoke-lifecycle-status",
                "canceled",
                "--operator-smoke-clone-reuse-allowed",
                "true",
            )
        )

        self.assertIsNotNone(lookup)
        payload = lookup("old_profile", {"screen_type": "active_account_profile"})

        self.assertEqual(payload["lifecycle_status"], "canceled")
        self.assertIs(payload["clone_reuse_allowed"], True)
        self.assertEqual(payload["source"], "operator_smoke_override")

    def test_stale_deleted_unmanaged_account_allows_replacement_lookup(self) -> None:
        queries: list[tuple[str, dict[str, str]]] = []

        def fake_request_json(_method, table, *, query=None, **_kwargs):
            queries.append((table, dict(query or {})))
            if table == "account_assignments" and query.get("account_id") == f"eq.{ACCOUNT_ID}":
                return [{"id": "assignment-1", "status": "active"}]
            if table == "ig_accounts":
                return []
            return []

        with patch.object(cli, "_request_json", side_effect=fake_request_json):
            lookup = cli._build_previous_account_lifecycle_lookup(
                _args("--expected-app-instance-id", "clone-1")
            )
            self.assertIsNotNone(lookup)
            payload = lookup("growth_with_bmb", {"expected_username": USERNAME})

        self.assertTrue(payload["clone_reuse_allowed"])
        self.assertTrue(payload["stale_session_replacement_allowed"])
        self.assertEqual(payload["replacement_safety_status"], "allowed")
        self.assertEqual(payload["stale_account_state"], "deleted")
        self.assertEqual(payload["source"], "stale_replacement_safety_check")
        self.assertEqual(queries[0][0], "account_assignments")

    def test_stale_present_without_active_dependency_allows_replacement_lookup(self) -> None:
        old_account_id = "11111111-2222-4333-8444-555555555555"

        def fake_request_json(_method, table, *, query=None, **_kwargs):
            if table == "account_assignments" and query.get("account_id") == f"eq.{ACCOUNT_ID}":
                return [{"id": "assignment-1", "status": "active"}]
            if table == "ig_accounts":
                return [{"id": old_account_id, "username": "growth_with_bmb"}]
            return []

        with patch.object(cli, "_request_json", side_effect=fake_request_json):
            lookup = cli._build_previous_account_lifecycle_lookup(
                _args("--expected-app-instance-id", "clone-1")
            )
            payload = lookup("growth_with_bmb", {"expected_username": USERNAME})

        self.assertTrue(payload["clone_reuse_allowed"])
        self.assertTrue(payload["stale_session_replacement_allowed"])
        self.assertEqual(payload["stale_account_state"], "unmanaged")
        self.assertEqual(payload["reason"], "stale_account_present_without_active_dependency")

    def test_stale_active_assignment_blocks_replacement_lookup(self) -> None:
        old_account_id = "11111111-2222-4333-8444-555555555555"

        def fake_request_json(_method, table, *, query=None, **_kwargs):
            if table == "account_assignments" and query.get("account_id") == f"eq.{ACCOUNT_ID}":
                return [{"id": "assignment-1", "status": "active"}]
            if table == "ig_accounts":
                return [{"id": old_account_id, "username": "growth_with_bmb"}]
            if table == "account_assignments" and query.get("account_id") == f"eq.{old_account_id}":
                return [{"id": "old-assignment", "status": "active"}]
            return []

        with patch.object(cli, "_request_json", side_effect=fake_request_json):
            lookup = cli._build_previous_account_lifecycle_lookup(
                _args("--expected-app-instance-id", "clone-1")
            )
            payload = lookup("growth_with_bmb", {"expected_username": USERNAME})

        self.assertFalse(payload["clone_reuse_allowed"])
        self.assertFalse(payload["stale_session_replacement_allowed"])
        self.assertEqual(payload["replacement_safety_status"], "blocked")
        self.assertEqual(payload["reason"], "old_account_has_open_assignment")

    def test_stale_active_run_request_blocks_replacement_lookup(self) -> None:
        old_account_id = "11111111-2222-4333-8444-555555555555"

        def fake_request_json(_method, table, *, query=None, **_kwargs):
            if table == "account_assignments" and query.get("account_id") == f"eq.{ACCOUNT_ID}":
                return [{"id": "assignment-1", "status": "active"}]
            if table == "ig_accounts":
                return [{"id": old_account_id, "username": "growth_with_bmb"}]
            if table == "account_run_requests":
                return [{"id": "request-1", "status": "running"}]
            return []

        with patch.object(cli, "_request_json", side_effect=fake_request_json):
            lookup = cli._build_previous_account_lifecycle_lookup(
                _args("--expected-app-instance-id", "clone-1")
            )
            payload = lookup("growth_with_bmb", {"expected_username": USERNAME})

        self.assertFalse(payload["clone_reuse_allowed"])
        self.assertEqual(payload["reason"], "old_account_has_active_run_request")

    def test_preparation_flow_used_maps_use_another_profile_action(self) -> None:
        summary = cli._safe_summary_from_result(
            _fake_result(
                actions_taken=["tap_use_another_profile", "route:start_login_form_flow_replace_username"],
                safe_metadata={"router_decision": "start_login_form_flow_replace_username"},
            ),
            args=_args(),
            run_id="run-1",
        )

        self.assertEqual(summary["preparation_flow_used"], "use_another_profile_for_expected_account")

    def test_preparation_flow_used_maps_add_existing_direct_login_form_empty(self) -> None:
        summary = cli._safe_summary_from_result(
            _fake_result(
                actions_taken=["tap_add_instagram_account", "route:start_login_form_flow"],
                safe_metadata={
                    "recovery_path": "add_existing_account",
                    "screen_after_add_existing_final": "login_form_empty",
                },
            ),
            args=_args(),
            run_id="run-1",
        )

        self.assertEqual(summary["preparation_flow_used"], "add_existing_account_to_login_form_empty")
        self.assertEqual(summary["screen_before_submit"], "login_form_empty")

    def test_operator_smoke_previous_account_override_username_mismatch_blocks_reuse(self) -> None:
        args = _args(
            "--operator-smoke-previous-account-username",
            "old_profile",
            "--operator-smoke-lifecycle-status",
            "canceled",
            "--operator-smoke-clone-reuse-allowed",
            "true",
        )

        lookup = cli._build_operator_smoke_previous_account_lifecycle_lookup(args)

        self.assertIsNotNone(lookup)
        self.assertEqual(
            lookup("different_profile", {"screen_type": "continue_as_candidate"}),
            {
                "lifecycle_status": "unknown",
                "clone_reuse_allowed": False,
                "source": "operator_smoke_override",
                "reason": "operator_smoke_override_username_mismatch",
            },
        )

    def test_no_publish_is_default(self) -> None:
        captured: dict = {}

        def fake_flow(_d, **kwargs):
            captured.update(kwargs)
            return _fake_result()

        with tempfile.TemporaryDirectory() as tmp:
            _code, summary = cli.run_cli_command(
                _args_with_log(f"{tmp}/login.jsonl", "--json"),
                connect_func=lambda _serial: FakeDevice(),
                run_flow_func=fake_flow,
            )

        self.assertFalse(captured["publish_enabled"])
        self.assertIsNone(captured["publisher"])
        self.assertFalse(summary["would_publish"])
        self.assertFalse(summary["published"])

    def test_no_publish_flag_overrides_enabled_env_and_publish_flag(self) -> None:
        captured: dict = {}
        status_publisher = Mock(return_value={"published": True, "reason": "published"})

        def fake_flow(_d, **kwargs):
            captured.update(kwargs)
            return _fake_result(
                ok=True,
                completed=True,
                final_outcome="connected",
                final_login_status="connected",
                should_publish_status=True,
                safe_metadata={
                    "publish_enabled": False,
                    "publish_attempted": False,
                    "publish_result": "skipped",
                },
            )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            cli.os.environ,
            {"LOGIN_PROVISIONER_PUBLISH_ENABLED": "true"},
            clear=False,
        ):
            _code, summary = cli.run_cli_command(
                _args_with_log(f"{tmp}/login.jsonl", "--publish", "--no-publish", "--json"),
                connect_func=lambda _serial: FakeDevice(),
                run_flow_func=fake_flow,
                status_publisher=status_publisher,
            )

        self.assertFalse(captured["publish_enabled"])
        self.assertIsNone(captured["publisher"])
        self.assertFalse(summary["publish_enabled"])
        self.assertFalse(summary["would_publish"])
        self.assertFalse(summary["publish_attempted"])
        self.assertFalse(summary["published"])
        status_publisher.assert_not_called()

    def test_publish_flag_requires_enabled_env(self) -> None:
        captured: dict = {}

        def fake_flow(_d, **kwargs):
            captured.update(kwargs)
            return _fake_result(ok=True, completed=True, final_outcome="connected", final_login_status="connected")

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            cli.os.environ,
            {"LOGIN_PROVISIONER_PUBLISH_ENABLED": "false"},
            clear=False,
        ):
            _code, summary = cli.run_cli_command(
                _args_with_log(f"{tmp}/login.jsonl", "--publish", "--json"),
                connect_func=lambda _serial: FakeDevice(),
                run_flow_func=fake_flow,
            )

        self.assertFalse(captured["publish_enabled"])
        self.assertFalse(summary["publish_enabled"])

    def test_publish_enabled_env_and_flag_injects_safe_publisher(self) -> None:
        captured: dict = {}
        status_publisher = Mock(return_value={"published": True, "reason": "published"})

        def fake_flow(_d, **kwargs):
            captured.update(kwargs)
            kwargs["publisher"](
                account_id=ACCOUNT_ID,
                login_status="connected",
                provisioning_status="ready",
                onboarding_status="ready",
                reauth_required=False,
                reason="login_connected",
                metadata={
                    "central_orchestrator_version": "entry2e5p19-central-v1",
                    "selected_route": "login_form_empty",
                },
            )
            return _fake_result(
                ok=True,
                completed=True,
                final_outcome="connected",
                final_login_status="connected",
                should_publish_status=True,
                published=True,
                publish_reason="published_connected",
                safe_metadata={
                    "publish_enabled": True,
                    "publish_attempted": True,
                    "publish_result": "published",
                    "central_orchestrator_used": True,
                    "central_orchestrator_version": "entry2e5p19-central-v1",
                    "selected_route": "login_form_empty",
                },
            )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            cli.os.environ,
            {"LOGIN_PROVISIONER_PUBLISH_ENABLED": "true"},
            clear=False,
        ):
            _code, summary = cli.run_cli_command(
                _args_with_log(
                    f"{tmp}/login.jsonl",
                    "--publish",
                    "--json",
                    "--run-id",
                    "00000000-0000-4000-8000-000000000001",
                ),
                connect_func=lambda _serial: FakeDevice(),
                run_flow_func=fake_flow,
                status_publisher=status_publisher,
            )

        self.assertTrue(captured["publish_enabled"])
        self.assertIsNotNone(captured["publisher"])
        status_publisher.assert_called_once()
        payload = status_publisher.call_args.kwargs
        self.assertEqual(payload["metadata"]["source"], "login_provisioner")
        self.assertEqual(payload["metadata"]["run_id"], "00000000-0000-4000-8000-000000000001")
        rendered = json.dumps(payload, sort_keys=True)
        for forbidden in (FAKE_PASSWORD, SECRET_REF, "secret_ref", "Vault", "token", "xml", "screenshot"):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue(summary["publish_enabled"])
        self.assertTrue(summary["publish_attempted"])
        self.assertTrue(summary["would_publish"])
        self.assertTrue(summary["published"])

    def test_json_output_is_safe_without_secret_ref_token_or_xml(self) -> None:
        result = _fake_result(
            safe_metadata={
                "app_start_attempted": True,
                "app_start_ok": True,
                "screen_after_app_start": "login_form_empty",
                "screen_type": "login_form_prefilled_username",
                "prefilled_username": "i_m_your_traker",
                "displayed_username": "cinema_catchup",
                "password_only_username": "cinema_catchup",
                "username_match": True,
                "secret_ref": SECRET_REF,
                "token": "Be" + "arer " + "service" + "_role token",
                "xml": LOGIN_FORM_XML,
                "password_result": {
                    "executed": True,
                    "submit_tapped": True,
                    "username_replaced": True,
                    "username_input_confirmed": "true",
                    "username_input_result": "username_input_confirmed",
                    "username_input_ms": 123,
                    "input_method_used": "adb_keyboard_b64",
                    "password_field_non_empty_confirmed": True,
                    "post_submit_observation_count": 3,
                    "post_submit_wait_total_ms": 2250,
                    "post_submit_timeout_ms": 10000,
                    "post_submit_interval_ms": 1000,
                    "post_submit_loading_timeout": False,
                    "post_submit_screens": ["loading", "connected_home"],
                    "final_terminal_screen": "connected_home",
                    "save_password_prompt_detected": True,
                    "save_password_prompt_dismissed": True,
                    "save_password_prompt_dismiss_attempt_count": 1,
                    "dismiss_method": "back",
                    "post_dismiss_screen_type": "connected_home",
                },
                "credentials_error_code": "",
                "credentials_invalid_reason": "",
                "credentials_stage": "",
                "credential_metadata_found": True,
                "credentials_status": "active",
                "credentials_version": 1000,
                "secret_provider": "supabase_vault",
                "username_matches_expected": True,
                "secret_loaded": True,
                "injectable_password_only": True,
                "secret_value_safe_for_injection": True,
                "guard_would_block_revealed_value": False,
            }
        )

        summary = cli._safe_summary_from_result(result, args=_args("--json"), run_id=str(uuid.uuid4()))
        rendered = cli._render_safe_json(summary)

        self.assertNotIn(FAKE_PASSWORD, rendered)
        self.assertNotIn(SECRET_REF, rendered)
        self.assertNotIn("11111111-2222-4333-8444-555555555555", rendered)
        self.assertNotIn("service_role", rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("<node", rendered)
        payload = json.loads(rendered)
        self.assertEqual(payload["screen_type"], "login_form_prefilled_username")
        self.assertEqual(payload["prefilled_username"], "i_m_your_traker")
        self.assertEqual(payload["displayed_username"], "cinema_catchup")
        self.assertEqual(payload["password_only_username"], "cinema_catchup")
        self.assertTrue(payload["username_match"])
        self.assertTrue(payload["username_replaced"])
        self.assertEqual(payload["username_input_confirmed"], "true")
        self.assertEqual(payload["username_input_result"], "username_input_confirmed")
        self.assertEqual(payload["username_input_ms"], 123)
        self.assertEqual(payload["input_method_used"], "adb_keyboard_b64")
        self.assertTrue(payload["password_field_non_empty_confirmed"])
        self.assertEqual(payload["post_submit_observation_count"], 3)
        self.assertEqual(payload["post_submit_wait_total_ms"], 2250)
        self.assertEqual(payload["post_submit_timeout_ms"], 10000)
        self.assertEqual(payload["post_submit_interval_ms"], 1000)
        self.assertFalse(payload["post_submit_loading_timeout"])
        self.assertEqual(payload["post_submit_screens"], ["loading", "connected_home"])
        self.assertEqual(payload["final_terminal_screen"], "connected_home")
        self.assertTrue(payload["save_password_prompt_detected"])
        self.assertTrue(payload["save_password_prompt_dismissed"])
        self.assertEqual(payload["save_password_prompt_dismiss_attempt_count"], 1)
        self.assertEqual(payload["dismiss_method"], "back")
        self.assertEqual(payload["post_dismiss_screen_type"], "connected_home")
        self.assertEqual(payload["credentials_status"], "active")
        self.assertTrue(payload["secret_loaded"])
        self.assertTrue(payload["injectable_password_only"])

    def test_account_picker_metadata_is_exposed_in_safe_summary(self) -> None:
        result = _fake_result(
            ok=True,
            completed=True,
            final_outcome="connected",
            final_login_status="connected",
            reason="login_connected",
            actions_taken=["route:select_expected_account_from_picker", "tap_expected_account"],
            safe_metadata={
                "screen_after_app_start": "account_picker",
                "screen_after_app_start_final": "account_picker",
                "screen_type": "connected",
                "available_usernames": ["cinema_catchup", "i_m_your_traker"],
                "expected_username_present": True,
                "selected_account_username": "cinema_catchup",
                "account_picker_selection_executed": True,
                "account_picker_target_resolution_method": "account_picker_row_container_bounds_center",
                "account_picker_target_row_count": 1,
                "account_picker_target_node_count": 1,
                "account_picker_visible_usernames_count": 2,
                "account_picker_selected_row_index_if_known": 0,
                "account_picker_action_result": "row_tap_executed",
                "post_account_picker_observation_count": 2,
                "post_account_picker_screens": ["transition_unknown", "connected"],
                "screen_after_account_picker_final": "connected",
                "password_result": {"executed": False, "submit_tapped": False},
            },
        )

        summary = cli._safe_summary_from_result(result, args=_args("--json"), run_id="run-1")

        self.assertEqual(summary["screen_type"], "account_picker")
        self.assertEqual(summary["available_usernames"], ["cinema_catchup", "i_m_your_traker"])
        self.assertTrue(summary["expected_username_present"])
        self.assertEqual(summary["selected_account_username"], "cinema_catchup")
        self.assertTrue(summary["account_picker_selection_executed"])
        self.assertEqual(
            summary["account_picker_target_resolution_method"],
            "account_picker_row_container_bounds_center",
        )
        self.assertEqual(summary["account_picker_target_row_count"], 1)
        self.assertEqual(summary["account_picker_visible_usernames_count"], 2)
        self.assertEqual(summary["account_picker_action_result"], "row_tap_executed")
        self.assertEqual(summary["post_account_picker_observation_count"], 2)
        self.assertEqual(summary["post_account_picker_screens"], ["transition_unknown", "connected"])
        self.assertEqual(summary["screen_after_account_picker_final"], "connected")
        self.assertEqual(summary["preparation_flow_used"], "select_expected_account_from_picker")

    def test_cli_generates_run_id_and_writes_safe_jsonl(self) -> None:
        run_id = str(uuid.uuid4())
        captured: dict = {}

        def fake_flow(_d, **kwargs):
            captured.update(kwargs)
            return _fake_result(
                final_outcome="logged_out",
                reason="session_expired",
                safe_metadata={
                    "app_start_attempted": True,
                    "app_start_ok": True,
                    "screen_after_app_start": "continue_as_candidate",
                },
            )

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "login.jsonl"

            code, summary = cli.run_cli_command(
                _args_with_log(str(log_path), "--run-id", run_id, "--post-submit-timeout-ms", "9000", "--json"),
                connect_func=lambda _serial: FakeDevice(),
                run_flow_func=fake_flow,
            )

            self.assertEqual(code, 1)
            self.assertEqual(captured["post_submit_timeout_ms"], 9000)
            self.assertEqual(summary["run_id"], run_id)
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["run_id"], run_id)
            self.assertEqual(payload["final_outcome"], "logged_out")
            self.assertEqual(payload["reason"], "session_expired")
            self.assertEqual(summary["post_submit_timeout_ms"], 0)
            self.assertFalse(payload["would_publish"])
            self.assertNotIn(SECRET_REF, lines[0])
            self.assertNotIn("<node", lines[0])


if __name__ == "__main__":
    unittest.main()
