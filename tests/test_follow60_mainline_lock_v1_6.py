import hashlib
import json
import subprocess
import unittest
from pathlib import Path

import follow60_ordering_v2_behavioral_canary_v1 as v2


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "FOLLOW60_MAINLINE_MANIFEST_V1_6.json"
LEDGER = ROOT / "FOLLOW60_MAINLINE_APPROVAL_LEDGER_V1_6.json"
REX_ACCOUNT_ID = "b024e94e-395d-4f02-9787-81ddc679b014"


def git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


class Follow60MainlineLockV16Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))

    def test_approved_source_scope_and_hash(self) -> None:
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

    def test_historical_v15_files_remain_immutable(self) -> None:
        for relative, expected in self.manifest["historical_hashes"].items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(expected, actual)

    def test_source_go_is_consumed_once_and_cannot_activate_runtime(self) -> None:
        approval = self.manifest["approval"]
        approval_id = approval["approval_id"]
        self.assertEqual("consumed", approval["one_shot_status"])
        self.assertEqual(1, self.ledger["consumed_approval_ids"].count(approval_id))
        self.assertEqual(approval_id, self.ledger["entries"][0]["approval_id"])
        self.assertFalse(self.manifest["runtime_activation_authorized"])
        self.assertFalse(self.manifest["production_migration_authorized"])
        self.assertEqual("none", self.ledger["entries"][0]["activation_scope"])

    def test_default_off_exact_single_account_and_no_source_hardcode(self) -> None:
        self.assertFalse(self.manifest["default_enabled"])
        self.assertEqual([REX_ACCOUNT_ID], self.manifest["account_scope"])
        self.assertFalse(v2.behavioral_runtime_scope_for_account(REX_ACCOUNT_ID, environ={})[0])
        source = (ROOT / "follow60_ordering_v2_behavioral_canary_v1.py").read_text()
        self.assertNotIn(REX_ACCOUNT_ID, source)
        self.assertTrue(v2.behavioral_runtime_scope_for_account(
            REX_ACCOUNT_ID,
            environ={v2.ENABLED_ENV: "true", v2.ALLOWLIST_ENV: REX_ACCOUNT_ID},
        )[0])

    def test_barrier_and_v1_isolation_decisions_are_locked(self) -> None:
        decision = self.manifest["decision"]
        self.assertEqual("v2_complete_count", decision["v2_barrier_counter"])
        self.assertFalse(decision["v1_fallback_counts_toward_v2_barrier"])
        self.assertTrue(decision["tenth_v2_complete_stops_before_candidate_eleven"])
        self.assertTrue(decision["claim_atomic_one_shot"])
        self.assertTrue(decision["v5_mandatory"])


if __name__ == "__main__":
    unittest.main()
