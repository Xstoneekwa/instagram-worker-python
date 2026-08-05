import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "FOLLOW60_MAINLINE_MANIFEST_V1_3.json"
LEDGER = ROOT / "FOLLOW60_MAINLINE_APPROVAL_LEDGER_V1_3.json"


def git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


class Follow60MainlineLockV13Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))

    def test_shadow_diff_scope_and_hash_match_approved_source_commit(self) -> None:
        base = self.manifest["worker_parent_sha"]
        target = self.manifest["approved_source_sha"]
        scope = self.manifest["approved_diff"]["scope"]
        actual_scope = git_bytes("diff", "--name-only", base, target).decode().splitlines()
        self.assertEqual(sorted(scope), sorted(actual_scope))
        payload = git_bytes("diff", "--no-ext-diff", "--binary", base, target, "--", *scope)
        self.assertEqual(
            self.manifest["approved_diff"]["sha256"],
            hashlib.sha256(payload).hexdigest(),
        )

    def test_critical_hashes_match_approved_source_commit(self) -> None:
        target = self.manifest["approved_source_sha"]
        for relative, expected in self.manifest["critical_files"].items():
            actual = hashlib.sha256(git_bytes("show", f"{target}:{relative}")).hexdigest()
            self.assertEqual(expected, actual)

    def test_one_shot_consumed_once_and_does_not_authorize_runtime(self) -> None:
        approval = self.manifest["approval"]
        approval_id = approval["approval_id"]
        self.assertEqual("consumed", approval["one_shot_status"])
        self.assertEqual(1, self.ledger["consumed_approval_ids"].count(approval_id))
        self.assertEqual(approval_id, self.ledger["entries"][0]["approval_id"])
        self.assertFalse(self.manifest["runtime_activation_authorized"])
        self.assertFalse(self.manifest["behavioral_ordering_v2"])
        self.assertFalse(self.manifest["default_enabled"])
        self.assertFalse(self.ledger["entries"][0]["runtime_activation_authorized"])

    def test_v1_runtime_and_critical_action_engines_remain_pinned(self) -> None:
        self.assertEqual(
            "4dce7f69ef7f80f69c15d1c7276545a74c057b16",
            self.manifest["active_production_worker_sha"],
        )
        parent = self.manifest["worker_parent_sha"]
        target = self.manifest["approved_source_sha"]
        for relative in (
            "instagram_navigation.py",
            "post_follow_stage_outbox.py",
            "supabase_client.py",
            "follow_60s_canary.py",
        ):
            self.assertEqual(
                git_bytes("show", f"{parent}:{relative}"),
                git_bytes("show", f"{target}:{relative}"),
            )


if __name__ == "__main__":
    unittest.main()
