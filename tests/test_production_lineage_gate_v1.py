from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_lineage_gate_v1.py"
REGISTRY = ROOT / "docs" / "governance" / "PRODUCTION_CANONICAL_DELTA_REGISTRY_V1.json"
HISTORICAL = ROOT / "tests" / "fixtures" / "production_lineage_gate_v1" / "historical_rollback_missing_delta.json"
BACKEND_ROOT = Path("/Users/admin/Projects/boost-production-lineage-gate-v1")


def _delta(repo: str, delta_id: str, sha: str, *, status: str = "ACTIVE", **extra):
    return {
        "id": delta_id,
        "repo": repo,
        "introduced_by_sha": sha,
        "scope": ["fixture"],
        "activated_at": "2026-08-20T00:00:00Z",
        "required": status == "ACTIVE",
        "superseded_by": None,
        "migration_dependencies": [],
        "tests": ["tests/test_production_lineage_gate_v1.py"],
        "notes": "Deterministic governance fixture.",
        "status": status,
        **extra,
    }


def _registry(repo: str, production: str, deltas: list[dict], migrations=None):
    return {
        "schema": "PHONE_FARM_CANONICAL_DELTA_REGISTRY_V1",
        "components": {
            repo: {
                "production": {"verification_state": "VERIFIED", "sha": production},
                "artifact_provenance_required": False,
                "deltas": deltas,
            }
        },
        "migrations": list(migrations or []),
    }


def _load_gate_module():
    spec = importlib.util.spec_from_file_location("production_lineage_gate_v1", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _commit(repo: Path, message: str, content: str) -> str:
    (repo / "state.txt").write_text(content, encoding="utf-8")
    _git(repo, "add", "state.txt")
    _git(repo, "-c", "user.name=Gate Fixture", "-c", "user.email=gate@example.invalid", "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class ProductionLineageGateV1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def test_canonical_registry_is_schema_valid(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.gate._validate_registry(payload)
        self.assertEqual(payload["components"]["worker"]["production"]["sha"], "b6601efdfe14b23866fb256b0cd3240a17aba54d")
        self.assertEqual(payload["components"]["backend"]["production"]["sha"], "530802780b2f3de6b0a1046c21ca4f6bde77bbb9")
        self.assertEqual(payload["components"]["botapp"]["production"]["verification_state"], "UNVERIFIED")

    def test_historical_stale_lineage_is_blocked_even_when_tests_pass(self) -> None:
        fixture = json.loads(HISTORICAL.read_text(encoding="utf-8"))
        self.assertEqual(fixture["expected_gate_result"], "BLOCK")
        self.assertEqual(
            fixture["production_sha"], "2349a8e299ec28563e87423e43bcf891684304b7"
        )
        self.assertEqual(
            fixture["candidate_sha"], "ee677adc93a2dca71fdb5029b60e5a6e14931c27"
        )
        exact_registry = _registry(
            "backend",
            fixture["production_sha"],
            [_delta("backend", "backend_manual_stop_next_tick", fixture["production_sha"])],
        )
        with self.assertRaisesRegex(
            self.gate.GateFailure, "candidate_not_descendant_of_exact_production"
        ):
            self.gate.evaluate_gate(
                registry=exact_registry,
                component_name="backend",
                repo_root=BACKEND_ROOT,
                candidate_sha=fixture["candidate_sha"],
                actual_production_sha=fixture["production_sha"],
                require_clean_worktree=False,
            )

    def test_synthetic_stale_lineage_is_blocked_even_when_tests_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _git(repo, "init")
            base = _commit(repo, "base", "base")
            canonical = _commit(repo, "canonical delta", "canonical")
            production = _commit(repo, "production", "production")
            _git(repo, "checkout", "--detach", base)
            stale = _commit(repo, "stale candidate tests pass", "stale-green")
            registry = _registry("worker", production, [_delta("worker", "required", canonical)])
            with self.assertRaisesRegex(self.gate.GateFailure, "candidate_not_descendant_of_exact_production"):
                self.gate.evaluate_gate(
                    registry=registry,
                    component_name="worker",
                    repo_root=repo,
                    candidate_sha=stale,
                    actual_production_sha=production,
                )

    def test_descendant_with_all_active_deltas_passes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _git(repo, "init")
            active = _commit(repo, "active", "active")
            production = _commit(repo, "production", "production")
            candidate = _commit(repo, "candidate", "candidate")
            registry = _registry("worker", production, [_delta("worker", "required", active)])
            result = self.gate.evaluate_gate(
                registry=registry,
                component_name="worker",
                repo_root=repo,
                candidate_sha=candidate,
                actual_production_sha=production,
            )
            self.assertTrue(result["ok"])

    def test_missing_active_delta_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _git(repo, "init")
            base = _commit(repo, "base", "base")
            production = _commit(repo, "production", "production")
            candidate = _commit(repo, "candidate", "candidate")
            _git(repo, "checkout", "--detach", base)
            missing = _commit(repo, "missing active delta", "delta")
            _git(repo, "checkout", "--detach", candidate)
            registry = _registry(
                "worker", production, [_delta("worker", "missing_required", missing)]
            )
            with self.assertRaisesRegex(self.gate.GateFailure, "active_canonical_deltas_missing"):
                self.gate.evaluate_gate(
                    registry=registry,
                    component_name="worker",
                    repo_root=repo,
                    candidate_sha=candidate,
                    actual_production_sha=production,
                )

    def test_unknown_production_sha_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _git(repo, "init")
            production = _commit(repo, "production", "production")
            candidate = _commit(repo, "candidate", "candidate")
            registry = _registry(
                "worker", production, [_delta("worker", "required", production)]
            )
            with self.assertRaisesRegex(
                self.gate.GateFailure, "actual_production_sha_registry_mismatch"
            ):
                self.gate.evaluate_gate(
                    registry=registry,
                    component_name="worker",
                    repo_root=repo,
                    candidate_sha=candidate,
                    actual_production_sha="0" * 40,
                )

    def test_dirty_worktree_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _git(repo, "init")
            production = _commit(repo, "production", "production")
            candidate = _commit(repo, "candidate", "candidate")
            (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
            registry = _registry(
                "worker", production, [_delta("worker", "required", production)]
            )
            with self.assertRaisesRegex(self.gate.GateFailure, "dirty_worktree"):
                self.gate.evaluate_gate(
                    registry=registry,
                    component_name="worker",
                    repo_root=repo,
                    candidate_sha=candidate,
                    actual_production_sha=production,
                )

    def test_required_candidate_migration_missing_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _git(repo, "init")
            production = _commit(repo, "production", "production")
            candidate = _commit(repo, "candidate", "candidate")
            migration = {
                "id": "required_migration",
                "status": "ACTIVE",
                "production_version": "20260820000000",
                "repo": "backend",
                "path": "supabase/migrations/20260820000000_required.sql",
            }
            registry = _registry(
                "backend",
                production,
                [_delta("backend", "required", production)],
                [migration],
            )
            with self.assertRaisesRegex(
                self.gate.GateFailure, "candidate_migration_lineage_missing"
            ):
                self.gate.evaluate_gate(
                    registry=registry,
                    component_name="backend",
                    repo_root=repo,
                    candidate_sha=candidate,
                    actual_production_sha=production,
                    applied_migrations={"20260820000000"},
                )

    def test_strict_migration_requires_exact_hash_identity_and_schema_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _git(repo, "init")
            production = _commit(repo, "production", "production")
            migrations = repo / "supabase" / "migrations"
            migrations.mkdir(parents=True)
            sql = b"select 1;\n"
            path = migrations / "20260826021814_control_plane_reliability_v1.sql"
            path.write_bytes(sql)
            _git(repo, "add", str(path.relative_to(repo)))
            _git(repo, "-c", "user.name=Gate Fixture", "-c", "user.email=gate@example.invalid", "commit", "-m", "candidate migration")
            candidate = _git(repo, "rev-parse", "HEAD")
            import hashlib

            migration = {
                "id": "control_plane",
                "status": "ACTIVE",
                "production_version": "20260826021814",
                "repo": "worker",
                "path": str(path.relative_to(repo)),
                "canonical_name": "control_plane_reliability_v1",
                "sha256": hashlib.sha256(sql).hexdigest(),
                "content_attestation_required": True,
                "require_schema_match": True,
                "required_zero_invariants": ["null_lineage_count"],
            }
            registry = _registry(
                "worker", production, [_delta("worker", "required", production)], [migration]
            )
            attestation = {
                "schema": "PHONE_FARM_MIGRATION_ATTESTATION_V1",
                "migrations": {
                    "20260826021814": {
                        "name": "control_plane_reliability_v1",
                        "sql_sha256": hashlib.sha256(sql).hexdigest(),
                        "deployed_schema_matches": True,
                    }
                },
                "invariants": {"null_lineage_count": 0},
            }
            result = self.gate.evaluate_gate(
                registry=registry,
                component_name="worker",
                repo_root=repo,
                candidate_sha=candidate,
                actual_production_sha=production,
                applied_migrations={"20260826021814"},
                migration_attestation=attestation,
            )
            self.assertEqual(result["migration_gate"], "PASS")
            self.assertEqual(result["validated_migrations"][0]["sha256"], hashlib.sha256(sql).hexdigest())

            bad_attestation = json.loads(json.dumps(attestation))
            bad_attestation["invariants"]["null_lineage_count"] = 1
            with self.assertRaisesRegex(self.gate.GateFailure, "migration_schema_invariant_failed"):
                self.gate.evaluate_gate(
                    registry=registry,
                    component_name="worker",
                    repo_root=repo,
                    candidate_sha=candidate,
                    actual_production_sha=production,
                    applied_migrations={"20260826021814"},
                    migration_attestation=bad_attestation,
                )

    def test_registry_unavailable_is_blocked_by_cli(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--registry",
                "/definitely/missing/registry.json",
                "--component",
                "worker",
                "--repo-root",
                str(ROOT),
                "--candidate-sha",
                "a794da764c57a50187bd94c9411f6566519ad3ca",
                "--actual-production-sha",
                "a794da764c57a50187bd94c9411f6566519ad3ca",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("json_unreadable", proc.stdout)

    def test_unverified_botapp_artifact_fails_closed(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(self.gate.GateFailure, "production_identity_unverified:botapp"):
            self.gate.evaluate_gate(
                registry=payload,
                component_name="botapp",
                repo_root=ROOT,
                candidate_sha="aed34d55cd583faeaac00c8b35b926ff1f4ff152",
                actual_production_sha="aed34d55cd583faeaac00c8b35b926ff1f4ff152",
                artifact_provenance={"source_sha": "aed34d55cd583faeaac00c8b35b926ff1f4ff152"},
            )


if __name__ == "__main__":
    unittest.main()
