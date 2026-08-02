from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runner
import worker_runtime_identity as identity_module


ROOT = Path(__file__).resolve().parents[1]
SHA = subprocess.run(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
REQUEST_ID = "66f92055-21ee-4a7c-a042-3a857d3f8448"
RUN_ID = "9bde8e78-7955-46d5-bd29-044548d2a911"


class CtResumeRuntimeIdentityV5Tests(unittest.TestCase):
    def test_wrapper_consumer_runner_checkpoint_chain_without_legacy_hints(self):
        env = dict(os.environ)
        env.pop("GIT_SHA", None)
        env.pop("PHONEFARM_ACTIVE_ROOT", None)
        env.pop("PHONEFARM_ACTIVE_COMMIT", None)
        env.pop(identity_module.RUNTIME_IDENTITY_ENV, None)
        env["RUN_CONTROL_DISPATCHER_PYTHON"] = sys.executable
        with tempfile.TemporaryDirectory() as temporary_directory:
            current_link = Path(temporary_directory) / "phonefarm-worker-current"
            current_link.symlink_to(ROOT)
            env["PHONEFARM_CURRENT_SYMLINK"] = str(current_link)
            env["WORKER_RUNTIME_REQUIRE_CANONICAL_SYMLINK"] = "true"
            completed = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "run_control_dispatcher_service.sh"),
                    "ct-resume-identity-self-test",
                ],
                cwd="/private/tmp",
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=45.0,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(
            result["verdict"],
            "CT_RESUME_RUNTIME_IDENTITY_PROPAGATED_END_TO_END",
        )
        self.assertEqual(result["worker_sha"], SHA)
        self.assertEqual(result["release_head"], SHA)
        self.assertTrue(result["runtime_root_ok"])
        self.assertNotEqual(result["wrapper_pid"], result["consumer_pid"])
        self.assertNotEqual(result["consumer_pid"], result["runner_pid"])
        self.assertTrue(result["runner_cwd_outside_release"])
        self.assertTrue(result["legacy_identity_hints_absent"])
        self.assertTrue(result["checkpoint_found"])
        self.assertTrue(result["checkpoint_loaded"])
        self.assertEqual(result["checkpoint_depth_before"], 0)
        self.assertEqual(result["proposed_resume_depth"], 0)
        self.assertEqual(result["anchor_overlap_count"], 3)
        self.assertTrue(result["lease_claimed"])
        self.assertTrue(result["lease_released"])
        self.assertEqual(result["device_actions"], 0)
        self.assertEqual(result["db_writes"], 0)

    def test_ct_resume_release_sha_does_not_reinvoke_git_or_read_symlink(self):
        identity = identity_module.WorkerRuntimeIdentity(
            runtime_root=str(ROOT),
            worker_sha=SHA,
            source="test",
            release_path=str(ROOT),
            release_head=SHA,
            runtime_root_ok=True,
        )
        with patch.object(runner.subprocess, "run", side_effect=AssertionError("git forbidden")):
            self.assertEqual(runner._resolve_active_worker_release_sha(identity), SHA)

    def test_missing_identity_fails_closed_with_exact_taxonomy(self):
        provenance, reason, diagnostics = runner._resolve_target_followers_resume_provenance(
            target_followers_resume_source_request_id=REQUEST_ID,
            auto_restart_resume_policy={"attempt_id": 1},
            worker_runtime_identity=None,
            run_id=RUN_ID,
        )
        self.assertIsNone(provenance)
        self.assertEqual(reason, "identity_not_propagated")
        self.assertEqual(diagnostics["stage"], "ct_resume_identity_binding")
        self.assertIn("pid", diagnostics)
        self.assertIn("cwd", diagnostics)


if __name__ == "__main__":
    unittest.main()
