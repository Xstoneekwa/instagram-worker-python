import hashlib
import inspect
import json
import subprocess
import unittest
from pathlib import Path

import follow60_ordering_v2_behavioral_canary_v1 as v2
import instagram_navigation


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "FOLLOW60_MAINLINE_MANIFEST_V1_5.json"
LEDGER = ROOT / "FOLLOW60_MAINLINE_APPROVAL_LEDGER_V1_5.json"
REX_ACCOUNT_ID = "b024e94e-395d-4f02-9787-81ddc679b014"


def git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


class Follow60MainlineLockV15Tests(unittest.TestCase):
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

    def test_critical_hashes_match_functional_source_commit(self) -> None:
        target = self.manifest["approved_source_sha"]
        for relative, expected in self.manifest["critical_files"].items():
            actual = hashlib.sha256(git_bytes("show", f"{target}:{relative}")).hexdigest()
            self.assertEqual(expected, actual)

    def test_historical_manifests_remain_immutable(self) -> None:
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
        self.assertFalse(self.ledger["entries"][0]["runtime_activation_authorized"])
        self.assertEqual("none", self.ledger["entries"][0]["activation_scope"])

    def test_v1_is_default_and_v2_is_exact_rex_only(self) -> None:
        self.assertFalse(self.manifest["default_enabled"])
        self.assertEqual([REX_ACCOUNT_ID], self.manifest["account_scope"])
        self.assertTrue(self.manifest["decision"]["v1_default_path"])
        self.assertTrue(self.manifest["decision"]["v2_default_off"])
        binding, reason = v2.validate_behavioral_canary_binding(
            None,
            account_id=REX_ACCOUNT_ID,
            run_id="run",
            request_id="request",
            business_session_id="session",
            attempt_id=1,
            worker_sha="a" * 40,
            completed_v2_cycles=0,
            environ={},
        )
        self.assertIsNone(binding)
        self.assertEqual("v2_behavioral_disabled", reason)
        self.assertEqual(
            ("FOLLOW60_V1", "v2_binding_absent_or_invalid"),
            v2.route_candidate_v2(
                binding=None, stable_proof=None, completed_v2_cycles=0
            ),
        )

    def test_v1_entrypoint_defaults_are_off_path(self) -> None:
        likes_sig = inspect.signature(instagram_navigation.run_post_follow_post_likes_phase)
        post_sig = inspect.signature(instagram_navigation.run_visual_candidate_post_follow_phase)
        self.assertIsNone(likes_sig.parameters["ordering_v2_post_first_context"].default)
        self.assertIsNone(post_sig.parameters["precompleted_like_result"].default)

    def test_security_decisions_are_locked(self) -> None:
        decision = self.manifest["decision"]
        self.assertEqual("DIRECT_GRID_SAFE", decision["v2_candidate_gate"])
        self.assertTrue(decision["v5_mandatory"])
        self.assertTrue(decision["pre_post_bounds_invalidated"])
        self.assertTrue(decision["level1_reentry_only"])
        self.assertFalse(decision["new_ui_acquisition_for_measurement"])


if __name__ == "__main__":
    unittest.main()
