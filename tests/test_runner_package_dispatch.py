"""CLI/default package propagation through the real immutable profile fence."""
import unittest

from tests.test_runner_device_serial import OfflineRunnerProof, base_argv, PACKAGE, SERIAL

CLONE_PACKAGE = "com.instagram.androif"


class RunnerPackageDispatchTest(unittest.TestCase):
    def assert_package(self, package, argv):
        proof = OfflineRunnerProof(self, package=package)
        code, seen = proof.run(argv)
        self.assertNotEqual(code, 0)
        proof.connect.assert_called_once_with(SERIAL)
        self.assertEqual(seen.get("package"), package)
        self.assertEqual(seen["binding"]["package"], package)
        self.assertTrue(seen["armed"])
        self.assertTrue(seen["attached"])
        self.assertEqual(proof.writes, ["ig_runs", "account_session_resume_plans"])

    def test_cli_package_name_overrides_default_primary_package(self):
        self.assert_package(CLONE_PACKAGE, base_argv("--package-name", CLONE_PACKAGE))

    def test_without_cli_package_runner_keeps_default_package(self):
        self.assert_package(PACKAGE, base_argv())

    def test_identical_fresh_assignment_and_cli_are_accepted(self):
        self.assert_package(CLONE_PACKAGE, base_argv("--package-name", CLONE_PACKAGE))

    def test_contradictory_fresh_assignment_and_cli_never_connect(self):
        proof = OfflineRunnerProof(self, package="com.instagram.androig")
        code, seen = proof.run(base_argv("--package-name", CLONE_PACKAGE))
        self.assertNotEqual(code, 0)
        proof.connect.assert_not_called()
        self.assertNotIn("package", seen)
        self.assertFalse(seen["armed"])
        self.assertEqual(proof.writes, [])


if __name__ == "__main__":
    unittest.main()
