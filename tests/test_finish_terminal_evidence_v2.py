from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from follow60_external_deployment_lock_v2 import DeploymentTransaction, LockFailure


SHA = "a" * 40
TRANSACTION_ID = "worker-promotion:test-finish-0001"


class FinishTerminalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.release = base / "release"
        self.release.mkdir()
        self.current = base / "current"
        self.current.symlink_to(self.release)
        self.registry = base / "registry.json"
        worker = {
            "production": {"sha": SHA, "runtime_root": str(self.release.resolve()), "committed_by_transaction": TRANSACTION_ID},
            "rollback": {"sha": "b" * 40, "runtime_root": "/releases/old"},
        }
        self.registry.write_text(json.dumps({"components": {"worker": worker}}))
        self.transaction = DeploymentTransaction.__new__(DeploymentTransaction)
        self.transaction.root = self.release.resolve()
        self.transaction.expected = {"candidate_commit_sha": SHA}
        self.evidence = {
            "ok": True,
            "status": "NEW_RELEASE_FULLY_ACTIVE",
            "runtime_root": str(self.release.resolve()),
            "release_sha": SHA,
            "services": {
                "ok": True,
                "services": {
                    service: {
                        "ok": True,
                        "process_count": 1,
                        "processes": [{"module_root": str(self.release.resolve()), "runtime_sha": SHA}],
                    }
                    for service in ("dispatcher", "heartbeat", "notifier")
                },
                "startupTokenGate": {
                    "ok": True,
                    "token_id": "token-1",
                    "binding": {"phase": "PROMOTE", "candidate_sha": SHA, "transaction_id": TRANSACTION_ID},
                },
            },
            "fence": {"ok": True, "active": True, "transaction_id": TRANSACTION_ID},
            "registry_path": str(self.registry),
            "registry_worker": worker,
            "registry_worker_sha256": hashlib.sha256(
                json.dumps(worker, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
            ).hexdigest(),
        }
        self.receipt = {
            "schema": "PHONE_FARM_IMMUTABLE_PROMOTION_RECEIPT_V2",
            "receipt_state": "PROMOTED",
            "transaction_id": TRANSACTION_ID,
            "candidate_sha": SHA,
            "runtime_root": str(self.release.resolve()),
            "release_sha": SHA,
            "previous_release_sha": "b" * 40,
            "terminal_evidence": self.evidence,
        }

    def verify(self, receipt=None, evidence=None):
        with (
            mock.patch.dict(os.environ, {"PHONEFARM_RUNTIME_CURRENT_LINK": str(self.current)}),
            mock.patch("follow60_external_deployment_lock_v2.exact_admission", return_value={"candidate_commit_sha": SHA}),
            mock.patch("follow60_external_deployment_lock_v2.verify_seal", return_value={"ok": True}),
        ):
            self.transaction._verify_transactional_terminal(receipt or self.receipt, evidence or self.evidence)

    def test_exact_terminal_evidence_passes(self):
        self.verify()

    def test_missing_service_fails(self):
        evidence = {**self.evidence, "services": {**self.evidence["services"], "services": {"dispatcher": self.evidence["services"]["services"]["dispatcher"]}}}
        receipt = {**self.receipt, "terminal_evidence": evidence}
        with self.assertRaises(LockFailure):
            self.verify(receipt, evidence)

    def test_wrong_token_phase_fails(self):
        services = {**self.evidence["services"], "startupTokenGate": {**self.evidence["services"]["startupTokenGate"], "binding": {"phase": "ROLLBACK", "candidate_sha": SHA}}}
        evidence = {**self.evidence, "services": services}
        receipt = {**self.receipt, "terminal_evidence": evidence}
        with self.assertRaises(LockFailure):
            self.verify(receipt, evidence)

    def test_fence_released_before_finish_fails(self):
        evidence = {**self.evidence, "fence": {"ok": True, "active": False, "transaction_id": TRANSACTION_ID, "terminal_state": "PROMOTED"}}
        receipt = {**self.receipt, "terminal_evidence": evidence}
        with self.assertRaises(LockFailure):
            self.verify(receipt, evidence)

    def test_pointer_mismatch_fails(self):
        other = self.release.parent / "other"
        other.mkdir()
        self.current.unlink()
        self.current.symlink_to(other)
        with self.assertRaises(LockFailure):
            self.verify()


if __name__ == "__main__":
    unittest.main()
