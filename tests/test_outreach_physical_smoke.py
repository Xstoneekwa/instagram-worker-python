from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import outreach_physical_smoke as smoke


def _valid_state(**overrides: object) -> dict:
    state = {
        "account_rows_count": 1,
        "username": "j_automatise_pour_toi",
        "account_id": "acct-1",
        "client_status": [
            {
                "login_status": "connected",
                "provisioning_status": "ready",
                "onboarding_status": "ready",
            }
        ],
        "assignment": {
            "assignment_found": True,
            "reason": "assignment_resolved",
            "run_type": "outreach_session",
            "adb_serial": "RFGL145VCKE",
            "package_name": "com.instagram.androif",
        },
        "ig_account_settings": [
            {
                "follow_enabled": True,
                "like_enabled": True,
                "mute_posts_after_follow": True,
                "mute_stories_after_follow": True,
                "welcome_dm_enabled": True,
                "cold_dm_enabled": False,
                "unfollow_enabled": False,
            }
        ],
        "dm_settings": [
            {
                "welcome_enabled": True,
                "outreach_enabled": True,
                "default_outreach_template_id": "tpl-1",
                "outreach_per_session_limit": 1,
                "outreach_per_day_limit": 10,
                "total_dm_per_day_limit": 15,
                "outreach_skip_if_existing_thread": True,
            }
        ],
        "outreach_template": [
            {
                "id": "tpl-1",
                "account_id": "acct-1",
                "template_type": "outreach",
                "active": True,
                "body": "Bonjour, message outreach controle.",
            }
        ],
        "active_runs": [],
        "active_requests": [],
        "active_live_views": [],
        "dm_jobs_reserved_running": [],
        "welcome_jobs": [{"id": "welcome-1", "status": "pending", "dm_type": "welcome"}],
        "outreach_jobs": [
            {
                "id": "job-1",
                "account_id": "acct-1",
                "status": "pending",
                "dm_type": "outreach",
                "recipient_username": "safe_recipient",
                "message_body": "Bonjour, message outreach controle.",
                "source": "manual",
                "template_id": "tpl-1",
                "metadata": {
                    "created_by": "outreach_physical_smoke_test",
                    "source_context": "manual_smoke",
                },
            }
        ],
        "selected_job": None,
        "recent_sent_outreach_jobs": [],
        "counter_today": [
            {
                "counter_date": smoke._today_utc(),
                "outreach_sent_count": 0,
                "total_dm_sent_count": 0,
            }
        ],
        "env": {
            "OUTREACH_DM_REAL_SEND_ENABLED": "false",
            "WELCOME_DM_REAL_SEND_ENABLED": "false",
            "DM_SENDER_REAL_SEND_ENABLED": "false",
            "ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES": "outreach_session",
        },
        "current_ime": {"stdout": "com.android.adbkeyboard/.AdbIME", "exit_code": 0},
        "package_installed": {"stdout": "package:/base.apk", "exit_code": 0},
    }
    state.update(overrides)
    return state


def _reasons(state: dict, *, mode: str = "dry-run", job_id: str = "") -> list[str]:
    ok, reasons, _summary = smoke.validate_outreach_physical_smoke_state(
        state,
        mode=mode,
        job_id=job_id,
    )
    if ok:
        return []
    return reasons


class OutreachPhysicalSmokeAssignmentTests(unittest.TestCase):
    def test_outreach_smoke_assignment_uses_outreach_session_without_schedule_window(self) -> None:
        with patch.object(smoke, "resolve_account_assignment_runtime_context") as mock_resolve:
            mock_resolve.return_value = {
                "assignment_found": True,
                "reason": "assignment_resolved",
                "adb_serial": "RFGL145VCKE",
                "package_name": None,
                "assignment_type": "full_cycle",
            }
            out = smoke.resolve_outreach_smoke_assignment(
                "acct-1",
                ig_account_settings=[{"app_package": "com.instagram.androif"}],
            )

        mock_resolve.assert_called_once_with(
            "acct-1",
            smoke.RUN_TYPE,
            require_assignment=True,
            enforce_window=False,
        )
        self.assertEqual(smoke.RUN_TYPE, "outreach_session")
        self.assertEqual(out["package_name"], "com.instagram.androif")
        self.assertEqual(out["package_name_source"], "ig_account_settings.app_package")

    def test_device_checks_use_resolved_serial_for_package_and_ime(self) -> None:
        calls: list[list[str]] = []

        def _adb(argv: list[str]) -> tuple[int, str, str]:
            calls.append(argv)
            if argv[:2] == ["adb", "devices"]:
                return 0, "List of devices attached\nRFGL145VCKE\tdevice\n", ""
            if "pm" in argv:
                return 0, "package:/base.apk\n", ""
            return 0, "com.android.adbkeyboard/.AdbIME\n", ""

        with patch.object(smoke, "_adb_text", side_effect=_adb):
            out = smoke._collect_device_checks(
                {"adb_serial": "RFGL145VCKE", "package_name": "com.instagram.androif"},
                include_device=True,
            )

        self.assertIn(["adb", "-s", "RFGL145VCKE", "shell", "pm", "path", "com.instagram.androif"], calls)
        self.assertIn("com.android.adbkeyboard/.AdbIME", out["current_ime"]["stdout"])


class OutreachPhysicalSmokeEnvBootstrapTests(unittest.TestCase):
    def test_follow_smoke_env_file_loaded_before_supabase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "SUPABASE_URL=https://example.supabase.co\n"
                "SUPABASE_SERVICE_ROLE_KEY=service-role-key-from-file\n",
                encoding="utf-8",
            )

            def _collect(username: str, **kwargs: object) -> dict:
                self.assertEqual(os.environ.get("SUPABASE_URL"), "https://example.supabase.co")
                self.assertEqual(os.environ.get("SUPABASE_SERVICE_ROLE_KEY"), "service-role-key-from-file")
                return _valid_state()

            with patch.dict(os.environ, {"FOLLOW_SMOKE_ENV_FILE": str(env_path)}, clear=True):
                with patch.object(smoke, "collect_outreach_physical_smoke_state", side_effect=_collect):
                    stdout = StringIO()
                    with patch("sys.stdout", stdout):
                        code = smoke.main(["j_automatise_pour_toi", "--json", "--skip-device-check"])

        self.assertEqual(code, 0)

    def test_missing_env_file_emits_json_stop(self) -> None:
        with patch.dict(
            os.environ,
            {"FOLLOW_SMOKE_ENV_FILE": "/tmp/does-not-exist-outreach-smoke.env"},
            clear=True,
        ):
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                code = smoke.main(["j_automatise_pour_toi", "--json", "--skip-device-check"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 10)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reasons"], ["env_file_not_found"])

    def test_output_does_not_leak_env_secrets(self) -> None:
        sensitive_value = "redacted-test-value"
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "SUPABASE_URL=https://example.supabase.co\n"
                f"SUPABASE_SERVICE_ROLE_KEY={sensitive_value}\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"FOLLOW_SMOKE_ENV_FILE": str(env_path)}, clear=True):
                with patch.object(smoke, "collect_outreach_physical_smoke_state", return_value=_valid_state()):
                    stdout = StringIO()
                    with patch("sys.stdout", stdout):
                        code = smoke.main(["j_automatise_pour_toi", "--json", "--skip-device-check"])

        output = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn(sensitive_value, output)
        self.assertFalse(smoke._output_contains_sensitive_values(output, os.environ))


class OutreachPhysicalSmokePreflightTests(unittest.TestCase):
    def test_single_pending_outreach_job_template_and_caps_ok_allowed(self) -> None:
        ok, reasons, summary = smoke.validate_outreach_physical_smoke_state(
            _valid_state(),
            mode="dry-run",
        )

        self.assertTrue(ok)
        self.assertEqual(reasons, [])
        self.assertEqual(summary["run_type"], "outreach_session")
        self.assertEqual(summary["recipient_username"], "safe_recipient")
        self.assertEqual(summary["max_jobs_effective"], 1)
        self.assertEqual(summary["pending_welcome_jobs_count"], 1)
        self.assertEqual(summary["job_source"], "manual")
        self.assertEqual(summary["job_created_by"], "outreach_physical_smoke_test")
        self.assertEqual(summary["job_created_from"], "manual_smoke")
        self.assertEqual(summary["job_template_id"], "tpl-1")

    def test_dry_run_stops_if_outreach_real_send_enabled(self) -> None:
        state = _valid_state(
            env={
                "OUTREACH_DM_REAL_SEND_ENABLED": "true",
                "WELCOME_DM_REAL_SEND_ENABLED": "false",
                "DM_SENDER_REAL_SEND_ENABLED": "false",
            }
        )

        self.assertIn("outreach_real_send_enabled_during_dry_run", _reasons(state))

    def test_real_send_mode_requires_outreach_flag_but_remains_blocked(self) -> None:
        state = _valid_state(
            env={
                "OUTREACH_DM_REAL_SEND_ENABLED": "true",
                "WELCOME_DM_REAL_SEND_ENABLED": "false",
                "DM_SENDER_REAL_SEND_ENABLED": "false",
            }
        )

        reasons = _reasons(state, mode="real-send")
        self.assertIn("real_send_mode_not_enabled_yet", reasons)
        self.assertNotIn("outreach_real_send_disabled", reasons)

    def test_send_one_mode_requires_job_id_and_remains_blocked(self) -> None:
        state = _valid_state(
            env={
                "OUTREACH_DM_REAL_SEND_ENABLED": "true",
                "WELCOME_DM_REAL_SEND_ENABLED": "false",
                "DM_SENDER_REAL_SEND_ENABLED": "false",
            }
        )

        reasons = _reasons(state, mode="send-one")
        self.assertIn("missing_job_id", reasons)
        self.assertIn("send_one_mode_not_enabled_yet", reasons)

    def test_welcome_and_legacy_real_send_flags_stop(self) -> None:
        state = _valid_state(
            env={
                "OUTREACH_DM_REAL_SEND_ENABLED": "false",
                "WELCOME_DM_REAL_SEND_ENABLED": "true",
                "DM_SENDER_REAL_SEND_ENABLED": "true",
            }
        )
        reasons = _reasons(state)

        self.assertIn("welcome_real_send_enabled", reasons)
        self.assertIn("legacy_dm_real_send_enabled", reasons)

    def test_outreach_disabled_stops(self) -> None:
        state = _valid_state(dm_settings=[{**_valid_state()["dm_settings"][0], "outreach_enabled": False}])
        self.assertIn("outreach_disabled", _reasons(state))

    def test_missing_template_stops(self) -> None:
        state = _valid_state(outreach_template=[])
        reasons = _reasons(state)

        self.assertIn("outreach_template_not_found", reasons)
        self.assertIn("outreach_template_body_empty", reasons)

    def test_template_with_supported_variables_is_renderable(self) -> None:
        state = _valid_state(
            outreach_template=[
                {
                    **_valid_state()["outreach_template"][0],
                    "body": "Salut {name}, c'est {account_username}.",
                }
            ]
        )

        ok, reasons, summary = smoke.validate_outreach_physical_smoke_state(
            state,
            mode="dry-run",
        )

        self.assertTrue(ok)
        self.assertEqual(reasons, [])
        self.assertEqual(summary["template_used_variables"], ["name", "account_username"])
        self.assertEqual(summary["template_fallbacks_used"], ["name:recipient_username"])

    def test_template_unknown_variable_stops(self) -> None:
        state = _valid_state(
            outreach_template=[
                {
                    **_valid_state()["outreach_template"][0],
                    "body": "Salut {company}.",
                }
            ]
        )

        self.assertIn("outreach_template_unknown_variable", _reasons(state))

    def test_pending_job_with_unresolved_supported_token_stops(self) -> None:
        state = _valid_state(
            outreach_jobs=[
                {
                    **_valid_state()["outreach_jobs"][0],
                    "message_body": "Salut {username}.",
                }
            ]
        )
        reasons = _reasons(state)

        self.assertIn("outreach_job_unresolved_template_token", reasons)
        self.assertNotIn("outreach_job_unknown_template_variable", reasons)

    def test_pending_job_with_unknown_token_stops(self) -> None:
        state = _valid_state(
            outreach_jobs=[
                {
                    **_valid_state()["outreach_jobs"][0],
                    "message_body": "Salut {company}.",
                }
            ]
        )
        reasons = _reasons(state)

        self.assertIn("outreach_job_unresolved_template_token", reasons)
        self.assertIn("outreach_job_unknown_template_variable", reasons)

    def test_pending_job_with_unknown_source_stops(self) -> None:
        state = _valid_state(
            outreach_jobs=[
                {
                    **_valid_state()["outreach_jobs"][0],
                    "source": "surprise",
                }
            ]
        )

        self.assertIn("outreach_job_source_not_allowed", _reasons(state))

    def test_pending_job_without_audit_metadata_stops(self) -> None:
        state = _valid_state(
            outreach_jobs=[
                {
                    **_valid_state()["outreach_jobs"][0],
                    "metadata": {},
                }
            ]
        )

        self.assertIn("outreach_job_missing_audit_metadata", _reasons(state))

    def test_reserved_or_running_job_stops(self) -> None:
        state = _valid_state(dm_jobs_reserved_running=[{"id": "job-2", "status": "running", "dm_type": "outreach"}])
        self.assertIn("dm_job_reserved_or_running_exists", _reasons(state))

    def test_no_pending_outreach_job_stops(self) -> None:
        self.assertIn("no_pending_outreach_job", _reasons(_valid_state(outreach_jobs=[])))

    def test_multiple_pending_jobs_stop_when_they_exceed_effective_cap(self) -> None:
        state = _valid_state(
            outreach_jobs=[
                _valid_state()["outreach_jobs"][0],
                {**_valid_state()["outreach_jobs"][0], "id": "job-2", "recipient_username": "other"},
            ]
        )
        self.assertIn("pending_outreach_jobs_exceed_effective_cap", _reasons(state))

    def test_two_pending_jobs_allowed_when_effective_cap_allows_two(self) -> None:
        state = _valid_state(
            dm_settings=[
                {
                    **_valid_state()["dm_settings"][0],
                    "outreach_per_session_limit": 2,
                }
            ],
            outreach_jobs=[
                _valid_state()["outreach_jobs"][0],
                {
                    **_valid_state()["outreach_jobs"][0],
                    "id": "job-2",
                    "recipient_username": "other",
                    "message_body": "Bonjour, autre message outreach controle.",
                    "metadata": {
                        "created_by": "outreach_physical_smoke_test",
                        "source_context": "manual_smoke",
                    },
                },
            ],
        )

        ok, reasons, summary = smoke.validate_outreach_physical_smoke_state(
            state,
            mode="dry-run",
        )

        self.assertTrue(ok)
        self.assertEqual(reasons, [])
        self.assertEqual(summary["pending_outreach_jobs_count"], 2)
        self.assertEqual(summary["pending_outreach_recipients"], ["safe_recipient", "other"])

    def test_pinned_job_allows_multiple_pending_jobs(self) -> None:
        selected = {**_valid_state()["outreach_jobs"][0], "id": "job-2", "recipient_username": "other"}
        state = _valid_state(
            outreach_jobs=[
                _valid_state()["outreach_jobs"][0],
                selected,
            ],
            selected_job=selected,
        )
        ok, reasons, summary = smoke.validate_outreach_physical_smoke_state(
            state,
            mode="dry-run",
            job_id="job-2",
        )

        self.assertTrue(ok)
        self.assertEqual(reasons, [])
        self.assertEqual(summary["job_id"], "job-2")
        self.assertEqual(summary["recipient_username"], "other")

    def test_pinned_non_outreach_job_stops(self) -> None:
        state = _valid_state(selected_job={**_valid_state()["outreach_jobs"][0], "dm_type": "welcome"})
        self.assertIn("outreach_job_wrong_type", _reasons(state, job_id="job-1"))

    def test_pinned_terminal_job_stops(self) -> None:
        state = _valid_state(
            outreach_jobs=[],
            selected_job={**_valid_state()["outreach_jobs"][0], "status": "sent", "sent_at": "2026-06-08T00:00:00Z"},
        )
        reasons = _reasons(state, job_id="job-1")

        self.assertIn("outreach_job_not_pending", reasons)
        self.assertIn("outreach_job_terminal", reasons)

    def test_caps_and_counters_stop_when_exhausted(self) -> None:
        state = _valid_state(
            counter_today=[
                {
                    "counter_date": smoke._today_utc(),
                    "outreach_sent_count": 10,
                    "total_dm_sent_count": 15,
                }
            ]
        )
        reasons = _reasons(state)

        self.assertIn("outreach_day_quota_exhausted", reasons)
        self.assertIn("total_dm_day_quota_exhausted", reasons)
        self.assertIn("outreach_effective_cap_insufficient", reasons)

    def test_missing_counter_stops(self) -> None:
        self.assertIn("dm_counter_today_missing", _reasons(_valid_state(counter_today=[])))

    def test_device_package_and_ime_stop(self) -> None:
        state = _valid_state(
            current_ime={"stdout": "com.android.inputmethod.latin/.LatinIME", "exit_code": 0},
            package_installed={"stdout": "", "stderr": "not found", "exit_code": 1},
        )
        reasons = _reasons(state)

        self.assertIn("adbkeyboard_not_active", reasons)
        self.assertIn("package_not_installed", reasons)

    def test_recent_sent_same_recipient_stops(self) -> None:
        state = _valid_state(
            recent_sent_outreach_jobs=[
                {"id": "old-1", "status": "sent", "dm_type": "outreach", "recipient_username": "safe_recipient"}
            ]
        )

        self.assertIn("outreach_recipient_already_sent", _reasons(state))


if __name__ == "__main__":
    unittest.main()
