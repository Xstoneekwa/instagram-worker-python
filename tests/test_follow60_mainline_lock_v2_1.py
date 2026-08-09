import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "FOLLOW60_V2_MAINLINE_MANIFEST_V1.json"
LEDGER = ROOT / "FOLLOW60_V2_MAINLINE_APPROVAL_LEDGER_V1.json"
VERIFIER = ROOT / "scripts/verify-follow60-mainline-lock-v2-1.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("follow60_lock_v2_1", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class Follow60MainlineLockV21Tests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text())
        self.ledger = json.loads(LEDGER.read_text())
        self.verifier = load_verifier()

    def test_lock_accepts_exact_source_and_consumed_go(self):
        result = self.verifier.verify(ROOT, MANIFEST, LEDGER)
        self.assertTrue(result["ok"], result)
        self.assertEqual("consumed_once", result["approval_status"])

    def test_manifest_hash_is_sidecar_and_ledger_bound(self):
        actual = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
        self.assertEqual(actual, self.ledger["manifest_sha256"])
        self.assertEqual(actual, (ROOT / "FOLLOW60_V2_MAINLINE_MANIFEST_V1.sha256").read_text().split()[0])

    def test_reuse_and_manifest_mutation_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            changed_manifest = temp / "manifest.json"
            changed_manifest.write_bytes(MANIFEST.read_bytes() + b"\n")
            result = self.verifier.verify(ROOT, changed_manifest, LEDGER)
            self.assertEqual("manifest_hash_mismatch", result["reason"])
            ledger = dict(self.ledger)
            ledger["consumed_approval_ids"] = ledger["consumed_approval_ids"] * 2
            changed_ledger = temp / "ledger.json"
            changed_ledger.write_text(json.dumps(ledger))
            result = self.verifier.verify(ROOT, MANIFEST, changed_ledger)
            self.assertEqual("approval_not_consumed_exactly_once", result["reason"])

    def test_normal_mainline_contract_and_shared_startup_path(self):
        self.assertTrue(self.manifest["default_global"])
        self.assertFalse(self.manifest["normal_path_requires_canary_control"])
        self.assertFalse(self.manifest["normal_path_requires_allowlist"])
        self.assertFalse(self.manifest["normal_path_has_ten_cycle_barrier"])
        promotion = (ROOT / "scripts/promote-follow60-v2-mainline.sh").read_text()
        self.assertIn("/Users/admin/phonefarm-runtime/run/run-control-dispatcher/auto-restart-skip-startup-tick.once", promotion)
        self.assertNotIn("/.local/", promotion)

    def test_approved_scope_and_diff_hash(self):
        base = self.manifest["worker_parent_sha"]
        target = self.manifest["approved_source_sha"]
        scope = self.manifest["approved_diff"]["scope"]
        actual_scope = subprocess.check_output(
            ["git", "-C", str(ROOT), "diff", "--name-only", base, target], text=True
        ).splitlines()
        self.assertEqual(sorted(scope), sorted(actual_scope))


if __name__ == "__main__":
    unittest.main()
