from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Follow60P0SourceContractTest(unittest.TestCase):
    def test_armed_control_rejections_return_before_device_setup(self) -> None:
        source = (ROOT / "runner.py").read_text(encoding="utf-8")
        gate = source.index("follow_60s_armed_control_safe_stop_pre_device")
        device = source.index("device_connected")
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

    def test_unfollow_ui_files_are_not_in_follow60_diff(self) -> None:
        # The original P0 excluded every navigation edit. The approved
        # PostGrid/Like successor deliberately changes instagram_navigation,
        # while Unfollow remains outside scope and must stay untouched.
        forbidden = {
            "unfollow_hybrid_strategy.py",
            "unfollow_session_orchestrator.py",
        }
        import subprocess

        changed = set(
            subprocess.check_output(
                ["git", "diff", "--name-only", "5132e8c"],
                cwd=ROOT,
                text=True,
            ).splitlines()
        )
        self.assertFalse(changed & forbidden, changed & forbidden)

        # The transactional activation GO explicitly requires the canary
        # registry to install concrete callbacks before consuming control.
        canary = (ROOT / "follow_60s_canary.py").read_text(encoding="utf-8")
        self.assertIn("def install_activation_components", canary)
        self.assertIn("callable(components.get(name))", canary)


if __name__ == "__main__":
    unittest.main()
