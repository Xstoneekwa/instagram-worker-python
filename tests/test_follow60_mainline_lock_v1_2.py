import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "FOLLOW60_MAINLINE_MANIFEST_V1_2.json"
LEDGER = ROOT / "FOLLOW60_MAINLINE_APPROVAL_LEDGER_V1_2.json"


def git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


class Follow60MainlineLockV12Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))

    def test_approved_diff_scope_and_hash_match_git(self) -> None:
        base = self.manifest["worker_parent_sha"]
        target = self.manifest["approved_worker_sha"]
        scope = self.manifest["approved_diff"]["scope"]
        actual_scope = git_bytes("diff", "--name-only", base, target).decode().splitlines()
        self.assertEqual(sorted(scope), sorted(actual_scope))
        payload = git_bytes("diff", "--no-ext-diff", "--binary", base, target, "--", *scope)
        self.assertEqual(
            self.manifest["approved_diff"]["sha256"], hashlib.sha256(payload).hexdigest()
        )

    def test_critical_file_hashes_match_approved_target(self) -> None:
        target = self.manifest["approved_worker_sha"]
        for relative, expected in self.manifest["critical_files"].items():
            self.assertEqual(expected, hashlib.sha256(git_bytes("show", f"{target}:{relative}")).hexdigest())

    def test_one_shot_approval_is_consumed_exactly_once(self) -> None:
        approval = self.manifest["approval"]
        approval_id = approval["approval_id"]
        self.assertEqual("consumed", approval["one_shot_status"])
        self.assertEqual(1, self.ledger["consumed_approval_ids"].count(approval_id))
        self.assertEqual(approval_id, self.ledger["entries"][0]["approval_id"])


if __name__ == "__main__":
    unittest.main()
