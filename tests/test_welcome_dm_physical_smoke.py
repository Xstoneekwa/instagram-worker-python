from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import welcome_dm_physical_smoke as smoke


def _valid_state(**overrides: object) -> dict:
    state = {
        "account_rows_count": 1,
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
            "adb_serial": "RFGL145VCKE",
            "package_name": "com.instagram.androif",
        },
        "ig_account_settings": [
            {
                "follow_enabled": False,
                "like_enabled": False,
                "mute_posts_after_follow": False,
                "mute_stories_after_follow": False,
                "welcome_dm_enabled": True,
                "cold_dm_enabled": False,
                "unfollow_enabled": False,
            }
        ],
        "dm_settings": [
            {
                "welcome_enabled": True,
                "outreach_enabled": False,
                "welcome_per_session_limit": 1,
                "welcome_template_id": "tpl-1",
                "welcome_baseline_completed_at": "2026-06-07T00:00:00Z",
            }
        ],
        "welcome_template": [
            {
                "id": "tpl-1",
                "account_id": "acct-1",
                "template_type": "welcome",
                "active": True,
                "body": "Bonjour et merci pour le follow.",
            }
        ],
        "active_runs": [],
        "active_requests": [],
        "active_live_views": [],
        "outreach_jobs": [],
        "welcome_jobs": [
            {
                "id": "job-1",
                "status": "pending",
                "dm_type": "welcome",
                "recipient_username": "safe_recipient",
                "message_body": "Bonjour et merci pour le follow.",
                "source": "manual",
            }
        ],
        "env": {
            "WELCOME_DM_REAL_SEND_ENABLED": "false",
            "OUTREACH_DM_REAL_SEND_ENABLED": "false",
            "DM_SENDER_REAL_SEND_ENABLED": "false",
        },
        "current_ime": {"stdout": "com.android.adbkeyboard/.AdbIME", "exit_code": 0},
        "package_installed": {"stdout": "package:/base.apk", "exit_code": 0},
    }
    state.update(overrides)
    return state


def _reasons(state: dict, *, mode: str = "dry-run") -> list[str]:
    ok, reasons, _summary = smoke.validate_welcome_physical_smoke_state(state, mode=mode)
    if ok:
        return []
    return reasons


class WelcomeDmPhysicalSmokeAssignmentTests(unittest.TestCase):
    def test_welcome_smoke_assignment_uses_full_cycle_without_schedule_window(self) -> None:
        with patch.object(smoke, "resolve_account_assignment_runtime_context") as mock_resolve:
            mock_resolve.return_value = {
                "assignment_found": True,
                "reason": "assignment_resolved",
                "adb_serial": "RFGL145VCKE",
                "package_name": None,
                "assignment_type": "full_cycle",
            }
            out = smoke.resolve_welcome_smoke_assignment(
                "acct-1",
                ig_account_settings=[{"app_package": "com.instagram.androif"}],
            )

        mock_resolve.assert_called_once_with(
            "acct-1",
            smoke.RUN_TYPE,
            require_assignment=True,
            enforce_window=False,
        )
        self.assertEqual(out["adb_serial"], "RFGL145VCKE")
        self.assertEqual(out["package_name"], "com.instagram.androif")
        self.assertEqual(out["package_name_source"], "ig_account_settings.app_package")

    def test_welcome_smoke_assignment_prefers_assignment_package(self) -> None:
        with patch.object(smoke, "resolve_account_assignment_runtime_context") as mock_resolve:
            mock_resolve.return_value = {
                "assignment_found": True,
                "reason": "assignment_resolved",
                "adb_serial": "RFGL145VCKE",
                "package_name": "com.instagram.androie",
            }
            out = smoke.resolve_welcome_smoke_assignment(
                "acct-1",
                ig_account_settings=[{"app_package": "com.instagram.androif"}],
            )

        self.assertEqual(out["package_name"], "com.instagram.androie")
        self.assertEqual(out["package_name_source"], "assignment.app_instance")

    def test_device_checks_skip_adb_shell_when_serial_missing(self) -> None:
        with patch.object(smoke, "_adb_text", return_value=(0, "List of devices attached\n", "")) as mock_adb:
            out = smoke._collect_device_checks(
                {"adb_serial": "", "package_name": "com.instagram.androif"},
                include_device=True,
            )

        mock_adb.assert_called_once_with(["adb", "devices"])
        self.assertNotIn("current_ime", out)
        self.assertNotIn("package_installed", out)

    def test_device_checks_use_resolved_serial_for_ime(self) -> None:
        calls: list[list[str]] = []

        def _adb(argv: list[str]) -> tuple[int, str, str]:
            calls.append(argv)
            if argv[:2] == ["adb", "devices"]:
                return 0, "List of devices attached\nRFGL145VCKE\tdevice\n", ""
            return 0, "com.android.adbkeyboard/.AdbIME\n", ""

        with patch.object(smoke, "_adb_text", side_effect=_adb):
            out = smoke._collect_device_checks(
                {
                    "adb_serial": "RFGL145VCKE",
                    "package_name": "com.instagram.androif",
                },
                include_device=True,
            )

        self.assertIn(
            [
                "adb",
                "-s",
                "RFGL145VCKE",
                "shell",
                "settings",
                "get",
                "secure",
                "default_input_method",
            ],
            calls,
        )
        self.assertIn("com.android.adbkeyboard/.AdbIME", out["current_ime"]["stdout"])

    def test_missing_assignment_serial_stops_cleanly(self) -> None:
        reasons = _reasons(
            _valid_state(
                assignment={
                    "assignment_found": False,
                    "reason": "assignment_device_missing_adb_serial",
                    "adb_serial": "",
                    "package_name": "",
                },
                current_ime=None,
            )
        )

        self.assertIn("assignment_not_resolved", reasons)
        self.assertIn("assignment_device_missing", reasons)
        self.assertIn("assignment_package_missing", reasons)
        self.assertNotIn("adbkeyboard_not_active", reasons)

    def test_welcome_smoke_does_not_launch_account_session(self) -> None:
        with patch.object(smoke, "resolve_account_assignment_runtime_context") as mock_resolve:
            mock_resolve.return_value = {
                "assignment_found": True,
                "reason": "assignment_resolved",
                "adb_serial": "RFGL145VCKE",
                "package_name": "com.instagram.androif",
            }
            smoke.resolve_welcome_smoke_assignment("acct-1", ig_account_settings=[])

        mock_resolve.assert_called_once_with(
            "acct-1",
            smoke.RUN_TYPE,
            require_assignment=True,
            enforce_window=False,
        )
        self.assertEqual(smoke.RUN_TYPE, "dm_welcome_session_send")


class WelcomeDmPhysicalSmokeEnvBootstrapTests(unittest.TestCase):
    def test_follow_smoke_env_file_loaded_before_supabase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "SUPABASE_URL=https://example.supabase.co\n"
                "SUPABASE_SERVICE_ROLE_KEY=service-role-key-from-file\n",
                encoding="utf-8",
            )

            def _collect(username: str, **kwargs: object) -> dict:
                self.assertEqual(
                    os.environ.get("SUPABASE_URL"),
                    "https://example.supabase.co",
                )
                self.assertEqual(
                    os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
                    "service-role-key-from-file",
                )
                return _valid_state()

            with patch.dict(os.environ, {"FOLLOW_SMOKE_ENV_FILE": str(env_path)}, clear=True):
                with patch.object(smoke, "collect_welcome_physical_smoke_state", side_effect=_collect):
                    stdout = StringIO()
                    with patch("sys.stdout", stdout):
                        code = smoke.main(["j_automatise_pour_toi", "--json", "--skip-device-check"])

            self.assertEqual(code, 0)

    def test_missing_env_file_emits_json_stop(self) -> None:
        with patch.dict(
            os.environ,
            {"FOLLOW_SMOKE_ENV_FILE": "/tmp/does-not-exist-welcome-smoke.env"},
            clear=True,
        ):
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                code = smoke.main(["j_automatise_pour_toi", "--json", "--skip-device-check"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 10)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reasons"], ["env_file_not_found"])

    def test_missing_supabase_url_emits_json_stop(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                code = smoke.main(["j_automatise_pour_toi", "--json", "--skip-device-check"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 10)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reasons"], ["missing_required_env"])
        self.assertEqual(
            payload["missing"],
            ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"],
        )

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
                with patch.object(
                    smoke,
                    "collect_welcome_physical_smoke_state",
                    return_value=_valid_state(),
                ):
                    stdout = StringIO()
                    with patch("sys.stdout", stdout):
                        code = smoke.main(["j_automatise_pour_toi", "--json", "--skip-device-check"])

        output = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn(sensitive_value, output)
        self.assertFalse(smoke._output_contains_sensitive_values(output, os.environ))


class WelcomeDmPhysicalSmokePreflightTests(unittest.TestCase):
    def test_exactly_one_pending_job_template_and_gates_ok_allowed(self) -> None:
        ok, reasons, summary = smoke.validate_welcome_physical_smoke_state(
            _valid_state(),
            mode="dry-run",
        )

        self.assertTrue(ok)
        self.assertEqual(reasons, [])
        self.assertEqual(summary["run_type"], "dm_welcome_session_send")
        self.assertEqual(summary["recipient_username"], "safe_recipient")
        self.assertEqual(summary["template_body"], "Bonjour et merci pour le follow.")

    def test_real_send_mode_requires_welcome_real_send_only(self) -> None:
        state = _valid_state(
            env={
                "WELCOME_DM_REAL_SEND_ENABLED": "true",
                "OUTREACH_DM_REAL_SEND_ENABLED": "false",
                "DM_SENDER_REAL_SEND_ENABLED": "false",
            }
        )
        ok, reasons, _summary = smoke.validate_welcome_physical_smoke_state(
            state,
            mode="real-send",
        )

        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_follow_on_stops(self) -> None:
        state = _valid_state(
            ig_account_settings=[{**_valid_state()["ig_account_settings"][0], "follow_enabled": True}]
        )
        self.assertIn("follow_enabled", _reasons(state))

    def test_outreach_on_stops(self) -> None:
        state = _valid_state(
            ig_account_settings=[{**_valid_state()["ig_account_settings"][0], "cold_dm_enabled": True}]
        )
        self.assertIn("outreach_enabled", _reasons(state))

    def test_unfollow_on_stops(self) -> None:
        state = _valid_state(
            ig_account_settings=[{**_valid_state()["ig_account_settings"][0], "unfollow_enabled": True}]
        )
        self.assertIn("unfollow_enabled", _reasons(state))

    def test_welcome_per_session_limit_above_one_stops(self) -> None:
        state = _valid_state(
            dm_settings=[{**_valid_state()["dm_settings"][0], "welcome_per_session_limit": 2}]
        )
        self.assertIn("welcome_per_session_limit_not_1", _reasons(state))

    def test_missing_template_stops(self) -> None:
        state = _valid_state(
            dm_settings=[{**_valid_state()["dm_settings"][0], "welcome_template_id": ""}],
            welcome_template=[],
        )
        reasons = _reasons(state)
        self.assertIn("welcome_template_missing", reasons)
        self.assertIn("welcome_template_not_found", reasons)

    def test_missing_baseline_allowed_for_strict_manual_smoke(self) -> None:
        state = _valid_state(
            dm_settings=[{**_valid_state()["dm_settings"][0], "welcome_baseline_completed_at": None}]
        )
        self.assertNotIn("welcome_baseline_not_completed", _reasons(state))

    def test_missing_baseline_stops_for_scan_job(self) -> None:
        state = _valid_state(
            dm_settings=[{**_valid_state()["dm_settings"][0], "welcome_baseline_completed_at": None}],
            welcome_jobs=[{**_valid_state()["welcome_jobs"][0], "source": "welcome_scan"}],
        )
        self.assertIn("welcome_baseline_not_completed", _reasons(state))

    def test_multiple_pending_jobs_stop(self) -> None:
        state = _valid_state(
            welcome_jobs=[
                _valid_state()["welcome_jobs"][0],
                {**_valid_state()["welcome_jobs"][0], "id": "job-2", "recipient_username": "other"},
            ]
        )
        self.assertIn("multiple_pending_welcome_jobs", _reasons(state))

    def test_no_pending_job_stops(self) -> None:
        self.assertIn("no_pending_welcome_job", _reasons(_valid_state(welcome_jobs=[])))

    def test_outreach_real_send_false_is_required(self) -> None:
        state = _valid_state(
            env={
                "WELCOME_DM_REAL_SEND_ENABLED": "false",
                "OUTREACH_DM_REAL_SEND_ENABLED": "true",
                "DM_SENDER_REAL_SEND_ENABLED": "false",
            }
        )
        self.assertIn("outreach_real_send_enabled", _reasons(state))

    def test_legacy_real_send_flag_stops_as_ambiguous(self) -> None:
        state = _valid_state(
            env={
                "WELCOME_DM_REAL_SEND_ENABLED": "false",
                "OUTREACH_DM_REAL_SEND_ENABLED": "false",
                "DM_SENDER_REAL_SEND_ENABLED": "true",
            }
        )
        self.assertIn("legacy_dm_real_send_enabled", _reasons(state))


class WelcomeDmPhysicalSmokeSendOneTests(unittest.TestCase):
    def _send_one_reasons(self, state: dict, job_id: str = "job-1") -> list[str]:
        ok, reasons, _summary, _job = smoke.validate_send_one_smoke_state(
            state,
            job_id=job_id,
        )
        if ok:
            return []
        return reasons

    def test_send_one_refuses_missing_job_id(self) -> None:
        self.assertIn("missing_job_id", self._send_one_reasons(_valid_state(), job_id=""))

    def test_send_one_refuses_non_pending_job(self) -> None:
        state = _valid_state(welcome_jobs=[{**_valid_state()["welcome_jobs"][0], "status": "sent"}])
        self.assertIn("welcome_job_not_pending", self._send_one_reasons(state))

    def test_send_one_refuses_non_welcome_job(self) -> None:
        state = _valid_state(welcome_jobs=[{**_valid_state()["welcome_jobs"][0], "dm_type": "outreach"}])
        self.assertIn("welcome_job_wrong_type", self._send_one_reasons(state))

    def test_send_one_refuses_recipient_mismatch(self) -> None:
        state = _valid_state(welcome_jobs=[{**_valid_state()["welcome_jobs"][0], "id": "other-job"}])
        self.assertIn("job_id_mismatch", self._send_one_reasons(state, job_id="job-1"))

    def test_send_one_refuses_multiple_pending_welcome_jobs(self) -> None:
        state = _valid_state(
            welcome_jobs=[
                _valid_state()["welcome_jobs"][0],
                {**_valid_state()["welcome_jobs"][0], "id": "job-2", "recipient_username": "other"},
            ]
        )
        self.assertIn("multiple_pending_welcome_jobs", self._send_one_reasons(state))

    def test_send_one_refuses_outreach_job_pending(self) -> None:
        state = _valid_state(outreach_jobs=[{"id": "outreach-1", "status": "pending"}])
        self.assertIn("active_outreach_job_exists", self._send_one_reasons(state))

    def test_send_one_refuses_flow_flags_on(self) -> None:
        settings = _valid_state()["ig_account_settings"][0]
        for key, reason in (
            ("follow_enabled", "follow_enabled"),
            ("like_enabled", "like_enabled"),
            ("mute_posts_after_follow", "mute_posts_enabled"),
            ("mute_stories_after_follow", "mute_stories_enabled"),
            ("cold_dm_enabled", "outreach_enabled"),
            ("unfollow_enabled", "unfollow_enabled"),
        ):
            state = _valid_state(ig_account_settings=[{**settings, key: True}])
            self.assertIn(reason, self._send_one_reasons(state))

    def test_send_one_requires_welcome_real_send_enabled(self) -> None:
        state = _valid_state(
            env={
                "WELCOME_DM_REAL_SEND_ENABLED": "false",
                "OUTREACH_DM_REAL_SEND_ENABLED": "false",
                "DM_SENDER_REAL_SEND_ENABLED": "false",
            }
        )
        self.assertIn("welcome_real_send_disabled", self._send_one_reasons(state))

    def test_send_one_refuses_outreach_real_send_enabled(self) -> None:
        state = _valid_state(
            env={
                "WELCOME_DM_REAL_SEND_ENABLED": "true",
                "OUTREACH_DM_REAL_SEND_ENABLED": "true",
                "DM_SENDER_REAL_SEND_ENABLED": "false",
            }
        )
        self.assertIn("outreach_real_send_enabled", self._send_one_reasons(state))

    def test_send_one_refuses_legacy_real_send_enabled(self) -> None:
        state = _valid_state(
            env={
                "WELCOME_DM_REAL_SEND_ENABLED": "true",
                "OUTREACH_DM_REAL_SEND_ENABLED": "false",
                "DM_SENDER_REAL_SEND_ENABLED": "true",
            }
        )
        self.assertIn("legacy_dm_real_send_enabled", self._send_one_reasons(state))

    def test_send_one_calls_true_sender_when_gates_ok(self) -> None:
        state = _valid_state(
            env={
                "WELCOME_DM_REAL_SEND_ENABLED": "true",
                "OUTREACH_DM_REAL_SEND_ENABLED": "false",
                "DM_SENDER_REAL_SEND_ENABLED": "false",
            }
        )
        with (
            patch.dict(
                "sys.modules",
                {
                    "uiautomator2": type(
                        "U2",
                        (),
                        {"connect": staticmethod(lambda _serial: object())},
                    ),
                },
            ),
            patch.dict("os.environ", state["env"], clear=False),
            patch("device.app_start") as app_start,
            patch("dm_sender_engine.run_dm_sender_send") as sender,
            patch.object(smoke, "_row_by_id", return_value={**state["welcome_jobs"][0], "status": "sent"}),
        ):
            sender.return_value = (0, {"sender_status": "success", "jobs_sent_count": 1})
            code, execution = smoke.execute_send_one_smoke(
                state=state,
                job=state["welcome_jobs"][0],
                account_username="j_automatise_pour_toi",
            )

        self.assertEqual(code, 0)
        app_start.assert_called_once()
        sender.assert_called_once()
        kwargs = sender.call_args.kwargs
        self.assertEqual(kwargs["max_jobs"], 1)
        self.assertEqual(kwargs["dm_type"], "welcome")
        self.assertEqual(kwargs["prepared_jobs"], [state["welcome_jobs"][0]])
        self.assertEqual(execution["final_job"]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
