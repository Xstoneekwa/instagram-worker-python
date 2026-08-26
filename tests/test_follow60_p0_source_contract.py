from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Follow60P0SourceContractTest(unittest.TestCase):
    def test_armed_control_rejections_return_before_device_setup(self) -> None:
        source = (ROOT / "runner.py").read_text(encoding="utf-8")
        gate = source.index("follow_60s_armed_control_safe_stop_pre_device")
        device = source.index("gate_result = supabase_client.begin_device_activity_v1(")
        self.assertLess(gate, device)
        block = source[gate : gate + 1300]
        self.assertIn("return 96", block)
        self.assertIn("device_actions_started=False", block)
        self.assertIn("fallback_used=False", block)

    def test_runtime_sha_is_exported_by_canonical_wrapper(self) -> None:
        source = (ROOT / "scripts/run_control_dispatcher_service.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('export WORKER_RUNTIME_ROOT="$ROOT_DIR"', source)
        self.assertIn('export WORKER_GIT_SHA="$RUNTIME_GIT_SHA"', source)
        self.assertIn('WORKER_GIT_SHA_SOURCE="runtime_release_head"', source)

    def test_unfollow_search_hardening_preserves_follow60_activation_contract(self) -> None:
        # The Unfollow Search terminal-proof GO now explicitly authorizes the
        # shared search classifier.  Keep this lock semantic: a generic exact
        # text row cannot authorize a profile tap without account semantics.
        search = (ROOT / "unfollow_hybrid_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('bool(match.get("account_signal"))', search)
        self.assertIn('not bool(match.get("suggestion_signal"))', search)
        self.assertIn("Only canonical/account-semantic rows", search)

        unfollow = (ROOT / "unfollow_session_orchestrator.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"unfollow_diagnostic_v2_viewport"', unfollow)
        self.assertIn('"unfollow_candidate_lineage_v1"', unfollow)

        # The transactional activation GO explicitly requires the canary
        # registry to install concrete callbacks before consuming control.
        canary = (ROOT / "follow_60s_canary.py").read_text(encoding="utf-8")
        self.assertIn("def install_activation_components", canary)
        self.assertIn("callable(components.get(name))", canary)


if __name__ == "__main__":
    unittest.main()
