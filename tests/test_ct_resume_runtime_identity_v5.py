from __future__ import annotations

import json
import hashlib
import shutil
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
    def _certified_runtime_fixture(self, base: Path):
        """Copy exact runtime bytes into a disposable, genuinely signed test release.

        Only the test trust anchor differs. No production key or certification is
        used; the real deployment gate verifies the fixture commit and every file.
        """
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from tests.test_follow60_deployment_gate_v1 import Follow60DeploymentGateV1Tests
        from follow60_lock_v3 import canonical_json, git_file_entry, sha256_bytes, transitive_import_graph
        from follow60_deployment_gate_v1 import verify_deployment_candidate
        from follow60_candidate_identity_v2 import RELEASE_CERTIFICATION_FILES
        root = base / "release"
        root.mkdir()
        paths = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
        ).decode().split("\0")
        public_relative = "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem"
        for name in sorted(set(filter(None, paths))):
            source, target = ROOT / name, root / name
            if not source.is_file():
                raise AssertionError(f"runtime fixture source missing: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),
                             hashlib.sha256(target.read_bytes()).digest(), name)
        private = Ed25519PrivateKey.generate()
        public = root / public_relative
        public.write_bytes(private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
        (root / "protected.py").write_text("# Disposable certification fixture only.\n")
        def git(*args):
            return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
        git("init", "-q")
        git("config", "user.name", "Offline fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("add", ".")
        git("commit", "-qm", "Disposable signed runtime fixture")
        sha = git("rev-parse", "HEAD")
        payload = Follow60DeploymentGateV1Tests()._manifest(root, sha)
        entries = {name: {**git_file_entry(root, name), "role": "runtime", "lock_version": "3.1.0"}
                   for name in git("ls-files").splitlines() if name not in RELEASE_CERTIFICATION_FILES}
        graph = transitive_import_graph(root, entries)
        payload.update(protected_entries=entries, protected_scope_sha256=sha256_bytes(canonical_json(entries)),
                       import_graph=graph, import_graph_sha256=sha256_bytes(canonical_json(graph)))
        evidence = base / "runtime" / "deployment-evidence" / sha
        evidence.mkdir(parents=True)
        manifest = evidence / "FOLLOW60_MAINLINE_LOCK_V3.json"
        signature = evidence / "FOLLOW60_MAINLINE_LOCK_V3.sig"
        manifest.write_bytes(canonical_json(payload))
        signed = private.sign(manifest.read_bytes())
        signature.write_bytes(signed)
        args = dict(manifest_path=manifest, signature_path=signature, public_key_path=public)
        verified = verify_deployment_candidate(root, **args)
        self.assertTrue(verified["ok"], verified)
        signature.write_bytes(bytes([signed[0] ^ 1]) + signed[1:])
        self.assertFalse(verify_deployment_candidate(root, **args)["ok"])
        signature.write_bytes(signed)
        return root, sha

    def test_wrapper_consumer_runner_checkpoint_chain_without_legacy_hints(self):
        env = dict(os.environ)
        env.pop("GIT_SHA", None)
        env.pop("PHONEFARM_ACTIVE_ROOT", None)
        env.pop("PHONEFARM_ACTIVE_COMMIT", None)
        env.pop(identity_module.RUNTIME_IDENTITY_ENV, None)
        env["RUN_CONTROL_DISPATCHER_PYTHON"] = sys.executable
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory).resolve()
            release, fixture_sha = self._certified_runtime_fixture(base)
            current_link = base / "phonefarm-worker-current"
            current_link.symlink_to(release)
            env["PHONEFARM_RUNTIME_HOME"] = str(base / "runtime")
            env["PHONEFARM_RUNTIME_ROOT"] = str(base / "runtime")
            env["PHONEFARM_CURRENT_SYMLINK"] = str(current_link)
            env["WORKER_RUNTIME_REQUIRE_CANONICAL_SYMLINK"] = "true"
            completed = subprocess.run(
                [
                    "bash",
                    str(release / "scripts" / "run_control_dispatcher_service.sh"),
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
        self.assertEqual(result["worker_sha"], fixture_sha)
        self.assertEqual(result["release_head"], fixture_sha)
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
