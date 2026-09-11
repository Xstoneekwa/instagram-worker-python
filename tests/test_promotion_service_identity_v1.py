from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import phonefarm_runtime_control as control


SHA = "a" * 40


class PromotionServiceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.releases = base / "releases"
        self.runtime = base / "runtime"
        self.new = self.releases / "new"
        self.old = self.releases / "old"
        for root in (self.runtime, self.new, self.old):
            root.mkdir(parents=True)
        for root in (self.new, self.old):
            (root / "account_run_request_consumer.py").write_text("# fixture\n")
        self.paths = control.RuntimePaths(base / "current", self.releases, self.runtime, base / "legacy")

    def converge(self, command, cwd=None, rows=None, sha=SHA):
        cwd = str(cwd or self.runtime)
        rows = [(101, 1, command)] if rows is None else rows
        with (
            mock.patch.object(control, "_pid_cwd", return_value=cwd),
            mock.patch.object(control, "_release_sha_for_process_root", return_value=sha),
        ):
            return control._canonical_component_process_convergence(
                component="dispatcher",
                paths=self.paths,
                target_root=self.new,
                target_sha=SHA,
                rows=rows,
            )

    def test_runtime_cwd_with_new_module_and_sha_passes(self):
        matches, failures = self.converge(f"python3 {self.new}/account_run_request_consumer.py")
        self.assertEqual(len(matches), 1)
        self.assertEqual(failures, [])

    def test_runtime_cwd_with_old_module_fails(self):
        _, failures = self.converge(f"python3 {self.old}/account_run_request_consumer.py")
        self.assertIn("module_old_or_unknown_root", failures)

    def test_unexpected_module_root_fails(self):
        other = Path(self.tmp.name) / "unexpected"
        other.mkdir()
        (other / "account_run_request_consumer.py").write_text("# fixture\n")
        _, failures = self.converge(f"python3 {other}/account_run_request_consumer.py")
        self.assertIn("module_old_or_unknown_root", failures)

    def test_wrong_release_sha_fails(self):
        _, failures = self.converge(f"python3 {self.new}/account_run_request_consumer.py", sha="b" * 40)
        self.assertIn("runtime_sha_mismatch", failures)

    def test_pid_reuse_with_wrong_module_fails(self):
        _, failures = self.converge(f"python3 {self.old}/account_run_request_consumer.py", rows=[(101, 999, f"python3 {self.old}/account_run_request_consumer.py")])
        self.assertTrue(failures)

    def test_duplicate_services_are_visible_to_cardinality_gate(self):
        rows = [
            (101, 1, f"python3 {self.new}/account_run_request_consumer.py"),
            (102, 1, f"python3 {self.new}/account_run_request_consumer.py"),
        ]
        matches, failures = self.converge(rows[0][2], rows=rows)
        self.assertEqual(len(matches), 2)
        self.assertEqual(failures, [])
        self.assertNotEqual(len(matches), 1)

    def test_missing_service_is_visible_to_cardinality_gate(self):
        matches, failures = self.converge("irrelevant", rows=[])
        self.assertEqual(matches, [])
        self.assertEqual(failures, [])
        self.assertNotEqual(len(matches), 1)

    def test_token_timeout_forces_overall_ok_false(self):
        service_pass = {
            "ok": True,
            "status": "SERVICE_CONVERGENCE_PASS",
            "services": {},
        }
        with (
            mock.patch.object(control, "canonical_service_snapshot", return_value=service_pass),
            mock.patch.object(control, "verify_startup_skip_consumed", return_value={"ok": False, "reason": "wrong_phase"}),
        ):
            result = control._wait_for_service_convergence(
                paths=self.paths,
                expected_root=str(self.new),
                expected_sha=SHA,
                expected_startup_token={"token_id": "wrong"},
                timeout_seconds=0,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "SERVICE_CONVERGENCE_TIMEOUT")
        self.assertFalse(result["startupTokenGate"]["ok"])


if __name__ == "__main__":
    unittest.main()
