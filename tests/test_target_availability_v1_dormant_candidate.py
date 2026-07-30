from __future__ import annotations

from pathlib import Path
import unittest

from target_availability_writer import TargetAvailabilityFeatureFlags


ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
CURRENT_PRODUCTION_BASE = "fecf91dfe8e60535810cd99ad9c10d370022ab16"


class TargetAvailabilityV1DormantCandidateTests(unittest.TestCase):
    def test_all_availability_controls_default_off_and_allowlist_is_mandatory(self):
        flags = TargetAvailabilityFeatureFlags.from_mapping({})
        self.assertFalse(flags.capture_allowed(ACCOUNT_ID))
        self.assertFalse(flags.writer_allowed(ACCOUNT_ID))
        self.assertFalse(flags.target_availability_shadow_enabled)
        self.assertFalse(flags.target_availability_policy_shadow_enabled)

        writer_without_capture = TargetAvailabilityFeatureFlags.from_mapping({
            "TARGET_AVAILABILITY_WRITER_ENABLED": "true",
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": ACCOUNT_ID,
        })
        self.assertFalse(writer_without_capture.writer_allowed(ACCOUNT_ID))

    def test_worker_has_no_identity_assessment_or_current_table_producer(self):
        root = Path(__file__).resolve().parents[1]
        forbidden_tables = {
            "ct_target_identity_history",
            "ct_target_identity_current",
            "ct_target_availability_assessments",
            "ct_target_availability_current",
        }
        violations = []
        for source_path in root.glob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            for table in forbidden_tables:
                if table in source:
                    violations.append("%s:%s" % (source_path.name, table))
        self.assertEqual(violations, [])

    def test_candidate_changes_tests_only_from_current_worker_production_base(self):
        root = Path(__file__).resolve().parents[1]
        import subprocess

        changed = subprocess.run(
            ["git", "diff", "--name-only", CURRENT_PRODUCTION_BASE, "--"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(changed.returncode, 0, changed.stderr)
        changed_paths = [line for line in changed.stdout.splitlines() if line]
        self.assertTrue(all(path.startswith("tests/") for path in changed_paths), changed_paths)


if __name__ == "__main__":
    unittest.main()
