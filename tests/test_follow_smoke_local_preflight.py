from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "follow_smoke_local_preflight.sh"
MULTITARGET_WRAPPER = ROOT / "scripts" / "run_follow_smoke_multitarget.sh"


class FollowSmokeLocalPreflightTest(unittest.TestCase):
    def _write_minimal_root(
        self,
        root: Path,
        *,
        config_body: str,
        identity_guard_body: str = (
            "ACCOUNT_IDENTITY_MISMATCH_REASON = "
            "'active_instagram_account_mismatch'\n"
        ),
    ) -> Path:
        (root / "scripts").mkdir()
        wrapper = root / "scripts" / "run_follow_smoke_multitarget.sh"
        wrapper.write_text("#!/usr/bin/env bash\necho wrapper\n", encoding="utf-8")
        (root / "config.py").write_text(config_body, encoding="utf-8")
        (root / "runner.py").write_text("import config\n", encoding="utf-8")
        (root / "instagram_navigation.py").write_text("# navigation\n", encoding="utf-8")
        (root / "account_identity_guard.py").write_text(
            identity_guard_body,
            encoding="utf-8",
        )
        (root / "account_session_orchestrator.py").write_text("# orchestrator\n", encoding="utf-8")
        return wrapper

    def _run_helper(self, root: Path, wrapper: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(HELPER), str(root), str(wrapper)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_empty_config_stops_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._write_minimal_root(root, config_body="")
            proc = self._run_helper(root, wrapper)

        self.assertEqual(proc.returncode, 10)
        self.assertIn("STOP reason=critical_file_invalid", proc.stdout)
        self.assertIn("file=config.py", proc.stdout)
        self.assertIn("detail=missing_or_empty_or_missing_INSTAGRAM_PACKAGE", proc.stdout)

    def test_config_missing_instagram_package_stops_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._write_minimal_root(root, config_body="OTHER = 'x'\n")
            proc = self._run_helper(root, wrapper)

        self.assertEqual(proc.returncode, 10)
        self.assertIn("STOP reason=critical_file_invalid", proc.stdout)
        self.assertIn("file=config.py", proc.stdout)
        self.assertIn("detail=missing_or_empty_or_missing_INSTAGRAM_PACKAGE", proc.stdout)

    def test_valid_config_allows_smoke_to_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._write_minimal_root(
                root,
                config_body="INSTAGRAM_PACKAGE = 'com.instagram.android'\n",
            )
            proc = self._run_helper(root, wrapper)

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_empty_account_identity_guard_stops_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._write_minimal_root(
                root,
                config_body="INSTAGRAM_PACKAGE = 'com.instagram.android'\n",
                identity_guard_body="",
            )
            proc = self._run_helper(root, wrapper)

        self.assertEqual(proc.returncode, 10)
        self.assertIn("STOP reason=critical_file_invalid", proc.stdout)
        self.assertIn("file=account_identity_guard.py", proc.stdout)

    def test_account_identity_guard_missing_reason_stops_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._write_minimal_root(
                root,
                config_body="INSTAGRAM_PACKAGE = 'com.instagram.android'\n",
                identity_guard_body="OTHER = 'x'\n",
            )
            proc = self._run_helper(root, wrapper)

        self.assertEqual(proc.returncode, 10)
        self.assertIn("STOP reason=critical_file_invalid", proc.stdout)
        self.assertIn("file=account_identity_guard.py", proc.stdout)
        self.assertIn(
            "detail=missing_or_empty_or_missing_ACCOUNT_IDENTITY_MISMATCH_REASON",
            proc.stdout,
        )

    def test_missing_wrapper_stops_with_clear_operator_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._write_minimal_root(
                root,
                config_body="INSTAGRAM_PACKAGE = 'com.instagram.android'\n",
            )
            wrapper.unlink()
            proc = self._run_helper(root, wrapper)

        self.assertEqual(proc.returncode, 10)
        self.assertIn("STOP reason=critical_file_invalid", proc.stdout)
        self.assertIn("detail=missing_or_empty", proc.stdout)

    def test_multitarget_wrapper_runs_local_preflight_before_request_creation(self) -> None:
        text = MULTITARGET_WRAPPER.read_text(encoding="utf-8")
        preflight_idx = text.index("follow_smoke_local_preflight.sh")
        ok_idx = text.index("LOCAL_PREFLIGHT_OK")
        request_idx = text.index("create_account_run_request")
        runs_dir_idx = text.index("mkdir -p runs")

        self.assertLess(preflight_idx, runs_dir_idx)
        self.assertLess(ok_idx, runs_dir_idx)
        self.assertLess(preflight_idx, request_idx)

    def test_multitarget_wrapper_resolves_account_from_username_arg(self) -> None:
        text = MULTITARGET_WRAPPER.read_text(encoding="utf-8")

        self.assertIn('USERNAME="${1:-}"', text)
        self.assertIn('"username": f"eq.{USERNAME}"', text)
        self.assertIn('ACCOUNT_ID = str(account_rows[0].get("id")', text)
        self.assertNotIn("EXPECTED_USERNAME", text)
        self.assertNotIn("83de9cc9-5c37-42d1-9edc-c924352b17b1", text)

    def test_multitarget_wrapper_resolves_assignment_package_dynamically(self) -> None:
        text = MULTITARGET_WRAPPER.read_text(encoding="utf-8")

        self.assertIn("resolve_account_assignment_runtime_context", text)
        self.assertIn('DEVICE_SERIAL = str(ctx.get("adb_serial")', text)
        self.assertIn('PACKAGE_NAME = str(ctx.get("package_name")', text)
        self.assertNotIn('PACKAGE_NAME="com.instagram.androie"', text)

    def test_multitarget_wrapper_caps_mismatch_stops_before_request(self) -> None:
        text = MULTITARGET_WRAPPER.read_text(encoding="utf-8")
        caps_stop_idx = text.index('stop("account_caps_not_aligned")')
        request_idx = text.index("create_account_run_request")

        self.assertLess(caps_stop_idx, request_idx)
        self.assertIn('follow_limit != REQUESTED_FOLLOW_LIMIT', text)
        self.assertIn('max_targets_per_run") or 0) != REQUESTED_MAX_TARGETS_PER_RUN', text)
        self.assertIn(
            'max_follows_per_target_per_run") or 0) != REQUESTED_MAX_FOLLOWS_PER_TARGET_PER_RUN',
            text,
        )

    def test_multitarget_wrapper_checks_targets_and_active_work_before_request(self) -> None:
        text = MULTITARGET_WRAPPER.read_text(encoding="utf-8")
        request_idx = text.index("create_account_run_request")

        for marker in (
            "active_ig_run_exists",
            "active_account_run_request_exists",
            "active_live_view_session_exists",
            "insufficient_eligible_follow_targets",
        ):
            self.assertIn(marker, text)
            self.assertLess(text.index(marker), request_idx)

    def test_multitarget_wrapper_checks_adbkeyboard_before_request(self) -> None:
        text = MULTITARGET_WRAPPER.read_text(encoding="utf-8")
        ime_idx = text.index("adbkeyboard_not_active")
        request_idx = text.index("create_account_run_request")

        self.assertLess(ime_idx, request_idx)
        self.assertIn("CURRENT_IME", text)
        self.assertIn("FAST_IME", text)

    def test_multitarget_wrapper_request_created_after_device_package_checks(self) -> None:
        text = MULTITARGET_WRAPPER.read_text(encoding="utf-8")
        device_idx = text.index("device_offline")
        package_idx = text.index("unexpected_package")
        request_idx = text.index("create_account_run_request")

        self.assertLess(device_idx, request_idx)
        self.assertLess(package_idx, request_idx)

    def test_multitarget_wrapper_reads_onboarding_status_from_client_instagram_accounts(self) -> None:
        text = MULTITARGET_WRAPPER.read_text(encoding="utf-8")

        self.assertIn('"select": "login_status,provisioning_status,onboarding_status"', text)
        self.assertIn('stop("onboarding_status_not_ready")', text)


if __name__ == "__main__":
    unittest.main()
