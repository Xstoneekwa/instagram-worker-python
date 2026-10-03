from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import phonefarm_runtime_control as control


def _canonical_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class SealedReleaseFinalTreeV1Tests(unittest.TestCase):
    def test_generation_publication_is_explicit_and_bounded(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "scripts/create-certified-phonefarm-release-v1.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--generation")', source)
        self.assertIn("generation_selector_invalid", source)
        self.assertIn("generation_selector_outside_sha", source)
        self.assertIn("evidence_dir / generation", source)

    def test_external_evidence_binds_final_clean_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release = base / "release"
            runtime = base / "runtime"
            release.mkdir()
            subprocess.check_call(["git", "init", "-q", str(release)])
            subprocess.check_call(["git", "-C", str(release), "config", "user.email", "test@example.invalid"])
            subprocess.check_call(["git", "-C", str(release), "config", "user.name", "Test"])
            (release / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(release), "add", "."])
            subprocess.check_call(["git", "-C", str(release), "commit", "-qm", "sealed"])
            sha = subprocess.check_output(
                ["git", "-C", str(release), "rev-parse", "HEAD"], text=True
            ).strip()
            tree = subprocess.check_output(
                ["git", "-C", str(release), "rev-parse", "HEAD^{tree}"], text=True
            ).strip()
            evidence = runtime / "deployment-evidence" / sha
            evidence.mkdir(parents=True)
            migration = {
                "schema": "PHONE_FARM_MIGRATION_ATTESTATION_V1",
                "applied_versions": ["v1"],
            }
            lineage = {
                "status": "PRODUCTION_LINEAGE_GATE_V1_PASS",
                "candidate_sha": sha,
                "worktree_clean": True,
                "migration_gate": "PASS",
                "migration_attestation_sha256": _canonical_hash(migration),
                "validated_migrations": [{"version": "v1"}],
            }
            build = {
                "candidate_sha": sha,
                "release_sha": sha,
                "release_tree_sha": tree,
                "sealed_release_clean": True,
                "manifest_certified_sha": sha,
                "manifest_sha256": "a" * 64,
                "candidate_worktree_clean": True,
                "uncommitted_protected_diff": False,
                "untracked_protected_file": False,
                "protected_diff_status": "PASS",
                "follow60_lock_result": "PASS",
                "signature_result": "PASS",
                "lineage_gate_result": "PASS",
                "migration_attestation_sha256": _canonical_hash(migration),
            }
            for name, payload in (
                ("candidate-build-receipt.json", build),
                ("migration-attestation.json", migration),
                ("lineage-gate-receipt.json", lineage),
            ):
                (evidence / name).write_text(json.dumps(payload), encoding="utf-8")
            integrity = {
                "candidate_sha": sha,
                "manifest_certified_sha": sha,
                "manifest_sha256": "a" * 64,
            }
            with mock.patch.dict(
                os.environ,
                {"PHONEFARM_RUNTIME_ROOT": "", "PHONEFARM_RUNTIME_HOME": str(runtime)},
                clear=False,
            ), mock.patch.object(control, "_is_user_immutable", return_value=True):
                result = control._validate_release_evidence(release, integrity)
            self.assertTrue(result["ok"], result)
            self.assertEqual(
                subprocess.check_output(
                    ["git", "-C", str(release), "status", "--porcelain=v1", "--untracked-files=all"],
                    text=True,
                ),
                "",
            )
            self.assertFalse((release / ".deployment").exists())
            self.assertEqual(
                subprocess.check_output(
                    ["git", "-C", str(release), "rev-parse", "HEAD^{tree}"], text=True
                ).strip(),
                tree,
            )

    def test_external_evidence_with_wrong_final_tree_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            release = Path(raw) / "release"
            release.mkdir()
            subprocess.check_call(["git", "init", "-q", str(release)])
            subprocess.check_call(["git", "-C", str(release), "config", "user.email", "test@example.invalid"])
            subprocess.check_call(["git", "-C", str(release), "config", "user.name", "Test"])
            (release / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(release), "add", "."])
            subprocess.check_call(["git", "-C", str(release), "commit", "-qm", "sealed"])
            sha = subprocess.check_output(["git", "-C", str(release), "rev-parse", "HEAD"], text=True).strip()
            with mock.patch.object(control, "deployment_evidence_dir") as evidence_dir:
                evidence = Path(raw) / "evidence"
                evidence.mkdir()
                evidence_dir.return_value = evidence
                migration = {"schema": "PHONE_FARM_MIGRATION_ATTESTATION_V1", "applied_versions": ["v1"]}
                lineage = {
                    "status": "PRODUCTION_LINEAGE_GATE_V1_PASS", "candidate_sha": sha,
                    "worktree_clean": True, "migration_gate": "PASS",
                    "migration_attestation_sha256": _canonical_hash(migration),
                    "validated_migrations": [{"version": "v1"}],
                }
                build = {
                    "candidate_sha": sha, "release_sha": sha,
                    "release_tree_sha": "f" * 40, "sealed_release_clean": True,
                    "manifest_certified_sha": sha, "manifest_sha256": "a" * 64,
                    "candidate_worktree_clean": True, "uncommitted_protected_diff": False,
                    "untracked_protected_file": False, "protected_diff_status": "PASS",
                    "follow60_lock_result": "PASS", "signature_result": "PASS",
                    "lineage_gate_result": "PASS", "migration_attestation_sha256": _canonical_hash(migration),
                }
                for name, payload in (("candidate-build-receipt.json", build), ("migration-attestation.json", migration), ("lineage-gate-receipt.json", lineage)):
                    (evidence / name).write_text(json.dumps(payload), encoding="utf-8")
                with mock.patch.object(control, "_is_user_immutable", return_value=True):
                    result = control._validate_release_evidence(
                        release,
                        {"candidate_sha": sha, "manifest_certified_sha": sha, "manifest_sha256": "a" * 64},
                    )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "candidate_build_receipt_mismatch:release_tree_sha")


if __name__ == "__main__":
    unittest.main()
